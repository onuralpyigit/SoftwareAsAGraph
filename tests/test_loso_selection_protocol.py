"""
test_loso_selection_protocol.py
────────────────────────────────
Pins the LOSO model-selection protocol added alongside the Topo-QoS comparison:

  * ``--inner-val-scenario auto`` selects checkpoints on a training scenario that
    is held out of the loss, instead of a within-scenario split of the primary
    graph. Selecting on the training distribution, under a protocol whose entire
    point is distribution shift, selects for the wrong thing.
  * the outer holdout is never eligible as that validation scenario;
  * ``val_data=None`` leaves ``GNNTrainer.train``'s validation target exactly
    where it was, so no committed number moves without an explicit flag;
  * inference rebuilds features under the same transform training used —
    a rank-normalized model scored on un-normalized features is silently wrong.
"""

import inspect

import networkx as nx
import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("torch_geometric")

from unittest.mock import patch

from cli.loso_evaluate import (  # noqa: E402
    ScenarioBundle,
    _build_validation_hetero,
    _select_val_bundle,
    discover_scenarios,
)
from saag.prediction.gnn_service import GNNService  # noqa: E402
from saag.prediction.trainer import GNNTrainer  # noqa: E402


class _Bundle:
    """Duck-typed stand-in for ScenarioBundle: only the fields under test."""

    def __init__(self, scenario_id: str, n_nodes: int):
        self.scenario_id = scenario_id
        self.n_nodes = n_nodes


def _bundles():
    return [
        _Bundle("alpha", 100),
        _Bundle("bravo", 300),
        _Bundle("charlie", 200),
        _Bundle("delta", 500),
        _Bundle("echo", 400),
    ]


# ── Selection of the inner validation scenario ────────────────────────────────

def test_inner_val_off_by_default_returns_none():
    assert _select_val_bundle(_bundles(), "none") is None


def test_inner_val_auto_picks_median_sized_scenario():
    picked = _select_val_bundle(_bundles(), "auto")
    # sorted sizes 100, 200, 300, 400, 500 → index 2 → bravo (300)
    assert picked.scenario_id == "bravo"


def test_inner_val_auto_is_deterministic_under_reordering():
    import random

    reference = _select_val_bundle(_bundles(), "auto").scenario_id
    for _ in range(10):
        shuffled = _bundles()
        random.shuffle(shuffled)
        assert _select_val_bundle(shuffled, "auto").scenario_id == reference


def test_inner_val_auto_breaks_size_ties_by_scenario_id():
    tied = [_Bundle("zulu", 100), _Bundle("alpha", 100), _Bundle("mike", 100)]
    assert _select_val_bundle(tied, "auto").scenario_id == "mike"


def test_inner_val_auto_handles_empty_inductive_set():
    assert _select_val_bundle([], "auto") is None


def test_holdout_can_never_be_the_validation_scenario():
    """The candidate pool is the inductive set, which excludes holdout and primary."""
    all_bundles = _bundles()
    for holdout in all_bundles:
        train_set = [b for b in all_bundles if b.scenario_id != holdout.scenario_id]
        primary = max(train_set, key=lambda b: b.n_nodes)
        inductives = [b for b in train_set if b.scenario_id != primary.scenario_id]
        picked = _select_val_bundle(inductives, "auto")
        assert picked.scenario_id != holdout.scenario_id
        assert picked.scenario_id != primary.scenario_id


# ── The off-switch is a true no-op ────────────────────────────────────────────

def test_trainer_val_data_defaults_to_none():
    sig = inspect.signature(GNNTrainer.train)
    assert sig.parameters["val_data"].default is None


def test_service_val_graph_defaults_to_none():
    sig = inspect.signature(GNNService.train)
    assert sig.parameters["val_graph"].default is None


def test_trainer_prefers_val_data_over_primary_data():
    """Source order in train(): val_data, then primary_data, then the loader."""
    src = inspect.getsource(GNNTrainer.train)
    i_val = src.index("if val_data is not None")
    i_primary = src.index("elif primary_data is not None")
    assert i_val < i_primary


# ── Train/inference feature-transform agreement ───────────────────────────────

