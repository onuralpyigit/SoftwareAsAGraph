"""
Heuristic placements: the comparison baselines, and the fallback search used
when the MILP is too large or runs out of time.

Baselines (all respect pins, capacity and anti-affinity where they can, try to
respect deadlines, and report what they could not satisfy):
    random        a random allowed host per copy
    ffd           first-fit decreasing bin packing (concentrates load)
    k8s_spread    topology spread: least-loaded domain, then host
    indeg_spread  components in decreasing dependent count, spread so no domain
                  collects much of the single-copy dependent mass
    authored      the topology's own RUNS_ON, when every component has one
"""

from __future__ import annotations

import random
import time
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional, Tuple

from .instance import Instance, Placement

BASELINES = ("random", "ffd", "k8s_spread", "indeg_spread")

Key = Callable[[Placement], Tuple]


class Greedy:
    """Place copies one at a time, choosing the best-keyed host that fits."""

    def __init__(self, inst: Instance):
        self.inst = inst

    def fits(self, pl: Placement, cpu, mem, c: str, n: str, deadline: bool) -> bool:
        inst = self.inst
        mine = pl.get(c, ())
        if n not in inst.allowed[c] or n in mine:
            return False
        if inst.spec.anti_affinity == "domain" and any(inst.dom[h] == inst.dom[n] for h in mine):
            return False
        if cpu[n] + inst.cpu[c] > inst.cap_cpu[n] + 1e-9 or mem[n] + inst.mem[c] > inst.cap_mem[n] + 1e-9:
            return False
        return not deadline or self.deadline_fits(pl, c, n)

    def deadline_fits(self, pl: Placement, c: str, n: str) -> bool:
        """Would a copy of *c* on *n* break a deadline against copies already placed?"""
        inst = self.inst
        for _, p, s, b, dl in inst.deadline_of.get(c, ()):
            ps = [n] if c == p else pl.get(p, [])
            ss = [n] if c == s else pl.get(s, [])
            bs = [None] if b is None else ([n] if c == b else pl.get(b, []))
            if any(inst.path_latency(a, k, m) > dl for a in ps for k in bs for m in ss):
                return False
        return True

    def run(self, order: List[str], key: Callable[[str, str, Placement, dict, dict], Any]) -> Placement:
        inst = self.inst
        pl: Placement = {}
        cpu, mem = defaultdict(float), defaultdict(float)
        for c in order:
            for _ in range(inst.r[c]):
                cand = [n for n in inst.hosts if self.fits(pl, cpu, mem, c, n, True)] \
                    or [n for n in inst.hosts if self.fits(pl, cpu, mem, c, n, False)] \
                    or [n for n in inst.allowed[c] if n not in pl.get(c, ())] \
                    or [n for n in inst.hosts if n not in pl.get(c, ())]
                if not cand:
                    break
                n = min(cand, key=lambda h: key(c, h, pl, cpu, mem))
                pl.setdefault(c, []).append(n)
                cpu[n] += inst.cpu[c]
                mem[n] += inst.mem[c]
        return self.repair(pl, cpu, mem) if inst.spec.use_all_hosts else pl

    def repair(self, pl: Placement, cpu, mem) -> Placement:
        """Move the smallest movable copy off the busiest host onto each empty host."""
        inst = self.inst
        for n in inst.hosts:
            if any(n in hs for hs in pl.values()):
                continue
            moves = []
            for c, hs in sorted(pl.items()):
                for i, h in enumerate(hs):
                    if sum(h in v for v in pl.values()) < 2:
                        continue
                    rest = {**pl, c: hs[:i] + hs[i + 1:]}
                    c2, m2 = defaultdict(float, cpu), defaultdict(float, mem)
                    c2[h] -= inst.cpu[c]
                    m2[h] -= inst.mem[c]
                    if self.fits(rest, c2, m2, c, n, True):
                        moves.append((-cpu[h], inst.cpu[c], c, i))
            if moves:
                _, _, c, i = min(moves)
                h = pl[c][i]
                pl[c][i] = n
                cpu[h] -= inst.cpu[c]
                mem[h] -= inst.mem[c]
                cpu[n] += inst.cpu[c]
                mem[n] += inst.mem[c]
        return pl


