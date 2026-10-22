"""
Exact placement MILP over scipy.optimize.milp (HiGHS).

Variables
    x[c,n]   binary   a copy of component c runs on host n (0 where c is not allowed)
    y[c,n]   binary   c's primary copy runs on host n
    z[e,c]   [0, 1]   c is down in failure event e (created only where it can be)
    t                 worst-case number of Applications down (epigraph)
    U                 peak utilisation (only with ``balance``)
    v, u     [0, 1]   cross-host traffic indicators

Constraint families (named, so an infeasible model can say which family failed)
    replicas        sum_n x[c,n] = r(c), sum_n y[c,n] = 1, y <= x
    anti_affinity   at most one copy of a replicated c per failure domain
    capacity_cpu    sum_c cpu(c) x[c,n] <= max_utilization * cpu(n); same for memory
    capacity_mem
    deadline        failover-safe: for every copy of the publisher, of the routing
                    broker and of the subscriber, publisher->broker->subscriber
                    latency stays within the topic's deadline
    use_all_hosts   optional: every host carries at least one copy
    coverage        z[e,c] >= sum_{n in e} x[c,n] - (r(c) - 1)   (all copies inside e)
                    z[e,v] >= z[e,u] for every dependency arc v -> u
                    At the optimum z is exactly "c is down in e" (replica-aware).

Objectives
    blast_exp   sum_e p(e) * (Applications down in e)
    blast_max   max_e (Applications down in e)
    traffic     cross-host bytes/s: each publisher primary and each subscriber copy
                pays B(t) = size * frequency when it is not on the routing broker's
                primary host (or, brokerless, on the publisher's primary host)
    balance     U, the peak cpu/memory utilisation

Every set is iterated as a sorted list so column order, and therefore which of
several tied optima HiGHS returns, does not depend on PYTHONHASHSEED.
"""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_matrix

from .instance import Instance, Placement

Expr = Dict[int, float]
Row = Tuple[Expr, float, float]

#: Families an elastic solve may relax to explain an infeasible model.
DIAGNOSED_FAMILIES = ("replicas", "anti_affinity", "capacity_cpu", "capacity_mem", "deadline", "use_all_hosts")

_STATUS = {0: "optimal", 1: "time_limit", 2: "infeasible", 3: "unbounded", 4: "error"}


class ModelTooLarge(RuntimeError):
    """The model would exceed the nonzero budget; use the heuristic path instead."""


class Model:
    """Minimal sparse MILP builder with named constraint families."""

    def __init__(self, max_nonzeros: Optional[int] = None):
        self.lb: List[float] = []
        self.ub: List[float] = []
        self.integer: List[int] = []
        self.rows: List[Row] = []
        self.family: List[str] = []
        self.tag: List[Optional[Dict[str, Any]]] = []
        self.nnz = 0
        self.max_nonzeros = max_nonzeros

    def var(self, lb: float = 0.0, ub: float = 1.0, integer: bool = False) -> int:
        self.lb.append(lb)
        self.ub.append(ub)
        self.integer.append(int(integer))
        return len(self.lb) - 1

    def add(self, coefs: Expr, lo: float = -np.inf, hi: float = np.inf,
            family: str = "", tag: Optional[Dict[str, Any]] = None) -> None:
        self.nnz += len(coefs)
        if self.max_nonzeros is not None and self.nnz > self.max_nonzeros:
            raise ModelTooLarge(f"model exceeds {self.max_nonzeros} nonzeros")
        self.rows.append((coefs, lo, hi))
        self.family.append(family)
        self.tag.append(tag)

    def solve(self, obj: Expr, extra: Sequence[Row] = (), time_limit: Optional[float] = None,
              gap: float = 1e-6) -> Tuple[Optional[np.ndarray], Dict[str, Any]]:
        return _solve(self.lb, self.ub, self.integer, list(self.rows) + list(extra), obj, time_limit, gap)

    def solve_elastic(self, families: Sequence[str] = DIAGNOSED_FAMILIES,
                      time_limit: Optional[float] = None) -> List[Tuple[int, float]]:
        """Relax every row in *families* with slack and minimise total slack.

        Returns (row index, slack) for each row the relaxed optimum violates.
        """
        lb, ub, integer = list(self.lb), list(self.ub), list(self.integer)
        rows: List[Row] = []
        obj: Expr = {}
        slack_of: List[Tuple[int, int]] = []
        for i, (coefs, lo, hi) in enumerate(self.rows):
            if self.family[i] not in families:
                rows.append((coefs, lo, hi))
                continue
            coefs = dict(coefs)
            for bound, sign in ((hi, -1.0), (lo, 1.0)):
                if np.isfinite(bound):
                    s = len(lb)
                    lb.append(0.0)
                    ub.append(np.inf)
                    integer.append(0)
                    coefs[s] = sign
                    obj[s] = 1.0
                    slack_of.append((i, s))
            rows.append((coefs, lo, hi))
        sol, _ = _solve(lb, ub, integer, rows, obj, time_limit, 1e-6)
        if sol is None:
            return []
        total: Dict[int, float] = defaultdict(float)
        for i, s in slack_of:
            total[i] += float(sol[s])
        return sorted(((i, v) for i, v in total.items() if v > 1e-6), key=lambda iv: (-iv[1], iv[0]))


