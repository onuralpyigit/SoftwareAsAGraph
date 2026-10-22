from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Set, Tuple, Any, Optional, Union
from enum import Enum

from ._stats import nearest_rank


# =============================================================================
# Core Simulation Enums
# =============================================================================

class ComponentState(Enum):
    """State of a component during simulation."""
    ACTIVE = "active"
    FAILED = "failed"
    DEGRADED = "degraded"
    OVERLOADED = "overloaded"
    COMPROMISED = "compromised"


class EventType(Enum):
    """Types of discrete events in the simulation."""
    PUBLISH = "publish"       # Message published by app
    ROUTE = "route"           # Message routed by broker
    DELIVER = "deliver"       # Message delivered to subscriber
    ACK = "ack"               # Acknowledgment (for reliable delivery)
    TIMEOUT = "timeout"       # Delivery timeout
    DROP = "drop"             # Message dropped
    FAIL_COMPONENT = "fail_component"       # Poisson-injected component failure
    RECOVER_COMPONENT = "recover_component" # Recovery after a Poisson failure


class FailureMode(Enum):
    """Types of component failure modes."""
    CRASH = "crash"           # Complete failure - component stops
    DEGRADED = "degraded"     # Partial failure (50% capacity). Starvation threshold: SL < 0.3
    PARTITION = "partition"   # Network partition - unreachable
    OVERLOAD = "overload"     # Resource exhaustion (Future work)


class CascadeRule(Enum):
    """Rules governing failure cascade propagation."""
    PHYSICAL = "physical"     # Node failure cascades to hosted components
    LOGICAL = "logical"       # Broker failure affects topic routing
    NETWORK = "network"       # Network partition propagation
    LIBRARY = "library"       # Library failure cascades to using applications
    ALL = "all"               # All cascade rules applied


# =============================================================================
# Core Entities
# =============================================================================

@dataclass
class ComponentInfo:
    """Information about a component in the simulation."""
    id: str
    type: str  # Application, Topic, Broker, Node
    state: ComponentState = ComponentState.ACTIVE
    weight: float = 1.0
    properties: Dict[str, Any] = field(default_factory=dict)
    # Custom continuous performance value override
    custom_performance: Optional[float] = None
    
    # Runtime metrics (accumulated during simulation)
    messages_sent: int = 0
    messages_received: int = 0
    messages_dropped: int = 0
    messages_routed: int = 0
    total_latency: float = 0.0
    
    def reset_metrics(self) -> None:
        """Reset runtime metrics for a new simulation run."""
        self.messages_sent = 0
        self.messages_received = 0
        self.messages_dropped = 0
        self.messages_routed = 0
        self.total_latency = 0.0
        self.custom_performance = None
    
    @property
    def avg_latency(self) -> float:
        """Average latency per message processed."""
        if self.messages_sent + self.messages_received == 0:
            return 0.0
        return self.total_latency / (self.messages_sent + self.messages_received)

    @property
    def performance(self) -> float:
        """Performance level (1.0 = healthy, 0.5 = degraded, 0.0 = failed)."""
        if self.custom_performance is not None:
            return self.custom_performance
        if self.state == ComponentState.FAILED:
            return 0.0
        elif self.state == ComponentState.DEGRADED:
            return 0.5
        # COMPROMISED continues to operate (return 1.0) but produces malicious output
        return 1.0
    
    @property
    def throughput(self) -> int:
        """Total messages processed (sent + routed)."""
        return self.messages_sent + self.messages_routed


@dataclass
class TopicInfo:
    """Information about a topic including QoS settings."""
    id: str
    name: str
    message_size: int = 1024
    qos_reliability: str = "BEST_EFFORT"  # BEST_EFFORT, RELIABLE
    qos_durability: str = "VOLATILE"      # VOLATILE, TRANSIENT, PERSISTENT
    qos_priority: str = "LOW"             # LOW, MEDIUM, HIGH, URGENT
    weight: float = 1.0
    deadline_ms: Optional[float] = None
    history_depth: int = 10
    
    @property
    def requires_ack(self) -> bool:
        """Check if topic requires acknowledgment (reliable delivery)."""
        return self.qos_reliability == "RELIABLE"
    
    @property
    def priority_value(self) -> int:
        """Numeric priority for scheduling."""
        return {"URGENT": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}.get(self.qos_priority, 1)
    
