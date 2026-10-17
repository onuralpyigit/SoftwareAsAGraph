"""
Network-topology probe: does modelling switches/routers move the oracle rankings?

Prototype measurement for replacing the host-to-host CONNECTS_TO mesh with a
switched fabric (Node -> top-of-rack switch -> core router), before any schema,
generator or corpus change. Nothing here touches data/scenarios/.

Arms, per scenario (FailureSimulator composite I_comp, system layer):

    A   committed topology: random host mesh, current network cascade
    B   same system on a switched fabric, network_gating=False
    C   same fabric, network_gating=True (delivery needs a live physical path)

A->B isolates what the fabric does to the existing terms (fragmentation, the
host-isolation rule); B->C isolates the gating itself. Each fabric is built for
several rack assignments (seeded shuffles of the hosts) and for one or two core
routers, so the movement is reported as a spread rather than one draw.

Also reported:
  * rho(Q, I_comp) per arm -- Q(v) is the RM score, which never reads
    CONNECTS_TO, so any change in this is the validation verdict moving;
  * where the network devices land in arm C's ranking;
  * I*(v) from FaultInjector on the committed vs the switched topology.

Usage:
    python reproduce/network_topology_probe.py
    python reproduce/network_topology_probe.py --scenarios atm_system --rack-seeds 0 1
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
from pathlib import Path
from typing import Any, Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scipy.stats import spearmanr

from reproduce._provenance import stamp

logger = logging.getLogger(__name__)

SCENARIOS_DIR = Path("data/scenarios")
DEFAULT_SCENARIOS = ["atm_system", "financial_trading_system", "iot_smart_city_system"]
SCORED_TYPES = ("Application", "Broker", "Node")
SEED = 42

#: Descriptive link properties of the proposed CONNECTS_TO schema. Carried on the
#: edges so the fabric looks like the proposal, but nothing in the oracles reads them.
HOST_LINK = {"interface": "eth0", "capacity_bps": 10_000_000_000}
UPLINK = {"capacity_bps": 40_000_000_000}


# ── Fabric construction ──────────────────────────────────────────────────────

def build_fabric(
    topology: Dict[str, Any], rack_size: int, n_cores: int, rack_seed: int
) -> Dict[str, Any]:
    """Two-tier fabric: hosts in racks of *rack_size* behind one ToR switch each,
    every ToR uplinked to each of *n_cores* core routers.

    Returns ``{"devices": [...], "connects_to": [...]}`` in topology-JSON shape.
    Host order is shuffled by *rack_seed*: the corpus carries no placement
    information, so which hosts share a rack is a free parameter.
    """
    hosts = sorted(n["id"] for n in topology["nodes"])
    random.Random(rack_seed).shuffle(hosts)
    racks = [hosts[i:i + rack_size] for i in range(0, len(hosts), rack_size)]

    devices, links = [], []
    cores = [f"CORE{j}" for j in range(n_cores)]
    for core in cores:
        devices.append({"id": core, "name": core, "role": "router"})
    for i, rack in enumerate(racks):
        tor = f"TOR{i}"
        devices.append({"id": tor, "name": tor, "role": "switch"})
        links += [{"from": h, "to": tor, **HOST_LINK} for h in rack]
        links += [{"from": tor, "to": core, "interface": f"uplink{j}", **UPLINK}
                  for j, core in enumerate(cores)]
    return {"devices": devices, "connects_to": links}


def _graph_data(topology: Dict[str, Any], fabric: Dict[str, Any] | None):
    """GraphData for the simulator, with CONNECTS_TO swapped for *fabric* if given.

    Device weight is the worst case of what sits beneath it (max host weight for
    a ToR, max ToR weight for a core) -- the same worst-case lift Rules 3-4 use.
    """
    from saag.core.models import ComponentData, EdgeData
    from saag.infrastructure.memory_repo import MemoryRepository

    repo = MemoryRepository()
    repo.save_graph(copy.deepcopy(topology), clear=True)
    gd = repo.get_graph_data(include_raw=True)
    if fabric is None:
        return gd

    gd.edges = [e for e in gd.edges if e.relation_type != "CONNECTS_TO"]
    weight = {c.id: c.weight for c in gd.components}
    dev_ids = {d["id"] for d in fabric["devices"]}
    below: Dict[str, List[str]] = {}
    for link in fabric["connects_to"]:
        below.setdefault(link["to"], []).append(link["from"])
    for d in sorted(dev_ids, key=lambda x: x.startswith("CORE")):   # ToRs first
        weight[d] = max(weight.get(c, 1.0) for c in below[d])

    for d in fabric["devices"]:
        gd.components.append(ComponentData(
            id=d["id"], component_type="NetworkDevice", weight=weight[d["id"]],
            properties={k: v for k, v in d.items() if k != "id"},
        ))
    for link in fabric["connects_to"]:
        src, dst = link["from"], link["to"]
        gd.edges.append(EdgeData(
            source_id=src, target_id=dst,
            source_type="NetworkDevice" if src in dev_ids else "Node",
            target_type="NetworkDevice",
            dependency_type="connects_to", relation_type="CONNECTS_TO",
            properties={k: v for k, v in link.items() if k not in ("from", "to")},
        ))
    return gd


# ── Oracles ──────────────────────────────────────────────────────────────────

def failure_sim_scores(gd, gating: bool, devices: List[str]) -> Dict[str, Tuple[str, float]]:
    """I_comp(v) for every App/Broker/Node, plus every device in *devices*.

    Same path as convergent_validity._failure_simulator_labels: flows primed
    through SimulationService, exhaustive sweep seeded at 42. Devices are not in
    any layer's analyze_types yet, so they are simulated one by one with the
    same per-component seed derivation the sweep uses.
    """
    from saag.simulation.failure_simulator import FailureSimulator
    from saag.simulation.graph import SimulationGraph
    from saag.simulation.models import FailureScenario
    from saag.simulation.service import SimulationService

    graph = SimulationGraph(gd)
    sim = FailureSimulator(graph, qos_weighting=True, network_gating=gating)
    SimulationService._prime_baseline_flows(graph, sim)
    out = {
        r.target_id: (r.target_type, float(r.impact.composite_impact))
        for r in sim.simulate_exhaustive(layer="system", seed=SEED)
    }
    for d in devices:
        res = sim.simulate(FailureScenario(target_ids=[d], seed=sim._derive_seed(SEED, d)))
        out[d] = ("NetworkDevice", float(res.impact.composite_impact))
    return out


def rm_scores(topology: Dict[str, Any]) -> Dict[str, float]:
    """Q(v), the RM composite. Independent of CONNECTS_TO, so computed once."""
    from saag.analysis.service import AnalysisService
    from saag.infrastructure.memory_repo import MemoryRepository
    from saag.prediction.service import PredictionService

    repo = MemoryRepository()
    repo.save_graph(copy.deepcopy(topology), clear=True)
    quality = PredictionService().predict_quality(AnalysisService(repo).analyze_layer("system").structural)
    return {c.id: float(c.scores.overall) for c in quality.components}


def fault_injector_scores(topology: Dict[str, Any]) -> Dict[str, float]:
    """I*(v) for App/Broker/Library/Node, as convergent_validity runs it (+ Node)."""
    from saag.core.graph_io import build_graph_from_json
    from saag.simulation.fault_injector import FaultInjector

    injector = FaultInjector(graph=build_graph_from_json(topology), seeds=[SEED],
                             cascade_depth_limit=0, qos_factor_mode="ladder")
    result = injector.run(node_types=["Application", "Broker", "Library", "Node"])
    return {nid: float(rec.impact_score) for nid, rec in result.records.items()}


# ── Comparison ───────────────────────────────────────────────────────────────

def _topk(scores: Dict[str, float], k: int) -> set:
    return {cid for cid, _ in sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:k]}


def _rho(a: Dict[str, float], b: Dict[str, float], keys: List[str]) -> float | None:
    if len(keys) < 3:
        return None
    x, y = [a[k] for k in keys], [b[k] for k in keys]
    if len(set(x)) < 2 or len(set(y)) < 2:
        return None
    return float(spearmanr(x, y).statistic)


def compare(a: Dict[str, Tuple[str, float]], b: Dict[str, Tuple[str, float]]) -> Dict[str, Any]:
    """Rank movement between two I_comp label sets over the shared App/Broker/Node ids."""
    keys = sorted(k for k in a if k in b and a[k][0] in SCORED_TYPES)
    av = {k: a[k][1] for k in keys}
    bv = {k: b[k][1] for k in keys}
    k = max(3, round(0.1 * len(keys)))
    deltas = [abs(av[x] - bv[x]) for x in keys]
    out = {
        "n": len(keys),
        "rho": _rho(av, bv, keys),
        f"top{k}_overlap": len(_topk(av, k) & _topk(bv, k)) / k,
        "n_changed": sum(d > 1e-9 for d in deltas),
        "max_abs_delta": max(deltas, default=0.0),
    }
    for t in SCORED_TYPES:
        tk = [x for x in keys if a[x][0] == t]
        out[f"rho_{t}"] = _rho(av, bv, tk)
    return out


def validation_rho(q: Dict[str, float], i: Dict[str, Tuple[str, float]]) -> Dict[str, Any]:
    keys = sorted(k for k in i if k in q and i[k][0] in SCORED_TYPES)
    iv = {k: i[k][1] for k in keys}
    out = {"all": _rho(q, iv, keys)}
    for t in SCORED_TYPES:
        out[t] = _rho(q, iv, [k for k in keys if i[k][0] == t])
    return out


def device_placement(c: Dict[str, Tuple[str, float]]) -> List[Dict[str, Any]]:
    """Rank of each device among everything scored in arm C, and among infra."""
    ranked = sorted(c.items(), key=lambda kv: (-kv[1][1], kv[0]))
    infra = [kv for kv in ranked if kv[1][0] in ("Node", "NetworkDevice")]
    rank_all = {cid: i + 1 for i, (cid, _) in enumerate(ranked)}
    rank_infra = {cid: i + 1 for i, (cid, _) in enumerate(infra)}
    return [
        {"id": cid, "impact": v, "rank_all": rank_all[cid], "of_all": len(ranked),
         "rank_infra": rank_infra[cid], "of_infra": len(infra)}
        for cid, (t, v) in ranked if t == "NetworkDevice"
    ]


# ── Driver ───────────────────────────────────────────────────────────────────

def run_scenario(name: str, rack_size: int, cores: List[int], rack_seeds: List[int]) -> Dict[str, Any]:
    topology = json.loads((SCENARIOS_DIR / f"{name}.json").read_text())
    q = rm_scores(topology)
    arm_a = failure_sim_scores(_graph_data(topology, None), gating=False, devices=[])

    istar_orig = fault_injector_scores(topology)
    variants = []
    for n_cores in cores:
        for rs in rack_seeds:
            fabric = build_fabric(topology, rack_size, n_cores, rs)
            dev_ids = [d["id"] for d in fabric["devices"]]
            gd = _graph_data(topology, fabric)
            arm_b = failure_sim_scores(gd, gating=False, devices=dev_ids)
            arm_c = failure_sim_scores(_graph_data(topology, fabric), gating=True, devices=dev_ids)

            switched = copy.deepcopy(topology)
            switched["relationships"]["connects_to"] = fabric["connects_to"]
            istar_sw = fault_injector_scores(switched)
            shared = [k for k in istar_orig if k in istar_sw]

            variants.append({
                "cores": n_cores, "rack_seed": rs, "n_devices": len(dev_ids),
                "A_vs_B": compare(arm_a, arm_b),
                "B_vs_C": compare(arm_b, arm_c),
                "A_vs_C": compare(arm_a, arm_c),
                "validation_rho": {"A": validation_rho(q, arm_a), "B": validation_rho(q, arm_b),
                                   "C": validation_rho(q, arm_c)},
                "devices_C": device_placement(arm_c),
                "istar_max_abs_delta": max((abs(istar_orig[k] - istar_sw[k]) for k in shared), default=0.0),
                "istar_n": len(shared),
            })
            logger.info("%s cores=%d seed=%d  rho(A,C)=%s", name, n_cores, rs,
                        variants[-1]["A_vs_C"]["rho"])
    return {"scenario": name, "n_hosts": len(topology["nodes"]),
            "n_mesh_links": len(topology["relationships"].get("connects_to", [])),
            "variants": variants}


def _spread(vals: List[float | None]) -> str:
    vals = [v for v in vals if v is not None and not math.isnan(v)]
    if not vals:
        return "n/a"
    if len(vals) == 1:
        return f"{vals[0]:.3f}"
    return f"{statistics.fmean(vals):.3f} [{min(vals):.3f}, {max(vals):.3f}]"


def print_summary(results: List[Dict[str, Any]]) -> None:
    for r in results:
        print(f"\n=== {r['scenario']}  ({r['n_hosts']} hosts, {r['n_mesh_links']} mesh links) ===")
        for n_cores in sorted({v["cores"] for v in r["variants"]}):
            vs = [v for v in r["variants"] if v["cores"] == n_cores]
            print(f"  -- {n_cores} core router(s), {len(vs)} rack assignments: mean [min, max]")
            for pair in ("A_vs_B", "B_vs_C", "A_vs_C"):
                topk = next(k for k in vs[0][pair] if k.endswith("_overlap"))
                print(f"    {pair}: rho {_spread([v[pair]['rho'] for v in vs])}"
                      f" | App {_spread([v[pair]['rho_Application'] for v in vs])}"
                      f" | Broker {_spread([v[pair]['rho_Broker'] for v in vs])}"
                      f" | Node {_spread([v[pair]['rho_Node'] for v in vs])}"
                      f" | {topk} {_spread([v[pair][topk] for v in vs])}"
                      f" | changed {_spread([v[pair]['n_changed'] for v in vs])}/{vs[0][pair]['n']}")
            for arm in ("A", "B", "C"):
                print(f"    rho(Q, I_comp) arm {arm}: all {_spread([v['validation_rho'][arm]['all'] for v in vs])}"
                      f" | App {_spread([v['validation_rho'][arm]['Application'] for v in vs])}"
                      f" | Node {_spread([v['validation_rho'][arm]['Node'] for v in vs])}")
            best = [min(d["rank_all"] for d in v["devices_C"]) for v in vs]
            print(f"    best device rank in C (of {vs[0]['devices_C'][0]['of_all']}): {_spread(best)}")
            print(f"    I* max |delta| (committed vs switched): {max(v['istar_max_abs_delta'] for v in vs):.3g}"
                  f" over {vs[0]['istar_n']} components")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenarios", nargs="+", default=DEFAULT_SCENARIOS)
    ap.add_argument("--rack-size", type=int, default=4)
    ap.add_argument("--cores", nargs="+", type=int, default=[1, 2])
    ap.add_argument("--rack-seeds", nargs="+", type=int, default=[0, 1, 2, 3, 4])
    ap.add_argument("--out", default="results/network_topology_probe.json")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for noisy in ("saag", "reproduce.ahp_sensitivity"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    results = [run_scenario(s, args.rack_size, args.cores, args.rack_seeds) for s in args.scenarios]
    print_summary(results)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "provenance": stamp(scenarios=args.scenarios, rack_size=args.rack_size, cores=args.cores,
                            rack_seeds=args.rack_seeds, seed=SEED, qos_weighting=True, layer="system"),
        "results": results,
    }, indent=2))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
