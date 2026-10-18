"""
Placement trade-off probe: the two fixes placement_redundancy_probe left open.

placement_objective_probe found that expected blast rewards consolidation, but
most of that came from *using fewer hosts* (empty hosts score 0). The
redundancy probe found that anti-affine replicas help once `replica_aware` is
on, but it charged replicas nothing, so anti-affinity looked free. This probe
removes both confounds. All oracles run with `replica_aware=True`.

Traffic model (shared by both parts). A topic t carries B(t) = frequency * size.
Library endpoints are attributed to every application that USES the library.
A flow runs publisher -> first router -> subscriber when the topic is routed,
publisher -> subscriber otherwise. Hot-standby replicas subscribe to their
inputs (to stay warm) but do not publish. Each replica also receives state
sync from its primary, priced at the bandwidth the component publishes.
locality = 1 - cross-host share of hop-bytes.

Part C -- consolidation at a fixed host count (atm, av, autoware).
    k in {H, H/2} used hosts, one host per component, loads balanced (sizes
    differ by at most one and never change). Starting from a balanced random
    placement, pairwise swaps hill-climb toward minimum cross-host traffic and,
    separately, toward maximum. Snapshots along both trajectories give a
    locality sweep in which only *which* components share a host changes.
    Impact = mean Node impact over the k used hosts (I*, I_comp).

Part D -- replica placement with a cost (atm, av, autoware).
    k = H, hosts in failure domains of 2. Primaries: the locality-optimised
    endpoint of Part C, or the balanced random start. Replicated set = apps
    flagged hotstandby. Arms:
      none, same_domain (all), cross_domain (all), and a 50% cross-domain budget
      (rest same_domain) chosen by: random, InDeg (distinct subscribers of the
      component's topics), or I*-marginal (single-flip gain in domain-outage
      E[I*], selected on I* and judged on I_comp as the held-out oracle).
    Failure events: every single host, and every domain (collapsed to one Node).

Usage:
    python reproduce/placement_tradeoff_probe.py
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scipy.stats import spearmanr

from reproduce._provenance import stamp
from reproduce.placement_objective_probe import host_ids, hosts_of, load, with_placement
from reproduce.placement_redundancy_probe import collapse, domains_for, icomp_nodes, istar_nodes

logger = logging.getLogger(__name__)

SEEDS = [0, 1, 2]
SNAPSHOTS = 5            # per hill-climb direction, plus the shared start
PATIENCE = 1500          # proposals without improvement before a climb stops
BUDGET = 0.5
REPLICA_AWARE = True

Single = Dict[str, str]                 # component -> host
Placement = Dict[str, List[str]]        # component -> hosts (primary first)


# ── Traffic model ────────────────────────────────────────────────────────────

class Traffic:
    def __init__(self, topo: Dict[str, Any], comps: List[str]):
        rel = topo["relationships"]
        bw = {t["id"]: float(t.get("frequency", 1.0)) * float(t.get("size", 1.0)) for t in topo["topics"]}
        users = defaultdict(set)
        for e in rel.get("uses", []):
            users[e["to"]].add(e["from"])
        placed = set(comps)

        def resolve(x: str) -> set:
            if x in placed:
                return {x}
            return {a for a in users.get(x, ()) if a in placed}

        pubs, subs = defaultdict(set), defaultdict(set)
        for e in rel.get("publishes_to", []):
            pubs[e["to"]] |= resolve(e["from"])
        for e in rel.get("subscribes_to", []):
            subs[e["to"]] |= resolve(e["from"])
        router = {}
        for e in sorted(rel.get("routes", []), key=lambda e: (e["to"], e["from"])):
            router.setdefault(e["to"], e["from"])
        self.flows: List[Tuple[str, Optional[str], str, float]] = [
            (p, router.get(t) if router.get(t) in placed else None, s, bw.get(t, 1.0))
            for t in sorted(subs) for s in sorted(subs[t]) for p in sorted(pubs.get(t, ())) if p != s
        ]
        self.inputs = defaultdict(list)       # subscriber -> [(pub, router, B)]
        self.out_bw = defaultdict(float)      # component -> bandwidth it publishes
        self.subscribers = defaultdict(set)   # publisher -> distinct subscribers
        for p, b, s, w in self.flows:
            self.inputs[s].append((p, b, w))
            self.subscribers[p].add(s)
        for t, ps in pubs.items():
            for p in ps:
                self.out_bw[p] += bw.get(t, 1.0)
        self.by_comp = defaultdict(list)
        for i, (p, b, s, _) in enumerate(self.flows):
            for c in {p, b, s} - {None}:
                self.by_comp[c].append(i)
        self.total = sum(w * (2 if b else 1) for _, b, _, w in self.flows)

    def flow_cost(self, i: int, h: Single) -> float:
        p, b, s, w = self.flows[i]
        if b is None:
            return w * (h[p] != h[s])
        return w * ((h[p] != h[b]) + (h[b] != h[s]))

    def cross_host(self, h: Single) -> float:
        return sum(self.flow_cost(i, h) for i in range(len(self.flows)))

    def cost_with_replicas(self, pl: Placement, dom: Dict[str, str]) -> Dict[str, float]:
        """Cross-host and cross-domain hop-bytes, including warm-replica inputs and state sync."""
        prim = {c: hs[0] for c, hs in pl.items()}
        xh = xd = 0.0

        def hop(a: str, b: str, w: float) -> None:
            nonlocal xh, xd
            if a != b:
                xh += w
                if dom[a] != dom[b]:
                    xd += w

        for p, b, s, w in self.flows:
            for hs in pl[s]:                       # primary and every warm replica subscribe
                if b is None:
                    hop(prim[p], hs, w)
                else:
                    hop(prim[p], prim[b], w)
                    hop(prim[b], hs, w)
        for c, hs in pl.items():
            for r in hs[1:]:
                hop(hs[0], r, self.out_bw[c])      # state sync primary -> replica
        return {"cross_host": xh / self.total, "cross_domain": xd / self.total}


def climb(tr: Traffic, start: Single, rng: random.Random, sign: int) -> List[Tuple[int, Single]]:
    """Swap hill-climb; sign=+1 minimises cross-host traffic, -1 maximises it. Returns (accepted, placement) trail."""
    h = dict(start)
    comps = sorted(h)
    trail = [(0, dict(h))]
    accepted, idle = 0, 0
    while idle < PATIENCE:
        a, b = rng.sample(comps, 2)
        if h[a] == h[b]:
            idle += 1
            continue
        idx = set(tr.by_comp[a]) | set(tr.by_comp[b])
        before = sum(tr.flow_cost(i, h) for i in idx)
        h[a], h[b] = h[b], h[a]
        after = sum(tr.flow_cost(i, h) for i in idx)
        if sign * (after - before) < -1e-9:
            accepted += 1
            idle = 0
            trail.append((accepted, dict(h)))
        else:
            h[a], h[b] = h[b], h[a]
            idle += 1
    if accepted == 0:
        return [trail[0]]
    picks = sorted({round(accepted * j / SNAPSHOTS) for j in range(1, SNAPSHOTS + 1)})
    by_n = {n: pl for n, pl in trail}
    return [(n, by_n[n]) for n in picks]


def balanced_start(comps: List[str], hosts: List[str], rng: random.Random) -> Single:
    order = list(comps)
    rng.shuffle(order)
    return {c: hosts[i % len(hosts)] for i, c in enumerate(order)}


# ── Oracles over failure events ──────────────────────────────────────────────

def host_impacts(topo, pl: Placement, used: List[str]) -> Dict[str, Dict[str, float]]:
    t = with_placement(topo, pl)
    ist, ic = istar_nodes(t, REPLICA_AWARE), icomp_nodes(t, REPLICA_AWARE)
    return {"istar": {h: ist.get(h, 0.0) for h in used}, "icomp": {h: ic.get(h, 0.0) for h in used}}


def domain_impacts(topo, pl: Placement, dom: Dict[str, str], oracles=("istar", "icomp")):
    t = collapse(topo, pl, dom)
    ds = sorted(set(dom.values()))
    out = {}
    if "istar" in oracles:
        s = istar_nodes(t, REPLICA_AWARE)
        out["istar"] = {d: s.get(d, 0.0) for d in ds}
    if "icomp" in oracles:
        s = icomp_nodes(t, REPLICA_AWARE)
        out["icomp"] = {d: s.get(d, 0.0) for d in ds}
    return out


def _rho(x, y) -> Optional[float]:
    if len(set(x)) < 2 or len(set(y)) < 2:
        return None
    return float(spearmanr(x, y).statistic)


# ── Part C ───────────────────────────────────────────────────────────────────

def part_c(name: str) -> Dict[str, Any]:
    topo = load(name)
    hosts = host_ids(topo)
    comps = sorted(hosts_of(topo))
    tr = Traffic(topo, comps)
    out = {"scenario": name, "n_hosts": len(hosts), "n_components": len(comps),
           "n_flows": len(tr.flows), "by_k": {}, "locality_optimised": {}}
    for k in sorted({len(hosts), max(2, len(hosts) // 2)}, reverse=True):
        rows = []
        for seed in SEEDS:
            rng = random.Random(seed)
            used = sorted(rng.sample(hosts, k))
            start = balanced_start(comps, used, rng)
            for direction, sign in (("toward_local", 1), ("toward_spread", -1)):
                trail = climb(tr, start, random.Random(seed * 7 + sign), sign)
                if direction == "toward_spread":
                    trail = [(0, start)] + trail
                for step, h in trail:
                    imp = host_impacts(topo, {c: [x] for c, x in h.items()}, used)
                    row = {"seed": seed, "direction": direction, "step": step,
                           "locality": 1 - tr.cross_host(h) / tr.total}
                    for o, v in imp.items():
                        row[f"{o}_mean"] = statistics.fmean(v.values())
                        row[f"{o}_max"] = max(v.values())
                    rows.append(row)
                if direction == "toward_local" and k == len(hosts):
                    out["locality_optimised"][seed] = trail[-1][1]
            logger.info("%s k=%d seed=%d done (%d rows)", name, k, seed, len(rows))

        def ends(seed):
            loc = [r for r in rows if r["seed"] == seed and r["direction"] == "toward_local"]
            spr = [r for r in rows if r["seed"] == seed and r["direction"] == "toward_spread"]
            return max(loc, key=lambda r: r["step"]), max(spr, key=lambda r: r["step"])

        summary = {"locality_range": [min(r["locality"] for r in rows), max(r["locality"] for r in rows)]}
        for o in ("istar", "icomp"):
            for agg in ("mean", "max"):
                key = f"{o}_{agg}"
                diffs = [ends(s)[0][key] - ends(s)[1][key] for s in SEEDS]
                summary[key] = {
                    "rho_locality": _rho([r["locality"] for r in rows], [r[key] for r in rows]),
                    "local_minus_spread": statistics.fmean(diffs),
                    "n_local_lower": sum(d < -1e-9 for d in diffs),
                    "n_local_higher": sum(d > 1e-9 for d in diffs),
                    "local_end": statistics.fmean(ends(s)[0][key] for s in SEEDS),
                    "spread_end": statistics.fmean(ends(s)[1][key] for s in SEEDS),
                }
        summary["locality_local_end"] = statistics.fmean(ends(s)[0]["locality"] for s in SEEDS)
        summary["locality_spread_end"] = statistics.fmean(ends(s)[1]["locality"] for s in SEEDS)
        out["by_k"][k] = {"summary": summary, "rows": rows}
    return out


# ── Part D ───────────────────────────────────────────────────────────────────

def with_replicas(primary: Single, replicated: List[str], dom: Dict[str, str],
                  cross: set) -> Placement:
    pl: Placement = {c: [h] for c, h in primary.items()}
    load_ = defaultdict(int)
    for h in primary.values():
        load_[h] += 1
    members = defaultdict(list)
    for h, d in dom.items():
        members[d].append(h)
    for c in replicated:
        p = primary[c]
        cand = ([h for h in dom if dom[h] != dom[p]] if c in cross
                else [h for h in members[dom[p]] if h != p])
        if cand:
            h = min(sorted(cand), key=lambda x: load_[x])
            pl[c].append(h)
            load_[h] += 1
    return pl


def score_arm(topo, tr, pl, dom, hosts) -> Dict[str, float]:
    hi = host_impacts(topo, pl, hosts)
    di = domain_impacts(topo, pl, dom)
    row = dict(tr.cost_with_replicas(pl, dom))
    row["max_load"] = max(sum(h in hs for hs in pl.values()) for h in hosts)
    for o in ("istar", "icomp"):
        row[f"{o}_host_mean"] = statistics.fmean(hi[o].values())
        row[f"{o}_dom_mean"] = statistics.fmean(di[o].values())
        row[f"{o}_worst"] = max(list(hi[o].values()) + list(di[o].values()))
    return row


def part_d(name: str, optimised: Dict[int, Single]) -> Dict[str, Any]:
    topo = load(name)
    hosts = host_ids(topo)
    comps = sorted(hosts_of(topo))
    tr = Traffic(topo, comps)
    replicated = sorted(a["id"] for a in topo["applications"] if a.get("hotstandby") and a["id"] in comps)
    b = max(1, round(BUDGET * len(replicated)))
    rows = []
    for seed in SEEDS:
        dom = domains_for(hosts, random.Random(1000 + seed))
        primaries = {"local_opt": optimised[seed],
                     "balanced_random": balanced_start(comps, hosts, random.Random(seed))}
        for pname, prim in primaries.items():
            base = with_replicas(prim, replicated, dom, set())
            base_dom = statistics.fmean(domain_impacts(topo, base, dom, ("istar",))["istar"].values())
            gain = {}
            for c in replicated:
                pl = with_replicas(prim, replicated, dom, {c})
                gain[c] = base_dom - statistics.fmean(domain_impacts(topo, pl, dom, ("istar",))["istar"].values())
            rng = random.Random(seed + 99)
            arms = {
                "none": {c: [h] for c, h in prim.items()},
                "same_domain": base,
                "cross_domain": with_replicas(prim, replicated, dom, set(replicated)),
                "budget_random": with_replicas(prim, replicated, dom, set(rng.sample(replicated, b))),
                "budget_indeg": with_replicas(prim, replicated, dom, set(
                    sorted(replicated, key=lambda c: (-len(tr.subscribers[c]), c))[:b])),
                "budget_istar_gain": with_replicas(prim, replicated, dom, set(
                    sorted(replicated, key=lambda c: (-gain[c], c))[:b])),
            }
            for arm, pl in arms.items():
                row = {"seed": seed, "primary": pname, "arm": arm, **score_arm(topo, tr, pl, dom, hosts)}
                rows.append(row)
                logger.info("%s seed=%d %-15s %-18s xdom=%.3f Icomp host=%.3f dom=%.3f", name, seed, pname,
                            arm, row["cross_domain"], row["icomp_host_mean"], row["icomp_dom_mean"])
            rows.append({"seed": seed, "primary": pname, "arm": "_istar_gain",
                         "n_positive_gain": sum(g > 1e-9 for g in gain.values()), "gain": gain})
    scored = [r for r in rows if not r["arm"].startswith("_")]
    metrics = [k for k in scored[0] if k not in ("seed", "primary", "arm")]
    table = {}
    for pname in ("local_opt", "balanced_random"):
        for arm in dict.fromkeys(r["arm"] for r in scored):
            rs = [r for r in scored if r["primary"] == pname and r["arm"] == arm]
            table[f"{pname}/{arm}"] = {m: statistics.fmean(r[m] for r in rs) for m in metrics}
    return {"scenario": name, "n_replicated": len(replicated), "budget": b, "table": table, "rows": rows}


# ── Reporting ────────────────────────────────────────────────────────────────

def print_summary(pc, pd) -> None:
    print("\n### Part C -- locality sweep at a fixed number of used hosts (replica_aware on)")
    for r in pc:
        print(f"\n  {r['scenario']} ({r['n_hosts']} hosts, {r['n_components']} placed, {r['n_flows']} flows)")
        for k, v in r["by_k"].items():
            s = v["summary"]
            print(f"    k={k}: locality {s['locality_spread_end']:.2f} (spread end) -> {s['locality_local_end']:.2f} (local end)")
            for key in ("istar_mean", "istar_max", "icomp_mean", "icomp_max"):
                m = s[key]
                rho = "n/a" if m["rho_locality"] is None else f"{m['rho_locality']:+.2f}"
                print(f"      {key:11s} spread {m['spread_end']:.3f} -> local {m['local_end']:.3f}"
                      f"  (local lower {m['n_local_lower']}/3, higher {m['n_local_higher']}/3)  rho(loc)={rho}")
    print("\n### Part D -- replica placement with traffic cost (domains of 2; mean over 3 seeds)")
    for r in pd:
        print(f"\n  {r['scenario']} ({r['n_replicated']} hotstandby, budget {r['budget']})")
        print(f"    {'arm':34s} {'xhost':>6s} {'xdom':>6s} {'load':>5s} {'I*host':>7s} {'I*dom':>6s}"
              f" {'Ichost':>7s} {'Icdom':>6s} {'Icworst':>7s}")
        for arm, m in r["table"].items():
            print(f"    {arm:34s} {m['cross_host']:6.3f} {m['cross_domain']:6.3f} {m['max_load']:5.1f}"
                  f" {m['istar_host_mean']:7.3f} {m['istar_dom_mean']:6.3f} {m['icomp_host_mean']:7.3f}"
                  f" {m['icomp_dom_mean']:6.3f} {m['icomp_worst']:7.3f}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenarios", nargs="+", default=["atm_system", "av_system", "realworld_autoware_ros2"])
    ap.add_argument("--out", default="results/placement_tradeoff_probe.json")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for noisy in ("saag", "reproduce.ahp_sensitivity"):
        logging.getLogger(noisy).setLevel(logging.ERROR)

    pc = [part_c(s) for s in args.scenarios]
    pd = [part_d(r["scenario"], r.pop("locality_optimised")) for r in pc]
    print_summary(pc, pd)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "provenance": stamp(scenarios=args.scenarios, seeds=SEEDS, snapshots=SNAPSHOTS,
                            patience=PATIENCE, budget=BUDGET, replica_aware=REPLICA_AWARE),
        "part_c_fixed_host_count": pc, "part_d_replica_cost": pd,
    }, indent=2, default=str))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