def _solve(lb, ub, integer, rows: List[Row], obj: Expr, time_limit: Optional[float],
           gap: float) -> Tuple[Optional[np.ndarray], Dict[str, Any]]:
    r, c, v = [], [], []
    for i, (coefs, _, _) in enumerate(rows):
        for j, a in coefs.items():
            r.append(i)
            c.append(j)
            v.append(a)
    n = len(lb)
    cost = np.zeros(n)
    for j, a in obj.items():
        cost[j] += a
    options: Dict[str, Any] = {"mip_rel_gap": gap}
    if time_limit is not None:
        options["time_limit"] = max(float(time_limit), 0.1)
    constraints = []
    if rows:
        A = coo_matrix((v, (r, c)), shape=(len(rows), n)).tocsr()
        constraints = [LinearConstraint(A, [lo for _, lo, _ in rows], [hi for _, _, hi in rows])]
    t0 = time.perf_counter()
    res = milp(cost, integrality=np.array(integer), bounds=Bounds(lb, ub), constraints=constraints, options=options)
    retried = False
    if res.status == 2:
        # HiGHS presolve (scipy 1.15) can declare a feasible model infeasible after
        # a lexicographic cap is added; confirm every infeasibility without it.
        res = milp(cost, integrality=np.array(integer), bounds=Bounds(lb, ub), constraints=constraints,
                   options={**options, "presolve": False})
        retried = True
    status = _STATUS.get(int(res.status), "error")
    info = {"status": status, "seconds": round(time.perf_counter() - t0, 4),
            "gap": getattr(res, "mip_gap", None), "objective": None if res.x is None else float(res.fun)}
    if retried:
        info["presolve_retry"] = True
    return (res.x if res.x is not None else None), info


def value(expr: Expr, sol: np.ndarray) -> float:
    return float(sum(a * sol[j] for j, a in expr.items()))


