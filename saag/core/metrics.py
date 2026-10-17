"""
Graph Metrics Domain Models

Data structures for storing and manipulating graph analysis metrics.
Provides clean separation between raw structural metrics and quality scores.
"""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Any, Optional, Tuple

from .criticality import CriticalityLevel


# =============================================================================
# Structural Metrics (Raw Graph Metrics)
# =============================================================================

@dataclass
class StructuralMetrics:
    """
    Raw topological metrics for a single graph component (node).
    
    Metrics are organized by category:
    - Centrality: Importance measures (PageRank, Betweenness, etc.)
    - Degree: Connectivity measures (in/out degree)
    - Resilience: Redundancy and fault tolerance indicators
    - Weights: Component and dependency weights
    """
    id: str
    name: str  # Human-readable name
    type: str  # Application, Node, Broker, Topic
    
    # === Centrality Metrics ===
    pagerank: float = 0.0           # Global importance in dependency graph
    reverse_pagerank: float = 0.0   # Influence on failure propagation
    betweenness: float = 0.0        # Bridge/bottleneck centrality
    closeness: float = 0.0          # Average distance to all nodes
    eigenvector: float = 0.0        # Influence via neighbor importance
    
    # === Degree Metrics ===
    degree: float = 0.0             # Total degree centrality (normalized)
    in_degree: float = 0.0          # Normalized in-degree (who depends on this)
    out_degree: float = 0.0         # Normalized out-degree (this depends on)
    in_degree_raw: int = 0          # Raw in-degree count
    out_degree_raw: int = 0         # Raw out-degree count
    
    # === Resilience Metrics ===
    clustering_coefficient: float = 0.0  # Local redundancy (higher = more alternatives)
    is_articulation_point: bool = False  # Removal disconnects graph (SPOF)
    is_directed_ap: bool = False         # Removal disconnects reachable set from sources
    is_isolated: bool = False            # No connections
    bridge_count: int = 0                # Number of bridges touching this node
    bridge_ratio: float = 0.0            # Fraction of edges that are bridges
    ap_c_directed: float = 0.0           # Directed SPOF severity
    cdi: float = 0.0                     # Connectivity Degradation Index
    blast_radius: int = 0                # Number of nodes that become unreachable if this node is removed
    cascade_depth: int = 0               # Longest failure propagation path from this node
    publisher_spof: float = 0.0          # Sole-publisher risk: qos_weight * min(sub_count/5, 1) if sole pub
    
    # === Pub-Sub Topology Metrics ===
    pubsub_degree: float = 0.0           # Topics this app publishes to OR subscribes to
    pubsub_betweenness: float = 0.0      # Betweenness in the pub-sub bipartite graph
    broker_exposure: float = 0.0         # Distinct brokers routing touched topics
    fan_out_criticality: float = 0.0     # Topic fan-out (Topic nodes only): log1p(Hz)·sub_count / max
    topic_frequency_hz: float = 0.0      # Raw message rate in Hz (Topic nodes only; 0.0 for all other types)
    mpci: float = 0.0                    # Multi-path coupling intensity
    path_complexity: float = 0.0         # Efferent path count complexity: mean(log2(1+path_count))
    coupling_risk_enh: float = 0.0       # CouplingRisk_enh(v): Martin instability enriched by path_complexity
                                          # (mirrors the value computed in analyzer.py's _compute_rm; 0.0 for
                                          # non-Application/Library types where instability isn't meaningful)
    topic_subscriber_count: int = 0      # Raw SUBSCRIBES_TO count (Topic nodes only; 0 for all other types)
    topic_publisher_count: int = 0       # Raw PUBLISHES_TO count (Topic nodes only; 0 for all other types)

    # === Infrastructure Metrics (Node and Broker nodes only) ===
    ip_address: str = ""
    cpu_cores: int = 0
    memory_gb: int = 0
    os_type: str = ""
    broker_type: str = ""      # renamed from 'type' to avoid collision with 'type' field
    max_connections: int = 0
    host: str = ""

    # === Network Load (Node and NetworkDevice; 0.0 unless the topology models its network) ===
    max_link_utilization: float = 0.0  # Worst offered-load / capacity_mbps over the vertex's CONNECTS_TO links
    link_load_mbps: float = 0.0        # Baseline pub-sub traffic on the vertex's CONNECTS_TO links, Mbps

    # === Code Quality Metrics (Application and Library nodes only; 0.0 for all other types) ===
    # Populated from optional JSON fields (loc, cyclomatic_complexity, coupling_*, lcom).
    # Initialized with raw values during assembly, then min-max normalized by StructuralAnalyzer.
    # When fields are absent or zero, these remain 0.0 and contribute nothing to M(v).
    loc_norm: float = 0.0              # Normalised Lines-of-Code (population min-max) ∈ [0,1]
    complexity_norm: float = 0.0       # Normalised cyclomatic complexity ∈ [0,1]
    instability_code: float = 0.0      # Martin instability Ce/(Ca+Ce) ∈ [0,1] (raw ratio)
    lcom_norm: float = 0.0             # Normalised Lack of Cohesion of Methods ∈ [0,1]
    code_quality_penalty: float = 0.0  # CQP = 0.10·LOC + 0.35·Complexity + 0.30·Instability + 0.25·LCOM
    
    # SonarQube / Quality Metrics
    sqale_debt_ratio: float = 0.0
    bugs: int = 0
    vulnerabilities: int = 0
    duplicated_lines_density: float = 0.0

    # === Weights ===
    weight: float = 1.0                  # Intrinsic component weight
    dependency_weight_in: float = 0.0    # Sum of incoming dependency weights
    dependency_weight_out: float = 0.0   # Sum of outgoing dependency weights
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    @property
    def total_degree_raw(self) -> int:
        """Raw total degree (in + out)."""
        return self.in_degree_raw + self.out_degree_raw
    
    @property
    def is_hub(self) -> bool:
        """Check if this is a hub (high in-degree)."""
        return self.in_degree > 0.5 or self.in_degree_raw > 10
    
    @property
    def is_sink(self) -> bool:
        """Check if this is a sink (no outgoing edges)."""
        return self.out_degree_raw == 0 and self.in_degree_raw > 0
    
    @property
    def is_source(self) -> bool:
        """Check if this is a source (no incoming edges)."""
        return self.in_degree_raw == 0 and self.out_degree_raw > 0
    @property
    def vis_shape(self) -> str:
        """Map vertex type to shape token for visualization rendering."""
        mapping = {
            "Application": "ellipse",
            "Library": "diamond",
            "Broker": "hexagon",
            "Node": "box",
            "NetworkDevice": "triangle",
            "Topic": "star",
        }
        return mapping.get(self.type, "ellipse")


