"""
Structural Metric Role Registry

Declares, for every field on ``StructuralMetrics`` (saag.core.metrics), which
downstream consumer actually reads it. This module computes nothing and
changes no score — it is a declaration, in the spirit of
``saag.core.quality_model`` — but unlike that module it documents the *raw
metric* layer (Layer 0 QMEs) rather than the RM composite above it.

Why this exists: only 19 of ~50 StructuralMetrics fields feed Q(v). The rest
are computed, normalised, exported, and often visualised, but are read by
detectors, the GNN feature pipeline, or nothing but the dashboard. Without a
declared boundary, "how many metrics does this model use" has no single
answer — before this module, four different counts (11 / 12-13 / 13 / 14)
were independently published across docs/README.md, docs/structural-analysis.md,
docs/antipatterns.md and docs/remediation.md, none of them enumerating the
same set. structural-analysis.md §10 now derives its metric table from
``METRIC_ROLES`` instead of restating it by hand.

Roles are not mutually exclusive: a metric can be both a Q(v) input and a
GNN feature (most SCORING metrics are). Verified against source, not
inferred — see the call site cited in each entry's trailing comment.
"""

from __future__ import annotations

from enum import Enum
from typing import Dict, FrozenSet


class MetricRole(Enum):
    """What a StructuralMetrics field is actually used for, downstream."""

    #: Read by QualityAnalyzer._compute_rm — moves FT(v)/A(v)/M(v)/R(v)/Q(v).
    SCORING = "scoring"
    #: Read by AntiPatternDetector, but by no RM formula.
    DETECTION = "detection"
    #: Read by saag.prediction.data_preparation as a GNN node feature, but
    #: by no rule-based RM formula (SCORING metrics are also GNN features;
    #: this role is for metrics that are *only* a GNN input).
    GNN_FEATURE = "gnn_feature"
    #: Exported/serialised and, at most, rendered in the SMART dashboard
    #: (explorer tooltips, glossary, statistics charts). Read by no scoring
    #: or detection code. Not dead weight to a user browsing a graph — but
    #: it moves no score, and should never be described as though it does.
    DESCRIPTIVE = "descriptive"


