"""
Held-out verification of candidate placements, and the recommendation rule.

The optimizer works on a first-order dependency proxy; this module scores every
placement on a different oracle, FailureSimulator with replica-aware host
semantics, so a reliability claim is never graded by the model that produced
it. FaultInjector is the Predict-stage labeler and is deliberately not used.
"""

from __future__ import annotations

import copy
import math
import statistics
from typing import Any, Dict, List, Optional, Sequence, Tuple

from scipy.stats import spearmanr

from saag.infrastructure.memory_repo import MemoryRepository
from saag.simulation.service import SimulationService

from .instance import Placement
from .models import Candidate, HeldOutScore

OPTIMIZER_KINDS = ("front", "tie", "max", "heuristic")


def placement_key(pl: Placement) -> Tuple:
    return tuple((c, tuple(hs)) for c, hs in sorted(pl.items()))


def apply_placement(topology: Dict[str, Any], pl: Placement) -> Dict[str, Any]:
    """Copy of *topology* with RUNS_ON of the placed components replaced, primary first.

    Derived DEPENDS_ON edges are dropped: they depend on placement and must be
    re-derived from the new RUNS_ON.
    """
    out = copy.deepcopy(topology)
    rel = out.setdefault("relationships", {})
    old = rel.get("runs_on", [])
    extras: Dict[str, Dict[str, Any]] = {}
    for e in old:
        extras.setdefault(e["from"], {k: v for k, v in e.items() if k not in ("from", "to")})
    kept = [e for e in old if e["from"] not in pl]
    rel["runs_on"] = kept + [{"from": c, "to": h, **extras.get(c, {})} for c in sorted(pl) for h in pl[c]]
    rel.pop("depends_on", None)
    return out


def score_placement(topology: Dict[str, Any], pl: Placement, events: Dict[str, List[str]],
                    p: Dict[str, float], seeds: Sequence[int], network_gating: bool) -> HeldOutScore:
    """Expected and worst-case composite impact of *pl* over *events*, averaged over *seeds*."""
    repo = MemoryRepository()
    repo.save_graph(apply_placement(topology, pl), clear=True)
    svc = SimulationService(repo, qos_weighting=True, network_gating=network_gating, replica_aware=True)
    runs = [{e: r.impact.composite_impact for e, r in svc.run_failure_events(events, seed=s).items()}
            for s in seeds]
    per_event = {e: statistics.fmean(run[e] for run in runs) for e in sorted(events)}
    per_seed = [sum(p[e] * run[e] for e in events) for run in runs]
    return HeldOutScore(
        expected_impact=sum(p[e] * v for e, v in per_event.items()),
        worst_impact=max(per_event.values(), default=0.0),
        sigma_seed=statistics.stdev(per_seed) if len(per_seed) > 1 else 0.0,
        per_event=per_event,
        per_seed=per_seed,
    )


def score_placements(topology: Dict[str, Any], placements: Dict[str, Placement], events: Dict[str, List[str]],
                     p: Dict[str, float], seeds: Sequence[int], network_gating: bool) -> Dict[str, HeldOutScore]:
    """Score each named placement once; identical placements share a score."""
    cache: Dict[Tuple, HeldOutScore] = {}
    out = {}
    for name in placements:
        key = placement_key(placements[name])
        if key not in cache:
            cache[key] = score_placement(topology, placements[name], events, p, seeds, network_gating)
        out[name] = cache[key]
    return out


def mark_pareto(candidates: List[Candidate]) -> None:
    """Flag optimizer candidates not dominated on (traffic, blast_exp)."""
    pool = [c for c in candidates if c.kind in OPTIMIZER_KINDS and c.feasible]
    for c in pool:
        c.pareto = not any(
            o is not c and o.traffic <= c.traffic + 1e-9 and o.blast_exp <= c.blast_exp + 1e-9
            and (o.traffic < c.traffic - 1e-9 or o.blast_exp < c.blast_exp - 1e-9)
            for o in pool)


