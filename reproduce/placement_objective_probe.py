"""
Placement-objective probe: can the existing oracles score an app-to-node placement?

Read-only pre-study for a placement-optimization paper. Nothing in saag/, the
corpus or any published label is changed; every arm is a modified *copy* of a
committed topology, scored by the unmodified oracles:

    I*      FaultInjector, Node injection, seeds 42/123/456, ladder QoS
    I_comp  FailureSimulator composite, the network_topology_probe path
            (event-sim baseline primed, exhaustive sweep seeded at 42)

Part 1 -- replica semantics (atm, av).
    Both oracles kill a component when ANY one of its hosts fails
    (FaultInjector._cascade via residents_of, FailureSimulator._cascade_physical
    via get_hosted_components). Measured three ways per Node:
      authored       the committed deployment, replicas and all
      single_host    every multi-hosted component keeps only its first host
      replica_aware  emulated all-hosts-down semantics: when Node n is injected,
                     a component that also runs elsewhere has n removed from its
                     host list first, so it survives. Built per n on a copy; the
                     oracle code is untouched.
    If authored > single_host the oracles score redundancy backwards. A
    synthetic two-host micro-topology isolates the mechanism.

Part 2 -- consolidation vs spread (atm, av, one realworld system).
    Deployments over the scenario's own hosts, one host per component:
    random, round_robin, colocate (greedy: subscriber joins its main publisher,
    capped at ceil(1.5 n/H) per host), consolidate_1, consolidate_2; plus
    authored and authored_single as references. Three placement seeds.
    Objectives over hosts with uniform p:
      expected blast  mean_n Impact(n)
      worst-case      max_n Impact(n)
    Locality = share of (subscriber, publisher) topic pairs that share a host.

Usage:
    python reproduce/placement_objective_probe.py
    python reproduce/placement_objective_probe.py --realworld realworld_trainticket
"""

from __future__ import annotations

import argparse
import copy
import json
import logging
import math
import random
import statistics
import sys
from collections import Counter, defaultdict, deque
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scipy.stats import spearmanr

from reproduce._provenance import stamp
from reproduce.network_topology_probe import _graph_data, failure_sim_scores

logger = logging.getLogger(__name__)

SCENARIOS_DIR = Path("data/scenarios")
INJECTOR_SEEDS = [42, 123, 456]
PLACEMENT_SEEDS = [0, 1, 2]
COLOCATE_SLACK = 1.5

Placement = Dict[str, List[str]]   # component -> hosts it RUNS_ON


# ── Topology helpers ─────────────────────────────────────────────────────────

def load(name: str) -> Dict[str, Any]:
    return json.loads((SCENARIOS_DIR / f"{name}.json").read_text())


def hosts_of(topo: Dict[str, Any]) -> Placement:
    out: Placement = defaultdict(list)
    for e in topo["relationships"].get("runs_on", []):
        out[e["from"]].append(e["to"])
    return dict(out)


def host_ids(topo: Dict[str, Any]) -> List[str]:
    return sorted(n["id"] for n in topo["nodes"])


def with_placement(topo: Dict[str, Any], placement: Placement) -> Dict[str, Any]:
    """Copy of *topo* with RUNS_ON replaced by *placement* (edge extras kept)."""
    extras = {}
    for e in topo["relationships"].get("runs_on", []):
        extras.setdefault(e["from"], {k: v for k, v in e.items() if k not in ("from", "to")})
    out = copy.deepcopy(topo)
    out["relationships"]["runs_on"] = [
        {"from": c, "to": h, **extras.get(c, {})}
        for c in sorted(placement) for h in placement[c]
    ]
    return out


def residents(placement: Placement, hosts: List[str]) -> Dict[str, int]:
    count = Counter(h for hs in placement.values() for h in hs)
    return {h: count.get(h, 0) for h in hosts}


def pubsub_pairs(topo: Dict[str, Any]) -> Counter:
    """(subscriber, publisher) -> number of topics they share."""
    pubs, subs = defaultdict(set), defaultdict(set)
    for e in topo["relationships"].get("publishes_to", []):
        pubs[e["to"]].add(e["from"])
    for e in topo["relationships"].get("subscribes_to", []):
        subs[e["to"]].add(e["from"])
    pairs: Counter = Counter()
    for t in sorted(subs):
        for s in sorted(subs[t]):
            for p in sorted(pubs.get(t, ())):
                if s != p:
                    pairs[(s, p)] += 1
    return pairs


