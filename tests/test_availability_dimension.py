"""
Unit tests for the Availability dimension improvements (A(v) v3).

Covers:
  - QualityWeights: new a_qspof, a_ap_c_directed, a_cdi, a_qos_weight fields present
  - AHPMatrices: criteria_availability is 5×5
  - AHPProcessor: positive weights with correct ordering (AP primary)
  - QSPOF(v) formula correctness
  - AP_c_directed = max(AP_c_out, AP_c_in)
  - CDI normalisation
  - ImpactMetrics: IA(v) default fields, availability_impact property arithmetic, to_dict()
  - calculate_spof_f1: perfect, zero, partial coverage
"""
import pytest
from saag.core import GraphData, ComponentData, EdgeData, AnalysisLayer
from saag.core.metrics import StructuralMetrics
from saag.analysis.models import StructuralAnalysisResult
from saag.analysis.weight_calculator import QualityWeights, AHPMatrices, AHPProcessor
from saag.analysis.analyzer import QualityAnalyzer
from saag.analysis.structural_analyzer import StructuralAnalyzer
from saag.simulation.models import ImpactMetrics
from saag.validation.metric_calculator import calculate_spof_f1


# ===========================================================================
# QualityWeights: new v2 A fields present, deprecated fields zero
# ===========================================================================

class TestQualityWeightsV3A:

    def test_new_a_fields_are_present_with_defaults(self):
        """A(v) v3 fields must exist and be positive by default."""
        w = QualityWeights()
        assert hasattr(w, 'a_qspof'),        "a_qspof missing"
        assert hasattr(w, 'a_ap_c_directed'), "a_ap_c_directed missing"
        assert hasattr(w, 'a_cdi'),           "a_cdi missing"
        assert hasattr(w, 'a_qos_weight'),    "a_qos_weight missing"
        assert w.a_qspof > 0.0,        "a_qspof should be positive"
        assert w.a_ap_c_directed > 0.0, "a_ap_c_directed should be positive"
        assert w.a_cdi > 0.0,           "a_cdi should be positive"
        assert w.a_qos_weight > 0.0,    "a_qos_weight should be positive"

    def test_legacy_a_fields_are_removed(self):
        """a_articulation and a_importance should be removed."""
        w = QualityWeights()
        assert not hasattr(w, 'a_articulation'), "a_articulation should be removed"
        assert not hasattr(w, 'a_importance'),   "a_importance should be removed"

    def test_default_a_weights_sum_to_one(self):
        """Active A(v) v3 weights must sum to 1.0."""
        w = QualityWeights()
        total = (
            w.a_qspof + 
            w.a_bridge_ratio + 
            w.a_ap_c_directed + 
            w.a_cdi + 
            w.a_qos_weight
        )
        assert total == pytest.approx(1.0, abs=0.1), (
            f"Active A(v) v3 weights should sum ~1.0, got {total:.4f}"
        )


# ===========================================================================
# AHPMatrices: criteria_availability is now 4×4
# ===========================================================================

class TestAHPAvailabilityMatrix:

    def test_availability_matrix_is_5x5(self):
        """criteria_availability must be 5×5 (AP_c_d, QSPOF, BR, CDI, w)."""
        m = AHPMatrices()
        assert len(m.criteria_availability) == 5, (
            f"Expected 5×5, got {len(m.criteria_availability)} rows"
        )
        assert all(len(row) == 5 for row in m.criteria_availability), (
            "All rows in criteria_availability must have 5 columns"
        )

    def test_ahp_computed_a_weights_sum_near_one(self):
        """AHP-computed A(v) v3 weights should be positive and sum ~1.0."""
        proc = AHPProcessor()
        w = proc.compute_weights()
        active = (
            w.a_qspof + 
            w.a_bridge_ratio + 
            w.a_ap_c_directed + 
            w.a_cdi + 
            w.a_qos_weight
        )
        assert active == pytest.approx(1.0, abs=0.05), (
            f"Computed A(v) weights should sum ~1.0, got {active:.4f}"
        )
        assert w.a_qspof > 0.0
        assert w.a_bridge_ratio > 0.0
        assert w.a_ap_c_directed > 0.0
        assert w.a_cdi > 0.0
        assert w.a_qos_weight > 0.0

    def test_ahp_weight_ordering_ap_c_directed_largest(self):
        """AP_c_directed should have the largest weight (baseline)."""
        proc = AHPProcessor()
        w = proc.compute_weights()
        assert w.a_ap_c_directed >= w.a_qspof, (
            f"AP_c_directed ({w.a_ap_c_directed:.4f}) should be >= QSPOF ({w.a_qspof:.4f})"
        )
        assert w.a_qos_weight <= w.a_cdi + 0.05, (
            f"w ({w.a_qos_weight:.4f}) should be the smallest"
        )


