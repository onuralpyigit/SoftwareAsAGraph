"""
Public SDK Data Models

These models provide a stable, discoverable facade over the internal
SoftwareAsAGraph result structures.
"""
from typing import TYPE_CHECKING, List, Optional, Any
from dataclasses import dataclass

from saag.analysis.models import LayerAnalysisResult as _LayerAnalysisResult, StructuralAnalysisResult as _StructuralAnalysisResult
from saag.analysis.models import QualityAnalysisResult as _QualityAnalysisResult, DetectedProblem as _DetectedProblem
from saag.core.metrics import ComponentQuality as _ComponentQuality
from saag.validation.models import LayerValidationResult as _LayerValidationResult
from saag.usecases.models import ImportStats as _ImportStats
from saag.core.criticality import CriticalityRanking, CompatNamespace

if TYPE_CHECKING:
    # Import-time only: saag.prescription's package init pulls in the whole
    # Analyze/Predict/Simulate/Validate stack the evaluator runs.
    from saag.prescription.models import PrescribeResult
    from saag.deployment.models import DeploymentResult


def _rm_components_to_facades(components) -> List["ComponentFacade"]:
    """Map an iterable of RM ``ComponentQuality`` objects to ranked
    ``ComponentFacade``s, sorted by overall score descending.

    Shared by ``AnalysisResult``, ``PredictionResult`` (RM fallback path),
    and ``DiagnosisResult`` — all three surface the same RM dimension
    breakdown, just sourced from different stages of the pipeline.
    """
    from saag.core.criticality import CriticalityRanking

    rankings = []
    for cq in components:
        scores = {
            "reliability": cq.scores.reliability,
            "maintainability": cq.scores.maintainability,
            "fault_tolerance": cq.scores.fault_tolerance,
            "availability": cq.scores.availability,
            "overall": cq.scores.overall,
        }
        levels = {
            "reliability": cq.levels.reliability.name if hasattr(cq.levels.reliability, "name") else str(cq.levels.reliability).upper(),
            "maintainability": cq.levels.maintainability.name if hasattr(cq.levels.maintainability, "name") else str(cq.levels.maintainability).upper(),
            "fault_tolerance": cq.levels.fault_tolerance.name if hasattr(cq.levels.fault_tolerance, "name") else str(cq.levels.fault_tolerance).upper(),
            "availability": cq.levels.availability.name if hasattr(cq.levels.availability, "name") else str(cq.levels.availability).upper(),
            "overall": cq.levels.overall.name if hasattr(cq.levels.overall, "name") else str(cq.levels.overall).upper(),
        }
        name = getattr(cq.structural, "name", cq.id) if cq.structural else cq.id
        blast_radius = getattr(cq.structural, "blast_radius", 0) if cq.structural else 0
        cascade_depth = getattr(cq.structural, "cascade_depth", 0) if cq.structural else 0
        is_articulation_point = getattr(cq.structural, "is_articulation_point", False) if cq.structural else False
        rankings.append(CriticalityRanking(
            id=cq.id,
            type=cq.type,
            scores=scores,
            levels=levels,
            overall=cq.scores.overall,
            level=levels["overall"],
            provenance="rm",
            name=name,
            blast_radius=blast_radius,
            cascade_depth=cascade_depth,
            is_articulation_point=is_articulation_point
        ))
    rankings.sort(key=lambda x: x.overall, reverse=True)
    return [ComponentFacade(r) for r in rankings]


