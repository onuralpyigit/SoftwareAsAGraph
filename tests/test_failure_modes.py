
import statistics

import pytest
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

from saag.simulation.models import FailureScenario, FailureMode, CascadeRule, ImpactMetrics
from saag.simulation.failure_simulator import FailureSimulator, _mean_impact_metrics
from saag.simulation.graph import SimulationGraph
from saag.core.models import GraphData, ComponentData, EdgeData

def create_test_graph(n_publishers: int):
    """Create a graph with N publishers to 1 topic and 1 subscriber."""
    components = [
        ComponentData("Topic1", "Topic"),
        ComponentData("Sub1", "Application"),
        ComponentData("Broker1", "Broker"),
    ]
    edges = [
        EdgeData("Broker1", "Topic1", "Broker", "Topic", "logic", "ROUTES"),
        EdgeData("Sub1", "Topic1", "Application", "Topic", "logic", "SUBSCRIBES_TO"),
    ]
    
    for i in range(1, n_publishers + 1):
        pid = f"Pub{i}"
        components.append(ComponentData(pid, "Application"))
        edges.append(EdgeData(pid, "Topic1", "Application", "Topic", "logic", "PUBLISHES_TO"))
    
    return SimulationGraph(GraphData(components=components, edges=edges))

def test_degraded_single_publisher():
    """Single publisher degraded (0.5) > threshold (0.2) -> Topic lives."""
    graph = create_test_graph(n_publishers=1)
    sim = FailureSimulator(graph)

    # Degrade the only publisher
    scenario = FailureScenario(target_ids=["Pub1"], failure_mode=FailureMode.DEGRADED)
    res = sim.simulate(scenario)

    # avg_pub_impact = 0.5 < (1 - 0.2) = 0.8. Topic1 should NOT be in failed_set.
    assert "Topic1" not in res.cascaded_failures
    assert "Sub1" not in res.cascaded_failures

def test_degraded_crushed_multi_publisher():
    """
    5 publishers. 4 fail, 1 lives.
    avg_pub_impact = (1.0*4 + 0) / 5 = 0.8 >= (1 - 0.2) -> Topic fails.

    Canonical propagation_threshold is 0.2 (FailureSimulator default, matching
    the paper's committed default); the starvation boundary is therefore
    avg_pub_impact >= 0.8, not the pre-fix 0.3-threshold boundary of 0.7.
    """
    graph = create_test_graph(n_publishers=5)
    sim = FailureSimulator(graph)

    # Scenario: 4 of 5 publishers fail (Crash)
    scenario = FailureScenario(target_ids=["Pub1", "Pub2", "Pub3", "Pub4"], failure_mode=FailureMode.CRASH)
    res = sim.simulate(scenario)

    # avg_pub_impact = 0.8 >= 0.8. Topic1 should fail.
    assert "Topic1" in res.cascaded_failures
    assert "Sub1" in res.cascaded_failures

    # Re-test: 3 of 5 publishers fail.
    # avg_pub_impact = 0.6 < 0.8. Topic1 lives.
    scenario2 = FailureScenario(target_ids=["Pub1", "Pub2", "Pub3"], failure_mode=FailureMode.CRASH)
    res2 = sim.simulate(scenario2)
    assert "Topic1" not in res2.cascaded_failures

def test_degraded_starvation_boundary():
    """
    Boundary check around the canonical propagation_threshold = 0.2
    (starvation fires when avg_pub_impact >= 1 - 0.2 = 0.8).

    5 publishers. Fail 4. avg_pub_impact = 4/5 = 0.8 >= 0.8 -> Fails.
    5 publishers. Fail 3. avg_pub_impact = 3/5 = 0.6 < 0.8 -> Lives.
    """
    # 5 pubs, fail 4
    graph = create_test_graph(n_publishers=5)
    sim = FailureSimulator(graph)
    res = sim.simulate(FailureScenario(target_ids=["Pub1", "Pub2", "Pub3", "Pub4"], failure_mode=FailureMode.CRASH))
    assert "Topic1" in res.cascaded_failures

    # 5 pubs, fail 3
    graph3 = create_test_graph(n_publishers=5)
    sim3 = FailureSimulator(graph3)
    res3 = sim3.simulate(FailureScenario(target_ids=["Pub1", "Pub2", "Pub3"], failure_mode=FailureMode.CRASH))
    assert "Topic1" not in res3.cascaded_failures

# --- Monte Carlo trial aggregation: composite and sub-metrics must describe
# the same statistical quantity, not an MC-mean composite paired with a
# single arbitrary draw's other fields ---

