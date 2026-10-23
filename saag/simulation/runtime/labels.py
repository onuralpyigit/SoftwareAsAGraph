"""
Runtime label sweep: fault every candidate, measure what the system lost.

A Predict-stage labeller like `FaultInjector`, but driven by the runtime
engine (`MessageFlowSimulator` with the reactive model, the deployment on the
data path and the paired estimator), so a label can express multi-hop
propagation, contention, failover and recovery rather than first-order feed
loss. It writes labels to disk; `saag/prediction/` reads them from there and
never imports this module (tests/test_predict_simulate_separation.py).

Labels (each a mean and std over seeds)
───────────────────────────────────────
``i_delivery``           paired volume-weighted delivery loss of the surviving
                         readers: baseline-post minus fault-post delivery rate.
``i_delivery_consumer``  the same, averaged per subscriber instead of per
                         message, so a 700 Hz feed does not outweigh a 1 Hz one
                         700:1.
``i_flow``               criticality-weighted end-to-end loss at the sinks
                         (applications that subscribe but publish nothing). A
                         sink that is itself faulted loses everything, so --
                         unlike I*(v) -- terminal consumers are not harmless.
``i_timeliness``         signed change in the deadline-miss share of delivered
                         samples. Under ``qos_semantics="dds"`` -- where a
                         deadline is a period contract that drops nothing --
                         it is instead the paired increase in the DDS
                         ``requested_deadline_missed`` rate per surviving
                         reader (misses per reader-second). Negative values are real: a fault can relieve
                         contention. Never clamped.
``i_recovery``           seconds from the end of a bounded fault until delivery
                         regained 95% of its pre-fault rate (bounded faults only).

Order-k references (CLAUDE.md, "Order-k Reference Criterion")
──────────────────────────────────────────────────────────────
Every label ships with the truncation of its own computation that a
training-free ranker could compute from the topology, so a learned ranker is
compared against it rather than presented as recovering it:

``ref_delivery_first_order``  Eq. 7 generalised to brokers and hosts: the
                              rate-weighted share of reader demand on topics the
                              fault silences directly (no propagation, no
                              queueing).
``ref_flow_reach``            criticality-weighted share of sinks reachable
                              downstream of the fault in the pub-sub graph (full
                              propagation, no dynamics). It saturates on densely
                              connected systems -- most applications reach every
                              sink -- so it is reported, but it is not the
                              binding reference.
``ref_flow_policy``           the reactive model's own truncation: stalls
                              propagate deterministically through the same
                              trigger policies (`behavior.policy_for`) with no
                              timing, staleness or queueing, and each sink loses
                              the share of its inputs that end up dead. This is
                              the reference `i_flow` has to beat to be more than
                              a restatement of the policies.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from .faults import FaultSpec
from .uncertainty import rank_stability

#: Runtime-oracle settings. Kept separate from the published I_dyn settings
#: (reproduce/oracle_robust_ltr.py:IDYN_SETTINGS) so that cache key never moves.
RUNTIME_LABEL_SETTINGS: Dict[str, Any] = {
    "duration": 60.0,
    "qos_mode": "full",
    "target_utilization": 0.65,
    "behavior_model": "reactive",
    "rng_streams": "per_process",
    "estimator": "paired",
    "deployment_model": "stations",
}

LABEL_NAMES: Tuple[str, ...] = (
    "i_delivery", "i_delivery_consumer", "i_flow", "i_timeliness", "i_recovery")

#: Sink weight per declared criticality; undeclared counts as LOW.
CRITICALITY_WEIGHT = {"LOW": 1.0, "MEDIUM": 2.0, "HIGH": 3.0, "CRITICAL": 4.0}


# ── Topology views (raw structural edges only) ───────────────────────────────

class _Topology:
    def __init__(self, graph) -> None:
        self.type = {n: d.get("type") for n, d in graph.nodes(data=True)}
        self.pubs: Dict[str, List[str]] = defaultdict(list)
        self.subs: Dict[str, List[str]] = defaultdict(list)
        self.publishes: Dict[str, Set[str]] = defaultdict(set)
        self.subscribes: Dict[str, Set[str]] = defaultdict(set)
        self.routers: Dict[str, Set[str]] = defaultdict(set)
        self.residents: Dict[str, Set[str]] = defaultdict(set)
        for u, v, d in graph.edges(data=True):
            t = d.get("type")
            if t == "PUBLISHES_TO":
                self.pubs[v].append(u)
                self.publishes[u].add(v)
            elif t == "SUBSCRIBES_TO":
                self.subs[v].append(u)
                self.subscribes[u].add(v)
            elif t == "ROUTES":
                self.routers[v].add(u)
            elif t == "RUNS_ON":
                self.residents[v].add(u)
        self.rate = {
            n: float(d.get("frequency") or d.get("topic_frequency") or 10.0)
            for n, d in graph.nodes(data=True) if d.get("type") == "Topic"
        }
        self.sinks = sorted(a for a in self.subscribes if not self.publishes.get(a))
        self.weight = {
            a: CRITICALITY_WEIGHT.get(str(graph.nodes[a].get("criticality") or "LOW").upper(), 1.0)
            for a in self.sinks
        }

    def killed(self, v: str) -> Set[str]:
        return {v} | set(self.residents.get(v, ()))

    def topic_loss(self, killed: Set[str]) -> Dict[str, float]:
        loss = {}
        for t, rate in self.rate.items():
            pubs = self.pubs.get(t, [])
            pub_loss = (sum(1 for p in pubs if p in killed) / len(pubs)) if pubs else 0.0
            routers = self.routers.get(t, set())
            router_loss = 1.0 if routers and routers <= killed else 0.0
            loss[t] = 1.0 if t in killed else max(pub_loss, router_loss)
        return loss


def ref_delivery_first_order(topo: _Topology, v: str) -> float:
    """Rate-weighted reader demand lost to direct silencing, over readers other than *v*.

    Same accounting as I_dyn: *v*'s own readership leaves both numerator and
    denominator, while a reader killed alongside it (a host's resident) loses
    every input it has.
    """
    killed = topo.killed(v)
    loss = topo.topic_loss(killed)
    num = den = 0.0
    for t, rate in topo.rate.items():
        readers = [s for s in topo.subs.get(t, []) if s != v]
        den += rate * len(readers)
        num += rate * sum(1.0 if s in killed else loss[t] for s in readers)
    return num / den if den else 0.0


def ref_flow_policy(topo: _Topology, graph, v: str) -> float:
    """Deterministic stall fixpoint under the trigger policies (see module docstring)."""
    from .behavior import policy_for

    if not topo.sinks:
        return 0.0
    dead = set(topo.killed(v))
    published = {t for ts in topo.publishes.values() for t in ts}
    required = {
        a: sorted((topo.subscribes.get(a, set()) & published) - topo.publishes.get(a, set()))
        for a in topo.publishes
    }
    while True:
        loss = topo.topic_loss(dead)
        dead_topics = {t for t, l in loss.items() if l >= 1.0}
        stalled = set()
        for a, inputs in required.items():
            if a in dead or not inputs:
                continue
            kind = policy_for(graph.nodes.get(a, {})).kind
            live = [t for t in inputs if t not in dead_topics]
            if (kind == "all_of" and len(live) < len(inputs)) or (kind != "periodic" and not live):
                stalled.add(a)
        if stalled <= dead:
            break
        dead |= stalled
    loss = topo.topic_loss(dead)
    num = den = 0.0
    for a in topo.sinks:
        feeds = sorted(topo.subscribes.get(a, set()) & published)
        lost = 1.0 if a in dead else (
            sum(loss[t] for t in feeds) / len(feeds) if feeds else 0.0)
        num += topo.weight[a] * lost
        den += topo.weight[a]
    return num / den if den else 0.0


def ref_flow_reach(topo: _Topology, v: str) -> float:
    if not topo.sinks:
        return 0.0
    dead = set(topo.killed(v))
    frontier = sorted(dead)
    silenced = {t for t, l in topo.topic_loss(dead).items() if l >= 1.0}
    reached: Set[str] = set(dead)
    topics = set(silenced)
    for a in frontier:
        topics |= topo.publishes.get(a, set())
    while topics:
        nxt: Set[str] = set()
        for t in sorted(topics):
            for s in topo.subs.get(t, []):
                if s not in reached:
                    reached.add(s)
                    nxt |= topo.publishes.get(s, set())
        topics = nxt
    total = sum(topo.weight.values())
    return sum(w for a, w in topo.weight.items() if a in reached) / total


# ── Per-run label extraction ─────────────────────────────────────────────────

def _received_post(result, app: str) -> int:
    st = result.subscriber_stats.get(app)
    return st.received_post_fault if st else 0


def _miss_share(result) -> Optional[float]:
    viol = sum(ts.deadline_violations_post for ts in result.topic_stats.values())
    got = sum(ts.delivered_post for ts in result.topic_stats.values())
    return viol / (viol + got) if (viol + got) else None


def _deadline_rate(result, target: str, span: float) -> float:
    """Post-window DDS reader deadline misses per surviving reader per second.

    *span* is the faulted run's post window: the paired baseline has no fault
    event of its own, but it is bucketed on the same fault time.
    """
    status = result.dds_status or {}
    readers = [k for k, e in status.get("entities", {}).items()
               if e["kind"] == "reader" and e["component"] != target]
    if not readers or span <= 0:
        return 0.0
    misses = status.get("statuses", {}).get("requested_deadline_missed", {})
    return sum(misses.get(k, {}).get("post", 0) for k in readers) / (len(readers) * span)


def labels_from_run(topo: _Topology, target: str, fault_run, baseline) -> Dict[str, Optional[float]]:
    fe = fault_run.fault_event
    consumer = []
    for app in sorted(baseline.subscriber_stats):
        if app == target:
            continue
        base = _received_post(baseline, app)
        if base > 0:
            consumer.append(1.0 - _received_post(fault_run, app) / base)
    flow_num = flow_den = 0.0
    for app in topo.sinks:
        base = _received_post(baseline, app)
        if base <= 0:
            continue
        w = topo.weight[app]
        lost = 1.0 if app == target else 1.0 - _received_post(fault_run, app) / base
        flow_num += w * lost
        flow_den += w
    if (fault_run.runtime_config or {}).get("qos_semantics") == "dds":
        span = fe.post_window_s
        timeliness = _deadline_rate(fault_run, target, span) - _deadline_rate(baseline, target, span)
    else:
        miss_f, miss_b = _miss_share(fault_run), _miss_share(baseline)
        timeliness = (miss_f - miss_b) if miss_f is not None and miss_b is not None else None
    return {
        "i_delivery": fe.i_dyn_paired,
        "i_delivery_consumer": statistics.fmean(consumer) if consumer else 0.0,
        "i_flow": flow_num / flow_den if flow_den else 0.0,
        "i_timeliness": timeliness,
        "i_recovery": fe.time_to_recover_s,
    }


# ── Sweep ────────────────────────────────────────────────────────────────────

def default_candidates(graph, deployment_model: str) -> List[str]:
    types = {"Application"} | ({"Broker", "Node"} if deployment_model == "stations" else set())
    return sorted(n for n, d in graph.nodes(data=True) if d.get("type") in types)


def _sweep_seed(args: Tuple[Any, ...]) -> Tuple[int, Dict[str, Dict[str, Optional[float]]]]:
    from ..message_flow_simulator import MessageFlowSimulator

    graph, candidates, seed, settings, fault = args
    topo = _Topology(graph)
    cache: Dict[Any, Any] = {}
    out: Dict[str, Dict[str, Optional[float]]] = {}
    fault_time = settings["duration"] / 2.0
    for c in candidates:
        spec = FaultSpec(c, fault.get("mode", "crash"), fault_time,
                         fault.get("duration_s"), fault.get("params", {}))
        sim = MessageFlowSimulator(graph, seed=seed, faults=[spec],
                                   baseline_cache=cache, **settings)
        run = sim.run()
        out[c] = labels_from_run(topo, c, run, cache[(seed, fault_time)])
    return seed, out


def runtime_labels(
    graph,
    candidates: Optional[Sequence[str]] = None,
    seeds: Iterable[int] = (42,),
    settings: Optional[Mapping[str, Any]] = None,
    fault: Optional[Mapping[str, Any]] = None,
    workers: int = 1,
) -> Dict[str, Any]:
    """Fault every candidate under every seed; return labels, references, stability.

    ``fault`` is ``{"mode": ..., "duration_s": ..., "params": {...}}`` (default a
    permanent crash at mid-run). ``settings`` override `RUNTIME_LABEL_SETTINGS`.
    The paired estimator is required, since every label is paired.
    """
    cfg = {**RUNTIME_LABEL_SETTINGS, **(settings or {})}
    if cfg.get("estimator") != "paired":
        raise ValueError("runtime labels are paired: settings must keep estimator='paired'")
    fault = dict(fault or {"mode": "crash"})
    seeds = list(seeds)
    cands = list(candidates) if candidates is not None else default_candidates(
        graph, cfg.get("deployment_model", "none"))

    jobs = [(graph, cands, s, cfg, fault) for s in seeds]
    if workers > 1 and len(jobs) > 1:
        with ProcessPoolExecutor(max_workers=min(workers, len(jobs))) as pool:
            per_seed = dict(pool.map(_sweep_seed, jobs))
    else:
        per_seed = dict(map(_sweep_seed, jobs))

    topo = _Topology(graph)
    records: Dict[str, Dict[str, Any]] = {}
    for c in cands:
        rec: Dict[str, Any] = {"node_type": topo.type.get(c)}
        for name in LABEL_NAMES:
            vals = [per_seed[s][c][name] for s in seeds if per_seed[s][c][name] is not None]
            rec[name] = None if not vals else {
                "mean": statistics.fmean(vals),
                "std": statistics.stdev(vals) if len(vals) > 1 else 0.0,
                "per_seed": {str(s): per_seed[s][c][name] for s in seeds},
            }
        rec["ref_delivery_first_order"] = ref_delivery_first_order(topo, c)
        rec["ref_flow_reach"] = ref_flow_reach(topo, c)
        rec["ref_flow_policy"] = ref_flow_policy(topo, graph, c)
        records[c] = rec

    measured = [n for n in LABEL_NAMES if any(records[c][n] is not None for c in cands)]
    stability = {
        n: {k: v for k, v in rank_stability(
            [{c: per_seed[s][c][n] for c in cands if per_seed[s][c][n] is not None}
             for s in seeds]).items() if k not in ("mean", "std")}
        for n in measured
    }
    return {
        "schema_version": "1.0",
        "graph_id": graph.graph.get("id", ""),
        "labeler": "MessageFlowSimulator/runtime",
        "settings": cfg,
        "fault": fault,
        "seeds": seeds,
        "labeled_dimensions": measured,
        "references": ["ref_delivery_first_order", "ref_flow_reach", "ref_flow_policy"],
        "unlabeled_node_ids": sorted(set(graph.nodes) - set(cands)),
        "records": records,
        "label_stability": stability,
    }