# =============================================================================
# Event Simulation Models
# =============================================================================

@dataclass(order=True)
class Event:
    """A discrete event in the simulation."""
    time: float                                      # Event time (for priority queue)
    event_type: EventType = field(compare=False)
    source: str = field(compare=False)               # Source component
    target: str = field(compare=False)               # Target component
    message_id: str = field(compare=False)
    data: Dict[str, Any] = field(default_factory=dict, compare=False)


@dataclass
class Message:
    """A message being transmitted through the pub-sub system."""
    id: str
    source_app: str
    topic_id: str
    size: int
    priority: int
    requires_ack: bool
    created_at: float
    hops: int = 0
    delivered_to: Set[str] = field(default_factory=set)
    dropped: bool = False
    drop_reason: Optional[str] = None
    delivered_at: Optional[float] = None
    
    @property
    def latency(self) -> float:
        """End-to-end latency (only valid after delivery)."""
        if self.delivered_at is not None:
            return self.delivered_at - self.created_at
        return 0.0


@dataclass
class EventScenario:
    """Configuration for an event simulation run."""
    source_app: str
    description: str = ""
    num_messages: int = 100
    message_interval: float = 0.01
    message_size: int = 1024
    duration: float = 10.0
    seed: Optional[int] = None
    publish_latency: float = 0.001
    broker_latency: float = 0.002
    network_latency: float = 0.005
    base_processing_latency: float = 0.001
    complexity_scale_factor: float = 1.0
    library_complexity_weight: float = 0.3
    enrich_processing_time: bool = True
    drop_probability: float = 0.0
    broker_failure_prob: float = 0.0
    delivery_timeout: float = 1.0
    failure_rate: float = 0.0
    failure_targets: Optional[List[str]] = None
    mean_recovery_time: float = 0.0
    poisson_arrivals: bool = False


@dataclass
class RuntimeMetrics:
    """Runtime metrics collected during event simulation."""
    messages_published: int = 0
    messages_delivered: int = 0
    messages_dropped: int = 0
    total_latency: float = 0.0
    min_latency: float = float('inf')
    max_latency: float = 0.0
    latencies: List[float] = field(default_factory=list)
    simulation_duration: float = 0.0
    
    @property
    def delivery_rate(self) -> float:
        """Percentage of messages delivered."""
        total = self.messages_published
        return (self.messages_delivered / total * 100) if total > 0 else 0.0
    
    @property
    def drop_rate(self) -> float:
        """Percentage of messages dropped."""
        total = self.messages_published
        return (self.messages_dropped / total * 100) if total > 0 else 0.0
    
    @property
    def avg_latency(self) -> float:
        """Average latency in seconds."""
        return self.total_latency / self.messages_delivered if self.messages_delivered > 0 else 0.0
    
    @property
    def p99_latency(self) -> float:
        """99th percentile latency (nearest-rank; see saag/simulation/_stats.py)."""
        return nearest_rank(self.latencies, 99)
    
    @property
    def throughput(self) -> float:
        """Messages per second."""
        return self.messages_delivered / self.simulation_duration if self.simulation_duration > 0 else 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "messages_published": self.messages_published,
            "messages_delivered": self.messages_delivered,
            "messages_dropped": self.messages_dropped,
            "delivery_rate_percent": round(self.delivery_rate, 2),
            "drop_rate_percent": round(self.drop_rate, 2),
            "avg_latency_ms": round(self.avg_latency * 1000, 3),
            "p99_latency_ms": round(self.p99_latency * 1000, 3),
            "throughput_per_sec": round(self.throughput, 2),
        }


