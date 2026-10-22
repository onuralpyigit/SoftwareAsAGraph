"""Held-out verification: placement application, the failure-event oracle, and the recommendation rule."""

import ast
from pathlib import Path

import pytest

from saag.deployment import apply_placement
from saag.deployment.models import Candidate, HeldOutScore
from saag.deployment.service import DeploymentService
from saag.deployment.verify import select_recommendation
from saag.infrastructure.memory_repo import MemoryRepository
from saag.simulation.failure_simulator import FailureSimulator
from saag.simulation.graph import SimulationGraph
from saag.simulation.models import FailureScenario
from saag.simulation.service import SimulationService
from tests.deployment_helpers import spec, topology


def _repo(topo):
    repo = MemoryRepository()
    repo.save_graph(topo, clear=True)
    return repo


# ── apply_placement ──────────────────────────────────────────────────────────

def test_apply_placement_replaces_only_placed_runs_on():
    topo = topology()
    topo["relationships"]["runs_on"][0]["weight"] = 0.7          # P -> N1 carries an extra
    topo["relationships"]["depends_on"] = [{"from": "S1", "to": "P"}]
    placed = apply_placement(topo, {"P": ["N4", "N2"], "S1": ["N3"]})
    runs = [(e["from"], e["to"]) for e in placed["relationships"]["runs_on"]]
    assert ("P", "N4") in runs and ("P", "N2") in runs and ("P", "N1") not in runs
    assert runs.index(("P", "N4")) < runs.index(("P", "N2"))                  # primary first
    assert ("S2", "N4") in runs and ("B", "N1") in runs                       # untouched
    assert all(e.get("weight") == 0.7 for e in placed["relationships"]["runs_on"] if e["from"] == "P")
    assert "depends_on" not in placed["relationships"]
    assert topo["relationships"]["runs_on"][0]["to"] == "N1"                  # input not mutated
    repo = _repo(placed)                                                      # round-trips
    assert {(e["from"], e["to"]) for e in repo.export_json()["relationships"]["runs_on"]} == set(runs)


# ── SimulationService.run_failure_events ─────────────────────────────────────

def test_failure_events_match_direct_simulation_and_prime_flows():
    repo = _repo(topology())
    events = {"N1": ["N1"], "D0": ["N1", "N2"]}
    res = SimulationService(repo, replica_aware=True).run_failure_events(events, seed=7)
    assert list(res) == ["D0", "N1"]

    graph = SimulationGraph(repo.get_graph_data(include_raw=True))
    sim = FailureSimulator(graph, replica_aware=True)
    SimulationService._prime_baseline_flows(graph, sim)
    direct = sim.simulate(FailureScenario(target_ids=["N1", "N2"], seed=FailureSimulator._derive_seed(7, "D0")))
    assert res["D0"].impact.composite_impact == pytest.approx(direct.impact.composite_impact)
    assert any(r.impact.flow_disruption > 0 for r in res.values())


# ── Oracle contract ──────────────────────────────────────────────────────────

def test_verification_uses_replica_aware_failure_simulator_never_fault_injector(monkeypatch):
    from saag.simulation import fault_injector

    def forbidden(*a, **kw):
        raise AssertionError("deployment verification must not use FaultInjector")

    monkeypatch.setattr(fault_injector.FaultInjector, "__init__", forbidden)
    seen = []
    original = FailureSimulator.__init__

    def spy(self, *a, **kw):
        seen.append(kw.get("replica_aware"))
        original(self, *a, **kw)

    monkeypatch.setattr(FailureSimulator, "__init__", spy)
    res = DeploymentService(_repo(topology())).optimize(spec(), seeds=(1,), ties=1, front_points=2)
    assert seen and all(flag is True for flag in seen)
    assert res.oracle["engine"] == "FailureSimulator" and res.oracle["replica_aware"] is True
    assert all(c.held_out is not None for c in res.candidates)