@dataclass
class EdgeMetrics:
    """
    Raw topological metrics for a single edge (dependency).
    
    Tracks edge-level importance and criticality indicators.
    """
    source: str
    target: str
    source_type: str
    target_type: str
    dependency_type: str  # app_to_app, node_to_node, app_to_broker, node_to_broker
    
    # === Metrics ===
    betweenness: float = 0.0  # Edge betweenness centrality
    is_bridge: bool = False   # Removal disconnects graph
    weight: float = 1.0       # Edge weight
    
    @property
    def key(self) -> Tuple[str, str]:
        """Unique identifier tuple for the edge."""
        return (self.source, self.target)
    
    @property
    def id(self) -> str:
        """String identifier for the edge."""
        return f"{self.source}->{self.target}"
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class GraphSummary:
    """
    Summary statistics for an analyzed graph layer.
    
    Provides high-level insights about graph structure and health.
    """
    layer: str
    nodes: int = 0
    edges: int = 0
    density: float = 0.0
    avg_degree: float = 0.0
    avg_clustering: float = 0.0
    is_connected: bool = False
    num_components: int = 0
    num_articulation_points: int = 0
    num_bridges: int = 0
    diameter: Optional[int] = None
    avg_path_length: Optional[float] = None
    assortativity: float = 0.0       # Degree assortativity coefficient (Pearson r)
    
    # Breakdown by type
    node_types: Dict[str, int] = field(default_factory=dict)
    edge_types: Dict[str, int] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    @property
    def spof_ratio(self) -> float:
        """Ratio of single points of failure (articulation points) to total nodes."""
        return self.num_articulation_points / self.nodes if self.nodes > 0 else 0.0
    
    @property
    def bridge_ratio(self) -> float:
        """Ratio of bridges to total edges."""
        return self.num_bridges / self.edges if self.edges > 0 else 0.0
    
    @property
    def connectivity_health(self) -> str:
        """Simple connectivity health indicator."""
        if self.num_components <= 1 and self.spof_ratio < 0.1:
            return "HEALTHY"
        elif self.num_components <= 2 and self.spof_ratio < 0.2:
            return "MODERATE"
        else:
            return "AT_RISK"
    
    @property
    def assortativity_pattern(self) -> str:
        """Interpret degree assortativity as a mixing pattern.
        
        Positive: assortative (hubs connect to hubs) — resilient to random failure
        Negative: disassortative (hubs connect to leaves) — vulnerable to targeted attack
        Near zero: neutral mixing
        """
        if self.assortativity > 0.1:
            return "ASSORTATIVE"
        elif self.assortativity < -0.1:
            return "DISASSORTATIVE"
        else:
            return "NEUTRAL"


# =============================================================================
# Quality Scores (Computed from Structural Metrics)
# =============================================================================

