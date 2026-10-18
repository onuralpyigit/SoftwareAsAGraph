"""
Placement-redundancy probe: does redundancy create a locality / reliability tension?

Follow-up to reproduce/placement_objective_probe.py, which found that with one
host per component, expected blast radius *rewards* consolidation. That probe
could not test redundancy, because the oracles killed a replicated component
when any one of its hosts failed. This one runs on the `replica_aware` flag.

Part A -- flag check (atm, av).
    Real `replica_aware=True` Node I* must equal the placement_objective_probe's
    emulated replica-aware arm (inject n after deleting n from every replicated
    component's host list). Off, labels are the published ones.

Part B -- replica placement under correlated failures (atm, av).
    A single host failure can never kill a replicated component under
    replica-aware semantics, so replica placement only matters when failures are
    correlated. Hosts are grouped into failure domains of 2 (seeded grouping) and
    a domain outage takes down all its hosts. Emulated by collapsing each domain
    into one Node and injecting that Node; a component whose hosts all fall in
    the domain dies, one with a host elsewhere survives.

    Primaries are placed by `colocate` (locality-greedy) or `round_robin`
    (spread), from placement_objective_probe. The authored multi-hosted set gets
    one replica under each policy:
      none          replicas dropped
      same_domain   replica on the other host of the primary's domain
                    (keeps replication traffic in-domain)
      cross_domain  replica on the least-loaded host of another domain
                    (anti-affinity; replication traffic crosses domains)
    Scored with I* (FaultInjector) and I_comp (FailureSimulator), both with the
    flag on and off. Objectives over domains, uniform p: mean and max.
    Cost proxies: share of topic-weighted (subscriber, publisher) pairs whose
    primaries sit in different domains, and number of cross-domain replicas.

Usage:
    python reproduce/placement_redundancy_probe.py
"""

from __future__ import annotations

import argparse
import copy
import json
import logging
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from reproduce._provenance import stamp
from reproduce.network_topology_probe import SEED, _graph_data
from reproduce.placement_objective_probe import (
    INJECTOR_SEEDS, PLACEMENT_SEEDS, Placement, host_ids, hosts_of, load,
    place_colocate, place_round_robin, pubsub_pairs, with_placement,
)

logger = logging.getLogger(__name__)

DOMAIN_SIZE = 2
PRIMARY = {"colocate": place_colocate, "round_robin": place_round_robin}
POLICIES = ("none", "same_domain", "cross_domain")


# ── Oracles with the flag ────────────────────────────────────────────────────

def istar_nodes(topo: Dict[str, Any], replica_aware: bool, node_ids=None) -> Dict[str, float]:
    from saag.core.graph_io import build_graph_from_json
    from saag.simulation.fault_injector import FaultInjector

    inj = FaultInjector(graph=build_graph_from_json(topo), seeds=INJECTOR_SEEDS,
                        cascade_depth_limit=0, qos_factor_mode="ladder",
                        replica_aware=replica_aware)
    res = inj.run(node_types=["Node"], node_ids=node_ids)
    return {nid: float(r.impact_score) for nid, r in res.records.items()}


def icomp_nodes(topo: Dict[str, Any], replica_aware: bool) -> Dict[str, float]:
    from saag.simulation.failure_simulator import FailureSimulator
    from saag.simulation.graph import SimulationGraph
    from saag.simulation.service import SimulationService

    graph = SimulationGraph(_graph_data(topo, None))
    sim = FailureSimulator(graph, qos_weighting=True, network_gating=False,
                           replica_aware=replica_aware)
    SimulationService._prime_baseline_flows(graph, sim)
    return {r.target_id: float(r.impact.composite_impact)
            for r in sim.simulate_exhaustive(layer="system", seed=SEED) if r.target_type == "Node"}


# ── Part A ───────────────────────────────────────────────────────────────────