def test_predict_defaults_to_the_training_feature_transform(tmp_path, monkeypatch):
    """`predict` must rebuild features the way `train` built them.

    A model fitted on rank-normalized features and scored on raw ones produces
    numbers, not errors, so this is pinned rather than left to inspection.
    """
    sig = inspect.signature(GNNService.predict)
    assert sig.parameters["rank_normalize_features"].default is None

    import saag.prediction.gnn_service as gs

    seen = {}
    real = gs.networkx_to_hetero_data

    def _spy(*args, **kwargs):
        seen["rank_normalize_features"] = kwargs.get("rank_normalize_features")
        return real(*args, **kwargs)

    monkeypatch.setattr(gs, "networkx_to_hetero_data", _spy)

    svc = GNNService(checkpoint_dir=str(tmp_path), predict_edges=False)
    svc._rank_normalize_features = True
    svc._node_model = object()  # past the "models not initialised" guard

    bundle = _tiny_bundle()
    with pytest.raises(Exception):
        # predict_from_data will fail on the dummy model; the conversion that
        # this test is about has already happened by then.
        svc.predict(graph=bundle.graph, structural_metrics={})

    assert seen["rank_normalize_features"] is True, (
        "predict() rebuilt features under a different transform than train()"
    )


def test_fresh_service_reports_no_rank_normalization(tmp_path):
    svc = GNNService(checkpoint_dir=str(tmp_path), predict_edges=False)
    assert svc._rank_normalize_features is False


# ── The validation graph is scored, never fitted ──────────────────────────────

def _tiny_bundle():
    from saag.prediction.data_preparation import networkx_to_hetero_data  # noqa: F401

    g = nx.DiGraph()
    for i in range(6):
        g.add_node(f"A{i}", component_type="Application", type="Application")
    g.add_node("T0", component_type="Topic", type="Topic")
    for i in range(6):
        g.add_edge(f"A{i}", "T0", dependency_type="app_to_app",
                   type="PUBLISHES_TO", weight=1.0)

    class B:
        scenario_id = "tiny"
        graph = g
        structural = {}
        rm = {}
        simulation = {f"A{i}": {"composite": 0.1 * (i + 1)} for i in range(6)}
        # No edge-removal sweep for this fixture, so the edge head goes
        # unsupervised — which is the point: labels are measured or absent.
        edge_simulation = {}
        n_nodes = 7

    return B()


def test_validation_graph_masks_are_score_only():
    data = _build_validation_hetero(_tiny_bundle(), use_qos=True,
                                    rank_normalize_features=False)
    saw_labelled = False
    for store in data.node_stores:
        assert not store.train_mask.any(), "validation graph must not be trained on"
        assert not store.test_mask.any(), "validation graph is not a test set"
        if store.val_mask.any():
            saw_labelled = True
    assert saw_labelled, "validation graph carries no scorable nodes"


def test_validation_graph_val_mask_is_the_labelled_population():
    from saag.prediction.data_preparation import _labelled_index_mask

    data = _build_validation_hetero(_tiny_bundle(), use_qos=True,
                                    rank_normalize_features=False)
    for store in data.node_stores:
        if hasattr(store, "y") and store.y.numel() > 0:
            expected = torch.from_numpy(_labelled_index_mask(store))
            assert torch.equal(store.val_mask, expected)


# ── GAT/HGT training-set parity ───────────────────────────────────────────────

def _labelled_bundle(scenario_id: str, n_apps: int, seed: int):
    """A minimal ScenarioBundle: enough graph and labels to train two epochs on."""
    import numpy as np

    from cli.loso_evaluate import ScenarioBundle
    from saag.prediction.data_preparation import networkx_to_hetero_data

    rng = np.random.default_rng(seed)
    g = nx.DiGraph()
    for i in range(n_apps):
        g.add_node(f"{scenario_id}_A{i}", component_type="Application", type="Application")
    for t in range(2):
        g.add_node(f"{scenario_id}_T{t}", component_type="Topic", type="Topic")
    for i in range(n_apps):
        t = f"{scenario_id}_T{i % 2}"
        g.add_edge(f"{scenario_id}_A{i}", t, dependency_type="app_to_app",
                   type="PUBLISHES_TO", weight=1.0)
        g.add_edge(t, f"{scenario_id}_A{(i + 1) % n_apps}", dependency_type="app_to_app",
                   type="SUBSCRIBES_TO", weight=1.0)

    simulation = {
        f"{scenario_id}_A{i}": {"composite": float(rng.random())}
        for i in range(n_apps)
    }
    conv = networkx_to_hetero_data(g, {}, simulation, {})
    return ScenarioBundle(
        scenario_id=scenario_id, graph=g, structural={}, rm={},
        simulation=simulation, hetero_data=conv.hetero_data,
        n_nodes=g.number_of_nodes(), n_edges=g.number_of_edges(),
        n_labelled=n_apps,
    )