#: One entry per StructuralMetrics field (id/name/type and the derived
#: @property fields are identifiers/views, not metrics, and are excluded).
#:
#: SCORING set verified against saag/analysis/analyzer.py:_compute_rm
#: (reverse_pagerank, betweenness, in_degree_raw, out_degree_raw,
#: clustering_coefficient, weight, ap_c_directed, cdi, mpci,
#: fan_out_criticality, dependency_weight_in [Topic FT branch],
#: dependency_weight_out, path_complexity, bridge_ratio) plus
#: StructuralAnalyzer._compute_code_quality_metrics's four CQP inputs
#: (loc_norm, complexity_norm, instability_code, lcom_norm feed
#: code_quality_penalty, itself a fifth SCORING metric). 19 fields total.
METRIC_ROLES: Dict[str, FrozenSet[MetricRole]] = {
    # === Centrality ===
    "pagerank": frozenset({MetricRole.GNN_FEATURE, MetricRole.DETECTION}),  # antipattern_detector.py:471 (CONCENTRATION_RISK)
    "reverse_pagerank": frozenset({MetricRole.SCORING, MetricRole.GNN_FEATURE}),  # analyzer.py: FT term, w=0.45
    "betweenness": frozenset({MetricRole.SCORING, MetricRole.GNN_FEATURE, MetricRole.DETECTION}),  # analyzer.py: M term, w=0.35; antipattern_detector.py:481 (GOD_COMPONENT)
    "closeness": frozenset({MetricRole.GNN_FEATURE}),
    "eigenvector": frozenset({MetricRole.GNN_FEATURE}),

    # === Degree ===
    "degree": frozenset({MetricRole.DESCRIPTIVE}),
    "in_degree": frozenset({MetricRole.GNN_FEATURE}),
    "out_degree": frozenset({MetricRole.GNN_FEATURE}),
    "in_degree_raw": frozenset({MetricRole.SCORING, MetricRole.DETECTION}),  # analyzer.py: FT term, w=0.30 (rank-normalised)
    "out_degree_raw": frozenset({MetricRole.SCORING, MetricRole.DETECTION}),  # analyzer.py: CDPot + instability terms; antipattern_detector.py:400 (FAILURE_HUB)

    # === Resilience ===
    "clustering_coefficient": frozenset({MetricRole.SCORING, MetricRole.GNN_FEATURE}),  # analyzer.py: M term, w=0.08 as (1-CC)
    "is_articulation_point": frozenset({MetricRole.DETECTION}),  # antipattern_detector.py:441 (SPOF)
    "is_directed_ap": frozenset({MetricRole.DETECTION}),  # antipattern_detector.py:438 (SPOF)
    "is_isolated": frozenset({MetricRole.DETECTION}),  # antipattern_detector.py:537 (ISOLATED)
    "bridge_count": frozenset({MetricRole.DESCRIPTIVE}),  # statistics.py:1463 (dashboard); feeds bridge_ratio internally, not read on its own downstream
    "bridge_ratio": frozenset({MetricRole.SCORING, MetricRole.GNN_FEATURE}),  # analyzer.py: A term, w=0.20
    "ap_c_directed": frozenset({MetricRole.SCORING, MetricRole.GNN_FEATURE}),  # analyzer.py: A term, w=0.25, and QSPOF = ap_c * qw (w=0.20)
    "cdi": frozenset({MetricRole.SCORING, MetricRole.GNN_FEATURE}),  # analyzer.py: A term, w=0.25 (v4: promoted to parity with ap_c_directed)
    "blast_radius": frozenset({MetricRole.DESCRIPTIVE}),  # saag/models.py ComponentFacade reporting only
    "cascade_depth": frozenset({MetricRole.DESCRIPTIVE}),  # saag/models.py ComponentFacade reporting only
    "publisher_spof": frozenset({MetricRole.DESCRIPTIVE}),  # smart dashboard explorer only

    # === Pub-Sub Topology ===
    "pubsub_degree": frozenset({MetricRole.DESCRIPTIVE}),  # smart dashboard explorer only
    "pubsub_betweenness": frozenset({MetricRole.DESCRIPTIVE}),  # smart dashboard explorer/statistics only
    "broker_exposure": frozenset({MetricRole.DESCRIPTIVE}),  # smart dashboard explorer only
    "fan_out_criticality": frozenset({MetricRole.SCORING, MetricRole.GNN_FEATURE}),  # analyzer.py: both terms of Topic FT(v)
    "topic_frequency_hz": frozenset({MetricRole.DESCRIPTIVE}),  # GNN's log1p_frequency_norm is re-derived from raw graph attrs, not this field
    "mpci": frozenset({MetricRole.SCORING, MetricRole.GNN_FEATURE}),  # analyzer.py: CDPot_enh multiplier
    "path_complexity": frozenset({MetricRole.SCORING, MetricRole.GNN_FEATURE}),  # analyzer.py: CouplingRisk_enh multiplier, delta=0.10
    "coupling_risk_enh": frozenset({MetricRole.DETECTION}),  # antipattern_detector.py:694 (UNSTABLE_INTERFACE); written back by analyzer, not itself an RM input
    "topic_subscriber_count": frozenset({MetricRole.DETECTION, MetricRole.GNN_FEATURE}),  # antipattern_detector.py (TOPIC_FANOUT, ORPHANED_TOPIC); feeds subscriber_count_norm
    "topic_publisher_count": frozenset({MetricRole.DETECTION, MetricRole.GNN_FEATURE}),  # antipattern_detector.py:583 (ORPHANED_TOPIC); feeds publisher_count_norm

    # === Infrastructure (Node/Broker only) ===
    "ip_address": frozenset({MetricRole.DESCRIPTIVE}),
    "cpu_cores": frozenset({MetricRole.GNN_FEATURE, MetricRole.DESCRIPTIVE}),
    "memory_gb": frozenset({MetricRole.GNN_FEATURE, MetricRole.DESCRIPTIVE}),
    "os_type": frozenset({MetricRole.DESCRIPTIVE}),
    "broker_type": frozenset({MetricRole.DESCRIPTIVE}),
    "max_connections": frozenset({MetricRole.GNN_FEATURE, MetricRole.DESCRIPTIVE}),
    "host": frozenset({MetricRole.DESCRIPTIVE}),

    # === Network load (Node/NetworkDevice; Phase 5c, saag.core.network) ===
    # The GNN re-derives these from the raw graph (data_preparation), not from here.
    "max_link_utilization": frozenset({MetricRole.DESCRIPTIVE}),
    "link_load_mbps": frozenset({MetricRole.DESCRIPTIVE}),

    # === Code Quality (Application/Library only) ===
    "loc_norm": frozenset({MetricRole.SCORING, MetricRole.GNN_FEATURE}),  # feeds code_quality_penalty, weight 0.10
    "complexity_norm": frozenset({MetricRole.SCORING, MetricRole.GNN_FEATURE}),  # feeds code_quality_penalty, weight 0.35
    "instability_code": frozenset({MetricRole.SCORING, MetricRole.GNN_FEATURE}),  # feeds code_quality_penalty, weight 0.30
    "lcom_norm": frozenset({MetricRole.SCORING, MetricRole.GNN_FEATURE}),  # feeds code_quality_penalty, weight 0.25
    "code_quality_penalty": frozenset({MetricRole.SCORING, MetricRole.GNN_FEATURE}),  # analyzer.py: M term, w=0.15
    "sqale_debt_ratio": frozenset({MetricRole.DESCRIPTIVE}),  # ingested/persisted; not read by cm_avg_cbo's CLAUDE.md gotcha class
    "bugs": frozenset({MetricRole.DESCRIPTIVE}),
    "vulnerabilities": frozenset({MetricRole.DESCRIPTIVE}),
    "duplicated_lines_density": frozenset({MetricRole.DESCRIPTIVE}),

    # === Weights ===
    "weight": frozenset({MetricRole.SCORING, MetricRole.GNN_FEATURE, MetricRole.DETECTION}),  # analyzer.py: A term (w=0.10) + QSPOF; antipattern_detector.py:605 (QOS_MISMATCH)
    "dependency_weight_in": frozenset({MetricRole.SCORING, MetricRole.GNN_FEATURE}),  # analyzer.py: Topic FT branch only, as w_in — summed in-edge QoS weight (all edge types), not publisher count — see structural-analysis.md §9.13
    "dependency_weight_out": frozenset({MetricRole.SCORING, MetricRole.GNN_FEATURE}),  # analyzer.py: M term, w=0.30
}


def role_of(metric: str) -> FrozenSet[MetricRole]:
    """Roles for a StructuralMetrics field name. Unknown fields return empty."""
    return METRIC_ROLES.get(metric, frozenset())


def metrics_with_role(role: MetricRole) -> FrozenSet[str]:
    """Every StructuralMetrics field carrying the given role."""
    return frozenset(m for m, roles in METRIC_ROLES.items() if role in roles)


#: The 19 metrics that move Q(v). This is the Tier-1 vector referenced
#: throughout docs/structural-analysis.md §11.2 and docs/criticality.md §4.3.
SCORING_METRICS: FrozenSet[str] = metrics_with_role(MetricRole.SCORING)
