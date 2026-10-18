"""
 Quality Analyzer

 Computes composite quality scores for the two RM (ISO/IEC 25010:2023) external
 quality characteristics — Reliability and Maintainability — and an overall
 quality score, then classifies every component using the Box-Plot method
 (adaptive, data-driven thresholds). Availability is scored and reported as a
 sub-characteristic of Reliability, not as a peer dimension; Vulnerability/
 Security is not part of this model (see saag/core/quality_model.py).

 Formulas (per component v):
     FT*(v) = 0.45×RPR + 0.30×DG_in + 0.25×CDPot             (Fault Tolerance v5, Application/Broker/Node/Library)
     FT_topic*(v) = 0.50×FOC_freq(v) + 0.50×CDPot_freq(v)     (Fault Tolerance v6, Topic nodes)
                   where FOC_freq(t) = log1p(f(t))·s(t) / max_t[log1p(f(t))·s(t)],
                   f(t) = message rate (Hz), s(t) = subscriber count
     A*(v) = 0.25×AP_c_directed + 0.20×QSPOF + 0.20×BR + 0.25×CDI + 0.10×w(v) (Availability v4, sub-characteristic)
                   CDI is computed for every node in the main graph component, not only
                   articulation points — a hard AP-gate leaves CDI 0.0 for every node whose
                   removal does not literally disconnect the graph, which degenerates to a
                   near-constant A(v) in redundant multi-publisher pub-sub topologies.
     R*(v) = r_alpha×FT*(v) + (1-r_alpha)×A*(v)               (Reliability, hierarchical; r_alpha default 0.36)
     M*(v) = 0.35×BT  + 0.30×w_out + 0.15×CQP + 0.12×CR + 0.08×(1–CC) (Maintainability v6)
     Q*(v) = w_R×R*(v) + w_M×M*(v)                            (Overall; w_R, w_M default 0.80, 0.20)

 M*(v) v7 change (Hardening Phase):
     CQP formula updated to include LOC:
     CQP = 0.10·loc_norm + 0.35·complexity_norm + 0.30·instability_code + 0.25·lcom_norm
     Library Ca/Ce clarified as internal static analysis coupling.
     Single-node populations handle zero-span by returning 1.0 (most critical).

 FT*(v) v5 change:
     w_in (QoS-weighted in-degree) removed from FT*(v).
     w_in was assigned to the retired V*(v) as QADS (orthogonality resolved).
     DG_in (raw in-degree, normalised) replaces it at weight 0.30.
     RPR weight increases from 0.40 → 0.45 to compensate.
     CDPot retains depth signal at 0.25.

 Classification (Box-Plot):
     CRITICAL : score > Q3 + k×IQR
     HIGH     : score > Q3
     MEDIUM   : score > Median
     LOW      : score > Q1
     MINIMAL  : score ≤ Q1
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, replace as dataclass_replace
from datetime import datetime
from typing import Dict, List, Optional, Any

import networkx as nx

from .classifier import BoxPlotClassifier
from saag.core.criticality import CriticalityLevel, BoxPlotStats
from saag.core.metrics import (
    QualityScores,
    QualityLevels,
    ComponentQuality,
    EdgeQuality,
    StructuralMetrics,
    EdgeMetrics,
    ClassificationSummary,
)
from .models import StructuralAnalysisResult, QualityAnalysisResult
from saag.core.layers import AnalysisLayer
from saag.core.models import COUPLING_PATH_DELTA
from .weight_calculator import AHPProcessor, QualityWeights
from saag.core.quality_model import Provenance, derive_rm_weights


# ---------------------------------------------------------------------------
# Criticality Profile
# ---------------------------------------------------------------------------

@dataclass
class CriticalityProfile:
    """
    Per-component criticality flags for Reliability's two sub-characteristics
    (Fault Tolerance, Availability), Maintainability, and the composite.

    Each flag is True if the component's score exceeds the upper fence
    (Q3 + k×IQR) of the corresponding dimension's box-plot distribution.
    ``r_crit`` is Reliability's own (hierarchical) composite flag; ``ft_crit``
    and ``a_crit`` are its sub-characteristic flags, kept because the pattern
    vocabulary below (SPOF, Fragile Hub, ...) depends on distinguishing "hub
    of propagation reach" from "structural SPOF" — both currently collapse
    into Reliability alone.

    The ``pattern`` property maps the (FT, A, M) flag triple to a named
    architectural risk pattern useful for triage and remediation guidance.
    """
    ft_crit: bool = False
    a_crit: bool = False
    m_crit: bool = False
    r_crit: bool = False
    q_crit: bool = False

    _PATTERNS = {
        (True,  True,  True):  "Total Hub",
        (True,  False, False): "Fault-Tolerance Hub",
        (False, True,  False): "SPOF",
        (False, False, True):  "Bottleneck",
        (True,  True,  False): "Fragile Hub",
        (False, True,  True):  "Fragile Bottleneck",
    }

    @property
    def pattern(self) -> str:
        """Named pattern from (FT_crit, A_crit, M_crit) tuple."""
        flag = (self.ft_crit, self.a_crit, self.m_crit)
        return self._PATTERNS.get(flag, "Composite Risk")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ft_crit": self.ft_crit,
            "a_crit": self.a_crit,
            "m_crit": self.m_crit,
            "r_crit": self.r_crit,
            "q_crit": self.q_crit,
            "pattern": self.pattern,
        }


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Minimum sample size for box-plot classification. Below this, we use
#: fixed percentile thresholds instead.
MIN_BOXPLOT_SAMPLE = 12


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------

class QualityAnalyzer:
    """
    Computes quality scores and box-plot classifications for components / edges.

    Uses configurable weights (manual or AHP-derived) and the BoxPlotClassifier
    for adaptive threshold determination.

    Args:
        k_factor: IQR multiplier for outlier fence (default 0.75).
        weights: Manually specified QualityWeights (overridden by use_ahp).
        use_ahp: If True, compute weights from default AHP matrices.
        normalization_method: 'robust' (default, rank-based) or 'max'.
    """

    def __init__(self, k_factor: float = 0.75, weights: Optional[QualityWeights] = None, use_ahp: bool = False, ahp_shrinkage: float = 0.7, normalization_method: str = "robust", winsorize: bool = True, winsorize_limit: float = 0.05, adapt_qos_weights: bool = True, equal_weights: bool = False, domain_weights: Optional[str] = None) -> None:
        """
        Args:
            domain_weights: A deployment-domain label (e.g. "healthcare",
                "autoware_ros2" — the values scenario metadata.domain carries)
                to derive the composite q_* weights from ISO/IEC 25019:2023
                stakeholder priorities via saag.core.quality_model, in place of
                the static/AHP default. None (the default) leaves scoring
                byte-identical to today. See docs/criticality.md §3.5 for what
                this derivation is and, importantly, is not: it is DECLARED
                (a design judgement, unvalidated), not a further prediction
                stage — see saag.core.quality_model.QIU_PROJECTION.
        """
        weights = weights or QualityWeights()
        if equal_weights:
            # Copy rather than mutate: `weights` may be a caller-owned
            # instance shared across multiple QualityAnalyzer construction
            # calls, and mutating it in place would corrupt every other
            # analyzer holding a reference to the same object.
            # r_alpha is included: "equal weights" must mean equal at every
            # level, not just at the composite — otherwise it would silently
            # keep the 0.36/0.64 FT-vs-A stakeholder judgement baked into R.
            weights = dataclass_replace(
                weights,
                q_reliability=0.5, q_maintainability=0.5,
                r_alpha=0.5,
            )

        self.classifier = BoxPlotClassifier(k_factor=k_factor)
        self.weights = (
            AHPProcessor(shrinkage_factor=ahp_shrinkage).compute_weights() if use_ahp
            else weights
        )
        self.normalization_method = normalization_method
        self.winsorize = winsorize
        self.winsorize_limit = winsorize_limit
        self.adapt_qos_weights = adapt_qos_weights
        self.domain_weights = domain_weights
        self._logger = logging.getLogger(__name__)

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def analyze(self, structural_result: StructuralAnalysisResult, context: Optional[str] = None, run_sensitivity: bool = False, sensitivity_perturbations: int = 200, sensitivity_noise: float = 0.05) -> QualityAnalysisResult:
        """
        Run quality analysis on structural results.

        Steps:
            1. Normalise raw metrics across components
            2. Compute R, M, A, V scores per component
            3. Classify components via box-plot on each dimension + overall
            4. Analyse and classify edges (with endpoint quality context)
            5. Build classification summary
            6. (Optional) Run weight sensitivity analysis
        """
        # Handle both Enum and string for backward/internal compatibility
        layer_val = structural_result.layer.value if hasattr(structural_result.layer, "value") else str(structural_result.layer)
        layer_name = layer_val
        ctx = context or f"{layer_name} layer analysis"

        # --- Domain-derived weight adjustment (Layer 3, DECLARED) --------
        # Applied before QoS adaptation so the two compose in one fixed
        # order rather than silently overriding each other depending on
        # call order; see saag.core.quality_model.
        effective_weights = self.weights
        domain_weighting: Optional[Dict[str, Any]] = None
        if self.domain_weights:
            effective_weights, was_derived = derive_rm_weights(
                self.domain_weights, effective_weights
            )
            domain_weighting = {
                "domain": self.domain_weights,
                "derived": was_derived,
                "provenance": Provenance.DECLARED.value,
            }

        # --- QoS-aware weight adjustment ----------------------------------
        if self.adapt_qos_weights and hasattr(structural_result, "qos_profile"):
            effective_weights = self._derive_qos_weights(
                structural_result.qos_profile, effective_weights
            )

        # Store effective weights for the duration of this analysis
        original_weights = self.weights
        self.weights = effective_weights

        try:
            # --- Component analysis -----------------------------------------
            raw_components = list(structural_result.components.values())
            norm = self._normalize(raw_components)

            # Continuous AP scores/CDI are now precomputed in StructuralAnalyzer
            components = self._score_and_classify_components(raw_components, norm)

            # --- Edge analysis (with endpoint quality context) --------------
            raw_edges = list(structural_result.edges.values())
            comp_quality_map = {c.id: c for c in components}
            edges = self._score_and_classify_edges(raw_edges, comp_quality_map)

            # --- Summary ----------------------------------------------------
            summary = self._build_summary(components, edges)

            # --- Sensitivity (optional) -------------------------------------
            sensitivity = None
            if run_sensitivity:
                sensitivity = self._sensitivity_analysis(
                    raw_components, norm,
                    n_perturbations=sensitivity_perturbations,
                    noise_std=sensitivity_noise,
                )

            return QualityAnalysisResult(
                timestamp=datetime.now().isoformat(),
                layer=layer_name,
                context=ctx,
                components=components,
                edges=edges,
                classification_summary=summary,
                weights=self.weights,
                sensitivity=sensitivity,
                domain_weighting=domain_weighting,
            )
        finally:
            self.weights = original_weights

    # ------------------------------------------------------------------
    # Component scoring
    # ------------------------------------------------------------------

    def _score_and_classify_components(self, metrics_list: List[StructuralMetrics], norm: Dict[str, float]) -> List[ComponentQuality]:
        """Score every component, then classify using box-plot per dimension."""
        if not metrics_list:
            return []

        # Compute raw RM scores
        scored: List[ComponentQuality] = []
        for m in metrics_list:
            scores = self._compute_rm(m, norm)
            scored.append(ComponentQuality(
                id=m.id,
                type=m.type,
                scores=scores,
                levels=QualityLevels(),  # placeholder, filled below
                structural=m,
            ))

        # Classify each dimension with box-plot (or percentile fallback).
        # fault_tolerance and availability are Reliability's sub-characteristics —
        # classified here too so CriticalityProfile can distinguish them.
        dim_keys = ["reliability", "maintainability", "fault_tolerance", "availability", "overall"]
        level_maps: Dict[str, Dict[str, CriticalityLevel]] = {}
        upper_fences: Dict[str, float] = {}  # per-dimension upper-fence for CriticalityProfile

        use_percentile_fallback = len(scored) < MIN_BOXPLOT_SAMPLE

        # Tiers are assigned *within* node type, not across the whole layer. A
        # single fence spanning Applications, Brokers, Topics, Nodes and Libraries
        # ranks each component against populations whose score scale it does not
        # share; on this corpus that moves 62.8% of components to a different tier
        # and changes CRITICAL/HIGH membership for 19.0% of them
        # (results/tier_pooling_check.json). Groups below the classifier's minimum
        # fall back to the pooled fence rather than being scored on a handful of
        # samples, so every component still receives a level.
        group_fences: Dict[str, Dict[str, float]] = {}

        for dim in dim_keys:
            data = [
                {"id": c.id, "score": getattr(c.scores, dim), "type": c.type}
                for c in scored
            ]
            if use_percentile_fallback:
                level_maps[dim] = self._percentile_classify(data)
                scores_sorted = sorted(d["score"] for d in data)
                n_d = len(scores_sorted)
                # Top ~10 % treated as the "upper fence" in the percentile path
                upper_fences[dim] = scores_sorted[max(0, int(n_d * 0.90))] if n_d > 0 else 1.0
            else:
                result = self.classifier.classify(data, metric_name=dim, group_key="type")
                level_maps[dim] = {item.id: item.level for item in result.items}
                # Pooled fence stays the fallback for types the classifier merged.
                upper_fences[dim] = result.stats.upper_fence if result.stats else 1.0
                group_fences[dim] = {
                    g: st.upper_fence for g, st in result.group_stats.items()
                }

        def _fence(dim: str, ctype: str) -> float:
            """The fence this component was actually classified against."""
            return group_fences.get(dim, {}).get(ctype, upper_fences.get(dim, 1.0))

        # Apply classified levels and CriticalityProfile
        for c in scored:
            c.levels = QualityLevels(
                reliability=level_maps["reliability"].get(c.id, CriticalityLevel.MINIMAL),
                maintainability=level_maps["maintainability"].get(c.id, CriticalityLevel.MINIMAL),
                fault_tolerance=level_maps["fault_tolerance"].get(c.id, CriticalityLevel.MINIMAL),
                availability=level_maps["availability"].get(c.id, CriticalityLevel.MINIMAL),
                overall=level_maps["overall"].get(c.id, CriticalityLevel.MINIMAL),
            )
            # CriticalityProfile: True iff score > the upper fence of the
            # population this component was classified against — the same fence
            # that produced its level, so flag and level cannot disagree.
            c.profile = CriticalityProfile(
                ft_crit=c.scores.fault_tolerance > _fence("fault_tolerance", c.type),
                a_crit=c.scores.availability     > _fence("availability",    c.type),
                m_crit=c.scores.maintainability  > _fence("maintainability", c.type),
                r_crit=c.scores.reliability      > _fence("reliability",     c.type),
                q_crit=c.scores.overall          > _fence("overall",         c.type),
            )

        # Sort by overall score descending
        scored.sort(key=lambda c: c.scores.overall, reverse=True)
        return scored


    def _compute_rm(self, m: StructuralMetrics, norm: Dict[str, Any]) -> QualityScores:
        """
        Compute Reliability (hierarchical: Fault Tolerance + Availability) and
        Maintainability scores, and the overall composite.

        Design principles:
            - Each raw metric maps to at most one dimension (orthogonality) — with
              one deliberate, bounded exception: QSPOF = AP_c_directed x w(v) is an
              interaction term that reuses ap_c_directed, so ap_c_directed's true
              effective weight in A(v) ranges from w.a_ap_c_directed (qw=0) to
              w.a_ap_c_directed + w.a_qspof (qw=1). This is intentional (QSPOF exists
              so structural SPOF risk is not masked at low QoS weight, pinned by
              tests/test_availability_dimension.py) and is kept small by the v4
              rebalance (a_qspof=0.20, down from 0.25) rather than removed.
            - AP is continuous (ap_c = ap_c_directed).
            - A(v) v4: QSPOF + directed SPOF + BR + CDI (CDI ungated, see
              StructuralAnalyzer._compute_continuous_ap_scores).
        """
        w = self.weights

        def _n(val: float, key: str) -> float:
            """Look up pre-normalised (rank-based or max-based) value."""
            if isinstance(norm, dict) and key in norm:
                entry = norm[key]
                if isinstance(entry, dict):
                    # Rank-based: lookup per component id → rank score
                    return entry.get(m.id, 0.0)
                else:
                    # Max-based: divide by stored max
                    return val / entry if entry > 0 else 0.0
            return 0.0

        # Normalised values
        rpr  = _n(m.reverse_pagerank, "reverse_pagerank")
        bt   = _n(m.betweenness,      "betweenness")
        id_n = _n(m.in_degree_raw,    "in_degree")
        od_n = _n(m.out_degree_raw,   "out_degree")
        cc   = m.clustering_coefficient
        qw   = _n(m.weight,           "weight")

        # New precomputed Tier 1 signals
        ap_c = m.ap_c_directed
        cdi = m.cdi
        mpci = m.mpci
        foc = m.fan_out_criticality

        # --- Fault Tolerance: FT*(v) v6 = RPR + DG_in + CDPot_enh ---
        if m.type == "Topic":
            # FT_topic(v) = ft_topic_foc x FOC(v) + ft_topic_cdpot x CDPot_topic(v)
            # CDPot_topic(v) = FOC(v) × (1 − min(w_in_norm(v), 1))
            # w_in_norm is NOT publisher count: dependency_weight_in is the summed
            # in-edge QoS weight across every incoming edge type (PUBLISHES_TO and
            # broker ROUTES), rank-normalised against the whole component population,
            # not against topics alone (see structural_analyzer.py's dep_weight_in
            # accumulation). It correlates with publisher redundancy but is not it —
            # documented as w_in in docs/structural-analysis.md §9.13.
            w_in_norm = _n(m.dependency_weight_in, "w_in")
            cdpot_topic = foc * (1.0 - min(w_in_norm, 1.0))
            FT = w.ft_topic_foc * foc + w.ft_topic_cdpot * cdpot_topic
        else:
            # Standard Fault Tolerance formula (Application, Broker, Node, Library)
            # FT(v) v6: RPR + DG_in + CDPot_enh (see §11.2 of structural-analysis.md)
            # DEPENDS_ON edges point dependent→dependency; failure propagates against edge
            # direction, so RPR on G^T is the correct cascade-reach estimator.
            _eps_r = 1e-9
            _id_raw = float(m.in_degree_raw)
            _od_raw = float(m.out_degree_raw)
            cdpot_base = ((rpr + id_n) / 2.0) * (1.0 - min(_od_raw / max(_id_raw, _eps_r), 1.0))
            cdpot_enh = min(cdpot_base * (1.0 + mpci), 1.0)
            FT = (
                w.ft_reverse_pagerank * rpr
                + w.ft_in_degree * id_n
                + w.ft_cdpot * cdpot_enh
            )

        # Maintainability: M(v) v6 — adds CQP as 5th signal
        w_out_n = _n(m.dependency_weight_out, "w_out")
        cqp = m.code_quality_penalty
        _eps = 1e-9
        # Instability based on raw counts (deployment graph)
        _raw_total = float(m.in_degree_raw + m.out_degree_raw)
        _instability = m.out_degree_raw / (_raw_total + _eps)
        coupling_risk = 1.0 - abs(2.0 * _instability - 1.0)
        # Enrich coupling risk with path complexity (Issue 4/7)
        coupling_risk = min(1.0, coupling_risk * (1.0 + COUPLING_PATH_DELTA * m.path_complexity))
        m.coupling_risk_enh = coupling_risk  # Persisted for UNSTABLE_INTERFACE detection (antipattern_detector.py)
        M = (
            w.m_betweenness * bt
            + w.m_w_out * w_out_n
            + w.m_code_quality_penalty * cqp
            + w.m_coupling_risk * coupling_risk
            + w.m_clustering * (1.0 - cc)
        )

        # Availability: A(v) v4 — 5-term additive formula, a Reliability sub-characteristic
        # A(v) = 0.25·AP_c_directed + 0.20·QSPOF + 0.20·BR + 0.25·CDI + 0.10·w(v)
        qspof = ap_c * qw
        A = (
            w.a_ap_c_directed * ap_c
            + w.a_qspof       * qspof
            + w.a_bridge_ratio * m.bridge_ratio
            + w.a_cdi         * cdi
            + w.a_qos_weight  * qw
        )

        # Reliability: hierarchical combination of Fault Tolerance and Availability
        R = w.r_alpha * FT + (1.0 - w.r_alpha) * A

        Q = w.q_reliability * R + w.q_maintainability * M

        return QualityScores(
            reliability=R,
            maintainability=M,
            fault_tolerance=FT,
            availability=A,
            overall=Q,
        )

    # ------------------------------------------------------------------
    # Edge scoring (RM-aligned with endpoint context)
    # ------------------------------------------------------------------

    def _score_and_classify_edges(self, edges_list: List[EdgeMetrics], comp_quality_map: Optional[Dict[str, ComponentQuality]] = None) -> List[EdgeQuality]:
        """
        Score and classify edges using endpoint-aware RM formulas.

        When component quality scores are available, edge scores incorporate
        the criticality of connected endpoints. Otherwise falls back to
        edge-intrinsic metrics only.
        """
        if not edges_list:
            return []

        w = self.weights
        edges: List[EdgeQuality] = []

        for em in edges_list:
            bridge_factor = 1.0 if em.is_bridge else 0.0
            bt_norm = em.betweenness  # already normalised by NetworkX
            edge_weight = em.weight

            # Endpoint quality scores (fallback to 0.0 if unavailable)
            src_q = comp_quality_map.get(em.source) if comp_quality_map else None
            tgt_q = comp_quality_map.get(em.target) if comp_quality_map else None

            src_ft = src_q.scores.fault_tolerance if src_q else 0.0
            tgt_ft = tgt_q.scores.fault_tolerance if tgt_q else 0.0
            src_a = src_q.scores.availability if src_q else 0.0
            tgt_a = tgt_q.scores.availability if tgt_q else 0.0

            # Edge RM, hierarchical to mirror the node-level model
            e_fault_tolerance = (
                w.e_betweenness * bt_norm
                + w.e_bridge * edge_weight
                + w.e_endpoint * max(src_ft, tgt_ft)
            )
            e_availability = (
                w.e_bridge * bridge_factor
                + w.e_endpoint * min(src_a, tgt_a)
            )
            e_reliability = w.r_alpha * e_fault_tolerance + (1.0 - w.r_alpha) * e_availability
            e_maintainability = (
                w.e_betweenness * bt_norm
                + w.e_bridge * bridge_factor
                + w.e_qos_weight * edge_weight
            )

            overall = (
                w.q_reliability * e_reliability
                + w.q_maintainability * e_maintainability
            )

            edges.append(EdgeQuality(
                source=em.source,
                target=em.target,
                source_type=em.source_type,
                target_type=em.target_type,
                dependency_type=em.dependency_type,
                scores=QualityScores(
                    reliability=e_reliability,
                    maintainability=e_maintainability,
                    fault_tolerance=e_fault_tolerance,
                    availability=e_availability,
                    overall=overall,
                ),
                structural=em,
            ))

        # Classify
        if edges:
            data = [{"id": e.id, "score": e.scores.overall} for e in edges]
            if len(edges) < MIN_BOXPLOT_SAMPLE:
                level_map = self._percentile_classify(data)
            else:
                result = self.classifier.classify(data, metric_name="edge_criticality")
                level_map = {item.id: item.level for item in result.items}
            for edge in edges:
                edge.level = level_map.get(edge.id, CriticalityLevel.MINIMAL)

        edges.sort(key=lambda e: e.scores.overall, reverse=True)
        return edges

    # ------------------------------------------------------------------
    # Classification helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _percentile_classify(data: List[Dict[str, Any]]) -> Dict[str, CriticalityLevel]:
        """
        Fixed percentile classification for small samples (< MIN_BOXPLOT_SAMPLE).

        Uses rank-based percentile thresholds instead of box-plot quartiles
        to avoid unreliable quartile estimates with few data points.
        """
        sorted_data = sorted(data, key=lambda d: d["score"], reverse=True)
        n = len(sorted_data)
        level_map: Dict[str, CriticalityLevel] = {}

        for i, d in enumerate(sorted_data):
            pct = i / n if n > 0 else 0.0
            if pct < 0.10:
                level = CriticalityLevel.CRITICAL
            elif pct < 0.25:
                level = CriticalityLevel.HIGH
            elif pct < 0.50:
                level = CriticalityLevel.MEDIUM
            elif pct < 0.75:
                level = CriticalityLevel.LOW
            else:
                level = CriticalityLevel.MINIMAL
            level_map[d["id"]] = level

        return level_map

    # ------------------------------------------------------------------
    # Sensitivity analysis
    # ------------------------------------------------------------------

    def _sensitivity_analysis(self, components: List[StructuralMetrics], norm: Dict[str, float], n_perturbations: int = 200, noise_std: float = 0.05) -> Dict[str, Any]:
        """
        Measure ranking stability under weight perturbations.

        Perturbs all AHP weights by Gaussian noise, recomputes Q(v),
        and reports how stable the top-K rankings are.

        Returns:
            Dictionary with top5_stability, mean_kendall_tau, std_kendall_tau.
        """
        if len(components) < 3:
            return {"top5_stability": 1.0, "mean_kendall_tau": 1.0, "std_kendall_tau": 0.0}

        # Original ranking
        original_scores = {}
        for m in components:
            scores = self._compute_rm(m, norm).overall
            original_scores[m.id] = scores

        original_ranking = sorted(original_scores, key=original_scores.get, reverse=True)
        original_top5 = set(original_ranking[:5])

        stable_count = 0
        taus = []

        for _ in range(n_perturbations):
            perturbed_weights = self._perturb_weights(noise_std)
            perturbed_scores = {}
            for m in components:
                perturbed_scores[m.id] = self._compute_rm_with_weights(
                    m, norm, perturbed_weights
                ).overall

            perturbed_ranking = sorted(perturbed_scores, key=perturbed_scores.get, reverse=True)
            perturbed_top5 = set(perturbed_ranking[:5])

            if perturbed_top5 == original_top5:
                stable_count += 1

            # Kendall tau approximation via rank displacement
            tau = self._kendall_tau(original_ranking, perturbed_ranking)
            taus.append(tau)

        import statistics as stats_module
        return {
            "top5_stability": stable_count / n_perturbations,
            "mean_kendall_tau": stats_module.mean(taus) if taus else 1.0,
            "std_kendall_tau": stats_module.stdev(taus) if len(taus) > 1 else 0.0,
            "n_perturbations": n_perturbations,
            "noise_std": noise_std,
        }

    def _perturb_weights(self, noise_std: float) -> QualityWeights:
        """Create a perturbed copy of current weights with Gaussian noise.

        r_alpha is perturbed alongside q_reliability/q_maintainability: it is
        the largest new free parameter in the RM composite (it governs FT vs
        A within Reliability), and omitting it would let this report claim
        stability under weight noise without ever exercising it.
        """
        w = self.weights

        def _perturb_group(*values: float) -> list:
            perturbed = [max(0.01, v + random.gauss(0, noise_std)) for v in values]
            total = sum(perturbed)
            return [p / total for p in perturbed]

        ft_weights = _perturb_group(
            w.ft_reverse_pagerank, w.ft_in_degree, w.ft_cdpot,
        )
        # Topic FT is a separate 2-term formula (FOC + CDPot_topic, no RPR/in-degree
        # signal available for Topic nodes) — perturbed as its own group rather than
        # folded into ft_weights above, since the two formulas share no terms.
        ft_topic_weights = _perturb_group(w.ft_topic_foc, w.ft_topic_cdpot)
        m_weights = _perturb_group(
            w.m_betweenness, w.m_w_out, w.m_code_quality_penalty,
            w.m_coupling_risk, w.m_clustering,
        )
        a_weights = _perturb_group(
            w.a_ap_c_directed, w.a_qspof, w.a_bridge_ratio, w.a_cdi, w.a_qos_weight,
        )
        q_weights = _perturb_group(w.q_reliability, w.q_maintainability)
        r_alpha = min(0.99, max(0.01, w.r_alpha + random.gauss(0, noise_std)))

        return QualityWeights(
            ft_pagerank=0.0, ft_reverse_pagerank=ft_weights[0], ft_in_degree=ft_weights[1],
            ft_cdpot=ft_weights[2],
            ft_topic_foc=ft_topic_weights[0], ft_topic_cdpot=ft_topic_weights[1],
            r_alpha=r_alpha,
            m_betweenness=m_weights[0], m_w_out=m_weights[1],
            m_code_quality_penalty=m_weights[2],
            m_coupling_risk=m_weights[3], m_clustering=m_weights[4],
            m_out_degree=0.0,
            a_ap_c_directed=a_weights[0], a_qspof=a_weights[1],
            a_bridge_ratio=a_weights[2], a_cdi=a_weights[3], a_qos_weight=a_weights[4],
            q_reliability=q_weights[0], q_maintainability=q_weights[1],
        )

    def _compute_rm_with_weights(self, m: StructuralMetrics, norm: Dict[str, float], weights: QualityWeights) -> QualityScores:
        """
        Version of _compute_rm that accepts arbitrary weights (for sensitivity analysis).
        """
        # Save real weights, swap in perturbed ones, then swap back
        old_weights = self.weights
        self.weights = weights
        try:
            return self._compute_rm(m, norm)
        finally:
            self.weights = old_weights

    @staticmethod
    def _kendall_tau(ranking_a: List[str], ranking_b: List[str]) -> float:
        """
        Compute Kendall's tau-b between two rankings.

        Simplified O(n²) implementation sufficient for the component counts
        we deal with (typically < 1000).
        """
        n = len(ranking_a)
        if n < 2:
            return 1.0

        rank_b = {item: i for i, item in enumerate(ranking_b)}
        concordant = 0
        discordant = 0

        for i in range(n):
            for j in range(i + 1, n):
                a_i, a_j = ranking_a[i], ranking_a[j]
                if a_i not in rank_b or a_j not in rank_b:
                    continue
                b_diff = rank_b[a_i] - rank_b[a_j]
                if b_diff < 0:
                    concordant += 1
                elif b_diff > 0:
                    discordant += 1

        total = concordant + discordant
        if total == 0:
            return 1.0
        return (concordant - discordant) / total

    # ------------------------------------------------------------------
    # Normalization
    # ------------------------------------------------------------------

    def _normalize(self, components: List[StructuralMetrics]) -> Dict[str, Any]:
        """
        Compute normalization factors based on the configured method.

        Steps:
            1. (Optional) Winsorize: caps extreme outliers at (1 - limit) percentile.
            2. Scale: 'max' or 'robust' (rank-based).
        """
        if self.winsorize:
            components = self._winsorize_components(components, limit=self.winsorize_limit)

        if self.normalization_method == "robust":
            return self._normalize_robust(components)
        return self._normalize_max(components)

    @staticmethod
    def _normalize_max(components: List[StructuralMetrics]) -> Dict[str, float]:
        """Compute per-metric maximums for max-normalization."""
        if not components:
            return {}
        return {
            "pagerank": max((c.pagerank for c in components), default=0),
            "reverse_pagerank": max((c.reverse_pagerank for c in components), default=0),
            "betweenness": max((c.betweenness for c in components), default=0),
            "closeness": max((c.closeness for c in components), default=0),
            "eigenvector": max((c.eigenvector for c in components), default=0),
            "in_degree": max((c.in_degree_raw for c in components), default=0),
            "out_degree": max((c.out_degree_raw for c in components), default=0),
            "total_degree": max((c.total_degree_raw for c in components), default=0),
            "weight": max((c.weight for c in components), default=1.0),
            "w_out": max((c.dependency_weight_out for c in components), default=1.0),
            "w_in": max((c.dependency_weight_in for c in components), default=1.0),
        }

    @staticmethod
    def _normalize_robust(components: List[StructuralMetrics]) -> Dict[str, Dict[str, float]]:
        """
        True rank-based normalization. For each metric, replace raw values with
        rank(v) / N where N = number of components. This gives a uniform [0, 1]
        distribution per metric, making the score robust to outliers.

        Returns a nested dict: {metric_name: {component_id: normalised_rank_score}}
        The _n() helper in _compute_rm detects this structure and looks up
        the pre-computed rank score instead of dividing by max.
        """
        if not components:
            return {}

        n = len(components)

        def _rank_normalise(values: List[tuple]) -> Dict[str, float]:
            """Assign rank/(n-1) with average-rank tie-breaking."""
            # values: list of (component_id, raw_value)
            sorted_vals = sorted(values, key=lambda x: x[1])
            ranks: Dict[str, float] = {}
            i = 0
            while i < n:
                j = i
                # Find extent of ties
                while j < n - 1 and sorted_vals[j][1] == sorted_vals[j + 1][1]:
                    j += 1
                avg_rank = (i + j) / 2  # 0-based average rank
                for k in range(i, j + 1):
                    cid = sorted_vals[k][0]
                    ranks[cid] = avg_rank / (n - 1) if n > 1 else 0.0
                i = j + 1
            return ranks

        metrics = {
            "pagerank":        [(c.id, c.pagerank)          for c in components],
            "reverse_pagerank":[(c.id, c.reverse_pagerank)  for c in components],
            "betweenness":     [(c.id, c.betweenness)       for c in components],
            "closeness":       [(c.id, c.closeness)         for c in components],
            "eigenvector":     [(c.id, c.eigenvector)       for c in components],
            "in_degree":       [(c.id, c.in_degree_raw)     for c in components],
            "out_degree":      [(c.id, c.out_degree_raw)    for c in components],
            "total_degree":    [(c.id, c.total_degree_raw)  for c in components],
            "weight":          [(c.id, c.weight)            for c in components],
            "w_out":           [(c.id, c.dependency_weight_out) for c in components],
            "w_in":            [(c.id, c.dependency_weight_in)  for c in components],
        }
        return {name: _rank_normalise(vals) for name, vals in metrics.items()}

    @staticmethod
    def _winsorize_components(components: List[StructuralMetrics], limit: float = 0.05) -> List[StructuralMetrics]:
        """
        Apply winsorization to raw structural metrics.
        Caps values above the (1 - limit) percentile to mitigate outlier influence.
        """
        if not components or limit <= 0:
            return components

        import copy

        def _get_cap(values: List[float], p: float) -> float:
            if not values: return 0.0
            s = sorted(values)
            n = len(s)
            k_idx = (n - 1) * p
            f = int(k_idx)
            c = min(f + 1, n - 1)
            return s[f] + (k_idx - f) * (s[c] - s[f])

        # We must copy metrics because we are going to modify them
        winsorized = [copy.copy(c) for c in components]
        
        metrics_to_winsorize = [
            "pagerank", "reverse_pagerank", "betweenness", "closeness",
            "eigenvector", "in_degree_raw", "out_degree_raw", "weight"
        ]

        for attr in metrics_to_winsorize:
            raw_vals = [getattr(c, attr) for c in components]
            cap = _get_cap(raw_vals, 1.0 - limit)
            
            for c in winsorized:
                val = getattr(c, attr)
                if val > cap:
                    setattr(c, attr, cap)
        
        return winsorized

    # ------------------------------------------------------------------
    # QoS-aware weight derivation (Fix 3)
    # ------------------------------------------------------------------

    @staticmethod
    def _derive_qos_weights(qos_profile: Dict, base_weights: QualityWeights) -> QualityWeights:
        """
        Adjust overall quality dimension weights based on the dominant
        QoS profile of the topic set. Rules:

        - PERSISTENT / RELIABLE / CRITICAL priority → high-durability,
          mission-critical system → raise q_reliability.
        - VOLATILE / BEST_EFFORT / LOW priority → high-churn, low-durability
          system → raise q_maintainability.
        - Mixed → keep balanced defaults.

        Both weights are re-normalised to sum to 1.0. r_alpha (the FT-vs-A
        split within Reliability) is a separate, stakeholder-declared lever
        (see saag.core.quality_model.derive_rm_weights) and is untouched here.
        """
        import copy
        w = copy.copy(base_weights)

        total = qos_profile.get("total_topics", 0)
        if total == 0:
            return w

        dur = qos_profile.get("durability", {})
        rel = qos_profile.get("reliability", {})
        pri = qos_profile.get("priority", {})

        # Fraction of high-durability topics
        persistent_frac = (
            dur.get("persistent", 0) + dur.get("transient_local", 0) + dur.get("transient", 0)
        ) / total

        # Fraction of reliable topics
        reliable_frac = rel.get("reliable", 0) / total

        # Fraction of critical/high priority topics
        critical_frac = (
            pri.get("critical", 0) + pri.get("high", 0)
        ) / total

        # Composite "reliability signal"
        rel_signal = (persistent_frac + reliable_frac + critical_frac) / 3.0

        # Adjust weights: rel_signal > 0.6 → reliability-critical system
        #                 rel_signal < 0.4 → volatile/best-effort system
        if rel_signal >= 0.6:
            # Mission-critical: emphasise reliability
            delta = min(0.15, (rel_signal - 0.5) * 0.30)
            q_r = w.q_reliability + delta
            q_m = w.q_maintainability - delta
        elif rel_signal <= 0.4:
            # Volatile/best-effort: emphasise maintainability
            delta = min(0.15, (0.5 - rel_signal) * 0.30)
            q_r = w.q_reliability - delta
            q_m = w.q_maintainability + delta
        else:
            # Balanced — keep defaults
            return w

        # Re-normalise to sum to 1.0 (clamp to small positive to avoid negatives)
        q_r = max(0.05, q_r)
        q_m = max(0.05, q_m)
        total_q = q_r + q_m

        w.q_reliability      = q_r / total_q
        w.q_maintainability  = q_m / total_q

        return w

    @staticmethod
    def _build_summary(components: List[ComponentQuality], edges: List[EdgeQuality]) -> ClassificationSummary:
        comp_dist = {level.value: 0 for level in CriticalityLevel}
        edge_dist = {level.value: 0 for level in CriticalityLevel}
        for c in components:
            comp_dist[c.levels.overall.value] += 1
        for e in edges:
            edge_dist[e.level.value] += 1
        return ClassificationSummary(
            total_components=len(components),
            total_edges=len(edges),
            component_distribution=comp_dist,
            edge_distribution=edge_dist,
        )
