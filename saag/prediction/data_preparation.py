"""
GNN Data Preparation
====================
Converts the Software-as-a-Graph framework's internal representations into
PyTorch Geometric (PyG) HeteroData objects suitable for training and inference.

Design principles
-----------------
* **Heterogeneous-first**: The pub-sub multi-layer graph has five distinct
  node types (Application, Broker, Topic, Node, Library) and seven edge types
  (PUBLISHES_TO, SUBSCRIBES_TO, ROUTES, RUNS_ON, CONNECTS_TO, USES,
  DEPENDS_ON).  All type information is preserved as separate PyG stores.

* **Feature parity with RM**: The 13 topological metrics that feed the RM
  quality scorer are reused directly as node features so GNN predictions are
  directly comparable to RM predictions under the same validation protocol.

* **Runtime-enriched features**: Broker, Topic, and Node types carry
  additional infrastructure/runtime features (max_connections, subscriber/
  publisher counts, cpu_cores, memory_gb) beyond the base topology metrics.

* **Multi-task labels**: All five simulation ground-truth dimensions
  (I*(v), IR(v), IM(v), IA(v), IV(v)) are stored as multi-column label
  tensors, enabling multi-task learning or single-task ablations.

Architectural decision: Application.criticality vs Topic.topic_qos_criticality
------------------------------------------------------------------------------
Two fields named ``criticality`` exist in the system, and they are *not* the
same concept.  They must NOT share a feature dimension:

  ``Application.criticality`` (str: LOW/MEDIUM/HIGH)
      Process-level operational criticality. Represents whether the *service*
      is mission-critical. Set by the two-pass topology and QoS-correlated
      assignment in the generator.

  ``Topic.criticality`` (str: LOW/MEDIUM/HIGH)
      QoS-channel urgency, derived from the QoS weight score
      (0.24·Rel + 0.62·Dur + 0.14·Pri) with ≈17% label-noise injection
      so the GNN must use graph context to resolve ambiguous cases.
      Encoded as an ordinal integer 0–2 (``topic_qos_criticality_ord``).
      Appears **only** in the Topic NodeStorage (dim 21) — it is absent
      from Application, Broker, Node, and Library stores.

Consequences:
  * No feature-dimension collision in the heterograph.
  * Attention heads on the PUBLISHES_TO / SUBSCRIBES_TO bipartite
    subgraph can independently learn what frequency and QoS urgency
    contribute to topic-level impact, without entangling the boolean
    service-criticality signal from the Application store.
  * Frequency normalization is always **per-scenario** (z-score of
    log1p(Hz) over the Topic nodes present in the current graph) so
    the normalizer does not leak scenario identity across LOSO folds.

Node feature vector (heterogeneous)
------------------------------------
Each node type HAS ITS OWN feature dimension.

Base Topological Metrics (dim = 18) - all node types:
  0  PageRank (PR)
  1  Reverse PageRank (RPR)
  2  Betweenness Centrality (BT)
  3  Closeness Centrality (CL)
  4  Eigenvector Centrality (EV)
  5  In-Degree normalised (DG_in)
  6  Out-Degree normalised (DG_out)
  7  Clustering Coefficient (CC)
  8  AP_c undirected
  9  Bridge Ratio (BR)
 10  QoS aggregate weight (w)
 11  QoS weighted in-degree (w_in)
 12  QoS weighted out-degree (w_out)
 13  MPCI
 14  Path Complexity (path_complexity)
 15  Fan-Out Criticality (FOC)
 16  AP_c directed
 17  CDI

Code Quality Metrics (dim = 5) - Application and Library ONLY (indices 18-22):
 18  Normalised LOC (loc_norm)
 19  Normalised Complexity (complexity_norm)
 20  Instability I = Ce/(Ca+Ce) (instability_code)
 21  Normalised LCOM (lcom_norm)
 22  Code Quality Penalty (CQP)

Infrastructure/Runtime Extras:
  Broker (index 18):   max_connections_norm
  Node   (18, 19):     cpu_cores_norm, memory_gb_norm
  Topic  (18–21):      subscriber_count_norm, publisher_count_norm,
                       log1p_frequency_norm (per-scenario z-score),
                       topic_qos_criticality_ord (ordinal 0–4)

Total dimensions:
  Application:          23
  Library:              25   ← +2 (reverse-USES closure reach + downstream subscribers)
  Broker:               19
  Topic:                22   ← +2 vs previous (frequency + QoS criticality)
  Node:                 20

Edge feature vector (dim = 16)   [expanded for Middleware 2026 Q-HGL]
------------------------------------------------------------------------
  0   QoS aggregate weight w(e)  (normalised scalar, unchanged)
  1   path_count_norm            (log2-scaled coupling intensity, unchanged)
  2–8 Edge-type one-hot          (PUBLISHES_TO … DEPENDS_ON, 7 dims, unchanged)
  9   reliability_score          (0.0 BEST_EFFORT / 1.0 RELIABLE)
  10  durability_score           (VOLATILE=0.0 / TRANSIENT_LOCAL=0.5 /
                                  TRANSIENT=0.6 / PERSISTENT=1.0)
  11  priority_score             (LOW=0.0 / MEDIUM=0.33 / HIGH=0.66 / URGENT=1.0)
  12  has_deadline               (1.0 if a finite deadline_ns is set, else 0.0)
  13  deadline_ns_log            (log10(1 + deadline_ns / 1e6), 0.0 if absent)
  14  max_blocking_ms_log        (log10(1 + max_blocking_ms), 0.0 if absent)
  15  qos_heterogeneity_flag     (1.0 if topic QoS differs from scenario-level
                                  majority profile, else 0.0)

Dims 9–15 are non-zero only for PUBLISHES_TO / SUBSCRIBES_TO edges, where
QoS profiles are semantically meaningful.  All other edge types receive zeros
for these dimensions, preserving backward numerical compatibility.
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

import networkx as nx
import numpy as np
import torch
from scipy.stats import rankdata
from torch import Tensor

from saag.core.models import (
    QoSPolicy,
    TOPIC_CRITICALITY_ORD as _TOPIC_CRITICALITY_ORD,
    MAX_TOPIC_CRITICALITY_ORD,
    canonical_criticality,
)
from saag.core.network import NETWORK_TYPES, Fabric, NetworkState, baseline_load, link_key

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

# NetworkDevice is appended, not inserted: a graph without switches/routers
# yields exactly the tensors and HGT metadata it did before it existed.
NODE_TYPES: List[str] = ["Application", "Broker", "Topic", "Node", "Library", "NetworkDevice"]
EDGE_TYPES: List[str] = [
    "PUBLISHES_TO",
    "SUBSCRIBES_TO",
    "ROUTES",
    "RUNS_ON",
    "CONNECTS_TO",
    "USES",
    "DEPENDS_ON",
]

NODE_TYPE_INDEX: Dict[str, int] = {t: i for i, t in enumerate(NODE_TYPES)}
EDGE_TYPE_INDEX: Dict[str, int] = {t: i for i, t in enumerate(EDGE_TYPES)}

# Base topological metrics applicable to all node types (18 dims)
BASE_METRIC_KEYS: List[str] = [
    "pagerank",              # 0
    "reverse_pagerank",      # 1
    "betweenness_centrality", # 2
    "closeness_centrality",   # 3
    "eigenvector_centrality", # 4
    "in_degree_centrality",   # 5
    "out_degree_centrality",  # 6
    "clustering_coefficient", # 7
    "ap_c_score",            # 8
    "bridge_ratio",          # 9
    "qos_weight",            # 10
    "qos_weight_in",         # 11
    "qos_weight_out",        # 12
    "mpci",                  # 13
    "path_complexity",       # 14
    "fan_out_criticality",   # 15
    "ap_c_directed",         # 16
    "cdi",                   # 17
]

# Code quality metrics for Application and Library (appended to base, indices 18-22)
CQ_METRIC_KEYS: List[str] = [
    "loc_norm",              # 18
    "complexity_norm",       # 19
    "instability_code",      # 20
    "lcom_norm",             # 21
    "code_quality_penalty",  # 22
]

TOPOLOGICAL_METRIC_KEYS = BASE_METRIC_KEYS + CQ_METRIC_KEYS  # 23 dims for App/Lib

# Infrastructure/runtime extra feature keys (appended to BASE_METRIC_KEYS per type)
NODE_INFRA_KEYS: List[str] = ["cpu_cores_norm", "memory_gb_norm"]      # indices 18-19
BROKER_EXTRA_KEYS: List[str] = ["max_connections_norm"]                  # index 18
# Topic-type-only extras (indices 18-21).
# IMPORTANT: these keys must NEVER appear in Application/Broker/Node/Library
# feature dicts — doing so would create a spurious shared embedding dimension
# between semantically distinct node types.  See the module docstring for the
# Application.criticality vs Topic.topic_qos_criticality design decision.
TOPIC_RUNTIME_KEYS: List[str] = [
    "subscriber_count_norm",       # 18 — pub/sub fan-in (topology)
    "publisher_count_norm",        # 19 — pub/sub fan-out (topology)
    "log1p_frequency_norm",        # 20 — per-scenario z-score of log1p(Hz)
    "topic_qos_criticality_ord",   # 21 — QoS urgency ordinal {0=minimal…4=critical}
]

# NetworkDevice-only extras (indices 18-19), from the baseline routing of the
# pub-sub flows over CONNECTS_TO (saag.core.network.baseline_load).
NETWORK_DEVICE_KEYS: List[str] = [
    "link_utilization",    # 18 — worst offered-load / capacity over its links, clipped to [0, 1]
    "transit_load_norm",   # 19 — traffic on its links, per-graph max-normalized over devices
]

# Ordinal mapping for Topic.criticality labels (5-level QoS urgency scale).
# Separate from Application.criticality which is a bool (process-level ground truth).
# Defined in saag.core.models so the simulation severity model can share it.
TOPIC_CRITICALITY_ORD: Dict[str, float] = _TOPIC_CRITICALITY_ORD

# Library-only extras (indices 23-24): causal drivers of a library's blast
# radius under both simulators' rules (transitive USES-consumer failure),
# which the code-quality metrics in CQ_METRIC_KEYS do not capture. See
# _normalize_infra_features's Library branch for how these are computed.
LIBRARY_EXTRA_KEYS: List[str] = [
    "library_uses_reach_norm",         # 23 — |transitive reverse-USES closure|, per-graph max-normalized
    "library_downstream_subs_norm",    # 24 — distinct subscribers reachable from that closure's
                                        #      published topics, per-graph max-normalized
]

# Per-type feature key mapping used during feature extraction
KEYS_BY_TYPE: Dict[str, List[str]] = {
    "Application": TOPOLOGICAL_METRIC_KEYS,
    # A separate list, not TOPOLOGICAL_METRIC_KEYS + LIBRARY_EXTRA_KEYS in place —
    # Application and Library used to share the same TOPOLOGICAL_METRIC_KEYS list
    # object; appending here would have silently widened Application too.
    "Library":     TOPOLOGICAL_METRIC_KEYS + LIBRARY_EXTRA_KEYS,
    "Broker":      BASE_METRIC_KEYS + BROKER_EXTRA_KEYS,
    "Topic":       BASE_METRIC_KEYS + TOPIC_RUNTIME_KEYS,
    "Node":        BASE_METRIC_KEYS + NODE_INFRA_KEYS,
    "NetworkDevice": BASE_METRIC_KEYS + NETWORK_DEVICE_KEYS,
}

NODE_TYPE_TO_DIM: Dict[str, int] = {
    "Application": 23,
    "Library":     25,   # +2: library_uses_reach_norm, library_downstream_subs_norm
    "Broker":      19,   # +1: max_connections_norm
    "Topic":       22,   # +4: subscriber_count_norm, publisher_count_norm,
                         #      log1p_frequency_norm, topic_qos_criticality_ord
    "Node":        20,   # +2: cpu_cores_norm, memory_gb_norm
}

#: Widths of node types that only exist when a topology models its network.
#: Kept out of NODE_TYPE_TO_DIM on purpose: the baselines (models/baselines.py,
#: reproduce/render_table.py) build one input projection per entry there, so
#: adding a type to it would change their parameter counts -- and their
#: initialisation draws -- even on graphs that do not contain it.
OPTIONAL_NODE_TYPE_TO_DIM: Dict[str, int] = {
    "NetworkDevice": 20,  # +2: link_utilization, transit_load_norm
}


def node_type_dim(node_type: str) -> int:
    """Feature width of *node_type*, including the optional network types."""
    return NODE_TYPE_TO_DIM.get(
        node_type, OPTIONAL_NODE_TYPE_TO_DIM.get(node_type, len(BASE_METRIC_KEYS))
    )

# Base dims (unchanged): weight + path_count_norm + 7 type one-hot
_EDGE_BASE_DIM = 2 + len(EDGE_TYPES)  # = 9
# QoS decomposition dims added for Middleware 2026 Q-HGL contribution:
#   reliability_score, durability_score, priority_score,
#   has_deadline, deadline_ns_log, max_blocking_ms_log, qos_heterogeneity_flag
_QOS_EXTRA_DIM = 7
EDGE_FEATURE_DIM = _EDGE_BASE_DIM + _QOS_EXTRA_DIM  # = 16

# Label column indices in the (N, 3) label matrix. availability is not a
# separate column — it is a Reliability sub-characteristic and already feeds
# the "reliability" column's ground truth (see saag.simulation.models
# ImpactMetrics.reliability_impact, the r_alpha blend of fault_tolerance_impact
# and availability_impact). Vulnerability/Security is retired.
LABEL_COLS = {
    "composite": 0,
    "reliability": 1,
    "maintainability": 2,
}

# ── QoS edge feature helpers ───────────────────────────────────────────────────

# Canonical score tables, sourced from QoSPolicy rather than restated here.
# They were previously duplicated, and the copy drifted: it lacked the
# CRITICAL/HIGHEST priority tier, so those topics scored below MEDIUM.
_RELIABILITY_SCORE: Dict[str, float] = QoSPolicy.RELIABILITY_SCORES
_DURABILITY_SCORE: Dict[str, float] = QoSPolicy.DURABILITY_SCORES
_PRIORITY_SCORE: Dict[str, float] = QoSPolicy.PRIORITY_SCORES

# Edge types for which QoS attributes are semantically meaningful
_QOS_EDGE_TYPES = {"PUBLISHES_TO", "SUBSCRIBES_TO", "DEPENDS_ON"}


def _compute_qos_heterogeneity_flags(
    graph: nx.DiGraph,
) -> Dict[Tuple[str, str], float]:
    """Return a per-edge flag indicating QoS heterogeneity relative to the
    scenario-level majority profile.

    For every PUBLISHES_TO / SUBSCRIBES_TO edge we compare its QoS triple
    (reliability, durability, priority) against the modal profile across all
    such edges in the graph.  The flag is 1.0 if the edge deviates, 0.0 otherwise.
    """
    profiles: List[Tuple[str, str, str]] = []
    edge_profiles: Dict[Tuple[str, str], Tuple[str, str, str]] = {}

    for src, dst, attrs in graph.edges(data=True):
        if attrs.get("type", "") not in _QOS_EDGE_TYPES:
            continue
        qp = attrs.get("qos_profile") or {}
        triple = (
            str(qp.get("reliability", "BEST_EFFORT")).upper(),
            str(qp.get("durability", "VOLATILE")).upper(),
            str(qp.get("transport_priority", qp.get("priority", "MEDIUM"))).upper(),
        )
        profiles.append(triple)
        edge_profiles[(src, dst)] = triple

    if not profiles:
        return {}

    # Modal (majority) profile
    from collections import Counter
    modal_triple = Counter(profiles).most_common(1)[0][0]

    flags: Dict[Tuple[str, str], float] = {}
    for (src, dst), triple in edge_profiles.items():
        flags[(src, dst)] = 0.0 if triple == modal_triple else 1.0
    return flags


def _extract_qos_edge_features(
    attrs: Dict[str, Any],
    edge_type: str,
    heterogeneity_flag: float = 0.0,
    qos_enabled: bool = True,
) -> List[float]:
    """Return the 7 QoS-specific edge feature values (dims 9-15).

    Non-zero only for PUBLISHES_TO / SUBSCRIBES_TO edges.
    All values are in [0, 1] or log-scaled to a bounded range.
    """
    if not qos_enabled or edge_type not in _QOS_EDGE_TYPES:
        return [0.0] * _QOS_EXTRA_DIM

    qp: Dict[str, Any] = attrs.get("qos_profile") or {}

    reliability = _RELIABILITY_SCORE.get(
        str(qp.get("reliability", "BEST_EFFORT")).upper(), 0.0
    )
    durability = _DURABILITY_SCORE.get(
        str(qp.get("durability", "VOLATILE")).upper(), 0.0
    )
    priority_key = str(
        qp.get("transport_priority", qp.get("priority", "MEDIUM"))
    ).upper()
    priority = _PRIORITY_SCORE.get(priority_key, 0.33)

    # Deadline handling: ms or raw nanoseconds → log10-scaled ms, clamped
    raw_deadline_ms = qp.get("deadline_ms")
    if raw_deadline_ms is not None and float(raw_deadline_ms) > 0:
        deadline_ns = float(raw_deadline_ms) * 1e6
    else:
        deadline_ns = float(qp.get("deadline_ns", qp.get("deadline", 0)) or 0)
    has_deadline = 1.0 if deadline_ns > 0 else 0.0
    deadline_log = math.log10(1.0 + deadline_ns / 1e6) if deadline_ns > 0 else 0.0
    deadline_log = min(deadline_log, 10.0) / 10.0  # normalise to [0,1]

    # Max blocking time: raw ms → log10-scaled
    blocking_ms = float(qp.get("max_blocking_ms", qp.get("max_blocking", 0)) or 0)
    blocking_log = math.log10(1.0 + blocking_ms) if blocking_ms > 0 else 0.0
    blocking_log = min(blocking_log, 5.0) / 5.0   # normalise to [0,1]

    return [
        reliability,       # dim 9
        durability,        # dim 10
        priority,          # dim 11
        has_deadline,      # dim 12
        deadline_log,      # dim 13
        blocking_log,      # dim 14
        heterogeneity_flag,  # dim 15
    ]


# ── Infrastructure feature helpers ─────────────────────────────────────────────

def _graph_network_load(graph: nx.DiGraph) -> Optional[Tuple[Fabric, NetworkState]]:
    """Baseline routing of the pub-sub flows over CONNECTS_TO, or None.

    None unless the graph models its network (a NetworkDevice, or a link with
    ``capacity_mbps``), so device-free graphs keep exactly their old features.
    """
    vertices = [n for n, a in graph.nodes(data=True) if a.get("type") in NETWORK_TYPES]
    links, pubs, subs, routers, hosts = [], {}, {}, {}, {}
    for src, dst, attrs in graph.edges(data=True):
        etype = attrs.get("type", "")
        if etype == "CONNECTS_TO":
            links.append((src, dst, attrs.get("capacity_mbps")))
        elif etype == "PUBLISHES_TO":
            pubs.setdefault(dst, []).append(src)
        elif etype == "SUBSCRIBES_TO":
            subs.setdefault(dst, []).append(src)
        elif etype == "ROUTES":
            routers.setdefault(dst, []).append(src)
        elif etype == "RUNS_ON":
            hosts.setdefault(src, []).append(dst)
    has_devices = any(graph.nodes[v].get("type") == "NetworkDevice" for v in vertices)
    if not (has_devices or any(cap is not None for _, _, cap in links)):
        return None
    return baseline_load(
        vertices, links, pubs, subs, routers, hosts,
        topic_attrs={n: a for n, a in graph.nodes(data=True) if a.get("type") == "Topic"},
    )


def _normalize_infra_features(
    graph: nx.DiGraph,
    structural_metrics: Optional[Dict[str, Dict[str, float]]] = None,
    qos_enabled: bool = True,
    network: Optional[Tuple[Fabric, NetworkState]] = None,
) -> Dict[str, Dict[str, float]]:
    """Compute normalized infrastructure/runtime features per node.

    Derives:
    - Node: cpu_cores_norm, memory_gb_norm  (from graph attrs or structural_metrics)
    - Broker: max_connections_norm          (from graph attrs or structural_metrics)
    - Topic: subscriber_count_norm, publisher_count_norm  (from graph edge topology)
    - Topic: log1p_frequency_norm (per-scenario z-score of log1p(Hz))
    - Topic: topic_qos_criticality_ord (ordinal 0–4 of the 5-level QoS label)
    - Library: library_uses_reach_norm, library_downstream_subs_norm (see below)
    - NetworkDevice: link_utilization, transit_load_norm (from the baseline
      routing of the pub-sub flows; *network* is computed here when not given)

    All values are normalized to [0, 1] via per-graph max, EXCEPT
    log1p_frequency_norm which uses z-score normalization limited to the
    Topic nodes present in *this* graph (per-scenario, not global).

    Library reach features
    -----------------------
    USES edges run consumer -> library and are transitive (a library can use
    another library), matching the blast-radius rule both simulators apply on
    a library failure: FailureSimulator._blast_library_consumers walks "every
    transitive consumer"; FaultInjector's cascade pushes app_to_lib dependents
    onto the next BFS frontier. library_uses_reach_norm is the size of that
    reverse-USES closure; library_downstream_subs_norm is the distinct set of
    topic subscribers reachable from what the closure publishes — the closer
    proxy for the label, since simulated impact is subscriber feed loss, not
    raw consumer count. Both are per-graph max-normalized like the rest of
    this function. These are deliberately isomorphic to the label's causal
    mechanism (see data_preparation module notes / Stage 2 plan) — legitimate
    for a pre-deployment predictor, but that framing must travel with any
    reported Library ρ improvement.

    Per-scenario frequency normalization rationale
    -----------------------------------------------
    Frequency spans ~5 orders of magnitude across domains (0.001 Hz healthcare
    to 10 000 Hz HFT).  Normalizing globally (across all scenarios in the
    training corpus) would encode the scenario's domain identity in the
    standardised values, leaking label information into features across LOSO
    folds.  Normalizing per-scenario ensures each graph's frequency distribution
    is centred at 0 regardless of domain, preserving relative ordering within
    a scenario while eliminating cross-scenario scale differences.
    """
    node_cpu: Dict[str, float] = {}
    node_mem: Dict[str, float] = {}
    broker_conn: Dict[str, float] = {}
    topic_subs: Dict[str, float] = {}
    topic_pubs: Dict[str, float] = {}
    topic_freq_raw: Dict[str, float] = {}    # raw Hz values for all Topic nodes
    topic_crit_ord: Dict[str, float] = {}   # ordinal criticality values
    library_nodes: List[str] = []

    for n, attrs in graph.nodes(data=True):
        nt = attrs.get("type") or attrs.get("component_type") or "Application"
        sm = (structural_metrics or {}).get(n, {})
        if nt == "Node":
            cpu = float(attrs.get("cpu_cores", sm.get("cpu_cores", 0)) or 0)
            mem = float(attrs.get("memory_gb", sm.get("memory_gb", 0)) or 0)
            node_cpu[n] = cpu
            node_mem[n] = mem
        elif nt == "Broker":
            conn = float(attrs.get("max_connections", sm.get("max_connections", 0)) or 0)
            broker_conn[n] = conn
        elif nt == "Topic":
            # Collect raw frequency for per-scenario z-score (computed after this loop).
            freq_raw = float(
                attrs.get("frequency", attrs.get("topic_frequency", 0.0)) or 0.0
            )
            topic_freq_raw[n] = freq_raw
            # Criticality ordinal (Topic.criticality, NOT Application.criticality).
            crit_raw = attrs.get("criticality", attrs.get("topic_criticality"))
            topic_crit_ord[n] = (
                TOPIC_CRITICALITY_ORD[canonical_criticality(crit_raw, default="LOW")]
                if qos_enabled
                else 0.0
            )
        elif nt == "Library":
            library_nodes.append(n)

    # used_by[lib]: nodes that directly USE lib (reverse USES, for the
    # transitive consumer closure below). pubs_topics[member]: topics a
    # closure member publishes to. subs_of_topic[topic]: that topic's
    # subscribers. All three built in the same pass as the existing
    # PUBLISHES_TO/SUBSCRIBES_TO counters, to avoid a second full edge scan.
    used_by: Dict[str, set] = {}
    pubs_topics: Dict[str, set] = {}
    subs_of_topic: Dict[str, set] = {}

    # PUBLISHES_TO: Application → Topic; SUBSCRIBES_TO: Application → Topic.
    # Both counts are from the Topic's perspective (how many publishers/subscribers it has).
    for src, dst, attrs in graph.edges(data=True):
        etype = attrs.get("type", "")
        if etype == "SUBSCRIBES_TO":
            topic_subs[dst] = topic_subs.get(dst, 0.0) + 1.0
            subs_of_topic.setdefault(dst, set()).add(src)
        elif etype == "PUBLISHES_TO":
            topic_pubs[dst] = topic_pubs.get(dst, 0.0) + 1.0
            pubs_topics.setdefault(src, set()).add(dst)
        elif etype == "USES":
            # consumer -> library; reversed here so a closure walk starting
            # from the library reaches its consumers.
            used_by.setdefault(dst, set()).add(src)

    max_cpu = max(node_cpu.values(), default=1.0) or 1.0
    max_mem = max(node_mem.values(), default=1.0) or 1.0
    max_conn = max(broker_conn.values(), default=1.0) or 1.0
    max_subs = max(topic_subs.values(), default=1.0) or 1.0
    max_pubs = max(topic_pubs.values(), default=1.0) or 1.0

    # --- Per-scenario log1p z-score for topic frequency -------------------
    # Apply log1p first (compresses the 0.001–10 000 Hz span to ~0–9.2),
    # then z-score within this graph so the distribution is centred at μ=0
    # regardless of domain.  Clamp to [-3, 3] to bound outliers.
    log1p_vals = {n: math.log1p(hz) for n, hz in topic_freq_raw.items()}
    if log1p_vals:
        mu = sum(log1p_vals.values()) / len(log1p_vals)
        variance = sum((v - mu) ** 2 for v in log1p_vals.values()) / len(log1p_vals)
        sigma = max(math.sqrt(variance), 1e-6)  # guard against zero-variance graphs
        log1p_freq_norm: Dict[str, float] = {
            n: max(-3.0, min(3.0, (v - mu) / sigma))
            for n, v in log1p_vals.items()
        }
    else:
        log1p_freq_norm = {}

    # Normalise topic_qos_criticality_ord to [0, 1] (max value is MAX_TOPIC_CRITICALITY_ORD = 2.0).
    max_crit_ord = MAX_TOPIC_CRITICALITY_ORD

    all_topic_nodes = set(topic_subs) | set(topic_pubs) | set(topic_freq_raw)

    # Dynamic masking (G4 covariate shift mitigation):
    # If all topic criticalities in this graph are identical (zero variance),
    # it means we cannot justify a real ground-truth distribution for this scenario
    # and the field is flat (typically all minimal). We mask the field to a uniform 0.0
    # to prevent inductive covariate shift when training/testing across scenarios.
    crit_vals = [topic_crit_ord.get(n, 0.0) for n in all_topic_nodes]
    if len(crit_vals) > 0:
        mean_crit = sum(crit_vals) / len(crit_vals)
        variance = sum((v - mean_crit) ** 2 for v in crit_vals) / len(crit_vals)
        if variance < 1e-9:
            topic_crit_ord = {n: 0.0 for n in all_topic_nodes}

    # --- Library transitive reverse-USES closure ---------------------------
    # Consumers (Applications or other Libraries) that fail if this library
    # fails, found by walking `used_by` outward from the library. Iterative
    # (not recursive) to avoid stack limits on deep USES chains.
    def _uses_closure(start: str) -> set:
        seen: set = set()
        stack = [start]
        while stack:
            cur = stack.pop()
            for consumer in used_by.get(cur, ()):
                if consumer not in seen:
                    seen.add(consumer)
                    stack.append(consumer)
        return seen

    library_reach: Dict[str, float] = {}
    library_downstream: Dict[str, float] = {}
    for lib in library_nodes:
        closure = _uses_closure(lib)
        library_reach[lib] = float(len(closure))
        downstream: set = set()
        for member in closure:
            for topic in pubs_topics.get(member, ()):
                downstream |= subs_of_topic.get(topic, set())
        library_downstream[lib] = float(len(downstream))

    max_reach = max(library_reach.values(), default=1.0) or 1.0
    max_downstream = max(library_downstream.values(), default=1.0) or 1.0

    infra: Dict[str, Dict[str, float]] = {}
    for n in node_cpu:
        infra[n] = {
            "cpu_cores_norm": node_cpu[n] / max_cpu,
            "memory_gb_norm": node_mem.get(n, 0.0) / max_mem,
        }
    for n, v in broker_conn.items():
        infra[n] = {"max_connections_norm": v / max_conn}
    for n in all_topic_nodes:
        infra[n] = {
            "subscriber_count_norm": topic_subs.get(n, 0.0) / max_subs,
            "publisher_count_norm": topic_pubs.get(n, 0.0) / max_pubs,
            "log1p_frequency_norm": log1p_freq_norm.get(n, 0.0),
            "topic_qos_criticality_ord": topic_crit_ord.get(n, 0.0) / max_crit_ord,
        }
    for n in library_nodes:
        infra[n] = {
            "library_uses_reach_norm": library_reach.get(n, 0.0) / max_reach,
            "library_downstream_subs_norm": library_downstream.get(n, 0.0) / max_downstream,
        }

    devices = [n for n, a in graph.nodes(data=True) if a.get("type") == "NetworkDevice"]
    if devices:
        fabric, state = network or _graph_network_load(graph)
        loads = {d: state.vertex_load.get(d, 0.0) for d in devices}
        max_load = max(loads.values(), default=1.0) or 1.0
        for d in devices:
            infra[d] = {
                "link_utilization": min(1.0, state.max_util(d, fabric)),
                "transit_load_norm": loads[d] / max_load,
            }
    return infra


def _rank_normalize_base_columns(feat_matrix: np.ndarray) -> None:
    """In-place within-graph rank normalization of the ``BASE_METRIC_KEYS`` columns.

    ``KEYS_BY_TYPE`` always places ``BASE_METRIC_KEYS`` first, so columns
    ``[0, len(BASE_METRIC_KEYS))`` are the scale-dependent centrality/topology
    metrics (pagerank, betweenness, mpci, qos_weight, ...) for every node
    type; later columns are type-specific extras already normalized to
    [0, 1] (``*_norm``, ordinals) and are left untouched.

    Each column is mapped to ``rank / (n - 1)`` using average-rank tie
    handling (matching :func:`scipy.stats.spearmanr`'s own convention), so
    the transform is exactly rank-preserving: re-ranking the normalized
    column reproduces the original ordering, ties included. PageRank sums to
    1 over all nodes, so its mean tracks 1/N — under a corpus spanning
    74-to-520-node graphs that is nearly an order of magnitude of pure scale
    difference with no ranking information in it; rank-normalizing removes
    that cross-graph scale artefact while leaving within-graph order intact.

    A node type with 0 or 1 members has no ranking to express; those columns
    are set to the neutral midpoint (0.5) rather than raising or emitting NaN.
    """
    n = feat_matrix.shape[0]
    n_base_cols = min(len(BASE_METRIC_KEYS), feat_matrix.shape[1])
    if n <= 1:
        feat_matrix[:, :n_base_cols] = 0.5
        return
    for col in range(n_base_cols):
        ranks = rankdata(feat_matrix[:, col], method="average")
        feat_matrix[:, col] = (ranks - 1.0) / (n - 1)


# ── Public API ─────────────────────────────────────────────────────────────────

@dataclass
class GraphConversionResult:
    """Output of :func:`networkx_to_hetero_data`."""

    hetero_data: object

    node_id_map: Dict[str, List[str]] = field(default_factory=dict)

    node_name_to_idx: Dict[str, Tuple[str, int]] = field(default_factory=dict)

    edge_name_map: Dict[Tuple[str, str, str], List[Tuple[str, str]]] = field(
        default_factory=dict
    )

    num_labelled_nodes: int = 0

    #: Edges carrying a measured criticality label (``y_edge_mask`` true).
    #: Zero means L_edge is inactive — no edge-removal sweep was supplied.
    num_labelled_edges: int = 0

    present_node_types: List[str] = field(default_factory=list)

    #: Which of the 3 label columns (see LABEL_COLS) the labeler actually
    #: measured. Columns marked False are structural zeros, not measurements —
    #: training or scoring on them regresses a head toward a constant. Inferred
    #: from which keys the simulation dict carries; see extract_simulation_dict.
    dimension_mask: List[bool] = field(
        default_factory=lambda: [True, True, True]
    )


def networkx_to_hetero_data(
    graph: nx.DiGraph,
    structural_metrics: Optional[Dict[str, Dict[str, float]]] = None,
    simulation_results: Optional[Dict[str, Dict[str, float]]] = None,
    rm_scores: Optional[Dict[str, Dict[str, float]]] = None,
    qos_enabled: bool = True,
    rank_normalize_features: bool = False,
    edge_simulation_results: Optional[Dict[Tuple[str, str], float]] = None,
    append_prior: bool = False,
    drop_feature_keys: Sequence[str] = (),
    qos_exempt_keys: Sequence[str] = (),
    extra_node_keys: Sequence[str] = (),
    extra_edge_keys: Sequence[str] = (),
) -> GraphConversionResult:
    """Convert a NetworkX DiGraph to a PyG HeteroData object.

    ``extra_node_keys`` appends one column per key to every node type, read from
    ``structural_metrics[node][key]`` (0.0 when absent), after rank normalisation
    and before the prior column; ``extra_edge_keys`` appends one column per key to
    every edge row, read from the edge attribute (0.0 when absent). Both are empty
    by default, which leaves the output unchanged (Amendment 19's rate-fed arms).

    ``drop_feature_keys`` zeroes those base columns for every node type after
    rank normalisation, keeping the input width (Amendment 14's degree-free
    arms). ``qos_exempt_keys`` names QoS node columns kept when ``qos_enabled``
    is False (Amendment 14's w_in-held 2x2).

    With ``append_prior`` set, every node type gets one extra last column
    holding ``structural_metrics[node]["topo_prior"]`` (0.0 when absent): the
    SaG-Hybrid prior of PREREGISTRATION.md Amendment 5. It is appended after
    rank normalisation so the already-normalised prior is not re-ranked.

    Parameters
    ----------
    graph:
        The full structural NetworkX graph from Step 1.
        Nodes must carry a ``type`` attribute (one of NODE_TYPES).
        Edges must carry a ``type`` attribute (one of EDGE_TYPES) and
        optionally ``weight`` and ``path_count`` attributes.
    structural_metrics:
        ``{node_name: {metric_key: value}}`` from ``StructuralAnalyzer``.
    simulation_results:
        ``{node_name: {composite, reliability, maintainability}}``
        training labels (see LABEL_COLS).
    rm_scores:
        ``{node_name: {overall, reliability, maintainability,
                        fault_tolerance, availability}}`` for node
        predictions — fault_tolerance/availability are Reliability's
        sub-characteristics, reported but not separate label columns.
    edge_simulation_results:
        ``{(source_id, target_id): combined_impact}`` measured edge criticality
        from ``FailureSimulator.simulate_edge_removal`` — the edge is actually
        severed and the composite recomputed. Only edges present here are
        supervised; the rest carry ``y_edge_mask=False`` so the edge loss skips
        them. Omit it and no edge labels are written at all, which leaves
        ``L_edge`` inactive rather than substituting a proxy. There used to be
        such a proxy here (``I*(source) x {1.0 if bridge else 0.1}``), so the
        edge head was scored against a hand-chosen structural heuristic rather
        than against a measurement; that is the defect this parameter closes.
    rank_normalize_features:
        Apply within-graph rank normalization (see
        :func:`_rank_normalize_base_columns`) to the ``BASE_METRIC_KEYS``
        columns of every node type's feature matrix. Off by default so
        existing callers are unaffected; the un-normalized path remains the
        ablation arm against this one.

    Returns
    -------
    GraphConversionResult
    """
    try:
        from torch_geometric.data import HeteroData
    except ImportError as exc:
        raise ImportError(
            "PyTorch Geometric is required for GNN functionality. "
            "Install with: pip install torch-geometric"
        ) from exc

    result = GraphConversionResult(hetero_data=HeteroData())
    data: HeteroData = result.hetero_data  # type: ignore[assignment]

    # Infer which label dimensions the labeler actually measured, from the keys
    # it emitted. A labeler that reports only a scalar impact (FaultInjector)
    # leaves maintainability absent; that column stays zero in the matrix but
    # must not be treated as an observation. See GraphConversionResult.
    if simulation_results:
        # Key presence is the weaker prior rule and was not enough: the
        # FailureResult branch of extract_simulation_dict always emits a
        # "maintainability" key, so an identically-zero column was marked
        # measured -- exactly the conflation that regresses a head to a
        # constant. A column has to actually vary to count as an observation.
        seen: Dict[str, set] = defaultdict(set)
        n_labelled = 0
        for sim in simulation_results.values():
            if isinstance(sim, dict):
                n_labelled += 1
                for key, value in sim.items():
                    try:
                        seen[key].add(round(float(value), 12))
                    except (TypeError, ValueError):
                        continue
        # With a population to measure over, a column that never varies is a
        # structural constant rather than an observation. With a single labelled
        # node there is no variance to have, so key presence is all the evidence
        # available and the weaker rule applies.
        result.dimension_mask = [
            (len(seen.get(dim, ())) >= 2 if n_labelled > 1 else dim in seen)
            for dim in sorted(LABEL_COLS, key=LABEL_COLS.get)
        ]

    # Also carry the mask on the HeteroData itself so GNNTrainer can default to it
    # without every caller having to thread GraphConversionResult.dimension_mask
    # through by hand (see trainer.py's dimension_mask fallback).
    data.dimension_mask = result.dimension_mask

    # ── 1. Partition nodes by type ────────────────────────────────────────────
    type_to_nodes: Dict[str, List[str]] = {t: [] for t in NODE_TYPES}

    for node, attrs in graph.nodes(data=True):
        comp_type = attrs.get("component_type")
        node_type = comp_type if comp_type in NODE_TYPES else attrs.get("type")
        if node_type is None:
            logger.warning(
                "Node '%s' has no 'type' or 'component_type'; defaulting to 'Application'.",
                node,
            )
            node_type = "Application"
        if node_type not in type_to_nodes:
            logger.warning(
                "Unknown node type '%s' for node '%s'; treating as Application.",
                node_type, node,
            )
            node_type = "Application"
        type_to_nodes[node_type].append(node)

    for node_type, nodes in type_to_nodes.items():
        if not nodes:
            continue
        result.node_id_map[node_type] = nodes
        result.present_node_types.append(node_type)
        for local_idx, name in enumerate(nodes):
            result.node_name_to_idx[name] = (node_type, local_idx)

    # Pre-compute infrastructure/runtime features (Topic counts, Node/Broker infra).
    # A DEPENDS_ON projection carries its native graph as graph["infra_source"]
    # (Amendment 9): the Library reach features need the USES / pub-sub edges the
    # projection no longer has.
    infra_source = graph.graph.get("infra_source", graph)
    network = _graph_network_load(infra_source)
    infra_features = _normalize_infra_features(
        infra_source, structural_metrics, qos_enabled=qos_enabled, network=network,
    )

    # ── 2. Build node feature tensors per type ────────────────────────────────
    for node_type in result.present_node_types:
        nodes = result.node_id_map[node_type]
        n = len(nodes)
        dim = node_type_dim(node_type)
        feat_matrix = np.zeros((n, dim), dtype=np.float32)

        keys_to_use = KEYS_BY_TYPE.get(node_type, BASE_METRIC_KEYS)

        import os
        decouple_features = os.environ.get("DECOUPLE_FEATURES") in ("1", "true", "True")

        for local_idx, name in enumerate(nodes):
            base_source = (structural_metrics or {}).get(name, {})
            infra_source = infra_features.get(name, {})
            for col, key in enumerate(keys_to_use):
                # Base metrics come from structural_metrics; infra keys from infra_source
                val = base_source.get(key, infra_source.get(key, 0.0))
                if (not qos_enabled and key in ("qos_weight", "qos_weight_in", "qos_weight_out")
                        and key not in qos_exempt_keys):
                    val = 0.0
                if decouple_features and key in (
                    "pagerank", "reverse_pagerank", "betweenness_centrality",
                    "closeness_centrality", "eigenvector_centrality",
                    "in_degree_centrality", "out_degree_centrality",
                    "clustering_coefficient", "ap_c_score", "ap_c_directed",
                    "cdi", "mpci", "path_complexity", "fan_out_criticality", "bridge_ratio"
                ):
                    val = 0.0
                feat_matrix[local_idx, col] = float(val)

        if rank_normalize_features:
            _rank_normalize_base_columns(feat_matrix)

        # After normalisation, so a zeroed column stays exactly 0 either way.
        # "*" zeroes every column (Amendment 17's constant-feature arm).
        drop_all = "*" in drop_feature_keys
        for col, key in enumerate(keys_to_use):
            if drop_all or key in drop_feature_keys:
                feat_matrix[:, col] = 0.0

        if extra_node_keys:
            extra_cols = np.array(
                [[float((structural_metrics or {}).get(name, {}).get(key, 0.0))
                  for key in extra_node_keys] for name in nodes],
                dtype=np.float32,
            ).reshape(n, len(extra_node_keys))
            feat_matrix = np.concatenate([feat_matrix, extra_cols], axis=1)

        if append_prior:
            prior_col = np.array(
                [[float((structural_metrics or {}).get(name, {}).get("topo_prior", 0.0))]
                 for name in nodes],
                dtype=np.float32,
            ).reshape(n, 1)
            feat_matrix = np.concatenate([feat_matrix, prior_col], axis=1)

        data[node_type].x = torch.from_numpy(feat_matrix)
        data[node_type].num_nodes = n

        # ── Node labels (simulation ground truth) ─────────────────────────────
        if simulation_results:
            label_matrix = np.zeros((n, len(LABEL_COLS)), dtype=np.float32)
            # Presence in the simulation dict, NOT |y| > 0. A node the simulator
            # targeted and scored 0.0 is a real observation at the low end of the
            # ranking; a node never targeted is missing data. Collapsing both to
            # 0.0 excluded genuine zeros from training while still scoring on them.
            label_mask = np.zeros(n, dtype=bool)
            labelled_count = 0
            for local_idx, name in enumerate(nodes):
                sim = simulation_results.get(name)
                if sim is not None:
                    for key, col in LABEL_COLS.items():
                        label_matrix[local_idx, col] = float(sim.get(key, 0.0))
                    label_mask[local_idx] = True
                    labelled_count += 1
            data[node_type].y = torch.from_numpy(label_matrix)
            data[node_type].label_mask = torch.from_numpy(label_mask)
            result.num_labelled_nodes += labelled_count

        # ── RM scores (for consistency regularization) ───────────
        if rm_scores:
            # rm_scores dicts (extract_rm_scores_dict) key the composite "overall"
            # rather than "composite" — same column order as LABEL_COLS, different
            # key name for that one column.
            rm_matrix = np.zeros((n, len(LABEL_COLS)), dtype=np.float32)
            rm_score_cols = {"overall": LABEL_COLS["composite"], **{
                k: v for k, v in LABEL_COLS.items() if k != "composite"
            }}
            for local_idx, name in enumerate(nodes):
                rm = rm_scores.get(name)
                if rm is not None:
                    for key, col in rm_score_cols.items():
                        rm_matrix[local_idx, col] = float(rm.get(key, 0.0))
            data[node_type].y_rm = torch.from_numpy(rm_matrix)

    # ── 3. Build edge index and feature tensors per relation ──────────────────
    # Pre-compute per-edge QoS heterogeneity flags (single graph pass)
    _hetero_flags: Dict[Tuple[str, str], float] = _compute_qos_heterogeneity_flags(graph)

    rel_edges: Dict[Tuple[str, str, str], Tuple[List[int], List[int], List[List[float]]]] = {}

    for src, dst, attrs in graph.edges(data=True):
        if src not in result.node_name_to_idx or dst not in result.node_name_to_idx:
            continue

        src_type, src_local = result.node_name_to_idx[src]
        dst_type, dst_local = result.node_name_to_idx[dst]
        edge_type = attrs.get("type", "DEPENDS_ON")

        rel_key = (src_type, edge_type, dst_type)

        if rel_key not in rel_edges:
            rel_edges[rel_key] = ([], [], [])
            result.edge_name_map[rel_key] = []

        rel_edges[rel_key][0].append(src_local)
        rel_edges[rel_key][1].append(dst_local)
        result.edge_name_map[rel_key].append((src, dst))

        # ── Edge features: 16-d (base 9 + QoS decomposition 7) ───────────────
        weight = float(attrs.get("weight", 1.0))
        if edge_type == "CONNECTS_TO" and network is not None:
            # A network link's weight slot carries its baseline headroom
            # (1 - utilisation): 1.0 for an unbounded link, as before.
            util = network[1].link_util.get(link_key(src, dst))
            weight = 1.0 - min(1.0, util) if util is not None else 1.0
        path_count_raw = float(attrs.get("path_count", 1) or 1)
        path_count_norm = math.log2(1.0 + path_count_raw) / math.log2(17.0)
        type_onehot = [0.0] * len(EDGE_TYPES)
        if edge_type in EDGE_TYPE_INDEX:
            type_onehot[EDGE_TYPE_INDEX[edge_type]] = 1.0

        # QoS decomposition dims 9-15 (non-zero only for pub/sub edges)
        hetero_flag = _hetero_flags.get((src, dst), 0.0)
        qos_dims = _extract_qos_edge_features(attrs, edge_type, hetero_flag, qos_enabled=qos_enabled)

        extra_dims = [float(attrs.get(key, 0.0)) for key in extra_edge_keys]
        rel_edges[rel_key][2].append([weight, path_count_norm] + type_onehot + qos_dims + extra_dims)

    n_edge_labelled = 0
    for (src_type, edge_type, dst_type), (srcs, dsts, feats) in rel_edges.items():
        rel = (src_type, edge_type, dst_type)
        data[rel].edge_index = torch.tensor([srcs, dsts], dtype=torch.long)
        data[rel].edge_attr = torch.tensor(feats, dtype=torch.float32)

        # Edge labels: the measured cost of severing this relationship, from
        # FailureSimulator.simulate_edge_removal. Edges the sweep did not
        # evaluate are masked out rather than written as 0.0 — an unmeasured
        # edge is not an edge measured to be harmless (the same distinction
        # FaultInjectionResult.unlabeled_node_ids draws for nodes).
        if edge_simulation_results:
            src_nodes = result.node_id_map[src_type]
            dst_nodes = result.node_id_map[dst_type]
            edge_labels = np.zeros((len(srcs), 1), dtype=np.float32)
            edge_mask = np.zeros(len(srcs), dtype=bool)
            for i, (s_idx, d_idx) in enumerate(zip(srcs, dsts)):
                measured = edge_simulation_results.get(
                    (src_nodes[s_idx], dst_nodes[d_idx])
                )
                if measured is None:
                    continue
                edge_labels[i, 0] = float(measured)
                edge_mask[i] = True
            if edge_mask.any():
                data[rel].y_edge = torch.from_numpy(edge_labels)
                data[rel].y_edge_mask = torch.from_numpy(edge_mask)
                n_edge_labelled += int(edge_mask.sum())

    result.num_labelled_edges = n_edge_labelled
    logger.info(
        "Graph converted: %d node types, %d relation types, %d labelled nodes, "
        "%d labelled edges.",
        len(result.present_node_types),
        len(rel_edges),
        result.num_labelled_nodes,
        n_edge_labelled,
    )
    if simulation_results and not edge_simulation_results:
        # Gated on simulation_results so only label-bearing (training)
        # conversions speak up; a predict-time conversion has no labels of any
        # kind and nothing to say. Stated rather than silent: the
        # edge-criticality head still predicts, but nothing supervises it, so
        # L_edge contributes zero to the loss. Silence here is what let a
        # structural proxy stand in for a measurement unnoticed.
        logger.warning(
            "No measured edge criticality supplied — L_edge is inactive. Run "
            "`cli/simulate_graph.py edge-criticality` and pass its output as "
            "edge_simulation_results to supervise the edge head."
        )
    if result.num_labelled_nodes == 0 and simulation_results:
        logger.warning(
            "ZERO labelled nodes found! Check if component IDs in simulation results "
            "match those in the graph. Sample graph nodes: %s, Sample sim keys: %s",
            list(graph.nodes())[:5], list(simulation_results.keys())[:5]
        )
    return result


# ── Training split utilities ───────────────────────────────────────────────────

def _labelled_index_mask(store) -> np.ndarray:
    """Boolean array over a node store: which nodes carry simulation ground truth.

    Prefers the explicit ``label_mask`` (presence in the simulator's output) and
    falls back to the historical ``|y_composite| > 0`` proxy for stores built
    before that field existed. The proxy misclassifies a node the simulator
    targeted and scored 0.0 as unlabelled, which pushed genuine low-impact
    observations out of the training split while leaving them in evaluation.
    """
    mask = getattr(store, "label_mask", None)
    if mask is not None:
        return mask.detach().cpu().numpy().astype(bool)
    return np.abs(store.y[:, 0].detach().cpu().numpy()) > 1e-6


def _store_device(store) -> torch.device:
    """Device this node store's existing tensors already live on.

    Splits can be (re-)computed after the graph has been moved to CUDA (e.g.
    re-deriving the winning seed's masks for eval metrics), so a freshly built
    boolean mask must land on that same device — `torch.zeros(...)` defaults to
    CPU, and a CPU mask attached to an otherwise-CUDA store breaks the next
    `HeteroData.subgraph()` call with "indices should be either on cpu or on
    the same device as the indexed tensor". Mirrors the device probe already
    used in `trainer.get_inductive_subgraph`'s own fallback branch.
    """
    if hasattr(store, "x") and isinstance(store.x, torch.Tensor):
        return store.x.device
    for _, val in store.items():
        if isinstance(val, torch.Tensor):
            return val.device
    return torch.device("cpu")


def apply_external_splits(
    hetero_data,
    conversion_result,
    splits: Dict[str, Iterable[str]],
) -> None:
    """Set train/val/test masks from caller-supplied **node id** sets, in place.

    :func:`create_node_splits` draws a fresh split per model, so two variants
    trained on the same scenario see different train/test partitions and cannot
    be compared on a common held-out sample. Pinning the split by node id — the
    one identifier that is stable across substrates — makes a like-for-like
    comparison table possible.

    Ids absent from a store are ignored; a node named in no split gets all three
    masks ``False``, so it is neither trained on nor scored.

    Parameters
    ----------
    splits:
        ``{"train": [...], "val": [...], "test": [...]}`` of node ids.
    """
    wanted = {
        key: {str(v) for v in splits.get(key, ())}
        for key in ("train", "val", "test")
    }
    overlap = (
        (wanted["train"] & wanted["val"])
        | (wanted["train"] & wanted["test"])
        | (wanted["val"] & wanted["test"])
    )
    if overlap:
        raise ValueError(
            f"external splits overlap on {len(overlap)} node(s), e.g. "
            f"{sorted(overlap)[:5]}; a node cannot be both trained on and held out"
        )

    id_map = getattr(conversion_result, "node_id_map", None) or {}
    for node_type in hetero_data.node_types:
        store = hetero_data[node_type]
        names = [str(n) for n in id_map.get(node_type, [])]
        n = store.num_nodes
        masks = {
            key: torch.zeros(n, dtype=torch.bool) for key in ("train", "val", "test")
        }
        for idx, name in enumerate(names[:n]):
            for key in ("train", "val", "test"):
                if name in wanted[key]:
                    masks[key][idx] = True
                    break
        device = _store_device(store)
        store.train_mask = masks["train"].to(device)
        store.val_mask = masks["val"].to(device)
        store.test_mask = masks["test"].to(device)


def create_node_splits(
    hetero_data,
    train_ratio: float = 0.6,
    val_ratio: float = 0.2,
    seed: int = 42,
) -> None:
    """Add train/val/test boolean masks to every node store in-place.

    Uses **stratified splitting** when ground-truth labels exist: labelled nodes
    (those the simulator actually targeted — see :func:`_labelled_index_mask`)
    are split proportionally across train/val/test first, ensuring each split
    contains labelled nodes for meaningful ρ evaluation.
    Unlabelled nodes are then distributed to fill the remaining capacity.
    Falls back to uniform random split when no ``y`` attribute is present.
    """
    rng = np.random.default_rng(seed)

    for store in hetero_data.node_stores:
        n = store.num_nodes
        if n == 0:
            continue

        train_mask = torch.zeros(n, dtype=torch.bool)
        val_mask   = torch.zeros(n, dtype=torch.bool)
        test_mask  = torch.zeros(n, dtype=torch.bool)

        # ── Stratified split when labels available ──────────────────────────
        if hasattr(store, "y") and store.y.numel() > 0:
            is_labelled = _labelled_index_mask(store)
            labelled_idx   = np.where(is_labelled)[0]
            unlabelled_idx = np.where(~is_labelled)[0]

            if len(labelled_idx) >= 3:
                lab_shuffled = rng.permutation(labelled_idx)
                n_lab_train = max(1, int(len(lab_shuffled) * train_ratio))
                n_lab_val   = max(1, int(len(lab_shuffled) * val_ratio))
                lab_train = lab_shuffled[:n_lab_train]
                lab_val   = lab_shuffled[n_lab_train: n_lab_train + n_lab_val]
                lab_test  = lab_shuffled[n_lab_train + n_lab_val:]

                unlab_shuffled = rng.permutation(unlabelled_idx)
                n_total_train  = max(1, int(n * train_ratio))
                n_total_val    = max(1, int(n * val_ratio))
                n_need_train   = max(0, n_total_train - len(lab_train))
                n_need_val     = max(0, n_total_val   - len(lab_val))

                unlab_train = unlab_shuffled[:n_need_train]
                unlab_val   = unlab_shuffled[n_need_train: n_need_train + n_need_val]
                unlab_test  = unlab_shuffled[n_need_train + n_need_val:]

                all_train = np.concatenate([lab_train, unlab_train])
                all_val   = np.concatenate([lab_val,   unlab_val])
                all_test  = np.concatenate([lab_test,  unlab_test])

                train_mask[torch.from_numpy(all_train.astype(np.int64))] = True
                val_mask[torch.from_numpy(all_val.astype(np.int64))]     = True
                test_mask[torch.from_numpy(all_test.astype(np.int64))]   = True

                device = _store_device(store)
                store.train_mask = train_mask.to(device)
                store.val_mask   = val_mask.to(device)
                store.test_mask  = test_mask.to(device)
                continue

        # ── Fallback: uniform random split ──────────────────────────────────
        indices = rng.permutation(n)
        n_train = max(1, int(n * train_ratio))
        n_val   = max(1, int(n * val_ratio))
        train_mask[torch.from_numpy(indices[:n_train].astype(np.int64))]               = True
        val_mask[torch.from_numpy(indices[n_train: n_train + n_val].astype(np.int64))] = True
        test_mask[torch.from_numpy(indices[n_train + n_val:].astype(np.int64))]        = True
        device = _store_device(store)
        store.train_mask = train_mask.to(device)
        store.val_mask   = val_mask.to(device)
        store.test_mask  = test_mask.to(device)


def create_kfold_masks(
    hetero_data,
    k: int = 5,
    fold_idx: int = 0,
    val_ratio: float = 0.15,
    seed: int = 42,
) -> None:
    """Add train/val/test boolean masks to every node store in-place for one
    fold of a within-graph, stratified k-fold split.

    Unlike :func:`create_node_splits` (a one-shot random holdout), this
    partitions labelled nodes (see :func:`_labelled_index_mask`) into ``k`` roughly-equal
    folds; fold ``fold_idx`` is held out as the test set, and the remaining
    ``k - 1`` folds are split into train/val by ``val_ratio``. Unlabelled
    nodes are folded the same way so every split still has full graph
    context. Node types with fewer than ``k`` labelled nodes fall back to
    putting everything in train (test/val empty for that store), matching
    ``compute_inductive_metrics``'s existing n<3 guard.
    """
    rng = np.random.default_rng(seed)

    def _fold_test_trainval(idx: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        if len(idx) < k:
            return np.array([], dtype=np.int64), idx
        shuffled = rng.permutation(idx)
        folds = np.array_split(shuffled, k)
        test = folds[fold_idx]
        trainval = np.concatenate([f for i, f in enumerate(folds) if i != fold_idx])
        return test, trainval

    for store in hetero_data.node_stores:
        n = store.num_nodes
        if n == 0:
            continue

        train_mask = torch.zeros(n, dtype=torch.bool)
        val_mask   = torch.zeros(n, dtype=torch.bool)
        test_mask  = torch.zeros(n, dtype=torch.bool)

        if hasattr(store, "y") and store.y.numel() > 0:
            is_labelled = _labelled_index_mask(store)
            labelled_idx   = np.where(is_labelled)[0]
            unlabelled_idx = np.where(~is_labelled)[0]

            if len(labelled_idx) >= k:
                lab_test, lab_trainval = _fold_test_trainval(labelled_idx)
                n_lab_val = max(1, int(len(lab_trainval) * val_ratio)) if len(lab_trainval) > 1 else 0
                lab_val   = lab_trainval[:n_lab_val]
                lab_train = lab_trainval[n_lab_val:]

                unlab_test, unlab_trainval = _fold_test_trainval(unlabelled_idx)
                n_unlab_val = max(0, int(len(unlab_trainval) * val_ratio))
                unlab_val   = unlab_trainval[:n_unlab_val]
                unlab_train = unlab_trainval[n_unlab_val:]

                all_train = np.concatenate([lab_train, unlab_train])
                all_val   = np.concatenate([lab_val,   unlab_val])
                all_test  = np.concatenate([lab_test,  unlab_test])

                train_mask[torch.from_numpy(all_train.astype(np.int64))] = True
                val_mask[torch.from_numpy(all_val.astype(np.int64))]     = True
                test_mask[torch.from_numpy(all_test.astype(np.int64))]   = True

                device = _store_device(store)
                store.train_mask = train_mask.to(device)
                store.val_mask   = val_mask.to(device)
                store.test_mask  = test_mask.to(device)
                continue

        # ── Fallback: not enough labelled nodes for k folds — all train ─────
        train_mask[:] = True
        device = _store_device(store)
        store.train_mask = train_mask.to(device)
        store.val_mask   = val_mask.to(device)
        store.test_mask  = test_mask.to(device)



def normalize_labels_robust(hetero_data, rank_normalize: bool = False) -> None:
    """In-place normalization of .y label tensors, preserving zeros.

    Computes statistics over non-zero labelled nodes (pooled across node
    types, matching the transductive scope the reported metrics are computed
    over), then maps non-zero labels onto (0, 1). Zeros remain 0.0.

    Parameters
    ----------
    rank_normalize:
        Default (``False``): IQR normalization — ``(x - median) / IQR``,
        clamped to [-3, 3], then sigmoid. Preserves relative *magnitude*
        alongside rank.

        ``True``: within-graph rank normalization (average-rank ties,
        ``rank / (n + 1)``), per column, in the same spirit as the transform
        applied to node features in :func:`_rank_normalize_base_columns`.
        The headline metric this model is scored on is Spearman rho — a
        purely rank-based statistic — so a target that preserves magnitude
        spends gradient budget getting inter-node spacing right in a way
        the evaluation never rewards. This is independent of any
        cross-graph concern: `normalize_labels_robust` already runs once
        per graph (see call site in gnn_service.py), so there is no
        cross-scenario pooling for either variant to fix.

        Deliberately ``rank / (n + 1)`` rather than ``rank / (n - 1)``:
        the latter maps the lowest rank to exactly 0.0, colliding with the
        "0.0 means no observation" sentinel this module's ``.abs() > 1e-6``
        checks rely on everywhere (a real node's genuinely-lowest rank
        would silently read back as unlabelled on the next pass). Adding
        one on each side keeps every value strictly inside (0, 1) for any
        n, including n=1, with no separate small-n branch needed.
    """
    all_labels = [
        hetero_data[nt].y
        for nt in hetero_data.node_types
        if hasattr(hetero_data[nt], "y") and hetero_data[nt].y.numel() > 0
    ]
    if not all_labels:
        return
    concat = torch.cat(all_labels, dim=0)   # (N_total, len(LABEL_COLS))

    # Mask out original zeros to preserve zero structure
    non_zero_mask = concat[:, 0].abs() > 1e-6
    if not non_zero_mask.any():
        return

    non_zero_concat = concat[non_zero_mask]

    if rank_normalize:
        n = non_zero_concat.shape[0]
        arr = non_zero_concat.detach().cpu().numpy()
        ranked = np.empty_like(arr)
        for col in range(arr.shape[1]):
            ranks = rankdata(arr[:, col], method="average")   # 1-indexed, ties averaged
            ranked[:, col] = ranks / (n + 1)
        ranked = torch.from_numpy(ranked).to(non_zero_concat.dtype)

        # Snapshot every store's non-zero mask from the ORIGINAL (pre-mutation)
        # labels before scattering anything back. Overwriting one type's `.y`
        # must not change the non-zero count computed for a later type — the
        # lowest rank in a store can legitimately be small but is never 0.0
        # (see the docstring), so this is purely about read/write ordering,
        # not about the zero-collision the ranking formula already avoids.
        nz_masks = {
            nt: hetero_data[nt].y[:, 0].abs() > 1e-6
            for nt in hetero_data.node_types
            if hasattr(hetero_data[nt], "y") and hetero_data[nt].y.numel() > 0
        }

        offset = 0
        for nt in hetero_data.node_types:
            nz = nz_masks.get(nt)
            if nz is not None and nz.any():
                count = int(nz.sum().item())
                store = hetero_data[nt]
                new_y = store.y.clone()
                new_y[nz] = ranked[offset:offset + count]
                store.y = new_y
                offset += count
        return

    q25 = torch.quantile(non_zero_concat, 0.25, dim=0)
    q75 = torch.quantile(non_zero_concat, 0.75, dim=0)
    iqr = (q75 - q25).clamp(min=1e-6)
    median = torch.median(non_zero_concat, dim=0).values

    for nt in hetero_data.node_types:
        store = hetero_data[nt]
        if hasattr(store, "y") and store.y.numel() > 0:
            nz = store.y[:, 0].abs() > 1e-6
            if nz.any():
                scaled = torch.sigmoid(((store.y[nz] - median) / iqr).clamp(-3.0, 3.0))
                new_y = store.y.clone()
                new_y[nz] = scaled
                store.y = new_y


# ── Extraction utilities ───────────────────────────────────────────────────────

def extract_simulation_dict(simulation_results: Union[list, dict, Any]) -> Dict[str, Dict[str, float]]:
    """Normalise Simulation output to common flat dict format."""
    out: Dict[str, Dict[str, float]] = {}

    if hasattr(simulation_results, "to_dict"):
        simulation_results = simulation_results.to_dict()

    if isinstance(simulation_results, dict) and "component_criticality" in simulation_results:
        for c in simulation_results["component_criticality"]:
            name = c.get("id")
            # As above: maintainability is unmeasured here, not zero.
            out[name] = {
                "composite": float(c.get("combined_impact", 0.0)),
                "reliability": float(c.get("failure_impact", 0.0)),
            }
        return out

    if isinstance(simulation_results, dict) and "records" in simulation_results:
        # FaultInjector emits a single scalar I*(v). It measures reachability-style
        # impact, so it maps onto composite/reliability (reliability is itself the
        # r_alpha-blend of fault-tolerance and availability — this scalar doesn't
        # distinguish them, so it is not emitted as a separate "availability" key)
        # — but it says nothing about maintainability. That key is deliberately
        # absent rather than set to 0.0: an omitted key is an unmeasured dimension,
        # a 0.0 is a measurement of "no impact". Conflating them made a prediction
        # head regress toward a constant. See the artifact's `labeled_dimensions`
        # field for the authoritative list.
        records = simulation_results["records"]
        if isinstance(records, dict):
            for nid, r in records.items():
                if not isinstance(r, dict):
                    continue
                score = float(r.get("impact_score", 0.0))
                out[str(nid)] = {
                    "composite": score,
                    "reliability": score,
                }
        elif isinstance(records, list):
            for r in records:
                if not isinstance(r, dict):
                    continue
                nid = r.get("node_id") or r.get("id")
                if nid is None:
                    continue
                score = float(r.get("impact_score", 0.0))
                out[str(nid)] = {
                    "composite": score,
                    "reliability": score,
                }
        return out

    results_list = simulation_results
    if isinstance(simulation_results, dict) and "results" in simulation_results:
        results_list = simulation_results["results"]

    if not isinstance(results_list, list):
        return out

    for r in results_list:
        if hasattr(r, "target_id"):
            name = r.target_id
            impact = r.impact
            out[name] = {
                "composite": float(impact.composite_impact),
                "reliability": float(impact.reliability_impact),
                "maintainability": float(impact.maintainability_impact),
            }
        elif isinstance(r, dict):
            name = r.get("target_id")
            if not name:
                continue
            impact = r.get("impact", r)
            out[name] = {
                "composite": float(impact.get("composite_impact", 0.0)),
                "reliability": float(impact.get("reliability_impact", 0.0)),
                "maintainability": float(impact.get("maintainability_impact", 0.0)),
            }
    return out


def extract_edge_simulation_dict(
    edge_results: Union[list, dict, Any]
) -> Dict[Tuple[str, str], float]:
    """Normalise edge-removal sweep output to ``{(source, target): impact}``.

    Accepts a ``SimulationReport``-shaped dict (``{"edge_criticality": [...]}``),
    a bare list of :class:`~saag.simulation.models.EdgeCriticality` dicts, or the
    objects themselves.

    Entries marked ``evaluated=False`` are dropped, not read as 0.0: the sweep
    bounds its candidate set to bridges and top-betweenness edges, and an edge
    it never severed is unmeasured rather than measured harmless.
    """
    if hasattr(edge_results, "to_dict"):
        edge_results = edge_results.to_dict()

    if isinstance(edge_results, dict):
        edge_results = edge_results.get("edge_criticality", [])

    out: Dict[Tuple[str, str], float] = {}
    for e in edge_results or []:
        if hasattr(e, "to_dict"):
            e = e.to_dict()
        if not isinstance(e, dict):
            continue
        if not e.get("evaluated", True):
            continue
        src, tgt = e.get("source"), e.get("target")
        if src is None or tgt is None:
            continue
        out[(str(src), str(tgt))] = float(e.get("combined_impact", 0.0))
    return out


def extract_structural_metrics_dict(structural_result) -> Dict[str, Dict[str, float]]:
    """Normalise StructuralAnalyzer output to the flat dict expected by this module."""
    if isinstance(structural_result, dict) and "structural_analysis" in structural_result:
        structural_result = structural_result["structural_analysis"]
    out: Dict[str, Dict[str, float]] = {}

    def _from_component(comp):
        def _get(obj, attr, default=0.0):
            if isinstance(obj, dict):
                if attr in obj:
                    return obj[attr]
                # Older/reduced serializations nest a subset of metrics under
                # "metrics" instead of the flat StructuralMetrics shape; fall
                # back there before giving up, so a stale cache degrades to
                # partial data instead of an all-zero feature vector.
                nested = obj.get("metrics")
                if isinstance(nested, dict) and attr in nested:
                    return nested[attr]
                return default
            return getattr(obj, attr, default)

        return {
            "pagerank": float(_get(comp, "pagerank")),
            "reverse_pagerank": float(_get(comp, "reverse_pagerank")),
            "betweenness_centrality": float(_get(comp, "betweenness")),
            "closeness_centrality": float(_get(comp, "closeness")),
            "eigenvector_centrality": float(_get(comp, "eigenvector")),
            "in_degree_centrality": float(_get(comp, "in_degree")),
            "out_degree_centrality": float(_get(comp, "out_degree")),
            "clustering_coefficient": float(_get(comp, "clustering_coefficient")),
            "ap_c_score": float(_get(comp, "ap_c_directed")),
            "ap_c_directed": float(_get(comp, "ap_c_directed")),
            "cdi": float(_get(comp, "cdi")),
            "mpci": float(_get(comp, "mpci")),
            "path_complexity": float(_get(comp, "path_complexity")),
            "fan_out_criticality": float(_get(comp, "fan_out_criticality")),
            "bridge_ratio": float(_get(comp, "bridge_ratio")),
            "qos_weight": float(_get(comp, "weight", 1.0)),
            "qos_weight_in": float(_get(comp, "dependency_weight_in")),
            "qos_weight_out": float(_get(comp, "dependency_weight_out")),
            "loc_norm": float(_get(comp, "loc_norm")),
            "complexity_norm": float(_get(comp, "complexity_norm")),
            "instability_code": float(_get(comp, "instability_code")),
            "lcom_norm": float(_get(comp, "lcom_norm")),
            "code_quality_penalty": float(_get(comp, "code_quality_penalty")),
            # Infrastructure attributes (used by _normalize_infra_features)
            "cpu_cores": float(_get(comp, "cpu_cores", 0)),
            "memory_gb": float(_get(comp, "memory_gb", 0)),
            "max_connections": float(_get(comp, "max_connections", 0)),
        }

    if hasattr(structural_result, "components"):
        components = structural_result.components
        if hasattr(components, "values"):
            components = components.values()
        for comp in components:
            # `id` is the graph node identifier StructuralMetrics is keyed by
            # everywhere else in this module; `name`/`component_id` are only
            # fallbacks for objects that lack it (StructuralMetrics never does).
            name = getattr(comp, "id", getattr(comp, "component_id", getattr(comp, "name", str(comp))))
            out[name] = _from_component(comp)
    elif isinstance(structural_result, dict):
        components = structural_result.get("components", structural_result)
        if isinstance(components, dict):
            for name, comp in components.items():
                out[name] = _from_component(comp)
        elif isinstance(components, list):
            for comp in components:
                name = getattr(comp, "id", getattr(comp, "component_id", getattr(comp, "name", str(comp))))
                if isinstance(comp, dict):
                    name = comp.get("id", comp.get("component_id", comp.get("name", str(comp))))
                out[name] = _from_component(comp)
        else:
            for name, comp in structural_result.items():
                out[name] = _from_component(comp)

    return out


def extract_rm_scores_dict(quality_result) -> Dict[str, Dict[str, float]]:
    """Normalise QualityAnalyzer output (RM scores) to a flat dict."""
    out: Dict[str, Dict[str, float]] = {}

    if hasattr(quality_result, "components"):
        for comp in quality_result.components:
            # `id` is the graph node identifier used everywhere else in this
            # module (see extract_structural_metrics_dict); `component_id`/
            # `name` are only fallbacks for objects that lack it — ComponentQuality
            # never does, it has `id`, not `component_id`.
            name = getattr(comp, "id", getattr(comp, "component_id", getattr(comp, "name", str(comp))))
            scores = getattr(comp, "scores", None)
            if scores is not None:
                out[name] = {
                    "overall": float(getattr(scores, "overall", 0.0)),
                    "reliability": float(getattr(scores, "reliability", 0.0)),
                    "maintainability": float(getattr(scores, "maintainability", 0.0)),
                    "fault_tolerance": float(getattr(scores, "fault_tolerance", 0.0)),
                    "availability": float(getattr(scores, "availability", 0.0)),
                }
    elif isinstance(quality_result, dict):
        components = quality_result.get("components", quality_result)
        if isinstance(components, list):
            for comp in components:
                name = getattr(comp, "id", getattr(comp, "component_id", getattr(comp, "name", str(comp))))
                if isinstance(comp, dict):
                    name = comp.get("id", comp.get("component_id", comp.get("name", str(comp))))
                scores = getattr(comp, "scores", None)
                if isinstance(comp, dict):
                    scores = comp.get("scores", comp)
                if scores is not None:
                    out[name] = {
                        "overall": float(getattr(scores, "overall", 0.0) if not isinstance(scores, dict) else scores.get("overall", 0.0)),
                        "reliability": float(getattr(scores, "reliability", 0.0) if not isinstance(scores, dict) else scores.get("reliability", 0.0)),
                        "maintainability": float(getattr(scores, "maintainability", 0.0) if not isinstance(scores, dict) else scores.get("maintainability", 0.0)),
                        "fault_tolerance": float(getattr(scores, "fault_tolerance", 0.0) if not isinstance(scores, dict) else scores.get("fault_tolerance", 0.0)),
                        "availability": float(getattr(scores, "availability", 0.0) if not isinstance(scores, dict) else scores.get("availability", 0.0)),
                    }
        elif isinstance(components, dict):
            for name, scores in components.items():
                if isinstance(scores, dict):
                    out[name] = {
                        "overall": float(scores.get("overall", 0.0)),
                        "reliability": float(scores.get("reliability", 0.0)),
                        "maintainability": float(scores.get("maintainability", 0.0)),
                        "fault_tolerance": float(scores.get("fault_tolerance", 0.0)),
                        "availability": float(scores.get("availability", 0.0)),
                    }

    return out