def test_gat_branch_trains_on_every_training_scenario(tmp_path, monkeypatch):
    """The untyped baseline must see the same graphs the typed model sees.

    This branch used to call ``trainer.train(data)`` with only the primary
    scenario while the HGT branch passed every remaining scenario through
    ``inductive_graphs``, so the published typed-vs-untyped LOSO margin compared
    a model with N-1 training graphs against one with a single graph.
    """
    from torch_geometric.data import HeteroData
    from torch_geometric.loader import DataLoader

    import saag.prediction.trainer as trainer_mod
    from cli.loso_evaluate import run_one_fold

    bundles = [
        _labelled_bundle("alpha", 8, 1),
        _labelled_bundle("bravo", 10, 2),
        _labelled_bundle("charlie", 6, 3),
        _labelled_bundle("delta", 7, 4),
    ]

    seen = {}
    real_train = trainer_mod.GNNTrainer.train

    def _spy(self, data, primary_data=None, val_data=None):
        seen["n_graphs"] = 1 if isinstance(data, HeteroData) else len(data.dataset)
        seen["has_val"] = val_data is not None
        return real_train(self, data, primary_data=primary_data, val_data=val_data)

    monkeypatch.setattr(trainer_mod.GNNTrainer, "train", _spy)

    run_one_fold(
        bundles=bundles, holdout_idx=0, seeds=[42], layer="app", epochs=2,
        lr=3e-4, hidden=16, heads=2, layers=1, dropout=0.1,
        workdir=tmp_path, mode="gnn", variant="gl_qos",
        auto_layers=False, inner_val="none",
    )

    # holdout excluded, primary + 2 inductives train => 3 of the 4 scenarios.
    assert seen["n_graphs"] == len(bundles) - 1, (
        f"GAT branch trained on {seen['n_graphs']} graph(s); the HGT branch "
        f"gets {len(bundles) - 1}"
    )


def test_gat_branch_accepts_the_inner_validation_graph(tmp_path, monkeypatch):
    import saag.prediction.trainer as trainer_mod
    from cli.loso_evaluate import run_one_fold

    bundles = [
        _labelled_bundle("alpha", 8, 1),
        _labelled_bundle("bravo", 10, 2),
        _labelled_bundle("charlie", 6, 3),
        _labelled_bundle("delta", 7, 4),
    ]

    seen = {}
    real_train = trainer_mod.GNNTrainer.train

    def _spy(self, data, primary_data=None, val_data=None):
        seen["has_val"] = val_data is not None
        return real_train(self, data, primary_data=primary_data, val_data=val_data)

    monkeypatch.setattr(trainer_mod.GNNTrainer, "train", _spy)

    run_one_fold(
        bundles=bundles, holdout_idx=0, seeds=[42], layer="app", epochs=2,
        lr=3e-4, hidden=16, heads=2, layers=1, dropout=0.1,
        workdir=tmp_path, mode="gnn", variant="gl_qos",
        auto_layers=False, inner_val="auto",
    )
    assert seen["has_val"] is True


# =============================================================================
# Scenario Discovery Contract (from test_discover_scenarios_min.py)
# =============================================================================

class TestDiscoverScenarios:
    @staticmethod
    def _fake_bundle(scenario_id: str) -> ScenarioBundle:
        return ScenarioBundle(
            scenario_id=scenario_id, graph=None, structural={}, rm={}, simulation={},
            hetero_data=None, n_nodes=10, n_edges=10, n_labelled=5,
        )

    def test_default_requires_at_least_two_scenarios(self, tmp_path):
        (tmp_path / "atm_system").mkdir()
        with patch("cli.loso_evaluate.load_scenario_bundle", return_value=self._fake_bundle("atm_system")):
            with pytest.raises(ValueError, match="Need >= 2"):
                discover_scenarios(tmp_path, skip=[])

    def test_min_scenarios_one_allows_single_scenario(self, tmp_path):
        (tmp_path / "atm_system").mkdir()
        with patch("cli.loso_evaluate.load_scenario_bundle", return_value=self._fake_bundle("atm_system")):
            bundles = discover_scenarios(tmp_path, skip=[], min_scenarios=1)
        assert len(bundles) == 1
        assert bundles[0].scenario_id == "atm_system"

    def test_min_scenarios_one_still_errors_on_zero_usable(self, tmp_path):
        (tmp_path / "atm_system").mkdir()
        with patch("cli.loso_evaluate.load_scenario_bundle", return_value=None):
            with pytest.raises(ValueError, match="Need >= 1"):
                discover_scenarios(tmp_path, skip=[], min_scenarios=1)