def test_mean_impact_metrics_averages_every_reported_field():
    trials = [
        ImpactMetrics(
            reachability_loss=0.2, fragmentation=0.4, throughput_loss=0.6, flow_disruption=0.0,
            cascade_count=2, cascade_depth=1, affected_topics=1,
            affected_subscribers=2, affected_publishers=1,
            cascade_by_type={"physical": 2},
        ),
        ImpactMetrics(
            reachability_loss=0.6, fragmentation=0.8, throughput_loss=1.0, flow_disruption=0.4,
            cascade_count=4, cascade_depth=3, affected_topics=3,
            affected_subscribers=4, affected_publishers=3,
            cascade_by_type={"logical": 5},
        ),
    ]
    mean = _mean_impact_metrics(trials)

    assert mean.reachability_loss == pytest.approx(0.4)
    assert mean.fragmentation == pytest.approx(0.6)
    assert mean.throughput_loss == pytest.approx(0.8)
    assert mean.flow_disruption == pytest.approx(0.2)
    # Count-like fields are rounded to the nearest int of the trial mean.
    assert mean.cascade_count == 3   # mean(2, 4) = 3
    assert mean.cascade_depth == 2   # mean(1, 3) = 2

    # composite_impact is a linear combination of the four float fields
    # above, so averaging the fields directly reproduces the mean of the
    # per-trial composites for free -- the two must never disagree, unlike
    # the pre-fix code which overrode only composite_impact via
    # _manual_composite_impact and left every other field at a single
    # arbitrary draw's value.
    expected_composite = statistics.fmean(t.composite_impact for t in trials)
    assert mean.composite_impact == pytest.approx(expected_composite)


def _rns_graph():
    """Node1 hosts three apps via RUNS_ON, so a stochastic physical cascade
    (cascade_probability < 1) fails a variable number of them per trial."""
    components = [
        ComponentData("Node1", "Node"),
        ComponentData("App1", "Application"),
        ComponentData("App2", "Application"),
        ComponentData("App3", "Application"),
    ]
    edges = [
        EdgeData("App1", "Node1", "Application", "Node", "physical", "RUNS_ON"),
        EdgeData("App2", "Node1", "Application", "Node", "physical", "RUNS_ON"),
        EdgeData("App3", "Node1", "Application", "Node", "physical", "RUNS_ON"),
    ]
    return SimulationGraph(GraphData(components=components, edges=edges))


def test_simulate_exhaustive_monte_carlo_matches_trial_mean():
    """End-to-end: simulate_exhaustive(n_trials>1) must report the same
    numbers as independently averaging the same per-trial draws it runs
    internally (same seed derivation: _derive_seed(seed, f'{comp_id}:{trial}')).

    Pre-fix, this diverged: the reported result came from one final
    self.simulate(scenario) call using the *component's* exhaustive seed, not
    the trial-derived seeds, with only composite_impact patched to the MC
    mean.
    """
    template = FailureScenario(target_ids=["Node1"], cascade_probability=0.5)

    sim = FailureSimulator(_rns_graph())
    results = sim.simulate_exhaustive(
        scenario_template=template, layer="system", n_trials=12, seed=7,
    )
    reported = next(r for r in results if r.target_id == "Node1").impact

    # Independently replicate the same 12 trials this run is defined to draw.
    replay = FailureSimulator(_rns_graph())
    replay.graph.reset()
    replay._compute_baseline()
    trial_impacts = [
        replay.simulate(FailureScenario(
            target_ids=["Node1"], cascade_probability=0.5,
            seed=replay._derive_seed(7, f"Node1:{trial}"),
        )).impact
        for trial in range(12)
    ]

    # cascade_count varies across these trials for this graph/seed (the whole
    # point of the check) -- if it doesn't, the fixture stopped exercising
    # the stochastic path this test depends on.
    assert len({t.cascade_count for t in trial_impacts}) > 1

    assert reported.cascade_count == round(statistics.fmean(t.cascade_count for t in trial_impacts))
    assert reported.composite_impact == pytest.approx(
        statistics.fmean(t.composite_impact for t in trial_impacts)
    )


# =============================================================================
# Partition Mode & Pairwise / Multi-Target Failures
# =============================================================================

def test_partition_mode_skips_physical_cascade():
    """Test PARTITION mode skips Rule 1 (Physical Cascade). Consolidated from test_partition_mode.py."""
    components = [
        ComponentData("Node1", "Node"),
        ComponentData("App1", "Application"),
        ComponentData("Topic1", "Topic"),
        ComponentData("Broker1", "Broker"),
    ]
    edges = [
        EdgeData("App1", "Node1", "Application", "Node", "infra", "RUNS_ON"),
        EdgeData("App1", "Topic1", "Application", "Topic", "logic", "PUBLISHES_TO"),
        EdgeData("Broker1", "Topic1", "Broker", "Topic", "logic", "ROUTES"),
    ]

    graph = SimulationGraph(GraphData(components=components, edges=edges))

    # CASE 1: CRASH mode
    sim_crash = FailureSimulator(graph)
    res_crash = sim_crash.simulate(FailureScenario(target_ids=["Node1"], failure_mode=FailureMode.CRASH))
    assert "App1" in res_crash.cascaded_failures

    # CASE 2: PARTITION mode
    graph.reset()
    sim_part = FailureSimulator(graph)
    scenario_part = FailureScenario(target_ids=["Node1"], failure_mode=FailureMode.PARTITION)
    res_part = sim_part.simulate(scenario_part)

    # App1 should NOT be failed in PARTITION mode because Rule 1 is skipped
    assert "App1" not in res_part.cascaded_failures
    assert len(res_part.cascaded_failures) == 0


