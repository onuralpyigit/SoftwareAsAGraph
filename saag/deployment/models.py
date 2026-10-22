"""Result types for the deployment planning stage."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


def _clean(value: Any) -> Any:
    """JSON-safe copy: non-finite floats become None."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    return value


@dataclass
class HeldOutScore:
    """A placement scored on FailureSimulator(replica_aware=True) over every failure event."""
    expected_impact: float            # sum_e p(e) * I(e), I = composite impact averaged over seeds
    worst_impact: float               # max_e I(e)
    sigma_seed: float                 # std of expected_impact across seeds
    per_event: Dict[str, float]
    per_seed: List[float]


@dataclass
class Candidate:
    """One placement: an optimizer output or a baseline, with its metrics and held-out score."""
    name: str
    kind: str                         # front | tie | max | heuristic | baseline
    placement: Dict[str, List[str]]
    traffic: float
    cross_host_share: float
    cross_domain_share: float
    latency_weighted: float
    blast_exp: float
    blast_max: float
    load: Dict[str, float]
    violations: Dict[str, int]
    pareto: bool = False
    aliases: List[str] = field(default_factory=list)
    solve: Optional[Dict[str, Any]] = None
    held_out: Optional[HeldOutScore] = None
    verdict: Optional[str] = None     # better | tied | worse, against the best baseline
    margin: Optional[float] = None    # I(best baseline) - I(this); positive is better
    noise_bar: Optional[float] = None

    @property
    def feasible(self) -> bool:
        return not any(self.violations.values())

    def to_dict(self) -> Dict[str, Any]:
        return _clean(asdict(self))


@dataclass
class Diagnosis:
    """Why no placement satisfies the hard constraints."""
    prechecks: List[str] = field(default_factory=list)
    families: Dict[str, int] = field(default_factory=dict)
    culprits: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class DeploymentResult:
    """Outcome of :meth:`DeploymentService.optimize`."""
    status: str                       # optimal | time_limit | heuristic | infeasible (proven) | no_feasible (none found)
    solver: str                       # highs | heuristic | none
    candidates: List[Candidate] = field(default_factory=list)
    recommended: Optional[str] = None
    best_baseline: Optional[str] = None
    noise_floor: float = 0.0          # held-out spread across the proxy-tied optima
    tie_spread: Dict[str, Any] = field(default_factory=dict)
    baseline_spread: Dict[str, float] = field(default_factory=dict)
    proxy_oracle_rho: Optional[float] = None
    front_flat: bool = False
    reliability_claim: bool = False
    rationale: str = ""
    oracle: Dict[str, Any] = field(default_factory=dict)
    spec_summary: Dict[str, Any] = field(default_factory=dict)
    diagnosis: Optional[Diagnosis] = None
    skipped: List[str] = field(default_factory=list)
    seconds: Dict[str, float] = field(default_factory=dict)

    @property
    def recommended_candidate(self) -> Optional[Candidate]:
        return next((c for c in self.candidates if c.name == self.recommended), None)

    def candidate(self, name: str) -> Optional[Candidate]:
        return next((c for c in self.candidates if c.name == name or name in c.aliases), None)

    def to_dict(self) -> Dict[str, Any]:
        out = {k: v for k, v in asdict(self).items() if k != "candidates"}
        out["candidates"] = [c.to_dict() for c in self.candidates]
        return _clean(out)