class ComponentFacade:
    """A developer-friendly wrapper around a component's quality metrics."""
    def __init__(self, inner: CriticalityRanking):
        self._inner = inner

    @property
    def id(self) -> str:
        """The component identifier."""
        return self._inner.id

    @property
    def rm_score(self) -> float:
        """The overall RM criticality score."""
        return self._inner.overall
        
    @property
    def type(self) -> str:
        """The component type (Application, Broker, etc)."""
        return self._inner.type
        
    @property
    def is_critical(self) -> bool:
        """Is this component deemed critical?"""
        return self._inner.level.lower() == "critical"

    @property
    def name(self) -> str:
        """The logical name of the component."""
        return self._inner.name or self._inner.id

    @property
    def blast_radius(self) -> int:
        """How many nodes become unreachable if this node is removed."""
        return self._inner.blast_radius

    @property
    def cascade_depth(self) -> int:
        """Length of the longest reachable propagation path from this node."""
        return self._inner.cascade_depth

    @property
    def criticality_level(self) -> str:
        """The overall risk classification level as a string."""
        return self._inner.level

    @property
    def criticality_levels(self) -> dict:
        """The breakdown of criticality levels by dimension."""
        return dict(self._inner.levels)

    @property
    def scores(self) -> dict:
        """The breakdown of quality predicted scores."""
        return dict(self._inner.scores)

    @property
    def provenance(self) -> str:
        """Which scoring pathway produced this component's scores: 'rm' or 'gnn'."""
        return self._inner.provenance

    @property
    def levels(self) -> dict:
        """Alias for criticality_levels to support backward compatibility."""
        return self.criticality_levels

    @property
    def structural(self) -> CompatNamespace:
        """Structural metrics mock/facade for backward compatibility."""
        return CompatNamespace(
            name=self.name,
            is_articulation_point=self._inner.is_articulation_point,
            blast_radius=self.blast_radius,
            cascade_depth=self.cascade_depth
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "is_critical": self.is_critical,
            "rm_score": self.rm_score,
            "criticality_level": self.criticality_level,
            "criticality_levels": self.criticality_levels,
            "scores": self.scores,
            "blast_radius": self.blast_radius,
            "cascade_depth": self.cascade_depth,
            "provenance": self.provenance
        }


class AnalysisResult:
    """Result of the deterministic Analyze stage (Step 2): structural metrics only.

    RM dimension scores, Q(v), anti-pattern problems, and explanations are
    produced by the Predict stage instead — see ``Client.predict()`` /
    ``PredictionResult``. ``quality``/``all_components``/``critical_components``
    below return empty until predict() has been run and the result merged back
    in (e.g. via ``Client.validate()``/``Client.visualize()``, which do this
    internally).
    """
    def __init__(self, inner: _LayerAnalysisResult):
        self._inner = inner

    @property
    def raw(self) -> _LayerAnalysisResult:
        """Access the underlying internal model."""
        return self._inner

    @property
    def quality(self) -> Optional[_QualityAnalysisResult]:
        """RM quality scores and criticality levels, if the Predict stage has populated them."""
        return self._inner.quality

    @property
    def critical_components(self) -> List["ComponentFacade"]:
        """Components classified as CRITICAL by the RM/Q scoring (requires predict() to have run)."""
        return [c for c in self.all_components if c.criticality_level.lower() == "critical"]

    @property
    def all_components(self) -> List["ComponentFacade"]:
        """All assessed components with their RM scores and criticality levels (requires predict() to have run)."""
        if self._inner.quality is None:
            return []
        return _rm_components_to_facades(self._inner.quality.components)

    @property
    def problems(self) -> List[_DetectedProblem]:
        """Anti-patterns and architectural smells (populated by the Predict stage — see PredictionResult.problems)."""
        return self._inner.problems or []

    def save(self, filepath: str) -> None:
        """Export the analysis result to a JSON file."""
        import json
        from pathlib import Path
        out = Path(filepath)
        out.parent.mkdir(parents=True, exist_ok=True)
        data = self._inner.to_dict()
        with out.open("w") as f:
            json.dump(data, f, indent=2, default=str)


from types import SimpleNamespace


