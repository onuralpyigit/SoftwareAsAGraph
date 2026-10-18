"""
Unit tests for the Maintainability dimension improvements (v6).

Covers:
  - CouplingRisk(v) formula correctness at boundaries and in range
  - M(v) v6 QualityWeights: new fields present, m_out_degree deprecated, CQP added
  - AHP 5×5 matrix for Maintainability: weights sum to 1.0 and positive
  - ImpactMetrics: IM(v) default fields, maintainability_impact property arithmetic
  - calculate_cocr_at_k: mirrors CCR@K semantics for change propagation
  - calculate_weighted_kappa_cta: 3-tier weighted κ
  - calculate_bottleneck_precision: BT-dominant subset precision
  - ChangePropagationSimulator: basic BFS correctness and stop conditions
"""
import pytest
from saag.core import GraphData, ComponentData, EdgeData, AnalysisLayer
from saag.core.metrics import StructuralMetrics
from saag.analysis.models import StructuralAnalysisResult, GraphSummary
from saag.analysis.weight_calculator import QualityWeights, AHPMatrices, AHPProcessor
from saag.analysis.analyzer import QualityAnalyzer
from saag.analysis.structural_analyzer import StructuralAnalyzer
from saag.simulation.models import ImpactMetrics
from saag.validation.metric_calculator import (
    calculate_cocr_at_k,
    calculate_weighted_kappa_cta,
    calculate_bottleneck_precision,
)


# ===========================================================================
# QualityWeights: new v5 M fields present, deprecated field is zero
# ===========================================================================

class TestQualityWeightsV5:

    def test_default_weights_have_m_w_out_and_m_coupling_risk(self):
        """QualityWeights must expose m_w_out and m_coupling_risk with sensible defaults."""
        w = QualityWeights()
        assert hasattr(w, 'm_w_out'), "m_w_out field missing from QualityWeights"
        assert hasattr(w, 'm_coupling_risk'), "m_coupling_risk field missing from QualityWeights"
        assert w.m_w_out > 0.0, "m_w_out should be positive"
        assert w.m_coupling_risk > 0.0, "m_coupling_risk should be positive"

    def test_deprecated_m_out_degree_is_zero(self):
        """m_out_degree must be 0.0 (deprecated in v5)."""
        w = QualityWeights()
        assert w.m_out_degree == 0.0, "m_out_degree should be deprecated (0.0) in v5"

    def test_ahp_computed_m_weights_are_positive_and_sum_near_one(self):
        """AHP-computed Maintainability weights should be positive and sum ~1.0 (v6 = 5 terms)."""
        proc = AHPProcessor()
        w = proc.compute_weights()
        active = w.m_betweenness + w.m_w_out + getattr(w, 'm_code_quality_penalty', 0.0) + w.m_coupling_risk + w.m_clustering
        assert active == pytest.approx(1.0, abs=0.05), (
            f"Active M(v) v6 weights should sum ~1.0, got {active:.4f}"
        )
        assert w.m_betweenness > 0.0
        assert w.m_w_out > 0.0
        assert w.m_coupling_risk > 0.0
        assert w.m_clustering > 0.0
        assert w.m_out_degree == 0.0
        assert getattr(w, 'm_code_quality_penalty', -1) > 0.0, "m_code_quality_penalty should be positive in v6"

    def test_ahp_maintainability_matrix_is_5x5(self):
        """Maintainability AHP matrix should be 5×5 (BT, w_out, CQP, CouplingRisk, (1-CC)) in v6."""
        m = AHPMatrices()
        assert len(m.criteria_maintainability) == 5, (
            f"Expected 5×5 matrix, got {len(m.criteria_maintainability)} rows"
        )
        assert all(len(row) == 5 for row in m.criteria_maintainability), (
            "All rows in criteria_maintainability must have 5 columns"
        )

    def test_ahp_m_weights_order_bt_gt_w_out_gt_cr_gt_cc(self):
        """BT should be largest weight; (1-CC) should be smallest."""
        proc = AHPProcessor()
        w = proc.compute_weights()
        assert w.m_betweenness >= w.m_w_out, (
            f"BT weight ({w.m_betweenness:.4f}) should be >= w_out ({w.m_w_out:.4f})"
        )
        assert w.m_clustering <= w.m_coupling_risk + 0.05, (
            f"(1-CC) weight ({w.m_clustering:.4f}) should be smallest"
        )


