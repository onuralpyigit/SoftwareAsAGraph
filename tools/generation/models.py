"""
Statistical Data Models for Graph Generation
"""
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional
from saag.core.models import QoSPolicy, CRITICALITY_LEVELS, canonical_criticality

SCALE_PRESETS: Dict[str, Dict[str, int]] = {
    "tiny":   {"apps": 5,   "topics": 5,   "brokers": 1,  "nodes": 2,  "libs": 2},
    "small":  {"apps": 15,  "topics": 10,  "brokers": 2,  "nodes": 4,  "libs": 5},
    "medium": {"apps": 50,  "topics": 30,  "brokers": 3,  "nodes": 8,  "libs": 10},
    "large":  {"apps": 150, "topics": 100, "brokers": 6,  "nodes": 20, "libs": 30},
    # "jumbo" matches scenario_07_enterprise_xlarge.yaml counts exactly, giving
    # --scale jumbo a named, reproducible approximation of that scenario.
    "jumbo":  {"apps": 300, "topics": 120, "brokers": 10, "nodes": 40, "libs": 50},
    "xlarge": {"apps": 500, "topics": 300, "brokers": 10, "nodes": 50, "libs": 100},
}

DURABILITY_OPTIONS = list(QoSPolicy.DURABILITY_SCORES.keys())
RELIABILITY_OPTIONS = list(QoSPolicy.RELIABILITY_SCORES.keys())
PRIORITY_OPTIONS = list(QoSPolicy.PRIORITY_SCORES.keys())

APP_TYPE_OPTIONS = ["sensor", "actuator", "controller", "monitor", "gateway", "processor"]
APP_HOTSTANDBY_OPTIONS = [False, True]
APP_USER_ROLE_OPTIONS = ["Operative", "Engineer", "Analyst", "Administrator", "Supervisor"]


@dataclass
class StatisticalMetric:
    """Represents statistical parameters for a metric."""
    count: int = 0
    mean: float = 0.0
    median: float = 0.0
    std: float = 0.0
    min: float = 0.0
    max: float = 0.0
    q1: float = 0.0
    q3: float = 0.0
    iqr: float = 0.0
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StatisticalMetric":
        return cls(
            count=int(data.get("count", 0)),
            mean=float(data.get("mean", 0)),
            median=float(data.get("median", 0)),
            std=float(data.get("std", 0)),
            min=float(data.get("min", 0)),
            max=float(data.get("max", 0)),
            q1=float(data.get("q1", 0)),
            q3=float(data.get("q3", 0)),
            iqr=float(data.get("iqr", 0)),
        )


@dataclass
class CategoricalDistribution:
    """Base class for categorical distribution statistics."""
    total_count: int = 0
    category_counts: Dict[str, int] = field(default_factory=dict)
    mode: str = ""
    mode_count: int = 0
    mode_percentage: float = 0.0
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CategoricalDistribution":
        return cls(
            total_count=data.get("total_count", 0),
            category_counts=data.get("category_counts", {}),
            mode=data.get("mode", ""),
            mode_count=data.get("mode_count", 0),
            mode_percentage=float(data.get("mode_percentage", 0)),
        )
    
    def to_weighted_list(self, default_options: Optional[List[str]] = None) -> List[str]:
        """Convert category_counts to weighted list for random sampling."""
        result = []
        for category, count in self.category_counts.items():
            result.extend([category] * count)
        return result if result else (default_options or [])


@dataclass
class AppCriticalityDistribution(CategoricalDistribution):
    """Distribution of application operational criticality (HIGH, MEDIUM, LOW)."""

    def to_weighted_list(self, default_options: Optional[List[str]] = None) -> List[str]:
        """Convert to weighted list with HIGH, MEDIUM, LOW criticality values."""
        result = []
        for category, count in self.category_counts.items():
            result.extend([canonical_criticality(category)] * count)
        return result if result else (default_options or list(CRITICALITY_LEVELS))


@dataclass
class AppHotstandbyDistribution(CategoricalDistribution):
    """Distribution of application hotstandby redundancy (True, False)."""

    def to_weighted_list(self, default_options: Optional[List[bool]] = None) -> List[bool]:
        """Convert to weighted list with boolean True / False values."""
        result = []
        for category, count in self.category_counts.items():
            cat_str = str(category).strip().lower()
            if cat_str in ("true", "1", "yes", "redundant", "hotstandby"):
                val = True
            elif cat_str in ("false", "0", "no", "non_redundant", "standalone"):
                val = False
            else:
                val = False
            result.extend([val] * count)
        return result if result else (default_options or [False, True])


