from typing import Dict, Any, List, Optional, Tuple
import logging
from datetime import datetime

from .graph import SimulationGraph
from .event_simulator import EventSimulator
from .failure_simulator import FailureSimulator
from .models import (
    SimulationReport,
    LayerMetrics,
    ComponentCriticality,
    CascadeRule,
    EventScenario,
    FailureScenario,
    FailureMode,
    FailureResult,
)
from saag.core.ports.graph_repository import IGraphRepository

logger = logging.getLogger(__name__)

#: Impact thresholds shared by the layer histogram and the criticality labels.
_CRITICALITY_BANDS = (
    (0.8, "critical"),
    (0.6, "high"),
    (0.4, "medium"),
    (0.2, "low"),
)

#: Blend of failure impact and observed event throughput in the combined score.
_FAILURE_IMPACT_SHARE = 0.7
_EVENT_IMPACT_SHARE = 0.3


def _band(impact: float) -> str:
    """Map a composite impact onto its criticality label."""
    for threshold, level in _CRITICALITY_BANDS:
        if impact > threshold:
            return level
    return "minimal"


class SimulationService:
    """
    Service for running system simulations (events, failures).
    Orchestrates EventSimulator and FailureSimulator directly.
    """

    def __init__(self, repository: IGraphRepository, qos_weighting: bool = True,
                 network_gating: bool = False, replica_aware: bool = False):
        """
        Args:
            repository: Graph repository port.
            qos_weighting: Weight impact terms by the QoS severity of what was lost.
                False produces the topology-only oracle arm used by the label
                ablation (reproduce/qos_label_ablation.py).
            network_gating: Count a delivery only while the CONNECTS_TO network
                still carries it (reachability, and congestion where links declare
                capacity). See FailureSimulator.
            replica_aware: A host outage takes a multi-hosted component down only
                once all of its hosts are down. See FailureSimulator.
        """
        self.repository = repository
        self.qos_weighting = qos_weighting
        self.network_gating = network_gating
        self.replica_aware = replica_aware

    # =========================================================================
    # Wiring helpers
    # =========================================================================

    def _get_graph(self) -> SimulationGraph:
        """Helper to fetch data and create SimulationGraph."""
        graph_data = self.repository.get_graph_data(include_raw=True)
        return SimulationGraph(graph_data)

    def _failure_sim(
        self, propagation_threshold: float = 0.2
    ) -> Tuple[SimulationGraph, FailureSimulator]:
        """Load the graph and wire a FailureSimulator over it."""
        graph = self._get_graph()
        return graph, FailureSimulator(
            graph,
            propagation_threshold=propagation_threshold,
            qos_weighting=self.qos_weighting,
            network_gating=self.network_gating,
            replica_aware=self.replica_aware,
        )

    @staticmethod
    def _sweep_scenario(
        failure_mode: FailureMode,
        cascade_probability: float,
        library_cascade_probability: Optional[float],
    ) -> FailureScenario:
        """The template scenario shared by the exhaustive and pairwise sweeps."""
        return FailureScenario(
            target_ids=["template"],
            failure_mode=failure_mode,
            cascade_probability=cascade_probability,
            library_cascade_probability=library_cascade_probability,
            cascade_rule=CascadeRule.ALL,
        )

    @staticmethod
    def _prime_baseline_flows(
        graph: SimulationGraph, fail_sim: FailureSimulator
    ) -> Dict[str, Any]:
        """
        Stage A: establish the discrete-event baseline the flow-disruption term
        is measured against, and hand the per-publisher results back so callers
        can reuse them instead of re-running the event simulation.
        """
        event_results = EventSimulator(graph).simulate_all_publishers(
            EventScenario(source_app="all", num_messages=50, duration=5.0)
        )
        flows = []
        for res in event_results.values():
            flows.extend(res.successful_flows)
        fail_sim.set_baseline_flows(flows)
        return event_results

    # =========================================================================
    # Event simulation
    # =========================================================================

    def run_event_simulation(
        self,
        source_app: str,
        num_messages: int = 100,
        duration: float = 10.0,
        layer: str = "system",
        failure_rate: float = 0.0,
        failure_targets: list = None,
        mean_recovery_time: float = 0.0,
        poisson_arrivals: bool = False,
    ) -> Any:
        """Run event simulation from a source application."""
        graph = self._get_graph()
        return EventSimulator(graph).simulate(EventScenario(
            source_app=source_app,
            num_messages=num_messages,
            duration=duration,
            failure_rate=failure_rate,
            failure_targets=failure_targets,
            mean_recovery_time=mean_recovery_time,
            poisson_arrivals=poisson_arrivals,
        ))

    def run_event_simulation_all(
        self,
        num_messages: int = 100,
        duration: float = 10.0,
        layer: str = "system",
        failure_rate: float = 0.0,
        failure_targets: list = None,
        mean_recovery_time: float = 0.0,
        poisson_arrivals: bool = False,
    ) -> Dict[str, Any]:
        """Run event simulation for all publishers."""
        graph = self._get_graph()
        return EventSimulator(graph).simulate_all_publishers(EventScenario(
            source_app="all",
            num_messages=num_messages,
            duration=duration,
            failure_rate=failure_rate,
            failure_targets=failure_targets,
            mean_recovery_time=mean_recovery_time,
            poisson_arrivals=poisson_arrivals,
        ))

    # =========================================================================
    # Failure simulation
    # =========================================================================

    def run_failure_simulation(self, target_ids: List[str], layer: str = "system",
                               cascade_rule: CascadeRule = CascadeRule.ALL,
                               cascade_probability: float = 1.0,
                               library_cascade_probability: Optional[float] = None,
                               propagation_threshold: float = 0.2,
                               failure_mode: FailureMode = FailureMode.CRASH) -> FailureResult:
        """Run a single failure simulation for one or more targets."""
        _, sim = self._failure_sim(propagation_threshold)
        return sim.simulate(FailureScenario(
            target_ids=target_ids,
            failure_mode=failure_mode,
            cascade_rule=cascade_rule,
            cascade_probability=cascade_probability,
            library_cascade_probability=library_cascade_probability,
        ))

    def run_failure_simulation_monte_carlo(self, target_id: str, layer: str = "system",
                                           cascade_probability: float = 1.0,
                                           library_cascade_probability: Optional[float] = None,
                                           propagation_threshold: float = 0.2,
                                           failure_mode: FailureMode = FailureMode.CRASH,
                                           n_trials: int = 100) -> Any:
        """Run Monte Carlo failure simulation."""
        _, sim = self._failure_sim(propagation_threshold)
        scenario = self._sweep_scenario(
            failure_mode, cascade_probability, library_cascade_probability)
        scenario.target_ids = [target_id]
        return sim.simulate_monte_carlo(scenario, n_trials=n_trials)

    def run_failure_simulation_exhaustive(self, layer: str = "system", cascade_probability: float = 1.0,
                                          library_cascade_probability: Optional[float] = None,
                                          propagation_threshold: float = 0.2,
                                          failure_mode: FailureMode = FailureMode.CRASH,
                                          seed: Optional[int] = 42, **kwargs) -> List[Any]:
        """Run exhaustive failure analysis for all components in a layer.

        `seed` makes the sweep reproducible; pass None for free-running behaviour.
        """
        graph, fail_sim = self._failure_sim(propagation_threshold)
        self._prime_baseline_flows(graph, fail_sim)

        return fail_sim.simulate_exhaustive(
            scenario_template=self._sweep_scenario(
                failure_mode, cascade_probability, library_cascade_probability),
            layer=layer,
            n_trials=kwargs.get("n_trials", 1),
            seed=seed,
        )

    def run_failure_events(self, events: Dict[str, List[str]], seed: Optional[int] = 42,
                           propagation_threshold: float = 0.2) -> Dict[str, FailureResult]:
        """Run one simultaneous-failure scenario per named event.

        Each event fails its targets together (e.g. every host of a failure
        domain). Unlike ``run_failure_simulation``, the discrete-event baseline
        is primed first, so the flow-disruption term is live. Events run in
        sorted order, each with a seed derived from (seed, event name), so a
        result does not depend on which other events share the call.
        """
        graph, sim = self._failure_sim(propagation_threshold)
        self._prime_baseline_flows(graph, sim)
        return {
            name: sim.simulate(FailureScenario(
                target_ids=list(events[name]),
                description=f"Failure event: {name}",
                seed=FailureSimulator._derive_seed(seed, name),
            ))
            for name in sorted(events)
        }

    def run_failure_simulation_pairwise(self, layer: str = "system", cascade_probability: float = 1.0,
                                        library_cascade_probability: Optional[float] = None,
                                        propagation_threshold: float = 0.2,
                                        failure_mode: FailureMode = FailureMode.CRASH) -> List[Any]:
        """Run pairwise failure analysis for all component pairs in a layer.

        Primes the same discrete-event baseline the exhaustive sweep uses: the
        flow-disruption term carries 15% of the composite and silently returns
        0.0 for every pair when ``_baseline_flows`` is empty, so an unprimed
        sweep reports a composite built from three of its four criteria.
        """
        graph, sim = self._failure_sim(propagation_threshold)
        self._prime_baseline_flows(graph, sim)
        return sim.simulate_pairwise(
            scenario_template=self._sweep_scenario(
                failure_mode, cascade_probability, library_cascade_probability),
            layer=layer,
        )

    # =========================================================================
    # Reporting
    # =========================================================================

    def generate_report(self, layers: List[str] = ["app", "infra", "mw", "system"],
                        classify_edges: bool = False) -> SimulationReport:
        """Generate a simulation report across layers without redundant execution passes."""
        graph, fail_sim = self._failure_sim()
        report_timestamp = datetime.now().isoformat()

        # Establish baseline flows via discrete-event simulation ONCE globally.
        event_results = self._prime_baseline_flows(graph, fail_sim)

        # Edge criticality by actual edge removal. Runs once over the system
        # scope — an edge belongs to the topology, not to a layer projection.
        edge_criticality = (
            fail_sim.simulate_edge_removal_sweep(layer="system") if classify_edges else []
        )

        layer_metrics_map: Dict[str, LayerMetrics] = {}
        component_criticality: List[ComponentCriticality] = []
        seen_components: set = set()

        for layer in layers:
            logger.info(f"Generating simulation metrics for layer scope: {layer}")
            fail_results = fail_sim.simulate_exhaustive(layer=layer)

            layer_metrics_map[layer] = self._layer_metrics(
                layer, fail_results, event_results, graph)
            component_criticality.extend(
                self._component_criticality(fail_results, event_results, seen_components))

        component_criticality.sort(key=lambda c: c.combined_impact, reverse=True)

        return SimulationReport(
            timestamp=report_timestamp,
            graph_summary=graph.get_summary(),
            layer_metrics=layer_metrics_map,
            component_criticality=component_criticality,
            edge_criticality=edge_criticality,
            top_critical=[c.to_dict() for c in component_criticality[:10]],
            recommendations=self._recommendations(component_criticality),
            csc_names={c.id: c.properties.get("name", c.id) for c in graph.components.values()},
            library_usage=graph.get_library_usage(),
            node_allocations=graph.get_node_allocations(),
            broker_routing=graph.get_broker_routing(),
        )

    @staticmethod
    def _layer_metrics(
        layer: str,
        fail_results: List[FailureResult],
        event_results: Dict[str, Any],
        graph: SimulationGraph,
    ) -> LayerMetrics:
        """Aggregate one layer's failure sweep and event profile."""
        m = LayerMetrics(layer=layer)

        if fail_results:
            n = len(fail_results)
            top_fail = max(fail_results, key=lambda r: r.impact.composite_impact)
            m.total_components = n
            m.max_impact = top_fail.impact.composite_impact
            m.max_impact_component = top_fail.target_id
            m.avg_reachability_loss = sum(r.impact.reachability_loss for r in fail_results) / n
            m.avg_fragmentation = sum(r.impact.fragmentation for r in fail_results) / n
            m.avg_throughput_loss = sum(r.impact.throughput_loss for r in fail_results) / n

            counters = {
                "critical": "critical_count", "high": "high_count", "medium": "medium_count",
                "low": "low_count", "minimal": "minimal_count",
            }
            for r in fail_results:
                attr = counters[_band(r.impact.composite_impact)]
                setattr(m, attr, getattr(m, attr) + 1)
                if r.impact.fragmentation > 0.01:
                    m.spof_count += 1

        # Aggregate event parameters by layer boundaries
        layer_comps = set(graph.get_components_by_layer(layer))
        layer_events = [res for app, res in event_results.items() if app in layer_comps]

        if layer_events:
            n = len(layer_events)
            m.event_throughput = sum(r.metrics.messages_published for r in layer_events)
            m.event_delivered = sum(r.metrics.messages_delivered for r in layer_events)
            m.event_dropped = sum(r.metrics.messages_dropped for r in layer_events)
            m.event_delivery_rate = sum(r.metrics.delivery_rate for r in layer_events) / n
            m.event_drop_rate = sum(r.metrics.drop_rate for r in layer_events) / n
            m.event_avg_latency_ms = (sum(r.metrics.avg_latency for r in layer_events) / n) * 1000
            m.event_p99_latency_ms = (sum(r.metrics.p99_latency for r in layer_events) / n) * 1000
            m.event_throughput_per_sec = sum(r.metrics.throughput for r in layer_events)

        return m

    @staticmethod
    def _component_criticality(
        fail_results: List[FailureResult],
        event_results: Dict[str, Any],
        seen: set,
    ) -> List[ComponentCriticality]:
        """
        Blend each component's failure impact with its observed event
        throughput. `seen` is carried across layers so a component appearing in
        more than one layer projection is scored once.
        """
        out: List[ComponentCriticality] = []
        for r in fail_results:
            comp_id = r.target_id
            if comp_id in seen:
                continue
            seen.add(comp_id)

            ev_res = event_results.get(comp_id)
            throughput = ev_res.metrics.messages_published if ev_res else 0
            ev_impact = min(1.0, throughput / 1000.0) if ev_res else 0.0

            fail_imp = r.impact.composite_impact
            combined = (fail_imp * _FAILURE_IMPACT_SHARE) + (ev_impact * _EVENT_IMPACT_SHARE)

            out.append(ComponentCriticality(
                id=comp_id,
                type=r.target_type,
                event_impact=ev_impact,
                failure_impact=fail_imp,
                combined_impact=combined,
                level=_band(combined),
                cascade_count=r.impact.cascade_count,
                cascade_depth=r.impact.cascade_depth,
                message_throughput=throughput,
                reachability_loss=r.impact.reachability_loss,
                throughput_loss=r.impact.throughput_loss,
                affected_topics=r.impact.affected_topics,
                affected_subscribers=r.impact.affected_subscribers,
            ))
        return out

    @staticmethod
    def _recommendations(criticality: List[ComponentCriticality]) -> List[str]:
        """Surface the two remediation hints the report is able to justify."""
        recommendations = []
        if any(c.reachability_loss > 0.5 for c in criticality):
            recommendations.append(
                "High reachability loss detected. Consider adding redundant paths for critical flows.")
        if any(c.cascade_depth > 5 for c in criticality):
            recommendations.append(
                "Deep failure cascades detected. Implement circuit breakers to limit propagation.")
        return recommendations

    def analyze_layer(self, layer: str) -> LayerMetrics:
        """Alias for generate_report for a single layer, for visualization compatibility."""
        report = self.generate_report(layers=[layer])
        return report.layer_metrics.get(layer, LayerMetrics(layer=layer))

    def classify_components(self, layer: str = "system", k_factor: float = 1.5) -> List[Any]:
        """Classify components by criticality based on simulation results."""
        return self.generate_report(layers=[layer]).component_criticality

    def classify_edges(self, layer: str = "system", k_factor: float = 1.5) -> List[Any]:
        """Classify edges by criticality, measured by actually removing each one."""
        return self.generate_report(layers=[layer], classify_edges=True).edge_criticality
