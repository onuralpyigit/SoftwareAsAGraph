"""
Parameter uncertainty for pre-deployment runs.

Before deployment every runtime parameter -- the operating point, the service
distribution, how long an input stays fresh, how bursty traffic is -- is a
guess. Rather than report a label computed at one guessed point, a
pre-deployment study draws the parameters from declared ranges, recomputes the
label under each draw, and reports how stable the resulting ranking is.

`sample_settings` turns `PARAMETER_RANGES` (or a caller's own) into
`MessageFlowSimulator` keyword arguments; `rank_stability` summarises the
labels obtained across draws.
"""

from __future__ import annotations

import math
import random
import statistics
from typing import Any, Dict, List, Mapping, Sequence

#: Default ranges. Numeric ranges are (low, high) and drawn uniformly; tuples of
#: strings are categorical choices. Every key is a `MessageFlowSimulator`
#: keyword argument.
PARAMETER_RANGES: Dict[str, Any] = {
    "target_utilization": (0.45, 0.85),
    "service_distribution": ("exponential", "uniform"),
    "staleness_periods": (2.0, 6.0),
    "burst_ratio": (2.0, 6.0),
}


def sample_settings(rng: random.Random, ranges: Mapping[str, Any] = PARAMETER_RANGES) -> Dict[str, Any]:
    """One draw from *ranges*, in sorted key order so a seed fixes the draw."""
    out: Dict[str, Any] = {}
    for key in sorted(ranges):
        spec = ranges[key]
        if spec and all(isinstance(v, str) for v in spec):
            out[key] = spec[rng.randrange(len(spec))]
        else:
            lo, hi = spec
            out[key] = lo + (hi - lo) * rng.random()
    return out


def rank_stability(labels: Sequence[Mapping[str, float]], k_frac: float = 0.2) -> Dict[str, Any]:
    """Spread of per-node labels and agreement of rankings across draws.

    Returns per-node mean and std, the worst pairwise Spearman rho between
    draws, and the worst pairwise top-K Jaccard. Worst rather than mean, for the
    same reason as `FaultInjector._compute_label_stability`: the ceiling is set
    by the weakest agreement.
    """
    from scipy.stats import spearmanr

    nodes = sorted(set().union(*labels)) if labels else []
    out: Dict[str, Any] = {"n_draws": len(labels), "n_nodes": len(nodes)}
    if not nodes:
        return out
    table = [[draw.get(n, 0.0) for n in nodes] for draw in labels]
    out["mean"] = {n: statistics.fmean(col) for n, col in zip(nodes, zip(*table))}
    out["std"] = {n: (statistics.stdev(col) if len(col) > 1 else 0.0)
                  for n, col in zip(nodes, zip(*table))}
    if len(labels) < 2:
        out["worst_spearman"] = None
        out["worst_topk_jaccard"] = None
        return out
    k = max(1, int(round(len(nodes) * k_frac)))

    def top(row: List[float]) -> set:
        return set(sorted(range(len(row)), key=lambda i: (-row[i], i))[:k])

    rhos, jacc = [], []
    for i in range(len(table)):
        for j in range(i + 1, len(table)):
            rho = spearmanr(table[i], table[j]).correlation
            if not math.isnan(rho):
                rhos.append(float(rho))
            a, b = top(table[i]), top(table[j])
            jacc.append(len(a & b) / len(a | b))
    out["worst_spearman"] = min(rhos) if rhos else None
    out["worst_topk_jaccard"] = min(jacc)
    return out
