"""
Input-driven publication: when an application is willing to publish.

`MessageFlowSimulator`'s historical publishers emit on a fixed schedule whatever
they receive, so a starved relay keeps feeding its consumers and no fault ever
travels more than one hop. That is why I_dyn(v) reduces in closed form to the
rate-weighted first-order expansion (Eq. 7). Under ``behavior_model="reactive"``
each application instead publishes only while its inputs are fresh, so a lost
feed propagates downstream, with a detection delay set by the staleness window.

Trigger policies
────────────────
``periodic``   emits on schedule regardless of inputs (sources; today's model).
``all_of``     every required input must be fresh (sensor fusion, control laws).
``any_of``     at least one fresh input suffices (aggregators, gateways).
``k_of_n:K``   at least K fresh inputs; with fewer it degrades, emitting a
               ``fresh / K`` share of its schedule (DEGRADED).

The policy comes from an explicit ``trigger`` node attribute when one is
declared, otherwise from the application's ``app_type`` via `ROLE_POLICIES`.
Neither is required, so the corpus needs no new fields.

Freshness
─────────
An input topic is fresh while its last *delivered* message (one that met its
deadline and lifespan) is younger than the topic's staleness window: the
longer of its declared ``deadline_ms`` and `STALENESS_PERIODS` periods of the
topic's aggregate frequency. The floor matters: corpus topics declare deadlines
shorter than their own period, and a window shorter than the inter-arrival
time stalls every reader before the first sample can arrive. Inputs start
fresh at t=0, so applications are not stalled while the system warms up.

Two kinds of subscription are never required inputs: a topic the application
publishes itself (a self-loop would latch a stall permanently and says nothing
about its dependencies), and a topic that has no publisher at all in the model
(nothing can ever arrive on it, so requiring it stalls the reader from t=0 --
a modelling gap, not a runtime behaviour).

Determinism
───────────
Partial emission under DEGRADED uses a credit accumulator, not a random draw,
so the policy consumes no randomness and cannot perturb any RNG stream.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Set, Tuple

#: Health states an application moves through as its inputs go stale.
HEALTHY, DEGRADED, STALLED = "HEALTHY", "DEGRADED", "STALLED"

TRIGGER_POLICIES: Tuple[str, ...] = ("periodic", "all_of", "any_of", "k_of_n")

#: Default policy per declared `app_type`. Sensors are sources; processors and
#: controllers fuse their inputs; gateways, services, monitors and actuators
#: forward or react to whatever arrives. A type not listed falls back to
#: `DEFAULT_POLICY`.
ROLE_POLICIES: Dict[str, str] = {
    "sensor": "periodic",
    "processor": "all_of",
    "controller": "all_of",
    "gateway": "any_of",
    "service": "any_of",
    "monitor": "any_of",
    "actuator": "any_of",
}
DEFAULT_POLICY = "any_of"

#: Staleness window, in periods of the input topic (a floor under any deadline).
STALENESS_PERIODS = 3.0

#: A stall must last this long (and one staleness window) to count as lasting.
MIN_STALL_S = 1.0


@dataclass(frozen=True)
class TriggerPolicy:
    kind: str
    k: int = 1

    @classmethod
    def parse(cls, spec: str) -> "TriggerPolicy":
        """``periodic`` | ``all_of`` | ``any_of`` | ``k_of_n:K``."""
        spec = str(spec).strip().lower()
        if spec.startswith("k_of_n"):
            _, _, k = spec.partition(":")
            try:
                k_int = int(k)
            except ValueError:
                raise ValueError(f"k_of_n needs an integer K, e.g. 'k_of_n:2'; got {spec!r}")
            if k_int < 1:
                raise ValueError(f"k_of_n needs K >= 1; got {spec!r}")
            return cls("k_of_n", k_int)
        if spec not in TRIGGER_POLICIES:
            raise ValueError(f"trigger must be one of {TRIGGER_POLICIES} (k_of_n:K); got {spec!r}")
        return cls(spec)


def policy_for(node_attrs: Mapping) -> TriggerPolicy:
    """Explicit ``trigger`` attribute, else the `app_type` role default."""
    declared = node_attrs.get("trigger")
    if declared:
        return TriggerPolicy.parse(declared)
    role = str(node_attrs.get("app_type") or node_attrs.get("role") or "").lower()
    return TriggerPolicy.parse(ROLE_POLICIES.get(role, DEFAULT_POLICY))


def staleness_s(topic_attrs: Mapping, aggregate_rate_hz: float,
                periods: float = STALENESS_PERIODS) -> float:
    """How long a topic's last delivery keeps it fresh (see module docstring)."""
    floor = periods / aggregate_rate_hz if aggregate_rate_hz > 0 else periods
    deadline = topic_attrs.get("deadline_ms")
    try:
        d = float(deadline) / 1000.0 if deadline is not None else 0.0
    except (TypeError, ValueError):
        d = 0.0
    return max(d, floor)


@dataclass
class AppBehavior:
    policy: TriggerPolicy
    #: Required input topic -> its staleness window (s).
    inputs: Dict[str, float]