def part_a(name: str) -> Dict[str, Any]:
    topo = load(name)
    hosts = host_ids(topo)
    authored = hosts_of(topo)
    real = istar_nodes(topo, replica_aware=True)
    emulated = {}
    for n in hosts:
        pl = {c: ([h for h in hs if h != n] if len(hs) > 1 and n in hs else list(hs))
              for c, hs in authored.items()}
        emulated[n] = istar_nodes(with_placement(topo, pl), replica_aware=False, node_ids=[n])[n]
    off = istar_nodes(topo, replica_aware=False)
    return {"scenario": name,
            "max_abs_diff_real_vs_emulated": max(abs(real[h] - emulated[h]) for h in hosts),
            "istar_mean_off": statistics.fmean(off[h] for h in hosts),
            "istar_mean_on": statistics.fmean(real[h] for h in hosts),
            "per_node": {"off": off, "on": real, "emulated": emulated}}


# ── Part B ───────────────────────────────────────────────────────────────────

def domains_for(hosts: List[str], rng: random.Random) -> Dict[str, str]:
    order = list(hosts)
    rng.shuffle(order)
    return {h: f"D{i // DOMAIN_SIZE}" for i, h in enumerate(order)}


def add_replicas(primary: Placement, replicated: List[str], dom: Dict[str, str],
                 policy: str) -> Placement:
    out = {c: list(hs) for c, hs in primary.items()}
    if policy == "none":
        return out
    load = defaultdict(int)
    for hs in out.values():
        for h in hs:
            load[h] += 1
    members = defaultdict(list)
    for h, d in dom.items():
        members[d].append(h)
    for c in replicated:
        p = out[c][0]
        if policy == "same_domain":
            cand = [h for h in members[dom[p]] if h != p]
        else:
            cand = [h for h in dom if dom[h] != dom[p]]
        if not cand:
            continue
        h = min(sorted(cand), key=lambda x: load[x])
        out[c].append(h)
        load[h] += 1
    return out


def collapse(topo: Dict[str, Any], placement: Placement, dom: Dict[str, str]) -> Dict[str, Any]:
    """Topology whose Nodes are the failure domains."""
    t = with_placement(topo, {c: sorted({dom[h] for h in hs}) for c, hs in placement.items()})
    t = copy.deepcopy(t)
    t["nodes"] = [{"id": d, "name": d} for d in sorted(set(dom.values()))]
    seen, links = set(), []
    for e in t["relationships"].get("connects_to", []):
        a, b = dom.get(e["from"], e["from"]), dom.get(e["to"], e["to"])
        if a != b and (a, b) not in seen:
            seen.add((a, b))
            links.append({**e, "from": a, "to": b})
    t["relationships"]["connects_to"] = links
    return t


def cross_domain_traffic(pairs, placement: Placement, dom: Dict[str, str]) -> float:
    total = sum(pairs.values())
    cross = sum(w for (s, p), w in pairs.items()
                if s in placement and p in placement
                and dom[placement[s][0]] != dom[placement[p][0]])
    return cross / total if total else float("nan")


