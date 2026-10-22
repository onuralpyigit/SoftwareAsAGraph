from types import SimpleNamespace

import pytest
from saag import Pipeline
from saag.infrastructure.memory_repo import MemoryRepository

def test_pipeline_fail_fast_no_checkpoint_no_simulate(monkeypatch):
    """
    Verifies that calling predict() in a pipeline without a GNN checkpoint
    and without simulate() fails fast with RuntimeError.
    """
    repo = MemoryRepository()
    # Seed with some basic data
    repo.save_graph({
        "applications": [{"id": "AppA", "name": "App A"}],
        "relationships": {}
    })
    
    from saag.prediction.service import PredictionService
    monkeypatch.setattr(PredictionService, "_has_checkpoint", staticmethod(lambda directory: False))

    pipeline = Pipeline(repo=repo)
    pipeline.analyze().predict()  # predict requested, no simulate, no checkpoint
    
    with pytest.raises(RuntimeError) as exc_info:
        pipeline.run()
        
    assert "GNN Prediction requested but no trained GNN checkpoint was found" in str(exc_info.value)

def test_pipeline_reordered_simulate_before_predict(monkeypatch):
    """
    Verifies that Pipeline.run() executes Simulate before Predict.
    """
    repo = MemoryRepository()
    repo.save_graph({
        "applications": [{"id": "AppA", "name": "App A"}],
        "relationships": {}
    })
    
    execution_order = []
    
    # Monkeypatch client methods to track execution order
    monkeypatch.setattr(pipeline_client := Pipeline(repo=repo).client, "import_topology", lambda *args, **kwargs: execution_order.append("import"))
    monkeypatch.setattr(pipeline_client, "analyze", lambda *args, **kwargs: type('MockRes', (), {'raw': type('MockRaw', (), {'structural': type('MockStruct', (), {'graph': None})()})()})())
    
    # We mock predict to just track execution
    monkeypatch.setattr(pipeline_client, "predict", lambda *args, **kwargs: execution_order.append("predict"))
    # We mock simulate to track execution
    monkeypatch.setattr(pipeline_client, "simulate", lambda *args, **kwargs: execution_order.append("simulate"))
    
    pipeline = Pipeline(repo=repo)
    # Inject our mocked client
    pipeline.client = pipeline_client
    
    # Set analyze, predict, and simulate
    pipeline.analyze().predict().simulate()
    
    # Mock checkpoint check to succeed so we don't trigger the fail-fast guard
    monkeypatch.setattr("pathlib.Path.exists", lambda self: True)
    
    pipeline.run()
    
    # Ensure simulate ran before predict
    assert "simulate" in execution_order
    assert "predict" in execution_order
    assert execution_order.index("simulate") < execution_order.index("predict")

def test_pipeline_predict_kwargs_reach_client(monkeypatch):
    """RM weighting options passed to Pipeline.predict() must reach
    Client.predict() — they previously only reached Pipeline.analyze(),
    whose Client.analyze() explicitly discards unknown kwargs, so
    `saag --all --use-ahp` was a silent no-op."""
    repo = MemoryRepository()
    repo.save_graph({"applications": [{"id": "AppA", "name": "App A"}], "relationships": {}})

    pipeline = Pipeline(repo=repo)
    captured = {}

    monkeypatch.setattr(pipeline.client, "analyze", lambda *a, **kw: object())
    monkeypatch.setattr(
        pipeline.client, "predict",
        lambda *a, **kw: captured.update(kw) or object(),
    )
    from saag.prediction.service import PredictionService
    monkeypatch.setattr(PredictionService, "_has_checkpoint", staticmethod(lambda d: True))

    pipeline.analyze("app").predict(mode="rm", use_ahp=True, equal_weights=False)
    pipeline.run()

    assert captured.get("use_ahp") is True
    assert captured.get("equal_weights") is False
    assert captured.get("mode") == "rm"


def test_pipeline_simulate_layer_independent_of_analyze_layer(monkeypatch):
    """`.analyze("app").simulate("infra")` must analyze "app" and simulate
    "infra" — the two calls used to share one `_layer` field, so the later
    call silently overwrote the earlier one's layer."""
    repo = MemoryRepository()
    repo.save_graph({"applications": [{"id": "AppA", "name": "App A"}], "relationships": {}})

    pipeline = Pipeline(repo=repo)
    seen_layers = {}

    monkeypatch.setattr(
        pipeline.client, "analyze",
        lambda layer, **kw: seen_layers.setdefault("analyze", layer) or object(),
    )
    monkeypatch.setattr(
        pipeline.client, "simulate",
        lambda layer, **kw: seen_layers.setdefault("simulate", layer) or object(),
    )

    pipeline.analyze("app").simulate("infra")
    pipeline.run()

    assert seen_layers == {"analyze": "app", "simulate": "infra"}


