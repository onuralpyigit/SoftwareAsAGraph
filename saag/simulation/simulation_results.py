"""
simulation_results.py
─────────────────────
Dataclasses for the two simulation modes in the SaG pipeline:

  1. FaultInjectionResult  – per-node proxy ground-truth I(v) produced by
                             the BFS cascade fault injector.  This is the
                             I(v) vector that Q(v) is validated against
                             (Spearman ρ, F1, etc.).

  2. MessageFlowResult     – aggregate statistics from a discrete-event
                             pub-sub message-flow simulation run (SimPy).

Both are JSON-serialisable via `asdict()` / `to_dict()`.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Any
import json
from pathlib import Path

from ._stats import percentile


# ─────────────────────────────────────────────────────────────────────────────
# Fault Injection
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CascadeWave:
    """One propagation wave in a fault-injection cascade."""
    wave_index: int                      # 0-based
    newly_orphaned_topics: List[str]     # Topics that lost all publishers in this wave
    newly_impacted_subscribers: List[str]  # Subscribers that lost ≥1 feed in this wave
    newly_failed_publishers: List[str]   # Publishers silenced by this wave (cascade spread)


@dataclass
class FaultInjectionRecord:
    """
    Full result of injecting a single-node fault.

    I(v) – the proxy ground-truth impact score – is the primary output.
    It is defined as the *weighted* fraction of subscriber data-feed capacity
    destroyed:

        I(v) = Σ_{a ∈ Subscribers}  |lost_feeds(a)| / |total_feeds(a)|
               ─────────────────────────────────────────────────────────
                              |Subscribers|

    This is a richer measure than binary "is subscriber impacted?" because it
    captures partial feed loss (e.g. an ATCWorkstation losing 1 of 3 feeds
    scores less than losing all 3).
    """
    node_id: str
    node_type: str                   # Application | Broker | Node | Library
    node_name: str

    # Core impact score (the I(v) used for Spearman correlation with Q(v))
    impact_score: float              # ∈ [0, 1]

    # Cascade statistics
    total_orphaned_topics: int       # Topics that lost all publishers
    total_impacted_subscribers: int  # Subscribers that lost ≥1 feed
    total_subscribers: int           # Denominator
    cascade_depth: int               # Number of cascade waves fired

    # Derived breakdown
    directly_orphaned_topics: List[str]     # Topics orphaned by removing *this* node
    all_orphaned_topics: List[str]          # Including cascaded orphaning
    impacted_subscriber_ids: List[str]      # Unique subscriber IDs impacted
    per_subscriber_feed_loss: Dict[str, float]  # subscriber_id → fraction of feeds lost

    # Cascade trace (for visualisation / debugging)
    cascade_waves: List[CascadeWave] = field(default_factory=list)

    # Multi-seed stability (populated when --seeds used)
    seed_impact_scores: Dict[int, float] = field(default_factory=dict)  # seed → I(v)
    impact_score_std: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["cascade_waves"] = [asdict(w) for w in self.cascade_waves]
        return d


@dataclass
class FaultInjectionResult:
    """
    Aggregated result of a full fault-injection experiment across all nodes.
    This is the canonical output written to ``impact_scores.json``.
    """
    schema_version: str = "2.1"
    graph_id: str = ""
    total_nodes_injected: int = 0
    total_application_nodes: int = 0
    total_broker_nodes: int = 0
    total_subscribers: int = 0          # Denominator used for all I(v)
    seeds_used: List[int] = field(default_factory=list)

    # ── Provenance (schema 2.1) ──────────────────────────────────────────────
    # Which engine produced these labels. Two simulators exist and they measure
    # different quantities (see saag/simulation/models.py ImpactMetrics docstring),
    # so consumers must be able to tell them apart rather than infer it.
    labeler: str = "FaultInjector"
    #: Component types actually injected. Types absent here have NO ground truth.
    labeled_node_types: List[str] = field(default_factory=list)
    #: Label dimensions this engine genuinely produces. Anything outside this
    #: list is a structural zero, not a measurement — do not train or score on it.
    #: "reliability" is itself the r_alpha-blend of fault-tolerance and
    #: availability (see saag.core.quality_model), so this scalar-impact
    #: labeler does not additionally declare "availability" as a separate
    #: measured dimension.
    labeled_dimensions: List[str] = field(
        default_factory=lambda: ["composite", "reliability"]
    )
    #: Nodes present in the graph but never injected. Makes the coverage gap
    #: explicit instead of letting it vanish in a set intersection downstream.
    unlabeled_node_ids: List[str] = field(default_factory=list)

    #: Injected types whose every label came out 0.0. A type that scores zero
    #: everywhere is a structural zero, not a measurement, and training or
    #: scoring on it teaches a constant. This travelled only as a log line
    #: before, which a batch run discards — the same shape of defect as a
    #: silently stale cache. Carried in the artifact so a consumer can act on it.
    degenerate_node_types: List[str] = field(default_factory=list)

    #: How reproducible these labels are, measured across `seeds_used`. Travels
    #: with the labels so downstream reports can state the ceiling on any
    #: correlation metric: a model scoring rho=0.93 against labels whose own
    #: test-retest rho is 0.91 has saturated, not underperformed.
    label_stability: Dict[str, Any] = field(default_factory=dict)
    #: Non-default labeller settings (e.g. ``failure_model="threshold_cdf"``).
    #: Empty for the published labels, and then omitted from `to_dict()`.
    labeler_config: Dict[str, Any] = field(default_factory=dict)

    # Per-node records, keyed by node_id
    records: Dict[str, FaultInjectionRecord] = field(default_factory=dict)

    # Ranked summary (top-k by I(v)), populated after all records are added
    top_k_by_impact: List[Dict[str, Any]] = field(default_factory=list)

    def add_record(self, rec: FaultInjectionRecord) -> None:
        self.records[rec.node_id] = rec
        self.total_nodes_injected = len(self.records)

    def finalise(self, top_k: int = 20) -> None:
        """Sort records and build top-k summary list."""
        ranked = sorted(
            self.records.values(),
            key=lambda r: r.impact_score,
            reverse=True,
        )
        self.top_k_by_impact = [
            {
                "rank": i + 1,
                "node_id": r.node_id,
                "node_type": r.node_type,
                "node_name": r.node_name,
                "impact_score": round(r.impact_score, 4),
                "cascade_depth": r.cascade_depth,
                "orphaned_topics": r.total_orphaned_topics,
                "impacted_subscribers": r.total_impacted_subscribers,
                "impact_score_std": round(r.impact_score_std, 4),
            }
            for i, r in enumerate(ranked[:top_k])
        ]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "graph_id": self.graph_id,
            "total_nodes_injected": self.total_nodes_injected,
            "total_application_nodes": self.total_application_nodes,
            "total_broker_nodes": self.total_broker_nodes,
            "total_subscribers": self.total_subscribers,
            "seeds_used": self.seeds_used,
            "labeler": self.labeler,
            "labeled_node_types": self.labeled_node_types,
            "labeled_dimensions": self.labeled_dimensions,
            "unlabeled_node_ids": self.unlabeled_node_ids,
            "degenerate_node_types": self.degenerate_node_types,
            "label_stability": self.label_stability,
            **({"labeler_config": self.labeler_config} if self.labeler_config else {}),
            "top_k_by_impact": self.top_k_by_impact,
            "records": {
                nid: rec.to_dict() for nid, rec in self.records.items()
            },
        }

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as fh:
            json.dump(self.to_dict(), fh, indent=2)


# ─────────────────────────────────────────────────────────────────────────────
# Site Reliability Engineering (SRE) — Four Golden Signals
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class LatencySignal:
    """Latency metrics: distribution and structural decomposition."""
    p50_ms: Optional[float] = None
    p95_ms: Optional[float] = None
    p99_ms: Optional[float] = None
    mean_e2e_ms: Optional[float] = None
    mean_queue_wait_ms: Optional[float] = None
    mean_service_time_ms: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "p50_ms": round(self.p50_ms, 3) if self.p50_ms is not None else None,
            "p95_ms": round(self.p95_ms, 3) if self.p95_ms is not None else None,
            "p99_ms": round(self.p99_ms, 3) if self.p99_ms is not None else None,
            "mean_e2e_ms": round(self.mean_e2e_ms, 3) if self.mean_e2e_ms is not None else None,
            "mean_queue_wait_ms": (
                round(self.mean_queue_wait_ms, 3)
                if self.mean_queue_wait_ms is not None else None
            ),
            "mean_service_time_ms": (
                round(self.mean_service_time_ms, 3)
                if self.mean_service_time_ms is not None else None
            ),
        }


@dataclass
class TrafficSignal:
    """Traffic metrics: publication demand and delivered throughput rates."""
    published_rate_hz: float = 0.0
    delivered_rate_hz: float = 0.0
    throughput_bytes_per_s: float = 0.0
    throughput_kbps: float = 0.0
    total_messages_published: int = 0
    total_messages_delivered: int = 0
    total_bytes_delivered: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "published_rate_hz": round(self.published_rate_hz, 4),
            "delivered_rate_hz": round(self.delivered_rate_hz, 4),
            "throughput_bytes_per_s": round(self.throughput_bytes_per_s, 2),
            "throughput_kbps": round(self.throughput_kbps, 2),
            "total_messages_published": self.total_messages_published,
            "total_messages_delivered": self.total_messages_delivered,
            "total_bytes_delivered": self.total_bytes_delivered,
        }


@dataclass
class ErrorSignal:
    """Error metrics: policy breaches, buffer drops, and loss rates."""
    total_errors: int = 0
    error_rate: float = 0.0
    deadline_violations: int = 0
    queue_overflows: int = 0
    best_effort_drops: int = 0
    unserved_demand: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_errors": self.total_errors,
            "error_rate": round(self.error_rate, 4),
            "deadline_violations": self.deadline_violations,
            "queue_overflows": self.queue_overflows,
            "best_effort_drops": self.best_effort_drops,
            "unserved_demand": self.unserved_demand,
        }


@dataclass
class SaturationSignal:
    """Saturation metrics: server capacity utilization and queue buffer occupancy."""
    cpu_utilization: float = 0.0
    peak_cpu_utilization: float = 0.0
    mean_queue_depth: float = 0.0
    mean_queue_occupancy: float = 0.0
    peak_queue_occupancy: float = 0.0
    saturated_subscribers_fraction: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cpu_utilization": round(self.cpu_utilization, 4),
            "peak_cpu_utilization": round(self.peak_cpu_utilization, 4),
            "mean_queue_depth": round(self.mean_queue_depth, 2),
            "mean_queue_occupancy": round(self.mean_queue_occupancy, 4),
            "peak_queue_occupancy": round(self.peak_queue_occupancy, 4),
            "saturated_subscribers_fraction": round(self.saturated_subscribers_fraction, 4),
        }


@dataclass
class GoldenSignalsReport:
    """Consolidated Google SRE Four Golden Signals telemetry report."""
    latency: LatencySignal = field(default_factory=LatencySignal)
    traffic: TrafficSignal = field(default_factory=TrafficSignal)
    errors: ErrorSignal = field(default_factory=ErrorSignal)
    saturation: SaturationSignal = field(default_factory=SaturationSignal)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "latency": self.latency.to_dict(),
            "traffic": self.traffic.to_dict(),
            "errors": self.errors.to_dict(),
            "saturation": self.saturation.to_dict(),
        }


# ─────────────────────────────────────────────────────────────────────────────
# Message Flow Simulation
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TopicFlowStats:
    """Per-topic statistics from a message-flow simulation run."""
    topic_id: str
    topic_name: str
    reliability_policy: str             # RELIABLE | BEST_EFFORT
    deadline_ms: Optional[float]        # None means no deadline enforced
    durability_policy: str              # VOLATILE | TRANSIENT_LOCAL
    history_depth: int = 10
    #: Fan-out width: how many subscribers this topic feeds. Needed because
    #: `total_published` and `total_delivered` are counted on different units.
    n_subscribers: int = 0

    total_published: int = 0            # Messages injected by all publishers
    total_delivered: int = 0            # (subscriber, message) pairs delivered
    total_dropped_queue_full: int = 0   # Dropped due to queue overflow
    total_dropped_deadline: int = 0     # Dropped due to deadline violation
    total_dropped_best_effort: int = 0  # Dropped because policy = BEST_EFFORT under load

    latency_samples: List[float] = field(default_factory=list)  # ms, sampled
    queue_wait_samples: List[float] = field(default_factory=list)  # ms, sampled
    service_time_samples: List[float] = field(default_factory=list)  # ms, sampled
    total_bytes_delivered: int = 0

    # ── Fault-windowed counters ──────────────────────────────────────────────
    # Split on the message's *creation* time, so demand and delivery describe the
    # same population. The run used to hold these as locals and collapse them to
    # one system-wide scalar pair, which made a per-topic decomposition of
    # I_dyn(v) impossible to recover from the result object.
    published_pre: int = 0
    published_post: int = 0
    delivered_pre: int = 0
    delivered_post: int = 0
    deadline_violations_pre: int = 0
    deadline_violations_post: int = 0
    queue_overflows_pre: int = 0
    queue_overflows_post: int = 0
    #: Durability replay, at three stages: offered by the writer, accepted into
    #: a reader queue, and actually handed to the application. offered-minus-
    #: enqueued is replay lost to the queue pressure it created itself;
    #: enqueued-minus-delivered is replay that arrived too stale to be useful,
    #: which is how "durability recovers state, not timeliness" becomes a number.
    replayed_total: int = 0
    replayed_enqueued: int = 0
    replayed_delivered: int = 0

    def _window_rate(self, delivered: int, published: int) -> Optional[float]:
        """Delivered share of one window's demand, or None if nothing was due."""
        expected = published * self.n_subscribers
        return delivered / expected if expected else None

    @property
    def delivery_rate_pre(self) -> Optional[float]:
        return self._window_rate(self.delivered_pre, self.published_pre)

    @property
    def delivery_rate_post(self) -> Optional[float]:
        return self._window_rate(self.delivered_post, self.published_post)

    @property
    def i_dyn_topic(self) -> Optional[float]:
        """This topic's contribution to I_dyn(v): its own pre/post delivery drop.

        None when either window carried no demand — an unmeasured topic, which
        must stay distinguishable from one measured at zero loss.
        """
        pre, post = self.delivery_rate_pre, self.delivery_rate_post
        if pre is None or post is None:
            return None
        return pre - post

    @property
    def total_expected(self) -> int:
        """Delivery demand: one copy per (published message, subscriber)."""
        return self.total_published * self.n_subscribers

    @property
    def delivery_rate(self) -> float:
        """Delivered share of demand, in [0, 1].

        The denominator has to be `total_expected`, not `total_published`:
        `total_delivered` counts one unit per (subscriber, message) copy while
        `total_published` counts one per message, so dividing by the latter
        returned N on a fault-free topic with N subscribers — and a matching
        negative `drop_rate`. This is the per-topic form of the system-wide
        normalisation `MessageFlowSimulator.run` already applied correctly.
        """
        return self.total_delivered / self.total_expected if self.total_expected else 0.0

    @property
    def drop_rate(self) -> float:
        return 1.0 - self.delivery_rate

    @property
    def latency_p50(self) -> Optional[float]:
        return percentile(self.latency_samples, 50)

    @property
    def latency_p95(self) -> Optional[float]:
        return percentile(self.latency_samples, 95)

    @property
    def latency_p99(self) -> Optional[float]:
        return percentile(self.latency_samples, 99)

    @property
    def mean_queue_wait_ms(self) -> Optional[float]:
        return (sum(self.queue_wait_samples) / len(self.queue_wait_samples)) if self.queue_wait_samples else None

    @property
    def mean_service_time_ms(self) -> Optional[float]:
        return (sum(self.service_time_samples) / len(self.service_time_samples)) if self.service_time_samples else None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "topic_id": self.topic_id,
            "topic_name": self.topic_name,
            "reliability_policy": self.reliability_policy,
            "deadline_ms": self.deadline_ms,
            "durability_policy": self.durability_policy,
            "history_depth": self.history_depth,
            "n_subscribers": self.n_subscribers,
            "total_published": self.total_published,
            "total_expected": self.total_expected,
            "total_delivered": self.total_delivered,
            "total_bytes_delivered": self.total_bytes_delivered,
            "total_dropped_queue_full": self.total_dropped_queue_full,
            "total_dropped_deadline": self.total_dropped_deadline,
            "total_dropped_best_effort": self.total_dropped_best_effort,
            "delivery_rate": round(self.delivery_rate, 4),
            "drop_rate": round(self.drop_rate, 4),
            "published_pre": self.published_pre,
            "published_post": self.published_post,
            "delivered_pre": self.delivered_pre,
            "delivered_post": self.delivered_post,
            "deadline_violations_pre": self.deadline_violations_pre,
            "deadline_violations_post": self.deadline_violations_post,
            "queue_overflows_pre": self.queue_overflows_pre,
            "queue_overflows_post": self.queue_overflows_post,
            "replayed_total": self.replayed_total,
            "replayed_enqueued": self.replayed_enqueued,
            "replayed_delivered": self.replayed_delivered,
            "delivery_rate_pre": (
                round(self.delivery_rate_pre, 4)
                if self.delivery_rate_pre is not None else None
            ),
            "delivery_rate_post": (
                round(self.delivery_rate_post, 4)
                if self.delivery_rate_post is not None else None
            ),
            "i_dyn_topic": (
                round(self.i_dyn_topic, 4) if self.i_dyn_topic is not None else None
            ),
            "latency_p50_ms": round(self.latency_p50, 3) if self.latency_p50 is not None else None,
            "latency_p95_ms": round(self.latency_p95, 3) if self.latency_p95 is not None else None,
            "latency_p99_ms": round(self.latency_p99, 3) if self.latency_p99 is not None else None,
            "mean_queue_wait_ms": round(self.mean_queue_wait_ms, 3) if self.mean_queue_wait_ms is not None else None,
            "mean_service_time_ms": round(self.mean_service_time_ms, 3) if self.mean_service_time_ms is not None else None,
        }