def baselines(inst: Instance, seed: int, names=BASELINES) -> Dict[str, Placement]:
    """The named baseline placements, brokers placed first."""
    g = Greedy(inst)
    rng = random.Random(seed)

    def util(h, cpu, mem):
        return max(cpu[h] / inst.cap_cpu[h], mem[h] / inst.cap_mem[h])

    cpu_max, mem_max = max(inst.cap_cpu.values()), max(inst.cap_mem.values())
    size = {c: inst.cpu[c] / cpu_max + inst.mem[c] / mem_max for c in inst.comps}

    def brokers_first(comps: List[str]) -> List[str]:
        return [c for c in comps if c in inst.brokers] + [c for c in comps if c not in inst.brokers]

    def dom_count(pl, d, w=lambda c: 1.0):
        return sum(w(c) for c, hs in pl.items() for h in hs if inst.dom[h] == d)

    def host_count(pl, h, w=lambda c: 1.0):
        return sum(w(c) for c, hs in pl.items() if h in hs)

    out: Dict[str, Placement] = {}
    if "random" in names:
        order = list(inst.comps)
        rng.shuffle(order)
        out["random"] = g.run(brokers_first(order), lambda c, h, pl, cp, mm: rng.random())
    if "ffd" in names:
        out["ffd"] = g.run(brokers_first(sorted(inst.comps, key=lambda c: (-size[c], c))),
                           lambda c, h, pl, cp, mm: inst.hosts.index(h))
    if "k8s_spread" in names:
        out["k8s_spread"] = g.run(brokers_first(sorted(inst.comps)), lambda c, h, pl, cp, mm: (
            dom_count(pl, inst.dom[h]), host_count(pl, h), util(h, cp, mm), h))
    if "indeg_spread" in names:
        def single(c):
            return float(inst.indeg[c]) if inst.r[c] == 1 else 0.0
        out["indeg_spread"] = g.run(
            brokers_first(sorted(inst.comps, key=lambda c: (-single(c), -inst.indeg[c], c))),
            lambda c, h, pl, cp, mm: (dom_count(pl, inst.dom[h], single) if inst.r[c] == 1 else 0.0,
                                      host_count(pl, h, single), util(h, cp, mm), h))
    return out


def authored(inst: Instance) -> Optional[Placement]:
    """The topology's own RUNS_ON as a placement, or None if any component lacks a host."""
    pl: Placement = defaultdict(list)
    for e in inst.topology.get("relationships", {}).get("runs_on", []):
        if e["from"] in inst.r and e["to"] in inst.hosts and e["to"] not in pl[e["from"]]:
            pl[e["from"]].append(e["to"])
    if any(not pl.get(c) for c in inst.comps):
        return None
    return {c: list(pl[c]) for c in inst.comps}


def hard_violations(inst: Instance, pl: Placement) -> float:
    """Size-weighted violation (see Instance.violation_score); 0 exactly when feasible."""
    return inst.violation_score(pl)


def local_search(inst: Instance, pl: Placement, key: Key, max_passes: int = 50,
                 deadline: Optional[float] = None) -> Placement:
    """First-improvement search over copy relocations and primary swaps."""
    pl = {c: list(hs) for c, hs in pl.items()}
    best = key(pl)
    for _ in range(max_passes):
        improved = False
        for c in inst.comps:
            if deadline is not None and time.monotonic() > deadline:
                return pl
            for i in range(len(pl[c])):
                for n in inst.allowed[c]:
                    if n in pl[c]:
                        continue
                    trial = {**pl, c: pl[c][:i] + [n] + pl[c][i + 1:]}
                    k = key(trial)
                    if k < best:
                        pl, best, improved = trial, k, True
                        break
            for i in range(1, len(pl[c])):
                trial = {**pl, c: [pl[c][i]] + pl[c][:i] + pl[c][i + 1:]}
                k = key(trial)
                if k < best:
                    pl, best, improved = trial, k, True
        if not improved:
            break
    return pl


def heuristic_front(inst: Instance, starts: Dict[str, Placement], points: int,
                    deadline: Optional[float] = None) -> List[Tuple[str, Placement, Dict[str, Any]]]:
    """Weighted-sum local search from the best start, sweeping blast_exp vs traffic."""
    def score(pl):
        return hard_violations(inst, pl), inst.proxy_blast(pl)["blast_exp"], inst.traffic(pl)["traffic"]

    start_name = min(starts, key=lambda n: score(starts[n]) + (n,))
    start = starts[start_name]
    _, b_ref, t_ref = score(start)
    b_ref, t_ref = max(b_ref, 1e-9), max(t_ref, 1e-9)
    out = []
    for k in range(points):
        lam = 1.0 - k / (points - 1) if points > 1 else 1.0

        def key(pl, lam=lam):
            v, b, t = score(pl)
            return (v, lam * b / b_ref + (1 - lam) * t / t_ref)

        pl = local_search(inst, start, key, deadline=deadline)
        out.append((f"front_{k}", pl, {"status": "heuristic", "lambda": lam, "start": start_name}))
    return out
