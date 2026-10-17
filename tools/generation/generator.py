"""
Statistical Graph Generator
"""
import math
import random
import logging
from collections import Counter
from typing import Dict, Any, List, Optional, Tuple

from saag.core.models import (
    Application,
    Broker,
    Node,
    Topic,
    Library,
    QoSPolicy,
    CRITICALITY_THRESHOLDS,
    CRITICALITY_LEVELS,
    TOPIC_CRITICALITY_ORD,
    MAX_TOPIC_CRITICALITY_ORD,
    canonical_criticality,
)
from .models import (
    GraphConfig,
    NetworkConfig,
    StatisticalMetric,
    DURABILITY_OPTIONS,
    RELIABILITY_OPTIONS,
    PRIORITY_OPTIONS,
    APP_TYPE_OPTIONS,
    APP_USER_ROLE_OPTIONS,
)
from .datasets import (
    DomainDataset,
    get_app_type_for_name,
    get_lib_archetype_for_name,
    get_generic_system_hierarchy,
    SYSTEM_HIERARCHY_POOLS,
    GENERIC_HIERARCHY_POOL,
)


# --- Code-metrics generation parameters by app_type ---
# Each entry maps to ranges for the full code_metrics structure.
# Format: {loc_lo, loc_hi, classes_per_kloc, methods_per_class, fields_per_class,
#          avg_wmc_lo, avg_wmc_hi, avg_lcom_lo, avg_lcom_hi,
#          avg_cbo_lo, avg_cbo_hi, avg_rfc_lo, avg_rfc_hi,
#          avg_fanin_lo, avg_fanin_hi, avg_fanout_lo, avg_fanout_hi}
_CODE_METRICS_PARAMS: Dict[str, Dict[str, Any]] = {
    "sensor":     {"loc": (100,  500),  "cls_per_kloc": (8, 15), "meth_per_cls": (4, 8),  "fld_per_cls": (2, 4),
                   "avg_wmc": (4, 10),   "avg_lcom": (5, 25),
                   "avg_cbo": (2, 6),    "avg_rfc": (8, 20),
                   "avg_fanin": (1, 4),  "avg_fanout": (2, 5)},
    "actuator":   {"loc": (100,  400),  "cls_per_kloc": (8, 14), "meth_per_cls": (4, 7),  "fld_per_cls": (2, 4),
                   "avg_wmc": (4, 9),    "avg_lcom": (5, 20),
                   "avg_cbo": (2, 5),    "avg_rfc": (8, 18),
                   "avg_fanin": (1, 3),  "avg_fanout": (2, 5)},
    "monitor":    {"loc": (300, 1000),  "cls_per_kloc": (10, 18), "meth_per_cls": (5, 9),  "fld_per_cls": (2, 5),
                   "avg_wmc": (8, 16),   "avg_lcom": (10, 40),
                   "avg_cbo": (4, 10),   "avg_rfc": (14, 32),
                   "avg_fanin": (1, 5),  "avg_fanout": (3, 8)},
    "controller": {"loc": (400, 2000),  "cls_per_kloc": (9, 16),  "meth_per_cls": (5, 10), "fld_per_cls": (2, 5),
                   "avg_wmc": (10, 20),  "avg_lcom": (12, 50),
                   "avg_cbo": (5, 14),   "avg_rfc": (16, 40),
                   "avg_fanin": (1, 6),  "avg_fanout": (3, 10)},
    "gateway":    {"loc": (800, 3000),  "cls_per_kloc": (8, 14),  "meth_per_cls": (6, 12), "fld_per_cls": (3, 6),
                   "avg_wmc": (12, 28),  "avg_lcom": (20, 65),
                   "avg_cbo": (6, 16),   "avg_rfc": (20, 50),
                   "avg_fanin": (2, 8),  "avg_fanout": (4, 14)},
    "processor":  {"loc": (600, 2500),  "cls_per_kloc": (8, 15),  "meth_per_cls": (5, 11), "fld_per_cls": (2, 5),
                   "avg_wmc": (10, 25),  "avg_lcom": (15, 55),
                   "avg_cbo": (5, 14),   "avg_rfc": (18, 45),
                   "avg_fanin": (1, 7),  "avg_fanout": (3, 12)},
}
# Fallback used when app_type is not in _CODE_METRICS_PARAMS (should not happen
# in normal generation since APP_TYPE_OPTIONS is exhaustive, but guards against
# future additions to APP_TYPE_OPTIONS that are not yet reflected here).
_DEFAULT_CODE_METRICS_PARAMS: Dict[str, Any] = {
    "loc": (400, 2000),  "cls_per_kloc": (9, 16),  "meth_per_cls": (5, 10), "fld_per_cls": (2, 5),
    "avg_wmc": (8, 22),  "avg_lcom": (10, 50),
    "avg_cbo": (4, 12),  "avg_rfc": (14, 38),
    "avg_fanin": (1, 6), "avg_fanout": (3, 10),
}

# --- Code-metrics generation parameters by library archetype ---
_LIB_CODE_METRICS_PARAMS: Dict[str, Dict[str, Any]] = {
    "utility":    {"loc": (50,   500),  "cls_per_kloc": (10, 20), "meth_per_cls": (3, 7),  "fld_per_cls": (1, 3),
                   "avg_wmc": (3, 8),    "avg_lcom": (3, 15),
                   "avg_cbo": (1, 4),    "avg_rfc": (5, 14),
                   "avg_fanin": (2, 8),  "avg_fanout": (1, 4)},
    "framework":  {"loc": (500, 6000),  "cls_per_kloc": (6, 12),  "meth_per_cls": (6, 14), "fld_per_cls": (3, 7),
                   "avg_wmc": (10, 30),  "avg_lcom": (15, 60),
                   "avg_cbo": (5, 16),   "avg_rfc": (16, 50),
                   "avg_fanin": (3, 12), "avg_fanout": (2, 8)},
    "driver":     {"loc": (100,  900),  "cls_per_kloc": (8, 16),  "meth_per_cls": (4, 8),  "fld_per_cls": (2, 5),
                   "avg_wmc": (5, 12),   "avg_lcom": (5, 25),
                   "avg_cbo": (2, 7),    "avg_rfc": (8, 22),
                   "avg_fanin": (2, 6),  "avg_fanout": (1, 5)},
    "middleware": {"loc": (200, 2500),  "cls_per_kloc": (7, 14),  "meth_per_cls": (5, 10), "fld_per_cls": (2, 5),
                   "avg_wmc": (7, 20),   "avg_lcom": (10, 45),
                   "avg_cbo": (4, 12),   "avg_rfc": (12, 35),
                   "avg_fanin": (2, 8),  "avg_fanout": (2, 6)},
    "protocol":   {"loc": (150, 1200),  "cls_per_kloc": (8, 16),  "meth_per_cls": (4, 9),  "fld_per_cls": (2, 4),
                   "avg_wmc": (5, 14),   "avg_lcom": (5, 30),
                   "avg_cbo": (3, 8),    "avg_rfc": (10, 28),
                   "avg_fanin": (2, 7),  "avg_fanout": (1, 5)},
}
# Weighted random selection of lib archetype (utility and driver are most common)
_LIB_ARCHETYPE_WEIGHTS = ["utility"] * 4 + ["framework"] * 2 + ["driver"] * 3 + ["middleware"] * 2 + ["protocol"] * 2

# Maps app_type → preferred QoS attributes for topic selection bias.
# Used by _qos_preferred_topics() to steer which topics are drawn into the
# "preferred" tier of _sample_biased(), making QoS semantics structurally
# coherent: gateways/controllers prefer RELIABLE/HIGH topics; sensors stay on
# BEST_EFFORT/LOW topics.  Falls back to the cluster pool when the preferred
# set is empty (e.g. all topics in the cluster share the same QoS level).
_APP_TYPE_QOS_AFFINITY: Dict[str, Dict[str, List[str]]] = {
    "gateway":    {"reliability": ["RELIABLE"],                "priority": ["HIGH", "HIGHEST", "CRITICAL"]},
    "controller": {"reliability": ["RELIABLE"],                "priority": ["HIGH", "HIGHEST", "CRITICAL"]},
    "processor":  {"reliability": ["RELIABLE", "BEST_EFFORT"], "priority": ["MEDIUM", "HIGH"]},
    "monitor":    {"reliability": ["RELIABLE", "BEST_EFFORT"], "priority": ["LOW", "MEDIUM"]},
    "actuator":   {"reliability": ["RELIABLE"],                "priority": ["MEDIUM", "HIGH"]},
    "sensor":     {"reliability": ["BEST_EFFORT"],             "priority": ["LOW", "MEDIUM"]},
}

# Maps app_type → (can_publish, can_subscribe) messaging capability.
# Derived from domain semantics:
#   sensor    — originates data, never consumes
#   actuator  — receives commands, never produces
#   monitor   — observes data streams, never produces
#   controller — issues commands and reads state feedback
#   gateway   — bridges data flows in both directions
#   processor — transforms and re-emits data
#   service   — general-purpose; bidirectional by default
_APP_TYPE_MESSAGING_CAPABILITY: Dict[str, tuple] = {
    "sensor":     (True,  False),   # publish only
    "actuator":   (False, True),    # subscribe only
    "monitor":    (False, True),    # subscribe only
    "controller": (True,  True),    # both
    "gateway":    (True,  True),    # both
    "processor":  (True,  True),    # both
    "service":    (True,  True),    # both
}