@dataclass
class SubscriberFlowStats:
    """Per-subscriber statistics from a message-flow simulation run."""
    subscriber_id: str
    subscribed_topics: List[str]

    received_per_topic: Dict[str, int] = field(default_factory=dict)     # topic_id → count
    missed_per_topic: Dict[str, int] = field(default_factory=dict)       # topic_id → count
    deadline_violations_per_topic: Dict[str, int] = field(default_factory=dict)

    # Fault-windowed statistics (populated only when fault injection is enabled).
    # Both windows are recorded so that a faulted node's own receipts can be
    # subtracted out of the before/after comparison.
    received_pre_fault: int = 0
    received_post_fault: int = 0
    missed_post_fault: int = 0
    #: The same two windows split per topic, so a per-topic I_dyn can subtract
    #: the faulted node's own receipts exactly as the system-wide rate does.
    received_window_per_topic: Dict[str, Dict[str, int]] = field(default_factory=dict)

    @property
    def total_received(self) -> int:
        return sum(self.received_per_topic.values())

    @property
    def total_missed(self) -> int:
        return sum(self.missed_per_topic.values())

    @property
    def overall_delivery_rate(self) -> float:
        total = self.total_received + self.total_missed
        return self.total_received / total if total else 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "subscriber_id": self.subscriber_id,
            "subscribed_topics": self.subscribed_topics,
            "received_per_topic": self.received_per_topic,
            "missed_per_topic": self.missed_per_topic,
            "deadline_violations_per_topic": self.deadline_violations_per_topic,
            "total_received": self.total_received,
            "total_missed": self.total_missed,
            "overall_delivery_rate": round(self.overall_delivery_rate, 4),
            "received_pre_fault": self.received_pre_fault,
            "received_post_fault": self.received_post_fault,
            "missed_post_fault": self.missed_post_fault,
        }