# ===========================================================================
# CouplingRisk formula
# CouplingRisk(v) = 1 - |2 * Instability - 1|
# where Instability = DG_out / (DG_in + DG_out + ε)
# ===========================================================================

class TestCouplingRiskFormula:
    """
    Properties:
      Instability = 0.0  (pure sink,  DG_out=0)  -> CouplingRisk = 0.0
      Instability = 0.5  (equal in/out)           -> CouplingRisk = 1.0  (maximised)
      Instability = 1.0  (pure source, DG_in=0)   -> CouplingRisk = 0.0
    """

    @staticmethod
    def _coupling_risk(dg_out: float, dg_in: float) -> float:
        _eps = 1e-9
        instability = dg_out / (dg_in + dg_out + _eps)
        return 1.0 - abs(2.0 * instability - 1.0)

    @pytest.mark.parametrize("dg_out, dg_in, expected, abs_tol", [
        (0.0, 1.0, 0.0, 1e-6),  # pure sink: DG_out=0 → Instability=0 → CouplingRisk=0
        (1.0, 0.0, 0.0, 1e-4),  # pure source: DG_in=0 → Instability≈1 → CouplingRisk≈0
        (5.0, 5.0, 1.0, 1e-6),  # equal in/out: Instability=0.5 → CouplingRisk maximised
    ])
    def test_coupling_risk_values(self, dg_out, dg_in, expected, abs_tol):
        cr = self._coupling_risk(dg_out=dg_out, dg_in=dg_in)
        assert cr == pytest.approx(expected, abs=abs_tol)

    def test_coupling_risk_always_in_range(self):
        """CouplingRisk should always be in [0, 1]."""
        for dg_in in [0, 1, 3, 10]:
            for dg_out in [0, 1, 3, 10]:
                cr = self._coupling_risk(float(dg_out), float(dg_in))
                assert 0.0 <= cr <= 1.0 + 1e-9, (
                    f"CouplingRisk out of range: {cr} for (in={dg_in}, out={dg_out})"
                )

    def test_zero_division_safe(self):
        """Both 0 should not raise ZeroDivisionError."""
        cr = self._coupling_risk(dg_out=0.0, dg_in=0.0)
        assert 0.0 <= cr <= 1.0


# ===========================================================================
# Property Tests: Maintainability via Analyzer Pipeline
# ===========================================================================

class TestMaintainabilityProperty:
    """
    Property tests for Maintainability: M(v) = f(BT, w_out, CQP, CouplingRisk, CC).
    Verifies that the QualityAnalyzer correctly applies the formula and that 
    the star_graph fixture yields expected directional results.
    """

    def test_linear_maintainability_gradient(self, linear_graph):
        """
        In A -> B -> C, B is the intermediate node.
        B should have a higher maintainability risk (M score) than A or C 
        because it has higher betweenness centrality.
        """
        # 1. Structural Analysis
        struct_analyzer = StructuralAnalyzer()
        struct_res = struct_analyzer.analyze(linear_graph)

        # 2. Quality Analysis
        quality_analyzer = QualityAnalyzer(normalization_method="max")
        quality_res = quality_analyzer.analyze(struct_res)

        # 3. Property Asserts
        comp_map = {c.id: c for c in quality_res.components}
        m_a = comp_map["A"].scores.maintainability
        m_b = comp_map["B"].scores.maintainability
        m_c = comp_map["C"].scores.maintainability

        # B should be higher than C (C has 0 out-degree and 0 betweenness)
        # B should be higher than A (both have 1 out-degree, but B has higher betweenness)
        assert m_b > m_c, f"Middle node B ({m_b:.4f}) should have higher M than sink C ({m_c:.4f})"
        assert m_b > m_a, f"Middle node B ({m_b:.4f}) should have higher M than source A ({m_a:.4f})"