@dataclass
class EventResult:
    """Result of an event simulation run."""
    source_app: str
    scenario: str
    duration: float
    metrics: RuntimeMetrics
    affected_topics: List[str] = field(default_factory=list)
    reached_subscribers: List[str] = field(default_factory=list)
    brokers_used: List[str] = field(default_factory=list)
    successful_flows: List[Tuple[str, str, str]] = field(default_factory=list) # (Pub, Topic, Sub)
    component_impacts: Dict[str, float] = field(default_factory=dict)
    failed_components: List[str] = field(default_factory=list)
    drop_reasons: Dict[str, int] = field(default_factory=dict)
    csc_names: Dict[str, str] = field(default_factory=dict)
    related_components: List[str] = field(default_factory=list)
    poisson_failure_log: List[Dict[str, Any]] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_app": self.source_app,
            "scenario": self.scenario,
            "duration_sec": round(self.duration, 3),
            "metrics": self.metrics.to_dict(),
            "affected_topics": self.affected_topics,
            "reached_subscribers": self.reached_subscribers,
            "brokers_used": self.brokers_used,
            "component_impacts": {k: round(v, 4) for k, v in self.component_impacts.items()},
            "failed_components": self.failed_components,
            "drop_reasons": self.drop_reasons,
            "related_components": self.related_components,
        }


# =============================================================================
# Failure Simulation Models
# =============================================================================

@dataclass
class RuntimeTelemetryProfile:
    """Holds empirical execution data to calibrate the simulation loop.

    The first three fields are read by `FailureSimulator`. Note that
    `edge_failure_correlation` is used there as a *cascade propagation
    probability*, so a measured message-drop rate must not be written into it:
    `saag.simulation.calibration.TelemetryCalibrator` never does.

    The remaining fields are filled by `TelemetryCalibrator` from observed
    telemetry and read by `MessageFlowSimulator(telemetry_profile=...)`, each
    overriding the declared or calibrated value it measures.
    """
    msg_rate_per_sec: Dict[str, float] = field(default_factory=dict)       # topic_id -> throughput
    edge_failure_correlation: Dict[Tuple[str, str], float] = field(default_factory=dict) # (src, tgt) -> drop rate
    custom_starvation_bounds: Dict[str, float] = field(default_factory=dict) # app_id -> custom threshold
    #: app_id -> {"dist": "exponential" | "lognormal", "mean_s": float, "cv": float, "n": int}
    service_time_dist: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    #: app_id -> inferred trigger policy ("periodic" | "all_of" | "any_of"), with evidence
    trigger_policy: Dict[str, str] = field(default_factory=dict)
    trigger_evidence: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    #: Where the numbers came from (source, window, sample counts).
    provenance: Dict[str, Any] = field(default_factory=dict)


@dataclass
class FailureScenario:
    """Configuration for a failure simulation."""
    target_ids: Union[str, List[str]]                # Simultaneous initial targets
    description: str = ""
    failure_mode: FailureMode = FailureMode.CRASH
    cascade_rule: CascadeRule = CascadeRule.ALL
    cascade_probability: float = 1.0
    # Library cascades model a simultaneous, certain blast (all consumers fail at once) and are
    # intentionally kept separate from the general (Physical/Logical/Network) cascade_probability,
    # which is expected to be <1.0 whenever real multi-seed variance is required (see
    # FailureSimulator.simulate_monte_carlo). None => falls back to cascade_probability, preserving
    # prior behavior for any caller that does not set this explicitly.
    library_cascade_probability: Optional[float] = None
    max_cascade_depth: int = 10
    layer: str = "system"
    seed: Optional[int] = None

    @property
    def target_id(self) -> str:
        """Backward compatibility for single-target scenarios."""
        return self.target_ids[0] if self.target_ids else ""

    def __post_init__(self):
        if isinstance(self.target_ids, str):
            self.target_ids = [self.target_ids]


def ahp_impact_weights() -> Dict[str, float]:
    """The I(v) criterion weights, taken from the AHP processor rather than restated.

    They were previously hardcoded here as 0.35/0.25/0.25/0.15 — the same numbers
    the AHP matrix yields, but as a literal, so perturbing the matrix in
    ``weight_calculator.py`` moved nothing. Falls back to the documented values if
    the analysis package is unavailable, keeping this module import-light.
    """
    try:
        from saag.analysis.weight_calculator import AHPProcessor

        w = AHPProcessor().compute_weights()
        return {
            "reachability": w.i_reachability,
            "fragmentation": w.i_fragmentation,
            "throughput": w.i_throughput,
            "flow_disruption": w.i_flow_disruption,
        }
    except Exception:  # pragma: no cover - defensive, keeps simulation standalone
        return {
            "reachability": 0.35,
            "fragmentation": 0.25,
            "throughput": 0.25,
            "flow_disruption": 0.15,
        }


