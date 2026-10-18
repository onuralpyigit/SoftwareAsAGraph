import json
import math
from pathlib import Path

import networkx as nx
import numpy as np
import pytest
from scipy.stats import spearmanr

torch = pytest.importorskip("torch")

from saag.core.graph_io import build_graph_from_json
from saag.core.metrics import StructuralMetrics
from saag.prediction.data_preparation import (
    BASE_METRIC_KEYS,
    KEYS_BY_TYPE,
    LIBRARY_EXTRA_KEYS,
    NODE_TYPE_TO_DIM,
    _normalize_infra_features,
    _rank_normalize_base_columns,
    extract_rm_scores_dict,
    extract_simulation_dict,
    extract_structural_metrics_dict,
    networkx_to_hetero_data,
    node_type_dim,
    normalize_labels_robust,
)

def test_extract_simulation_dict_records_dict():
    raw = {
        "schema_version": "2.0",
        "graph_id": "test_graph",
        "total_nodes_injected": 2,
        "records": {
            "App1": {
                "node_id": "App1",
                "node_type": "Application",
                "node_name": "App One",
                "impact_score": 0.85,
                "cascade_depth": 3,
                "total_impacted_subscribers": 5
            },
            "App2": {
                "node_id": "App2",
                "node_type": "Application",
                "node_name": "App Two",
                "impact_score": 0.35,
                "cascade_depth": 1,
                "total_impacted_subscribers": 2
            }
        }
    }
    res = extract_simulation_dict(raw)
    # FaultInjector emits a single scalar I*(v), which maps onto composite /
    # reliability (reliability is itself the alpha-blend of fault-tolerance and
    # availability, so this scalar isn't emitted as a separate "availability"
    # key). maintainability must be ABSENT, not 0.0: an omitted key means
    # "unmeasured", a 0.0 means "measured as no impact". Emitting a fabricated
    # zero trained a head on a constant.
    assert res == {
        "App1": {
            "composite": 0.85,
            "reliability": 0.85,
        },
        "App2": {
            "composite": 0.35,
            "reliability": 0.35,
        }
    }

def test_extract_simulation_dict_records_list():
    raw = {
        "schema_version": "2.0",
        "records": [
            {
                "node_id": "App1",
                "impact_score": 0.75
            },
            {
                "id": "App2",
                "impact_score": 0.25
            }
        ]
    }
    res = extract_simulation_dict(raw)
    # As above: unmeasured dimensions are absent, not zero.
    assert res == {
        "App1": {
            "composite": 0.75,
            "reliability": 0.75,
        },
        "App2": {
            "composite": 0.25,
            "reliability": 0.25,
        }
    }

def test_extract_simulation_dict_legacy_list():
    raw = [
        {
            "target_id": "App1",
            "impact": {
                "composite_impact": 0.95,
                "reliability_impact": 0.85,
                "maintainability_impact": 0.75,
            }
        }
    ]
    res = extract_simulation_dict(raw)
    assert res == {
        "App1": {
            "composite": 0.95,
            "reliability": 0.85,
            "maintainability": 0.75,
        }
    }

def test_extract_rm_scores_dict_keys_by_id():
    # ComponentQuality has `id`, not `component_id` or `name` — a regression
    # guard for the bug where every key fell through to str(comp), leaving
    # networkx_to_hetero_data's name-keyed lookup (and therefore y_rm) all
    # zero. Uses a minimal duck-typed stand-in rather than the full
    # ComponentQuality/QualityScores dataclasses to keep this a pure
    # data_preparation unit test.
    from types import SimpleNamespace

    class _QualityResult:
        def __init__(self, components):
            self.components = components

    comp = SimpleNamespace(
        id="App1",
        scores=SimpleNamespace(
            overall=0.9, reliability=0.8, maintainability=0.7,
            fault_tolerance=0.65, availability=0.6,
        ),
    )
    res = extract_rm_scores_dict(_QualityResult([comp]))
    assert res == {
        "App1": {
            "overall": 0.9,
            "reliability": 0.8,
            "maintainability": 0.7,
            "fault_tolerance": 0.65,
            "availability": 0.6,
        }
    }


