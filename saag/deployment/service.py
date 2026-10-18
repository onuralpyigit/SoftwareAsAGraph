"""
saag/deployment/service.py

Pre-deployment planning: map Applications and Brokers to Nodes under the
resource, replica, anti-affinity, pinning and topic-deadline constraints of a
deployment spec, trade cross-host traffic against expected blast radius, and
verify every candidate on the held-out failure simulator.

The stage plans a deployment; it never deploys anything and never writes to
the repository.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from saag.core.ports.graph_repository import IGraphRepository
from saag.infrastructure.memory_repo import MemoryRepository

from .heuristics import BASELINES, authored, baselines, hard_violations, heuristic_front, local_search
from .instance import DEPENDENCY_TYPES, Instance, Placement
from .milp import DeploymentMILP, ModelTooLarge
from .models import Candidate, DeploymentResult, Diagnosis
from .spec import DeploymentSpec, bind_spec
from .verify import mark_pareto, placement_key, proxy_oracle_rho, score_placements, select_recommendation

logger = logging.getLogger(__name__)

SOLVERS = ("auto", "milp", "heuristic")

#: Always scored: the dependency-count spread is the comparison any reliability
#: claim has to beat (counts matched cascade-aware selection in every probe).
REQUIRED_BASELINE = "indeg_spread"


class DeploymentService:
    """Plan an application-to-node deployment and verify it on a held-out oracle."""

    def __init__(self, repository: IGraphRepository):
        self.repository = repository

    def optimize(
        self,
        spec: Union[DeploymentSpec, Dict[str, Any], str, Path],
        *,
        front_points: int = 5,
        ties: int = 5,
        balance: bool = False,
        time_limit: float = 60.0,
        time_budget: float = 600.0,
        max_nonzeros: int = 2_000_000,
        solver: str = "auto",
        baseline_names: Sequence[str] = BASELINES,
        seeds: Sequence[int] = (42, 123, 456),
        kappa: float = 1.0,
        network_gating: Optional[bool] = None,
    ) -> DeploymentResult:
        """Solve, verify and recommend.

        Args:
            spec: Deployment spec (path, dict or DeploymentSpec).
            front_points: Points on the epsilon-constraint front (>= 2 for both ends).
            ties: Extra placements tied at the minimum blast_exp, used to measure how
                much the held-out score depends on the solver's arbitrary pick.
            balance: Break remaining ties toward lower peak utilisation.
            time_limit: Seconds per MILP solve.
            time_budget: Seconds for all MILP solves together.
            max_nonzeros: Model size above which ``solver="auto"`` uses the heuristic path.
            solver: ``auto`` (MILP, heuristic fallback), ``milp`` or ``heuristic``.
            baseline_names: Baselines to score; indeg_spread is always included.
            seeds: Simulator seeds for the held-out score.
            kappa: Noise-bar multiplier on the seed standard deviation.
            network_gating: Gate deliveries on the CONNECTS_TO network in the oracle;
                None turns it on exactly when the topology has NetworkDevices.
        """
        if solver not in SOLVERS:
            raise ValueError(f"solver must be one of {SOLVERS}, got {solver!r}")
        t_start = time.monotonic()
        topology = self.repository.export_json()
        bound = bind_spec(spec, topology)
        inst = Instance(topology, bound, _dependency_arcs(topology))
        gating = bool(topology.get("network_devices")) if network_gating is None else network_gating
        oracle = {"engine": "FailureSimulator", "replica_aware": True, "qos_weighting": True,
                  "network_gating": gating, "seeds": list(seeds), "events": inst.events, "p": inst.p}
        seconds: Dict[str, float] = {}

        prechecks = inst.prechecks()
        if prechecks:
            return DeploymentResult(status="infeasible", solver="none", oracle=oracle,
                                    spec_summary=bound.summary(), diagnosis=Diagnosis(prechecks=prechecks),
                                    rationale="The spec cannot be satisfied; see the diagnosis.")

        # ── Solve ────────────────────────────────────────────────────────────
        found: List[Tuple[str, str, Placement, Dict[str, Any]]] = []   # (name, kind, placement, info)
        status, used, skipped = "heuristic", "heuristic", []
        traffic_only: Optional[Placement] = None
        model = None
        if solver != "heuristic":
            try:
                model = DeploymentMILP(inst, balance=balance, max_nonzeros=max_nonzeros,
                                       time_limit=time_limit, deadline=t_start + time_budget)
            except ModelTooLarge as exc:
                if solver == "milp":
                    raise
                logger.warning("%s; using the heuristic path", exc)
        if model is not None:
            front = model.front(max(front_points, 1))
            lo_pl, lo_info = front[0][1], front[0][2]
            if lo_pl is None and lo_info.get("status") == "infeasible":
                diag = model.diagnose()
                return DeploymentResult(status="infeasible", solver="highs", oracle=oracle,
                                        spec_summary=bound.summary(),
                                        diagnosis=Diagnosis(families=diag["families"], culprits=diag["culprits"]),
                                        rationale="No placement satisfies the hard constraints; see the diagnosis.")
            if lo_pl is None:
                if solver == "milp":
                    raise RuntimeError(f"MILP found no placement: {lo_info.get('status')}")
                logger.warning("MILP found no placement (%s); using the heuristic path", lo_info.get("status"))
                model = None
            else:
                used = "highs"
                found += [(n, "front", pl, info) for n, pl, info in front if pl is not None]
                skipped += [n for n, pl, info in front if pl is None]
                if ties > 0:
                    found += [(n, "tie", pl, info) for n, pl, info in
                              model.ties(lo_pl, lo_info["blast_exp"], ties, [f[2] for f in found])]
                pl, info = model.lexi(model.objectives("blast_max", "blast_exp", "traffic"))
                if pl is not None:
                    found.append(("max_blast", "max", pl, info))
                else:
                    skipped.append("max_blast")
                traffic_only = model.traffic_only
                if traffic_only is None:
                    traffic_only, _ = model.lexi([model.traffic])
                skipped += model.skipped
                solved = [f[3].get("status") for f in found]
                status = "time_limit" if "time_limit" in solved else "optimal"
        seconds["solve"] = round(time.monotonic() - t_start, 3)

        base = baselines(inst, seed=seeds[0] if seeds else 0,
                         names=sorted(set(baseline_names) | {REQUIRED_BASELINE}))
        if model is None:
            deadline = t_start + time_budget
            starts = {k: v for k, v in base.items()}
            found = [(n, "heuristic", pl, info) for n, pl, info in
                     heuristic_front(inst, starts, max(front_points, 1), deadline=deadline)]
            start = min(base, key=lambda k: (hard_violations(inst, base[k]), k))
            traffic_only = local_search(inst, base[start],
                                        lambda pl: (hard_violations(inst, pl), inst.traffic(pl)["traffic"]),
                                        deadline=deadline)
        if traffic_only is not None:
            base["traffic_only"] = traffic_only
        own = authored(inst)
        if own is not None:
            base["authored"] = own

        # ── Candidates (duplicates folded into aliases) ──────────────────────
        candidates: List[Candidate] = []
        by_key: Dict[Tuple, Candidate] = {}
        for name, kind, pl, info in found:
            key = placement_key(pl)
            if key in by_key:
                by_key[key].aliases.append(name)
                continue
            by_key[key] = _candidate(inst, name, kind, pl, info)
            candidates.append(by_key[key])
        for name in sorted(base):
            candidates.append(_candidate(inst, name, "baseline", base[name], None))
        mark_pareto(candidates)

        # ── Held-out verification ────────────────────────────────────────────
        t_verify = time.monotonic()
        scores = score_placements(topology, {c.name: c.placement for c in candidates},
                                  inst.events, inst.p, seeds, gating)
        for c in candidates:
            c.held_out = scores[c.name]
        seconds["verify"] = round(time.monotonic() - t_verify, 3)
        pick = select_recommendation(candidates, kappa=kappa)
        seconds["total"] = round(time.monotonic() - t_start, 3)

        flat = False
        fronts = [c for c in candidates if c.kind in ("front", "heuristic")]
        if fronts:
            flat = max(c.blast_exp for c in fronts) - min(c.blast_exp for c in fronts) < 1e-9
        return DeploymentResult(
            status=status, solver=used, candidates=candidates, front_flat=flat,
            proxy_oracle_rho=proxy_oracle_rho(candidates), oracle=oracle,
            spec_summary=bound.summary(), skipped=skipped, seconds=seconds, **pick,
        )


def _dependency_arcs(topology: Dict[str, Any]) -> List[Tuple[str, str]]:
    """Placement-independent DEPENDS_ON arcs (dependent -> dependency), re-derived from the topology."""
    repo = MemoryRepository()
    repo.save_graph(topology, clear=True)
    repo.derive_dependencies()
    edges = repo.get_graph_data(dependency_types=list(DEPENDENCY_TYPES)).edges
    return sorted({(e.source_id, e.target_id) for e in edges})


def _candidate(inst: Instance, name: str, kind: str, pl: Placement, info: Optional[Dict[str, Any]]) -> Candidate:
    tr, bl = inst.traffic(pl), inst.proxy_blast(pl)
    solve = None
    if info is not None:
        solve = {k: v for k, v in info.items() if k in ("status", "stages", "deterministic", "eps", "brokers_from", "lambda", "start")}
    return Candidate(
        name=name, kind=kind, placement={c: list(hs) for c, hs in sorted(pl.items())},
        traffic=tr["traffic"], cross_host_share=tr["cross_host_share"],
        cross_domain_share=tr["cross_domain_share"], latency_weighted=tr["latency_weighted"],
        blast_exp=bl["blast_exp"], blast_max=bl["blast_max"], load=inst.load(pl),
        violations=inst.violations(pl), solve=solve,
    )