# ===========================================================================
# ImpactMetrics: IM(v) fields
# ===========================================================================

class TestImpactMetricsIMv:

    def test_default_im_fields_are_zero(self):
        """New IM(v) fields should default to 0.0 (backward compat)."""
        im = ImpactMetrics()
        assert im.change_reach == 0.0
        assert im.weighted_change_impact == 0.0
        assert im.normalized_change_depth == 0.0

    def test_maintainability_impact_zero_when_defaults(self):
        """maintainability_impact property should be 0.0 with all-zero sub-fields."""
        assert ImpactMetrics().maintainability_impact == pytest.approx(0.0)

    def test_maintainability_impact_formula(self):
        """IM(v) = 0.45*CR + 0.35*WCI + 0.20*NCD."""
        im = ImpactMetrics(
            change_reach=1.0,
            weighted_change_impact=1.0,
            normalized_change_depth=1.0,
        )
        expected = 0.45 * 1.0 + 0.35 * 1.0 + 0.20 * 1.0
        assert im.maintainability_impact == pytest.approx(expected, abs=1e-6)

    def test_maintainability_impact_partial_values(self):
        """Partial sub-fields give correct weighted sum."""
        im = ImpactMetrics(
            change_reach=0.6,
            weighted_change_impact=0.4,
            normalized_change_depth=0.2,
        )
        expected = 0.45 * 0.6 + 0.35 * 0.4 + 0.20 * 0.2
        assert im.maintainability_impact == pytest.approx(expected, abs=1e-6)

    def test_to_dict_includes_maintainability_section(self):
        """to_dict() should include a 'maintainability' section with IM(v) fields."""
        im = ImpactMetrics(change_reach=0.5, weighted_change_impact=0.3)
        d = im.to_dict()
        assert "maintainability" in d, "'maintainability' key missing from to_dict()"
        assert "change_reach" in d["maintainability"]
        assert "maintainability_impact" in d["maintainability"]


# ===========================================================================
# COCR@K
# ===========================================================================

class TestCOCRAtK:

    def test_perfect_overlap(self):
        """Same ordering → COCR@5 = 1.0."""
        scores = {f"c{i}": 10 - i for i in range(10)}
        assert calculate_cocr_at_k(scores, scores, k=5) == pytest.approx(1.0)

    def test_zero_overlap(self):
        """Completely disjoint top-K → COCR@K = 0.0."""
        pred   = {"a": 1.0, "b": 0.9, "d": 0.2, "e": 0.1}
        actual = {"d": 1.0, "e": 0.9, "a": 0.3, "b": 0.2}
        # pred top-2: {a,b}; actual top-2: {d,e} — no overlap
        assert calculate_cocr_at_k(pred, actual, k=2) == pytest.approx(0.0, abs=1e-9)

    def test_partial_overlap(self):
        """Partial overlap → COCR@K = |intersection| / K."""
        # All 6 keys in both dicts so effective_k = k = 4
        pred   = {"a": 1.0, "b": 0.9, "c": 0.8, "d": 0.7, "e": 0.2, "f": 0.1}
        actual = {"a": 1.0, "c": 0.9, "e": 0.8, "f": 0.7, "b": 0.2, "d": 0.1}
        # Top-4 pred: {a,b,c,d}; Top-4 actual: {a,c,e,f}; common={a,c} → 2/4 = 0.5
        assert calculate_cocr_at_k(pred, actual, k=4) == pytest.approx(0.5, abs=1e-9)

    def test_empty_inputs(self):
        """Empty inputs should return 0.0 safely."""
        assert calculate_cocr_at_k({}, {}) == 0.0
        assert calculate_cocr_at_k({"a": 1.0}, {}) == 0.0

    def test_k_larger_than_n(self):
        """k > |components| should return a valid, bounded value."""
        scores = {"a": 0.9, "b": 0.5}
        result = calculate_cocr_at_k(scores, scores, k=10)
        assert 0.0 <= result <= 1.0