def locality(pairs: Counter, placement: Placement) -> float:
    """Topic-weighted share of subscriber/publisher pairs that share a host."""
    total = sum(pairs.values())
    if not total:
        return float("nan")
    hit = sum(w for (s, p), w in pairs.items()
              if set(placement.get(s, ())) & set(placement.get(p, ())))
    return hit / total


# ── Oracles ──────────────────────────────────────────────────────────────────

def istar_nodes(topo: Dict[str, Any], node_ids: Optional[List[str]] = None) -> Dict[str, Dict[str, float]]:
    from saag.core.graph_io import build_graph_from_json
    from saag.simulation.fault_injector import FaultInjector

    inj = FaultInjector(graph=build_graph_from_json(topo), seeds=INJECTOR_SEEDS,
                        cascade_depth_limit=0, qos_factor_mode="ladder")
    res = inj.run(node_types=["Node"], node_ids=node_ids)
    return {nid: {"mean": float(r.impact_score), "std": float(r.impact_score_std)}
            for nid, r in res.records.items()}


def icomp_nodes(topo: Dict[str, Any]) -> Dict[str, float]:
    scores = failure_sim_scores(_graph_data(topo, None), gating=False, devices=[])
    return {cid: v for cid, (t, v) in scores.items() if t == "Node"}


def score(topo: Dict[str, Any], hosts: List[str]) -> Dict[str, Any]:
    ist = istar_nodes(topo)
    ic = icomp_nodes(topo)
    return {
        "istar": {h: ist[h]["mean"] for h in hosts},
        "istar_seed_std_max": max(v["std"] for v in ist.values()),
        "icomp": {h: ic.get(h, 0.0) for h in hosts},
    }


# ── Part 1: replica semantics ────────────────────────────────────────────────

def micro_topology(replicated: bool) -> Dict[str, Any]:
    """P publishes T to S, brokerless, no mesh. P on N1 (and N2 if *replicated*); S on N3."""
    qos = {"durability": "VOLATILE", "reliability": "BEST_EFFORT", "transport_priority": "MEDIUM"}
    return {
        "metadata": {"scenario": "placement_micro"},
        "nodes": [{"id": f"N{i}", "name": f"host{i}"} for i in (1, 2, 3)],
        "brokers": [],
        "topics": [{"id": "T", "name": "t", "size": 256, "qos": qos, "frequency": 10.0}],
        "applications": [{"id": "P", "name": "pub", "app_type": "service"},
                         {"id": "S", "name": "sub", "app_type": "service"}],
        "libraries": [],
        "relationships": {
            "runs_on": [{"from": "P", "to": "N1"}] + ([{"from": "P", "to": "N2"}] if replicated else [])
                       + [{"from": "S", "to": "N3"}],
            "routes": [], "publishes_to": [{"from": "P", "to": "T"}],
            "subscribes_to": [{"from": "S", "to": "T"}], "connects_to": [], "uses": [],
        },
    }


def _icomp_cascade(topo: Dict[str, Any], node: str) -> List[str]:
    """Components FailureSimulator fails when *node* is injected (same scenario as the sweep)."""
    from saag.simulation.failure_simulator import FailureSimulator
    from saag.simulation.graph import SimulationGraph
    from saag.simulation.service import SimulationService

    graph = SimulationGraph(_graph_data(topo, None))
    sim = FailureSimulator(graph, qos_weighting=True, network_gating=False)
    SimulationService._prime_baseline_flows(graph, sim)
    res = next(r for r in sim.simulate_exhaustive(layer="system", seed=42) if r.target_id == node)
    return sorted(res.cascaded_failures)


def micro_check() -> Dict[str, Any]:
    """Inject N2 with and without P's replica there; P stays alive on N1 either way."""
    from saag.core.graph_io import build_graph_from_json
    from saag.simulation.fault_injector import FaultInjector

    out = {}
    for replicated in (False, True):
        topo = micro_topology(replicated)
        rec = FaultInjector(graph=build_graph_from_json(topo), seeds=INJECTOR_SEEDS).run(
            node_types=["Node"]).records["N2"]
        out["P_on_N1_N2" if replicated else "P_on_N1"] = {
            "istar_N2": float(rec.impact_score),
            "S_feed_loss_when_N2_fails": rec.per_subscriber_feed_loss.get("S"),
            "icomp_N2": icomp_nodes(topo).get("N2"),
            "failsim_cascade_when_N2_fails": _icomp_cascade(topo, "N2"),
        }
    rep = out["P_on_N1_N2"]
    out["replica_killed_by_one_host"] = {
        "istar": rep["istar_N2"] > 0 and out["P_on_N1"]["istar_N2"] == 0,
        "icomp": "P" in rep["failsim_cascade_when_N2_fails"]
                 and "P" not in out["P_on_N1"]["failsim_cascade_when_N2_fails"],
    }
    return out