def reliability_alpha() -> float:
    """Weight of Fault Tolerance within Reliability (R = alpha*FT + (1-alpha)*A).

    Taken from ``QualityWeights.r_alpha`` rather than restated, same reasoning
    as ``ahp_impact_weights``: a literal here would go stale under weight
    perturbation. Falls back to the documented default if the analysis package
    is unavailable, keeping this module import-light.
    """
    try:
        from saag.analysis.weight_calculator import QualityWeights

        return QualityWeights().r_alpha
    except Exception:  # pragma: no cover - defensive, keeps simulation standalone
        return 0.36


@dataclass
class ImpactMetrics:
    """Impact metrics from a failure simulation.

    NOT the HGT/GAT paper's ground truth. ``composite_impact`` below and the IR(v)/IM(v)/IA(v)/
    IS(v) properties are AHP-weighted RM-dimension metrics produced by ``FailureSimulator``,
    used for the separate multi-dimensional quality-attribution framework (Q(v) validation).
    They are unrelated to, and must not be conflated with, the paper's I*(v) cascade-impact
    ground truth used to evaluate HGT/HGT-QoS/GAT/GAT-QoS/Topo/Topo-QoS: that I*(v) is produced
    solely by ``saag.simulation.fault_injector.FaultInjector`` (rate-weighted feed-loss fractions
    times topic QoS factors) and reported as ``FaultInjectionRecord.impact_score``. The two
    engines compute genuinely different quantities from genuinely different formulas; see
    docs/research/jss/draft.md Section 7.5 ("Ground Truth: Two Oracles") for the canonical
    I*(v) definition and the measured agreement between the two engines.
    """
    initial_paths: int = 0
    remaining_paths: int = 0
    reachability_loss: float = 0.0
    initial_components: int = 0
    failed_components: int = 0
    initial_connected_components: int = 1
    final_connected_components: int = 1
    fragmentation: float = 0.0
    initial_throughput: float = 1.0
    remaining_throughput: float = 1.0
    throughput_loss: float = 0.0
    affected_topics: int = 0
    affected_subscribers: int = 0
    affected_publishers: int = 0
    cascade_count: int = 0
    cascade_depth: int = 0
    flow_disruption: float = 0.0 # Fraction of event-sim flows broken
    cascade_by_type: Dict[str, int] = field(default_factory=dict)
    
    # composite_impact weights - AHP-derived in weight_calculator.py (not the paper's I*(v); see class docstring)
    impact_weights: Dict[str, float] = field(default_factory=lambda: ahp_impact_weights())

    # -----------------------------------------------------------------------
    # IR(v): Reliability-specific ground truth (fault propagation dynamics)
    # -----------------------------------------------------------------------
    # Populated by failure_simulator.simulate_exhaustive() in a post-pass.
    # cascade_reach = cascade_count / (|V| - 1)
    # weighted_cascade_impact = Σw(cascaded) / Σw(all)
    # normalized_cascade_depth = cascade_depth / max_observed_depth_in_run
    cascade_reach: float = 0.0
    weighted_cascade_impact: float = 0.0
    normalized_cascade_depth: float = 0.0

    fault_tolerance_weights: Dict[str, float] = field(default_factory=lambda: {
        "cascade_reach": 0.45,
        "weighted_cascade_impact": 0.35,
        "normalized_depth": 0.20,
    })

    # -----------------------------------------------------------------------
    # IM(v): Maintainability-specific ground truth (change propagation)
    # -----------------------------------------------------------------------
    # Populated by ChangePropagationSimulator in a post-pass after exhaustive
    # failure simulation. Based on BFS traversal of G^T (transposed DEPENDS_ON
    # graph) with loose-coupling and stable-interface stop conditions.
    # change_reach = |reached| / (|V| - 1)
    # weighted_change_impact = Σ max_path_weight(u) / Σ weight(all u)
    # normalized_change_depth = max_bfs_depth / global_max_change_depth
    change_reach: float = 0.0
    weighted_change_impact: float = 0.0
    normalized_change_depth: float = 0.0

    maintainability_weights: Dict[str, float] = field(default_factory=lambda: {
        "change_reach": 0.45,
        "weighted_change_impact": 0.35,
        "normalized_change_depth": 0.20,
    })
    
    # Optional override for Monte Carlo mean reporting
    _manual_composite_impact: Optional[float] = None

    @property
    def composite_impact(self) -> float:
        if self._manual_composite_impact is not None:
            return self._manual_composite_impact
            
        w = self.impact_weights
        return (
            w.get("reachability", 0.35) * self.reachability_loss +
            w.get("fragmentation", 0.25) * self.fragmentation +
            w.get("throughput", 0.25) * self.throughput_loss +
            w.get("flow_disruption", 0.15) * self.flow_disruption
        )

    @property
    def fault_tolerance_impact(self) -> float:
        """IFT(v) — Fault-Tolerance-specific ground truth from propagation dynamics.

        Measures cascade propagation directly rather than Availability-biased
        structural connectivity loss.  Only meaningful after the exhaustive
        simulation post-pass has populated cascade_reach, weighted_cascade_impact,
        and normalized_cascade_depth.  Defaults to 0.0 until then.
        """
        w = self.fault_tolerance_weights
        return (
            w.get("cascade_reach", 0.45) * self.cascade_reach +
            w.get("weighted_cascade_impact", 0.35) * self.weighted_cascade_impact +
            w.get("normalized_depth", 0.20) * self.normalized_cascade_depth
        )

    @property
    def reliability_impact(self) -> float:
        """IR(v) — Reliability ground truth: the r_alpha-blend of IFT(v) and IA(v).

        Mirrors the scoring-side hierarchy (R = r_alpha*FT + (1-r_alpha)*A).
        Blended at this raw scale as the training-label convenience form; the
        validation path re-blends post-scaling (see saag/validation/service.py)
        because IFT and IA have visibly different raw distributions and blending
        before vs. after `robust_sigmoid_scale_dict` is not interchangeable.
        """
        alpha = reliability_alpha()
        return alpha * self.fault_tolerance_impact + (1.0 - alpha) * self.availability_impact

    @property
    def maintainability_impact(self) -> float:
        """IM(v) — Maintainability-specific ground truth from change propagation.

        Measures how far a development-time change at v propagates through the
        dependency graph (G^T traversal with stop conditions). Only meaningful
        after ChangePropagationSimulator has populated change_reach,
        weighted_change_impact, and normalized_change_depth.  Defaults to 0.0.
        """
        w = self.maintainability_weights
        return (
            w.get("change_reach", 0.45) * self.change_reach +
            w.get("weighted_change_impact", 0.35) * self.weighted_change_impact +
            w.get("normalized_change_depth", 0.20) * self.normalized_change_depth
        )

    # -----------------------------------------------------------------------
    # IA(v): Availability-specific ground truth (connectivity disruption)
    # -----------------------------------------------------------------------
    # Populated by failure_simulator.simulate_exhaustive() in an IA(v) post-pass.
    # weighted_reachability_loss       = QoS-weighted fraction of broken pub-sub paths
    # weighted_fragmentation           = importance-weighted graph partition severity
    # path_breaking_throughput_loss    = throughput lost ONLY via PARTITION_LOSS events
    weighted_reachability_loss: float = 0.0
    weighted_fragmentation: float = 0.0
    path_breaking_throughput_loss: float = 0.0
    
    # Directed impact for Availability (DASA support)
    ia_out: float = 0.0
    ia_in: float = 0.0

    availability_weights: Dict[str, float] = field(default_factory=lambda: {
        "weighted_reachability": 0.50,
        "weighted_fragmentation": 0.35,
        "path_breaking_throughput": 0.15,
    })

    @property
    def availability_impact(self) -> float:
        """IA(v) — Availability-specific ground truth from connectivity disruption.

        Measures the structural connectivity loss caused by removing v,
        separated from cascade-propagation effects (which are measured by IR(v)).
        QoS-weighting ensures high-priority paths contribute more.
        Only meaningful after the IA(v) post-pass in simulate_exhaustive().
        Defaults to 0.0 until then.
        """
        w = self.availability_weights
        return (
            w.get("weighted_reachability", 0.50) * self.weighted_reachability_loss +
            w.get("weighted_fragmentation", 0.35) * self.weighted_fragmentation +
            w.get("path_breaking_throughput", 0.15) * self.path_breaking_throughput_loss
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "reachability": {
                "initial_paths": self.initial_paths,
                "remaining_paths": self.remaining_paths,
                "loss_percent": round(self.reachability_loss * 100, 2),
            },
            "fragmentation": {
                "fragmentation_percent": round(self.fragmentation * 100, 2),
            },
            "throughput": {
                "loss_percent": round(self.throughput_loss * 100, 2),
            },
            "flow_disruption": {
                "loss_percent": round(self.flow_disruption * 100, 2),
            },
            "cascade": {
                "count": self.cascade_count,
                "depth": self.cascade_depth,
            },
            "composite_impact": round(self.composite_impact, 4),
            # Reliability is hierarchical: reliability_impact is the r_alpha-blend
            # of fault_tolerance_impact and availability["availability_impact"];
            # both sub-characteristics are nested here so the hierarchy is
            # visible on disk, not just the blended composite.
            "reliability": {
                "reliability_impact": round(self.reliability_impact, 4),
                "fault_tolerance": {
                    "cascade_reach": round(self.cascade_reach, 4),
                    "weighted_cascade_impact": round(self.weighted_cascade_impact, 4),
                    "normalized_cascade_depth": round(self.normalized_cascade_depth, 4),
                    "fault_tolerance_impact": round(self.fault_tolerance_impact, 4),
                },
            },
            "maintainability": {
                "change_reach": round(self.change_reach, 4),
                "weighted_change_impact": round(self.weighted_change_impact, 4),
                "normalized_change_depth": round(self.normalized_change_depth, 4),
                "maintainability_impact": round(self.maintainability_impact, 4),
            },
            "availability": {
                "weighted_reachability_loss": round(self.weighted_reachability_loss, 4),
                "weighted_fragmentation": round(self.weighted_fragmentation, 4),
                "path_breaking_throughput_loss": round(self.path_breaking_throughput_loss, 4),
                "availability_impact": round(self.availability_impact, 4),
                "ia_out": round(self.ia_out, 4),
                "ia_in": round(self.ia_in, 4),
            },
        }