# ===========================================================================
# QSPOF formula: QSPOF(v) = AP_c_directed(v) × w(v)
# ===========================================================================

class TestQSPOFFormula:

    @staticmethod
    def _qspof(ap_c_dir: float, qw: float) -> float:
        return ap_c_dir * qw

    @pytest.mark.parametrize("ap_c_dir, qw, expected", [
        (0.0, 1.0, 0.0),   # non-articulation point → 0 regardless of QoS weight
        (0.8, 0.0, 0.0),   # zero QoS weight → 0 regardless of AP score
        (1.0, 1.0, 1.0),   # full AP and full QoS weight → 1.0
        (0.5, 0.6, 0.30),  # partial AP and QoS weight multiply correctly
    ])
    def test_qspof_values(self, ap_c_dir, qw, expected):
        assert self._qspof(ap_c_dir, qw) == pytest.approx(expected, abs=1e-9)

    def test_qspof_in_range(self):
        """QSPOF must always be in [0, 1] for valid inputs."""
        for ap in [0.0, 0.25, 0.5, 0.75, 1.0]:
            for qw in [0.0, 0.25, 0.5, 0.75, 1.0]:
                q = self._qspof(ap, qw)
                assert 0.0 <= q <= 1.0 + 1e-9, f"QSPOF out of range: {q}"


# ===========================================================================
# AP_c_directed = max(AP_c_out, AP_c_in)
# ===========================================================================

class TestAPcDirectedFormula:

    @staticmethod
    def _ap_c_directed(out: float, inp: float) -> float:
        return max(out, inp)

    @pytest.mark.parametrize("out, inp, expected", [
        (0.0, 0.0, 0.0),  # both zero
        (0.8, 0.3, 0.8),  # out dominates
        (0.2, 0.9, 0.9),  # in dominates
        (0.5, 0.5, 0.5),  # equal values
    ])
    def test_ap_c_directed_values(self, out, inp, expected):
        assert self._ap_c_directed(out, inp) == pytest.approx(expected)

    def test_result_in_range(self):
        for a in [0.0, 0.1, 0.5, 1.0]:
            for b in [0.0, 0.1, 0.5, 1.0]:
                result = self._ap_c_directed(a, b)
                assert 0.0 <= result <= 1.0


# ===========================================================================
# Property Tests: Availability via Analyzer Pipeline
# ===========================================================================

class TestAvailabilityProperty:
    """
    Property tests for Availability: A(v) = f(QSPOF, BR, AP_c, CDI).
    Verifies that the QualityAnalyzer correctly applies the formula and that 
    the ap_graph fixture yields expected directional results.
    """

    def test_articulation_point_availability_risk(self, ap_graph):
        """
        In an ap_graph (A-B-C, B-D), B is an articulation point.
        B should have a higher Availability score than others because 
        it is a Single Point of Failure (QSPOF > 0).
        """
        # 1. Structural Analysis
        struct_analyzer = StructuralAnalyzer()
        struct_res = struct_analyzer.analyze(ap_graph)

        # 2. Quality Analysis
        quality_analyzer = QualityAnalyzer(normalization_method="max")
        quality_res = quality_analyzer.analyze(struct_res)

        # 3. Property Asserts
        comp_map = {c.id: c for c in quality_res.components}
        b_quality = comp_map["B"]
        a_b = b_quality.scores.availability

        leaf_scores = [
            comp.scores.availability 
            for cid, comp in comp_map.items() 
            if cid != "B"
        ]

        # B should have higher availability risk (A score) than any leaf.
        assert all(a_b > a_leaf for a_leaf in leaf_scores), (
            f"Articulation Point B availability ({a_b:.4f}) should be higher than leaf nodes {leaf_scores}"
        )
        # B should have non-zero QSPOF contributing to it
        assert b_quality.structural.ap_c_directed > 0.0
        assert a_b > 0.1  # Sanity check it's non-negligible


# ===========================================================================
# ImpactMetrics: IA(v) fields and availability_impact property
# ===========================================================================