class PredictionResult:
    """Result of the inductive Predict stage: GNN-derived criticality ranks and attention weights.

    This stage generalises beyond the closed-form RM composite by learning nonlinear
    interactions and multi-hop motifs that AHP-weighted scoring cannot encode.
    RM/Q scores (deterministic) are available on AnalysisResult, not here.
    """
    def __init__(self, inner: Any):
        if isinstance(inner, dict):
            # Parse from dictionary
            self._inner = SimpleNamespace(
                prediction_mode=inner.get("prediction_mode", "gnn_only"),
                node_scores={
                    k: SimpleNamespace(
                        component=v.get("component", k),
                        composite_score=v.get("composite_score", 0.0),
                        reliability_score=v.get("reliability_score", 0.0),
                        maintainability_score=v.get("maintainability_score", 0.0),
                        criticality_level=v.get("criticality_level", "MINIMAL"),
                    )
                    for k, v in inner.get("node_scores", {}).items()
                },
                edges=[
                    SimpleNamespace(
                        source=e.get("source"),
                        target=e.get("target"),
                        dependency_type=e.get("edge_type", "DEPENDS_ON"),
                        level=SimpleNamespace(value=e.get("criticality_level", "minimal")),
                        scores=SimpleNamespace(
                            reliability=e.get("reliability_score", 0.0),
                            maintainability=e.get("maintainability_score", 0.0),
                            overall=e.get("composite_score", 0.0),
                        )
                    )
                    for e in inner.get("edge_scores", [])
                ],
                _structural_cache=inner.get("_structural_cache", {})
            )
        else:
            self._inner = inner

    @property
    def critical_components(self) -> List[ComponentFacade]:
        """Components identified as CRITICAL by the prediction model."""
        return [c for c in self.all_components if c.criticality_level.lower() == "critical"]

    @property
    def all_components(self) -> List[ComponentFacade]:
        """All components ranked by criticality."""
        from saag.core.criticality import CriticalityRanking
        
        # Helper to map scores to level strings
        def _score_to_level_str(val: float) -> str:
            if val >= 0.75: return "critical"
            if val >= 0.55: return "high"
            if val >= 0.35: return "medium"
            return "low" if val >= 0.15 else "minimal"

        if hasattr(self._inner, "node_scores"):
            # GNN path (GNNAnalysisResult or SimpleNamespace) — discriminate on
            # node_scores rather than prediction_mode, since QualityAnalysisResult
            # (RM path) now also carries a prediction_mode field.
            source_dict = getattr(self._inner, "node_scores", {})
            provenance = getattr(self._inner, "prediction_mode", "gnn_only")
            if provenance == "gnn_only":
                provenance = "gnn"
            elif provenance == "rm_only":
                provenance = "rm"
                
            struct_cache = getattr(self._inner, "_structural_cache", {})
            rankings = []
            for score in source_dict.values():
                # fault_tolerance/availability are Reliability sub-characteristics
                # scored on the analysis side, not GNN prediction targets.
                scores = {
                    "reliability": score.reliability_score,
                    "maintainability": score.maintainability_score,
                    "overall": score.composite_score,
                }
                levels = {
                    "reliability": _score_to_level_str(score.reliability_score).upper(),
                    "maintainability": _score_to_level_str(score.maintainability_score).upper(),
                    "overall": score.criticality_level.upper(),
                }
                s_dict = struct_cache.get(score.component, {}) if struct_cache else {}
                rankings.append(CriticalityRanking(
                    id=score.component,
                    type=s_dict.get("type", "Application"),
                    scores=scores,
                    levels=levels,
                    overall=score.composite_score,
                    level=levels["overall"],
                    provenance=provenance,
                    name=s_dict.get("name", score.component),
                    blast_radius=s_dict.get("blast_radius", 0),
                    cascade_depth=s_dict.get("cascade_depth", 0),
                    is_articulation_point=s_dict.get("is_articulation_point", False),
                ))
            # Sort by overall score descending
            rankings.sort(key=lambda x: x.overall, reverse=True)
            return [ComponentFacade(r) for r in rankings]
        else:
            # RM path (QualityAnalysisResult)
            return _rm_components_to_facades(getattr(self._inner, "components", []))

    @property
    def components(self) -> List[ComponentFacade]:
        """Alias for all_components to support backward compatibility."""
        return self.all_components

    @property
    def edges(self) -> List[Any]:
        """All prediction edges."""
        if hasattr(self._inner, "edges"):
            return list(self._inner.edges)
        return []

    @property
    def raw(self) -> Any:
        """Access the underlying result."""
        return self._inner

    @property
    def problems(self) -> List[Any]:
        """Anti-patterns and architectural smells detected from the RM scores."""
        return list(getattr(self._inner, "problems", []) or [])

    @property
    def explanation(self) -> Any:
        """Human-readable narrative explanation of the system's quality/criticality."""
        return getattr(self._inner, "explanation", None)

    @property
    def prediction_mode(self) -> str:
        """Which scoring path produced this result: 'rm', 'gnn_only' or 'rm_only'."""
        return getattr(self._inner, "prediction_mode", "rm")

    def save(self, filepath: str) -> None:
        """Export the prediction result to a JSON file."""
        import json
        from pathlib import Path
        out = Path(filepath)
        out.parent.mkdir(parents=True, exist_ok=True)
        if hasattr(self._inner, "to_dict"):
            data = self._inner.to_dict()
        else:
            data = getattr(self._inner, "__dict__", self._inner)
        with out.open("w") as f:
            json.dump(data, f, indent=2, default=str)