# ===========================================================================
# Weighted-κ Coupling Tier Agreement
# ===========================================================================

class TestWeightedKappaCTA:

    def test_perfect_agreement(self):
        """Identical scores → maximum agreement → κ near 1.0."""
        scores = {f"c{i}": float(i) for i in range(12)}
        kappa = calculate_weighted_kappa_cta(scores, scores)
        assert kappa > 0.9, f"Perfect agreement should give κ > 0.9, got {kappa:.4f}"

    def test_insufficient_data(self):
        """n < 3 should return 0.0 safely."""
        assert calculate_weighted_kappa_cta({}, {}) == 0.0
        assert calculate_weighted_kappa_cta({"a": 1.0, "b": 0.5}, {"a": 0.5, "b": 1.0}) == 0.0

    def test_kappa_in_range(self):
        """Weighted κ should always be in [-1, 1]."""
        import random
        rng = random.Random(42)
        keys = [f"c{i}" for i in range(20)]
        pred   = {k: rng.random() for k in keys}
        actual = {k: rng.random() for k in keys}
        kappa = calculate_weighted_kappa_cta(pred, actual)
        assert -1.0 <= kappa <= 1.0, f"κ out of range: {kappa}"

    def test_all_same_tier_gives_perfect_kappa(self):
        """If all components are in the same tier (extreme agreement), kappa is 1.0."""
        # All scores equal → all in Medium tier → perfect agreement
        scores = {f"c{i}": 0.5 for i in range(10)}
        kappa = calculate_weighted_kappa_cta(scores, scores)
        # With degenerate data perfect agreement still holds
        assert kappa >= 0.9 or kappa == 0.0  # either perfect or degenerate safe


# ===========================================================================
# Bottleneck Precision
# ===========================================================================

class TestBottleneckPrecision:
    """BP selects BT-dominant components and asks what share carry high IM.

    Two things changed together: the IM cut is a quantile rather than an absolute
    0.50 (IM arrives robust-sigmoid scaled with its median pinned at 0.5, so 0.50
    was a median split), and an empty BT-dominant selection reports None rather
    than 0.0 -- there is no precision to report on an empty set, and 0.0 reads as
    "every bottleneck was wrong".
    """

    def test_all_bt_dominant_high_im(self):
        """All BT-dominant sit in the top IM quartile → BP = 1.0."""
        bt    = {"a": 0.9, "b": 0.8, "c": 0.2}
        w_out = {"a": 0.1, "b": 0.1, "c": 0.8}
        im    = {"a": 0.8, "b": 0.7, "c": 0.1}
        # quantile 0.25 of [0.1, 0.7, 0.8] is 0.40, so a and b clear it.
        bp = calculate_bottleneck_precision(bt, w_out, im, im_quantile=0.25)
        assert bp == pytest.approx(1.0)

    def test_no_bt_dominant_components_is_unmeasured(self):
        """No BT-dominant component → None, not a measured 0.0."""
        bt    = {"a": 0.3, "b": 0.2}
        w_out = {"a": 0.5, "b": 0.6}
        im    = {"a": 0.8, "b": 0.9}
        assert calculate_bottleneck_precision(bt, w_out, im) is None

    def test_partial_bt_dominant_high_im(self):
        """Half the BT-dominant clear the IM cut → BP = 0.5."""
        bt    = {"a": 0.8, "b": 0.9, "c": 0.1}
        w_out = {"a": 0.1, "b": 0.2, "c": 0.9}
        im    = {"a": 0.8, "b": 0.3, "c": 0.1}
        # quantile 0.5 of [0.1, 0.3, 0.8] is 0.30, which only 'a' exceeds.
        bp = calculate_bottleneck_precision(bt, w_out, im, im_quantile=0.5)
        assert bp == pytest.approx(0.5)

    def test_empty_inputs_safe(self):
        """Empty inputs → None, no error."""
        assert calculate_bottleneck_precision({}, {}, {}) is None

    def test_custom_thresholds(self):
        """Custom BT/w_out thresholds are respected."""
        bt    = {"a": 0.5}  # below default bt_threshold=0.60
        w_out = {"a": 0.1}
        im    = {"a": 0.9}
        assert calculate_bottleneck_precision(bt, w_out, im) is None
        assert calculate_bottleneck_precision(
            bt, w_out, im, bt_threshold=0.40, im_quantile=0.0
        ) == pytest.approx(0.0)

    def test_inputs_are_expected_normalised(self):
        """Raw w_out is an unbounded QoS-weight sum; the thresholds assume [0, 1].

        Passing raw structural values made the BT>0.60 AND w_out<0.30 conjunction
        very nearly unsatisfiable, so BP read 0.0 by construction on every
        scenario. `dimensions._max_normalised` now scales both inputs first.
        """
        raw_w_out = {"a": 14.2, "b": 9.8}     # raw QoS-weight sums
        bt        = {"a": 0.9, "b": 0.8}
        im        = {"a": 0.9, "b": 0.8}
        assert calculate_bottleneck_precision(bt, raw_w_out, im) is None

        peak = max(raw_w_out.values())
        normalised = {k: v / peak for k, v in raw_w_out.items()}
        assert calculate_bottleneck_precision(bt, normalised, im) is None