def test_deployment_package_never_references_fault_injector():
    root = Path(__file__).resolve().parents[1] / "saag" / "deployment"
    for path in root.glob("*.py"):
        names = {n.id for n in ast.walk(ast.parse(path.read_text())) if isinstance(n, ast.Name)}
        names |= {a.name.split(".")[-1] for n in ast.walk(ast.parse(path.read_text()))
                  if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}
        assert "FaultInjector" not in names, path.name


# ── Selection rule ───────────────────────────────────────────────────────────

def _cand(name, kind, impact, traffic, blast=1.0, sigma=0.001, hosts=4.0, entropy=1.0, violations=0):
    return Candidate(
        name=name, kind=kind, placement={name: ["N1"]}, traffic=traffic, cross_host_share=0.5,
        cross_domain_share=0.2, latency_weighted=0.0, blast_exp=blast, blast_max=2.0,
        load={"max_cpu_util": 0.5, "max_mem_util": 0.5, "hosts_used": hosts, "copies_entropy": entropy},
        violations={"capacity": violations},
        held_out=HeldOutScore(expected_impact=impact, worst_impact=0.5, sigma_seed=sigma,
                              per_event={}, per_seed=[impact]),
    )


def test_tied_candidates_recommend_least_traffic_without_a_claim():
    cands = [_cand("front_0", "front", 0.100, 50), _cand("front_1", "front", 0.101, 20, blast=2.0),
             _cand("indeg_spread", "baseline", 0.100, 90), _cand("ffd", "baseline", 0.102, 95)]
    pick = select_recommendation(cands)
    assert pick["recommended"] == "front_1"
    assert pick["best_baseline"] == "indeg_spread"
    assert pick["reliability_claim"] is False
    assert "No held-out reliability gain over indeg_spread" in pick["rationale"]


def test_all_optimizer_candidates_worse_recommends_the_best_baseline():
    cands = [_cand("front_0", "front", 0.200, 10), _cand("indeg_spread", "baseline", 0.100, 90),
             _cand("ffd", "baseline", 0.150, 80, violations=1)]
    pick = select_recommendation(cands)
    assert pick["recommended"] == "indeg_spread" and pick["reliability_claim"] is False
    assert "worse" in pick["rationale"]


def test_claim_needs_to_beat_best_baseline_and_indeg_beyond_the_noise_bar():
    cands = [_cand("front_0", "front", 0.050, 30), _cand("indeg_spread", "baseline", 0.100, 90),
             _cand("k8s_spread", "baseline", 0.090, 80)]
    pick = select_recommendation(cands)
    assert pick["recommended"] == "front_0" and pick["best_baseline"] == "k8s_spread"
    assert pick["reliability_claim"] is True
    # A wide spread among proxy-tied optima raises the bar above the gain.
    cands.append(_cand("tie_1", "tie", 0.095, 30))
    pick = select_recommendation(cands)
    assert pick["noise_floor"] == pytest.approx(0.045)
    assert pick["reliability_claim"] is False


def test_infeasible_baselines_are_not_the_reference():
    cands = [_cand("front_0", "front", 0.100, 30), _cand("authored", "baseline", 0.010, 90, violations=3),
             _cand("indeg_spread", "baseline", 0.100, 90)]
    assert select_recommendation(cands)["best_baseline"] == "indeg_spread"


def test_concentration_caveat_is_reported():
    cands = [_cand("front_0", "front", 0.100, 30), _cand("indeg_spread", "baseline", 0.101, 90),
             _cand("ffd", "baseline", 0.099, 95, hosts=2.0, entropy=0.3)]
    assert "concentrates load" in select_recommendation(cands)["rationale"]


def test_without_a_feasible_baseline_there_is_no_reference_and_no_claim():
    cands = [_cand("front_0", "front", 0.050, 30), _cand("front_1", "front", 0.060, 20, blast=2.0),
             _cand("indeg_spread", "baseline", 0.100, 90, violations=2)]
    pick = select_recommendation(cands)
    assert pick["best_baseline"] is None
    assert pick["recommended"] == "front_1" and pick["reliability_claim"] is False
    cands = [_cand("front_0", "front", 0.050, 30, violations=1), _cand("indeg_spread", "baseline", 0.1, 90, violations=2)]
    assert select_recommendation(cands)["recommended"] is None
