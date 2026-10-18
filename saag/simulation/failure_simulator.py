"""
Failure Simulator

Simulates component failures and their cascading effects on the system.
Works directly on RAW structural relationships without DEPENDS_ON derivation.

Impact Metrics:
    - Reachability Loss: Percentage of broken pub-sub paths (broker-aware)
    - Infrastructure Fragmentation: Graph connectivity loss (connected components)
    - Throughput Loss: QoS-weighted reduction in message delivery capacity
    - Cascade Count: Number of components affected by cascade

Cascade Rules:
    - Physical: Node failure -> hosted components fail (RUNS_ON)
    - Logical: Broker failure -> topics become unreachable;
               Publisher failure -> subscriber starvation
    - Network: Network partition via CONNECTS_TO (or, with ``network_gating``,
               pub-sub delivery gated on host reachability across switches/routers)
    - Library: Library failure -> using applications fail (USES)
"""

from __future__ import annotations
import logging
import random
import statistics
import zlib
from dataclasses import dataclass, field
from typing import Dict, List, Set, Tuple, Any, Optional
from enum import Enum
from collections import defaultdict

from .graph import SimulationGraph, NETWORK_TYPES
from saag.core.network import evaluate, link_key, topic_load_mbps
from .models import ComponentState, FailureMode, CascadeRule, FailureScenario, ImpactMetrics, CascadeEvent, FailureResult, MonteCarloResult, RuntimeTelemetryProfile
from saag.core.models import TOPIC_CRITICALITY_ORD, MAX_TOPIC_CRITICALITY_ORD

#: Split of the fragmentation term between "how many islands" and "how much QoS
#: mass got stranded". Mirrors the 0.70/0.30 blend the IA(v) post-pass already used.
FRAGMENTATION_STRUCTURAL_COEFF: float = 0.70
FRAGMENTATION_SEVERITY_COEFF: float = 0.30

#: Share of topic severity taken from the declared Topic.criticality label when
#: ``use_topic_criticality`` is enabled. The remainder stays on w(t).
TOPIC_CRITICALITY_BLEND: float = 0.50


@dataclass
class _DependencyView:
    """The derived DEPENDS_ON graph, shared by the IM(v) and IV(v) post-passes."""

    #: (source, target, weight) arcs of the derived dependency graph.
    edges: List[Tuple[str, str, float]]
    component_ids: List[str]
    weights: Dict[str, float]
    in_degrees: Dict[str, int]
    out_degrees: Dict[str, int]


@dataclass
class _CascadeState:
    """Mutable bookkeeping threaded through one cascade traversal."""

    #: Per-component impact in [0, 1]; monotonically non-decreasing.
    impact: Dict[str, float]
    #: Mirror of `1.0 - impact`, owned by the caller.
    performance: Dict[str, float]
    #: Components driven all the way to impact >= 1.0.
    failed_set: Set[str]
    #: Ordered log of what failed, why, and at which depth.
    cascade_sequence: List[CascadeEvent]
    #: BFS frontier of (component_id, depth).
    queue: List[Tuple[str, int]] = field(default_factory=list)


#: Component types counted as "infrastructure that can be up or down" by the
#: fragmentation and failed-component terms.
_COUNTED_TYPES: Tuple[str, ...] = ("Application", "Broker") + NETWORK_TYPES


class _NetworkView:
    """What the physical network still delivers, for one graph state.

    Routes every live pub-sub flow over the live CONNECTS_TO fabric
    (``saag.core.network.evaluate``): a flow with no path is blocked, and when
    links declare ``capacity_mbps`` a flow crossing an over-subscribed link
    loses the excess. A component without a host is not on the network, so the
    network is never the reason its deliveries fail.
    """

    def __init__(self, graph: SimulationGraph):
        self.graph = graph
        fabric = graph.fabric()
        down = {v for v in fabric.vertices if not graph.is_active(v)}
        cut = {link_key(a, b) for a, b in graph._failed_edges
               if link_key(a, b) in fabric.capacity}
        flows, topics = [], []
        for topic_id in graph.topics:
            subs = graph.get_subscribers(topic_id)
            pubs = graph.get_publishers(topic_id)
            if pubs and subs:
                topics.append(topic_id)
                flows += [(p, topic_id, s) for p in pubs for s in subs if p != s]
        self.state = evaluate(
            fabric, flows, graph._runs_on_all,
            live_routers={t: graph.get_routing_brokers(t) for t in topics},
            brokered=[t for t in topics if graph.has_configured_brokers(t)],
            topic_load={t: topic_load_mbps(graph.components[t].properties) for t in topics},
            down=down, cut=cut, with_load=fabric.has_capacity,
        )

    def loss(self, pub_id: str, topic_id: str, sub_id: str) -> float:
        """Undelivered fraction of one flow due to the network (1.0 = no path)."""
        return self.state.flow_loss.get((pub_id, topic_id, sub_id), 0.0)

    def blocks(self, pub_id: str, topic_id: str, sub_id: str) -> bool:
        """Is the network what stops *pub_id*'s messages on *topic_id* reaching *sub_id*?

        A brokered topic with no live broker is not blocked *by the network* --
        that loss belongs to the logical cascade.
        """
        return self.loss(pub_id, topic_id, sub_id) >= 1.0

    def cut_off(self, topic_id: str, sub_id: str) -> bool:
        """*topic_id* is still produced and routed, but not to *sub_id*'s island."""
        pubs = self.graph.get_publishers(topic_id)
        return bool(pubs) and all(self.blocks(p, topic_id, sub_id) for p in pubs)


#: ImpactMetrics fields averaged as floats vs. rounded to nearest int when
#: aggregating Monte Carlo trials in `_mean_impact_metrics`.
_MC_FLOAT_FIELDS: Tuple[str, ...] = (
    "reachability_loss", "fragmentation", "initial_throughput",
    "remaining_throughput", "throughput_loss", "flow_disruption",
)
_MC_INT_FIELDS: Tuple[str, ...] = (
    "initial_paths", "remaining_paths", "initial_components", "failed_components",
    "initial_connected_components", "final_connected_components",
    "affected_topics", "affected_subscribers", "affected_publishers",
    "cascade_count", "cascade_depth",
)


def _mean_impact_metrics(trials: List[ImpactMetrics]) -> ImpactMetrics:
    """Aggregate per-trial ImpactMetrics into one whose every field is the
    trial mean, so composite_impact (a linear combination of the float
    fields) and the downstream RM post-passes describe the same
    statistical quantity rather than mixing an MC-mean composite with a
    single arbitrary draw's other fields.

    `cascade_by_type` and `impact_weights` are taken from the first trial:
    they are not per-draw scalar quantities the mean is well-defined over.
    """
    mean = ImpactMetrics(
        cascade_by_type=dict(trials[0].cascade_by_type),
        impact_weights=dict(trials[0].impact_weights),
    )
    for f in _MC_FLOAT_FIELDS:
        setattr(mean, f, statistics.fmean(getattr(t, f) for t in trials))
    for f in _MC_INT_FIELDS:
        setattr(mean, f, round(statistics.fmean(getattr(t, f) for t in trials)))
    return mean