def test_fault_injector_does_not_mutate_graph():
    import networkx as nx
    from saag.simulation.fault_injector import FaultInjector

    # Create a graph without DEPENDS_ON edges
    g = nx.DiGraph()
    g.add_node("App1", type="Application")
    g.add_node("App2", type="Application")
    g.add_node("Topic1", type="Topic")
    g.add_edge("App1", "Topic1", type="PUBLISHES_TO")
    g.add_edge("App2", "Topic1", type="SUBSCRIBES_TO")

    # Record the original edges
    original_edges = set(g.edges())

    # Initialize FaultInjector, which dynamically derives DEPENDS_ON edges
    injector = FaultInjector(g)

    # Check that original graph's edges have not changed
    assert set(g.edges()) == original_edges

    # Check that FaultInjector's internal graph has the derived DEPENDS_ON edges
    internal_edges = set(injector.graph.edges())
    assert len(internal_edges) > len(original_edges)
    assert ("App2", "App1") in internal_edges


# =============================================================================
# Physical Network Data Preparation Tests (from test_data_preparation_network.py)
# =============================================================================

class TestNetworkDataPreparation:
    @staticmethod
    def _hetero(path):
        return networkx_to_hetero_data(build_graph_from_json(json.loads(Path(path).read_text()))).hetero_data

    def test_device_free_graph_is_unchanged(self):
        data = self._hetero("data/scenarios/atm_system.json")
        assert "NetworkDevice" not in data.node_types
        attr = data["Node", "CONNECTS_TO", "Node"].edge_attr
        assert attr[:, 0].tolist() == pytest.approx([1.0] * len(attr))
        assert attr[:, 1].tolist() == pytest.approx([math.log2(2) / math.log2(17)] * len(attr))

    def test_devices_get_their_own_type_and_features(self):
        data = self._hetero("data/network/atm_two_tier_system.json")
        x = data["NetworkDevice"].x
        assert x.shape == (4, node_type_dim("NetworkDevice"))
        utilisation, transit = x[:, 18], x[:, 19]
        assert ((utilisation > 0) & (utilisation <= 1)).all()
        assert transit.max().item() == pytest.approx(1.0)

    def test_network_link_weight_slot_is_headroom(self):
        data = self._hetero("data/network/atm_two_tier_system.json")
        headroom = data["NetworkDevice", "CONNECTS_TO", "NetworkDevice"].edge_attr[:, 0]
        assert (headroom < 1.0).all() and (headroom >= 0.0).all()

    def test_untyped_device_is_not_relabelled_application(self):
        data = self._hetero("data/network/atm_two_tier_system.json")
        assert len(data["Application"].x) == 26

    def test_baseline_capacity_is_unchanged_by_the_optional_type(self):
        assert "NetworkDevice" not in NODE_TYPE_TO_DIM


# =============================================================================
# GNN Code Quality Features Tests (from test_gnn_code_quality_features.py)
# =============================================================================

class TestGNNCodeQualityFeatures:
    def test_node_feature_dimensions(self):
        assert NODE_TYPE_TO_DIM["Application"] == 23
        assert NODE_TYPE_TO_DIM["Library"] == 25
        assert NODE_TYPE_TO_DIM["Broker"] == 19
        assert NODE_TYPE_TO_DIM["Topic"] == 22
        assert NODE_TYPE_TO_DIM["Node"] == 20

    def test_networkx_to_hetero_data_feature_mapping(self):
        g = nx.DiGraph()
        g.add_node("A1", component_type="Application")
        g.add_node("L1", component_type="Library")
        g.add_node("B1", component_type="Broker")

        structural_metrics = {
            "A1": {
                "pagerank": 0.1,
                "loc_norm": 0.5,
                "complexity_norm": 0.6,
                "instability_code": 0.7,
                "lcom_norm": 0.8,
                "code_quality_penalty": 0.9,
            },
            "L1": {"loc_norm": 0.2, "complexity_norm": 0.3},
            "B1": {"pagerank": 0.05},
        }

        conv = networkx_to_hetero_data(g, structural_metrics=structural_metrics)
        data = conv.hetero_data

        assert data["Application"].x.shape[1] == 23
        assert data["Library"].x.shape[1] == 25
        assert data["Broker"].x.shape[1] == 19

        a1_x = data["Application"].x[0].numpy()
        assert a1_x[18] == pytest.approx(0.5)
        assert a1_x[19] == pytest.approx(0.6)
        assert a1_x[20] == pytest.approx(0.7)
        assert a1_x[21] == pytest.approx(0.8)
        assert a1_x[22] == pytest.approx(0.9)

        b1_x = data["Broker"].x[0].numpy()
        assert b1_x.shape[0] == 19
        assert b1_x[0] == pytest.approx(0.05)

    def test_extract_structural_metrics_dict_includes_cq(self):
        mock_comp = StructuralMetrics(
            id="A1",
            name="App1",
            type="Application",
            loc_norm=0.5,
            complexity_norm=0.6,
            instability_code=0.7,
            lcom_norm=0.8,
            code_quality_penalty=0.9,
        )

        class MockResult:
            def __init__(self, components):
                self.components = components

        out = extract_structural_metrics_dict(MockResult([mock_comp]))
        metrics = out["A1"]
        assert metrics["loc_norm"] == 0.5
        assert metrics["complexity_norm"] == 0.6
        assert metrics["instability_code"] == 0.7
        assert metrics["lcom_norm"] == 0.8
        assert metrics["code_quality_penalty"] == 0.9
        assert metrics["pagerank"] == 0.0