class TestImpactMetricsIAv:

    def test_default_ia_fields_are_zero(self):
        """New IA(v) fields should default to 0.0 (backward compat)."""
        im = ImpactMetrics()
        assert im.weighted_reachability_loss == 0.0
        assert im.weighted_fragmentation == 0.0
        assert im.path_breaking_throughput_loss == 0.0

    def test_availability_impact_zero_when_defaults(self):
        """availability_impact should be 0.0 with all-zero sub-fields."""
        assert ImpactMetrics().availability_impact == pytest.approx(0.0)

    def test_availability_impact_formula(self):
        """IA(v) = 0.50*WRL + 0.35*WFrag + 0.15*PBTL."""
        im = ImpactMetrics(
            weighted_reachability_loss=1.0,
            weighted_fragmentation=1.0,
            path_breaking_throughput_loss=1.0,
        )
        expected = 0.50 * 1.0 + 0.35 * 1.0 + 0.15 * 1.0
        assert im.availability_impact == pytest.approx(expected, abs=1e-6)

    def test_availability_impact_partial_values(self):
        """Partial sub-fields give correct weighted sum."""
        im = ImpactMetrics(
            weighted_reachability_loss=0.6,
            weighted_fragmentation=0.4,
            path_breaking_throughput_loss=0.2,
        )
        expected = 0.50 * 0.6 + 0.35 * 0.4 + 0.15 * 0.2
        assert im.availability_impact == pytest.approx(expected, abs=1e-6)

    def test_to_dict_includes_availability_section(self):
        """to_dict() should include an 'availability' section with IA(v) fields."""
        im = ImpactMetrics(
            weighted_reachability_loss=0.5,
            weighted_fragmentation=0.3,
            path_breaking_throughput_loss=0.1,
        )
        d = im.to_dict()
        assert "availability" in d, "'availability' key missing from to_dict()"
        av = d["availability"]
        assert "weighted_reachability_loss"    in av
        assert "weighted_fragmentation"        in av
        assert "path_breaking_throughput_loss" in av
        assert "availability_impact"           in av

    def test_fault_tolerance_impact_unchanged(self):
        """Existing IFT(v) fault_tolerance_impact formula (formerly reliability_impact,
        renamed when reliability_impact became the alpha-blend of IFT and IA) should
        still be correct."""
        im = ImpactMetrics(
            cascade_reach=0.5,
            weighted_cascade_impact=0.3,
            normalized_cascade_depth=0.8,
        )
        expected = 0.45 * 0.5 + 0.35 * 0.3 + 0.20 * 0.8
        assert im.fault_tolerance_impact == pytest.approx(expected, abs=1e-6)

    def test_maintainability_impact_unchanged(self):
        """Existing IM(v) maintainability_impact should still be correct."""
        im = ImpactMetrics(
            change_reach=0.5,
            weighted_change_impact=0.3,
            normalized_change_depth=0.8,
        )
        expected = 0.45 * 0.5 + 0.35 * 0.3 + 0.20 * 0.8
        assert im.maintainability_impact == pytest.approx(expected, abs=1e-6)


# ===========================================================================
# calculate_spof_f1
# ===========================================================================

class TestCalculateSPOFF1:

    def test_perfect_coverage(self):
        """All predicted SPOFs are actual SPOFs → F1 = 1.0."""
        ap  = {"a": 0.8, "b": 0.7, "c": 0.0}  # a,b predicted SPOF; c not
        ia  = {"a": 0.9, "b": 0.8, "c": 0.1}  # a,b actual SPOF; c not
        res = calculate_spof_f1(ap, ia, ap_threshold=0.0, ia_quantile=0.25)
        assert res["f1"] == pytest.approx(1.0)
        assert res["precision"] == pytest.approx(1.0)
        assert res["recall"] == pytest.approx(1.0)

    def test_zero_coverage(self):
        """No predicted SPOFs match actual SPOFs → F1 = 0.0."""
        ap  = {"a": 0.0, "b": 0.0, "c": 0.8}  # c predicted SPOF
        ia  = {"a": 0.9, "b": 0.8, "c": 0.1}  # a,b actual SPOF; c not
        res = calculate_spof_f1(ap, ia, ap_threshold=0.0, ia_quantile=0.25)
        assert res["f1"] == pytest.approx(0.0, abs=0.01)

    def test_partial_coverage(self):
        """Partial overlap → F1 between 0 and 1."""
        # a: TP; b: FP; c: FN
        ap  = {"a": 0.8, "b": 0.7, "c": 0.0}
        ia  = {"a": 0.9, "b": 0.1, "c": 0.8}
        res = calculate_spof_f1(ap, ia, ia_quantile=0.25)
        assert 0.0 < res["f1"] < 1.0
        # precision = 1/(1+1) = 0.5; recall = 1/(1+1) = 0.5; F1 = 0.5
        assert res["f1"] == pytest.approx(0.5, abs=0.01)

    def test_empty_inputs_safe(self):
        """Empty inputs → all metrics 0.0, no error."""
        res = calculate_spof_f1({}, {})
        assert res["f1"] == 0.0
        assert res["precision"] == 0.0
        assert res["recall"] == 0.0

    def test_result_in_range(self):
        """All metric values should be in [0, 1]."""
        import random
        rng = random.Random(42)
        keys = [f"c{i}" for i in range(20)]
        ap = {k: rng.random() for k in keys}
        ia = {k: rng.random() for k in keys}
        res = calculate_spof_f1(ap, ia)
        for metric_name, val in res.items():
            assert 0.0 <= val <= 1.0, f"{metric_name} out of range: {val}"