class FailureSimulator:
    """
    Simulates component failures and cascade propagation.
    
    Works on RAW structural relationships:
        - RUNS_ON: Physical cascade (node -> hosted components)
        - ROUTES: Logical cascade (broker -> topics -> subscriber starvation)
        - CONNECTS_TO: Network cascade (node -> connected nodes)
        - USES: Library cascade (library -> using applications)
        - PUBLISHES_TO / SUBSCRIBES_TO: Application cascade (publisher loss)
    
    Example:
        >>> graph = SimulationGraph(graph_data=data)
        >>> sim = FailureSimulator(graph)
        >>> result = sim.simulate(FailureScenario(target_id="Broker1"))
        >>> print(f"Impact: {result.impact.composite_impact}")
    """

    DEGRADED_PERFORMANCE = 0.5

    def __init__(self, graph: SimulationGraph, telemetry_profile: Optional[RuntimeTelemetryProfile] = None,
                 propagation_threshold: float = 0.2, qos_weighting: bool = True,
                 use_topic_criticality: bool = False, network_gating: bool = False,
                 replica_aware: bool = False):
        """
        Initialize the failure simulator.

        Args:
            graph: SimulationGraph instance
            telemetry_profile: Optional RuntimeTelemetryProfile containing runtime/synthetic calibration data
            propagation_threshold: Feed-loss fraction above which a subscriber is treated as
                starved and propagates the cascade (canonical default 0.2). Per-app overrides
                can still be supplied via telemetry_profile.custom_starvation_bounds.
            qos_weighting: Weight fragmentation, throughput and flow-disruption by the QoS
                severity w(t)·rate of the topics actually lost, rather than counting broken
                paths equally. False restores the count-based terms and is the topology-only
                arm of the label ablation (see reproduce/qos_label_ablation.py).
            use_topic_criticality: Also blend in the author-declared Topic.criticality
                label. Off by default and deliberately so: that label is partly derived
                from the same QoS weight (with ~17% injected noise, see
                tools/generation/generator.py), and it is simultaneously a GNN input
                feature, so enabling it makes the label a function of a feature. Only
                turn it on as an explicit, separately-reported ablation arm — and drop
                topic_qos_criticality_ord from KEYS_BY_TYPE if you do.
            network_gating: Count a pub-sub delivery as live only while the CONNECTS_TO
                network (hosts, switches, routers) still carries it from publisher via a
                routing broker to subscriber. Unreachable subscribers starve, replacing
                the host-to-host isolation rule of the NETWORK cascade; where links
                declare ``capacity_mbps``, flows rerouted onto an over-subscribed link
                lose the excess in the reachability, throughput and flow-disruption
                terms (relative to the healthy baseline). Off by default; a no-op on
                topologies without CONNECTS_TO links.
            replica_aware: A Node takes down a hosted component only as far as all of
                that component's hosts are down (its impact is the least impacted
                host's), so a hot-standby replica keeps it alive; fragmentation then
                attaches a multi-hosted component to every host rather than one.
                Off by default, which keeps the published labels: any one host
                failure takes a multi-hosted component down.
        """
        self.graph = graph
        self.telemetry = telemetry_profile or RuntimeTelemetryProfile()
        self.propagation_threshold = propagation_threshold
        self.qos_weighting = qos_weighting
        self.use_topic_criticality = use_topic_criticality
        self.network_gating = network_gating
        self.replica_aware = replica_aware
        self.logger = logging.getLogger(__name__)
        if not network_gating and any(c.type == "NetworkDevice" for c in graph.components.values()):
            self.logger.warning(
                "Topology models switches/routers but network_gating is off: their "
                "failures only score fragmentation. Pass network_gating=True to route "
                "deliveries over CONNECTS_TO."
            )
        
        # Random generator
        self._rng = random.Random()
        
        # Baseline metrics (computed once per exhaustive run, or per simulate
        # call). Seeded here as well as in _compute_baseline so that reaching
        # _calculate_impact without a baseline yields zeroed metrics rather than
        # an AttributeError.
        self._initial_paths_list: List[Tuple[str, str, str, float]] = []
        self._initial_connected_components: int = 1
        self._initial_total_weight: float = 0.0
        self._initial_paths: int = 0
        self._initial_capacity_sum: float = 0.0
        self._initial_components: int = 0
        self._baseline_flows: Set[Tuple[str, str, str]] = set()
        # Network loss of each flow in the healthy state (network gating only).
        self._baseline_net_loss: Dict[Tuple[str, str, str], float] = {}
        self._initial_stranded_severity: float = 0.0
        self._baseline_computed: bool = False
        # Cached impact of removing nothing; edge deltas subtract it.
        self._null_impact_cache: Optional[ImpactMetrics] = None
    
    @staticmethod
    def _derive_seed(run_seed: Optional[int], component_id: str) -> Optional[int]:
        """Derive a per-component seed from a run-level seed.

        Uses zlib.crc32 rather than hash() because hash() on str is salted by
        PYTHONHASHSEED, which would make labels differ across processes.
        Deriving from the component id (not its index) keeps a component's
        label independent of how many other components share the sweep.
        """
        if run_seed is None:
            return None
        return (run_seed ^ zlib.crc32(component_id.encode("utf-8"))) & 0x7FFFFFFF

    def topic_severity(self, topic_id: str) -> float:
        """Operational severity of losing *topic_id*: w(t) × its message rate.

        w(t) is the QoS-derived topic weight the repositories compute on import
        (0.75·QoS_score + 0.15·size_norm + 0.10·freq_norm); the rate comes from telemetry when
        calibrated and defaults to 1.0. Returns 1.0 uniformly when QoS weighting
        is disabled, which makes every severity-weighted sum collapse back to the
        count-based form.
        """
        if not self.qos_weighting:
            return 1.0
        topic_info = self.graph.topics.get(topic_id)
        weight = float(getattr(topic_info, "weight", 1.0)) if topic_info else 1.0
        if self.use_topic_criticality:
            weight = (
                TOPIC_CRITICALITY_BLEND * self._topic_criticality_norm(topic_id)
                + (1.0 - TOPIC_CRITICALITY_BLEND) * weight
            )
        return weight * self.telemetry.msg_rate_per_sec.get(topic_id, 1.0)

    def _topic_criticality_norm(self, topic_id: str) -> float:
        """Author-declared Topic.criticality as an ordinal normalised to [0, 1]."""
        comp = self.graph.components.get(topic_id)
        props = getattr(comp, "properties", {}) or {}
        label = canonical_criticality(
            props.get("criticality", props.get("topic_criticality")), default="LOW"
        )
        return TOPIC_CRITICALITY_ORD[label] / MAX_TOPIC_CRITICALITY_ORD

    def _component_severity(self, component_id: str) -> float:
        """Severity of a non-topic component, from its aggregate QoS weight."""
        if not self.qos_weighting:
            return 1.0
        comp = self.graph.components.get(component_id)
        return float(getattr(comp, "weight", 1.0)) if comp else 1.0

    def _stranded_severity_fraction(self) -> float:
        """Share of surviving QoS mass cut off from the largest island.

        0.0 while the survivors stay mutually reachable, however many components
        died; it rises only when the graph actually splits, and in proportion to
        how valuable the stranded side is.
        """
        islands = self.graph.active_connected_components(all_hosts=self.replica_aware)
        if len(islands) <= 1:
            return 0.0

        masses = [
            sum(self._component_severity(cid) for cid in island)
            for island in islands
        ]
        total = sum(masses)
        if total <= 0:
            return 0.0
        return (total - max(masses)) / total

    def set_baseline_flows(self, flows: List[Tuple[str, str, str]]) -> None:
        """Set the baseline successful flows from event simulation."""
        self._baseline_flows = set(flows)
        self.logger.info(f"Set {len(self._baseline_flows)} baseline flows for disruption analysis")
    
    def simulate(self, scenario: FailureScenario) -> FailureResult:
        """
        Run a failure simulation.
        
        Args:
            scenario: Configuration for the simulation
            
        Returns:
            FailureResult with impact metrics and cascade analysis
        """
        # Reset graph state
        self.graph.reset()
        
        if scenario.seed is not None:
            self._rng.seed(scenario.seed)
            
        # Ensure target_ids is a list (handles legacy single-target string passing)
        if isinstance(scenario.target_ids, str):
            scenario.target_ids = [scenario.target_ids]
        
        # Validate targets
        valid_targets = []
        for tid in scenario.target_ids:
            if tid in self.graph.components:
                valid_targets.append(tid)
            else:
                self.logger.warning(f"Target '{tid}' not found, skipping.")
        
        if not valid_targets:
            return self._empty_result_multi(scenario, "No valid targets found")
        
        self.logger.info(f"Simulating failure: {valid_targets}")
        
        # Capture initial state (skip if already cached by exhaustive run)
        if not self._baseline_computed:
            self._compute_baseline()
        
        # Performance tracking (1.0 = healthy, 0.5 = degraded, 0.0 = failed)
        performance: Dict[str, float] = {cid: 1.0 for cid in self.graph.components}
        
        # Fail the targets
        failed_set = set()
        cascade_sequence = []
        for tid in valid_targets:
            target_comp = self.graph.components[tid]
            
            # Set performance based on failure mode
            if scenario.failure_mode == FailureMode.DEGRADED:
                self.graph.set_degraded(tid)
                performance[tid] = self.DEGRADED_PERFORMANCE
            else:
                self.graph.fail_component(tid)
                performance[tid] = 0.0
                failed_set.add(tid)
            
            cascade_sequence.append(CascadeEvent(
                component_id=tid,
                component_type=target_comp.type,
                cause="initial_failure",
                depth=0
            ))
        
        # Propagate cascade starting from all initial failed targets
        max_depth = self._propagate_cascade_multi(
            scenario, 
            valid_targets, 
            failed_set, 
            cascade_sequence,
            performance
        )
        
        # Calculate impact metrics
        # For multi-target, we use the first target as the primary identifier if needed
        primary_target = valid_targets[0]
        # Calculate impact based on set of failed components (0.0 performance)
        impact = self._calculate_impact(primary_target, failed_set)
        impact.cascade_count = len(failed_set) - len([t for t in valid_targets if performance[t] == 0.0])
        impact.cascade_depth = max_depth
        
        # Calculate per-layer impacts
        layer_impacts = self._calculate_layer_impacts(failed_set)
        
        # Determine directly related components (combined list)
        related = []
        for tid in valid_targets:
            comp = self.graph.components[tid]
            related.extend(self._get_related_components(tid, comp.type))

        return FailureResult(
            target_id="+".join(valid_targets), # Combined ID for multi-failure
            target_type="Multi" if len(valid_targets) > 1 else self.graph.components[valid_targets[0]].type,
            scenario=scenario.description or f"Failure: {', '.join(valid_targets)}",
            impact=impact,
            cascaded_failures=[c for c in failed_set if c not in valid_targets],
            cascade_sequence=cascade_sequence,
            layer_impacts=layer_impacts,
            related_components=related,
            csc_names={c.id: c.properties.get("name", c.id) for c in self.graph.components.values()},
        )
    
    def simulate_edge_removal(
        self,
        source: str,
        target: str,
        relationship: str = "UNKNOWN",
    ) -> "EdgeCriticality":
        """Sever one relationship and measure what it costs, both endpoints alive.

        This is the observation that edge criticality was previously missing.
        Training labels were a projection of node labels through a hand-chosen
        bridge multiplier (``I*(u) x {1.0, 0.1}``), so reported edge metrics were
        validated against a heuristic rather than against a measurement. Here the
        edge is actually removed and the same reachability / fragmentation /
        throughput / flow quantities that back :class:`ImpactMetrics` are
        recomputed against the cached baseline.

        Only the edge is removed — no cascade is run — because the question an
        edge label answers is "what does *this link* carry", not "what else
        breaks afterwards". Cascade effects belong to the node labels.

        Returns
        -------
        EdgeCriticality
            ``flow_impact`` is the fraction of pub->topic->sub flows broken,
            ``connectivity_impact`` the fragmentation increase, and
            ``combined_impact`` the composite on the same 0.35/0.25/0.25/0.15
            weighting used for nodes, so node and edge scores stay comparable.
        """
        from saag.simulation.models import EdgeCriticality

        self.graph.reset()
        if not self._baseline_computed:
            self._compute_baseline()

        # ``_calculate_impact`` is not zero on a pristine graph: topics that
        # already lack a publisher or a subscriber are counted as lost
        # throughput regardless of what failed. Measured on av_system that floor
        # is composite 0.0061. An edge label must be the *cost of removing the
        # edge*, so every quantity is differenced against that null observation
        # — otherwise edges that cost nothing (RUNS_ON, which this cascade model
        # does not route traffic over) would all report the floor as if it were
        # signal.
        null = self._null_impact()

        self.graph.fail_edge(source, target)
        try:
            impact = self._calculate_impact(source, set())
        finally:
            self.graph.recover_edge(source, target)

        def _delta(after: float, before: float) -> float:
            return max(0.0, after - before)

        combined = _delta(impact.composite_impact, null.composite_impact)
        return EdgeCriticality(
            source=source,
            target=target,
            relationship=relationship,
            flow_impact=round(_delta(impact.flow_disruption, null.flow_disruption), 6),
            connectivity_impact=round(_delta(impact.fragmentation, null.fragmentation), 6),
            combined_impact=round(combined, 6),
            level=self._impact_level(combined),
        )

    def _null_impact(self) -> ImpactMetrics:
        """Impact of removing nothing — the floor every edge delta is measured from."""
        if getattr(self, "_null_impact_cache", None) is None:
            self._null_impact_cache = self._calculate_impact("__null__", set())
        return self._null_impact_cache

    @staticmethod
    def _impact_level(score: float) -> str:
        """Absolute band for a single edge, before per-scenario reclassification."""
        if score >= 0.50:
            return "critical"
        if score >= 0.25:
            return "high"
        if score >= 0.10:
            return "medium"
        return "low" if score > 1e-6 else "minimal"

    def simulate_edge_removal_sweep(
        self,
        candidates: Optional[List[Tuple[str, str, str]]] = None,
        layer: str = "system",
        top_q: int = 50,
    ) -> List["EdgeCriticality"]:
        """Measure every candidate edge, defaulting to bridges + top-betweenness.

        The candidate set is bounded on purpose: an exhaustive sweep is
        O(|E|) full impact recomputations, while the edges that can plausibly
        matter are the non-redundant ones (bridges) and the heavily traversed
        ones (top edge-betweenness). Edges outside the set are returned with
        ``evaluated=False`` rather than silently scored 0, so a consumer can tell
        "measured as harmless" from "never measured".
        """
        if candidates is None:
            candidates = self._select_edge_candidates(layer=layer, top_q=top_q)

        self.graph.reset()
        self._compute_baseline()
        self._null_impact_cache = None

        results = []
        try:
            for src, dst, rel in candidates:
                results.append(self.simulate_edge_removal(src, dst, rel))
        finally:
            self._baseline_computed = False
            self._null_impact_cache = None

        results.sort(key=lambda e: e.combined_impact, reverse=True)
        self.logger.info(
            "Edge-removal sweep: %d candidates, %d with non-zero impact",
            len(results), sum(1 for e in results if e.combined_impact > 1e-6),
        )
        return results

    def _select_edge_candidates(
        self, layer: str = "system", top_q: int = 50
    ) -> List[Tuple[str, str, str]]:
        """Bridges union the top-q edges by betweenness, on the undirected projection."""
        import networkx as nx

        g = self.graph.graph
        if g.number_of_edges() == 0:
            return []

        undirected = nx.Graph(g)
        selected: Set[Tuple[str, str]] = set()

        try:
            selected.update((u, v) for u, v in nx.bridges(undirected))
        except nx.NetworkXError:
            for component in nx.connected_components(undirected):
                sub = undirected.subgraph(component)
                if sub.number_of_nodes() > 1:
                    selected.update((u, v) for u, v in nx.bridges(sub))

        if top_q > 0:
            betweenness = nx.edge_betweenness_centrality(undirected)
            ranked = sorted(betweenness.items(), key=lambda kv: kv[1], reverse=True)
            selected.update(edge for edge, _ in ranked[:top_q])

        out: List[Tuple[str, str, str]] = []
        for u, v in selected:
            for src, dst in ((u, v), (v, u)):
                if g.has_edge(src, dst):
                    rel = g.edges[src, dst].get("relation", "UNKNOWN")
                    out.append((src, dst, rel))
        return sorted(set(out))

    def simulate_exhaustive(
        self,
        scenario_template: Optional[FailureScenario] = None,
        layer: str = "system",
        n_trials: int = 1,
        seed: Optional[int] = 42
    ) -> List[FailureResult]:
        """
        Run failure simulation for all components in a layer.

        Computes baseline once and reuses it across all simulations
        for efficiency.

        Args:
            scenario_template: Base scenario configuration
            layer: Layer to analyze
            n_trials: Monte Carlo trials per component (1 = single draw)
            seed: Run-level seed making the sweep reproducible. Each component
                gets its own seed derived from (seed, component_id), so a
                component's label does not depend on the size or ordering of
                the target set. Pass None to restore free-running behaviour.

        Returns:
            List of FailureResult sorted by impact (highest first)
        """
        results = []

        # Get components to analyze for the layer. Sorted because the underlying
        # component map follows repository insertion order, which is not stable
        # across backends and would otherwise leak into the derived seeds.
        component_ids = sorted(self.graph.get_analyze_components_by_layer(layer))

        self.logger.info(f"Running exhaustive failure analysis: {len(component_ids)} components in layer '{layer}'")
        
        # Compute baseline once (C5 fix: avoid recomputing per simulation)
        self.graph.reset()
        self._compute_baseline()
        
        try:
            for comp_id in component_ids:
                scenario = FailureScenario(
                    target_ids=[comp_id],
                    description=f"Exhaustive failure: {comp_id}",
                    failure_mode=scenario_template.failure_mode if scenario_template else FailureMode.CRASH,
                    layer=layer,
                    cascade_rule=scenario_template.cascade_rule if scenario_template else CascadeRule.ALL,
                    cascade_probability=scenario_template.cascade_probability if scenario_template else 1.0,
                    library_cascade_probability=scenario_template.library_cascade_probability if scenario_template else None,
                    max_cascade_depth=scenario_template.max_cascade_depth if scenario_template else 10,
                    seed=self._derive_seed(seed, comp_id),
                )

                if n_trials > 1:
                    # Run N trials and report the mean of every ImpactMetrics
                    # field, not just composite_impact — a result whose
                    # composite is an MC mean but whose reachability_loss /
                    # fragmentation / throughput_loss / flow_disruption (and
                    # the RM post-passes computed from them) come from one
                    # arbitrary draw is internally inconsistent.
                    trial_impacts: List[ImpactMetrics] = []
                    representative: Optional[FailureResult] = None
                    for trial in range(n_trials):
                        trial_scenario = FailureScenario(
                            target_ids=scenario.target_ids,
                            description=f"{scenario.description} (MC trial {trial})",
                            failure_mode=scenario.failure_mode,
                            cascade_rule=scenario.cascade_rule,
                            cascade_probability=scenario.cascade_probability,
                            library_cascade_probability=scenario.library_cascade_probability,
                            max_cascade_depth=scenario.max_cascade_depth,
                            layer=scenario.layer,
                            seed=self._derive_seed(seed, f"{comp_id}:{trial}"),
                        )
                        trial_result = self.simulate(trial_scenario)
                        trial_impacts.append(trial_result.impact)
                        if representative is None:
                            representative = trial_result
                    result = representative
                    result.impact = _mean_impact_metrics(trial_impacts)
                else:
                    result = self.simulate(scenario)
                results.append(result)
        finally:
            # Always clear the cached baseline flag
            self._baseline_computed = False
        
        # Sort by composite impact (highest first)
        results.sort(key=lambda r: r.impact.composite_impact, reverse=True)

        # RM sub-metric post-passes. They run after the sweep because their
        # normalisation denominators (graph size, total weight, max observed
        # depth) are only known once every component has been simulated.
        dep_view = self._build_dependency_view()
        self._postpass_fault_tolerance(results)
        self._postpass_maintainability(results, dep_view)
        self._postpass_availability(results)

        return results

    def _build_dependency_view(self) -> "_DependencyView":
        """
        Take the DEPENDS_ON projection once, for the IM(v) post-pass.

        Weights are the derived Rule 1-6 weights (probabilistic union, worst-case
        lift, harmonic coupling) straight off SimulationGraph. The previous version
        looked each arc up as a *structural* edge, which can never hit for a derived
        pair, so every weight defaulted to 1.0 and ChangePropagationSimulator's
        loose-coupling stop (theta_loose) could not fire.
        """
        components = self.graph.components
        dep_edges: List[Tuple[str, str, float]] = [
            (src, tgt, weight)
            for src, tgt, weight, _ in self.graph.get_dependency_edges()
        ]

        out_deg = {cid: 0 for cid in components}
        in_deg = {cid: 0 for cid in components}
        for src, tgt, _ in dep_edges:
            if src in out_deg:
                out_deg[src] += 1
            if tgt in in_deg:
                in_deg[tgt] += 1

        return _DependencyView(
            edges=dep_edges,
            component_ids=list(components.keys()),
            weights={cid: getattr(c, "weight", 1.0) for cid, c in components.items()},
            in_degrees=in_deg,
            out_degrees=out_deg,
        )

    def _postpass_fault_tolerance(self, results: List[FailureResult]) -> None:
        """IFT(v): how far and how heavily the runtime failure cascade spread."""
        total_components = len(self.graph.components)
        total_weight = sum(
            getattr(c, "weight", 1.0) for c in self.graph.components.values()
        ) or max(total_components, 1)

        max_observed_depth = max(
            (r.impact.cascade_depth for r in results if r.impact.cascade_depth > 0),
            default=1
        )
        n = total_components - 1  # exclude the failed component itself

        for r in results:
            cascaded_weight = sum(
                getattr(self.graph.components[cid], "weight", 1.0)
                for cid in r.cascaded_failures
                if cid in self.graph.components
            )
            r.impact.cascade_reach = len(r.cascaded_failures) / n if n > 0 else 0.0
            r.impact.weighted_cascade_impact = cascaded_weight / total_weight
            r.impact.normalized_cascade_depth = (
                r.impact.cascade_depth / max_observed_depth if max_observed_depth > 0 else 0.0
            )

    def _postpass_maintainability(
        self, results: List[FailureResult], dep: "_DependencyView"
    ) -> None:
        """
        IM(v): development-time change propagation over the transposed
        DEPENDS_ON graph — distinct from the runtime failure cascade.
        """
        from .change_propagation import ChangePropagationSimulator

        cp_results = ChangePropagationSimulator(
            theta_loose=0.20, theta_stable=0.20
        ).simulate_all(
            component_ids=dep.component_ids,
            dependency_edges=dep.edges,
            component_weights=dep.weights,
            component_in_degrees=dep.in_degrees,
            component_out_degrees=dep.out_degrees,
        )

        for r in results:
            cp = cp_results.get(r.target_id)
            if cp is not None:
                r.impact.change_reach = cp.change_reach
                r.impact.weighted_change_impact = cp.weighted_change_impact
                r.impact.normalized_change_depth = cp.normalized_change_depth

    def _postpass_availability(self, results: List[FailureResult]) -> None:
        """
        IA(v): connectivity disruption.

        Reachability loss and fragmentation are already QoS-weighted by
        `_calculate_impact` (fragmentation carries the 0.70/0.30 structural /
        QoS-mass blend itself), so they carry straight over — re-blending here
        would apply the split twice. Only the partition/cascade split of
        throughput loss is new: a high cascade_reach means most of the loss came
        from subscriber starvation rather than from structural path-breaking.
        """
        for r in results:
            im = r.impact
            im.weighted_reachability_loss = im.reachability_loss
            im.weighted_fragmentation = im.fragmentation
            im.path_breaking_throughput_loss = (
                im.throughput_loss * max(0.0, 1.0 - im.cascade_reach)
            )


    def simulate_pairwise(
        self,
        scenario_template: Optional[FailureScenario] = None,
        layer: str = "app"
    ) -> List[FailureResult]:
        """
        Run pairwise failure simulation for components in a layer.
        
        Simulates initial failure of all pairs (v1, v2) to detect
        superadditive impact and redundancy failure.
        
        Args:
            scenario_template: Base scenario configuration
            layer: Layer to analyze
            
        Returns:
            List of FailureResult sorted by joint impact
        """
        results = []
        component_ids = self.graph.get_analyze_components_by_layer(layer)
        n = len(component_ids)
        
        self.logger.info(f"Running pairwise failure analysis: {n*(n-1)//2} pairs in layer '{layer}'")
        
        self.graph.reset()
        self._compute_baseline()
        
        try:
            for i in range(n):
                for j in range(i + 1, n):
                    v1, v2 = component_ids[i], component_ids[j]
                    scenario = FailureScenario(
                        target_ids=[v1, v2],
                        description=f"Pairwise failure: {v1}+{v2}",
                        layer=layer,
                        cascade_rule=scenario_template.cascade_rule if scenario_template else CascadeRule.ALL,
                        cascade_probability=scenario_template.cascade_probability if scenario_template else 1.0,
                        library_cascade_probability=scenario_template.library_cascade_probability if scenario_template else None,
                    )

                    result = self.simulate(scenario)
                    results.append(result)
        finally:
            self._baseline_computed = False
            
        results.sort(key=lambda r: r.impact.composite_impact, reverse=True)
        return results
    
    def simulate_monte_carlo(
        self,
        scenario: FailureScenario,
        n_trials: int = 100,
    ) -> MonteCarloResult:
        """
        Run N stochastic simulations with cascade_probability < 1.0
        and return the distribution of I(v).
        
        Useful for generating confidence intervals on impact scores
        when cascade propagation is probabilistic.
        
        Args:
            scenario: Base scenario (cascade_probability should be < 1.0)
            n_trials: Number of Monte Carlo trials
            
        Returns:
            MonteCarloResult with mean, std, and 95% CI
        """
        impacts: List[float] = []
        
        for trial in range(n_trials):
            trial_scenario = FailureScenario(
                target_ids=scenario.target_ids,
                description=f"Monte Carlo trial {trial}",
                failure_mode=scenario.failure_mode,
                cascade_rule=scenario.cascade_rule,
                cascade_probability=scenario.cascade_probability,
                library_cascade_probability=scenario.library_cascade_probability,
                max_cascade_depth=scenario.max_cascade_depth,
                layer=scenario.layer,
                seed=trial,
            )
            result = self.simulate(trial_scenario)
            impacts.append(result.impact.composite_impact)
        
        sorted_impacts = sorted(impacts)
        ci_low = sorted_impacts[max(0, int(0.025 * n_trials))]
        ci_high = sorted_impacts[min(n_trials - 1, int(0.975 * n_trials))]
        
        return MonteCarloResult(
            target_id=scenario.target_id,
            n_trials=n_trials,
            mean_impact=statistics.mean(impacts),
            std_impact=statistics.stdev(impacts) if n_trials > 1 else 0.0,
            ci_95=(ci_low, ci_high),
            trial_impacts=impacts,
        )
    
    def _network_view(self) -> Optional[_NetworkView]:
        """Network delivery over the current graph state, or None when gating is off."""
        if not (self.network_gating and self.graph.has_network()):
            return None
        return _NetworkView(self.graph)

    def _network_loss(self, net: Optional[_NetworkView], pub_id: str, topic_id: str, sub_id: str) -> float:
        """Share of a flow's *baseline* delivery the network no longer carries.

        Relative to the healthy state, so a link that is already over-subscribed
        before any failure is not charged to every component: a flow that got
        60% through at baseline and 30% now has lost half of it. A flow the
        network could not carry even at baseline is outside the measurement.
        """
        if net is None:
            return 0.0
        base = self._baseline_net_loss.get((pub_id, topic_id, sub_id), 0.0)
        if base >= 1.0:
            return 1.0
        return min(1.0, max(0.0, 1.0 - (1.0 - net.loss(pub_id, topic_id, sub_id)) / (1.0 - base)))

    def _live_weighted_paths(self, net: Optional[_NetworkView]) -> List[Tuple[str, str, str, float]]:
        """Weighted pub-sub paths, scaled by what the network still carries of each."""
        paths = self.graph.get_weighted_pub_sub_paths(active_only=True)
        if net is None:
            return paths
        scaled = [(p, t, s, w * (1.0 - self._network_loss(net, p, t, s))) for p, t, s, w in paths]
        return [path for path in scaled if path[3] > 0.0]

    def _compute_baseline(self) -> None:
        """Compute and cache baseline metrics from the current (healthy) graph state."""
        net = self._network_view()
        self._baseline_net_loss = dict(net.state.flow_loss) if net is not None else {}
        self._initial_paths_list = self._live_weighted_paths(net)
        self._initial_paths = len(self._initial_paths_list)
        self._initial_capacity_sum = sum(p[3] for p in self._initial_paths_list)
        
        self._initial_components = len([
            c for c in self.graph.components.values()
            if c.type in _COUNTED_TYPES and c.state == ComponentState.ACTIVE
        ])
        self._initial_connected_components = self.graph.count_active_connected_components(
            all_hosts=self.replica_aware)
        self._initial_total_weight = self._compute_total_topic_weight()
        # A topology can already be disconnected when healthy. Record that so the
        # fragmentation term reports stranding *caused by the failure* rather than
        # charging every component for a pre-existing island.
        self._initial_stranded_severity = self._stranded_severity_fraction()
        self._baseline_computed = True
    
    def _compute_total_topic_weight(self) -> float:
        """Compute total QoS-weighted topic capacity calibrated by telemetry."""
        total = sum(self.topic_severity(tid) for tid in self.graph.topics)
        return total if total > 0 else float(len(self.graph.topics))
    
    def _apply_impact(
        self,
        state: "_CascadeState",
        comp_id: str,
        new_impact: float,
        cause: str,
        depth: int,
    ) -> None:
        """
        Raise *comp_id*'s impact to *new_impact*, if that is an increase.

        Every cascade rule ends the same way: take the worse of the two impacts,
        mirror it into the component's performance, flip the component to failed
        or degraded, record the event and enqueue it for further propagation.
        Monotonic by construction — a component's impact never decreases.
        """
        if new_impact <= state.impact[comp_id]:
            return

        state.impact[comp_id] = new_impact
        comp = self.graph.components[comp_id]
        comp.custom_performance = 1.0 - new_impact
        state.performance[comp_id] = 1.0 - new_impact

        if new_impact >= 1.0:
            self.graph.fail_component(comp_id)
            state.failed_set.add(comp_id)
        else:
            self.graph.set_degraded(comp_id)

        state.cascade_sequence.append(CascadeEvent(
            component_id=comp_id,
            component_type=comp.type,
            cause=cause,
            depth=depth,
        ))
        state.queue.append((comp_id, depth))

    def _blast_library_consumers(
        self,
        initial_targets: List[str],
        state: "_CascadeState",
    ) -> None:
        """
        Shared-library blast semantics: a failed Library takes every transitive
        consumer with it as a step function at T0, before the depth-bounded
        cascade starts. Recorded at depth 0 and not routed through
        `_apply_impact`, which would enqueue these for a second traversal.
        """
        failed_libs = [
            tid for tid in initial_targets
            if self.graph.components[tid].type == "Library" and state.impact[tid] >= 1.0
        ]
        if not failed_libs:
            return

        to_blast = set(failed_libs)
        visited: Set[str] = set()
        queue_blast = [(lid, lid) for lid in failed_libs]
        while queue_blast:
            curr, _cause = queue_blast.pop(0)
            if curr in visited:
                continue
            visited.add(curr)
            for consumer in self.graph.get_uses_consumers(curr):
                if consumer in to_blast:
                    continue
                to_blast.add(consumer)
                state.impact[consumer] = 1.0
                comp = self.graph.components.get(consumer)
                state.cascade_sequence.append(CascadeEvent(
                    component_id=consumer,
                    component_type=comp.type if comp else "Application",
                    cause=f"uses_library:{curr}",
                    depth=0,
                ))
                queue_blast.append((consumer, curr))

    def _cascade_physical(self, scenario, state, current_id, current_impact, depth) -> None:
        """Rule 1: a Node takes down everything it hosts (replica-aware: unless a peer host survives)."""
        for comp_id in self.graph.get_hosted_components(current_id):
            if self._rng.random() < scenario.cascade_probability:
                impact = current_impact
                if self.replica_aware:
                    # min() over the hosts: a surviving replica keeps the component up.
                    impact = min(state.impact.get(h, 0.0) for h in self.graph.get_all_hosts(comp_id))
                self._apply_impact(state, comp_id, impact,
                                   f"hosted_on:{current_id}", depth + 1)

    def _cascade_library(self, scenario, state, current_id, current_impact, depth) -> None:
        """Rule 4: a Library takes down the applications that use it."""
        lib_prob = (scenario.library_cascade_probability
                    if scenario.library_cascade_probability is not None
                    else scenario.cascade_probability)
        for app_id in self.graph.get_uses_consumers(current_id):
            if self._rng.random() < lib_prob:
                self._apply_impact(state, app_id, current_impact,
                                   f"uses_library:{current_id}", depth + 1)

    def _topic_weight(self, topic_id: str) -> float:
        """w(t) calibrated by the topic's observed message rate (telemetry, else 1.0)."""
        runtime_rate = self.telemetry.msg_rate_per_sec.get(topic_id, 1.0)
        topic_info = self.graph.topics.get(topic_id)
        return getattr(topic_info, "weight", 1.0) * runtime_rate if topic_info else runtime_rate

    def _edge_prob(self, src: str, dst: str, edge_weight, scenario) -> float:
        """
        Per-edge operational failure probability.

        Defaults to the edge's own QoS-derived coupling weight w(e) — a
        tightly-coupled (high-QoS) edge is likelier to actually propagate than a
        best-effort one — falling back to the scenario's flat
        cascade_probability when no per-edge weight was recorded. Telemetry
        overrides both when it has measured this specific edge.
        """
        default_prob = edge_weight if edge_weight is not None else scenario.cascade_probability
        return self.telemetry.edge_failure_correlation.get((src, dst), default_prob)

    def _cascade_publisher_to_topic(self, scenario, state, current_id, current_impact, depth) -> None:
        """An Application starves the topics it publishes to."""
        publishes_to, _ = self.graph.get_app_topics(current_id)
        for topic_id in publishes_to:
            if topic_id not in self.graph.topics:
                continue

            publishers = self.graph._publishers.get(topic_id, [])
            if publishers:
                avg_pub_impact = sum(state.impact.get(p[0], 0.0) for p in publishers) / len(publishers)
            else:
                avg_pub_impact = current_impact

            # Telemetry-calibrated starvation bound: once the average publisher
            # is this degraded, treat the topic as fully starved.
            starve_bound = self.telemetry.custom_starvation_bounds.get(
                current_id, self.propagation_threshold)
            effective = 1.0 if avg_pub_impact >= (1.0 - starve_bound) else avg_pub_impact

            pub_edge_weight = next((w for aid, w in publishers if aid == current_id), None)
            if self._rng.random() < self._edge_prob(current_id, topic_id, pub_edge_weight, scenario):
                self._apply_impact(
                    state, topic_id, effective * self._topic_weight(topic_id),
                    f"sl_starvation:{avg_pub_impact:.2f} (via {current_id})", depth + 1)

    def _cascade_broker_to_topic(self, scenario, state, current_id, current_impact, depth) -> None:
        """A Broker starves the topics it routes, unless a peer broker still routes them."""
        for topic_id, brokers in self.graph._routing.items():
            if not any(b[0] == current_id for b in brokers):
                continue

            # min() over the routing brokers: surviving redundancy keeps the topic up.
            routing_impact = min((state.impact.get(b[0], 0.0) for b in brokers),
                                 default=current_impact)
            route_edge_weight = next((w for bid, w in brokers if bid == current_id), None)
            if self._rng.random() < self._edge_prob(current_id, topic_id, route_edge_weight, scenario):
                self._apply_impact(
                    state, topic_id, routing_impact * self._topic_weight(topic_id),
                    f"no_active_brokers:{current_id}", depth + 1)

    def _cascade_topic_to_subscriber(self, scenario, state, current_id, current_impact, depth) -> None:
        """A Topic starves its subscribers, unless they have other healthy feeds."""
        for sub in self.graph._subscribers.get(current_id, []):
            sub_id, sub_edge_weight = sub[0], (sub[1] if len(sub) > 1 else None)
            _, subscribed_to = self.graph.get_app_topics(sub_id)
            # min() across feeds: one healthy subscription keeps the app alive.
            sub_impact = min((state.impact.get(t, 0.0) for t in subscribed_to),
                             default=current_impact)

            if self._rng.random() < self._edge_prob(current_id, sub_id, sub_edge_weight, scenario):
                self._apply_impact(state, sub_id, sub_impact,
                                   f"subscriber_starvation:{current_id}", depth + 1)

    def _cascade_network(self, scenario, state, current_id, current_impact, depth) -> None:
        """A Node isolates its network peers that have no other connection."""
        for neighbor_id in self.graph.get_connected_nodes(current_id):
            all_connections = [c[0] for c in self.graph._connections.get(neighbor_id, [])]
            other_impacts = [state.impact.get(c, 0.0) for c in all_connections if c != current_id]
            isolation_impact = min(current_impact, min(other_impacts)) if other_impacts else current_impact
            if self._rng.random() < scenario.cascade_probability:
                self._apply_impact(state, neighbor_id, isolation_impact,
                                   f"network_partition:{current_id}", depth + 1)

    def _cut_at_baseline(self, topic_id: str, sub_id: str) -> bool:
        pubs = self.graph._publishers.get(topic_id, [])
        return bool(pubs) and all(
            self._baseline_net_loss.get((p, topic_id, sub_id), 0.0) >= 1.0 for p, _w in pubs
        )

    def _cascade_network_reachability(self, scenario, state, current_id, current_impact, depth) -> None:
        """With network gating: starve subscribers the network has cut off.

        Same min-across-feeds rule as topic starvation — one feed that still
        arrives keeps the subscriber alive — but a feed counts as lost when its
        topic is still produced and routed, just not to this subscriber's island.
        """
        net = self._network_view()
        if net is None:
            return
        for sub_id, feeds in self.graph._app_subscribes.items():
            if not feeds or not self.graph.is_active(sub_id):
                continue
            # A feed the healthy network never delivered is not this failure's doing.
            cut = [net.cut_off(t, sub_id) and not self._cut_at_baseline(t, sub_id) for t in feeds]
            if not any(cut):
                continue
            sub_impact = min(1.0 if c else state.impact.get(t, 0.0) for t, c in zip(feeds, cut))
            self._apply_impact(state, sub_id, sub_impact,
                               f"network_unreachable:{current_id}", depth + 1)

    def _propagate_cascade_multi(
        self,
        scenario: FailureScenario,
        initial_targets: List[str],
        failed_set: Set[str],
        cascade_sequence: List[CascadeEvent],
        performance: Dict[str, float]
    ) -> int:
        """
        Propagate a failure cascade from multiple initial targets, using
        continuous-valued state reduction with attenuation.

        Impact is a per-component value in [0, 1] internal to this pass — it is
        NOT the paper's I*(v) ground truth (that is FaultInjector.impact_score);
        do not conflate the two.
        """
        state = _CascadeState(
            impact={cid: 0.0 for cid in self.graph.components},
            performance=performance,
            failed_set=failed_set,
            cascade_sequence=cascade_sequence,
        )

        degraded = scenario.failure_mode == FailureMode.DEGRADED
        for tid in initial_targets:
            state.impact[tid] = self.DEGRADED_PERFORMANCE if degraded else 1.0

        library_rule = scenario.cascade_rule in (CascadeRule.LIBRARY, CascadeRule.ALL)
        if library_rule and not degraded:
            self._blast_library_consumers(initial_targets, state)

        # Synchronise the seeded impacts into component state before traversal.
        for cid, imp in state.impact.items():
            if imp <= 0.0:
                continue
            comp = self.graph.components[cid]
            comp.custom_performance = 1.0 - imp
            performance[cid] = 1.0 - imp
            if imp >= 1.0:
                self.graph.fail_component(cid)
                failed_set.add(cid)
            else:
                self.graph.set_degraded(cid)

        state.queue = [(cid, 0) for cid, imp in state.impact.items() if imp > 0.0]

        physical_rule = (scenario.cascade_rule in (CascadeRule.PHYSICAL, CascadeRule.ALL)
                         and scenario.failure_mode != FailureMode.PARTITION)
        logical_rule = scenario.cascade_rule in (CascadeRule.LOGICAL, CascadeRule.ALL)
        network_rule = scenario.cascade_rule in (CascadeRule.NETWORK, CascadeRule.ALL)

        max_depth = 0
        while state.queue:
            current_id, depth = state.queue.pop(0)
            if depth >= scenario.max_cascade_depth:
                continue

            max_depth = max(max_depth, depth)
            current_comp = self.graph.components.get(current_id)
            if not current_comp:
                continue

            current_type = current_comp.type
            current_impact = state.impact[current_id]
            args = (scenario, state, current_id, current_impact, depth)

            if physical_rule and current_type == "Node":
                self._cascade_physical(*args)

            if library_rule and current_type == "Library":
                self._cascade_library(*args)

            if logical_rule:
                if current_type == "Application":
                    self._cascade_publisher_to_topic(*args)
                elif current_type == "Broker":
                    self._cascade_broker_to_topic(*args)
                elif current_type == "Topic":
                    self._cascade_topic_to_subscriber(*args)

            if network_rule and self.network_gating and current_type in NETWORK_TYPES:
                self._cascade_network_reachability(*args)
            elif network_rule and current_type == "Node":
                self._cascade_network(*args)

        return max_depth

    def _impact_reachability(self, net: Optional[_NetworkView] = None) -> Tuple[int, float]:
        """Weighted pub-sub path capacity still available. Returns (paths, loss)."""
        weighted_paths = self._live_weighted_paths(net)
        remaining_capacity_sum = sum(p[3] for p in weighted_paths)

        if self._initial_capacity_sum > 0:
            loss = 1.0 - (remaining_capacity_sum / self._initial_capacity_sum)
        else:
            loss = 0.0
        if net is not None:
            # Rerouting can relieve a link that was congested at baseline.
            loss = max(0.0, loss)
        return len(weighted_paths), loss

    def _impact_fragmentation(self) -> Tuple[int, float]:
        """
        Connectivity loss. Returns (final_connected_components, fragmentation).

        The structural term is the share of newly created islands, out of the
        most that could have been created. Counting islands says nothing about
        what is inside them, though — stranding one broker carrying every
        safety-critical topic reads the same as stranding an idle logger — so
        under QoS weighting the stranded QoS mass is blended in.
        """
        final_cc = self.graph.count_active_connected_components(all_hosts=self.replica_aware)
        initial_cc = self._initial_connected_components

        if self._initial_components > 1:
            denom = max(1, self._initial_components - initial_cc)
            new_cc = max(0, final_cc - initial_cc)
            fragmentation = min(1.0, new_cc / denom)
        else:
            fragmentation = 0.0

        if self.qos_weighting:
            new_stranded = max(
                0.0,
                self._stranded_severity_fraction() - self._initial_stranded_severity,
            )
            fragmentation = (
                FRAGMENTATION_STRUCTURAL_COEFF * fragmentation
                + FRAGMENTATION_SEVERITY_COEFF * new_stranded
            )
        return final_cc, fragmentation

    def _impact_throughput(self, net: Optional[_NetworkView] = None) -> Tuple[float, float, int]:
        """
        QoS-weighted delivery capacity lost. Returns (lost_weight, loss, topics).

        Loss is continuous — the fraction of a topic's delivery capability that
        is gone, not a binary "did it lose every publisher". Broker loss counts
        too: a topic whose only routing broker died delivers nothing.
        """
        lost_weight = 0.0
        affected_topics = 0

        for topic_id in self.graph.topics:
            live_subs = self.graph.get_subscribers(topic_id)
            if not live_subs:
                # Nothing consumes the topic; its whole throughput is moot.
                topic_loss = 1.0
            else:
                all_pubs = self.graph._publishers.get(topic_id, [])
                all_brokers = self.graph._routing.get(topic_id, [])
                live_pubs = self.graph.get_publishers(topic_id)
                live_brokers = self.graph.get_routing_brokers(topic_id)

                pub_loss = 1.0 - (len(live_pubs) / len(all_pubs)) if all_pubs else 0.0
                # Brokerless (DDS direct) topologies have no routing tier to lose.
                broker_loss = (
                    1.0 - (len(live_brokers) / len(all_brokers)) if all_brokers else 0.0
                )
                topic_loss = max(pub_loss, broker_loss)
                if net is not None and live_pubs:
                    # Produced and routed, but the network no longer carries all of
                    # it to the live subscribers (unreachable or congested).
                    feed_loss = [
                        statistics.mean(self._network_loss(net, p, topic_id, s) for p in live_pubs)
                        for s in live_subs
                    ]
                    topic_loss = max(topic_loss, statistics.mean(feed_loss))

            if topic_loss > 1e-9:
                lost_weight += self.topic_severity(topic_id) * topic_loss
                affected_topics += 1

        total_weight = self._initial_total_weight
        loss = min(1.0, lost_weight / total_weight) if total_weight > 0 else 0.0
        return lost_weight, loss, affected_topics

    def _impact_directed(self, target_id: str, failed_set: Set[str]) -> Tuple[float, float]:
        """
        DASA directed availability: (ia_out, ia_in).

        Splits the capacity of every broken baseline path by the role
        *target_id* played on it — upstream (publisher / topic / routing broker)
        versus downstream (subscriber).
        """
        if self._initial_capacity_sum <= 0:
            return 0.0, 0.0

        broken_out_w = 0.0
        broken_in_w = 0.0
        for p_id, t_id, s_id, cap in self._initial_paths_list:
            brokers = [b[0] for b in self.graph._routing.get(t_id, [])]
            path_broken = (
                p_id in failed_set or t_id in failed_set or s_id in failed_set
                or any(b in failed_set for b in brokers)
            )
            if not path_broken:
                continue
            # Attribute the loss to the role the initial target played.
            if target_id in (p_id, t_id) or target_id in brokers:
                broken_out_w += cap
            elif target_id == s_id:
                broken_in_w += cap

        return (broken_out_w / self._initial_capacity_sum,
                broken_in_w / self._initial_capacity_sum)

    def _impact_affected_sets(self) -> Tuple[int, int]:
        """Distinct publishers and subscribers sitting on a broken topic."""
        affected_pubs: Set[str] = set()
        affected_subs: Set[str] = set()

        for topic_id in self.graph.topics:
            # A topic is affected if any link of its delivery chain is broken.
            if (not self.graph.get_publishers(topic_id)
                    or not self.graph.get_routing_brokers(topic_id)
                    or not self.graph.get_subscribers(topic_id)):
                # These indices hold (component_id, weight) tuples; collect the
                # ids only, so an app on two differently-weighted edges to the
                # same topic is not counted twice.
                affected_pubs.update(p[0] for p in self.graph._publishers.get(topic_id, []))
                affected_subs.update(s[0] for s in self.graph._subscribers.get(topic_id, []))

        return len(affected_pubs), len(affected_subs)

    def _impact_flow_disruption(self, net: Optional[_NetworkView] = None) -> float:
        """
        FD(v): the share of baseline message flows that no longer complete.

        Each broken flow counts for the severity of the topic it carried, so
        silencing one safety-critical channel outweighs silencing several
        best-effort telemetry ones. With qos_weighting off every severity is
        1.0 and this collapses to the broken/total count ratio.
        """
        if not self._baseline_flows:
            return 0.0

        broken_weight = 0.0
        total_flow_weight = 0.0
        for pub_id, topic_id, sub_id in self._baseline_flows:
            severity = self.topic_severity(topic_id)
            total_flow_weight += severity

            # is_active() treats DEGRADED as active, which is the intended
            # weakest-link semantics here.
            endpoints_up = (self.graph.is_active(pub_id)
                            and self.graph.is_active(topic_id)
                            and self.graph.is_active(sub_id))
            brokers = self.graph.get_routing_brokers(topic_id)
            if not endpoints_up or not any(self.graph.is_active(b) for b in brokers):
                broken_weight += severity
            elif net is not None:
                broken_weight += severity * self._network_loss(net, pub_id, topic_id, sub_id)

        return broken_weight / total_flow_weight if total_flow_weight > 0 else 0.0

    def _calculate_impact(
        self,
        target_id: str,
        failed_set: Set[str]
    ) -> ImpactMetrics:
        """
        Assemble the impact metrics for the post-cascade graph state.

        Each term is computed by its own helper; this method only wires them
        into the result. Reachability and throughput are QoS-weighted.
        """
        net = self._network_view()
        remaining_paths, reachability_loss = self._impact_reachability(net)
        final_cc, fragmentation = self._impact_fragmentation()
        lost_weight, throughput_loss, affected_topics = self._impact_throughput(net)
        ia_out, ia_in = self._impact_directed(target_id, failed_set)
        affected_publishers, affected_subscribers = self._impact_affected_sets()

        remaining_active = len([
            c for c in self.graph.components.values()
            if c.type in _COUNTED_TYPES
            and c.state == ComponentState.ACTIVE
        ])

        cascade_by_type: Dict[str, int] = defaultdict(int)
        for comp_id in failed_set:
            if comp_id == target_id:
                continue
            comp = self.graph.components.get(comp_id)
            if comp:
                cascade_by_type[comp.type] += 1

        total_weight = self._initial_total_weight
        return ImpactMetrics(
            initial_paths=self._initial_paths,
            remaining_paths=remaining_paths,
            reachability_loss=reachability_loss,
            initial_components=self._initial_components,
            failed_components=self._initial_components - remaining_active,
            initial_connected_components=self._initial_connected_components,
            final_connected_components=final_cc,
            fragmentation=fragmentation,
            initial_throughput=total_weight,
            remaining_throughput=total_weight - lost_weight,
            throughput_loss=throughput_loss,
            flow_disruption=self._impact_flow_disruption(net),
            affected_topics=affected_topics,
            affected_subscribers=affected_subscribers,
            affected_publishers=affected_publishers,
            cascade_by_type=dict(cascade_by_type),
            ia_out=ia_out,
            ia_in=ia_in
        )


    def _calculate_layer_impacts(self, failed_set: Set[str]) -> Dict[str, float]:
        """Calculate impact per analysis layer."""
        layer_impacts = {}
        
        layers = ["app", "infra", "mw", "system"]
        
        for layer in layers:
            layer_comps = set(self.graph.get_components_by_layer(layer))
            if not layer_comps:
                layer_impacts[layer] = 0.0
                continue
            
            # Compute impact as fraction of layer components affected
            affected = failed_set & layer_comps
            layer_impacts[layer] = len(affected) / len(layer_comps)
        
        return layer_impacts
    
    def _get_related_components(self, target_id: str, target_type: str) -> List[str]:
        """Determine directly related components for context in results."""
        related = []
        if target_type == "Application":
            lib_ids = self.graph.get_library_usage().get(target_id, [])
            for lid in lib_ids:
                lcomp = self.graph.components.get(lid)
                name = lcomp.properties.get("name", lid) if lcomp else lid
                if lcomp and "version" in lcomp.properties:
                    name += f" ({lcomp.properties['version']})"
                related.append(f"Uses Lib: {name}")
        elif target_type == "Node":
            hosted_ids = self.graph.get_node_allocations().get(target_id, [])
            for hid in hosted_ids:
                hcomp = self.graph.components.get(hid)
                name = hcomp.properties.get("name", hid) if hcomp else hid
                related.append(f"Hosts: {name}")
        elif target_type == "Broker":
            topic_ids = self.graph.get_broker_routing().get(target_id, [])
            for tid in topic_ids:
                topic = self.graph.topics.get(tid)
                name = topic.name if topic else tid
                related.append(f"Routes: {name}")
        elif target_type == "NetworkDevice":
            for peer in sorted(self.graph.fabric().adj.get(target_id, ())):
                pcomp = self.graph.components.get(peer)
                related.append(f"Links: {pcomp.properties.get('name', peer) if pcomp else peer}")
        return related
    
    def _empty_result_multi(self, scenario: FailureScenario, reason: str) -> FailureResult:
        """Create an empty result for failed simulations."""
        return FailureResult(
            target_id=scenario.target_id,
            target_type="Unknown",
            scenario=reason,
            impact=ImpactMetrics(),
        )