@dataclass
class QualityScores:
    """
    Composite quality scores for the RM model (ISO/IEC 25010:2023).

    ``fault_tolerance`` and ``availability`` are Reliability's two
    sub-characteristics, reported alongside the hierarchical composite
    ``reliability`` rather than folded away — see saag/core/quality_model.py.

    Formulas:
        FT(v) = w_rpr·RPR + w_dg·DG_in + w_cd·CDPot    (Fault Tolerance)
        A(v)  = w_ap·AP + w_br·BR + w_imp·Imp          (Availability)
        R(v)  = r_alpha·FT(v) + (1-r_alpha)·A(v)       (Reliability, hierarchical)
        M(v)  = w_bt·BC + w_dg·Deg + w_cl·(1-CC)       (Maintainability)
        Q(v)  = w_r·R + w_m·M                          (Overall)
    """
    reliability: float = 0.0        # Fault propagation + availability risk (hierarchical)
    maintainability: float = 0.0    # Change/coupling complexity
    fault_tolerance: float = 0.0    # Reliability sub-characteristic: propagation-reach signals
    availability: float = 0.0       # Reliability sub-characteristic: single point of failure risk
    overall: float = 0.0            # Combined criticality

    def to_dict(self) -> Dict[str, float]:
        return {
            "reliability": round(self.reliability, 4),
            "maintainability": round(self.maintainability, 4),
            "fault_tolerance": round(self.fault_tolerance, 4),
            "availability": round(self.availability, 4),
            "overall": round(self.overall, 4),
        }


@dataclass
class QualityLevels:
    """
    Classified levels for each quality dimension.

    Levels are determined by box-plot classification based on
    score distribution, not static thresholds.
    """
    reliability: CriticalityLevel = CriticalityLevel.MINIMAL
    maintainability: CriticalityLevel = CriticalityLevel.MINIMAL
    fault_tolerance: CriticalityLevel = CriticalityLevel.MINIMAL
    availability: CriticalityLevel = CriticalityLevel.MINIMAL
    overall: CriticalityLevel = CriticalityLevel.MINIMAL

    def to_dict(self) -> Dict[str, str]:
        return {
            "reliability": self.reliability.value,
            "maintainability": self.maintainability.value,
            "fault_tolerance": self.fault_tolerance.value,
            "availability": self.availability.value,
            "overall": self.overall.value,
        }

    def max_level(self) -> CriticalityLevel:
        """Return the highest criticality level across the two top-level dimensions.

        Sub-characteristics (fault_tolerance, availability) are reported but
        excluded here — they are components of ``reliability``, not independent
        dimensions, so including them would double-count Reliability's signal.
        """
        return max(
            [self.reliability, self.maintainability],
            key=lambda x: x.numeric
        )
    
    def requires_attention(self) -> bool:
        """Check if any dimension requires attention (CRITICAL or HIGH)."""
        return self.max_level() >= CriticalityLevel.HIGH


@dataclass
class ComponentQuality:
    """
    Complete quality assessment for a single component.

    Combines raw structural metrics with computed quality scores,
    classification levels, and a CriticalityProfile (5-tuple of binary
    criticality flags per RM dimension + composite).
    """
    id: str
    type: str
    scores: QualityScores
    levels: QualityLevels
    structural: StructuralMetrics
    profile: Optional[Any] = None  # CriticalityProfile; Any avoids circular import

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "id": self.id,
            "type": self.type,
            "scores": self.scores.to_dict(),
            "levels": self.levels.to_dict(),
            "structural": self.structural.to_dict(),
        }
        if self.profile is not None and hasattr(self.profile, "to_dict"):
            result["profile"] = self.profile.to_dict()
        return result

    @property
    def is_critical(self) -> bool:
        """Check if component is classified as CRITICAL overall."""
        return self.levels.overall == CriticalityLevel.CRITICAL

    @property
    def requires_attention(self) -> bool:
        """Check if component requires attention (CRITICAL or HIGH)."""
        return self.levels.overall >= CriticalityLevel.HIGH


@dataclass
class EdgeQuality:
    """Quality assessment for a single edge (dependency)."""
    source: str
    target: str
    source_type: str
    target_type: str
    dependency_type: str
    scores: QualityScores
    level: CriticalityLevel = CriticalityLevel.MINIMAL
    structural: Optional[EdgeMetrics] = None
    
    @property
    def id(self) -> str:
        return f"{self.source}->{self.target}"
    
    @property
    def key(self) -> Tuple[str, str]:
        return (self.source, self.target)
    
    def to_dict(self) -> Dict[str, Any]:
        result = {
            "id": self.id,
            "source": self.source,
            "target": self.target,
            "source_type": self.source_type,
            "target_type": self.target_type,
            "dependency_type": self.dependency_type,
            "scores": self.scores.to_dict(),
            "level": self.level.value,
        }
        if self.structural:
            result["structural"] = self.structural.to_dict()
        return result


@dataclass
class ClassificationSummary:
    """Summary of classification distribution."""
    total_components: int = 0
    total_edges: int = 0
    component_distribution: Dict[str, int] = field(default_factory=dict)
    edge_distribution: Dict[str, int] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_components": self.total_components,
            "total_edges": self.total_edges,
            "component_distribution": self.component_distribution,
            "edge_distribution": self.edge_distribution,
        }
    
    @property
    def critical_components(self) -> int:
        return self.component_distribution.get("critical", 0)
    
    @property
    def high_components(self) -> int:
        return self.component_distribution.get("high", 0)
    
    @property
    def requires_attention(self) -> int:
        """Components requiring attention (CRITICAL + HIGH)."""
        return self.critical_components + self.high_components