@dataclass
class ApplicationStats:
    direct_publish_count: StatisticalMetric = field(default_factory=StatisticalMetric)
    direct_subscribe_count: StatisticalMetric = field(default_factory=StatisticalMetric)
    total_publish_count_including_libraries: StatisticalMetric = field(default_factory=StatisticalMetric)
    total_subscribe_count_including_libraries: StatisticalMetric = field(default_factory=StatisticalMetric)
    app_criticality_distribution: Optional[AppCriticalityDistribution] = None
    app_hotstandby_distribution: Optional[AppHotstandbyDistribution] = None
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ApplicationStats":
        return cls(
            direct_publish_count=StatisticalMetric.from_dict(data.get("direct_publish_count", {})),
            direct_subscribe_count=StatisticalMetric.from_dict(data.get("direct_subscribe_count", {})),
            total_publish_count_including_libraries=StatisticalMetric.from_dict(
                data.get("total_publish_count_including_libraries", {})
            ),
            total_subscribe_count_including_libraries=StatisticalMetric.from_dict(
                data.get("total_subscribe_count_including_libraries", {})
            ),
            app_criticality_distribution=AppCriticalityDistribution.from_dict(
                data.get("app_criticality_distribution", {})
            ) if "app_criticality_distribution" in data else None,
            app_hotstandby_distribution=AppHotstandbyDistribution.from_dict(
                data.get("app_hotstandby_distribution", {})
            ) if "app_hotstandby_distribution" in data else None,
        )


@dataclass
class LibraryStats:
    applications_using_this_library: StatisticalMetric = field(default_factory=StatisticalMetric)
    direct_publish_count: StatisticalMetric = field(default_factory=StatisticalMetric)
    direct_subscribe_count: StatisticalMetric = field(default_factory=StatisticalMetric)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LibraryStats":
        return cls(
            applications_using_this_library=StatisticalMetric.from_dict(
                data.get("applications_using_this_library", {})
            ),
            direct_publish_count=StatisticalMetric.from_dict(data.get("direct_publish_count", {})),
            direct_subscribe_count=StatisticalMetric.from_dict(data.get("direct_subscribe_count", {})),
        )


@dataclass
class TopicStats:
    topic_size_bytes: StatisticalMetric = field(default_factory=StatisticalMetric)
    applications_publishing_to_this_topic: StatisticalMetric = field(default_factory=StatisticalMetric)
    applications_subscribing_to_this_topic: StatisticalMetric = field(default_factory=StatisticalMetric)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TopicStats":
        return cls(
            topic_size_bytes=StatisticalMetric.from_dict(data.get("topic_size_bytes", {})),
            applications_publishing_to_this_topic=StatisticalMetric.from_dict(
                data.get("applications_publishing_to_this_topic", {})
            ),
            applications_subscribing_to_this_topic=StatisticalMetric.from_dict(
                data.get("applications_subscribing_to_this_topic", {})
            ),
        )


@dataclass
class QosStats:
    """QoS-related statistics.

    Each distribution is a plain ``CategoricalDistribution``; the
    domain-specific default option list (``DURABILITY_OPTIONS`` etc.) is
    supplied by the caller of ``to_weighted_list()`` rather than baked into a
    per-field subclass.
    """
    qos_durability_distribution: Optional[CategoricalDistribution] = None
    qos_reliability_distribution: Optional[CategoricalDistribution] = None
    qos_transport_priority_distribution: Optional[CategoricalDistribution] = None
    # No deadline_ms/history_depth distribution here on purpose: both are
    # derived per-topic from frequency and the QoS policy
    # (StatisticalGraphGenerator._derive_topic_deadline /
    # _derive_topic_history_depth), so a flat categorical pool would be parsed
    # and never read — the same dead-config shape that made qos_stats
    # unreachable before.

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "QosStats":
        return cls(
            qos_durability_distribution=CategoricalDistribution.from_dict(
                data["qos_durability_distribution"]
            ) if "qos_durability_distribution" in data else None,
            qos_reliability_distribution=CategoricalDistribution.from_dict(
                data["qos_reliability_distribution"]
            ) if "qos_reliability_distribution" in data else None,
            qos_transport_priority_distribution=CategoricalDistribution.from_dict(
                data["qos_transport_priority_distribution"]
            ) if "qos_transport_priority_distribution" in data else None,
        )


@dataclass
class NetworkConfig:
    """Switched physical network replacing the host-to-host CONNECTS_TO mesh.

    ``two_tier``: hosts are shuffled into racks of ``hosts_per_rack`` behind
    one top-of-rack switch each, and every switch uplinks to every one of the
    ``core_routers``. Capacities (Mbps) are optional; without them links are
    unbounded and only reachability is modelled.
    """
    topology: str = "two_tier"
    hosts_per_rack: int = 4
    core_routers: int = 2
    host_link_mbps: Optional[float] = None
    uplink_mbps: Optional[float] = None

    TOPOLOGIES = ("two_tier",)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "NetworkConfig":
        cfg = cls(**{k: data[k] for k in (
            "topology", "hosts_per_rack", "core_routers", "host_link_mbps", "uplink_mbps",
        ) if k in data})
        if cfg.topology not in cls.TOPOLOGIES:
            raise ValueError(f"network.topology must be one of {cls.TOPOLOGIES}, got {cfg.topology!r}")
        if cfg.hosts_per_rack < 1 or cfg.core_routers < 1:
            raise ValueError("network.hosts_per_rack and network.core_routers must be >= 1")
        return cfg

    def to_dict(self) -> Dict[str, Any]:
        return {
            "topology": self.topology,
            "hosts_per_rack": self.hosts_per_rack,
            "core_routers": self.core_routers,
            "host_link_mbps": self.host_link_mbps,
            "uplink_mbps": self.uplink_mbps,
        }