@dataclass
class CascadeEvent:
    """Record of a cascade propagation event."""
    component_id: str
    component_type: str
    cause: str
    depth: int


@dataclass
class FailureResult:
    """Result of a failure simulation."""
    target_id: str
    target_type: str
    scenario: str
    impact: ImpactMetrics
    cascaded_failures: List[str] = field(default_factory=list)
    cascade_sequence: List[CascadeEvent] = field(default_factory=list)
    layer_impacts: Dict[str, float] = field(default_factory=dict)
    related_components: List[str] = field(default_factory=list)
    csc_names: Dict[str, str] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "target_id": self.target_id,
            "target_type": self.target_type,
            "scenario": self.scenario,
            "impact": self.impact.to_dict(),
            "cascaded_failures": self.cascaded_failures,
            "cascade_sequence": [
                {"id": e.component_id, "type": e.component_type, "cause": e.cause, "depth": e.depth}
                for e in self.cascade_sequence
            ],
            "layer_impacts": {k: round(v, 4) for k, v in self.layer_impacts.items()},
        }

    def cascade_to_graph(self) -> Dict[str, Any]:
        """
        Convert the cascade sequence into a graph structure (nodes and edges)
        suitable for visualization.
        """
        nodes = []
        edges = []
        
        # Add root target node
        nodes.append({
            "id": self.target_id,
            "type": self.target_type,
            "name": self.csc_names.get(self.target_id, self.target_id),
            "depth": 0,
            "is_target": True
        })
        
        # Add cascaded nodes and edges
        for event in self.cascade_sequence:
            nodes.append({
                "id": event.component_id,
                "type": event.component_type,
                "name": self.csc_names.get(event.component_id, event.component_id),
                "depth": event.depth,
                "cause": event.cause,
                "is_target": False
            })
            
            # Find the cause in the existing nodes to create an edge
            # This is a simplification: we assume the cause is the parent ID
            edges.append({
                "source": event.cause if event.cause in [n["id"] for n in nodes] else self.target_id,
                "target": event.component_id,
                "type": "CASCADE"
            })
            
        return {"nodes": nodes, "edges": edges}


