"""
DDS standard communication statuses, and the QoS semantics they are defined by.

Two things live here.

1. **The DDS-semantics QoS arm** (``MessageFlowSimulator(qos_semantics="dds")``)
   needs the DDS definitions the published engine does not follow:

   * DEADLINE is a *period* contract: a reader misses it when no new sample
     arrives within the period (a writer, when it does not write within it).
     Nothing is dropped. The published engine drops samples whose end-to-end
     latency exceeds ``deadline_ms`` -- that is LIFESPAN semantics.
   * Request-vs-offered (RxO) matching: a writer whose offered QoS is weaker
     than a reader's requested QoS is never matched with it, so the pair does
     not communicate (`rxo_compatible`).
   * KEEP_LAST history replaces the oldest sample when full, whatever the
     reliability. The published engine drops the *new* sample on a full
     BEST_EFFORT queue (KEEP_ALL with resource limits).

2. **Status counting** (``dds_status=True``), named after the DCPS statuses so a
   run compares directly with what a DDS implementation reports -- Cyclone DDS
   in the testbed (``saag/testbed/dds.py``), or RTI Connext's Observability
   Framework, whose metric names are given as aliases (`STATUSES`).

Entities are keyed ``"reader|<topic>|<component>"`` /
``"writer|<topic>|<component>"`` everywhere -- simulator, testbed and fidelity
report -- so the two sides join on the key alone.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

QOS_SEMANTICS: Tuple[str, ...] = ("engine", "dds")

#: status -> (entity, kind, RTI Connext 7.7 Observability metric name).
#: "counter" statuses accumulate; "gauge" statuses are a value at an instant.
STATUSES: Dict[str, Tuple[str, str, Optional[str]]] = {
    "requested_deadline_missed": ("reader", "counter", "dds_data_reader_deadline_missed_total"),
    "offered_deadline_missed": ("writer", "counter", "dds_data_writer_deadline_missed_total"),
    "sample_lost": ("reader", "counter", "dds_data_reader_sample_lost_total"),
    "sample_rejected": ("reader", "counter", "dds_data_reader_sample_rejected_total"),
    # LIVELINESS_CHANGED. With AUTOMATIC liveliness a crashed or frozen host's
    # participant lease expires and its writers are *removed* -- alive_count
    # drops, not_alive_count does not rise (measured on Cyclone DDS, 2026-10-23).
    # So the simulator reports alive_count; not_alive_count is read from the
    # middleware only, and never reported as a simulated zero.
    "liveliness_alive_count": ("reader", "gauge", None),   # RTI exports no alive count
    "liveliness_not_alive_count": ("reader", "gauge", "dds_data_reader_liveliness_not_alive_count"),
    "requested_incompatible_qos": ("reader", "counter", "dds_data_reader_incompatible_qos_total"),
    # Reader-cache accounting the engine already keeps, under RTI's names.
    "cache_replaced_dropped_samples": (
        "reader", "counter", "dds_data_reader_cache_replaced_dropped_samples_total"),
    "cache_samples_peak": ("reader", "gauge", "dds_data_reader_cache_samples_peak"),
}

#: DURABILITY ordering for RxO: offered must be at least requested.
DURABILITY_ORDER = {"VOLATILE": 0, "TRANSIENT_LOCAL": 1, "TRANSIENT": 2, "PERSISTENT": 3}


def entity_key(kind: str, topic: str, component: str) -> str:
    return f"{kind}|{topic}|{component}"


def rxo_compatible(offered: Any, requested: Any) -> Tuple[bool, List[str]]:
    """DDS request-vs-offered check; returns (compatible, failing policies).

    Both arguments need ``reliability``, ``durability`` and ``deadline_ms``
    (a `QoSProfile` or any object/mapping with them). ``deadline_ms`` None is
    an infinite period.
    """
    def get(obj: Any, name: str) -> Any:
        return obj.get(name) if isinstance(obj, Mapping) else getattr(obj, name, None)

    failed: List[str] = []
    if (str(get(requested, "reliability") or "BEST_EFFORT").upper() == "RELIABLE"
            and str(get(offered, "reliability") or "BEST_EFFORT").upper() != "RELIABLE"):
        failed.append("reliability")
    if (DURABILITY_ORDER.get(str(get(offered, "durability") or "VOLATILE").upper(), 0)
            < DURABILITY_ORDER.get(str(get(requested, "durability") or "VOLATILE").upper(), 0)):
        failed.append("durability")
    off_d, req_d = get(offered, "deadline_ms"), get(requested, "deadline_ms")
    if req_d is not None and (off_d is None or float(off_d) > float(req_d)):
        failed.append("deadline")
    return not failed, failed


@dataclass
class DeadlineMonitor:
    """Lazy DDS deadline counting for one reader (or writer).

    The timer starts at the first sample. Every period that then elapses with
    no new sample is one miss, and the timer restarts at the miss -- so a gap g
    costs ``floor(g / period)`` misses. Counted on arrival, not with timers,
    and split across `segments` by the instant each period elapsed.
    """

    period: float
    segments: Sequence[Tuple[float, float, str]]
    last: Optional[float] = None
    counts: Dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def _add(self, start: float, n: int) -> None:
        """Misses elapsing at start + k*period, k = 1..n, bucketed by segment."""
        if n <= 0:
            return
        for lo, hi, name in self.segments:
            # k with lo <= start + k*period < hi.
            k_lo = max(1, math.ceil((lo - start) / self.period - 1e-12))
            k_hi = n if math.isinf(hi) else min(n, math.ceil((hi - start) / self.period - 1e-12) - 1)
            if k_hi >= k_lo:
                self.counts[name] += k_hi - k_lo + 1
        self.counts["total"] += n

    def arrival(self, t: float) -> None:
        if self.last is not None:
            self._add(self.last, int((t - self.last) // self.period))
        self.last = t

    def stop(self, t: float) -> None:
        """Close the monitor at *t* (end of run, or the entity died)."""
        self.arrival(t)
        self.last = None

    def pending(self, t: float) -> int:
        """Total misses as of *t*, without closing (for live snapshots)."""
        extra = int((t - self.last) // self.period) if self.last is not None else 0
        return self.counts["total"] + extra


class DdsStatusRecorder:
    """Collects the statuses of one `MessageFlowSimulator` run.

    ``segments`` are the measurement windows as ``(start, end, name)``, the
    same pre/post split the engine's bucketer uses. ``matched_writers(topic)``
    gives the writers a reader of *topic* is matched with: the routing brokers
    for a brokered topic (readers are matched with the relay, as in the
    testbed), otherwise the publishers.
    """

    def __init__(self, segments: Sequence[Tuple[float, float, str]], lease_s: float,
                 matched_writers: Mapping[str, Sequence[str]],
                 readers: Mapping[str, Sequence[str]], hosts: Mapping[str, Sequence[str]]) -> None:
        self.segments = list(segments)
        self.lease_s = lease_s
        self.matched_writers = matched_writers
        self.readers = readers
        self.hosts = hosts
        self.counts: Dict[str, Dict[str, Dict[str, int]]] = defaultdict(
            lambda: defaultdict(lambda: defaultdict(int)))
        self.monitors: Dict[str, DeadlineMonitor] = {}
        self.down_since: Dict[str, float] = {}
        self.down_intervals: Dict[str, List[Tuple[float, float]]] = defaultdict(list)
        self.incompatible: Dict[str, List[str]] = {}

    # ── bucketing ──────────────────────────────────────────────────────────
    def _windows(self, t: float) -> List[str]:
        return [name for lo, hi, name in self.segments if lo <= t < hi]

    def _bump(self, status: str, key: str, t: float, n: int = 1) -> None:
        c = self.counts[status][key]
        c["total"] += n
        for w in self._windows(t):
            c[w] += n

    # ── hooks ──────────────────────────────────────────────────────────────
    def add_deadline(self, key: str, deadline_ms: Optional[float]) -> None:
        if deadline_ms is not None and float(deadline_ms) > 0:
            self.monitors[key] = DeadlineMonitor(float(deadline_ms) / 1000.0, self.segments)

    def reader_arrival(self, topic: str, reader: str, t: float) -> None:
        mon = self.monitors.get(entity_key("reader", topic, reader))
        if mon is not None:
            mon.arrival(t)

    def writer_emit(self, writer: str, topic: str, t: float) -> None:
        mon = self.monitors.get(entity_key("writer", topic, writer))
        if mon is not None:
            mon.arrival(t)

    def replaced(self, topic: str, reader: str, t: float) -> None:
        self._bump("cache_replaced_dropped_samples", entity_key("reader", topic, reader), t)

    def rejected(self, topic: str, reader: str, t: float) -> None:
        self._bump("sample_rejected", entity_key("reader", topic, reader), t)

    def lost(self, topic: str, reader: str, t: float) -> None:
        self._bump("sample_lost", entity_key("reader", topic, reader), t)

    def mark_incompatible(self, topic: str, reader: str, writer: str, policies: List[str]) -> None:
        key = entity_key("reader", topic, reader)
        self.counts["requested_incompatible_qos"][key]["total"] += 1
        self.incompatible.setdefault(key, []).append(f"{writer}:{'+'.join(policies)}")

    def down(self, comp: str, t: float) -> None:
        if comp in self.down_since:
            return
        self.down_since[comp] = t
        # A dead entity stops counting deadline misses.
        for key, mon in self.monitors.items():
            if key.rsplit("|", 1)[1] == comp:
                mon.stop(t)
        # A reader whose matched writers are now all gone has an instance with
        # no writers (NOT_ALIVE_NO_WRITERS) once the lease expires, and DDS stops
        # monitoring its deadline until a writer is back (measured on Cyclone).
        for topic, writers in self.matched_writers.items():
            if comp in writers and all(w in self.down_since for w in writers):
                for reader in self.readers.get(topic, ()):
                    mon = self.monitors.get(entity_key("reader", topic, reader))
                    if mon is not None and mon.last is not None:
                        mon.stop(max(mon.last, t + self.lease_s))

    def up(self, comp: str, t: float) -> None:
        start = self.down_since.pop(comp, None)
        if start is not None:
            self.down_intervals[comp].append((start, t))

    # ── results ────────────────────────────────────────────────────────────
    def _down_at(self, comp: str, t: float) -> bool:
        spans = list(self.down_intervals.get(comp, []))
        if comp in self.down_since:
            spans.append((self.down_since[comp], math.inf))
        return any(s <= t < e for s, e in spans)

    def _alive(self, writer: str, t: float) -> bool:
        """Is *writer* matched and alive at *t* (not down for a full lease)?"""
        return not self._not_alive(writer, t)

    def _not_alive(self, writer: str, t: float) -> bool:
        """Was *writer* down, for at least one lease, at instant *t*?"""
        spans = list(self.down_intervals.get(writer, []))
        if writer in self.down_since:
            spans.append((self.down_since[writer], math.inf))
        return any(s + self.lease_s <= t < e for s, e in spans)

    def finalise(self, end: float, queues: Mapping[Tuple[str, str], Any]) -> Dict[str, Any]:
        for key, mon in self.monitors.items():
            if mon.last is not None:
                mon.stop(end)
            if mon.counts["total"]:
                status = "requested_deadline_missed" if key.startswith("reader|") else "offered_deadline_missed"
                self.counts[status][key] = dict(mon.counts)
        # alive_count is a gauge: matched writers alive at the end of each window.
        for topic, readers in self.readers.items():
            writers = list(self.matched_writers.get(topic, ()))
            for reader in readers:
                key = entity_key("reader", topic, reader)
                vals = {}
                # A dead reader reports nothing: no gauge value for that window.
                for lo, hi, name in self.segments:
                    t = min(hi, end) - 1e-9
                    if not self._down_at(reader, t):
                        vals[name] = sum(1 for w in writers if self._alive(w, t))
                if not self._down_at(reader, end - 1e-9):
                    vals["total"] = sum(1 for w in writers if self._alive(w, end - 1e-9))
                if writers and vals:
                    self.counts["liveliness_alive_count"][key] = vals
        for (topic, reader), q in queues.items():
            peaks = {"pre": q._peak_pre, "post": q._peak_post, "total": q._peak_depth}
            if peaks["total"]:
                self.counts["cache_samples_peak"][entity_key("reader", topic, reader)] = peaks
        entities = {}
        for topic, readers in self.readers.items():
            for r in readers:
                entities[entity_key("reader", topic, r)] = {
                    "kind": "reader", "topic": topic, "component": r,
                    "host": (self.hosts.get(r) or [None])[0]}
        for key in self.monitors:
            if key.startswith("writer|"):
                _, topic, w = key.split("|", 2)
                entities[key] = {"kind": "writer", "topic": topic, "component": w,
                                 "host": (self.hosts.get(w) or [None])[0]}
        return {
            "statuses": {s: {k: dict(v) for k, v in by_key.items()}
                         for s, by_key in sorted(self.counts.items()) if by_key},
            "entities": entities,
            "incompatible_pairs": self.incompatible,
            "rti_aliases": {s: alias for s, (_, _, alias) in STATUSES.items()},
            "lease_s": self.lease_s,
        }


def status_snapshot_events(status: Mapping[str, Any], t: float, window: str) -> List[Dict[str, Any]]:
    """`saag.status` trace records for one window's cumulative counts (simulator side)."""
    from ..calibration import make_event

    out = []
    for name, by_key in status.get("statuses", {}).items():
        for key, vals in by_key.items():
            kind, topic, comp = key.split("|", 2)
            value = vals.get(window, 0) if window != "total" else vals.get("total", 0)
            out.append(make_event(t, "saag.status", comp, topic, status=name,
                                  count=value, entity=kind, source="simulator"))
    return out
