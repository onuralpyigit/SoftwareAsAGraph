"""
Arrival-process models beyond the declared periodic rates.

No corpus topic declares ``workload_type``, so every publisher is strictly
periodic and no scenario ever sees a burst. These are opt-in arms; none changes
a topic's *mean* rate, so a subscriber calibrated to ``target_utilization`` is
still at that utilization on average -- bursts only change how the load arrives.

``mmpp``   two-state Markov-modulated Poisson process per publisher. In the
           burst state the rate is ``burst_ratio`` times the base; the calm
           state's rate is lowered so the long-run mean equals the declared
           rate. ``burst_fraction`` is the long-run share of time in the burst
           state and ``mean_burst_s`` the mean burst length.

`apply_zipf_rates` is a graph transform for the sensitivity arm on the
flat-rate scenarios (healthcare, iot_smart_city, financial_trading), where every
topic declares (nearly) the same frequency.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Dict, Tuple

WORKLOAD_MODELS: Tuple[str, ...] = ("declared", "mmpp")


@dataclass
class MMPP:
    burst_ratio: float = 4.0
    burst_fraction: float = 0.1
    mean_burst_s: float = 1.0
    #: publisher key -> (in_burst, state_ends_at)
    _state: Dict[str, Tuple[bool, float]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not 0.0 < self.burst_fraction < 1.0:
            raise ValueError("burst_fraction must lie in (0, 1)")
        if self.burst_ratio < 1.0:
            raise ValueError("burst_ratio must be >= 1")
        if self.burst_ratio * self.burst_fraction >= 1.0:
            raise ValueError(
                "burst_ratio * burst_fraction must be < 1, or the calm state would "
                "need a negative rate to keep the declared mean")
        if self.mean_burst_s <= 0:
            raise ValueError("mean_burst_s must be positive")

    @property
    def calm_ratio(self) -> float:
        f, b = self.burst_fraction, self.burst_ratio
        return (1.0 - f * b) / (1.0 - f)

    @property
    def mean_calm_s(self) -> float:
        return self.mean_burst_s * (1.0 - self.burst_fraction) / self.burst_fraction

    def interval(self, key: str, base_rate: float, now: float, rng: random.Random) -> float:
        """Next inter-arrival time for one publisher, from its own RNG stream."""
        in_burst, ends = self._state.get(key, (None, 0.0))
        if in_burst is None:
            in_burst = rng.random() < self.burst_fraction
            ends = now + rng.expovariate(1.0 / (self.mean_burst_s if in_burst else self.mean_calm_s))
        while now >= ends:
            in_burst = not in_burst
            ends += rng.expovariate(1.0 / (self.mean_burst_s if in_burst else self.mean_calm_s))
        self._state[key] = (in_burst, ends)
        rate = base_rate * (self.burst_ratio if in_burst else self.calm_ratio)
        return rng.expovariate(rate) if rate > 0 else 1.0


def apply_zipf_rates(graph, exponent: float = 1.0):
    """Copy of *graph* whose topic frequencies follow Zipf(exponent), same total.

    Topics are ranked by declared frequency (descending, ties by id) and
    reassigned rates proportional to ``1 / rank**exponent``, scaled so the
    system's total publication rate is unchanged.
    """
    g = graph.copy()
    topics = [(n, float(d.get("frequency") or 10.0))
              for n, d in g.nodes(data=True) if d.get("type") == "Topic"]
    if not topics:
        return g
    total = sum(f for _, f in topics)
    ranked = sorted(topics, key=lambda x: (-x[1], x[0]))
    weights = [1.0 / (i + 1) ** exponent for i in range(len(ranked))]
    scale = total / sum(weights)
    for (tid, _), w in zip(ranked, weights):
        g.nodes[tid]["frequency"] = w * scale
    return g