class DeploymentMILP:
    """The placement model for one instance; solve it with :meth:`lexi`."""

    def __init__(self, inst: Instance, balance: bool = False, max_nonzeros: Optional[int] = None,
                 time_limit: Optional[float] = 60.0, deadline: Optional[float] = None):
        self.inst = inst
        self.time_limit = time_limit
        self.deadline = deadline                # time.monotonic() after which no solve starts
        self.skipped: List[str] = []
        self.stage0: Optional[Placement] = None          # first-stage optimum of the last lexi()
        self.traffic_only: Optional[Placement] = None    # traffic-optimal placement, set by front()
        m = self.m = Model(max_nonzeros)

        def ub(c: str, n: str) -> float:
            return 1.0 if n in inst.allowed[c] else 0.0

        self.x = {(c, n): m.var(0.0, ub(c, n), True) for c in inst.comps for n in inst.hosts}
        self.y = {(c, n): m.var(0.0, ub(c, n), True) for c in inst.comps for n in inst.hosts}
        x, y = self.x, self.y

        for c in inst.comps:
            m.add({x[c, n]: 1 for n in inst.hosts}, inst.r[c], inst.r[c], "replicas", {"component": c})
            m.add({y[c, n]: 1 for n in inst.hosts}, 1, 1, "replicas", {"component": c, "primary": True})
            for n in inst.hosts:
                m.add({y[c, n]: 1, x[c, n]: -1}, hi=0, family="replicas", tag={"component": c, "host": n})
            if inst.r[c] > 1 and inst.spec.anti_affinity == "domain":
                for d in inst.domains:
                    members = [n for n in inst.hosts if inst.dom[n] == d]
                    if len(members) > 1:
                        m.add({x[c, n]: 1 for n in members}, hi=1, family="anti_affinity",
                              tag={"component": c, "domain": d})
        for n in inst.hosts:
            m.add({x[c, n]: inst.cpu[c] for c in inst.comps}, hi=inst.cap_cpu[n], family="capacity_cpu", tag={"host": n})
            m.add({x[c, n]: inst.mem[c] for c in inst.comps}, hi=inst.cap_mem[n], family="capacity_mem", tag={"host": n})
            if inst.spec.use_all_hosts:
                m.add({x[c, n]: 1 for c in inst.comps}, lo=1, family="use_all_hosts", tag={"host": n})
        self._deadline_rows()
        self._coverage()
        self._traffic()

        self.balance: Optional[Expr] = None
        if balance:
            U = m.var(0.0, np.inf)
            for n in inst.hosts:
                m.add({U: 1, **{x[c, n]: -inst.cpu[c] / inst.spec.host_cpu[n] for c in inst.comps}}, lo=0, family="balance")
                m.add({U: 1, **{x[c, n]: -inst.mem[c] / inst.spec.host_mem[n] for c in inst.comps}}, lo=0, family="balance")
            self.balance = {U: 1.0}

    # ── Model blocks ─────────────────────────────────────────────────────────

    def _deadline_rows(self) -> None:
        inst, m, x = self.inst, self.m, self.x
        strictest: Dict[Tuple[str, str, Optional[str]], Tuple[float, str]] = {}
        for t, p, s, b, dl in inst.deadline_pairs:
            key = (p, s, b)
            if key not in strictest or dl < strictest[key][0]:
                strictest[key] = (dl, t)
        for (p, s, b), (dl, t) in sorted(strictest.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2] or "")):
            rs = inst.r[s]
            tag = {"topic": t, "publisher": p, "subscriber": s, "broker": b, "deadline_ms": dl}
            for n in inst.allowed[p]:
                for k in (inst.allowed[b] if b is not None else [None]):
                    bad = [h for h in inst.allowed[s] if inst.path_latency(n, k, h) > dl]
                    if not bad:
                        continue
                    row = {x[p, n]: float(rs), **{x[s, h]: 1.0 for h in bad}}
                    if k is None:
                        m.add(row, hi=rs, family="deadline", tag={**tag, "host": n})
                    else:
                        row[x[b, k]] = float(rs)
                        m.add(row, hi=2 * rs, family="deadline", tag={**tag, "host": n, "broker_host": k})

    def _coverage(self) -> None:
        inst, m, x = self.inst, self.m, self.x
        self.z: Dict[str, Dict[str, int]] = {}
        apps = set(inst.apps)
        self.blast_exp: Expr = defaultdict(float)
        self.t = m.var(0.0, float(len(inst.apps)))
        for e in sorted(inst.events):
            seeds = [c for c in inst.comps if inst.max_copies_in(c, e) >= inst.r[c]]
            reach, stack = set(seeds), list(seeds)
            while stack:
                for v in inst.rdeps.get(stack.pop(), ()):
                    if v not in reach:
                        reach.add(v)
                        stack.append(v)
            z = {c: m.var() for c in sorted(reach)}
            self.z[e] = z
            for c in seeds:
                m.add({z[c]: 1, **{x[c, n]: -1 for n in inst.events[e] if n in inst.allowed[c]}},
                      lo=-(inst.r[c] - 1), family="coverage")
            for v, u in inst.arcs:
                if u in z:
                    m.add({z[v]: 1, z[u]: -1}, lo=0, family="coverage")
            for c, j in z.items():
                if c in apps:
                    self.blast_exp[j] += inst.p[e]
            m.add({self.t: 1, **{j: -1 for c, j in z.items() if c in apps}}, lo=0, family="coverage")
        self.blast_exp = dict(self.blast_exp)
        self.blast_max: Expr = {self.t: 1.0}

    def _traffic(self) -> None:
        inst, m, x, y = self.inst, self.m, self.x, self.y
        pub_w: Dict[Tuple[str, str], float] = defaultdict(float)   # (publisher, broker) -> B
        sub_w: Dict[Tuple[str, str], float] = defaultdict(float)   # (subscriber, broker) -> B
        direct: Dict[Tuple[str, str], float] = defaultdict(float)  # (publisher, subscriber) -> B
        for t, b in inst.router.items():
            w = inst.B[t]
            for p in inst.pubs.get(t, ()):
                if b is not None:
                    pub_w[p, b] += w
            for s in inst.subs.get(t, ()):
                if b is not None:
                    sub_w[s, b] += w
                else:
                    for p in inst.pubs.get(t, ()):
                        if p != s:
                            direct[p, s] += w
        tr: Expr = defaultdict(float)

        def priced(weights, own, other) -> None:
            for (a, b), w in sorted(weights.items()):
                for n in inst.hosts:
                    if n not in inst.allowed[a]:
                        continue
                    v = m.var()
                    m.add({v: 1, own[a, n]: -1, other[b, n]: 1}, lo=0, family="traffic")
                    tr[v] += w

        priced(pub_w, y, y)
        priced(sub_w, x, y)
        priced({(s, p): w for (p, s), w in direct.items()}, x, y)
        self.traffic: Expr = dict(tr)

    # ── Solving ──────────────────────────────────────────────────────────────

    def _limit(self) -> Optional[float]:
        """Seconds the next solve may take; 0 means the budget is spent."""
        if self.deadline is None:
            return self.time_limit
        left = self.deadline - time.monotonic()
        if left < 0.5:
            return 0.0
        return left if self.time_limit is None else min(self.time_limit, left)

    def objectives(self, *names: str) -> List[Expr]:
        exprs = {"blast_exp": self.blast_exp, "blast_max": self.blast_max, "traffic": self.traffic}
        out = [exprs[n] for n in names]
        if self.balance is not None:
            out.append(self.balance)
        return out

    def lexi(self, objectives: List[Expr], extra: Sequence[Row] = (), fix_brokers: bool = True,
             first_limit: Optional[float] = None) -> Tuple[Optional[Placement], Dict[str, Any]]:
        """Minimise *objectives* in order, holding each optimum before the next.

        With ``fix_brokers`` the first stage chooses the broker hosts and later
        stages refine the application placement around them. ``first_limit`` caps
        the first stage's time (a probe). Free brokers make
        the traffic term quadratic-like (who shares a host with the broker), and
        HiGHS cannot close the gap on a tie-break stage that may also move them;
        the first objective is still optimised over broker placement exactly.
        """
        extra = list(extra)
        info: Dict[str, Any] = {"stages": []}
        sol = None
        for k, obj in enumerate(objectives):
            limit = self._limit()
            if limit == 0.0:
                info["status"] = "skipped"
                return None, info
            if k == 0 and first_limit is not None:
                limit = first_limit if limit is None else min(limit, first_limit)
            sol, st = self.m.solve(obj, extra, limit)
            info["stages"].append(st)
            if sol is None:
                info["status"] = st["status"]
                return None, info
            if k == 0:
                self.stage0 = self.placement(sol)
            if k + 1 < len(objectives):
                v = value(obj, sol)
                extra.append((obj, -np.inf, v + 1e-6 * max(1.0, abs(v))))
                if k == 0 and fix_brokers:
                    extra.extend(self.pin_brokers(self.placement(sol)))
        info["status"] = "time_limit" if any(s["status"] == "time_limit" for s in info["stages"]) else "optimal"
        info["deterministic"] = info["status"] == "optimal"
        # Objective values recomputed on the placement: after a later stage z only
        # has to respect the earlier cap, so reading it off the solution would
        # carry that tolerance.
        pl = self.placement(sol)
        info.update(self.inst.traffic(pl))
        info.update(self.inst.proxy_blast(pl))
        return pl, info

    def pin_brokers(self, pl: Placement) -> List[Row]:
        """Rows fixing every broker that has a choice to its hosts in *pl*."""
        inst, rows = self.inst, []
        for b in inst.brokers:
            if len(inst.allowed[b]) > inst.r[b]:
                rows.append(({self.y[b, pl[b][0]]: 1.0}, 1.0, 1.0))
                rows.extend(({self.x[b, n]: 1.0}, 1.0, 1.0) for n in pl[b])
        return rows

    def placement(self, sol: np.ndarray) -> Placement:
        inst = self.inst
        pl: Placement = {}
        for c in inst.comps:
            prim = next(n for n in inst.hosts if sol[self.y[c, n]] > 0.5)
            pl[c] = [prim] + [n for n in inst.hosts if n != prim and sol[self.x[c, n]] > 0.5]
        return pl

    def nogood(self, pl: Placement) -> Row:
        """A cut excluding exactly this set of copies."""
        cols = {self.x[c, n]: 1.0 for c, hs in sorted(pl.items()) for n in hs if (c, n) in self.x}
        return cols, -np.inf, len(cols) - 1

    def cap(self, expr: Expr, bound: float) -> Row:
        return expr, -np.inf, bound + 1e-6 * max(1.0, abs(bound))

    def front(self, points: int, probe: Optional[float] = None) -> List[Tuple[str, Optional[Placement], Dict[str, Any]]]:
        """The epsilon-constraint front from min blast_exp to min traffic, both ends lexicographic.

        Each interior point is solved with the brokers of either anchor fixed,
        and keeps whichever gives less traffic. ``probe`` caps the very first
        stage: above about 16 hosts HiGHS can sit at the root node with no
        incumbent for the whole limit, and a longer limit does not help.
        """
        lo_pl, lo = self.lexi(self.objectives("blast_exp", "traffic"), first_limit=probe)
        out = [("front_0", lo_pl, lo)]
        if lo_pl is None or points < 2:
            return out
        hi_pl, hi = self.lexi(self.objectives("traffic", "blast_exp"))
        self.traffic_only = self.stage0 if hi_pl is not None else None
        if hi_pl is None:
            return out + [(f"front_{points - 1}", None, hi)]
        b_lo, b_hi = lo["blast_exp"], hi["blast_exp"]
        for k in range(1, points - 1):
            eps = b_lo + (b_hi - b_lo) * k / (points - 1)
            best: Tuple[Optional[Placement], Dict[str, Any]] = (None, {"status": "infeasible"})
            for anchor, pl_a in (("front_0", lo_pl), (f"front_{points - 1}", hi_pl)):
                pl, info = self.lexi(self.objectives("traffic", "blast_exp"),
                                     [self.cap(self.blast_exp, eps)] + self.pin_brokers(pl_a))
                if pl is not None and (best[0] is None or
                                       (info["traffic"], info["blast_exp"]) < (best[1]["traffic"], best[1]["blast_exp"])):
                    best = (pl, {**info, "brokers_from": anchor})
                elif best[0] is None:
                    best = (None, info)
            out.append((f"front_{k}", best[0], {**best[1], "eps": eps}))
        out.append((f"front_{points - 1}", hi_pl, {**hi, "eps": b_hi}))
        return out

    def ties(self, anchor: Placement, b_star: float, n: int,
             found: Sequence[Placement]) -> List[Tuple[str, Optional[Placement], Dict[str, Any]]]:
        """Up to *n* further placements at the minimum blast_exp (anchor's brokers), cheapest traffic first."""
        cuts = [self.nogood(pl) for pl in found] + self.pin_brokers(anchor)
        out = []
        for i in range(1, n + 1):
            pl, info = self.lexi(self.objectives("traffic"), [self.cap(self.blast_exp, b_star)] + cuts)
            if pl is None:
                if info.get("status") == "skipped":
                    self.skipped.append(f"tie_{i}")
                break
            out.append((f"tie_{i}", pl, info))
            cuts.append(self.nogood(pl))
        return out

    def diagnose(self, top: int = 20) -> Dict[str, Any]:
        """Which constraint families an infeasible model cannot satisfy, and where."""
        violated = self.m.solve_elastic(time_limit=self._limit() or self.time_limit)
        families: Dict[str, int] = defaultdict(int)
        for i, _ in violated:
            families[self.m.family[i]] += 1
        culprits = [{"family": self.m.family[i], "slack": round(s, 6), **(self.m.tag[i] or {})}
                    for i, s in violated[:top]]
        return {"families": dict(sorted(families.items())), "culprits": culprits}