class DiagnosisResult:
    """Result of the Diagnose stage (Step 4, Pathway A): deterministic
    ISO-RM root-cause attribution — dimension scores, 5-level classification,
    anti-pattern detection, and a human-readable explanation. Optionally
    carries the Triage Bridge shortlist joining Pathway B's ranking to this
    diagnosis (see ``Client.diagnose()``).
    """
    def __init__(self, inner: Any, triage: Optional[Any] = None):
        self._inner = inner
        self._triage = triage

    @property
    def raw(self) -> Any:
        """Access the underlying RM QualityAnalysisResult."""
        return self._inner

    @property
    def components(self) -> List[ComponentFacade]:
        """All components with their RM dimension scores and criticality levels."""
        return _rm_components_to_facades(getattr(self._inner, "components", []))

    @property
    def problems(self) -> List[Any]:
        """Anti-patterns and architectural smells detected from the RM scores."""
        return list(getattr(self._inner, "problems", None) or [])

    @property
    def problem_summary(self) -> Any:
        """Aggregate counts/severity breakdown of detected problems."""
        return getattr(self._inner, "problem_summary", None)

    @property
    def explanation(self) -> Any:
        """Human-readable narrative explanation of the system's quality/criticality."""
        return getattr(self._inner, "explanation", None)

    @property
    def triage(self) -> Optional[Any]:
        """Triage Bridge shortlist (Top-K joined to this diagnosis), if requested."""
        return self._triage

    def save(self, filepath: str) -> None:
        """Export the diagnosis result to a JSON file."""
        import json
        from pathlib import Path
        out = Path(filepath)
        out.parent.mkdir(parents=True, exist_ok=True)
        data = self._inner.to_dict() if hasattr(self._inner, "to_dict") else getattr(self._inner, "__dict__", self._inner)
        if self._triage is not None:
            data = dict(data)
            data["triage"] = self._triage.to_dict() if hasattr(self._triage, "to_dict") else self._triage
        with out.open("w") as f:
            json.dump(data, f, indent=2, default=str)


class ValidationResult:
    """Result of comparing predictions against simulated ground truth."""
    def __init__(self, inner: _LayerValidationResult):
        self._inner = inner

    @property
    def spearman_rho(self) -> float:
        """The primary structural correlation (Spearman Rank)."""
        return self._inner.spearman

    @property
    def f1_score(self) -> float:
        """The primary classification F1 score."""
        return self._inner.f1_score
        
    @property
    def raw(self) -> _LayerValidationResult:
        """Access the underlying internal model."""
        return self._inner

    def save(self, filepath: str) -> None:
        """Export the validation result to a JSON file."""
        import json
        from pathlib import Path
        out = Path(filepath)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w") as f:
            json.dump(self._inner.to_dict(), f, indent=2, default=str)


class ValidationPipelineFacade:
    """Result of comparing predictions against simulated ground truth across multiple layers."""
    def __init__(self, inner):
        self._inner = inner

    @property
    def layers(self) -> dict:
        return {k: ValidationResult(v) for k, v in self._inner.layers.items()}
        
    @property
    def raw(self):
        return self._inner
        
    def to_dict(self) -> dict:
        return self._inner.to_dict()

    def save(self, filepath: str) -> None:
        """Export the validation result to a JSON file."""
        import json
        from pathlib import Path
        out = Path(filepath)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w") as f:
            json.dump(self.to_dict(), f, indent=2, default=str)