@dataclass
class GraphConfig:
    """Configuration for graph generation with statistical distributions."""
    nodes: int = 8
    apps: int = 50
    topics: int = 30
    brokers: int = 3
    libs: int = 10
    seed: int = 42
    
    application_stats: Optional[ApplicationStats] = None
    library_stats: Optional[LibraryStats] = None
    topic_stats: Optional[TopicStats] = None
    qos_stats: Optional[QosStats] = None
    use_statistics: bool = False
    
    # Realistic generation fields
    domain: Optional[str] = None
    scenario: Optional[str] = None
    intra_cluster_coupling: float = 0.65
    connection_density: float = 0.3
    # When False, QoS stops steering topic selection (_APP_TYPE_QOS_AFFINITY) and
    # criticality/hot-standby assignment, so topology is sampled independently of
    # the declared QoS. Only the Amendment 7 control corpus sets it; the committed
    # corpus is generated with the default and stays byte-identical.
    qos_affinity: bool = True
    # Optional switched network (YAML ``network:`` block). None keeps the
    # host-to-host mesh, so the committed corpus stays byte-identical.
    network: Optional[NetworkConfig] = None
    
    @classmethod
    def from_scale(cls, scale: str, seed: int = 42) -> "GraphConfig":
        preset = SCALE_PRESETS.get(scale, SCALE_PRESETS["medium"])
        return cls(
            apps=preset["apps"],
            topics=preset["topics"],
            brokers=preset["brokers"],
            nodes=preset["nodes"],
            libs=preset["libs"],
            seed=seed,
            use_statistics=False,
            domain=None,
            scenario=None,
            intra_cluster_coupling=0.65,
            connection_density=0.3,
        )
    
    @classmethod
    def from_yaml(cls, data: Dict[str, Any]) -> "GraphConfig":
        graph_data = data.get("graph", data)
        counts = graph_data.get("counts", {})
        # "node_stats" is accepted (and ignored) for backward compatibility with
        # existing scenario YAMLs: the generator never reads it, but its
        # presence must still flip use_statistics / metadata.generation_mode
        # the same way it always has.
        stats_keys = ["node_stats", "application_stats", "library_stats", "topic_stats", "qos_stats"]
        has_stats = any(key in graph_data for key in stats_keys)

        return cls(
            apps=counts.get("applications", graph_data.get("apps", 50)),
            topics=counts.get("topics", graph_data.get("topics", 30)),
            brokers=counts.get("brokers", graph_data.get("brokers", 3)),
            nodes=counts.get("nodes", graph_data.get("nodes", 8)),
            libs=counts.get("libraries", graph_data.get("libs", 10)),
            seed=graph_data.get("seed", 42),
            application_stats=ApplicationStats.from_dict(graph_data["application_stats"]) if "application_stats" in graph_data else None,
            library_stats=LibraryStats.from_dict(graph_data["library_stats"]) if "library_stats" in graph_data else None,
            topic_stats=TopicStats.from_dict(graph_data["topic_stats"]) if "topic_stats" in graph_data else None,
            qos_stats=QosStats.from_dict(graph_data["qos_stats"]) if "qos_stats" in graph_data else None,
            use_statistics=has_stats,
            domain=graph_data.get("domain"),
            scenario=graph_data.get("scenario"),
            intra_cluster_coupling=graph_data.get("intra_cluster_coupling", 0.65),
            connection_density=graph_data.get("connection_density", 0.3),
            network=NetworkConfig.from_dict(graph_data["network"]) if graph_data.get("network") else None,
        )
    
    def to_scale_dict(self) -> Dict[str, int]:
        """Convert to scale config dict (excludes seed)."""
        return {
            "apps": self.apps,
            "topics": self.topics,
            "brokers": self.brokers,
            "nodes": self.nodes,
            "libs": self.libs,
        }
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert full config to dict."""
        base = self.to_scale_dict()
        base.update({
            "seed": self.seed,
            "use_statistics": self.use_statistics,
            "domain": self.domain,
            "scenario": self.scenario,
            "intra_cluster_coupling": self.intra_cluster_coupling,
            "connection_density": self.connection_density,
        })
        if self.network is not None:
            base["network"] = self.network.to_dict()
        return base