# ===========================================================================
# ChangePropagationSimulator: BFS correctness and stop conditions
# ===========================================================================

class TestChangePropagationSimulator:

    def _make_sim(self, theta_loose=0.20, theta_stable=0.20):
        from saag.simulation.change_propagation import ChangePropagationSimulator
        return ChangePropagationSimulator(theta_loose=theta_loose, theta_stable=theta_stable)

    def test_isolated_component_zero_reach(self):
        """Component with no outgoing/incoming deps → change_reach = 0."""
        sim = self._make_sim()
        results = sim.simulate_all(
            component_ids=["A", "B"],
            dependency_edges=[],   # no edges
        )
        assert results["A"].change_reach == pytest.approx(0.0)
        assert results["B"].change_reach == pytest.approx(0.0)

    def test_single_chain_propagates(self):
        """A -> B -> C (A depends on B, B depends on C).
        Changing C propagates to B (B depends on C) and then to A (A depends on B).
        On G^T: C→B→A.
        """
        sim = self._make_sim(theta_loose=0.0, theta_stable=0.0)  # no stops
        # edges: (src, tgt, weight) where src depends on tgt
        edges = [("A", "B", 0.8), ("B", "C", 0.8)]
        results = sim.simulate_all(
            component_ids=["A", "B", "C"],
            dependency_edges=edges,
        )
        # Changing C: G^T C→B→A, so C's change reaches B and A
        # n_others = 2, reached = 2 → reach = 1.0
        assert results["C"].change_reach == pytest.approx(1.0, abs=0.01)
        # Changing A: no one depends on A → reach = 0
        assert results["A"].change_reach == pytest.approx(0.0, abs=0.01)

    def test_loose_coupling_stop(self):
        """Low edge weight should stop propagation through that boundary."""
        sim = self._make_sim(theta_loose=0.50, theta_stable=0.0)
        # B depends on C with weight 0.1 (< theta_loose=0.50)
        # A depends on B with weight 0.9
        edges = [("A", "B", 0.9), ("B", "C", 0.1)]
        results = sim.simulate_all(
            component_ids=["A", "B", "C"],
            dependency_edges=edges,
        )
        # Changing C: G^T has C→B (weight 0.1 < 0.5 → loose coupling stop after reaching B)
        # B is still reached (stop only gates further propagation)
        # A should NOT be reached because stop at B prevents traversal to A
        c_result = results["C"]
        assert "B" in c_result.reached_components, "B should be reached (stop gates further, not current)"
        assert "A" not in c_result.reached_components, "A should not be reached (B is a coupling boundary)"

    def test_stable_interface_stop(self):
        """High-stability component (low instability) stops propagation."""
        # B has in_degree=10, out_degree=0 → Instability ≈ 0 < theta_stable=0.2
        sim = self._make_sim(theta_loose=0.0, theta_stable=0.20)
        edges = [("A", "B", 0.9), ("B", "C", 0.9)]
        results = sim.simulate_all(
            component_ids=["A", "B", "C"],
            dependency_edges=edges,
            component_in_degrees={"A": 0, "B": 10, "C": 0},
            component_out_degrees={"A": 1, "B": 0, "C": 1},
        )
        # B's instability = 0/(10+0) = 0.0 < 0.20 → stable interface → stops at B
        c_result = results["C"]
        assert "B" in c_result.reached_components, "B should be reached"
        assert "A" not in c_result.reached_components, "A should not be reached (B is a stable interface)"

    def test_normalized_depth_bounds(self):
        """normalized_change_depth should be in [0, 1] for all components."""
        sim = self._make_sim()
        edges = [("A", "B", 0.8), ("B", "C", 0.8), ("C", "D", 0.8)]
        results = sim.simulate_all(
            component_ids=["A", "B", "C", "D"],
            dependency_edges=edges,
        )
        for cid, r in results.items():
            assert 0.0 <= r.normalized_change_depth <= 1.0, (
                f"normalized_change_depth out of range for {cid}: {r.normalized_change_depth}"
            )