def test_pairwise_logic():
    """Verify multiple initial targets and pairwise impact. Consolidated from test_pairwise_failure.py."""
    components = [
        ComponentData(id="Node1", component_type="Node"),
        ComponentData(id="Node2", component_type="Node"),
        ComponentData(id="App1", component_type="Application"),
        ComponentData(id="App2", component_type="Application"),
        ComponentData(id="Broker1", component_type="Broker"),
        ComponentData(id="T1", component_type="Topic"),
    ]
    edges = [
        EdgeData("App1", "Node1", "Application", "Node", "structural", "RUNS_ON"),
        EdgeData("App2", "Node2", "Application", "Node", "structural", "RUNS_ON"),
        EdgeData("App1", "T1", "Application", "Topic", "logic", "PUBLISHES_TO"),
        EdgeData("App2", "T1", "Application", "Topic", "logic", "SUBSCRIBES_TO"),
        EdgeData("Broker1", "T1", "Broker", "Topic", "logic", "ROUTES"),
    ]

    graph = SimulationGraph(GraphData(components=components, edges=edges))
    sim = FailureSimulator(graph)

    res1 = sim.simulate(FailureScenario(target_ids=["Node1"]))
    assert "App1" in res1.cascaded_failures
    assert "T1" in res1.cascaded_failures
    impact1 = res1.impact.composite_impact

    res2 = sim.simulate(FailureScenario(target_ids=["Node2"]))
    assert "App2" in res2.cascaded_failures
    impact2 = res2.impact.composite_impact

    res_pair = sim.simulate(FailureScenario(target_ids=["Node1", "Node2"]))
    assert "App1" in res_pair.cascaded_failures
    assert "App2" in res_pair.cascaded_failures
    assert "T1" in res_pair.cascaded_failures
    impact_pair = res_pair.impact.composite_impact

    assert impact_pair >= min(impact1, impact2)


def test_superadditivity_scenario():
    """Scenario where I(v1, v2) >> I(v1) + I(v2). Consolidated from test_pairwise_failure.py."""
    components = [
        ComponentData("SegA", "Node"),
        ComponentData("SegB", "Node"),
        ComponentData("Bridge1", "Node"),
        ComponentData("Bridge2", "Node"),
        ComponentData("AppA", "Application"),
        ComponentData("AppB", "Application"),
        ComponentData("T", "Topic"),
        ComponentData("B1", "Broker"),
    ]

    edges = [
        EdgeData("AppA", "SegA", "Application", "Node", "structural", "RUNS_ON"),
        EdgeData("AppB", "SegB", "Application", "Node", "structural", "RUNS_ON"),
        EdgeData("SegA", "Bridge1", "Node", "Node", "network", "CONNECTS_TO"),
        EdgeData("Bridge1", "SegA", "Node", "Node", "network", "CONNECTS_TO"),
        EdgeData("SegA", "Bridge2", "Node", "Node", "network", "CONNECTS_TO"),
        EdgeData("Bridge2", "SegA", "Node", "Node", "network", "CONNECTS_TO"),
        EdgeData("Bridge1", "SegB", "Node", "Node", "network", "CONNECTS_TO"),
        EdgeData("SegB", "Bridge1", "Node", "Node", "network", "CONNECTS_TO"),
        EdgeData("Bridge2", "SegB", "Node", "Node", "network", "CONNECTS_TO"),
        EdgeData("SegB", "Bridge2", "Node", "Node", "network", "CONNECTS_TO"),
        EdgeData("AppA", "T", "Application", "Topic", "logic", "PUBLISHES_TO"),
        EdgeData("AppB", "T", "Application", "Topic", "logic", "SUBSCRIBES_TO"),
        EdgeData("B1", "SegA", "Broker", "Node", "structural", "RUNS_ON"),
        EdgeData("B1", "T", "Broker", "Topic", "logic", "ROUTES"),
    ]

    graph = SimulationGraph(GraphData(components=components, edges=edges))
    sim = FailureSimulator(graph)

    res1 = sim.simulate(FailureScenario(target_ids=["Bridge1"]))
    assert res1.impact.reachability_loss == 0.0

    res2 = sim.simulate(FailureScenario(target_ids=["Bridge2"]))
    assert res2.impact.reachability_loss == 0.0

    res_pair = sim.simulate(FailureScenario(target_ids=["Bridge1", "Bridge2"]))
    assert res_pair.impact.reachability_loss > 0.0
    assert res_pair.impact.composite_impact > (res1.impact.composite_impact + res2.impact.composite_impact)
