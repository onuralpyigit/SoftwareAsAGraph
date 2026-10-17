"""
Core Value Objects and Entities
"""
from __future__ import annotations
import logging
import math
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Any, Optional, ClassVar

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Minimum weight floor for any topic, preventing zero-importance components.
MIN_TOPIC_WEIGHT: float = 0.01

#: Topic frequency bins (Hz) indexed by reliability×priority score [0,1].
#: The [0,1] range is split into 12 equal-width bins.
TOPIC_FREQUENCY_HZ: list = [
    1.0,     # [0.00, 0.06)
    1.0,     # [0.06, 0.13)
    10.0,    # [0.13, 0.19)
    10.0,    # [0.19, 0.25)
    10.0,    # [0.25, 0.31)
    25.0,    # [0.31, 0.38)
    25.0,    # [0.38, 0.44)
    50.0,    # [0.44, 0.50)
    50.0,    # [0.50, 0.56)
    100.0,   # [0.56, 0.62)
    100.0,   # [0.62, 0.69)
    100.0,   # [0.69, 0.75)
    100.0,   # [0.75, 0.81)
    200.0,   # [0.81, 0.88)
    200.0,   # [0.88, 0.94)
    200.0,   # [0.94, 1.00]
]
#: Thresholds for classifying a QoS weight score into criticality levels.
#: Sorted ascending; each entry is the *upper* bound of its band — the first
#: threshold the score does not exceed wins (``qos_score <= threshold``).
CRITICALITY_THRESHOLDS: list = [
    (0.35, "LOW"),
    (0.70, "MEDIUM"),
    (1.00, "HIGH"),
]

#: The three canonical criticality labels, ascending.  Both Topic.criticality
#: (QoS-channel urgency) and Application.criticality (process-level operational
#: criticality) use this vocabulary — they remain distinct *concepts* that must
#: not share a feature dimension (see saag/prediction/data_preparation.py).
CRITICALITY_LEVELS: tuple = ("LOW", "MEDIUM", "HIGH")

#: Legacy spellings accepted on input.  The retired 5-level Topic scale
#: (minimal/low/medium/high/critical) and the retired Application boolean both
#: fold onto the 3-level vocabulary here, in one place.
_CRITICALITY_ALIASES: Dict[str, str] = {
    "LOW": "LOW",
    "MEDIUM": "MEDIUM",
    "HIGH": "HIGH",
    "MINIMAL": "LOW",
    "CRITICAL": "HIGH",
    "NON_CRITICAL": "LOW",
    "TRUE": "HIGH",
    "FALSE": "LOW",
    "1": "HIGH",
    "0": "LOW",
    "YES": "HIGH",
    "NO": "LOW",
}