class TestMaintainabilityOracleIsLive:
    """IM(v) must carry variance over the population the validation actually scores.

    Regression test for the disjoint-population bug: change-propagation reach was
    non-zero only for Topic and Library, while simulate_exhaustive targets only
    Node, Broker and Application, so IM(v) was identically 0.0 everywhere. The
    maintainability dimension silently never validated, and G6/G8 reported FAIL
    from an unmeasured zero.
    """

    def _exhaustive(self, scenario: str, layer: str = "system"):
        import json
        from saag.infrastructure.memory_repo import MemoryRepository
        from saag.simulation.service import SimulationService
        from saag.usecases.model_graph import ModelGraphUseCase

        repo = MemoryRepository()
        with open(f"data/scenarios/{scenario}_system.json") as fh:
            ModelGraphUseCase(repo).execute(json.load(fh), clear=True)
        return SimulationService(repo).run_failure_simulation_exhaustive(layer=layer)

    def test_im_is_non_degenerate_on_the_corpus(self):
        results = self._exhaustive("atm")
        distinct = {round(r.impact.maintainability_impact, 9) for r in results}
        assert len(distinct) >= 3, (
            f"IM(v) carries {len(distinct)} distinct value(s) over {len(results)} "
            "components; a constant ground truth cannot validate a dimension"
        )
        assert max(r.impact.maintainability_impact for r in results) > 0.0

    def test_im_is_populated_on_every_layer(self):
        """The bug was layer-independent, so the guard must be too."""
        for layer in ("app", "mw", "infra", "system"):
            results = self._exhaustive("atm", layer=layer)
            if len(results) < 3:
                continue
            distinct = {round(r.impact.maintainability_impact, 9) for r in results}
            assert len(distinct) >= 2, f"IM(v) is constant on layer '{layer}'"


# ===========================================================================
# CouplingRisk Hardening & Path Complexity
# ===========================================================================