def replica_aware_scores(topo: Dict[str, Any], hosts: List[str]) -> Dict[str, Dict[str, float]]:
    """Emulated all-hosts-down semantics for single-Node failure, one copy per Node."""
    authored = hosts_of(topo)
    out = {"istar": {}, "icomp": {}}
    for n in hosts:
        placement = {c: ([h for h in hs if h != n] if len(hs) > 1 and n in hs else list(hs))
                     for c, hs in authored.items()}
        t = with_placement(topo, placement)
        out["istar"][n] = istar_nodes(t, node_ids=[n])[n]["mean"]
        out["icomp"][n] = icomp_nodes(t).get(n, 0.0)
    return out


def part1(name: str) -> Dict[str, Any]:
    topo = load(name)
    hosts = host_ids(topo)
    authored = hosts_of(topo)
    single = {c: hs[:1] for c, hs in authored.items()}
    multi = sorted(c for c, hs in authored.items() if len(hs) > 1)

    arms = {
        "authored": score(topo, hosts),
        "single_host": score(with_placement(topo, single), hosts),
        "replica_aware": replica_aware_scores(topo, hosts),
    }
    arms["authored"]["residents"] = residents(authored, hosts)
    arms["single_host"]["residents"] = residents(single, hosts)

    summary = {}
    for oracle in ("istar", "icomp"):
        a, s, r = (arms[k][oracle] for k in ("authored", "single_host", "replica_aware"))
        summary[oracle] = {
            "mean_authored": statistics.fmean(a.values()),
            "mean_single_host": statistics.fmean(s.values()),
            "mean_replica_aware": statistics.fmean(r.values()),
            "max_authored": max(a.values()),
            "max_single_host": max(s.values()),
            "max_replica_aware": max(r.values()),
            "n_nodes_authored_gt_single": sum(a[h] > s[h] + 1e-9 for h in hosts),
            "n_nodes_authored_lt_single": sum(a[h] < s[h] - 1e-9 for h in hosts),
            "n_nodes_authored_gt_replica_aware": sum(a[h] > r[h] + 1e-9 for h in hosts),
        }
        summary[oracle]["replicas_raise_node_impact"] = (
            summary[oracle]["mean_authored"] > summary[oracle]["mean_single_host"] + 1e-9)
    return {"scenario": name, "n_hosts": len(hosts), "n_hosted": len(authored),
            "n_multi_hosted": len(multi), "multi_hosted": multi, "arms": arms, "summary": summary}


# ── Part 2: placement strategies ─────────────────────────────────────────────

def _least_loaded(load: Dict[str, int], rng: random.Random, allowed: Optional[List[str]] = None) -> str:
    cand = allowed if allowed is not None else list(load)
    low = min(load[h] for h in cand)
    return rng.choice(sorted(h for h in cand if load[h] == low))


def place_random(comps, hosts, topo, rng):
    return {c: [rng.choice(hosts)] for c in comps}


def place_round_robin(comps, hosts, topo, rng):
    order = list(comps)
    rng.shuffle(order)
    return {c: [hosts[i % len(hosts)]] for i, c in enumerate(order)}


def place_colocate(comps, hosts, topo, rng):
    """Brokers spread first; apps in publisher-first BFS order join their main publisher's host."""
    pairs = pubsub_pairs(topo)
    main_pub: Dict[str, str] = {}
    by_sub: Dict[str, List[Tuple[int, str]]] = defaultdict(list)
    for (s, p), w in pairs.items():
        by_sub[s].append((w, p))
    for s, cands in by_sub.items():
        top = max(w for w, _ in cands)
        main_pub[s] = rng.choice(sorted(p for w, p in cands if w == top))

    brokers = {b["id"] for b in topo["brokers"]}
    apps = [c for c in comps if c not in brokers]
    # depth = BFS distance from apps that subscribe to nothing, along publisher -> subscriber
    followers = defaultdict(list)
    for s, p in main_pub.items():
        followers[p].append(s)
    depth = {a: 0 for a in apps if a not in main_pub}
    q = deque(sorted(depth))
    while q:
        u = q.popleft()
        for v in sorted(followers[u]):
            if v not in depth:
                depth[v] = depth[u] + 1
                q.append(v)
    shuffled = list(apps)
    rng.shuffle(shuffled)
    order = sorted(shuffled, key=lambda a: depth.get(a, math.inf))

    cap = math.ceil(COLOCATE_SLACK * len(comps) / len(hosts))
    load = {h: 0 for h in hosts}
    out: Placement = {}
    for b in sorted(c for c in comps if c in brokers):
        out[b] = [_least_loaded(load, rng)]
        load[out[b][0]] += 1
    for a in order:
        p = main_pub.get(a)
        h = out[p][0] if p in out and load[out[p][0]] < cap else None
        if h is None:
            h = _least_loaded(load, rng, [x for x in hosts if load[x] < cap] or hosts)
        out[a] = [h]
        load[h] += 1
    return out