def _can_publish(app_type: str) -> bool:
    """Return True if an app of this type is allowed to publish to topics."""
    return _APP_TYPE_MESSAGING_CAPABILITY.get(app_type, (True, True))[0]

def _can_subscribe(app_type: str) -> bool:
    """Return True if an app of this type is allowed to subscribe to topics."""
    return _APP_TYPE_MESSAGING_CAPABILITY.get(app_type, (True, True))[1]

# ---------------------------------------------------------------------------
# Per-domain frequency bounds (Hz) for log-uniform sampling.
# Each entry is (lo_hz, hi_hz) for ``random.uniform(log10(lo), log10(hi))``.
# References:
#   ATM (ICAO SWIM/DDS):  radar/ADS-B updates 1–100 Hz, control msgs up to 200 Hz
#   AV (ROS2 pubsub):     sensor streams 10–100 Hz, HD-map infrequent ≈ 0.1 Hz
#   HFT (market data):    tick feeds 100–10 000 Hz
#   IoT smart-city:       environmental sensors 0.01–5 Hz
#   Healthcare/PACS:      clinical events 0.001–2 Hz
#   Financial/enterprise: order/CRM events 0.001–10 Hz
#   Hub-and-spoke/micro:  service calls 0.01–50 Hz
# Domains not listed fall back to the generic range (0.1–100 Hz).
#
# Keys must match the domain values actually passed via --domain / scenario
# YAML `domain:` (the same strings used as keys in datasets.py's
# DOMAIN_DATASETS / SYSTEM_HIERARCHY_POOLS), not descriptive names. Prior to
# this fix, "autonomous_vehicle" / "iot_smart_city" / "financial_trading" /
# "hub_and_spoke" never matched the real values ("av" / "iot" / "finance" /
# "hub-and-spoke"), so those four domains silently fell back to
# _DOMAIN_FREQ_BOUNDS_DEFAULT. See docs/graph-generation.md §11.
# ---------------------------------------------------------------------------
_DOMAIN_FREQ_BOUNDS: Dict[str, tuple] = {
    "av":                     (0.1,   100.0),
    "iot":                    (0.01,  5.0),
    "finance":                (100.0, 10000.0),
    "healthcare":             (0.001, 2.0),
    "enterprise":             (0.001, 10.0),
    "hub-and-spoke":          (0.01,  50.0),
    "microservices":          (0.01,  50.0),
    "atm":                    (1.0,   200.0),
    "air_traffic_management": (1.0,   200.0),
}
_DOMAIN_FREQ_BOUNDS_DEFAULT: tuple = (0.1, 100.0)

# Label-noise rate for Topic.criticality ground-truth injection.
# ~17 % of QoS-rule-derived labels are flipped uniformly so the GNN cannot
# trivially recover criticality from QoS features alone.
_CRITICALITY_NOISE_RATE: float = 0.17

# Ordered criticality levels (used to pick a random *different* label on flip).
_CRITICALITY_LABELS = list(CRITICALITY_LEVELS)

# --- Temporal contract sampling (deadline_ms / history_depth) ----------------
# Deadline slack is expressed as a multiple of the message period T = 1000/f.
# All three criticality tiers draw from the SAME support and differ only in the
# mode of a triangular distribution, so the bands overlap and the label cannot
# be inverted from (deadline_ms, frequency).  See _derive_topic_deadline().
_DEADLINE_FACTOR_RANGE: tuple = (1.0, 6.0)
_DEADLINE_FACTOR_MODE: Dict[str, float] = {
    "HIGH": 1.4,
    "MEDIUM": 2.4,
    "LOW": 4.0,
}

# Probability that a topic declares no deadline at all.  Nonzero for every tier:
# best-effort channels exist at all urgencies, and an all-or-nothing rule made
# "deadline is null" a perfect LOW detector.
_NO_DEADLINE_RATE: Dict[str, float] = {
    "HIGH": 0.05,
    "MEDIUM": 0.20,
    "LOW": 0.45,
}

# Shared KEEP_LAST depth ladder.  Criticality, durability/reliability and
# publish rate shift a sampling window over it; they do not partition it.
_HISTORY_DEPTH_CHOICES: List[int] = [1, 5, 10, 20, 50, 100]


