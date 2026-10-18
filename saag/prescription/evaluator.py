"""
saag/prescription/evaluator.py

Runs the Analyze -> Predict -> Simulate -> Validate chain over a repository and
returns the numbers Stage 6 judges a graph by.

Depends on the four services directly rather than on the Client facade: a core
service reaching back up into the SDK facade inverts the layering the rest of
the package follows (see ARCHITECTURE.md).
"""
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from saag.core.ports.graph_repository import IGraphRepository

#: Canonical evaluation point for reported SRI and metrics (see docs/failure-simulation.md).
CANONICAL_THRESHOLD = 0.2
CANONICAL_SEED = 42


@dataclass(frozen=True)
class Evaluation:
    """A graph's resilience at one (threshold, seed) point."""
    sri: float
    metrics: Dict[str, float] = field(default_factory=dict)
    #: Per-component cascade impact I(v), keyed by component id.
    impact: Dict[str, float] = field(default_factory=dict)


def repo_from_json(graph_json: Dict[str, Any]):
    """Load a JSON topology into a throwaway in-memory sandbox.

    Derived DEPENDS_ON edges are dropped on import and recomputed here, so a
    mutation that renames a topic cannot leave stale dependency edges behind.
    """
    from saag.infrastructure.memory_repo import MemoryRepository

    repo = MemoryRepository()
    repo.save_graph(graph_json, clear=True)
    repo.derive_dependencies()
    return repo


class GraphEvaluator:
    """Evaluates a repository's resilience for one layer."""

    def __init__(self, layer: str = "system", gnn_checkpoint: Optional[str] = None,
                 network_gating: bool = False, replica_aware: bool = False):
        self.layer = layer
        self.gnn_checkpoint = gnn_checkpoint
        self.network_gating = network_gating
        self.replica_aware = replica_aware

    def impact(self, repo: IGraphRepository, *, threshold: float, seed: int) -> Dict[str, float]:
        """Per-component cascade impact I(v) at one (threshold, seed) point.

        The acceptance sweep calls this once per (edit x threshold x seed) and
        reads nothing else, so it deliberately skips prediction and validation.
        """
        from saag.simulation.service import SimulationService

        sim_results = SimulationService(
            repo, network_gating=self.network_gating, replica_aware=self.replica_aware,
        ).run_failure_simulation_exhaustive(
            layer=self.layer, propagation_threshold=threshold, seed=seed,
        )
        return {r.target_id: r.impact.composite_impact for r in sim_results}

    def evaluate(
        self,
        repo: IGraphRepository,
        *,
        threshold: float = CANONICAL_THRESHOLD,
        seed: int = CANONICAL_SEED,
    ) -> Evaluation:
        """Full evaluation: SRI, aggregate metrics and I(v) from one simulation sweep.

        The sweep is run once here and handed to validation, so the reported SRI
        and the impact map provably describe the same simulation at the same
        (threshold, seed) — validating separately would silently re-simulate at
        the canonical defaults regardless of what was asked for.
        """
        from saag.analysis.service import AnalysisService
        from saag.prediction.service import PredictionService
        from saag.simulation.service import SimulationService
        from saag.validation.service import ValidationService

        analysis_service = AnalysisService(repo)
        prediction_service = PredictionService(gnn_checkpoint_dir=self.gnn_checkpoint)
        simulation_service = SimulationService(
            repo, network_gating=self.network_gating, replica_aware=self.replica_aware)

        analysis_result = analysis_service.analyze_layer(self.layer)
        analysis_result.quality = prediction_service.predict_quality(analysis_result.structural)

        sim_results = simulation_service.run_failure_simulation_exhaustive(
            layer=self.layer, propagation_threshold=threshold, seed=seed,
        )

        validation_service = ValidationService(
            analysis_service=analysis_service,
            prediction_service=prediction_service,
            simulation_service=simulation_service,
        )
        layer_result = validation_service.validate_single_layer_from_results(
            analysis_result, sim_results, self.layer,
        )

        n = len(sim_results)
        metrics = {
            "sri": layer_result.system_health.get("SRI", 1.0),
            "avg_reachability_loss": sum(r.impact.reachability_loss for r in sim_results) / n if n else 0.0,
            "avg_fragmentation": sum(r.impact.fragmentation for r in sim_results) / n if n else 0.0,
            "avg_throughput_loss": sum(r.impact.throughput_loss for r in sim_results) / n if n else 0.0,
        }
        return Evaluation(
            sri=metrics["sri"],
            metrics=metrics,
            impact={r.target_id: r.impact.composite_impact for r in sim_results},
        )