def place_consolidate(k: int):
    def fn(comps, hosts, topo, rng):
        chosen = rng.sample(hosts, k)
        order = list(comps)
        rng.shuffle(order)
        return {c: [chosen[i % k]] for i, c in enumerate(order)}
    return fn


STRATEGIES: Dict[str, Callable] = {
    "random": place_random,
    "round_robin": place_round_robin,
    "colocate": place_colocate,
    "consolidate_2": place_consolidate(2),
    "consolidate_1": place_consolidate(1),
}


def objectives(s: Dict[str, Any]) -> Dict[str, float]:
    return {
        "istar_expected": statistics.fmean(s["istar"].values()),
        "istar_worst": max(s["istar"].values()),
        "icomp_expected": statistics.fmean(s["icomp"].values()),
        "icomp_worst": max(s["icomp"].values()),
    }


def evaluate(topo, hosts, placement, pairs) -> Dict[str, Any]:
    s = score(with_placement(topo, placement), hosts)
    res = residents(placement, hosts)
    return {**objectives(s), "locality": locality(pairs, placement),
            "max_load": max(res.values()), "hosts_used": sum(v > 0 for v in res.values()),
            "per_node": {"istar": s["istar"], "icomp": s["icomp"], "residents": res}}


def _rho(x: List[float], y: List[float]) -> Optional[float]:
    if len(set(x)) < 2 or len(set(y)) < 2:
        return None
    return float(spearmanr(x, y).statistic)


def part2(name: str) -> Dict[str, Any]:
    topo = load(name)
    hosts = host_ids(topo)
    authored = hosts_of(topo)
    comps = sorted(authored)
    pairs = pubsub_pairs(topo)

    runs: Dict[str, List[Dict[str, Any]]] = {
        "authored": [evaluate(topo, hosts, authored, pairs)],
        "authored_single": [evaluate(topo, hosts, {c: hs[:1] for c, hs in authored.items()}, pairs)],
    }
    for strat, fn in STRATEGIES.items():
        runs[strat] = []
        for seed in PLACEMENT_SEEDS:
            r = evaluate(topo, hosts, fn(comps, hosts, topo, random.Random(seed)), pairs)
            r["seed"] = seed
            runs[strat].append(r)
            logger.info("%s %-14s seed=%d  E[I*]=%.3f  max I*=%.3f  loc=%.2f",
                        name, strat, seed, r["istar_expected"], r["istar_worst"], r["locality"])

    keys = ("istar_expected", "istar_worst", "icomp_expected", "icomp_worst", "locality", "max_load", "hosts_used")
    table = {
        strat: {k: {"mean": statistics.fmean(r[k] for r in rs),
                    "std": statistics.stdev(r[k] for r in rs) if len(rs) > 1 else 0.0} for k in keys}
        for strat, rs in runs.items()
    }

    # Subadditivity: sum of per-host blasts under round-robin vs one host holding everything.
    union = statistics.fmean(r["istar_worst"] for r in runs["consolidate_1"])
    union_c = statistics.fmean(r["icomp_worst"] for r in runs["consolidate_1"])
    rr_sum = statistics.fmean(sum(r["per_node"]["istar"].values()) for r in runs["round_robin"])
    rr_sum_c = statistics.fmean(sum(r["per_node"]["icomp"].values()) for r in runs["round_robin"])

    generated = [r for s in STRATEGIES for r in runs[s]]
    m = lambda strat, k: table[strat][k]["mean"]
    spread = ("random", "round_robin")
    cons = ("consolidate_1", "consolidate_2")
    verdict = {}
    for o in ("istar", "icomp"):
        e, w = f"{o}_expected", f"{o}_worst"
        verdict[o] = {
            "expected_rewards_consolidation": max(m(c, e) for c in cons) < min(m(s, e) for s in spread),
            "worst_case_penalises_consolidation": min(m(c, w) for c in cons) > max(m(s, w) for s in spread),
            "colocate_beats_round_robin_expected": m("colocate", e) < m("round_robin", e),
            "rho_locality_vs_expected": _rho([r["locality"] for r in generated], [r[e] for r in generated]),
            "rho_locality_vs_worst": _rho([r["locality"] for r in generated], [r[w] for r in generated]),
            "rank_by_expected": sorted(table, key=lambda s: m(s, e)),
        }
    verdict["subadditivity"] = {
        "istar_round_robin_sum_over_hosts": rr_sum, "istar_all_on_one_host": union,
        "istar_ratio": rr_sum / union if union else None,
        "icomp_round_robin_sum_over_hosts": rr_sum_c, "icomp_all_on_one_host": union_c,
        "icomp_ratio": rr_sum_c / union_c if union_c else None,
    }
    return {"scenario": name, "n_hosts": len(hosts), "n_hosted": len(comps),
            "n_pubsub_pairs": len(pairs), "colocate_cap": math.ceil(COLOCATE_SLACK * len(comps) / len(hosts)),
            "table": table, "verdict": verdict, "runs": runs}