class StatisticalGraphGenerator:
    """Generates graphs using statistical distributions from config."""

    def __init__(self, config: GraphConfig) -> None:
        self.config = config
        self.rng = random.Random(config.seed)
        # Track pub/sub counts per entity for recursive calculation
        self._direct_pub_counts: Dict[str, int] = {}
        self._direct_sub_counts: Dict[str, int] = {}
        self._uses_graph: Dict[str, List[str]] = {}  # entity_id -> [lib_ids it uses]
        self.logger = logging.getLogger(__name__)

    def _sample_from_distribution(self, metric: StatisticalMetric, as_int: bool = True) -> float:
        """Sample a value from a statistical distribution."""
        if metric.std <= 0:
            value = metric.mean
        else:
            value = self.rng.gauss(metric.mean, metric.std)

        value = max(metric.min, min(metric.max, value))

        if as_int:
            return int(round(value))
        return value

    def _make_edge(self, src: Any, tgt: Any) -> Dict[str, str]:
        return {"from": src.id, "to": tgt.id}

    def _get_all_used_libs_recursive(self, entity_id: str, visited: Optional[set] = None) -> set:
        """Get all libraries used by an entity (app or lib), recursively."""
        if visited is None:
            visited = set()

        if entity_id in visited:
            return set()  # Avoid cycles

        visited.add(entity_id)
        result = set()

        for lib_id in self._uses_graph.get(entity_id, []):
            result.add(lib_id)
            # Recursively get libs used by this lib
            result.update(self._get_all_used_libs_recursive(lib_id, visited))

        return result

    def _get_inherited_pub_count(self, entity_id: str) -> int:
        """Get total pub count from all recursively used libraries."""
        used_libs = self._get_all_used_libs_recursive(entity_id)
        return sum(self._direct_pub_counts.get(lib_id, 0) for lib_id in used_libs)

    def _get_inherited_sub_count(self, entity_id: str) -> int:
        """Get total sub count from all recursively used libraries."""
        used_libs = self._get_all_used_libs_recursive(entity_id)
        return sum(self._direct_sub_counts.get(lib_id, 0) for lib_id in used_libs)

    def _generate_code_metrics(self, app_type: str) -> Dict[str, Any]:
        """Generate a full code_metrics dict for an Application."""
        params = _CODE_METRICS_PARAMS.get(app_type, _DEFAULT_CODE_METRICS_PARAMS)
        return self._build_code_metrics(params)

    def _generate_lib_code_metrics(self, archetype: Optional[str] = None, name_rng: Optional[random.Random] = None) -> Dict[str, Any]:
        """Generate a full code_metrics dict for a Library."""
        if not archetype or archetype not in _LIB_CODE_METRICS_PARAMS:
            rng_to_use = name_rng if name_rng else self.rng
            archetype = rng_to_use.choice(_LIB_ARCHETYPE_WEIGHTS)
        params = _LIB_CODE_METRICS_PARAMS[archetype]
        return self._build_code_metrics(params)

    def _sample_biased(
        self,
        count: int,
        all_items: list,
        cluster_items: list,
        p_intra: float = 0.65,
    ) -> list:
        """Sample *count* items with intra-cluster bias.

        Approximately *p_intra* of the samples come from *cluster_items* (when
        available); the remainder are drawn from *all_items* without
        replacement across the combined result.  Falls back to plain uniform
        sampling when *cluster_items* is empty or *count* is zero.
        """
        if not cluster_items or count <= 0:
            return self.rng.sample(all_items, k=min(count, len(all_items)))

        intra_count = min(round(count * p_intra), len(cluster_items), count)
        intra_sample = self.rng.sample(cluster_items, k=intra_count)

        extra_count = count - intra_count
        if extra_count > 0:
            intra_ids = {id(x) for x in intra_sample}
            extra_pool = [x for x in all_items if id(x) not in intra_ids]
            extra_sample = self.rng.sample(extra_pool, k=min(extra_count, len(extra_pool)))
            return intra_sample + extra_sample
        return intra_sample

    def _build_code_metrics(self, p: Dict[str, Any]) -> Dict[str, Any]:
        """Build a code_metrics dict from parameter ranges."""
        rng = self.rng
        total_loc = rng.randint(*p["loc"])
        cls_per_kloc = rng.uniform(*p["cls_per_kloc"])
        total_classes = max(1, int(round(total_loc / 1000.0 * cls_per_kloc)))
        meth_per_cls = rng.uniform(*p["meth_per_cls"])
        total_methods = max(1, int(round(total_classes * meth_per_cls)))
        fld_per_cls = rng.uniform(*p["fld_per_cls"])
        total_fields = max(0, int(round(total_classes * fld_per_cls)))

        avg_wmc = round(rng.uniform(*p["avg_wmc"]), 2)
        total_wmc = int(round(avg_wmc * total_classes))
        max_wmc = max(int(round(avg_wmc)), int(round(avg_wmc * rng.lognormvariate(0.8, 0.5))))

        avg_lcom = round(rng.uniform(*p["avg_lcom"]), 2)
        max_lcom = round(avg_lcom * rng.uniform(2.0, 4.5), 1)

        avg_cbo = round(rng.uniform(*p["avg_cbo"]), 2)
        max_cbo = int(round(avg_cbo * rng.uniform(1.5, 2.5)))
        avg_rfc = round(rng.uniform(*p["avg_rfc"]), 2)
        max_rfc = int(round(avg_rfc * rng.uniform(1.5, 2.5)))
        avg_fanin = round(rng.uniform(*p["avg_fanin"]), 2)
        max_fanin = int(round(avg_fanin * rng.uniform(2.0, 4.5)))
        avg_fanout = round(rng.uniform(*p["avg_fanout"]), 2)
        max_fanout = int(round(avg_fanout * rng.uniform(2.0, 3.0)))

        return {
            "size": {
                "total_loc": total_loc,
                "total_classes": total_classes,
                "total_methods": total_methods,
                "total_fields": total_fields,
            },
            "complexity": {
                "total_wmc": total_wmc,
                "avg_wmc": avg_wmc,
                "max_wmc": max_wmc,
            },
            "cohesion": {
                "avg_lcom": avg_lcom,
                "max_lcom": max_lcom,
            },
            "coupling": {
                "avg_cbo": avg_cbo,
                "max_cbo": max_cbo,
                "avg_rfc": avg_rfc,
                "max_rfc": max_rfc,
                "avg_fanin": avg_fanin,
                "max_fanin": max_fanin,
                "avg_fanout": avg_fanout,
                "max_fanout": max_fanout,
            },
        }

    # ------------------------------------------------------------------
    # Structural-quality helpers (called from the generation phases below)
    # ------------------------------------------------------------------

    def _build_cluster_to_nodes(
        self,
        nodes: List[Node],
        cluster_domains: List[str],
    ) -> Dict[str, List[Node]]:
        """Randomly partition nodes into per-cluster subsets.

        Each node is assigned to one cluster via random.choices so the
        partition is seeded and deterministic.  Clusters that receive no
        nodes fall back to the full node list so no app/broker is ever
        stranded without a placement target.
        """
        cluster_to_nodes: Dict[str, List[Node]] = {d: [] for d in cluster_domains}
        assignments = self.rng.choices(cluster_domains, k=len(nodes))
        for node, cluster in zip(nodes, assignments):
            cluster_to_nodes[cluster].append(node)
        # Guard: empty cluster bins fall back to all nodes
        for cluster in cluster_domains:
            if not cluster_to_nodes[cluster]:
                cluster_to_nodes[cluster] = list(nodes)
        return cluster_to_nodes

    def _assign_apps_to_nodes(
        self,
        apps: List[Application],
        nodes: List[Node],
        app_cluster_domain: List[str],
        cluster_to_nodes: Dict[str, List[Node]],
        p_collocate: float = 0.70,
    ) -> List[Dict[str, str]]:
        """Assign apps to nodes with cluster affinity.

        Apps with hotstandby=True get a second RUNS_ON edge to a distinct
        node, providing the "active + standby" infra pair.

        With probability *p_collocate* the host is drawn from the app's
        cluster node subset; otherwise it is drawn uniformly from all nodes.
        This makes node-level structural metrics (betweenness, SPOF
        detection) realistic: functionally related apps share infrastructure.
        """
        runs_on = []
        for i, app in enumerate(apps):
            cluster = app_cluster_domain[i]
            if self.rng.random() < p_collocate:
                host = self.rng.choice(cluster_to_nodes[cluster])
            else:
                host = self.rng.choice(nodes)
            runs_on.append(self._make_edge(app, host))
            if app.hotstandby:
                # Pick a second distinct node for the standby pair
                candidates = [n for n in nodes if n.id != host.id]
                if candidates:
                    standby_host = self.rng.choice(candidates)
                    runs_on.append(self._make_edge(app, standby_host))
        return runs_on

    def _qos_preferred_topics(self, topics: List[Topic], app_type: str) -> List[Topic]:
        """Return the topics matching *app_type*'s QoS affinity tier.

        Falls back to (a copy of) *topics* unchanged when no affinity is
        defined for *app_type*, when *topics* is empty, or when no topic
        matches the affinity tier (e.g. every topic in the cluster shares the
        same QoS level).  See _APP_TYPE_QOS_AFFINITY.
        """
        affinity = _APP_TYPE_QOS_AFFINITY.get(app_type)
        if not self.config.qos_affinity:
            affinity = None
        if not affinity or not topics:
            return list(topics)
        pref_rel = set(affinity["reliability"])
        pref_pri = set(affinity["priority"])
        preferred = [
            t for t in topics
            if t.qos.reliability in pref_rel and t.qos.transport_priority in pref_pri
        ]
        return preferred if preferred else list(topics)

    def _rewrite_broker_placement(
        self,
        brokers: List[Broker],
        nodes: List[Node],
        routes: List[Dict[str, str]],
        topic_id_to_cluster: Dict[str, str],
        cluster_to_nodes: Dict[str, List[Node]],
    ) -> List[Dict[str, str]]:
        """Place each broker on a node in its plurality cluster.

        The plurality cluster is determined by the cluster distribution of
        topics the broker routes.  This co-locates brokers with the apps
        they serve, improving infra-layer SPOF and betweenness accuracy.
        Should be called *after* the routes list (including stranded-broker
        guard) is complete so every broker has at least one routed topic.
        """
        broker_cluster_votes: Dict[str, Counter] = {b.id: Counter() for b in brokers}
        for edge in routes:
            broker_id = edge["from"]
            topic_id = edge["to"]
            cluster = topic_id_to_cluster.get(topic_id)
            if cluster and broker_id in broker_cluster_votes:
                broker_cluster_votes[broker_id][cluster] += 1

        runs_on = []
        for broker in brokers:
            votes = broker_cluster_votes[broker.id]
            if votes:
                plurality_cluster = votes.most_common(1)[0][0]
                cluster_nodes = cluster_to_nodes.get(plurality_cluster, nodes)
                host = self.rng.choice(cluster_nodes)
            else:
                host = self.rng.choice(nodes)
            runs_on.append(self._make_edge(broker, host))
        return runs_on

    def _sample_topic_frequency(self, domain: Optional[str], rng: Optional[random.Random] = None) -> float:
        """Sample a topic frequency from a per-domain log-uniform distribution, then map it to the closest allowed frequency."""
        _rng = rng if rng is not None else self.rng
        lo, hi = _DOMAIN_FREQ_BOUNDS.get(domain or "", _DOMAIN_FREQ_BOUNDS_DEFAULT)
        log_val = _rng.uniform(math.log10(lo), math.log10(hi))
        freq = 10.0 ** log_val

        ALLOWED_FREQS = [1.0, 10.0, 25.0, 50.0, 100.0, 200.0]
        closest_freq = min(ALLOWED_FREQS, key=lambda x: abs(x - freq))
        return closest_freq

    def _derive_topic_criticality_with_noise(
        self,
        qos: "QoSPolicy",
        noise_rate: float = _CRITICALITY_NOISE_RATE,
        rng: Optional[random.Random] = None,
    ) -> str:
        """Derive a criticality label from QoS and inject controlled label noise.

        The base label is produced by the same threshold table used in
        ``Topic.__post_init__`` (``CRITICALITY_THRESHOLDS``).  Then, with
        probability *noise_rate*, the label is replaced by a uniformly
        chosen *different* label from ``_CRITICALITY_LABELS``.

        Rationale for noise injection:
            Without noise, ``criticality`` is a deterministic function of
            QoS weights.  Any model that sees the QoS features can recover the
            label perfectly via a lookup table — the prediction task collapses
            to a dictionary read, not structural graph learning.  The noise
            channel forces the GNN to exploit multi-hop graph context (fan-out,
            betweenness, cluster membership) to resolve ambiguous labels,
            which is precisely the generalisation the paper claims.

        Args:
            qos: The ``QoSPolicy`` of the topic being constructed.
            noise_rate: Fraction of labels to flip.  Default is
                ``_CRITICALITY_NOISE_RATE`` (≈ 17 %).
            rng: Random instance to use.  Defaults to ``self.rng``; callers
                that want to avoid perturbing the main topology RNG stream
                should pass a dedicated ``random.Random`` object.

        Returns:
            A criticality label string (one of ``_CRITICALITY_LABELS``).
        """
        _rng = rng if rng is not None else self.rng
        # --- derive base label via threshold table ---
        qos_score = qos.calculate_weight()
        base_label = "HIGH"  # fallback if all thresholds exceeded
        for threshold, label in CRITICALITY_THRESHOLDS:
            if qos_score <= threshold:
                base_label = label
                break

        # --- inject noise ---
        if _rng.random() < noise_rate:
            alternatives = [lbl for lbl in _CRITICALITY_LABELS if lbl != base_label]
            return _rng.choice(alternatives)
        return base_label

    def _derive_topic_deadline(
        self,
        frequency_hz: float,
        criticality: str,
        qos: "QoSPolicy",
        rng: Optional[random.Random] = None,
    ) -> Optional[float]:
        """Derive a temporal SLA deadline in milliseconds.

        In real-time publish-subscribe systems (e.g. DDS, ROS 2, time-critical
        streaming), the deadline is physically anchored to the message period
        T = 1000 / frequency_hz, and tightens as channel urgency rises.

        The slack factor is drawn from a *triangular* distribution over the
        shared support ``_DEADLINE_FACTOR_RANGE``, with criticality shifting
        only the mode.  The three bands therefore overlap: observing a factor
        of 2.0 is consistent with any criticality, just with different
        likelihood.  Likewise every tier has a nonzero chance of declaring no
        deadline at all.

        This overlap is load-bearing, not cosmetic.  An earlier version drew
        the factor from three *disjoint* uniform intervals (HIGH [1.0, 1.5],
        MEDIUM [1.5, 3.0], LOW [3.0, 5.0]) and emitted ``None`` only for LOW.
        Because both ``frequency`` and ``deadline_ms`` are model inputs,
        ``deadline_ms * frequency / 1000`` recovered the label exactly — which
        handed the GNN a closed-form inverse of the ~17 % label noise that
        ``_derive_topic_criticality_with_noise()`` exists to inject.
        Enforced by tests/test_generation_service.py.
        """
        _rng = rng if rng is not None else self.rng
        period_ms = 1000.0 / max(0.001, float(frequency_hz))

        crit_upper = canonical_criticality(criticality)
        mode = _DEADLINE_FACTOR_MODE.get(crit_upper, _DEADLINE_FACTOR_MODE["MEDIUM"])
        if _rng.random() < _NO_DEADLINE_RATE.get(crit_upper, _NO_DEADLINE_RATE["MEDIUM"]):
            return None

        lo, hi = _DEADLINE_FACTOR_RANGE
        factor = _rng.triangular(lo, hi, mode)
        return round(period_ms * factor, 1)

    def _derive_topic_history_depth(
        self,
        criticality: str,
        qos: "QoSPolicy",
        frequency_hz: float,
        rng: Optional[random.Random] = None,
    ) -> int:
        """Derive a queue/buffering limit (history depth).

        Corresponds to the OMG DDS ``HistoryQosPolicy`` KEEP_LAST depth, or to
        a broker's per-topic buffer limit.  Two forces pull in opposite
        directions and both are modelled:

        * durability/reliability pull the depth *up* — a RELIABLE or
          TRANSIENT_LOCAL channel buffers more samples to survive a slow or
          late-joining subscriber;
        * publish rate pulls it *down* — a high-rate stream keeps only the
          freshest samples, since a stale one has no value.

        Every tier draws from ``_HISTORY_DEPTH_CHOICES``, shifted by those two
        forces rather than partitioned by criticality.  As with
        ``_derive_topic_deadline()``, the per-tier supports must overlap: when
        the deep depths ``{20, 50, 100}`` belonged to HIGH alone, the depth
        alone identified the label.  Enforced by
        tests/test_generation_service.py.
        """
        _rng = rng if rng is not None else self.rng
        crit_upper = canonical_criticality(criticality)

        # Shift the sampling window over the shared ladder instead of carving
        # it into disjoint per-tier sets.
        offset = {"HIGH": 2, "MEDIUM": 1}.get(crit_upper, 0)
        if qos.reliability.upper() == "RELIABLE" or qos.durability.upper() in (
            "PERSISTENT", "TRANSIENT_LOCAL"
        ):
            offset += 1
        if frequency_hz >= 50.0:
            offset -= 1

        # The window is deliberately wide enough that no single depth value is
        # reachable from only one criticality tier — a lone reachable value
        # would identify the label as surely as a disjoint range does.
        ladder = _HISTORY_DEPTH_CHOICES
        centre = max(0, min(len(ladder) - 1, offset))
        lo = max(0, centre - 1)
        hi = min(len(ladder) - 1, centre + 3)
        return _rng.choice(ladder[lo:hi + 1])

    def _assign_criticality_two_pass(
        self,
        apps: List[Application],
        publishes: List[Dict[str, str]],
        subscribes: List[Dict[str, str]],
        criticality_pool: Optional[List[Any]],
        hotstandby_pool: Optional[List[Any]] = None,
        topics: Optional[List[Topic]] = None,
        nodes: Optional[List[Node]] = None,
        runs_on: Optional[List[Dict[str, str]]] = None,
    ) -> None:
        """Assign operational criticality (HIGH, MEDIUM, LOW) and hotstandby redundancy
        in-place after topology is built.

        Ranks apps by a composite correlation score combining:
          1. Connected topic QoS and Topic.criticality urgency (w(t) and urgency of pub/sub channels)
          2. Operational app_type semantics (controllers/gateways > processors > actuators > sensors/monitors)
          3. Structural degree proxy (pub_count + sub_count)

        Assigns HIGH, MEDIUM, LOW to apps according to the scenario's criticality_pool
        (or a calibrated domain fallback).
        Assigns hotstandby redundancy in direct correlation with criticality:
        top-ranked services (all HIGH, then top MEDIUM) receive hotstandby=True
        and a secondary RUNS_ON edge to a distinct infrastructure node.
        """
        pub_topics_by_app: Dict[str, List[str]] = {}
        sub_topics_by_app: Dict[str, List[str]] = {}
        for e in publishes:
            pub_topics_by_app.setdefault(e["from"], []).append(e["to"])
        for e in subscribes:
            sub_topics_by_app.setdefault(e["from"], []).append(e["to"])

        topic_map: Dict[str, Topic] = {t.id: t for t in (topics or [])}
        topic_imp: Dict[str, float] = {}
        for tid, t in topic_map.items():
            qos_w = t.qos.calculate_weight()
            crit_ord = (
                TOPIC_CRITICALITY_ORD[canonical_criticality(t.criticality)]
                / MAX_TOPIC_CRITICALITY_ORD
            )
            topic_imp[tid] = 0.6 * qos_w + 0.4 * crit_ord

        deg_counts = {
            a.id: len(pub_topics_by_app.get(a.id, [])) + len(sub_topics_by_app.get(a.id, []))
            for a in apps
        }
        max_deg = max(deg_counts.values(), default=1) or 1

        type_score_map = {
            "controller": 1.0,
            "gateway": 0.9,
            "processor": 0.75,
            "actuator": 0.65,
            "sensor": 0.45,
            "monitor": 0.35,
            "service": 0.55,
        }

        app_scores: Dict[str, float] = {}
        for a in apps:
            t_ids = pub_topics_by_app.get(a.id, []) + sub_topics_by_app.get(a.id, [])
            if t_ids and not self.config.qos_affinity:
                app_qos = 0.5
            elif t_ids:
                t_scores = [topic_imp.get(tid, 0.5) for tid in t_ids]
                app_qos = 0.55 * max(t_scores) + 0.45 * (sum(t_scores) / len(t_scores))
            else:
                app_qos = 0.4

            type_score = type_score_map.get(a.app_type, 0.5)
            deg_norm = deg_counts[a.id] / max_deg

            composite = (
                0.50 * app_qos
                + 0.25 * type_score
                + 0.25 * deg_norm
                + self.rng.uniform(0.0, 0.1)
            )
            app_scores[a.id] = composite

        ranked = sorted(apps, key=lambda a: app_scores[a.id], reverse=True)
        n_total = len(apps)

        if criticality_pool is not None and len(criticality_pool) > 0:
            pool_norm = [canonical_criticality(item) for item in criticality_pool]
            n_high = sum(1 for x in pool_norm if x == "HIGH")
            n_med = sum(1 for x in pool_norm if x == "MEDIUM")
            if n_med == 0 and n_high > 0 and n_high < n_total:
                n_med = max(1, round((n_total - n_high) * 0.6))
            n_high = min(n_high, n_total)
            n_med = min(n_med, n_total - n_high)
        else:
            n_high = max(1, round(n_total * 0.25))
            n_med = max(1, round(n_total * 0.50))
            if n_high + n_med > n_total:
                n_med = max(0, n_total - n_high)

        for idx, app in enumerate(ranked):
            if idx < n_high:
                app.criticality = "HIGH"
            elif idx < n_high + n_med:
                app.criticality = "MEDIUM"
            else:
                app.criticality = "LOW"

        # Correlate hotstandby redundancy with criticality:
        # High-criticality services receive redundancy first, followed by top medium-criticality apps.
        can_hotstandby = nodes is not None and len(nodes) >= 2
        if not can_hotstandby:
            n_standby = 0
        elif hotstandby_pool:
            n_standby = min(n_total, sum(1 for x in hotstandby_pool if x))
        else:
            # Domain fallback: all HIGH apps + 25% of MEDIUM apps get hotstandby
            n_standby = min(n_total, n_high + max(0, round(n_med * 0.25)))
        for idx, app in enumerate(ranked):
            app.hotstandby = (idx < n_standby)

        # Synchronize runs_on relationships:
        # hotstandby=True requires 2 RUNS_ON edges to distinct nodes;
        # hotstandby=False requires exactly 1 RUNS_ON edge.
        if runs_on is not None and nodes is not None:
            app_edges: Dict[str, List[Dict[str, str]]] = {a.id: [] for a in apps}
            other_edges = []
            for e in runs_on:
                fid = e.get("from")
                if fid in app_edges:
                    app_edges[fid].append(e)
                else:
                    other_edges.append(e)

            new_app_edges = []
            for app in apps:
                current_edges = app_edges[app.id]
                if not current_edges:
                    primary_host = self.rng.choice(nodes)
                    current_edges.append(self._make_edge(app, primary_host))

                if app.hotstandby and can_hotstandby:
                    if len(current_edges) == 1:
                        primary_host_id = current_edges[0]["to"]
                        candidates = [n for n in nodes if n.id != primary_host_id]
                        if candidates:
                            standby_host = self.rng.choice(candidates)
                            current_edges.append(self._make_edge(app, standby_host))
                    elif len(current_edges) > 2:
                        current_edges = current_edges[:2]
                else:
                    if len(current_edges) > 1:
                        current_edges = current_edges[:1]

                new_app_edges.extend(current_edges)

            runs_on.clear()
            runs_on.extend(new_app_edges)
            runs_on.extend(other_edges)

    # ------------------------------------------------------------------
    # Generation phases (called in order from generate() below)
    # ------------------------------------------------------------------

    def _build_infrastructure(
        self, c: GraphConfig, domain_ds: Optional[DomainDataset],
    ) -> Tuple[List[Node], List[Broker]]:
        """Construct Node and Broker entities (no relationships yet)."""
        nodes = [
            Node(id=f"N{i}", name=domain_ds.get_node_name() if domain_ds else f"Node-{i}")
            for i in range(c.nodes)
        ]
        brokers = [
            Broker(id=f"B{i}", name=domain_ds.get_broker_name() if domain_ds else f"Broker-{i}")
            for i in range(c.brokers)
        ]
        return nodes, brokers

    def _weighted_pool(self, dist, defaults: List[str]) -> Optional[List[str]]:
        """Materialize a shuffled weighted pool from a categorical distribution.

        Returns None (no self.rng draw) when *dist* is absent, matching the
        "only shuffle if this specific distribution was configured" behaviour
        of the original inline QoS-pool setup.

        Values are upper-cased: configs declare ``category_counts`` in lower
        case, but QoSPolicy's score tables, _APP_TYPE_QOS_AFFINITY and the
        emitted corpus are upper-case only, and the membership tests in
        _qos_preferred_topics() are raw (uncanonicalised) comparisons.
        """
        if not dist:
            return None
        pool = [str(v).strip().upper() for v in dist.to_weighted_list(defaults)]
        self.rng.shuffle(pool)
        return pool

    def _pick_categorical(self, pool: Optional[List[str]], options: List[str], i: int, rng: random.Random) -> str:
        """Pick a categorical value for item *i*: pool[i] deterministically
        when available, otherwise a random draw from the pool, falling back
        to the full option space when no pool was configured at all."""
        if pool and i < len(pool):
            return pool[i]
        elif pool:
            return rng.choice(pool)
        else:
            return rng.choice(options)

    def _build_topics(
        self,
        c: GraphConfig,
        domain_ds: Optional[DomainDataset],
        name_rng: random.Random,
        topic_attr_rng: random.Random,
    ) -> List[Topic]:
        """Construct all Topic entities: QoS, size, domain-aware frequency,
        and noisy criticality ground truth.

        Draws from self.rng (size sampling, QoS-pool shuffling), name_rng
        (naming and QoS-pool selection), and topic_attr_rng
        (frequency/criticality — isolated from the main topology stream so
        those draws never perturb it).
        """
        qos_stats = c.qos_stats
        durability_pool = self._weighted_pool(
            qos_stats.qos_durability_distribution if qos_stats else None, DURABILITY_OPTIONS
        )
        reliability_pool = self._weighted_pool(
            qos_stats.qos_reliability_distribution if qos_stats else None, RELIABILITY_OPTIONS
        )
        priority_pool = self._weighted_pool(
            qos_stats.qos_transport_priority_distribution if qos_stats else None, PRIORITY_OPTIONS
        )

        topics: List[Topic] = []
        for i in range(c.topics):
            if c.topic_stats and c.topic_stats.topic_size_bytes.mean > 0:
                size = self._sample_from_distribution(c.topic_stats.topic_size_bytes)
            else:
                size = self.rng.randint(64, 65536)

            # Round to nearest power of 2
            size = int(2 ** round(math.log2(max(1.0, float(size)))))

            topic_name = domain_ds.get_topic_name() if domain_ds else f"Topic-{i}"

            # QoS always comes from the configured distributions. It used to be
            # overridden by a name-pattern lookup whenever the scenario declared
            # a domain -- true of every scenario -- which made these pools dead
            # config and collapsed seven of the twelve LOSO scenarios onto a
            # single QoS triple, zeroing the entropy of the edge-feature
            # dimensions that carry it. See reproduce/qos_corpus_diagnostic.py.
            durability = self._pick_categorical(durability_pool, DURABILITY_OPTIONS, i, name_rng)
            reliability = self._pick_categorical(reliability_pool, RELIABILITY_OPTIONS, i, name_rng)
            transport_priority = self._pick_categorical(priority_pool, PRIORITY_OPTIONS, i, name_rng)

            qos_policy = QoSPolicy(
                durability=durability,
                reliability=reliability,
                transport_priority=transport_priority,
            )
            freq = self._sample_topic_frequency(c.domain, rng=topic_attr_rng)
            crit = self._derive_topic_criticality_with_noise(qos_policy, rng=topic_attr_rng)
            deadline = self._derive_topic_deadline(freq, crit, qos_policy, rng=topic_attr_rng)
            history = self._derive_topic_history_depth(crit, qos_policy, freq, rng=topic_attr_rng)

            topics.append(Topic(
                id=f"T{i}",
                name=topic_name,
                size=size,
                qos=qos_policy,
                # Inject domain-aware frequency and noisy criticality as
                # generator-supplied ground truth.  This prevents the prediction
                # task from collapsing to a QoS lookup (leakage path).
                # Both calls use the isolated topic_attr_rng so the main
                # topology RNG stream (self.rng) remains unperturbed.
                frequency=freq,
                criticality=crit,
                deadline_ms=deadline,
                history_depth=history,
            ))
        return topics

    def _assign_clusters(
        self, c: GraphConfig, name_rng: random.Random, topics: List[Topic],
    ) -> Tuple[Dict[str, List[str]], List[str], List[str], List[str], Dict[str, str], str, Dict[str, List[Topic]], Dict[str, str]]:
        """Pre-assign apps, libs, and topics to hierarchy clusters (css_name).

        Apps/libs in the same cluster share a css_name and preferentially
        pub/sub or depend on entities in the same cluster (later phases,
        p_intra = c.intra_cluster_coupling), making the hierarchy signal
        structurally meaningful for coupling analysis instead of being an
        independently-sampled label with no topological effect.
        """
        hier_pool = SYSTEM_HIERARCHY_POOLS.get(c.domain, GENERIC_HIERARCHY_POOL)
        cluster_domains: List[str] = hier_pool["domain"]
        n_clusters = len(cluster_domains)

        # Weighted random assignment creates natural cluster skew.
        app_cluster_domain: List[str] = self.rng.choices(cluster_domains, k=c.apps)
        lib_cluster_domain: List[str] = self.rng.choices(cluster_domains, k=c.libs)

        app_id_to_cluster: Dict[str, str] = {
            f"A{i}": app_cluster_domain[i] for i in range(c.apps)
        }

        single_csms_name: str = name_rng.choice(hier_pool["system"])

        cluster_to_topics: Dict[str, List[Topic]] = {d: [] for d in cluster_domains}
        topic_id_to_cluster: Dict[str, str] = {}
        for idx, topic in enumerate(topics):
            cluster_d = cluster_domains[idx % n_clusters]
            cluster_to_topics[cluster_d].append(topic)
            topic_id_to_cluster[topic.id] = cluster_d

        return (hier_pool, cluster_domains, app_cluster_domain, lib_cluster_domain,
                app_id_to_cluster, single_csms_name, cluster_to_topics, topic_id_to_cluster)

    def _build_apps(
        self,
        c: GraphConfig,
        domain_ds: Optional[DomainDataset],
        name_rng: random.Random,
        hier_pool: Dict[str, List[str]],
        app_cluster_domain: List[str],
        single_csms_name: str,
        cluster_domains: List[str],
    ) -> Tuple[List[Application], Optional[List[str]], Optional[List[bool]], Dict[str, List[Application]]]:
        """Construct all Application entities and the criticality/hotstandby pools
        and cluster grouping.

        Criticality and hotstandby redundancy are assigned in-place after
        topology is built (_apply_post_topology / _assign_criticality_two_pass)
        so they properly correlate with each other, topic QoS, and graph structure.
        """
        # Both pools are consumed for their *tier counts* only — the ranking in
        # _assign_criticality_two_pass() decides which app gets which tier — so
        # neither is shuffled: the order carries no information.
        criticality_pool = None
        if c.application_stats and c.application_stats.app_criticality_distribution:
            criticality_pool = c.application_stats.app_criticality_distribution.to_weighted_list()

        hotstandby_pool = None
        if c.application_stats and c.application_stats.app_hotstandby_distribution:
            hotstandby_pool = c.application_stats.app_hotstandby_distribution.to_weighted_list()

        apps: List[Application] = []
        for i in range(c.apps):
            app_name = domain_ds.get_app_name() if domain_ds else f"App-{i}"
            if domain_ds:
                app_type = get_app_type_for_name(app_name)
            else:
                app_type = name_rng.choice(APP_TYPE_OPTIONS)

            code_metrics = self._generate_code_metrics(app_type)
            # Use the pre-assigned cluster css_name so hierarchy reflects
            # actual structural grouping rather than an independent random draw.
            hierarchy = {
                "csc_name": name_rng.choice(hier_pool["component"]),
                "csci_name": name_rng.choice(hier_pool["config_item"]),
                "css_name": app_cluster_domain[i],
                "csms_name": single_csms_name,
            }

            num_roles = self.rng.randint(1, 3)
            role = sorted(self.rng.sample(APP_USER_ROLE_OPTIONS, num_roles))
            apps.append(Application(
                id=f"A{i}",
                name=app_name,
                app_type=app_type,
                role=role,
                criticality="MEDIUM",  # assigned after topology by _assign_criticality_two_pass
                hotstandby=False,      # assigned after topology correlated with criticality
                version=f"{self.rng.randint(1, 3)}.{self.rng.randint(0, 9)}.{self.rng.randint(0, 9)}",
                system_hierarchy=hierarchy,
                code_metrics=code_metrics,
            ))

        cluster_to_apps: Dict[str, List[Application]] = {d: [] for d in cluster_domains}
        for i, app in enumerate(apps):
            cluster_to_apps[app_cluster_domain[i]].append(app)

        return apps, criticality_pool, hotstandby_pool, cluster_to_apps

    def _build_libs(
        self,
        c: GraphConfig,
        domain_ds: Optional[DomainDataset],
        name_rng: random.Random,
        single_csms_name: str,
        lib_cluster_domain: List[str],
        cluster_domains: List[str],
    ) -> Tuple[List[Library], Dict[str, List[Library]]]:
        """Construct all Library entities and the cluster→libs grouping."""
        cluster_to_libs: Dict[str, List[Library]] = {d: [] for d in cluster_domains}

        libs: List[Library] = []
        for i in range(c.libs):
            lib_name = domain_ds.get_library_name() if domain_ds else f"Lib-{i}"
            if domain_ds:
                archetype = get_lib_archetype_for_name(lib_name)
                lib_code_metrics = self._generate_lib_code_metrics(archetype, name_rng)
            else:
                lib_code_metrics = self._generate_lib_code_metrics(None, name_rng)

            lib_hierarchy = domain_ds.get_system_hierarchy() if domain_ds else get_generic_system_hierarchy(name_rng)
            lib_hierarchy["csms_name"] = single_csms_name
            if not domain_ds:
                lib_hierarchy["css_name"] = lib_cluster_domain[i]

            libs.append(Library(
                id=f"L{i}",
                name=lib_name,
                version=f"{self.rng.randint(0, 2)}.{self.rng.randint(0, 9)}.{self.rng.randint(0, 9)}",
                system_hierarchy=lib_hierarchy,
                code_metrics=lib_code_metrics,
            ))
            cluster_to_libs[lib_cluster_domain[i]].append(libs[-1])
        return libs, cluster_to_libs

    def _wire_routes(self, brokers: List[Broker], topics: List[Topic]) -> List[Dict[str, str]]:
        """Assign each topic to 1–2 brokers (ROUTES edges), then guard
        against any broker ending up with zero routed topics — an unrouted
        broker would be invisible to betweenness and ROUTES-based metrics.
        """
        routes = []
        for topic in topics:
            # With len(brokers)>=2, there is a 30% chance of a second broker
            # (redundancy), so broker failure impact is proportional to
            # structural importance rather than a random lottery.
            primary_broker = self.rng.choice(brokers)
            routes.append(self._make_edge(primary_broker, topic))
            if len(brokers) >= 2 and self.rng.random() < 0.30:
                other_brokers = [b for b in brokers if b.id != primary_broker.id]
                if other_brokers:
                    secondary_broker = self.rng.choice(other_brokers)
                    routes.append(self._make_edge(secondary_broker, topic))

        # Guard: assign each stranded broker to a topic in round-robin order
        # (deterministic given the seed) so it is never left unrouted.
        routed_broker_ids = {edge["from"] for edge in routes}
        unrouted_brokers = [b for b in brokers if b.id not in routed_broker_ids]
        for idx, broker in enumerate(unrouted_brokers):
            topic = topics[idx % len(topics)]
            routes.append(self._make_edge(broker, topic))
        return routes

    def _wire_uses(
        self,
        c: GraphConfig,
        apps: List[Application],
        libs: List[Library],
        topics: List[Topic],
        app_id_to_cluster: Dict[str, str],
        lib_cluster_domain: List[str],
        cluster_to_apps: Dict[str, List[Application]],
        cluster_to_libs: Dict[str, List[Library]],
        publishes: List[Dict[str, str]],
        subscribes: List[Dict[str, str]],
        uses: List[Dict[str, str]],
    ) -> Tuple[List[Dict[str, str]], List[Dict[str, str]], List[Dict[str, str]]]:
        """Wire USES edges (lib→lib, app→lib) plus, when configured, direct
        library PUBLISHES_TO/SUBSCRIBES_TO edges.

        Mutates and returns (publishes, subscribes, uses): libraries can
        publish or subscribe directly to topics (library_stats.
        direct_publish_count), so this phase touches all three edge lists,
        not only `uses`.
        """
        # lib -> lib transitive dependencies (30% chance per lib)
        for lib in libs:
            if len(libs) > 1 and self.rng.random() < 0.3:
                n_lib_deps = self.rng.randint(0, min(2, len(libs) - 1))
                other_libs = [l for l in libs if l.id != lib.id]
                if n_lib_deps > 0 and other_libs:
                    targets = self.rng.sample(other_libs, k=min(n_lib_deps, len(other_libs)))
                    for t in targets:
                        uses.append(self._make_edge(lib, t))
                        self._uses_graph[lib.id].append(t.id)

        # Direct library publish/subscribe to topics
        if c.library_stats and c.library_stats.direct_publish_count.mean > 0:
            for lib in libs:
                pub_count = self._sample_from_distribution(c.library_stats.direct_publish_count)
                pub_count = min(pub_count, len(topics))
                if pub_count > 0:
                    pub_topics = self.rng.sample(topics, k=pub_count)
                    for t in pub_topics:
                        publishes.append(self._make_edge(lib, t))
                    self._direct_pub_counts[lib.id] = pub_count

                sub_count = self._sample_from_distribution(c.library_stats.direct_subscribe_count)
                sub_count = min(sub_count, len(topics))
                if sub_count > 0:
                    sub_topics = self.rng.sample(topics, k=sub_count)
                    for t in sub_topics:
                        subscribes.append(self._make_edge(lib, t))
                    self._direct_sub_counts[lib.id] = sub_count

        # Library usage by applications
        if c.library_stats and c.library_stats.applications_using_this_library.mean > 0:
            for i, lib in enumerate(libs):
                lib_cluster_apps = cluster_to_apps[lib_cluster_domain[i]]
                usage_count = self._sample_from_distribution(c.library_stats.applications_using_this_library)
                usage_count = min(usage_count, len(apps))
                if usage_count > 0:
                    using_apps = self._sample_biased(usage_count, apps, lib_cluster_apps, p_intra=c.intra_cluster_coupling)
                    for app in using_apps:
                        if lib.id not in self._uses_graph[app.id]:
                            uses.append(self._make_edge(app, lib))
                            self._uses_graph[app.id].append(lib.id)
        else:
            for app in apps:
                app_cluster_libs = cluster_to_libs[app_id_to_cluster[app.id]]
                if libs:
                    max_uses = min(max(3, int(0.1 * len(libs))), len(libs))
                    n_uses = self.rng.randint(0, max_uses)
                    targets = self._sample_biased(n_uses, libs, app_cluster_libs, p_intra=c.intra_cluster_coupling)
                    for t in targets:
                        if t.id not in self._uses_graph[app.id]:
                            uses.append(self._make_edge(app, t))
                            self._uses_graph[app.id].append(t.id)

        return publishes, subscribes, uses

    def _wire_pubsub_from_app_totals(
        self,
        c: GraphConfig,
        apps: List[Application],
        topics: List[Topic],
        app_id_to_cluster: Dict[str, str],
        cluster_to_topics: Dict[str, List[Topic]],
        publishes: List[Dict[str, str]],
        subscribes: List[Dict[str, str]],
    ) -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
        """Strategy: application_stats.total_publish_count_including_libraries.

        Subtracts each app's inherited library pub/sub counts from its total
        target to get the direct count still needed, then wires it with
        cluster + QoS-affinity biased sampling.
        """
        for app in apps:
            inherited_pub = self._get_inherited_pub_count(app.id)
            inherited_sub = self._get_inherited_sub_count(app.id)

            total_pub_target = self._sample_from_distribution(
                c.application_stats.total_publish_count_including_libraries
            )
            total_sub_target = self._sample_from_distribution(
                c.application_stats.total_subscribe_count_including_libraries
            )

            direct_pub_count = max(0, total_pub_target - inherited_pub)
            direct_sub_count = max(0, total_sub_target - inherited_sub)

            direct_pub_count = min(direct_pub_count, len(topics))
            direct_sub_count = min(direct_sub_count, len(topics))

            # Enforce messaging capability constraint derived from app_type
            if not _can_publish(app.app_type):
                direct_pub_count = 0
            if not _can_subscribe(app.app_type):
                direct_sub_count = 0

            # Cluster + QoS-affinity biased sampling: gateway/controller apps
            # draw from RELIABLE/HIGH topics first; sensors from BEST_EFFORT/LOW.
            app_cluster_topics = cluster_to_topics[app_id_to_cluster[app.id]]
            pool = self._qos_preferred_topics(app_cluster_topics, app.app_type)

            if direct_pub_count > 0:
                pub_topics = self._sample_biased(direct_pub_count, topics, pool, p_intra=c.intra_cluster_coupling)
                for t in pub_topics:
                    publishes.append(self._make_edge(app, t))
                self._direct_pub_counts[app.id] = direct_pub_count

            if direct_sub_count > 0:
                sub_topics = self._sample_biased(direct_sub_count, topics, pool, p_intra=c.intra_cluster_coupling)
                for t in sub_topics:
                    subscribes.append(self._make_edge(app, t))
                self._direct_sub_counts[app.id] = direct_sub_count
        return publishes, subscribes

    def _wire_pubsub_from_app_direct(
        self,
        c: GraphConfig,
        apps: List[Application],
        topics: List[Topic],
        app_id_to_cluster: Dict[str, str],
        cluster_to_topics: Dict[str, List[Topic]],
        publishes: List[Dict[str, str]],
        subscribes: List[Dict[str, str]],
    ) -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
        """Strategy: application_stats.direct_publish_count /
        direct_subscribe_count (no library-count subtraction)."""
        for app in apps:
            app_cluster_topics = cluster_to_topics[app_id_to_cluster[app.id]]
            pool = self._qos_preferred_topics(app_cluster_topics, app.app_type)

            pub_count = self._sample_from_distribution(c.application_stats.direct_publish_count)
            pub_count = min(pub_count, len(topics))
            if not _can_publish(app.app_type):
                pub_count = 0

            if pub_count > 0:
                pub_topics = self._sample_biased(pub_count, topics, pool, p_intra=c.intra_cluster_coupling)
                for t in pub_topics:
                    publishes.append(self._make_edge(app, t))
                self._direct_pub_counts[app.id] = pub_count

            sub_count = self._sample_from_distribution(c.application_stats.direct_subscribe_count)
            sub_count = min(sub_count, len(topics))
            if not _can_subscribe(app.app_type):
                sub_count = 0

            if sub_count > 0:
                sub_topics = self._sample_biased(sub_count, topics, pool, p_intra=c.intra_cluster_coupling)
                for t in sub_topics:
                    subscribes.append(self._make_edge(app, t))
                self._direct_sub_counts[app.id] = sub_count
        return publishes, subscribes

    def _wire_pubsub_from_topic_stats(
        self,
        c: GraphConfig,
        apps: List[Application],
        topics: List[Topic],
        cluster_to_apps: Dict[str, List[Application]],
        topic_id_to_cluster: Dict[str, str],
        publishes: List[Dict[str, str]],
        subscribes: List[Dict[str, str]],
    ) -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
        """Strategy: topic_stats.applications_publishing_to_this_topic /
        applications_subscribing_to_this_topic (per-topic fan-in/out targets)."""
        valid_pubs_all = [a for a in apps if _can_publish(a.app_type)]
        valid_subs_all = [a for a in apps if _can_subscribe(a.app_type)]
        for topic in topics:
            topic_cluster_apps = cluster_to_apps[topic_id_to_cluster[topic.id]]
            topic_cluster_pubs = [a for a in topic_cluster_apps if _can_publish(a.app_type)]
            topic_cluster_subs = [a for a in topic_cluster_apps if _can_subscribe(a.app_type)]

            pub_count = self._sample_from_distribution(c.topic_stats.applications_publishing_to_this_topic)
            pub_count = min(pub_count, len(valid_pubs_all))
            if pub_count > 0:
                pubs = self._sample_biased(pub_count, valid_pubs_all, topic_cluster_pubs, p_intra=c.intra_cluster_coupling)
                for p in pubs:
                    publishes.append(self._make_edge(p, topic))
                    self._direct_pub_counts[p.id] = self._direct_pub_counts.get(p.id, 0) + 1

            sub_count = self._sample_from_distribution(c.topic_stats.applications_subscribing_to_this_topic)
            sub_count = min(sub_count, len(valid_subs_all))
            if sub_count > 0:
                subs = self._sample_biased(sub_count, valid_subs_all, topic_cluster_subs, p_intra=c.intra_cluster_coupling)
                for s in subs:
                    subscribes.append(self._make_edge(s, topic))
                    self._direct_sub_counts[s.id] = self._direct_sub_counts.get(s.id, 0) + 1
        return publishes, subscribes

    def _wire_pubsub_uniform(
        self,
        c: GraphConfig,
        apps: List[Application],
        topics: List[Topic],
        cluster_to_apps: Dict[str, List[Application]],
        topic_id_to_cluster: Dict[str, str],
        publishes: List[Dict[str, str]],
        subscribes: List[Dict[str, str]],
    ) -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
        """Fallback strategy: no statistical config at all — uniform random
        fan-in/out per topic, still cluster-biased via _sample_biased()."""
        valid_pubs_all = [a for a in apps if _can_publish(a.app_type)]
        valid_subs_all = [a for a in apps if _can_subscribe(a.app_type)]
        for topic in topics:
            topic_cluster_apps = cluster_to_apps[topic_id_to_cluster[topic.id]]
            topic_cluster_pubs = [a for a in topic_cluster_apps if _can_publish(a.app_type)]
            topic_cluster_subs = [a for a in topic_cluster_apps if _can_subscribe(a.app_type)]

            k_pubs = self.rng.randint(1, max(2, min(5, len(valid_pubs_all))))
            k_subs = self.rng.randint(1, max(2, min(8, len(valid_subs_all))))
            pubs = self._sample_biased(k_pubs, valid_pubs_all, topic_cluster_pubs, p_intra=c.intra_cluster_coupling)
            subs = self._sample_biased(k_subs, valid_subs_all, topic_cluster_subs, p_intra=c.intra_cluster_coupling)
            for p in pubs:
                publishes.append(self._make_edge(p, topic))
            for s in subs:
                subscribes.append(self._make_edge(s, topic))
        return publishes, subscribes

    def _wire_pubsub(
        self,
        c: GraphConfig,
        apps: List[Application],
        topics: List[Topic],
        app_id_to_cluster: Dict[str, str],
        cluster_to_topics: Dict[str, List[Topic]],
        cluster_to_apps: Dict[str, List[Application]],
        topic_id_to_cluster: Dict[str, str],
        publishes: List[Dict[str, str]],
        subscribes: List[Dict[str, str]],
    ) -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
        """Wire PUBLISHES_TO / SUBSCRIBES_TO edges using whichever of four
        mutually-exclusive strategies the config selects, in priority order:
        per-app totals (incl. inherited library counts) > per-app direct
        counts > per-topic fan-in/out targets > uniform random fallback.

        The four strategies interleave their self.rng calls differently (e.g.
        strategy 1 samples both counts before sampling either edge set;
        strategy 2 alternates count/sample per direction) and are kept as
        separate methods rather than one parameterized loop for that reason:
        unifying them would reorder self.rng draws and change output for
        every already-seeded dataset.
        """
        app_stats = c.application_stats
        if app_stats and app_stats.total_publish_count_including_libraries.mean > 0:
            return self._wire_pubsub_from_app_totals(
                c, apps, topics, app_id_to_cluster, cluster_to_topics, publishes, subscribes
            )
        elif app_stats and app_stats.direct_publish_count.mean > 0:
            return self._wire_pubsub_from_app_direct(
                c, apps, topics, app_id_to_cluster, cluster_to_topics, publishes, subscribes
            )
        elif c.topic_stats and c.topic_stats.applications_publishing_to_this_topic.mean > 0:
            return self._wire_pubsub_from_topic_stats(
                c, apps, topics, cluster_to_apps, topic_id_to_cluster, publishes, subscribes
            )
        else:
            return self._wire_pubsub_uniform(
                c, apps, topics, cluster_to_apps, topic_id_to_cluster, publishes, subscribes
            )

    def _wire_connects(self, nodes: List[Node], connection_density: float) -> List[Dict[str, str]]:
        """Wire CONNECTS_TO edges between infrastructure nodes, guarding
        against a totally disconnected mesh in tiny topologies."""
        connects = []
        for i in range(len(nodes)):
            for j in range(i + 1, len(nodes)):
                if self.rng.random() < connection_density:
                    connects.append(self._make_edge(nodes[i], nodes[j]))

        if len(nodes) >= 2 and not connects:
            i, j = self.rng.sample(range(len(nodes)), 2)
            connects.append(self._make_edge(nodes[i], nodes[j]))
        return connects

    #: Offset of the fabric's RNG stream from the generator seed. A stream of its
    #: own, so adding a ``network:`` block cannot shift any other draw.
    FABRIC_SEED_OFFSET = 54321

    @staticmethod
    def _wire_fabric(
        nodes: List[Node], net: NetworkConfig, rng: random.Random,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Two-tier switched network: hosts -> top-of-rack switches -> core routers.

        Returns (network_devices, connects_to). Links run from the lower tier to
        the higher one and carry their interfaces and, when configured, capacity.
        """
        hosts = [n.id for n in nodes]
        rng.shuffle(hosts)
        racks = [hosts[i:i + net.hosts_per_rack] for i in range(0, len(hosts), net.hosts_per_rack)]
        cores = [f"RT{j}" for j in range(net.core_routers)] if len(racks) > 1 else []

        def link(src: str, dst: str, src_if: str, dst_if: str, cap: Optional[float]) -> Dict[str, Any]:
            edge: Dict[str, Any] = {"from": src, "to": dst, "from_interface": src_if, "to_interface": dst_if}
            if cap is not None:
                edge["capacity_mbps"] = cap
            return edge

        devices: List[Dict[str, Any]] = []
        connects: List[Dict[str, Any]] = []
        for i, rack in enumerate(racks):
            tor = f"SW{i}"
            devices.append({"id": tor, "name": f"tor-{i}", "role": "switch"})
            for k, host in enumerate(sorted(rack)):
                connects.append(link(host, tor, "eth0", f"port{k}", net.host_link_mbps))
        for j, core in enumerate(cores):
            devices.append({"id": core, "name": f"core-{j}", "role": "router"})
        for i in range(len(racks)):
            for j, core in enumerate(cores):
                connects.append(link(f"SW{i}", core, f"uplink{j}", f"port{i}", net.uplink_mbps))
        return devices, connects

    def _apply_post_topology(
        self,
        apps: List[Application],
        topics: List[Topic],
        publishes: List[Dict[str, str]],
        subscribes: List[Dict[str, str]],
        criticality_pool: Optional[List[str]],
        hotstandby_pool: Optional[List[bool]] = None,
        runs_on: Optional[List[Dict[str, str]]] = None,
        nodes: Optional[List[Node]] = None,
    ) -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
        """Post-topology quality passes: guarantee every app has at least one
        pub/sub edge (preventing inflated F1 from trivially-isolated nodes),
        then assign criticality and hotstandby redundancy to the structurally central
        apps now that the topology (and therefore each app's degree) is known.
        """
        connected_apps = {e["from"] for e in publishes}.union(e["from"] for e in subscribes)
        isolated_apps = [a for a in apps if a.id not in connected_apps]
        for app in isolated_apps:
            t = self.rng.choice(topics)
            if _can_publish(app.app_type):
                publishes.append(self._make_edge(app, t))
            elif _can_subscribe(app.app_type):
                subscribes.append(self._make_edge(app, t))

        self._assign_criticality_two_pass(
            apps,
            publishes,
            subscribes,
            criticality_pool,
            hotstandby_pool=hotstandby_pool,
            topics=topics,
            nodes=nodes,
            runs_on=runs_on,
        )
        return publishes, subscribes

    def _assemble(
        self,
        c: GraphConfig,
        nodes: List[Node],
        brokers: List[Broker],
        topics: List[Topic],
        apps: List[Application],
        libs: List[Library],
        runs_on: List[Dict[str, str]],
        routes: List[Dict[str, str]],
        publishes: List[Dict[str, str]],
        subscribes: List[Dict[str, str]],
        connects: List[Dict[str, str]],
        uses: List[Dict[str, str]],
        network_devices: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Serialize all entities/relationships and run schema validation."""
        graph_dict = {
            "metadata": {
                "scale": c.to_scale_dict(),
                "seed": c.seed,
                "generation_mode": "statistical" if c.use_statistics else "random",
                "domain": c.domain,
                "scenario": c.scenario,
            },
            "nodes": [n.to_dict() for n in nodes],
            "brokers": [b.to_dict() for b in brokers],
            "topics": [t.to_dict() for t in topics],
            "applications": [a.to_dict() for a in apps],
            "libraries": [l.to_dict() for l in libs],
            "relationships": {
                "runs_on": runs_on,
                "routes": routes,
                "publishes_to": publishes,
                "subscribes_to": subscribes,
                "connects_to": connects,
                "uses": uses
            }
        }
        if network_devices is not None:
            graph_dict["metadata"]["network"] = c.network.to_dict()
            graph_dict["network_devices"] = network_devices
        return validate_and_clean_schema(graph_dict)

    def generate(self) -> Dict[str, Any]:
        """Generate a complete graph, in two passes.

        Pass 1 (entities): infrastructure, topics, hierarchy clusters, apps,
        libraries — in that order, since topic and cluster attribute sampling
        must complete before apps/libs can be placed into clusters.

        Pass 2 (relationships): RUNS_ON, ROUTES, USES, PUBLISHES_TO/
        SUBSCRIBES_TO, CONNECTS_TO, then the post-topology quality passes
        (isolated-app guard, criticality assignment).  See each phase
        method's docstring for its role; see docs/graph-generation.md §8 for
        the narrative version of this pipeline.
        """
        c = self.config
        name_rng = random.Random(c.seed + 12345)
        # Dedicated RNG for topic attribute sampling (frequency, criticality).
        # Isolated from self.rng so that topic attribute draws do NOT perturb
        # the main topology RNG stream (broker routes, pub/sub edges, node
        # placement), preserving reproducibility of all previously seeded tests.
        topic_attr_rng = random.Random(c.seed + 99999)

        domain_ds = DomainDataset(c.domain, name_rng) if c.domain else None

        # --- Pass 1: entities ---
        nodes, brokers = self._build_infrastructure(c, domain_ds)
        topics = self._build_topics(c, domain_ds, name_rng, topic_attr_rng)
        (hier_pool, cluster_domains, app_cluster_domain, lib_cluster_domain,
         app_id_to_cluster, single_csms_name, cluster_to_topics, topic_id_to_cluster
         ) = self._assign_clusters(c, name_rng, topics)
        apps, criticality_pool, hotstandby_pool, cluster_to_apps = self._build_apps(
            c, domain_ds, name_rng, hier_pool, app_cluster_domain, single_csms_name, cluster_domains
        )
        libs, cluster_to_libs = self._build_libs(
            c, domain_ds, name_rng, single_csms_name, lib_cluster_domain, cluster_domains
        )

        # --- Pass 2: relationships ---
        cluster_to_nodes = self._build_cluster_to_nodes(nodes, cluster_domains)
        # Apps: cluster-affine node assignment (70% of apps land on a node
        # from their functional cluster; 30% are placed anywhere).
        runs_on = self._assign_apps_to_nodes(apps, nodes, app_cluster_domain, cluster_to_nodes)

        routes = self._wire_routes(brokers, topics)
        # Brokers: cluster-affine placement based on plurality of routed
        # topics.  Must run after routes (incl. the stranded-broker guard).
        runs_on.extend(self._rewrite_broker_placement(
            brokers, nodes, routes, topic_id_to_cluster, cluster_to_nodes
        ))

        for lib in libs:
            self._uses_graph[lib.id] = []
            self._direct_pub_counts[lib.id] = 0
            self._direct_sub_counts[lib.id] = 0
        for app in apps:
            self._uses_graph[app.id] = []
            self._direct_pub_counts[app.id] = 0
            self._direct_sub_counts[app.id] = 0

        publishes, subscribes, uses = self._wire_uses(
            c, apps, libs, topics, app_id_to_cluster, lib_cluster_domain,
            cluster_to_apps, cluster_to_libs, [], [], [],
        )
        publishes, subscribes = self._wire_pubsub(
            c, apps, topics, app_id_to_cluster, cluster_to_topics,
            cluster_to_apps, topic_id_to_cluster, publishes, subscribes,
        )
        # The mesh is always drawn, so the main RNG stream -- and with it every
        # later draw -- is the same with or without a ``network:`` block.
        connects = self._wire_connects(nodes, c.connection_density)
        network_devices = None
        if c.network is not None:
            network_devices, connects = self._wire_fabric(
                nodes, c.network, random.Random(c.seed + self.FABRIC_SEED_OFFSET),
            )

        publishes, subscribes = self._apply_post_topology(
            apps, topics, publishes, subscribes, criticality_pool,
            hotstandby_pool=hotstandby_pool, runs_on=runs_on, nodes=nodes,
        )

        return self._assemble(
            c, nodes, brokers, topics, apps, libs,
            runs_on, routes, publishes, subscribes, connects, uses,
            network_devices=network_devices,
        )


def validate_and_clean_schema(graph_data: Dict[str, Any]) -> Dict[str, Any]:
    """Validate the schema of the generated graph and clean up duplicate edges.

    Ensures all necessary top-level fields are present, deduplicates all edges
    in the relationships block, and validates that all edge references point
    to existing entities.
    """
    # 1. Verify top-level structure
    required_keys = {"metadata", "nodes", "brokers", "topics", "applications", "libraries", "relationships"}
    for k in required_keys:
        if k not in graph_data:
            raise ValueError(f"Generated graph is missing required top-level key: '{k}'")

    # 2. Collect all valid entity IDs to check for duplicates and dangling edges
    valid_ids = set()
    categories = ["nodes", "brokers", "topics", "applications", "libraries", "network_devices"]
    for category in categories:
        for entity in graph_data.get(category, []):
            entity_id = entity.get("id")
            if not entity_id:
                raise ValueError(f"Entity in '{category}' is missing 'id' attribute: {entity}")
            if entity_id in valid_ids:
                raise ValueError(f"Duplicate entity ID found in generated graph: '{entity_id}'")
            valid_ids.add(entity_id)

    # 3. Deduplicate and validate relationships
    relationships = graph_data["relationships"]
    required_rels = {"runs_on", "routes", "publishes_to", "subscribes_to", "connects_to", "uses"}
    for rel_type in required_rels:
        if rel_type not in relationships:
            raise ValueError(f"Relationships dict is missing required key: '{rel_type}'")

        edges = relationships[rel_type]
        if not isinstance(edges, list):
            raise ValueError(f"Relationship key '{rel_type}' must map to a list of edges")

        seen_edges = set()
        deduped_edges = []
        for idx, edge in enumerate(edges):
            if not isinstance(edge, dict):
                raise ValueError(f"Edge at index {idx} in '{rel_type}' is not a dictionary")
            if "from" not in edge or "to" not in edge:
                raise ValueError(f"Edge at index {idx} in '{rel_type}' is missing 'from' or 'to' key")

            from_id = edge["from"]
            to_id = edge["to"]

            # Check for dangling reference
            if from_id not in valid_ids:
                raise ValueError(f"Edge '{from_id}' -> '{to_id}' in '{rel_type}' references non-existent source ID '{from_id}'")
            if to_id not in valid_ids:
                raise ValueError(f"Edge '{from_id}' -> '{to_id}' in '{rel_type}' references non-existent target ID '{to_id}'")

            # Deduplicate edge pair
            edge_key = (from_id, to_id)
            if edge_key not in seen_edges:
                seen_edges.add(edge_key)
                deduped_edges.append(edge)

        relationships[rel_type] = deduped_edges

    return graph_data