def canonical_criticality(value: Any, default: str = "MEDIUM") -> str:
    """Fold any accepted criticality spelling onto ``LOW``/``MEDIUM``/``HIGH``.

    Accepts the canonical labels in any case, the retired 5-level Topic scale,
    the retired ``Application.criticality`` boolean, and ``None`` (which yields
    *default*).  An unrecognised non-empty value is a data error rather than a
    silent MEDIUM: it is logged and then folded to *default* so a malformed
    dataset degrades visibly instead of quietly mislabelling every topic.
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return "HIGH" if value else "LOW"
    key = str(value).strip().upper()
    if not key:
        return default
    canon = _CRITICALITY_ALIASES.get(key)
    if canon is None:
        logger.warning(
            "Unrecognised criticality %r; falling back to %s. "
            "Expected one of %s or a documented legacy alias.",
            value, default, ", ".join(CRITICALITY_LEVELS),
        )
        return default
    return canon


#: Ordinal encoding of the Topic.criticality label produced by
#: CRITICALITY_THRESHOLDS. Lives in core because both the prediction feature
#: encoder and the simulation severity model read it, and simulation must not
#: import prediction (see tests/test_independence_guarantee.py).
#: Keyed by canonical label only — route lookups through canonical_criticality().
TOPIC_CRITICALITY_ORD: Dict[str, float] = {
    label: float(i) for i, label in enumerate(CRITICALITY_LEVELS)
}

#: Highest value in TOPIC_CRITICALITY_ORD, used to normalise it to [0, 1].
MAX_TOPIC_CRITICALITY_ORD: float = float(len(CRITICALITY_LEVELS) - 1)

#: Ordinal encoding of the Application.criticality operational label.
APP_CRITICALITY_ORD: Dict[str, float] = dict(TOPIC_CRITICALITY_ORD)

#: Highest value in APP_CRITICALITY_ORD, used to normalise it to [0, 1].
MAX_APP_CRITICALITY_ORD: float = MAX_TOPIC_CRITICALITY_ORD

#: DDS ``HistoryQosPolicy`` KEEP_LAST depth applied when a topic declares none.
#: Matches the OMG DDS specification's own default of 1 sample scaled to the
#: buffered-transport convention used across the corpus.
DEFAULT_HISTORY_DEPTH: int = 10

#: Convex combination factors for topic weight: β QoS + α Size + ψ Frequency.
#: Rationale: QoS semantics are the primary signal; payload size and message rate modulate runtime stress.
TOPIC_QOS_WEIGHT_BETA: float = 0.75
TOPIC_SIZE_WEIGHT_ALPHA: float = 0.15
TOPIC_FREQ_WEIGHT_PSI: float = 0.10

#: Payload design envelope for SizeNorm: log2-normalises raw message bytes
#: against a practical DDS sample ceiling before RTPS fragmentation dominates
#: (OMG DDSI-RTPS 2.3 §8.4.14). Replaces an earlier undocumented KiB-based
#: divisor of 50.0, which implied a ~1 EiB envelope and left SizeNorm — and
#: therefore alpha's declared 15% budget — realizing only ~2% of its share on
#: real payloads (see reproduce/weight_dispersion_diagnostic.py).
TOPIC_SIZE_ENVELOPE_BYTES: float = 2.0 ** 20
TOPIC_SIZE_NORM_DIVISOR: float = math.log2(1.0 + TOPIC_SIZE_ENVELOPE_BYTES)  # == 20.0

#: Publish-rate design envelope for FreqNorm: log10-normalises Hz against a
#: ~1 kHz ceiling. Chosen so log10(1 + envelope) == 3.0 exactly, reproducing
#: the previously unnamed literal divisor bit-for-bit — this is a naming
#: change only, not a rescale (unlike TOPIC_SIZE_ENVELOPE_BYTES above).
TOPIC_FREQ_ENVELOPE_HZ: float = 999.0
TOPIC_FREQ_NORM_DIVISOR: float = math.log10(1.0 + TOPIC_FREQ_ENVELOPE_HZ)  # == 3.0

#: Declared default publish rate used only when a topic supplies no frequency.
#: The prior fallback derived a rate from the topic's own reliability x
#: priority score, feeding a QoS-derived value back into the nominally
#: independent frequency term of w(t). A single declared constant removes
#: that circularity; every scenario in the committed corpus supplies an
#: explicit frequency, so this path is not exercised in practice.
TOPIC_DEFAULT_FREQUENCY_HZ: float = 1.0

#: AHP Shrinkage Factor (λ) for weight distribution smoothing.
#: Applied as: w_final = λ * w_ahp + (1 - λ) * w_uniform.
#: Rationale: Blends expert AHP judgment with a uniform prior to reduce over-fitting/bias.
AHP_SHRINKAGE_LAMBDA: float = 0.70

#: Power mean exponent (p) for smooth worst-case component aggregation
COMPONENT_POWER_MEAN_P: float = 3.0

#: Library fan-out multiplier coefficient (γ) for simultaneous blast semantics.
#: Applied as: 1 + γ * log2(1 + DG_in).
#: Rationale for magnitude: at realistic fan-out (single digits to low tens of
#: consuming apps, DG_in ≈ 5-30), log2(1+DG_in) ≈ 2.6-5.0, so γ=0.15 yields a
#: ~40-75% amplification — noticeable but not dominant, keeping w(lib) well
#: under the min(1.0, ...) ceiling for all but extreme fan-out.
LIB_FANOUT_GAMMA: float = 0.15

#: Regularization coefficient (δ) for path count coupling complexity.
#: Applied as: CR_enriched = CR_base * (1 + δ * path_complexity).
COUPLING_PATH_DELTA: float = 0.10


def compute_effective_edge_weight(weights: List[float]) -> float:
    """Compute multi-topic effective coupling weight via probabilistic union.

    w_E(u -> v) = 1 - ∏_{t ∈ T} (1 - w(t))

    Ensures that multiple parallel failure vectors increase coupling monotonically
    with path_count while preserving the w_E ∈ [0, 1) contract.
    """
    if not weights:
        return MIN_TOPIC_WEIGHT
    prod = 1.0
    for w in weights:
        clamped_w = max(0.0, min(1.0, float(w)))
        prod *= (1.0 - clamped_w)
    effective = 1.0 - prod
    return max(MIN_TOPIC_WEIGHT, min(1.0, effective))


def compute_harmonic_coupling(w1: float, w2: float) -> float:
    """Compute harmonic coupling between consumer and dependency:

    w_E = 2 * (w1 * w2) / (w1 + w2)

    Calibrates caller operational criticality against shared dependency criticality
    for simultaneous blast edges (Rule 5: app_to_lib).
    """
    if w1 <= 0.0 or w2 <= 0.0:
        return MIN_TOPIC_WEIGHT
    harmonic = 2.0 * (w1 * w2) / (w1 + w2)
    return max(MIN_TOPIC_WEIGHT, min(1.0, harmonic))


def compute_lifted_edge_weight(weights: List[float]) -> float:
    """Compute the worst-case lift for Rules 3-4 (node_to_node, node_to_broker).

    w_E = max(w_1, ..., w_n)

    ``compute_effective_edge_weight``'s probabilistic union assumes the events
    it combines are independent (Pearl 1988). Rules 3-4 lift dependencies that
    are already aggregated over overlapping sets of hosted apps and mediating
    topics, so that assumption fails, and the union saturates: measured at
    91-100% of node_to_node edges >= 0.95 on the committed corpus. Worst-case
    propagation is the correct operator for a lifted dependency — the lifting
    node is exposed to the single most critical dependency any hosted
    component carries, not to the union of all of them.
    """
    if not weights:
        return MIN_TOPIC_WEIGHT
    return max(MIN_TOPIC_WEIGHT, min(1.0, max(float(w) for w in weights)))


def compute_power_mean_weight(weights: List[float], p: Optional[float] = None) -> float:
    """Compute Generalized Power Mean (p=3) for component vertex aggregation.

    w_p(v) = ( (1 / |T|) * ∑_{t ∈ T} w(t)^p )^(1/p)

    Provides smooth, scale-free approximation to worst-case topic exposure while
    penalizing components with multiple critical topic attachments.

    ``p`` resolves to ``COMPONENT_POWER_MEAN_P`` at call time when not given
    explicitly, rather than being bound as an ordinary default argument —
    a default argument is evaluated once, at function-definition time, so
    binding it directly to ``COMPONENT_POWER_MEAN_P`` silently froze every
    caller that omits ``p`` (i.e. every caller in this codebase) to whatever
    value the constant held when this module was first imported, making the
    "constant" un-patchable at runtime unlike every other one in this file.
    """
    if p is None:
        p = COMPONENT_POWER_MEAN_P
    if not weights:
        return MIN_TOPIC_WEIGHT
    n = len(weights)
    sum_pow = sum(math.pow(max(0.0, float(w)), p) for w in weights)
    mean_pow = sum_pow / n
    return max(MIN_TOPIC_WEIGHT, min(1.0, math.pow(mean_pow, 1.0 / p)))


@dataclass
class ComponentData:
    """Domain entity representing a graph component (vertex)."""
    id: str
    component_type: str
    weight: float = 1.0
    properties: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": self.component_type,
            "weight": self.weight,
            **self.properties,
        }


@dataclass
class EdgeData:
    """Domain entity representing a graph edge (dependency)."""
    source_id: str
    target_id: str
    source_type: str
    target_type: str
    dependency_type: str
    relation_type: str
    weight: float = 1.0
    path_count: int = 1
    properties: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source_id,
            "target": self.target_id,
            "source_type": self.source_type,
            "target_type": self.target_type,
            "dependency_type": self.dependency_type,
            "relation_type": self.relation_type,
            "weight": self.weight,
            **self.properties,
        }


@dataclass
class GraphData:
    """Domain entity representing a complete graph with components and edges."""
    components: List[ComponentData] = field(default_factory=list)
    edges: List[EdgeData] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "components": [c.to_dict() for c in self.components],
            "edges": [e.to_dict() for e in self.edges],
        }
    
    def get_components_by_type(self, comp_type: str) -> List[ComponentData]:
        return [c for c in self.components if c.component_type == comp_type]
    
    def get_edges_by_type(self, dep_type: str) -> List[EdgeData]:
        return [e for e in self.edges if e.dependency_type == dep_type]


@dataclass
class QoSPolicy:
    """
    Defines Quality of Service attributes for a Topic.
    """
    # QoS scoring constants - centralized for use in both Python and Cypher
    RELIABILITY_SCORES: ClassVar[Dict[str, float]] = {
        "BEST_EFFORT": 0.0,
        "RELIABLE": 1.0,  # Full weight if reliable
    }
    DURABILITY_SCORES: ClassVar[Dict[str, float]] = {
        "VOLATILE": 0.0,
        "TRANSIENT_LOCAL": 0.5,
        "TRANSIENT": 0.6,
        "PERSISTENT": 1.0,
    }
    # CRITICAL/HIGHEST are aliases for the top tier. They must be listed here:
    # the Cypher and in-memory scorers special-case them to 1.0, so omitting them
    # made this scorer rank a CRITICAL topic identically to a LOW one.
    PRIORITY_SCORES: ClassVar[Dict[str, float]] = {
        "LOW": 0.0,
        "MEDIUM": 0.33,
        "HIGH": 0.66,
        "URGENT": 1.0,
        "CRITICAL": 1.0,
        "HIGHEST": 1.0,
    }
    
    # Justification (AHP): Durability (0.62) > Reliability (0.24) > Priority (0.14).
    # Rationale: in DDS systems, durability governs whether data survives at
    # all (state persistence across restarts/partitions), which is why it
    # dominates; reliability (an unconditional delivery guarantee) and
    # transport priority (relative scheduling under contention) both govern
    # in-flight delivery quality rather than survival, with reliability
    # weighing somewhat more since a guarantee precedes the scheduling of it.
    # This is the geometric-mean priority vector of an independently-stated
    # Saaty matrix (AHPMatrices.criteria_topic_qos, CR ~= 0.016 — see that
    # matrix's docstring for why a small nonzero CR is the honest result here,
    # not a defect). Enforced by
    # tests/test_ahp_shrinkage.py::test_topic_qos_matrix_reproduces_shipped_weights.
    W_RELIABILITY: ClassVar[float] = 0.24
    W_DURABILITY: ClassVar[float] = 0.62
    W_PRIORITY: ClassVar[float] = 0.14

    durability: str = "VOLATILE"
    reliability: str = "BEST_EFFORT"
    transport_priority: str = "MEDIUM"
    deadline_ms: Optional[float] = None
    history_depth: int = DEFAULT_HISTORY_DEPTH

    def to_dict(self) -> Dict[str, Any]:
        # Both temporal-contract keys are emitted unconditionally, matching
        # Topic.to_dict(): a conditional key made "declared 10" and "defaulted
        # 10" indistinguishable on the round-trip.
        return {
            "durability": self.durability,
            "reliability": self.reliability,
            "transport_priority": self.transport_priority,
            "deadline_ms": self.deadline_ms,
            "history_depth": self.history_depth,
        }

    @staticmethod
    def _canon(value: Any, default: str) -> str:
        """Canonicalise a raw QoS enum value: trim whitespace, upper-case.

        Scenario generators and hand-authored topologies mix case
        (``"reliable"`` vs ``"RELIABLE"``); the score tables are upper-case
        only, so an unnormalised lookup silently fell through to 0.0 —
        scoring a lowercase-authored CRITICAL/PERSISTENT/RELIABLE topic
        identically to a LOW/VOLATILE/BEST_EFFORT one.
        """
        return str(value).strip().upper() if value else default

    @staticmethod
    def _first_present(*values: Any) -> Any:
        """Return the first argument that is not ``None``.

        Distinct from ``or``-chaining, which would discard a legitimate 0.
        """
        for value in values:
            if value is not None:
                return value
        return None

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "QoSPolicy":
        deadline = QoSPolicy._first_present(data.get("deadline_ms"), data.get("deadline"))
        history = data.get("history_depth")
        return QoSPolicy(
            durability=QoSPolicy._canon(data.get("durability"), "VOLATILE"),
            reliability=QoSPolicy._canon(data.get("reliability"), "BEST_EFFORT"),
            transport_priority=QoSPolicy._canon(data.get("transport_priority"), "MEDIUM"),
            deadline_ms=float(deadline) if deadline is not None else None,
            history_depth=int(history) if history is not None else DEFAULT_HISTORY_DEPTH,
        )

    @staticmethod
    def from_node_attrs(attrs: Dict[str, Any]) -> "QoSPolicy":
        """Resolve a policy from Topic graph-node attributes in either shape.

        Topic nodes reach consumers in two forms and neither is wrong:

        * **flat** — ``qos_reliability`` / ``qos_durability`` /
          ``qos_transport_priority``, written by the repositories and the
          serializer;
        * **nested** — a ``qos`` sub-dict, which is how raw topology JSON is
          shaped and how the ``cli`` research loaders pass it through.

        Reading only one shape silently yields the defaults on the other, which
        is how the simulation engines came to be QoS-blind on the research path.
        ``qos_priority`` is accepted as a legacy alias for the priority key.
        """
        nested = attrs.get("qos") or attrs.get("qos_policy") or {}
        deadline = QoSPolicy._first_present(
            attrs.get("deadline_ms"),
            attrs.get("qos_deadline_ms"),
            nested.get("deadline_ms"),
            nested.get("deadline"),
        )
        history = QoSPolicy._first_present(
            attrs.get("history_depth"),
            attrs.get("qos_history_depth"),
            nested.get("history_depth"),
        )
        return QoSPolicy(
            durability=QoSPolicy._canon(
                attrs.get("qos_durability") or nested.get("durability"), "VOLATILE"
            ),
            reliability=QoSPolicy._canon(
                attrs.get("qos_reliability") or nested.get("reliability"), "BEST_EFFORT"
            ),
            transport_priority=QoSPolicy._canon(
                attrs.get("qos_transport_priority")
                or attrs.get("qos_priority")
                or nested.get("transport_priority")
                or nested.get("priority"),
                "MEDIUM",
            ),
            deadline_ms=float(deadline) if deadline is not None else None,
            history_depth=int(history) if history is not None else DEFAULT_HISTORY_DEPTH,
        )

    def calculate_weight(self) -> float:
        """
        Calculates the weighted QoS score based on AHP-derived coefficients.

        QoS = 0.24*Rel + 0.62*Dur + 0.14*Pri

        Lookups are case-normalised (see ``_canon``) so a policy built by
        direct construction with a lowercase value — bypassing ``from_dict``/
        ``from_node_attrs`` — still scores correctly rather than silently
        falling through to 0.0.
        """
        s_reliability = self.RELIABILITY_SCORES.get(self._canon(self.reliability, ""), 0.0)
        s_durability = self.DURABILITY_SCORES.get(self._canon(self.durability, ""), 0.0)
        s_priority = self.PRIORITY_SCORES.get(self._canon(self.transport_priority, ""), 0.0)

        return (
            self.W_RELIABILITY * s_reliability +
            self.W_DURABILITY * s_durability +
            self.W_PRIORITY * s_priority
        )

@dataclass
class GraphEntity:
    """Base entity with identity. All graph vertices extend this."""
    id: str
    name: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class Application(GraphEntity):
    """A software service that publishes and/or subscribes to topics.

    Attributes:
        system_hierarchy: Decomposition (csms_name, css_name, csci_name, csc_name).
        code_metrics: Nested OO metrics (size, complexity, cohesion, coupling).
    """
    app_type: str = "service"
    role: List[str] = field(default_factory=lambda: ["Operative"])
    criticality: str = "MEDIUM"  # HIGH, MEDIUM, LOW
    hotstandby: bool = False  # true = runs on 2 distinct nodes
    version: Optional[str] = None
    system_hierarchy: Optional[Dict[str, str]] = None
    code_metrics: Optional[Dict[str, Any]] = None

    def __post_init__(self) -> None:
        # ``criticality`` was a bool before the 3-tier migration; legacy
        # datasets and hand-authored fixtures still carry that shape.
        self.criticality = canonical_criticality(self.criticality)

        if isinstance(self.hotstandby, str):
            self.hotstandby = self.hotstandby.strip().lower() in ("true", "1", "yes")
        else:
            self.hotstandby = bool(self.hotstandby)

    # --- backward-compatible computed properties for analysis pipeline ---

    @property
    def loc(self) -> int:
        if self.code_metrics:
            return self.code_metrics.get("size", {}).get("total_loc", 0)
        return 0

    @property
    def cyclomatic_complexity(self) -> float:
        if self.code_metrics:
            return float(self.code_metrics.get("complexity", {}).get("avg_wmc", 0.0))
        return 0.0

    @property
    def lcom(self) -> float:
        if self.code_metrics:
            return float(self.code_metrics.get("cohesion", {}).get("avg_lcom", 0.0))
        return 0.0

    @property
    def coupling_afferent(self) -> int:
        if self.code_metrics:
            return int(self.code_metrics.get("coupling", {}).get("avg_fanin", 0))
        return 0

    @property
    def coupling_efferent(self) -> int:
        if self.code_metrics:
            return int(self.code_metrics.get("coupling", {}).get("avg_fanout", 0))
        return 0

    @property
    def instability(self) -> float:
        """Martin Instability I = Ce / (Ca + Ce) ∈ [0, 1]."""
        total = self.coupling_afferent + self.coupling_efferent
        return self.coupling_efferent / total if total > 0 else 0.0

    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "app_type": self.app_type,
            "role": self.role,
            "criticality": self.criticality,
            "hotstandby": self.hotstandby,
            "system_hierarchy": self.system_hierarchy,
            "code_metrics": self.code_metrics,
        }
        return result

@dataclass
class Broker(GraphEntity):
    """A message broker routing messages."""
    pass

@dataclass
class Node(GraphEntity):
    """A compute node hosting applications and/or brokers."""
    pass

@dataclass
class NetworkDevice(GraphEntity):
    """A switch or router that host Nodes reach each other through (CONNECTS_TO)."""
    role: str = "switch"

@dataclass
class Topic(GraphEntity):
    """A named channel for message exchange with QoS policies.

    Attributes:
        size: Message size in bytes.
        qos: QoS policy governing reliability, durability, and priority.
        frequency: Message frequency in Hz.  When supplied explicitly by the
            generator (per-domain log-uniform sample), that value is kept as-is
            so that domain signal is preserved across LOSO folds.  When
            *None* (the default), frequency is derived from the
            reliability × priority score via ``TOPIC_FREQUENCY_HZ`` bins.
        criticality: Criticality label (``critical`` / ``high`` / ``medium`` /
            ``low`` / ``minimal``).  When supplied explicitly by the generator
            (rule-derived label ± noise injection), that value is kept as-is.
            When *None* (the default), it is derived deterministically from the
            QoS weight score via ``CRITICALITY_THRESHOLDS``.

    Note on leakage prevention:
        Passing ``frequency`` and ``criticality`` from the generator breaks the
        closed-form QoS→label mapping that would otherwise make the prediction
        task trivially solvable from QoS attributes alone.  The generator is
        responsible for (a) sampling frequency from per-domain log-uniform
        distributions and (b) injecting ~15–20 % label noise into criticality
        so that the GNN must use graph-structural context to recover it.
    """
    size: int = 256
    qos: QoSPolicy = field(default_factory=QoSPolicy)
    # Optional generator-supplied overrides.  ``None`` triggers QoS-derived fallback.
    frequency: Optional[float] = field(default=None)
    criticality: Optional[str] = field(default=None)
    deadline_ms: Optional[float] = field(default=None)
    #: ``None`` means "not declared at Topic level" — the value then falls back
    #: to ``qos.history_depth`` and finally to DEFAULT_HISTORY_DEPTH.
    history_depth: Optional[int] = field(default=None)

    def __post_init__(self) -> None:
        # Enforce size is a power of 2
        import math
        if self.size is not None:
            sz = max(1.0, float(self.size))
            self.size = int(2 ** round(math.log2(sz)))

        # --- frequency ---------------------------------------------------
        # If the generator did not supply a value, fall back to the legacy
        # reliability × priority bin-lookup so existing callers are unaffected.
        if self.frequency is None:
            score_map = QoSPolicy.RELIABILITY_SCORES
            r = score_map.get(self.qos.reliability, 0.0)
            p = QoSPolicy.PRIORITY_SCORES.get(self.qos.transport_priority, 0.0)
            combined = r * p  # range [0, 1]
            bin_idx = int(combined * len(TOPIC_FREQUENCY_HZ))
            bin_idx = max(0, min(bin_idx, len(TOPIC_FREQUENCY_HZ) - 1))
            self.frequency = float(TOPIC_FREQUENCY_HZ[bin_idx])

        # Enforce frequency is in [1, 10, 25, 50, 100, 200]
        if self.frequency is not None:
            ALLOWED_FREQS = [1.0, 10.0, 25.0, 50.0, 100.0, 200.0]
            self.frequency = float(min(ALLOWED_FREQS, key=lambda x: abs(x - self.frequency)))

        # --- criticality -------------------------------------------------
        # If the generator did not supply a value, derive it deterministically
        # from the QoS weight so existing callers are unaffected.
        if self.criticality is None:
            qos_score = self.qos.calculate_weight()
            for threshold, label in CRITICALITY_THRESHOLDS:
                if qos_score <= threshold:
                    self.criticality = label
                    break
            else:
                self.criticality = "HIGH"
        else:
            self.criticality = canonical_criticality(self.criticality)

        # --- temporal contract (deadline_ms / history_depth) --------------
        # The Topic is the single owner: an explicitly declared Topic-level
        # value wins, otherwise the value falls back to the QoS policy, and
        # the resolved result is pushed back down so both views agree.  The
        # earlier bidirectional sync used ``== 10`` as an "unset" sentinel,
        # which silently overwrote a topic that genuinely declared a depth
        # of 10.
        if self.deadline_ms is None:
            self.deadline_ms = self.qos.deadline_ms
        if self.deadline_ms is not None:
            try:
                self.deadline_ms = max(0.001, float(self.deadline_ms))
            except (ValueError, TypeError):
                self.deadline_ms = None
        self.qos.deadline_ms = self.deadline_ms

        if self.history_depth is None:
            self.history_depth = self.qos.history_depth
        try:
            self.history_depth = max(1, int(self.history_depth))
        except (ValueError, TypeError):
            self.history_depth = DEFAULT_HISTORY_DEPTH
        self.qos.history_depth = self.history_depth

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "size": self.size,
            "qos": self.qos.to_dict(),
            "frequency": self.frequency,
            "criticality": self.criticality,
            "deadline_ms": self.deadline_ms,
            "history_depth": self.history_depth,
        }
    
    def calculate_weight(self) -> float:
        """
        Topic importance = β * QoS_Score + α * Size_Norm + ψ * Freq_Norm.
        
        Refined Size & Frequency Norm: 
        - Logarithmic scaling for payload size and publish rate.
        - Convex combination ensures w(topic) ∈ [0, 1].
        """
        return compute_topic_weight(self.qos, self.size, frequency=self.frequency)


def compute_size_norm(size: float) -> float:
    """SizeNorm(t) = min(1.0, log2(1 + size_bytes) / log2(1 + envelope)).

    Extracted from ``compute_topic_weight`` so the realized-dynamic-range
    diagnostic (``reproduce/weight_dispersion_diagnostic.py``) can measure this
    term without a parallel reimplementation that could drift from it.
    """
    size_bytes = max(0.0, float(size))
    return min(math.log2(1.0 + size_bytes) / TOPIC_SIZE_NORM_DIVISOR, 1.0)


def compute_freq_norm(frequency: Optional[float]) -> float:
    """FreqNorm(t) = min(1.0, log10(1 + f) / log10(1 + envelope)).

    Falls back to ``TOPIC_DEFAULT_FREQUENCY_HZ`` — a declared constant — when
    no frequency is supplied, rather than deriving one from the topic's own
    reliability x priority score. The QoS score already carries that signal;
    re-deriving a frequency from it made the frequency term partially
    re-encode the QoS term it is supposed to be independent of.
    """
    f = float(frequency) if frequency and frequency > 0 else TOPIC_DEFAULT_FREQUENCY_HZ
    return min(math.log10(1.0 + f) / TOPIC_FREQ_NORM_DIVISOR, 1.0)


def compute_topic_weight(
    qos: "QoSPolicy",
    size: int,
    frequency: Optional[float] = None,
) -> float:
    """w(t) = β·QoS_score + α·size_norm + ψ·freq_norm, floored at MIN_TOPIC_WEIGHT.

    Free function so callers holding raw graph attributes rather than a
    :class:`Topic` can reach the same formula the repositories use.
    """
    qos_score = qos.calculate_weight()
    size_norm = compute_size_norm(size)
    freq_norm = compute_freq_norm(frequency)

    weight = (
        TOPIC_QOS_WEIGHT_BETA * qos_score +
        TOPIC_SIZE_WEIGHT_ALPHA * size_norm +
        TOPIC_FREQ_WEIGHT_PSI * freq_norm
    )
    return max(MIN_TOPIC_WEIGHT, min(1.0, weight))


def topic_weight_from_node_attrs(attrs: Dict[str, Any]) -> float:
    """Compute w(t) straight from Topic graph-node attributes (either QoS shape).

    Accepts both ``size`` and ``message_size`` for the payload key, matching the
    two spellings already in circulation between the topology JSON and the
    simulation graph.
    """
    size = attrs.get("size", attrs.get("message_size", 1024)) or 1024
    freq = attrs.get("frequency")
    return compute_topic_weight(QoSPolicy.from_node_attrs(attrs), int(size), frequency=freq)

@dataclass
class Library(GraphEntity):
    """A reusable code component (shared library, SDK, framework, driver, etc.).

    Attributes:
        system_hierarchy: Decomposition (csms_name, css_name, csci_name, csc_name).
        code_metrics: Nested OO metrics (size, complexity, cohesion, coupling).
    """
    version: Optional[str] = None
    system_hierarchy: Optional[Dict[str, str]] = None
    code_metrics: Optional[Dict[str, Any]] = None

    # --- backward-compatible computed properties for analysis pipeline ---

    @property
    def loc(self) -> int:
        if self.code_metrics:
            return self.code_metrics.get("size", {}).get("total_loc", 0)
        return 0

    @property
    def cyclomatic_complexity(self) -> float:
        if self.code_metrics:
            return float(self.code_metrics.get("complexity", {}).get("avg_wmc", 0.0))
        return 0.0

    @property
    def lcom(self) -> float:
        if self.code_metrics:
            return float(self.code_metrics.get("cohesion", {}).get("avg_lcom", 0.0))
        return 0.0

    @property
    def coupling_afferent(self) -> int:
        if self.code_metrics:
            return int(self.code_metrics.get("coupling", {}).get("avg_fanin", 0))
        return 0

    @property
    def coupling_efferent(self) -> int:
        if self.code_metrics:
            return int(self.code_metrics.get("coupling", {}).get("avg_fanout", 0))
        return 0

    @property
    def instability(self) -> float:
        """Martin Instability I = Ce / (Ca + Ce) ∈ [0, 1]."""
        total = self.coupling_afferent + self.coupling_efferent
        return self.coupling_efferent / total if total > 0 else 0.0

    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "system_hierarchy": self.system_hierarchy,
            "code_metrics": self.code_metrics,
        }
        return result

@dataclass
class GraphSummary:
    """Summary of graph structural properties."""
    nodes: int = 0
    edges: int = 0
    density: float = 0.0
    num_components: int = 0
    num_articulation_points: int = 0
    node_types: Dict[str, int] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)