def part_b(name: str) -> Dict[str, Any]:
    topo = load(name)
    hosts = host_ids(topo)
    authored = hosts_of(topo)
    comps = sorted(authored)
    replicated = sorted(c for c, hs in authored.items() if len(hs) > 1)
    pairs = pubsub_pairs(topo)

    rows = []
    for seed in PLACEMENT_SEEDS:
        dom = domains_for(hosts, random.Random(1000 + seed))
        for pname, fn in PRIMARY.items():
            primary = fn(comps, hosts, topo, random.Random(seed))
            for policy in POLICIES:
                pl = add_replicas(primary, replicated, dom, policy)
                t = collapse(topo, pl, dom)
                row = {"seed": seed, "primary": pname, "policy": policy,
                       "cross_domain_traffic": cross_domain_traffic(pairs, pl, dom),
                       "cross_domain_replicas": sum(len({dom[h] for h in pl[c]}) > 1 for c in replicated)}
                for flag in (False, True):
                    tag = "on" if flag else "off"
                    for oname, fn_o in (("istar", istar_nodes), ("icomp", icomp_nodes)):
                        s = fn_o(t, flag)
                        v = [s.get(d, 0.0) for d in sorted(set(dom.values()))]
                        row[f"{oname}_{tag}_mean"] = statistics.fmean(v)
                        row[f"{oname}_{tag}_max"] = max(v)
                rows.append(row)
                logger.info("%s seed=%d %-11s %-12s I*on E=%.3f max=%.3f | off E=%.3f | xtraffic=%.2f",
                            name, seed, pname, policy, row["istar_on_mean"], row["istar_on_max"],
                            row["istar_off_mean"], row["cross_domain_traffic"])

    metrics = [k for k in rows[0] if k not in ("seed", "primary", "policy")]
    table = {}
    for pname in PRIMARY:
        for policy in POLICIES:
            rs = [r for r in rows if r["primary"] == pname and r["policy"] == policy]
            table[f"{pname}/{policy}"] = {k: statistics.fmean(r[k] for r in rs) for k in metrics}

    def paired(metric, a, b):
        """Per (seed, primary) difference a - b of *metric*; sign counts."""
        d = []
        for seed in PLACEMENT_SEEDS:
            for pname in PRIMARY:
                ra = next(r for r in rows if r["seed"] == seed and r["primary"] == pname and r["policy"] == a)
                rb = next(r for r in rows if r["seed"] == seed and r["primary"] == pname and r["policy"] == b)
                d.append(ra[metric] - rb[metric])
        return {"mean": statistics.fmean(d), "n_neg": sum(x < -1e-9 for x in d),
                "n_pos": sum(x > 1e-9 for x in d), "n": len(d)}

    verdict = {f"{o}_{tag}_{agg}": paired(f"{o}_{tag}_{agg}", "cross_domain", "same_domain")
               for o in ("istar", "icomp") for tag in ("off", "on") for agg in ("mean", "max")}
    verdict["cross_domain_traffic"] = paired("cross_domain_traffic", "cross_domain", "same_domain")
    verdict["colocate_vs_round_robin_traffic"] = statistics.fmean(
        table[f"colocate/{p}"]["cross_domain_traffic"] - table[f"round_robin/{p}"]["cross_domain_traffic"]
        for p in POLICIES)
    return {"scenario": name, "n_hosts": len(hosts), "n_replicated": len(replicated),
            "domain_size": DOMAIN_SIZE, "table": table, "cross_minus_same": verdict, "rows": rows}


def print_summary(pa, pb) -> None:
    print("\n### Part A -- real flag vs emulated replica-aware I* (single-host injection)")
    for r in pa:
        print(f"  {r['scenario']}: max |real - emulated| = {r['max_abs_diff_real_vs_emulated']:.2e};"
              f" mean Node I* off {r['istar_mean_off']:.3f} -> on {r['istar_mean_on']:.3f}")
    print("\n### Part B -- replica placement under domain outages (mean over 3 seeds)")
    for r in pb:
        print(f"\n  {r['scenario']} ({r['n_hosts']} hosts, domains of {r['domain_size']}, {r['n_replicated']} replicated)")
        print(f"    {'arm':26s} {'I*on E':>7s} {'I*on max':>8s} {'I*off E':>7s} {'Icomp on E':>10s}"
              f" {'Icomp off E':>11s} {'xtraffic':>8s} {'xrepl':>5s}")
        for arm, m in r["table"].items():
            print(f"    {arm:26s} {m['istar_on_mean']:7.3f} {m['istar_on_max']:8.3f} {m['istar_off_mean']:7.3f}"
                  f" {m['icomp_on_mean']:10.3f} {m['icomp_off_mean']:11.3f} {m['cross_domain_traffic']:8.2f}"
                  f" {m['cross_domain_replicas']:5.1f}")
        print("    cross_domain - same_domain (mean, #neg/#pos of n):")
        for k, v in r["cross_minus_same"].items():
            if isinstance(v, dict):
                print(f"      {k:22s} {v['mean']:+.3f}  {v['n_neg']}/{v['n_pos']} of {v['n']}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenarios", nargs="+", default=["atm_system", "av_system"])
    ap.add_argument("--out", default="results/placement_redundancy_probe.json")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for noisy in ("saag", "reproduce.ahp_sensitivity"):
        logging.getLogger(noisy).setLevel(logging.ERROR)

    pa = [part_a(s) for s in args.scenarios]
    pb = [part_b(s) for s in args.scenarios]
    print_summary(pa, pb)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "provenance": stamp(scenarios=args.scenarios, injector_seeds=INJECTOR_SEEDS,
                            placement_seeds=PLACEMENT_SEEDS, domain_size=DOMAIN_SIZE),
        "part_a_flag_check": pa, "part_b_replica_placement": pb,
    }, indent=2, default=str))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