@dataclass
class ReactiveBehavior:
    """Per-run state of the reactive model, shared by all SimPy processes."""

    apps: Dict[str, AppBehavior]
    last_fresh: Dict[Tuple[str, str], float] = field(default_factory=dict)
    #: Emission credit per (app, topic) under DEGRADED.
    _credit: Dict[Tuple[str, str], float] = field(default_factory=lambda: defaultdict(float))
    #: Start of each app's current STALLED episode (cleared when it recovers),
    #: so a transient stall caused by queueing jitter is not a lasting one.
    stall_start: Dict[str, float] = field(default_factory=dict)
    #: Every (start, end) STALLED episode that ended, per app.
    stall_episodes: Dict[str, List[Tuple[float, float]]] = field(
        default_factory=lambda: defaultdict(list))

    @classmethod
    def build(
        cls,
        graph,
        subscriptions: Mapping[str, List[str]],
        publications: Mapping[str, Set[str]],
        aggregate_rate: Mapping[str, float],
        staleness_periods: float = STALENESS_PERIODS,
        policy_overrides: Optional[Mapping[str, str]] = None,
    ) -> "ReactiveBehavior":
        """Resolve every publishing application's policy and required inputs.

        ``policy_overrides`` (app -> policy spec) wins over the declared or role
        policy: it is how a policy inferred from telemetry reaches the run.
        """
        published = {t for topics in publications.values() for t in topics}
        apps: Dict[str, AppBehavior] = {}
        for app in sorted(publications):
            attrs = graph.nodes.get(app, {})
            own = publications.get(app, set())
            inputs = {
                t: staleness_s(graph.nodes.get(t, {}), aggregate_rate.get(t, 0.0),
                               staleness_periods)
                for t in sorted((set(subscriptions.get(app, ())) & published) - own)
            }
            if not inputs:
                policy = TriggerPolicy("periodic")
            elif policy_overrides and app in policy_overrides:
                policy = TriggerPolicy.parse(policy_overrides[app])
            else:
                policy = policy_for(attrs)
            apps[app] = AppBehavior(policy=policy, inputs=inputs)
        return cls(apps=apps)

    # ── SimPy hooks ─────────────────────────────────────────────────────────

    def on_delivery(self, app: str, topic: str, now: float) -> None:
        self.last_fresh[(app, topic)] = now

    def fresh_count(self, app: str, now: float) -> Tuple[int, int]:
        b = self.apps[app]
        fresh = sum(
            1 for t, window in b.inputs.items()
            if now - self.last_fresh.get((app, t), 0.0) <= window
        )
        return fresh, len(b.inputs)

    def emit_share(self, app: str, now: float) -> float:
        """Share of the schedule the app is willing to emit right now, in [0, 1]."""
        b = self.apps.get(app)
        if b is None or b.policy.kind == "periodic":
            return 1.0
        fresh, n = self.fresh_count(app, now)
        kind = b.policy.kind
        if kind == "all_of":
            return 1.0 if fresh == n else 0.0
        if kind == "any_of":
            return 1.0 if fresh >= 1 else 0.0
        need = min(b.policy.k, n)
        return min(1.0, fresh / need) if need else 1.0

    def should_emit(self, app: str, topic: str, now: float) -> bool:
        """Credit-accumulator thinning of the schedule; records health transitions."""
        share = self.emit_share(app, now)
        if share <= 0.0:
            self.stall_start.setdefault(app, now)
        elif app in self.stall_start:
            self.stall_episodes[app].append((self.stall_start.pop(app), now))
        if share >= 1.0:
            return True
        key = (app, topic)
        self._credit[key] += share
        if self._credit[key] >= 1.0:
            self._credit[key] -= 1.0
            return True
        return False

    def state(self, app: str, now: float) -> str:
        share = self.emit_share(app, now)
        return HEALTHY if share >= 1.0 else (STALLED if share <= 0.0 else DEGRADED)


def hop_distances(
    source: str,
    subscriptions: Mapping[str, List[str]],
    publications: Mapping[str, Set[str]],
) -> Dict[str, int]:
    """Pub-sub hop count from *source* to every downstream application.

    One hop is publisher -> topic -> subscriber. Computed on raw PUBLISHES_TO /
    SUBSCRIBES_TO edges only.
    """
    readers: Dict[str, Set[str]] = defaultdict(set)
    for app, topics in subscriptions.items():
        for t in topics:
            readers[t].add(app)
    dist = {source: 0}
    frontier = [source]
    while frontier:
        nxt: List[str] = []
        for u in frontier:
            for t in sorted(publications.get(u, ())):
                for v in sorted(readers.get(t, ())):
                    if v not in dist:
                        dist[v] = dist[u] + 1
                        nxt.append(v)
        frontier = nxt
    return dist


def stall_summary(
    behavior: Optional[ReactiveBehavior],
    fault_node: Optional[str],
    fault_time: Optional[float],
    subscriptions: Mapping[str, List[str]],
    publications: Mapping[str, Set[str]],
    end_time: float,
) -> Tuple[List[str], Dict[str, float], int]:
    """(stalled apps, seconds from fault to stall, max hop depth) for a fault run.

    An app counts as stalled when it is still in a STALLED episode at the end of
    the run, that episode began after the fault, and it has lasted at least one
    staleness window of the app (and at least `MIN_STALL_S`). Episodes that
    ended, or began only just before the run ended -- a relay briefly starved
    by queueing jitter -- are not a lasting cascade.
    """
    if behavior is None or fault_node is None or fault_time is None:
        return [], {}, 0

    def lasting(app: str, start: float) -> bool:
        windows = behavior.apps[app].inputs.values()
        return end_time - start >= max([MIN_STALL_S, *windows])

    stalled = sorted(
        a for a, t in behavior.stall_start.items()
        if a != fault_node and t >= fault_time and lasting(a, t)
    )
    delay = {a: round(behavior.stall_start[a] - fault_time, 6) for a in stalled}
    dist = hop_distances(fault_node, subscriptions, publications)
    depth = max((dist.get(a, 0) for a in stalled), default=0)
    return stalled, delay, depth
