"""The placement MILP against brute-force enumeration on a micro instance."""

import numpy as np
import pytest

from saag.deployment.heuristics import baselines, local_search
from saag.deployment.milp import DeploymentMILP, ModelTooLarge
from saag.deployment.service import DeploymentService, _dependency_arcs
from saag.infrastructure.memory_repo import MemoryRepository
from tests.deployment_helpers import all_placements, feasible, instance, spec, topology


@pytest.fixture(scope="module")
def inst():
    return instance()


@pytest.fixture(scope="module")
def model(inst):
    return DeploymentMILP(inst, time_limit=30)


@pytest.fixture(scope="module")
def enumerated(inst):
    return [pl for pl in all_placements(inst) if feasible(inst, pl)]


def _vector(model, pl):
    """Column values of x and y for a placement (other columns 0)."""
    v = np.zeros(len(model.m.lb))
    for c, hs in pl.items():
        for n in hs:
            v[model.x[c, n]] = 1.0
        v[model.y[c, hs[0]]] = 1.0
    return v


def _rows_hold(model, v, family):
    return all(lo - 1e-9 <= sum(a * v[j] for j, a in coefs.items()) <= hi + 1e-9
               for (coefs, lo, hi), f in zip(model.m.rows, model.m.family) if f == family)


def test_enumeration_has_feasible_and_infeasible_placements(inst, enumerated):
    total = sum(1 for _ in all_placements(inst))
    assert 0 < len(enumerated) < total


def test_anchors_reach_the_brute_force_optima(inst, model, enumerated):
    lo_pl, lo = model.lexi(model.objectives("blast_exp", "traffic"))
    hi_pl, hi = model.lexi(model.objectives("traffic", "blast_exp"))
    best_blast = min(inst.proxy_blast(pl)["blast_exp"] for pl in enumerated)
    best_traffic = min(inst.traffic(pl)["traffic"] for pl in enumerated)
    assert lo["status"] == hi["status"] == "optimal"
    assert lo["blast_exp"] == pytest.approx(best_blast)
    assert hi["traffic"] == pytest.approx(best_traffic)
    # The solver's objective values equal the direct computation on its placement.
    assert inst.proxy_blast(lo_pl)["blast_exp"] == pytest.approx(lo["blast_exp"])
    assert inst.traffic(hi_pl)["traffic"] == pytest.approx(hi["traffic"])
    assert feasible(inst, lo_pl) and feasible(inst, hi_pl)


def test_worst_case_objective_matches_enumeration(inst, model, enumerated):
    pl, info = model.lexi(model.objectives("blast_max", "blast_exp", "traffic"))
    assert info["blast_max"] == pytest.approx(min(inst.proxy_blast(p)["blast_max"] for p in enumerated))
    assert inst.proxy_blast(pl)["blast_max"] == pytest.approx(info["blast_max"])


def test_deadline_rows_keep_exactly_the_deadline_feasible_set(inst, model):
    for pl in all_placements(inst):
        v = _vector(model, pl)
        if not _rows_hold(model, v, "replicas") or not _rows_hold(model, v, "anti_affinity"):
            continue
        assert _rows_hold(model, v, "deadline") == (inst.violations(pl)["deadline"] == 0), pl