# ── Reporting ────────────────────────────────────────────────────────────────

def print_summary(micro, p1, p2) -> None:
    print("\n### Mechanism (synthetic: P on N1[+N2] -> T -> S on N3; inject N2)")
    for arm in ("P_on_N1", "P_on_N1_N2"):
        print(f"  {arm:11s} {micro[arm]}")
    print(f"  replica killed by one host failing: {micro['replica_killed_by_one_host']}")

    print("\n### Part 1 -- replica semantics (mean / max over Nodes)")
    for r in p1:
        print(f"  {r['scenario']}: {r['n_multi_hosted']}/{r['n_hosted']} components multi-hosted")
        for o, s in r["summary"].items():
            print(f"    {o:6s} authored {s['mean_authored']:.3f}/{s['max_authored']:.3f}"
                  f" | single_host {s['mean_single_host']:.3f}/{s['max_single_host']:.3f}"
                  f" | replica_aware {s['mean_replica_aware']:.3f}/{s['max_replica_aware']:.3f}"
                  f" | nodes authored>single {s['n_nodes_authored_gt_single']}/{r['n_hosts']}"
                  f" | replicas raise impact: {s['replicas_raise_node_impact']}")

    print("\n### Part 2 -- consolidation vs spread (mean ± sd over placement seeds)")
    for r in p2:
        print(f"\n  {r['scenario']} ({r['n_hosts']} hosts, {r['n_hosted']} hosted, colocate cap {r['colocate_cap']})")
        print(f"    {'strategy':16s} {'E[I*]':>13s} {'max I*':>13s} {'E[I_comp]':>13s} {'max I_comp':>13s} {'locality':>11s} {'maxload':>7s}")
        for strat, row in r["table"].items():
            f = lambda k: f"{row[k]['mean']:.3f}±{row[k]['std']:.3f}"
            print(f"    {strat:16s} {f('istar_expected'):>13s} {f('istar_worst'):>13s} {f('icomp_expected'):>13s}"
                  f" {f('icomp_worst'):>13s} {row['locality']['mean']:>11.2f} {row['max_load']['mean']:>7.1f}")
        for o in ("istar", "icomp"):
            v = r["verdict"][o]
            print(f"    {o}: expected rewards consolidation={v['expected_rewards_consolidation']}"
                  f" | worst-case penalises it={v['worst_case_penalises_consolidation']}"
                  f" | rho(locality, E)={v['rho_locality_vs_expected']} | rho(locality, max)={v['rho_locality_vs_worst']}")
        sa = r["verdict"]["subadditivity"]
        print(f"    subadditivity: sum_n I*(rr) / I*(all-on-one) = {sa['istar_ratio']:.2f};"
              f" I_comp {sa['icomp_ratio']:.2f}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--replica-scenarios", nargs="+", default=["atm_system", "av_system"])
    ap.add_argument("--placement-scenarios", nargs="+", default=["atm_system", "av_system"])
    ap.add_argument("--realworld", default="realworld_autoware_ros2")
    ap.add_argument("--out", default="results/placement_objective_probe.json")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for noisy in ("saag", "reproduce.ahp_sensitivity"):
        logging.getLogger(noisy).setLevel(logging.ERROR)

    micro = micro_check()
    p1 = [part1(s) for s in args.replica_scenarios]
    p2 = [part2(s) for s in args.placement_scenarios + [args.realworld]]
    print_summary(micro, p1, p2)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "provenance": stamp(replica_scenarios=args.replica_scenarios,
                            placement_scenarios=args.placement_scenarios + [args.realworld],
                            injector_seeds=INJECTOR_SEEDS, placement_seeds=PLACEMENT_SEEDS,
                            colocate_slack=COLOCATE_SLACK),
        "micro": micro, "part1_replica_semantics": p1, "part2_placement": p2,
    }, indent=2, default=str))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