@dataclass
class MonteCarloResult:
    """Result of Monte Carlo stochastic simulation."""
    target_id: str
    n_trials: int
    mean_impact: float
    std_impact: float
    ci_95: Tuple[float, float]
    trial_impacts: List[float] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "target_id": self.target_id,
            "n_trials": self.n_trials,
            "mean_impact": round(self.mean_impact, 4),
            "std_impact": round(self.std_impact, 4),
        }


# =============================================================================
# Aggregated Results
# =============================================================================

@dataclass
class LayerMetrics:
    """Aggregated metrics for a single simulation layer."""
    layer: str
    event_throughput: int = 0
    event_delivered: int = 0
    event_dropped: int = 0
    event_delivery_rate: float = 0.0
    event_drop_rate: float = 0.0
    event_avg_latency_ms: float = 0.0
    event_p99_latency_ms: float = 0.0
    event_throughput_per_sec: float = 0.0
    event_drop_reasons: Dict[str, int] = field(default_factory=dict)
    avg_reachability_loss: float = 0.0
    avg_fragmentation: float = 0.0
    avg_throughput_loss: float = 0.0
    max_impact: float = 0.0
    max_impact_component: str = ""
    critical_count: int = 0
    high_count: int = 0
    medium_count: int = 0
    low_count: int = 0
    minimal_count: int = 0
    spof_count: int = 0
    total_components: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "layer": self.layer,
            "event_metrics": {
                "delivery_rate_percent": round(self.event_delivery_rate, 2),
                "avg_latency_ms": round(self.event_avg_latency_ms, 3),
            },
            "failure_metrics": {
                "avg_reachability_loss_percent": round(self.avg_reachability_loss * 100, 2),
                "max_impact": round(self.max_impact, 4),
            },
            "criticality": {
                "critical": self.critical_count,
                "high": self.high_count,
            },
        }