def test_pipeline_simulate_defaults_to_analyze_layer(monkeypatch):
    """simulate() called with no layer argument must reuse analyze()'s layer,
    not the class-level default — preserving the pre-fix chaining behaviour
    for the common case."""
    repo = MemoryRepository()
    repo.save_graph({"applications": [{"id": "AppA", "name": "App A"}], "relationships": {}})

    pipeline = Pipeline(repo=repo)
    seen_layers = {}

    monkeypatch.setattr(
        pipeline.client, "analyze",
        lambda layer, **kw: seen_layers.setdefault("analyze", layer) or object(),
    )
    monkeypatch.setattr(
        pipeline.client, "simulate",
        lambda layer, **kw: seen_layers.setdefault("simulate", layer) or object(),
    )

    pipeline.analyze("infra").simulate()
    pipeline.run()

    assert seen_layers == {"analyze": "infra", "simulate": "infra"}


def test_gnn_independence_invariant_assertion():
    """
    Verifies that predict_from_data raises RuntimeError if target label dimensions
    leak into features, violating the independence invariant. A plain `assert`
    would silently vanish under `python -O`, so this is an explicit raise.
    """
    from saag.prediction.gnn_service import GNNService
    from saag.prediction.models.core import NUM_LABEL_DIMS
    from torch_geometric.data import HeteroData
    import torch

    # Initialize GNNService
    service = GNNService(hidden_channels=16, predict_edges=False)

    # Mock node model metadata
    class MockModel:
        node_types = ["Application"]
        edge_types = []
        def eval(self): pass

    service._node_model = MockModel()

    data = HeteroData()
    # Application node features sized exactly to NUM_LABEL_DIMS: the guard flags a
    # feature width matching the label width as a potential leak (App normally
    # expects 23 dims, well above NUM_LABEL_DIMS).
    data["Application"].x = torch.zeros((10, NUM_LABEL_DIMS))
    data["Application"].num_nodes = 10
    
    service._conversion_result = type('MockConv', (), {'node_id_map': {"Application": [f"App{i}" for i in range(10)]}})()
    
    with pytest.raises(RuntimeError) as exc_info:
        service.predict_from_data(data)

    assert "Violation of Independence Guarantee" in str(exc_info.value)


def test_pipeline_triage_requires_predict():
    """triage() without predict() must fail fast, mirroring predict()'s own
    analyze()-required guard."""
    repo = MemoryRepository()
    repo.save_graph({"applications": [{"id": "AppA", "name": "App A"}], "relationships": {}})

    pipeline = Pipeline(repo=repo)
    pipeline.analyze().triage(k=1)

    with pytest.raises(RuntimeError) as exc_info:
        pipeline.run()

    assert "triage() requires a PredictionResult" in str(exc_info.value)


def test_pipeline_triage_runs_after_predict(monkeypatch):
    """triage() must execute after predict() and receive predict()'s result
    and its own kwargs, not predict()'s."""
    repo = MemoryRepository()
    repo.save_graph({"applications": [{"id": "AppA", "name": "App A"}], "relationships": {}})

    pipeline = Pipeline(repo=repo)
    execution_order = []
    sentinel_prediction = object()

    monkeypatch.setattr(pipeline.client, "analyze", lambda *a, **kw: object())
    monkeypatch.setattr(
        pipeline.client, "predict",
        lambda *a, **kw: execution_order.append("predict") or sentinel_prediction,
    )
    captured = {}

    def fake_triage(prediction_result, **kw):
        execution_order.append("triage")
        captured["prediction_result"] = prediction_result
        captured.update(kw)
        return object()

    monkeypatch.setattr(pipeline.client, "triage", fake_triage)
    from saag.prediction.service import PredictionService
    monkeypatch.setattr(PredictionService, "_has_checkpoint", staticmethod(lambda d: True))

    pipeline.analyze("app").predict(mode="rm").triage(k=5, node_types=["Application"])
    pipeline.run()

    assert execution_order == ["predict", "triage"]
    assert captured["prediction_result"] is sentinel_prediction
    assert captured["k"] == 5
    assert captured["node_types"] == ["Application"]