# =============================================================================
# Feature Normalization Tests (from test_feature_normalization.py)
# =============================================================================

class TestFeatureNormalization:
    @staticmethod
    def _feat_graph(n_apps: int = 5, n_brokers: int = 0) -> nx.DiGraph:
        g = nx.DiGraph()
        for i in range(n_apps):
            g.add_node(f"A{i}", component_type="Application", type="Application")
        for i in range(n_brokers):
            g.add_node(f"B{i}", component_type="Broker", type="Broker")
        g.add_node("T0", component_type="Topic", type="Topic")
        for i in range(n_apps):
            g.add_edge(f"A{i}", "T0", dependency_type="app_to_app", type="PUBLISHES_TO", weight=1.0)
        return g

    @staticmethod
    def _feat_structural_metrics(n_apps: int = 5) -> dict:
        pagerank = [0.5, 0.1, 0.5, 0.3, 0.9][:n_apps]
        betweenness = [10.0, 20.0, 30.0, 5.0, 100.0][:n_apps]
        sm = {}
        for i in range(n_apps):
            sm[f"A{i}"] = {
                "pagerank": pagerank[i],
                "betweenness_centrality": betweenness[i],
                "loc_norm": 0.1 * i,
                "complexity_norm": 0.2 * i,
            }
        return sm

    def test_rank_normalize_range_and_order(self):
        g = self._feat_graph()
        sm = self._feat_structural_metrics()
        result = networkx_to_hetero_data(g, structural_metrics=sm, rank_normalize_features=True)
        x = result.hetero_data["Application"].x.numpy()

        n_base = len(BASE_METRIC_KEYS)
        assert np.all(x[:, :n_base] >= 0.0) and np.all(x[:, :n_base] <= 1.0)

        pr_col = BASE_METRIC_KEYS.index("pagerank")
        bt_col = BASE_METRIC_KEYS.index("betweenness_centrality")
        raw_pr = [0.5, 0.1, 0.5, 0.3, 0.9]
        raw_bt = [10.0, 20.0, 30.0, 5.0, 100.0]

        assert spearmanr(raw_pr, x[:, pr_col]).correlation == pytest.approx(1.0)
        assert spearmanr(raw_bt, x[:, bt_col]).correlation == pytest.approx(1.0)
        assert x[0, pr_col] == pytest.approx(x[2, pr_col])

    def test_rank_normalize_excludes_type_specific_extras(self):
        g = self._feat_graph()
        sm = self._feat_structural_metrics()
        off = networkx_to_hetero_data(g, structural_metrics=sm, rank_normalize_features=False)
        on = networkx_to_hetero_data(g, structural_metrics=sm, rank_normalize_features=True)
        n_base = len(BASE_METRIC_KEYS)
        np.testing.assert_array_equal(
            off.hetero_data["Application"].x.numpy()[:, n_base:],
            on.hetero_data["Application"].x.numpy()[:, n_base:],
        )

    def test_rank_normalize_off_by_default_is_bit_identical(self):
        g = self._feat_graph()
        sm = self._feat_structural_metrics()
        default = networkx_to_hetero_data(g, structural_metrics=sm)
        explicit_off = networkx_to_hetero_data(g, structural_metrics=sm, rank_normalize_features=False)
        np.testing.assert_array_equal(
            default.hetero_data["Application"].x.numpy(),
            explicit_off.hetero_data["Application"].x.numpy(),
        )
        x = default.hetero_data["Application"].x.numpy()
        pr_col = BASE_METRIC_KEYS.index("pagerank")
        assert x[:, pr_col] == pytest.approx([0.5, 0.1, 0.5, 0.3, 0.9])

    def test_rank_normalize_single_node_type_guard(self):
        g = self._feat_graph(n_apps=5, n_brokers=1)
        sm = self._feat_structural_metrics()
        sm["B0"] = {"pagerank": 0.7, "betweenness_centrality": 15.0}
        result = networkx_to_hetero_data(g, structural_metrics=sm, rank_normalize_features=True)
        x = result.hetero_data["Broker"].x.numpy()
        n_base = len(BASE_METRIC_KEYS)
        assert not np.any(np.isnan(x[:, :n_base]))
        assert np.all(x[:, :n_base] == 0.5)

    def test_rank_normalize_base_columns_helper_directly(self):
        feat = np.array([[1.0, 5.0], [1.0, 5.0], [1.0, 5.0]], dtype=np.float32)
        _rank_normalize_base_columns(feat)
        assert np.all(feat == 0.5)