@dataclass
class PipelineExecutionResult:
    """Aggregate result from running the full Pipeline sequentially.

    Stage mapping:
      analysis     — deterministic Analyze stage (Step 2: structural metrics only)
      prediction   — Predict stage (Step 3, Pathway B: GNN ranking when available, RM fallback); optional
      diagnosis    — Diagnose stage (Step 4, Pathway A: RM scores + anti-patterns + explanation); optional
      triage       — Triage bridge: Pathway B's Top-K joined to Pathway A's RM diagnosis; mirrors diagnosis.triage; optional
      simulation   — Simulate stage (Step 5: counterfactual cascade ground truth)
      validation   — Validate stage (Step 6: Predict/Diagnose vs Simulate ground truth)
      prescription — Prescribe stage (Step 7 optimization); optional
      deployment   — pre-deployment planning (application-to-node mapping); optional
    """
    analysis: Optional[AnalysisResult] = None
    prediction: Optional[PredictionResult] = None
    diagnosis: Optional[DiagnosisResult] = None
    triage: Optional[Any] = None
    simulation: Optional[Any] = None
    validation: Optional[ValidationPipelineFacade] = None
    prescription: Optional["PrescribeResult"] = None
    deployment: Optional["DeploymentResult"] = None

    def save(self, filepath: str) -> None:
        """Export the full pipeline result to a JSON file."""
        import json
        from pathlib import Path
        out = Path(filepath)
        out.parent.mkdir(parents=True, exist_ok=True)
        
        data = {}
        if self.analysis:
            # Reusing the structure defined in AnalysisResult.save()
            data["analysis"] = {
                "layer": getattr(self.analysis.raw.layer, "value", str(self.analysis.raw.layer)),
                "graph_summary": self.analysis.raw.graph_summary.to_dict() if hasattr(self.analysis.raw.graph_summary, "to_dict") else {},
                "components": {k: c.to_dict() for k, c in self.analysis.raw.components.items()},
            }
        if self.prediction:
            data["prediction"] = self.prediction.raw.to_dict()
        if self.diagnosis:
            data["diagnosis"] = self.diagnosis.raw.to_dict() if hasattr(self.diagnosis.raw, "to_dict") else self.diagnosis.raw
        if self.triage:
            data["triage"] = self.triage.to_dict() if hasattr(self.triage, "to_dict") else self.triage
        if self.simulation:
            if hasattr(self.simulation, "to_dict"):
                data["simulation"] = self.simulation.to_dict()
            else:
                data["simulation"] = self.simulation
        if self.validation:
            data["validation"] = self.validation.to_dict()
        if self.prescription:
            if hasattr(self.prescription, "to_dict"):
                data["prescription"] = self.prescription.to_dict()
            else:
                data["prescription"] = self.prescription
        if self.deployment:
            data["deployment"] = self.deployment.to_dict()
        with out.open("w") as f:
            json.dump(data, f, indent=2, default=str)
class ImportResult:
    """Result of a graph import operation."""
    def __init__(self, inner: _ImportStats):
        self._inner = inner

    @property
    def nodes_imported(self) -> int:
        """Number of nodes created or updated."""
        return self._inner.nodes_imported

    @property
    def edges_imported(self) -> int:
        """Number of relationships created or updated."""
        return self._inner.edges_imported

    @property
    def duration_ms(self) -> float:
        """Time taken for the import in milliseconds."""
        return self._inner.duration_ms

    @property
    def success(self) -> bool:
        """Whether the import was successful."""
        return self._inner.success

    @property
    def message(self) -> str:
        """Status message from the import process."""
        return self._inner.message

    @property
    def details(self) -> dict:
        """Additional raw statistics from the repository."""
        return self._inner.details

    def to_dict(self) -> dict:
        """Convert to a standardized dictionary representation."""
        return self._inner.to_dict()

    @property
    def raw(self) -> _ImportStats:
        """Access the underlying internal model."""
        return self._inner