def test_pipeline_diagnose_requires_analyze():
    """diagnose() without analyze() must fail fast, mirroring predict()'s
    and triage()'s own analyze()/predict()-required guards."""
    repo = MemoryRepository()
    repo.save_graph({"applications": [{"id": "AppA", "name": "App A"}], "relationships": {}})

    pipeline = Pipeline(repo=repo)
    pipeline.diagnose(k=1)

    with pytest.raises(RuntimeError) as exc_info:
        pipeline.run()

    assert "diagnose() requires an AnalysisResult" in str(exc_info.value)


def test_pipeline_diagnose_runs_after_predict(monkeypatch):
    """diagnose() must execute after predict() and receive predict()'s
    result as prediction_result, plus its own k/node_types kwargs — Step 4
    (Diagnose) runs on whatever ranking Step 3 (Predict) produced."""
    repo = MemoryRepository()
    repo.save_graph({"applications": [{"id": "AppA", "name": "App A"}], "relationships": {}})

    pipeline = Pipeline(repo=repo)
    execution_order = []
    sentinel_prediction = object()
    sentinel_diagnosis = SimpleNamespace(triage=None)

    monkeypatch.setattr(pipeline.client, "analyze", lambda *a, **kw: object())
    monkeypatch.setattr(
        pipeline.client, "predict",
        lambda *a, **kw: execution_order.append("predict") or sentinel_prediction,
    )
    captured = {}

    def fake_diagnose(analysis_result, prediction_result=None, **kw):
        execution_order.append("diagnose")
        captured["prediction_result"] = prediction_result
        captured.update(kw)
        return sentinel_diagnosis

    monkeypatch.setattr(pipeline.client, "diagnose", fake_diagnose)
    from saag.prediction.service import PredictionService
    monkeypatch.setattr(PredictionService, "_has_checkpoint", staticmethod(lambda d: True))

    pipeline.analyze("app").predict(mode="rm").diagnose(k=5, node_types=["Application"])
    result = pipeline.run()

    assert execution_order == ["predict", "diagnose"]
    assert captured["prediction_result"] is sentinel_prediction
    assert captured["k"] == 5
    assert captured["node_types"] == ["Application"]
    assert result.diagnosis is sentinel_diagnosis


def test_pipeline_diagnose_standalone_cold_start(monkeypatch):
    """diagnose() must work without predict() having run at all (zero-GNN
    cold start) — Client.diagnose() receives prediction_result=None and
    scores its own RM pass directly off analyze()'s result. Also verifies
    a triage() result attached to the diagnosis surfaces on
    PipelineExecutionResult.triage for backward compatibility."""
    repo = MemoryRepository()
    repo.save_graph({"applications": [{"id": "AppA", "name": "App A"}], "relationships": {}})

    pipeline = Pipeline(repo=repo)
    sentinel_triage = SimpleNamespace(k=2)
    sentinel_diagnosis = SimpleNamespace(triage=sentinel_triage)

    monkeypatch.setattr(pipeline.client, "analyze", lambda *a, **kw: object())
    captured = {}

    def fake_diagnose(analysis_result, prediction_result=None, **kw):
        captured["prediction_result"] = prediction_result
        captured.update(kw)
        return sentinel_diagnosis

    monkeypatch.setattr(pipeline.client, "diagnose", fake_diagnose)

    pipeline.analyze("system").diagnose(k=2)
    result = pipeline.run()

    assert captured["prediction_result"] is None
    assert captured["k"] == 2
    assert result.diagnosis is sentinel_diagnosis
    assert result.triage is sentinel_triage



def test_pipeline_deploy_kwargs_reach_client_without_analyze(monkeypatch):
    """deploy() reads only the topology, so it runs without analyze() and forwards its options."""
    repo = MemoryRepository()
    repo.save_graph({"applications": [{"id": "AppA", "name": "App A"}], "relationships": {}})
    pipeline = Pipeline(repo=repo)
    captured = {}
    monkeypatch.setattr(pipeline.client, "deploy",
                        lambda spec, **kw: captured.update(spec=spec, **kw) or "plan")

    result = pipeline.deploy("spec.yaml", ties=2, solver="heuristic").run()

    assert result.deployment == "plan"
    assert captured == {"spec": "spec.yaml", "ties": 2, "solver": "heuristic"}