# =============================================================================
# Label Normalization Tests (from test_label_normalization.py)
# =============================================================================

class TestLabelNormalization:
    @staticmethod
    def _lbl_graph_two_types(n_apps: int = 6, n_brokers: int = 4) -> nx.DiGraph:
        g = nx.DiGraph()
        for i in range(n_apps):
            g.add_node(f"A{i}", component_type="Application", type="Application")
        for i in range(n_brokers):
            g.add_node(f"B{i}", component_type="Broker", type="Broker")
        g.add_node("T0", component_type="Topic", type="Topic")
        for i in range(n_apps):
            g.add_edge(f"A{i}", "T0", dependency_type="app_to_app", type="PUBLISHES_TO", weight=1.0)
        for i in range(n_brokers):
            g.add_edge(f"B{i}", "T0", dependency_type="broker_to_topic", type="ROUTES", weight=1.0)
        return g

    @classmethod
    def _lbl_convert(cls, scores: dict, n_apps: int = 6, n_brokers: int = 4):
        artifact = {"records": {nid: {"impact_score": v} for nid, v in scores.items()}}
        sim = extract_simulation_dict(artifact)
        return networkx_to_hetero_data(
            cls._lbl_graph_two_types(n_apps, n_brokers), structural_metrics={}, simulation_results=sim
        )

    def test_default_is_iqr_and_unchanged(self):
        scores = {"A0": 0.9, "A1": 0.4, "A2": 0.1, "A3": 0.7, "B0": 0.5, "B1": 0.2}
        r1 = self._lbl_convert(scores)
        normalize_labels_robust(r1.hetero_data)
        r2 = self._lbl_convert(scores)
        normalize_labels_robust(r2.hetero_data, rank_normalize=False)
        torch.testing.assert_close(r1.hetero_data["Application"].y, r2.hetero_data["Application"].y)
        torch.testing.assert_close(r1.hetero_data["Broker"].y, r2.hetero_data["Broker"].y)

    def test_rank_normalize_range_order_and_zeros_preserved(self):
        scores = {"A0": 0.9, "A1": 0.4, "A2": 0.1, "A3": 0.0, "B0": 0.5, "B1": 0.2}
        result = self._lbl_convert(scores)
        data = result.hetero_data
        normalize_labels_robust(data, rank_normalize=True)

        app_y = data["Application"].y[:, 0]
        broker_y = data["Broker"].y[:, 0]
        assert app_y[3].item() == pytest.approx(0.0)

        nonzero_labelled = torch.cat([app_y[:3], broker_y[:2]])
        assert torch.all(nonzero_labelled > 0.0) and torch.all(nonzero_labelled < 1.0)

        raw = [0.9, 0.4, 0.1, 0.5, 0.2]
        normed = [app_y[0].item(), app_y[1].item(), app_y[2].item(), broker_y[0].item(), broker_y[1].item()]
        assert spearmanr(raw, normed).correlation == pytest.approx(1.0)

    def test_rank_normalize_does_not_swap_node_types(self):
        scores = {"A0": 0.05, "A1": 0.10, "A2": 0.15, "A3": 0.20, "B0": 0.90, "B1": 0.95}
        result = self._lbl_convert(scores)
        data = result.hetero_data
        normalize_labels_robust(data, rank_normalize=True)

        app_y = data["Application"].y[:4, 0]
        broker_y = data["Broker"].y[:2, 0]
        assert torch.all(app_y < broker_y.min())

    def test_rank_normalize_single_labelled_node_guard(self):
        result = self._lbl_convert({"A0": 0.5})
        data = result.hetero_data
        normalize_labels_robust(data, rank_normalize=True)
        assert data["Application"].y[0, 0].item() == pytest.approx(0.5)


