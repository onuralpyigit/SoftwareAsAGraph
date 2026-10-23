"""
Simulator fidelity: does the simulated system behave like the observed one?

Every oracle in this package (I*, I_comp, I_dyn and the runtime labels) is
scored against something the framework itself computed. This report is the
first comparison against *observed* behaviour -- the emulated DDS testbed
(``saag/testbed/``) or, later, a real deployment -- i.e. the "Link 2" that the
validation chain otherwise leaves unmeasured.

Two parts, both computed from traces in the format of
`saag.simulation.calibration` (`MessageFlowSimulator(record_events=True)`
writes the same format, so simulated and observed runs compare like for like):

``steady_state``    per-topic publication rate, per-(reader, topic) delivery
                    rate, per-topic p50/p95 latency and the deadline-miss share,
                    each as a relative (or, for the miss share, absolute) error
                    against a declared tolerance.
``fault_response``  rank agreement between simulated and observed per-component
                    impact (Spearman, top-K Jaccard) and agreement of the sets of
                    components that stalled.

It is a Tier-2 report: it never decides a pass/fail gate and never blocks a
build (CLAUDE.md, "Two-Tiered Triage Protocol"). Every comparison says how many
items it rested on, so an empty or tiny comparison is visibly not a result.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from typing import Any, Dict, Iterable, Mapping, Optional, Set

from saag.simulation._stats import percentile
from saag.simulation.calibration import TelemetryTrace

#: Declared tolerances. Relative for rates and latencies, absolute for the share.
DEFAULT_TOLERANCES: Dict[str, float] = {
    "topic_rate": 0.05,
    "delivery_rate": 0.10,
    "latency_p50": 0.25,
    "latency_p95": 0.35,
    "deadline_miss_share": 0.05,
}


def summarize(trace: TelemetryTrace) -> Dict[str, Any]:
    dur = trace.duration_s or 1.0
    pubs: Dict[str, int] = defaultdict(int)
    deliv: Dict[str, int] = defaultdict(int)
    lat: Dict[str, list] = defaultdict(list)
    miss = got = 0
    for e in trace.events:
        if e["kind"] == "publish":
            pubs[e["topic"]] += 1
        elif e["kind"] == "deliver":
            deliv[f"{e['app']}|{e['topic']}"] += 1
            got += 1
            if e["latency_ms"] is not None:
                lat[e["topic"]].append(float(e["latency_ms"]))
        elif e["kind"] == "deadline_miss":
            miss += 1
    return {
        "duration_s": trace.duration_s,
        "topic_rate": {t: n / dur for t, n in pubs.items()},
        "delivery_rate": {k: n / dur for k, n in deliv.items()},
        "latency_p50": {t: percentile(v, 50) for t, v in lat.items()},
        "latency_p95": {t: percentile(v, 95) for t, v in lat.items()},
        "deadline_miss_share": miss / (miss + got) if (miss + got) else None,
    }


def _rel_errors(sim: Mapping[str, float], obs: Mapping[str, float]) -> Dict[str, float]:
    out = {}
    for k in sorted(set(sim) & set(obs)):
        o, s = obs[k], sim[k]
        if o is None or s is None:
            continue
        out[k] = abs(s - o) / abs(o) if o else (0.0 if s == 0 else math.inf)
    return out


def compare_steady_state(
    simulated: TelemetryTrace, observed: TelemetryTrace,
    tolerances: Optional[Mapping[str, float]] = None,
) -> Dict[str, Any]:
    tol = {**DEFAULT_TOLERANCES, **(tolerances or {})}
    s, o = summarize(simulated), summarize(observed)
    report: Dict[str, Any] = {"tolerances": tol, "metrics": {}}
    for metric in ("topic_rate", "delivery_rate", "latency_p50", "latency_p95"):
        errs = _rel_errors(s[metric], o[metric])
        only_sim = sorted(set(s[metric]) - set(o[metric]))
        only_obs = sorted(set(o[metric]) - set(s[metric]))
        vals = list(errs.values())
        report["metrics"][metric] = {
            "n_compared": len(vals),
            "median_rel_error": statistics.median(vals) if vals else None,
            "max_rel_error": max(vals) if vals else None,
            "share_within_tolerance": (sum(1 for v in vals if v <= tol[metric]) / len(vals)
                                       if vals else None),
            "worst": sorted(errs.items(), key=lambda kv: -kv[1])[:5],
            "only_in_simulation": only_sim,
            "only_observed": only_obs,
        }
    ms, mo = s["deadline_miss_share"], o["deadline_miss_share"]
    report["metrics"]["deadline_miss_share"] = {
        "simulated": ms, "observed": mo,
        "abs_error": abs(ms - mo) if ms is not None and mo is not None else None,
        "within_tolerance": (abs(ms - mo) <= tol["deadline_miss_share"]
                             if ms is not None and mo is not None else None),
    }
    return report


def compare_fault_response(
    simulated_impact: Mapping[str, float],
    observed_impact: Mapping[str, float],
    simulated_stalled: Optional[Iterable[str]] = None,
    observed_stalled: Optional[Iterable[str]] = None,
    k_frac: float = 0.2,
) -> Dict[str, Any]:
    """Rank agreement of per-component impact, and stall-set agreement."""
    from scipy.stats import spearmanr

    common = sorted(set(simulated_impact) & set(observed_impact))
    out: Dict[str, Any] = {"n_common": len(common)}
    if len(common) >= 3:
        a = [simulated_impact[c] for c in common]
        b = [observed_impact[c] for c in common]
        rho = spearmanr(a, b).correlation
        out["spearman"] = None if math.isnan(rho) else float(rho)
        k = max(1, int(round(len(common) * k_frac)))
        top = lambda xs: set(sorted(range(len(xs)), key=lambda i: (-xs[i], i))[:k])  # noqa: E731
        ta, tb = top(a), top(b)
        out["topk_jaccard"] = len(ta & tb) / len(ta | tb)
        out["k"] = k
    else:
        out["spearman"] = out["topk_jaccard"] = None
    if simulated_stalled is not None and observed_stalled is not None:
        ss: Set[str] = set(simulated_stalled)
        so: Set[str] = set(observed_stalled)
        out["stall_jaccard"] = (len(ss & so) / len(ss | so)) if (ss | so) else 1.0
        out["stalled_only_in_simulation"] = sorted(ss - so)
        out["stalled_only_observed"] = sorted(so - ss)
    return out


def stalled_in_trace(trace: TelemetryTrace, after: float, min_silence_s: float = 1.0,
                     periods: float = 3.0) -> Set[str]:
    """Apps that emitted before *after* and then fell silent until the end of the trace.

    "Silent" means no emission for the last ``max(min_silence_s, periods / r)``
    seconds, with r the app's own pre-*after* emission rate: a 1 Hz publisher is
    routinely quiet for a second, so a fixed window would call it stalled. The
    observed counterpart of `FaultEventRecord.stalled_apps`; callers drop the
    faulted component itself.
    """
    last: Dict[str, float] = {}
    before: Dict[str, int] = defaultdict(int)
    for e in trace.events:
        if e["kind"] == "publish":
            last[e["app"]] = e["t"]
            if e["t"] < after:
                before[e["app"]] += 1
    span = max(after - trace.start_s, 1e-9)
    out = set()
    for a, n in before.items():
        window = max(min_silence_s, periods * span / n)
        if last[a] < trace.end_s - window:
            out.add(a)
    return out


def compare_dds_status(
    simulated: TelemetryTrace, observed: TelemetryTrace, split_at: Optional[float] = None,
    end_margin_s: float = 1.5, dead_instances: Iterable[str] = (),
    restrict_to_simulated: bool = True,
) -> Dict[str, Any]:
    """Compare DDS standard status counts (saag.simulation.runtime.dds_status).

    For each status: the entities where it fired on each side and their
    Jaccard, and per-entity count error over the common entities -- for the
    whole run and, when *split_at* (the first fault) is given, for the window
    after it. Counts are taken *end_margin_s* before each trace's end, so the
    shutdown of the testbed's own processes (writers going away as each
    container exits) is not read as an outage. Gauges skip *dead_instances*
    (components or ``"app@host"`` instances that died during the run): their
    last report is a stale pre-crash value. Counters keep them -- a dead
    entity's count up to its death is valid history.

    With *restrict_to_simulated* (default) observed entities of components the
    simulator does not model as DDS endpoints -- the testbed's broker relays,
    whose ingress readers and egress writers exist only there -- are left out,
    and their number is reported, so an unmodelled entity does not read as a
    disagreement.
    """
    from saag.simulation.runtime.dds_status import STATUSES

    dead = set(dead_instances)

    def counts(tr: TelemetryTrace, t: Optional[float]) -> Dict[str, Dict[str, int]]:
        out = tr.status_counts(until=t)
        live = tr.status_counts(until=t, exclude_instances=dead)
        for status, (_, kind, _) in STATUSES.items():
            if kind == "gauge" and status in out:
                out[status] = live.get(status, {})
        return out

    # The simulator has no shutdown to exclude and reports its totals at the end.
    s_end, o_end = counts(simulated, None), counts(observed, observed.end_s - end_margin_s)
    s_pre = counts(simulated, split_at) if split_at is not None else {}
    o_pre = counts(observed, split_at) if split_at is not None else {}
    excluded: Set[str] = set()
    if restrict_to_simulated:
        modelled = {k.split("|", 2)[2] for by_key in s_end.values() for k in by_key}

        def scope(c: Dict[str, Dict[str, int]]) -> Dict[str, Dict[str, int]]:
            out = {}
            for status, by_key in c.items():
                keep = {k: v for k, v in by_key.items() if k.split("|", 2)[2] in modelled}
                excluded.update(set(by_key) - set(keep))
                out[status] = keep
            return out
        o_end, o_pre = scope(o_end), scope(o_pre)
    report: Dict[str, Any] = {"excluded_observed_entities": 0, "statuses": {},
                              "only_in_simulation": sorted(set(s_end) - set(o_end)),
                              "only_observed": sorted(set(o_end) - set(s_end))}
    for status in sorted(set(s_end) | set(o_end)):
        _, kind, alias = STATUSES.get(status, ("?", "counter", None))

        def window(end, pre):
            if split_at is None or kind == "gauge":
                return dict(end.get(status, {}))
            p = pre.get(status, {})
            return {k: v - p.get(k, 0) for k, v in end.get(status, {}).items()}

        entry: Dict[str, Any] = {"rti_alias": alias, "kind": kind}
        for label, (sv, ov) in {"run": (s_end.get(status, {}), o_end.get(status, {})),
                                "after_split": (window(s_end, s_pre), window(o_end, o_pre))}.items():
            if label == "after_split" and split_at is None:
                continue
            fired_s = {k for k, v in sv.items() if v}
            fired_o = {k for k, v in ov.items() if v}
            union = fired_s | fired_o
            common = sorted(fired_s & fired_o)
            errs = [abs(sv[k] - ov[k]) / ov[k] for k in common if ov[k]]
            entry[label] = {
                "fired_simulated": len(fired_s), "fired_observed": len(fired_o),
                "fired_jaccard": (len(fired_s & fired_o) / len(union)) if union else None,
                "median_rel_error_common": statistics.median(errs) if errs else None,
                "only_simulated": sorted(fired_s - fired_o)[:10],
                "only_observed": sorted(fired_o - fired_s)[:10],
            }
        report["statuses"][status] = entry
    report["excluded_observed_entities"] = len(excluded)
    return report