def select_recommendation(candidates: List[Candidate], kappa: float = 1.0) -> Dict[str, Any]:
    """Pick the placement to recommend and decide whether a reliability gain is claimed.

    1. B* is the best feasible baseline on held-out expected impact.
    2. A candidate's noise bar is max(kappa * sqrt(sigma_c^2 + sigma_B*^2), tie spread),
       where the tie spread is the held-out range across proxy-tied optima: the
       solver's pick among them is arbitrary, so differences inside it are noise.
    3. Verdict vs B*: better / tied / worse beyond that bar.
    4. Recommend the least-traffic feasible optimizer candidate that is not worse;
       if every one is worse, recommend B* itself.
    5. Claim a reliability gain only if the recommendation is better than B* and
       also beats indeg_spread beyond the bar.
    """
    scored = [c for c in candidates if c.held_out is not None]
    baselines = [c for c in scored if c.kind == "baseline"]
    optimizer = [c for c in scored if c.kind in OPTIMIZER_KINDS]
    feasible_b = [c for c in baselines if c.feasible]
    caveats = []
    if not feasible_b and baselines:
        caveats.append("no baseline satisfies every hard constraint, so there is nothing to compare against")
    # Only feasible placements are references or recommendations: a placement that
    # breaks the spec is never a plan, however well the simulator scores it.
    best = min(feasible_b, key=lambda c: (c.held_out.expected_impact, c.name)) if feasible_b else None

    b_star = min((c.blast_exp for c in optimizer if c.feasible), default=None)
    tied = [c for c in optimizer if c.kind in ("front", "tie") and b_star is not None
            and c.blast_exp <= b_star + 1e-6 * max(1.0, abs(b_star))]
    tied_i = [c.held_out.expected_impact for c in tied]
    floor = (max(tied_i) - min(tied_i)) if len(tied_i) > 1 else 0.0
    tie_spread = {"n": len(tied_i), "min": min(tied_i, default=None), "max": max(tied_i, default=None)}

    def bar(a: Candidate, b: Candidate) -> float:
        return max(kappa * math.sqrt(a.held_out.sigma_seed ** 2 + b.held_out.sigma_seed ** 2), floor)

    if best is not None:
        for c in optimizer:
            c.noise_bar = bar(c, best)
            c.margin = best.held_out.expected_impact - c.held_out.expected_impact
            c.verdict = "better" if c.margin > c.noise_bar else "worse" if c.margin < -c.noise_bar else "tied"

    admissible = [c for c in optimizer if c.feasible and c.verdict != "worse"]
    if admissible:
        rec = min(admissible, key=lambda c: (c.traffic, c.held_out.expected_impact,
                                             max(c.load["max_cpu_util"], c.load["max_mem_util"]), c.name))
    else:
        rec = best

    claim, text = False, ""
    indeg = next((c for c in baselines if c.name == "indeg_spread"), None)
    if rec is None:
        text = "No feasible placement was found, so nothing is recommended; see the diagnosis."
    elif rec.kind == "baseline":
        text = (f"Every optimizer candidate is worse than {rec.name} on the held-out simulator; "
                f"recommending the baseline {rec.name}.") if optimizer else f"Recommending the baseline {rec.name}."
    elif best is None:
        text = f"{rec.name} is the least-traffic feasible candidate; with no feasible baseline, no reliability claim is made."
    else:
        beats_indeg = indeg is not None and (
            indeg.held_out.expected_impact - rec.held_out.expected_impact > bar(rec, indeg))
        if rec.verdict == "better" and beats_indeg:
            claim = True
            text = (f"{rec.name} lowers held-out expected impact below {best.name} (the best baseline) by "
                    f"{rec.margin:.3g} > noise bar {rec.noise_bar:.3g}, and below indeg_spread by "
                    f"{indeg.held_out.expected_impact - rec.held_out.expected_impact:.3g}.")
        else:
            gain = ""
            if best.traffic > 0:
                gain = f" ({100 * (rec.traffic / best.traffic - 1):+.0f}% cross-host traffic vs {best.name})"
            text = (f"No held-out reliability gain over {best.name} beyond noise bar {rec.noise_bar:.3g}"
                    + (" or over indeg_spread" if indeg is not None and best is not indeg else "")
                    + f". {rec.name} is recommended as the least-traffic placement the simulator does not "
                      f"rank worse{gain}.")
        if best.load["hosts_used"] < rec.load["hosts_used"] or best.load["copies_entropy"] < rec.load["copies_entropy"] - 0.05:
            caveats.append(f"{best.name} concentrates load ({best.load['hosts_used']:.0f} hosts used, copies entropy "
                           f"{best.load['copies_entropy']:.2f} vs {rec.load['hosts_used']:.0f} and "
                           f"{rec.load['copies_entropy']:.2f}); part of its held-out score comes from exposing fewer hosts")
    return {
        "recommended": rec.name if rec else None,
        "best_baseline": best.name if best else None,
        "noise_floor": floor,
        "tie_spread": tie_spread,
        "baseline_spread": {c.name: c.held_out.expected_impact for c in baselines},
        "reliability_claim": claim,
        "rationale": " ".join([text] + [f"Note: {c}." for c in caveats]),
    }


def proxy_oracle_rho(candidates: List[Candidate]) -> Optional[float]:
    """Spearman correlation of the proxy (blast_exp) with held-out expected impact, unique placements only."""
    seen, xs, ys = set(), [], []
    for c in candidates:
        key = placement_key(c.placement)
        if c.held_out is None or key in seen:
            continue
        seen.add(key)
        xs.append(c.blast_exp)
        ys.append(c.held_out.expected_impact)
    if len(xs) < 3 or len(set(xs)) < 2 or len(set(ys)) < 2:
        return None
    rho = spearmanr(xs, ys).statistic
    return float(rho) if math.isfinite(rho) else None
