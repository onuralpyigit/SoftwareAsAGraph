import pytest
import json
import torch
from pathlib import Path
from unittest.mock import MagicMock, patch
from saag.prediction.gnn_service import GNNService, GNNAnalysisResult
from saag.prediction.data_preparation import GraphConversionResult
from saag.prediction.trainer import EvalMetrics
from torch_geometric.data import HeteroData

@pytest.fixture
def temp_checkpoint(tmp_path):
    """Create a temporary checkpoint with a service_config.json."""
    ckpt_dir = tmp_path / "gnn_ckpt"
    ckpt_dir.mkdir()
    config = {
        "hidden_channels": 64,
        "num_heads": 4,
        "num_layers": 3,
        "dropout": 0.2,
        "predict_edges": False,
        "layer": "app",
        "label_dims": 3,
    }
    with open(ckpt_dir / "service_config.json", "w") as f:
        json.dump(config, f)
    return ckpt_dir

def test_layer_validation_error(temp_checkpoint):
    """G13: Verify that from_checkpoint raises ValueError on layer mismatch."""
    # Loading as 'infra' when ckpt is 'app'
    with pytest.raises(ValueError, match="GNN Layer Mismatch"):
        GNNService.from_checkpoint(str(temp_checkpoint), layer="infra")

def test_layer_validation_missing_ok(temp_checkpoint):
    """G13: Verify that loading without layer parameter or with matching layer is OK."""
    # Matching
    service = GNNService.from_checkpoint(str(temp_checkpoint), layer="app")
    assert service.layer == "app"
    
    # Missing (fallback)
    service2 = GNNService.from_checkpoint(str(temp_checkpoint))
    assert service2.layer == "app"

def test_prediction_mode_reporting():
    """Verify prediction_mode reporting for the two supported modes."""
    service = GNNService()
    service._node_model = MagicMock()
    service._node_model.predict_edges = False
    service._node_model.return_value = {"Application": torch.randn(2, 3)}
    service._conversion_result = MagicMock()
    service._conversion_result.node_id_map = {"Application": ["app1", "app2"]}

    data = HeteroData()
    data["Application"].x = torch.randn(2, 23)
    data["Application"].y_rm = torch.randn(2, 3)

    result_gnn = service.predict_from_data(data, mode="gnn")
    assert result_gnn.prediction_mode == "gnn_only"

    result_rm = service.predict_from_data(data, mode="rm")
    assert result_rm.prediction_mode == "rm_only"


def test_rm_mode_falls_back_to_gnn_without_rm_scores(caplog):
    """mode='rm' on a graph carrying no y_rm must degrade to GNN, not crash."""
    service = GNNService()
    service._node_model = MagicMock()
    service._node_model.predict_edges = False
    service._node_model.return_value = {"Application": torch.randn(2, 3)}
    service._conversion_result = MagicMock()
    service._conversion_result.node_id_map = {"Application": ["app1", "app2"]}

    data = HeteroData()
    data["Application"].x = torch.randn(2, 23)

    import logging
    with caplog.at_level(logging.WARNING):
        result = service.predict_from_data(data, mode="rm")

    assert result.prediction_mode == "gnn_only"
    assert result.node_scores["app1"].source == "GNN"
    assert "no RM scores available" in caplog.text


def test_edge_head_receives_gradient_from_edge_loss():
    """The TypedEdgeEncoder must actually be trained.

    `y_edge` labels were written by data_preparation but no loss term ever read
    them, so the edge head kept its random initialisation and every edge score
    the CLI/API emitted was noise. GNNTrainer._edge_loss closes that gap.

    `y_edge_mask` is part of the fixture because it is part of the contract:
    the edge-removal sweep measures only its candidate set, so a label tensor
    without a mask cannot say which rows are measurements and which are
    padding. `_edge_loss` skips an unmasked relation rather than guess.
    """
    from saag.prediction.models import build_edge_gnn
    from saag.prediction.trainer import GNNTrainer

    rel = ("Application", "DEPENDS_ON", "Application")
    metadata = (["Application"], [rel])
    model = build_edge_gnn(metadata, hidden_channels=16, num_heads=2, num_layers=1)

    data = HeteroData()
    data["Application"].x = torch.randn(4, 23)
    data["Application"].y = torch.rand(4, 3)
    data["Application"].train_mask = torch.ones(4, dtype=torch.bool)
    data["Application"].label_mask = torch.ones(4, dtype=torch.bool)
    data[rel].edge_index = torch.tensor([[0, 1, 2], [1, 2, 3]])
    data[rel].edge_attr = torch.randn(3, 16)
    data[rel].y_edge = torch.rand(3, 1)
    data[rel].y_edge_mask = torch.ones(3, dtype=torch.bool)

    trainer = GNNTrainer(model=model, checkpoint_dir="/tmp/_edge_loss_test", num_epochs=1)

    head = model.typed_edge_enc.out_head.fc1.weight
    before = head.detach().clone()

    optimizer = torch.optim.SGD(model.parameters(), lr=0.5)
    trainer._run_epoch([data], optimizer)

    assert not torch.equal(before, head.detach()), (
        "edge head weights unchanged after a training step — no edge gradient"
    )