@dataclass
class ComponentCriticality:
    """Criticality assessment for a component."""
    id: str
    type: str
    event_impact: float = 0.0
    failure_impact: float = 0.0
    combined_impact: float = 0.0
    level: str = "minimal"
    cascade_count: int = 0
    cascade_depth: int = 0
    message_throughput: int = 0
    reachability_loss: float = 0.0
    throughput_loss: float = 0.0
    affected_topics: int = 0
    affected_subscribers: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "level": self.level,
            "combined_impact": round(self.combined_impact, 4),
        }


@dataclass
class EdgeCriticality:
    """Criticality assessment for an edge.

    Populated by ``FailureSimulator.simulate_edge_removal``, which actually
    severs the relationship and recomputes impact. ``evaluated=False`` means the
    edge was outside the candidate set and never measured — which is *not* the
    same as measuring it and finding no impact, and must not be read as a zero.
    """
    source: str
    target: str
    relationship: str
    flow_impact: float = 0.0
    connectivity_impact: float = 0.0
    combined_impact: float = 0.0
    level: str = "minimal"
    evaluated: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "relationship": self.relationship,
            "level": self.level,
            "flow_impact": round(self.flow_impact, 4),
            "connectivity_impact": round(self.connectivity_impact, 4),
            "combined_impact": round(self.combined_impact, 4),
            "evaluated": self.evaluated,
        }


@dataclass
class SimulationReport:
    """Comprehensive simulation report."""
    timestamp: str
    graph_summary: Dict[str, Any]
    layer_metrics: Dict[str, LayerMetrics] = field(default_factory=dict)
    component_criticality: List[ComponentCriticality] = field(default_factory=list)
    edge_criticality: List[EdgeCriticality] = field(default_factory=list)
    top_critical: List[Dict[str, Any]] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)
    csc_names: Dict[str, str] = field(default_factory=dict)
    library_usage: Dict[str, List[str]] = field(default_factory=dict)
    node_allocations: Dict[str, List[str]] = field(default_factory=dict)
    broker_routing: Dict[str, List[str]] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "layer_metrics": {k: v.to_dict() for k, v in self.layer_metrics.items()},
            "top_critical": self.top_critical,
            "component_criticality": [c.to_dict() for c in self.component_criticality]
        }