# =============================================================================
# Library Features Tests (from test_library_features.py)
# =============================================================================

class TestLibraryFeatures:
    @staticmethod
    def _uses_chain_graph() -> nx.DiGraph:
        g = nx.DiGraph()
        g.add_node("A0", type="Application")
        g.add_node("L1", type="Library")
        g.add_node("L2", type="Library")
        g.add_node("L3", type="Library")
        g.add_edge("A0", "L1", type="USES")
        g.add_edge("L1", "L2", type="USES")
        return g

    def test_transitive_closure_is_actually_transitive(self):
        infra = _normalize_infra_features(self._uses_chain_graph())
        assert infra["L2"]["library_uses_reach_norm"] == pytest.approx(1.0)
        assert infra["L1"]["library_uses_reach_norm"] == pytest.approx(0.5)
        assert infra["L3"]["library_uses_reach_norm"] == pytest.approx(0.0)

    def test_downstream_subscribers_dedup_across_shared_topic(self):
        g = nx.DiGraph()
        g.add_node("A0", type="Application")
        g.add_node("A1", type="Application")
        g.add_node("L0", type="Library")
        g.add_node("T0", type="Topic")
        g.add_node("T1", type="Topic")
        g.add_node("Sub", type="Application")
        g.add_edge("A0", "L0", type="USES")
        g.add_edge("A1", "L0", type="USES")
        g.add_edge("A0", "T0", type="PUBLISHES_TO")
        g.add_edge("A1", "T1", type="PUBLISHES_TO")
        g.add_edge("Sub", "T0", type="SUBSCRIBES_TO")
        g.add_edge("Sub", "T1", type="SUBSCRIBES_TO")

        infra = _normalize_infra_features(g)
        g.add_node("L1", type="Library")
        g.add_node("A2", type="Application")
        g.add_node("T2", type="Topic")
        g.add_edge("A2", "L1", type="USES")
        g.add_edge("A2", "T2", type="PUBLISHES_TO")
        g.add_edge("Sub", "T2", type="SUBSCRIBES_TO")
        infra = _normalize_infra_features(g)
        assert infra["L0"]["library_downstream_subs_norm"] == pytest.approx(1.0)
        assert infra["L1"]["library_downstream_subs_norm"] == pytest.approx(1.0)

    def test_columns_land_at_expected_indices_and_survive_rank_normalization(self):
        g = self._uses_chain_graph()
        result = networkx_to_hetero_data(g, structural_metrics={}, rank_normalize_features=True)
        x = result.hetero_data["Library"].x.numpy()

        reach_col = KEYS_BY_TYPE["Library"].index("library_uses_reach_norm")
        downstream_col = KEYS_BY_TYPE["Library"].index("library_downstream_subs_norm")
        assert reach_col == 23
        assert downstream_col == 24
        assert x.shape[1] == NODE_TYPE_TO_DIM["Library"] == 25

        result_off = networkx_to_hetero_data(g, structural_metrics={}, rank_normalize_features=False)
        x_off = result_off.hetero_data["Library"].x.numpy()
        assert x[:, reach_col:downstream_col + 1] == pytest.approx(x_off[:, reach_col:downstream_col + 1])

    def test_application_feature_width_unchanged(self):
        assert KEYS_BY_TYPE["Application"] is not KEYS_BY_TYPE["Library"]
        assert len(KEYS_BY_TYPE["Application"]) == NODE_TYPE_TO_DIM["Application"] == 23
        assert "library_uses_reach_norm" not in KEYS_BY_TYPE["Application"]

        g = self._uses_chain_graph()
        result = networkx_to_hetero_data(g, structural_metrics={})
        assert result.hetero_data["Application"].x.shape[1] == 23

    def test_keys_by_type_and_node_type_to_dim_agree_for_library(self):
        assert len(KEYS_BY_TYPE["Library"]) == NODE_TYPE_TO_DIM["Library"] == 25
        assert set(LIBRARY_EXTRA_KEYS) <= set(KEYS_BY_TYPE["Library"])