@dataclass
class FaultEventRecord:
    """Records the timing and cascade of a fault injected during simulation."""
    fault_time: float
    faulted_node_id: str
    faulted_node_type: str
    cascade_silenced_publishers: List[str]  # Publishers that went silent after fault
    cascade_orphaned_topics: List[str]      # Topics that died after fault
    cascade_impacted_subscribers: List[str]  # Subscribers that lost feeds after fault
    delivery_rate_before: float            # System-wide rate in [0, fault_time]
    delivery_rate_after: float             # System-wide rate in (fault_time, end]
    # I_dyn(v): system-wide delivered-message latency, windowed on fault_time.
    # None when no latency was observed in that window.
    latency_p50_before: Optional[float] = None
    latency_p50_after: Optional[float] = None
    latency_p95_before: Optional[float] = None
    latency_p95_after: Optional[float] = None

    #: Per-topic decomposition of I_dyn(v): topic_id -> delivery_rate drop.
    #: Topics with no demand in one of the windows are omitted, not zeroed.
    per_topic_i_dyn: Dict[str, float] = field(default_factory=dict)

    #: QoS-contract violations attributable to each window.
    deadline_violations_before: int = 0
    deadline_violations_after: int = 0
    queue_overflows_before: int = 0
    queue_overflows_after: int = 0

    #: Diagnostic entity-impact telemetry (lost nodes in the fault cascade)
    lost_topics_count: int = 0
    lost_critical_topics_count: int = 0
    lost_applications_count: int = 0
    lost_critical_applications_count: int = 0

    #: Measurement geometry, recorded so a reader can tell which part of the run
    #: each window covers without re-deriving it from the run parameters.
    warmup_s: float = 0.0
    guard_band_s: float = 0.0
    pre_window_s: float = 0.0
    post_window_s: float = 0.0

    #: Four Golden Signals before and after fault injection.
    signals_before: Optional[GoldenSignalsReport] = None
    signals_after: Optional[GoldenSignalsReport] = None

    # ── Runtime-behaviour extensions (saag/simulation/runtime/) ──────────────
    # All None under the default open-loop, windowed run, and then omitted from
    # `to_dict()`, so default result files are unchanged.
    #: Paired estimator: the same run without the fault, on the same RNG
    #: streams, measured over the same post window and surviving population.
    delivery_rate_baseline_after: Optional[float] = None
    i_dyn_paired: Optional[float] = None
    #: Reactive model: applications that stalled after the fault, how long after
    #: it each one did, and the deepest pub-sub hop among them.
    stalled_apps: Optional[List[str]] = None
    time_to_stall_s: Optional[Dict[str, float]] = None
    cascade_hops: Optional[int] = None
    #: Runtime fault controller: seconds from the end of a bounded primary fault
    #: until delivery regained 95% of its pre-fault rate (None: never, or not a
    #: bounded fault), and the controller's event log.
    time_to_recover_s: Optional[float] = None
    runtime_events: Optional[List[Dict[str, Any]]] = None

    @property
    def i_dyn(self) -> float:
        """I_dyn(v): the delivery-rate loss surviving consumers suffer.

        Not clamped to [0, 1]: once the subscriber's compute is contended,
        removing a chatty publisher can relieve more load than it removes feeds,
        and a negative value there is a real measurement.
        """
        return self.delivery_rate_before - self.delivery_rate_after

    @property
    def delta_latency_p95(self) -> Optional[float]:
        """Change in p95 latency: post-fault minus pre-fault.

        Note: can be negative when removing a chatty publisher relieves
        contention for surviving consumers.
        """
        if self.latency_p95_after is not None and self.latency_p95_before is not None:
            return round(self.latency_p95_after - self.latency_p95_before, 6)
        return None

    @property
    def latency_inflation_factor(self) -> Optional[float]:
        """LIF: latency_p95_after / latency_p95_before."""
        if (
            self.latency_p95_after is not None
            and self.latency_p95_before is not None
            and self.latency_p95_before > 0
        ):
            return round(self.latency_p95_after / self.latency_p95_before, 6)
        return None

    @property
    def delta_deadline_violations(self) -> int:
        """Post-fault minus pre-fault deadline violations."""
        return self.deadline_violations_after - self.deadline_violations_before

    @property
    def delta_queue_overflows(self) -> int:
        """Post-fault minus pre-fault queue overflows."""
        return self.queue_overflows_after - self.queue_overflows_before

    def to_dict(self) -> Dict[str, Any]:
        out = asdict(self)
        out["i_dyn"] = round(self.i_dyn, 6)
        out["delta_latency_p95"] = self.delta_latency_p95
        out["latency_inflation_factor"] = self.latency_inflation_factor
        out["delta_deadline_violations"] = self.delta_deadline_violations
        out["delta_queue_overflows"] = self.delta_queue_overflows
        out["signals_before"] = self.signals_before.to_dict() if self.signals_before else None
        out["signals_after"] = self.signals_after.to_dict() if self.signals_after else None
        for key in self._RUNTIME_FIELDS:
            if out[key] is None:
                del out[key]
        return out

    _RUNTIME_FIELDS = (
        "delivery_rate_baseline_after", "i_dyn_paired",
        "stalled_apps", "time_to_stall_s", "cascade_hops",
        "time_to_recover_s", "runtime_events",
    )