def test_coverage_is_exact_for_fixed_placements(inst, model, enumerated):
    """With x, y fixed, minimising blast_exp over z reproduces the BFS proxy."""
    for pl in enumerated[::max(1, len(enumerated) // 15)]:
        fix = [({j: 1.0}, val, val) for j, val in
               [(model.x[c, n], 1.0 if n in pl[c] else 0.0) for c in inst.comps for n in inst.hosts]]
        sol, st = model.m.solve(model.blast_exp, fix)
        assert st["status"] == "optimal"
        assert float(sum(a * sol[j] for j, a in model.blast_exp.items())) == pytest.approx(
            inst.proxy_blast(pl)["blast_exp"])


def test_front_is_monotone_and_feasible(inst, model):
    front = [f for f in model.front(4) if f[1] is not None]
    assert len(front) == 4
    blasts = [info["blast_exp"] for _, _, info in front]
    traffic = [info["traffic"] for _, _, info in front]
    assert blasts == sorted(blasts)
    assert traffic == sorted(traffic, reverse=True)
    assert all(feasible(inst, pl) for _, pl, _ in front)


def test_ties_are_distinct_and_at_the_minimum(inst, model):
    lo_pl, lo = model.lexi(model.objectives("blast_exp", "traffic"))
    ties = model.ties(lo_pl, lo["blast_exp"], 3, [lo_pl])
    keys = {tuple(sorted((c, tuple(sorted(h))) for c, h in pl.items())) for _, pl, _ in ties}
    keys.add(tuple(sorted((c, tuple(sorted(h))) for c, h in lo_pl.items())))
    assert len(keys) == len(ties) + 1
    assert all(info["blast_exp"] == pytest.approx(lo["blast_exp"]) for _, _, info in ties)


def test_free_brokers_avoid_an_unreliable_pinned_host():
    """Brokers are decision variables: pinned to a flaky host they drag blast up; free, they move."""
    topo = topology(brokers=("B1", "B2"))
    flaky = {"N1": {"domain": "D0", "p": 0.5}}
    pinned = instance(topo, spec(nodes=flaky, brokers={"B1": {"pin": ["N1"]}, "B2": {"pin": ["N1"]}}))
    free = instance(topo, spec(nodes=flaky))
    blast = {}
    for name, inst in (("pinned", pinned), ("free", free)):
        m = DeploymentMILP(inst, time_limit=30)
        pl, info = m.lexi(m.objectives("blast_exp"))
        blast[name] = info["blast_exp"]
        if name == "free":
            assert "N1" not in pl["B1"] + pl["B2"]
    assert blast["free"] < blast["pinned"]


def test_capacity_shortfall_is_caught_by_prechecks():
    inst = instance(spc=spec(defaults={"node": {"cpu": 0.5, "mem_mb": 4096, "p": 0.02}}))
    msgs = inst.prechecks()
    assert any("total cpu demand" in m for m in msgs)


def test_failover_safe_deadline_conflict_is_caught_by_prechecks():
    # P must span both domains, and every P copy must reach S1 via B within 5 ms;
    # with 10 ms between domains no broker host works for both copies. Some single
    # host triple does meet the deadline, which is all the old precheck asked.
    inst = instance(spc=spec(latency={"intra_domain_ms": 1.0, "inter_domain_ms": 10.0}))
    assert any(inst.path_latency(n, k, m) <= 5.0 for n in inst.hosts for k in inst.hosts for m in inst.hosts)
    msgs = inst.prechecks()
    assert len(msgs) == 1 and "topic T1" in msgs[0] and "distinct domains" in msgs[0]


def test_failover_safe_precheck_never_rejects_a_feasible_spec(inst, enumerated):
    assert enumerated and inst.prechecks() == []


def test_bin_packing_conflict_is_diagnosed_by_the_milp():
    # Five copies, four hosts that each hold one: total capacity suffices and every
    # copy fits somewhere, so only the MILP can see the conflict.
    inst = instance(spc=spec(defaults={"node": {"cpu": 1.5, "mem_mb": 4096, "p": 0.02}}))
    assert inst.prechecks() == []
    model = DeploymentMILP(inst, time_limit=30)
    pl, info = model.lexi(model.objectives("blast_exp"))
    assert pl is None and info["status"] == "infeasible"
    diag = model.diagnose()
    assert diag["families"] and set(diag["families"]) <= {"capacity_cpu", "replicas", "anti_affinity", "deadline"}


def test_model_size_guard():
    with pytest.raises(ModelTooLarge):
        DeploymentMILP(instance(), max_nonzeros=10)


def test_service_falls_back_to_heuristic_when_the_model_is_too_large():
    repo = MemoryRepository()
    repo.save_graph(topology(), clear=True)
    res = DeploymentService(repo).optimize(spec(), max_nonzeros=10, seeds=(1,), ties=0, front_points=3)
    assert res.solver == "heuristic" and res.status == "heuristic"
    assert all(c.feasible for c in res.candidates if c.kind == "heuristic")
    with pytest.raises(ModelTooLarge):
        DeploymentService(repo).optimize(spec(), max_nonzeros=10, solver="milp")


def test_service_reports_infeasible_with_diagnosis():
    repo = MemoryRepository()
    repo.save_graph(topology(), clear=True)
    res = DeploymentService(repo).optimize(spec(defaults={"node": {"cpu": 1.5, "mem_mb": 4096, "p": 0.02}}),
                                           seeds=(1,))
    assert res.status == "infeasible" and res.candidates == []
    assert res.diagnosis is not None and res.diagnosis.families
    res = DeploymentService(repo).optimize(spec(latency={"inter_domain_ms": 10.0}), seeds=(1,))
    assert res.status == "infeasible" and res.solver == "none" and res.diagnosis.prechecks


def test_heuristic_never_recommends_an_infeasible_placement():
    """When search finds nothing feasible the stage says so instead of recommending a baseline."""
    repo = MemoryRepository()
    repo.save_graph(topology(), clear=True)
    tight = spec(defaults={"node": {"cpu": 1.5, "mem_mb": 4096, "p": 0.02}})
    res = DeploymentService(repo).optimize(tight, solver="heuristic", seeds=(1,), front_points=2)
    assert res.status == "no_feasible" and res.recommended is None
    assert res.diagnosis.families and not any(c.feasible for c in res.candidates)


def test_violation_score_grows_with_overload(inst):
    pl = {"B": ["N1"], "P": ["N1", "N3"], "S1": ["N2"], "S2": ["N4"]}
    stacked = {**pl, "S1": ["N1"], "S2": ["N1"]}
    one_more = {**pl, "S1": ["N1"]}
    tight = instance(spc=spec(defaults={"node": {"cpu": 2.0, "mem_mb": 4096, "p": 0.02}}))
    # Same count (one overloaded host), so a count-based search could stack for free.
    assert tight.violations(one_more)["capacity"] == tight.violations(stacked)["capacity"] == 1
    assert tight.violation_score(stacked) > tight.violation_score(one_more) > tight.violation_score(pl) == 0
    assert inst.violation_score(pl) == 0


def test_unbinding_deadlines_are_dropped():
    inst = instance(topology(), spec(latency={"intra_domain_ms": 0.1, "inter_domain_ms": 0.2}))
    assert inst.deadline_pairs == [] and inst.deadline_unbinding == 1
    assert DeploymentMILP(inst).m.family.count("deadline") == 0


def test_probe_limit_bounds_the_first_stage(model):
    pl, info = model.lexi(model.objectives("blast_exp", "traffic"), first_limit=0.1)
    assert info["stages"][0]["seconds"] < 5


def test_rule6_broker_arcs_are_excluded():
    topo = topology(brokers=("B1", "B2"), broker_hosts={"B1": "N1", "B2": "N1"})
    repo = MemoryRepository()
    repo.save_graph(topo, clear=True)
    repo.derive_dependencies()
    derived = {(e.source_id, e.target_id) for e in repo.get_graph_data().edges}
    assert ("B1", "B2") in derived                      # Rule 6 fires on the authored hosts...
    assert ("B1", "B2") not in _dependency_arcs(topo)   # ...but never steers a new placement


def test_baselines_and_local_search_respect_hard_constraints(inst):
    # Greedy baselines keep the placement families exact and report, not hide,
    # deadlines they could not meet.
    for name, pl in baselines(inst, seed=0).items():
        v = inst.violations(pl)
        assert v["replicas"] == v["pin"] == v["capacity"] == v["anti_affinity"] == 0, name
    start = baselines(inst, seed=0)["random"]
    better = local_search(inst, start, lambda pl: (sum(inst.violations(pl).values()), inst.traffic(pl)["traffic"]))
    assert feasible(inst, better)
    assert inst.traffic(better)["traffic"] <= inst.traffic(start)["traffic"]