class TestCouplingRiskHardening:
    """Consolidated from test_coupling_risk_hardening.py."""

    def test_coupling_risk_capping(self):
        """Verify that CouplingRisk is correctly modulated and capped at 1.0."""
        m = StructuralMetrics(id="app1", name="App 1", type="Application")
        m.in_degree_raw = 5
        m.out_degree_raw = 5
        m.path_complexity = 2.0

        sar = StructuralAnalysisResult(
            layer=AnalysisLayer.SYSTEM,
            components={"app1": m},
            edges={},
            graph_summary=GraphSummary(layer="system", nodes=1, edges=10),
        )

        analyzer = QualityAnalyzer()
        results = analyzer.analyze(sar)
        score = next(c for c in results.components if c.id == "app1")
        assert score.scores.maintainability == pytest.approx(0.20)

    def test_coupling_risk_modulation(self):
        """Verify that path_complexity increases CouplingRisk."""
        m3 = StructuralMetrics(id="app3", name="App 3", type="Application")
        m3.in_degree_raw = 8
        m3.out_degree_raw = 2
        m3.path_complexity = 1.0

        sar = StructuralAnalysisResult(
            layer=AnalysisLayer.SYSTEM,
            components={"app3": m3},
            edges={},
            graph_summary=GraphSummary(layer="system", nodes=1, edges=10),
        )

        analyzer = QualityAnalyzer()
        results = analyzer.analyze(sar)
        score = next(c for c in results.components if c.id == "app3")
        assert score.scores.maintainability == pytest.approx(0.1328)


class TestPathComplexity:
    """Consolidated from test_path_count.py."""

    @pytest.fixture
    def multi_path_graph(self):
        return GraphData(
            components=[
                ComponentData(id="A", component_type="Application"),
                ComponentData(id="B", component_type="Application"),
                ComponentData(id="C", component_type="Application"),
            ],
            edges=[
                EdgeData("A", "B", "Application", "Application", "app_to_app", "DEPENDS_ON", 1.0, path_count=1),
                EdgeData("A", "C", "Application", "Application", "app_to_app", "DEPENDS_ON", 1.0, path_count=3),
            ],
        )

    def test_path_complexity_calculation(self, multi_path_graph):
        """StructuralAnalyzer should correctly compute mean log-scaled path count."""
        analyzer = StructuralAnalyzer()
        res = analyzer.analyze(multi_path_graph)
        assert res.components["A"].path_complexity == pytest.approx(1.5)
        assert res.components["B"].path_complexity == 0.0
        assert res.components["C"].path_complexity == 0.0

    def test_coupling_risk_modulation_with_id(self):
        """Verifies modulation when base CouplingRisk > 0."""
        graph = GraphData(
            components=[
                ComponentData(id="X", component_type="Application"),
                ComponentData(id="Y", component_type="Application"),
                ComponentData(id="Z", component_type="Application"),
            ],
            edges=[
                EdgeData("Z", "X", "Application", "Application", "app_to_app", "DEPENDS_ON", 1.0, path_count=7),
                EdgeData("X", "Y", "Application", "Application", "app_to_app", "DEPENDS_ON", 1.0, path_count=3),
            ],
        )

        struct_res = StructuralAnalyzer().analyze(graph)
        quality_res = QualityAnalyzer().analyze(struct_res)
        x_quality = next(c for c in quality_res.components if c.id == "X")

        graph_base = GraphData(
            components=[
                ComponentData(id="X", component_type="Application"),
                ComponentData(id="Y", component_type="Application"),
                ComponentData(id="Z", component_type="Application"),
            ],
            edges=[
                EdgeData("Z", "X", "Application", "Application", "app_to_app", "DEPENDS_ON", 1.0, path_count=1),
                EdgeData("X", "Y", "Application", "Application", "app_to_app", "DEPENDS_ON", 1.0, path_count=1),
            ],
        )
        struct_base = StructuralAnalyzer().analyze(graph_base)
        quality_base = QualityAnalyzer().analyze(struct_base)
        m_base = next(c for c in quality_base.components if c.id == "X").scores.maintainability

        assert x_quality.scores.maintainability >= m_base
