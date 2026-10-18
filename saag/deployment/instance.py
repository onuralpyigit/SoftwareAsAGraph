"""
The placement problem for one topology and one bound spec.

Everything here is placement-independent: which components are placed and how
many copies each needs, what they demand, which flows they exchange through
which broker, which publisher/subscriber pairs carry a deadline, which failure
events can happen and how dependencies propagate a failure. The metric methods
score any placement on the same terms the MILP optimises, so the solver's
objective values can be checked against a direct computation.

A placement maps each component (Application or Broker) to its hosts, primary
first. Libraries are not placed: they run inside the applications that use them.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .spec import BoundSpec

Placement = Dict[str, List[str]]

MIB = 2 ** 20

#: Derived dependency types that are independent of placement. Rule 6
#: (broker_to_broker) comes from brokers sharing an *authored* host, and the
#: node-level rules from authored RUNS_ON, so neither may steer a new placement;
#: co-hosted fate sharing is already modelled by the failure events.
DEPENDENCY_TYPES = ("app_to_app", "app_to_broker")


class Instance:
    """Placement-independent problem data plus placement metrics."""

    def __init__(self, topology: Dict[str, Any], spec: BoundSpec, arcs: Iterable[Tuple[str, str]]):
        self.topology, self.spec = topology, spec
        self.hosts = list(spec.hosts)
        self.dom = dict(spec.domain)
        self.domains = sorted(set(self.dom.values()))
        self.L = spec.latency
        self.apps = sorted(a["id"] for a in topology.get("applications", []))
        self.brokers = sorted(b["id"] for b in topology.get("brokers", []))
        self.comps = sorted(self.apps + self.brokers)
        self.r = dict(spec.replicas)
        self.allowed = {c: list(spec.allowed[c]) for c in self.comps}
        self.cap_cpu = {h: spec.host_cpu[h] * spec.max_utilization for h in self.hosts}
        self.cap_mem = {h: spec.host_mem[h] * spec.max_utilization for h in self.hosts}

        rel = topology.get("relationships", {})
        topics = {t["id"]: t for t in topology.get("topics", [])}
        self.B = {t: float(v.get("frequency") or 1.0) * float(v.get("size") or 1.0) for t, v in topics.items()}

        # Libraries publish and subscribe on behalf of the applications using them.
        users = defaultdict(set)
        for e in rel.get("uses", []):
            users[e["to"]].add(e["from"])
        placed = set(self.apps)

        def resolve(x: str, seen: Optional[set] = None) -> set:
            if x in placed:
                return {x}
            seen = (seen or set()) | {x}
            return {a for u in sorted(users.get(x, ())) if u not in seen for a in resolve(u, seen)}

        pubs, subs = defaultdict(set), defaultdict(set)
        for e in rel.get("publishes_to", []):
            pubs[e["to"]] |= resolve(e["from"])
        for e in rel.get("subscribes_to", []):
            subs[e["to"]] |= resolve(e["from"])
        # Sorted lists, not sets: MILP column order must not depend on PYTHONHASHSEED.
        self.pubs = {t: sorted(pubs[t]) for t in sorted(pubs)}
        self.subs = {t: sorted(subs[t]) for t in sorted(subs)}

        # A topic's traffic goes through its first routing broker (by id).
        self.router: Dict[str, Optional[str]] = {t: None for t in sorted(topics)}
        for e in sorted(rel.get("routes", []), key=lambda e: (e["to"], e["from"])):
            if self.router.get(e["to"]) is None and e["from"] in self.brokers:
                self.router[e["to"]] = e["from"]

        # Memory: each subscribing copy buffers history_depth messages per topic.
        sub_topics = defaultdict(list)
        for t, ss in self.subs.items():
            for s in ss:
                sub_topics[s].append(t)
        self.cpu = {c: spec.cpu[c] for c in self.comps}
        self.mem = {c: spec.mem_mb[c] + sum(
            (_topic_field(topics[t], "history_depth") or 1) * float(topics[t].get("size") or 0)
            for t in sub_topics.get(c, ())) / MIB for c in self.comps}

        # Deadline-bound (topic, publisher, subscriber, broker, deadline_ms).
        self.deadline_pairs: List[Tuple[str, str, str, Optional[str], float]] = []
        for t in sorted(topics):
            dl = _topic_field(topics[t], "deadline_ms")
            if dl is None:
                continue
            for p in self.pubs.get(t, ()):
                for s in self.subs.get(t, ()):
                    if p != s:
                        self.deadline_pairs.append((t, p, s, self.router[t], float(dl)))

        # Failure events: every host, and every domain with at least two hosts.
        self.events: Dict[str, List[str]] = {h: [h] for h in self.hosts}
        self.p: Dict[str, float] = dict(spec.host_p)
        for d in self.domains:
            members = [h for h in self.hosts if self.dom[h] == d]
            if len(members) >= 2:
                self.events[d] = members
                self.p[d] = spec.domain_p[d]

        # Dependency arcs v -> u (v depends on u), placement-independent types only.
        comp_set = set(self.comps)
        self.arcs = sorted({(v, u) for v, u in arcs if v in comp_set and u in comp_set and v != u})
        self.deps: Dict[str, List[str]] = defaultdict(list)
        self.rdeps: Dict[str, List[str]] = defaultdict(list)
        for v, u in self.arcs:
            self.deps[v].append(u)
            self.rdeps[u].append(v)
        self.indeg = {c: len(self.rdeps.get(c, ())) for c in self.comps}

    # ── Helpers ──────────────────────────────────────────────────────────────

    def path_latency(self, n: str, k: Optional[str], m: str) -> float:
        """Latency publisher host n -> broker host k -> subscriber host m (k None: direct)."""
        return self.L[n][m] if k is None else self.L[n][k] + self.L[k][m]

    def is_domain_event(self, e: str) -> bool:
        return len(self.events[e]) > 1

    def max_copies_in(self, c: str, e: str) -> int:
        """Most copies of *c* a feasible placement can put inside event *e*."""
        inside = len(set(self.events[e]) & set(self.allowed[c]))
        cap = min(inside, self.r[c])
        if self.spec.anti_affinity == "domain" and self.r[c] >= 2 and len({self.dom[h] for h in self.events[e]}) == 1:
            cap = min(cap, 1)
        return cap

    # ── Placement metrics ────────────────────────────────────────────────────

    def traffic(self, pl: Placement) -> Dict[str, float]:
        """Cross-host bytes/s through the broker model, as the MILP prices it."""
        xh = xd = lw = total = 0.0

        def hop(a: str, b: str, w: float) -> None:
            nonlocal xh, xd, lw, total
            total += w
            if a != b:
                xh += w
                lw += w * self.L[a][b]
                if self.dom[a] != self.dom[b]:
                    xd += w

        for t, b in self.router.items():
            w = self.B[t]
            for p in self.pubs.get(t, ()):
                if b is not None:
                    hop(pl[p][0], pl[b][0], w)
            for s in self.subs.get(t, ()):
                for h in pl[s]:
                    if b is not None:
                        hop(pl[b][0], h, w)
                    else:
                        for p in self.pubs.get(t, ()):
                            if p != s:
                                hop(pl[p][0], h, w)
        return {"traffic": xh, "cross_host_share": xh / total if total else 0.0,
                "cross_domain_share": xd / total if total else 0.0, "latency_weighted": lw}

    def down_in(self, pl: Placement, e: str) -> set:
        """Components down in event *e*: every copy inside it, plus everything depending on those."""
        inside = set(self.events[e])
        dead = {c for c in self.comps if all(h in inside for h in pl[c])}
        stack = list(dead)
        while stack:
            for v in self.rdeps.get(stack.pop(), ()):
                if v not in dead:
                    dead.add(v)
                    stack.append(v)
        return dead

    def proxy_blast(self, pl: Placement) -> Dict[str, float]:
        """Expected and worst-case number of Applications down, over the failure events."""
        apps = set(self.apps)
        per = {e: len(self.down_in(pl, e) & apps) for e in self.events}
        return {"blast_exp": sum(self.p[e] * v for e, v in per.items()), "blast_max": float(max(per.values(), default=0))}

    def usage(self, pl: Placement) -> Tuple[Dict[str, float], Dict[str, float]]:
        cpu, mem = defaultdict(float), defaultdict(float)
        for c, hs in pl.items():
            for h in hs:
                cpu[h] += self.cpu[c]
                mem[h] += self.mem[c]
        return cpu, mem

    def deadline_ok(self, pl: Placement, pair: Tuple[str, str, str, Optional[str], float]) -> bool:
        _, p, s, b, dl = pair
        brokers = pl[b] if b is not None else [None]
        return all(self.path_latency(n, k, m) <= dl for n in pl[p] for k in brokers for m in pl[s])

    def violations(self, pl: Placement) -> Dict[str, int]:
        """Hard-constraint violations, by family (all zero for a feasible placement)."""
        cpu, mem = self.usage(pl)
        out = {
            "replicas": sum(len(pl.get(c, ())) != self.r[c] or len(set(pl.get(c, ()))) != len(pl.get(c, ()))
                            for c in self.comps),
            "pin": sum(any(h not in self.allowed[c] for h in pl.get(c, ())) for c in self.comps),
            "capacity": sum(cpu[h] > self.cap_cpu[h] + 1e-6 or mem[h] > self.cap_mem[h] + 1e-6 for h in self.hosts),
            "anti_affinity": sum(len({self.dom[h] for h in pl[c]}) < len(pl[c]) for c in self.comps
                                 if self.spec.anti_affinity == "domain" and len(pl.get(c, ())) > 1),
            "deadline": sum(not self.deadline_ok(pl, pair) for pair in self.deadline_pairs),
        }
        if self.spec.use_all_hosts:
            used = {h for hs in pl.values() for h in hs}
            out["use_all_hosts"] = sum(h not in used for h in self.hosts)
        return out

    def load(self, pl: Placement) -> Dict[str, float]:
        """Load concentration: peak utilisation, hosts used, evenness of copies per host."""
        cpu, mem = self.usage(pl)
        copies = defaultdict(int)
        for hs in pl.values():
            for h in hs:
                copies[h] += 1
        total = sum(copies.values())
        entropy = -sum((n / total) * math.log(n / total) for n in copies.values() if n) if total else 0.0
        norm = math.log(len(self.hosts)) if len(self.hosts) > 1 else 1.0
        return {
            "max_cpu_util": max((cpu[h] / self.spec.host_cpu[h] for h in self.hosts), default=0.0),
            "max_mem_util": max((mem[h] / self.spec.host_mem[h] for h in self.hosts), default=0.0),
            "hosts_used": float(len(copies)),
            "copies_entropy": entropy / norm,
        }

    # ── Infeasibility prechecks ──────────────────────────────────────────────

    def prechecks(self) -> List[str]:
        """Sufficient conditions for infeasibility, each as a readable sentence."""
        out = []
        need_cpu = sum(self.cpu[c] * self.r[c] for c in self.comps)
        need_mem = sum(self.mem[c] * self.r[c] for c in self.comps)
        if need_cpu > sum(self.cap_cpu.values()) + 1e-9:
            out.append(f"total cpu demand {need_cpu:.2f} exceeds usable capacity {sum(self.cap_cpu.values()):.2f}")
        if need_mem > sum(self.cap_mem.values()) + 1e-9:
            out.append(f"total memory demand {need_mem:.0f} MB exceeds usable capacity {sum(self.cap_mem.values()):.0f} MB")
        fit = {c: [h for h in self.allowed[c] if self.cpu[c] <= self.cap_cpu[h] + 1e-9
                   and self.mem[c] <= self.cap_mem[h] + 1e-9] for c in self.comps}
        for c in self.comps:
            if len(fit[c]) < self.r[c]:
                out.append(f"{c} needs {self.r[c]} copies but only {len(fit[c])} allowed Node(s) can hold it")
            elif self.spec.anti_affinity == "domain" and self.r[c] > 1 and len({self.dom[h] for h in fit[c]}) < self.r[c]:
                out.append(f"{c} needs {self.r[c]} copies in distinct domains but its allowed Nodes span "
                           f"{len({self.dom[h] for h in fit[c]})}")
        for t, p, s, b, dl in self.deadline_pairs:
            brokers = fit[b] if b is not None else [None]
            if not any(self.path_latency(n, k, m) <= dl for n in fit[p] for k in brokers for m in fit[s]):
                out.append(f"topic {t}: no allowed hosts for {p} -> {s}" + (f" via {b}" if b else "")
                           + f" meet its {dl:g} ms deadline")
        return out


def _topic_field(topic: Dict[str, Any], key: str) -> Any:
    value = topic.get(key)
    return value if value is not None else (topic.get("qos") or {}).get(key)