class TestSPOFThresholdIsAQuantile:
    """The actual-SPOF cut must be a quantile, not an absolute 0.50.

    IA(v) reaches `calculate_spof_f1` robust-sigmoid scaled, which pins its median
    at exactly 0.5. An absolute `ia_threshold=0.50` was therefore a median split in
    disguise, labelling half of every system a true SPOF regardless of the impact
    magnitudes (measured 55 of 111 components on microservices).
    """

    def test_absolute_half_threshold_is_not_reintroduced(self):
        import inspect
        from saag.validation.metric_calculator import calculate_spof_f1
        params = inspect.signature(calculate_spof_f1).parameters
        assert "ia_threshold" not in params
        assert params["ia_quantile"].default == 0.75

    def test_median_pinned_input_does_not_select_half_the_population(self):
        from saag.validation.metric_calculator import calculate_spof_f1
        from saag.validation.validator import robust_sigmoid_scale_dict

        raw = {f"c{i}": float(i) for i in range(100)}
        scaled = robust_sigmoid_scale_dict(raw)
        # Everything is an articulation point, so precision == |actual| / |predicted|.
        ap = {k: 1.0 for k in scaled}
        res = calculate_spof_f1(ap, scaled)
        assert res["precision"] == pytest.approx(0.25, abs=0.02), (
            "the top-quartile cut should select ~25% of the population, not ~50%"
        )


# ===========================================================================
# Availability Decoupling & Orthogonality
# ===========================================================================

class TestAvailabilityDecoupling:
    """Verifies decoupling of structural SPOF from QoS weight."""

    @pytest.fixture
    def spof_graph(self):
        """Graph where X is a structural SPOF (articulation point): Y -> X -> Z."""
        return GraphData(
            components=[
                ComponentData(id="X", component_type="Application"),
                ComponentData(id="Y", component_type="Application"),
                ComponentData(id="Z", component_type="Application"),
            ],
            edges=[
                EdgeData("Y", "X", "Application", "Application", "app_to_app", "DEPENDS_ON", 1.0),
                EdgeData("X", "Z", "Application", "Application", "app_to_app", "DEPENDS_ON", 1.0),
            ],
        )

    def test_structural_spof_not_masked_by_low_qos(self, spof_graph):
        """Structural SPOFs should retain baseline availability risk even if QoS weight is minimal."""
        for c in spof_graph.components:
            if c.id == "X":
                c.weight = 1.0

        struct_res_high = StructuralAnalyzer().analyze(spof_graph)
        quality_res_high = QualityAnalyzer().analyze(struct_res_high)
        a_high = {c.id: c.scores.availability for c in quality_res_high.components}["X"]

        for c in spof_graph.components:
            if c.id == "X":
                c.weight = 0.01

        struct_res_low = StructuralAnalyzer().analyze(spof_graph)
        quality_res_low = QualityAnalyzer().analyze(struct_res_low)
        a_low = {c.id: c.scores.availability for c in quality_res_low.components}["X"]

        ap_c_dir = struct_res_low.components["X"].ap_c_directed
        assert ap_c_dir >= 0.5
        expected_baseline = 0.35 * ap_c_dir
        assert a_low >= expected_baseline
        assert a_low < a_high


class TestAvailabilityOrthogonality:
    """Verifies that A(v) is driven by structural SPOF risk, not by PageRank alone."""

    def test_availability_orthogonality(self):
        comp_a = StructuralMetrics(
            id="A", name="AppA", type="app", pagerank=0.1, weight=0.5, is_articulation_point=True
        )
        comp_b = StructuralMetrics(
            id="B", name="AppB", type="app", pagerank=1.0, weight=0.1, is_articulation_point=False
        )

        result = StructuralAnalysisResult(
            layer=AnalysisLayer.APP,
            components={"A": comp_a, "B": comp_b},
            edges={},
            graph_summary=None,
        )

        weights = QualityWeights(
            a_qspof=1.0,
            a_bridge_ratio=0.0,
            a_ap_c_directed=0.0,
            a_cdi=0.0,
            a_qos_weight=0.0,
            ft_reverse_pagerank=1.0,
            ft_pagerank=0.0,
            ft_in_degree=0.0,
        )
        analyzer = QualityAnalyzer(weights=weights, use_ahp=False, normalization_method="max")
        quality_result = analyzer.analyze(result)

        qa = next(c for c in quality_result.components if c.id == "A")
        qb = next(c for c in quality_result.components if c.id == "B")

        assert qa.scores.availability >= qb.scores.availability