def test_edge_loss_is_zero_without_edge_labels():
    """Graphs converted without simulation results carry no y_edge; the edge
    term must contribute nothing rather than raise or fabricate a target."""
    from saag.prediction.models import build_edge_gnn
    from saag.prediction.trainer import GNNTrainer

    rel = ("Application", "DEPENDS_ON", "Application")
    model = build_edge_gnn((["Application"], [rel]), hidden_channels=16, num_heads=2, num_layers=1)
    trainer = GNNTrainer(model=model, checkpoint_dir="/tmp/_edge_loss_test")

    data = HeteroData()
    data[rel].edge_index = torch.tensor([[0], [1]])
    edge_preds = {rel: torch.rand(1, 3)}

    assert trainer._edge_loss(edge_preds, data).item() == 0.0


def test_node_model_is_edge_models_inner_gnn():
    """The edge model must not carry a second, independently-initialised node GNN.

    Training only ever optimises `_edge_model`; if `_node_model` were a separate
    network it would stay at random init and then be used for the reported
    metrics and written to node_model.pt.
    """
    metadata = (
        ["Application"],
        [("Application", "DEPENDS_ON", "Application")],
    )

    service = GNNService(hidden_channels=16, predict_edges=True)
    service._init_models(metadata)
    assert service._node_model is service._edge_model.node_gnn

    node_only = GNNService(hidden_channels=16, predict_edges=False)
    node_only._init_models(metadata)
    assert node_only._edge_model is None
    assert node_only._node_model is not None


# =============================================================================
# GNN Evaluation & Seed Consistency (from test_gnn_evaluation.py)
# =============================================================================

@pytest.fixture
def mock_gnn_service():
    """Service with mocked models and internal metadata."""
    service = GNNService(checkpoint_dir="test_ckpt")
    service._node_model = MagicMock()
    service._node_model.predict_edges = False
    service._node_model.to.return_value = service._node_model
    service._node_model.eval.return_value = None

    conv = MagicMock(spec=GraphConversionResult)
    conv.node_id_map = {"Application": ["app1", "app2"]}
    conv.edge_name_map = {}
    service._conversion_result = conv

    return service


def test_mask_consistency_best_seed(mock_gnn_service):
    """G9: Verify that the best seed is persisted and used for splitting."""
    service = mock_gnn_service
    service._best_seed = 1234

    data = HeteroData()
    data["Application"].x = torch.randn(2, 23)
    data["Application"].y = torch.tensor([[0.5] * 5, [0.8] * 5])

    mock_metrics = EvalMetrics(0.9, 0.8, 0.1, 0.1, 1.0, 1.0, 1.0)

    with patch("saag.prediction.gnn_service.create_node_splits") as mock_split:
        with patch("saag.prediction.gnn_service.evaluate", return_value=mock_metrics):
            service._node_model.return_value = {"Application": torch.randn(2, 5)}
            service.predict_from_data(data, eval_labels={"app1": {}})

            mock_split.assert_called_once()
            assert mock_split.call_args.kwargs["seed"] == 1234


def test_ablation_modes(mock_gnn_service):
    """Verify that mode selection picks the correct score source."""
    service = mock_gnn_service

    data = HeteroData()
    data["Application"].x = torch.randn(2, 23)
    data["Application"].y_rm = torch.tensor([[0.1] * 5, [0.2] * 5])

    service._node_model.return_value = {"Application": torch.tensor([[0.8] * 5, [0.9] * 5])}

    result_rm = service.predict_from_data(data, mode="rm")
    assert result_rm.node_scores["app1"].source == "RM"
    assert result_rm.node_scores["app1"].composite_score == pytest.approx(0.1)

    result_gnn = service.predict_from_data(data, mode="gnn")
    assert result_gnn.node_scores["app1"].source == "GNN"
    assert result_gnn.node_scores["app1"].composite_score == pytest.approx(0.8)