@dataclass
class MessageFlowResult:
    """
    Full result of one discrete-event message-flow simulation run.
    """
    schema_version: str = "2.0"
    graph_id: str = ""
    simulation_duration: float = 0.0     # simulated seconds
    seed: int = 42
    fault_event: Optional[FaultEventRecord] = None

    # Aggregate system-wide metrics
    system_delivery_rate: float = 0.0    # fraction of all published messages delivered
    system_drop_rate: float = 0.0
    total_messages_published: int = 0
    total_messages_delivered: int = 0
    total_deadline_violations: int = 0
    total_queue_overflows: int = 0

    #: Which QoS policies this run enforced (see MessageFlowSimulator.QOS_MODES).
    #: Recorded so a result file states its own ablation arm rather than relying
    #: on the caller to remember which one produced it.
    qos_mode: str = "legacy"
    #: How topic payload sizes entered the run (see MessageFlowSimulator.PAYLOAD_MODELS).
    payload_model: str = "fixed"

    # ── Operating point ──────────────────────────────────────────────────────
    #: Requested baseline utilization, or None when service times were not
    #: calibrated.
    target_utilization: Optional[float] = None
    utilization_mode: str = "per_subscriber"
    service_distribution: str = "exponential"
    #: Realised busy fraction per calibrated subscriber. The check that the
    #: operating point actually landed: a `target_utilization` that does not
    #: show up here is a requested number, not a measured one, and no result
    #: derived from it should be trusted until the two agree.
    measured_utilization: Dict[str, float] = field(default_factory=dict)
    #: Mean service time per subscriber, in seconds.
    service_time_s: Dict[str, float] = field(default_factory=dict)

    # Per-topic and per-subscriber breakdowns
    topic_stats: Dict[str, TopicFlowStats] = field(default_factory=dict)
    subscriber_stats: Dict[str, SubscriberFlowStats] = field(default_factory=dict)

    # Which components this engine could actually observe. It sees only
    # PUBLISHES_TO / SUBSCRIBES_TO traffic, so Brokers (ROUTES) and Nodes
    # (RUNS_ON) are structurally invisible: faulting one is a no-op. They are
    # listed as unlabelled rather than scored 0.0, so that a component nobody
    # measured is never mistaken for one measured to be harmless.
    labeler: str = "MessageFlowSimulator"
    labeled_node_ids: List[str] = field(default_factory=list)
    unlabeled_node_ids: List[str] = field(default_factory=list)
    #: Non-default runtime settings this run used (behavior_model, rng_streams,
    #: estimator, ...). Empty for a default run, and then omitted from the dict.
    runtime_config: Dict[str, Any] = field(default_factory=dict)
    #: Per-message trace (``record_events=True``), in the format of
    #: `saag.simulation.calibration`. Not part of `to_dict()`: export it with
    #: `export_events` instead.
    events: Optional[List[Dict[str, Any]]] = None
    #: DDS standard statuses (``dds_status`` / ``qos_semantics="dds"``), see
    #: saag/simulation/runtime/dds_status.py. None for a default run, and then
    #: omitted from `to_dict()`.
    dds_status: Optional[Dict[str, Any]] = None

    def export_events(self, path: Path) -> int:
        """Write the event log as OpenTelemetry-shaped JSON lines; returns the count."""
        if self.events is None:
            raise ValueError("this run did not record events (record_events=False)")
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as fh:
            for ev in self.events:
                fh.write(json.dumps(ev) + "\n")
        return len(self.events)

    #: Google SRE Four Golden Signals report (overall run)
    golden_signals: Optional[GoldenSignalsReport] = None

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "schema_version": self.schema_version,
            "graph_id": self.graph_id,
            "simulation_duration": self.simulation_duration,
            "seed": self.seed,
            "fault_event": self.fault_event.to_dict() if self.fault_event else None,
            "system_delivery_rate": round(self.system_delivery_rate, 4),
            "system_drop_rate": round(self.system_drop_rate, 4),
            "total_messages_published": self.total_messages_published,
            "total_messages_delivered": self.total_messages_delivered,
            "total_deadline_violations": self.total_deadline_violations,
            "total_queue_overflows": self.total_queue_overflows,
            "qos_mode": self.qos_mode,
            "payload_model": self.payload_model,
            "target_utilization": self.target_utilization,
            "utilization_mode": self.utilization_mode,
            "service_distribution": self.service_distribution,
            "measured_utilization": {
                k: round(v, 4) for k, v in self.measured_utilization.items()
            },
            "service_time_s": self.service_time_s,
            "golden_signals": self.golden_signals.to_dict() if self.golden_signals else None,
            "topic_stats": {tid: ts.to_dict() for tid, ts in self.topic_stats.items()},
            "subscriber_stats": {sid: ss.to_dict() for sid, ss in self.subscriber_stats.items()},
            "labeler": self.labeler,
            "labeled_node_ids": self.labeled_node_ids,
            "unlabeled_node_ids": self.unlabeled_node_ids,
        }
        if self.runtime_config:
            d["runtime_config"] = self.runtime_config
        if self.dds_status is not None:
            d["dds_status"] = self.dds_status
        return d

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as fh:
            json.dump(self.to_dict(), fh, indent=2)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

