"""FailureSimulator network gating: pub-sub delivery needs a live physical path.

Topology used throughout: two racks, each behind its own top-of-rack switch,
joined by one (or two) routers. A publisher in rack 1 feeds a subscriber in
rack 2 through a broker in rack 1.
"""

import pytest

from saag.core.models import ComponentData, EdgeData, GraphData
from saag.simulation.failure_simulator import FailureSimulator
from saag.simulation.graph import SimulationGraph
from saag.simulation.models import FailureScenario


def _graph(routers=("R1",), network=True) -> SimulationGraph:
    comps = [
        ComponentData("H1", "Node"), ComponentData("H2", "Node"),
        ComponentData("Pub", "Application"), ComponentData("Sub", "Application"),
        ComponentData("B1", "Broker"), ComponentData("T1", "Topic"),
    ]
    edges = [
        EdgeData("Pub", "H1", "Application", "Node", "runs_on", "RUNS_ON"),
        EdgeData("B1", "H1", "Broker", "Node", "runs_on", "RUNS_ON"),
        EdgeData("Sub", "H2", "Application", "Node", "runs_on", "RUNS_ON"),
        EdgeData("Pub", "T1", "Application", "Topic", "publishes_to", "PUBLISHES_TO"),
        EdgeData("Sub", "T1", "Application", "Topic", "subscribes_to", "SUBSCRIBES_TO"),
        EdgeData("B1", "T1", "Broker", "Topic", "routes", "ROUTES"),
    ]
    if network:
        comps += [ComponentData("S1", "NetworkDevice"), ComponentData("S2", "NetworkDevice")]
        comps += [ComponentData(r, "NetworkDevice") for r in routers]
        edges += [
            EdgeData("H1", "S1", "Node", "NetworkDevice", "connects_to", "CONNECTS_TO"),
            EdgeData("H2", "S2", "Node", "NetworkDevice", "connects_to", "CONNECTS_TO"),
        ]
        for r in routers:
            edges += [
                EdgeData("S1", r, "NetworkDevice", "NetworkDevice", "connects_to", "CONNECTS_TO"),
                EdgeData("S2", r, "NetworkDevice", "NetworkDevice", "connects_to", "CONNECTS_TO"),
            ]
    return SimulationGraph(GraphData(components=comps, edges=edges))


def _fail(sim: FailureSimulator, target: str):
    return sim.simulate(FailureScenario(target_ids=[target], seed=0))


def test_router_failure_cuts_the_cross_rack_delivery():
    res = _fail(FailureSimulator(_graph(), network_gating=True), "R1")
    assert "Sub" in res.cascaded_failures
    assert any(e.cause == "network_unreachable:R1" for e in res.cascade_sequence)
    assert res.impact.reachability_loss == pytest.approx(1.0)
    assert res.impact.throughput_loss > 0.0


def test_without_gating_the_router_is_invisible():
    res = _fail(FailureSimulator(_graph(), network_gating=False), "R1")
    assert "Sub" not in res.cascaded_failures
    assert res.impact.reachability_loss == pytest.approx(0.0)


def test_redundant_router_keeps_delivery_alive():
    res = _fail(FailureSimulator(_graph(routers=("R1", "R2")), network_gating=True), "R1")
    assert "Sub" not in res.cascaded_failures
    assert res.impact.reachability_loss == pytest.approx(0.0)


def test_severed_uplink_is_measured_as_edge_criticality():
    sim = FailureSimulator(_graph(), network_gating=True)
    edge = sim.simulate_edge_removal("H2", "S2", "CONNECTS_TO")
    assert edge.combined_impact > 0.0


@pytest.mark.parametrize("target", ["Pub", "B1", "Sub"])
def test_gating_leaves_non_network_failures_alone(target):
    """With the network intact, gating must not re-score logical losses.

    Guards the confound found while building the probe: a topic whose only
    broker died is a logical loss, and gating must not also count it as a
    network cut (which would quietly change Broker labels).
    """
    off = _fail(FailureSimulator(_graph(), network_gating=False), target)
    on = _fail(FailureSimulator(_graph(), network_gating=True), target)
    assert on.impact.composite_impact == pytest.approx(off.impact.composite_impact)


@pytest.mark.parametrize("target", ["Pub", "B1", "H1", "H2"])
def test_gating_is_a_no_op_without_a_network_model(target):
    off = _fail(FailureSimulator(_graph(network=False), network_gating=False), target)
    on = _fail(FailureSimulator(_graph(network=False), network_gating=True), target)
    assert on.impact.composite_impact == pytest.approx(off.impact.composite_impact)
    assert sorted(on.cascaded_failures) == sorted(off.cascaded_failures)


# ── Capacity: congestion after failover ───────────────────────────────────────

def _capped_graph(uplink_mbps=6.0) -> SimulationGraph:
    """Pub and broker on H1 (rack S1), Sub on H2 (rack S2), two cores R1/R2.

    T1 offers 8 Mbps (1 MB at 1 Hz) on the broker -> subscriber leg. ECMP puts
    4 Mbps on each uplink; with one core gone the other carries all 8.
    """
    comps = [
        ComponentData("H1", "Node"), ComponentData("H2", "Node"),
        ComponentData("Pub", "Application"), ComponentData("Sub", "Application"),
        ComponentData("B1", "Broker"),
        ComponentData("T1", "Topic", properties={"size": 1_000_000, "frequency": 1.0}),
        ComponentData("S1", "NetworkDevice"), ComponentData("S2", "NetworkDevice"),
        ComponentData("R1", "NetworkDevice"), ComponentData("R2", "NetworkDevice"),
    ]
    edges = [
        EdgeData("Pub", "H1", "Application", "Node", "runs_on", "RUNS_ON"),
        EdgeData("B1", "H1", "Broker", "Node", "runs_on", "RUNS_ON"),
        EdgeData("Sub", "H2", "Application", "Node", "runs_on", "RUNS_ON"),
        EdgeData("Pub", "T1", "Application", "Topic", "publishes_to", "PUBLISHES_TO"),
        EdgeData("Sub", "T1", "Application", "Topic", "subscribes_to", "SUBSCRIBES_TO"),
        EdgeData("B1", "T1", "Broker", "Topic", "routes", "ROUTES"),
        EdgeData("H1", "S1", "Node", "NetworkDevice", "connects_to", "CONNECTS_TO",
                 properties={"capacity_mbps": 100.0}),
        EdgeData("H2", "S2", "Node", "NetworkDevice", "connects_to", "CONNECTS_TO",
                 properties={"capacity_mbps": 100.0}),
    ]
    for s in ("S1", "S2"):
        for r in ("R1", "R2"):
            edges.append(EdgeData(s, r, "NetworkDevice", "NetworkDevice", "connects_to",
                                  "CONNECTS_TO", properties={"capacity_mbps": uplink_mbps}))
    return SimulationGraph(GraphData(components=comps, edges=edges))


def _capped_sim(**kw) -> FailureSimulator:
    sim = FailureSimulator(_capped_graph(**kw), network_gating=True)
    sim.set_baseline_flows([("Pub", "T1", "Sub")])
    return sim


def test_failover_onto_a_capped_uplink_degrades_without_starving():
    res = _fail(_capped_sim(), "R2")
    # Two congested hops (S1-R1, R1-S2) at utilisation 8/6: (6/8)^2 delivered.
    assert res.impact.flow_disruption == pytest.approx(1 - (6 / 8) ** 2)
    assert res.impact.reachability_loss == pytest.approx(1 - (6 / 8) ** 2)
    assert "Sub" not in res.cascaded_failures


def test_ample_capacity_makes_failover_free():
    res = _fail(_capped_sim(uplink_mbps=1000.0), "R2")
    assert res.impact.flow_disruption == pytest.approx(0.0)
    assert res.impact.reachability_loss == pytest.approx(0.0)


def test_congestion_is_measured_against_the_baseline():
    """A link already over-subscribed when healthy is not charged to every failure."""
    sim = _capped_sim(uplink_mbps=1.0)           # congested before anything fails
    res = _fail(sim, "Pub")                      # unrelated to the network
    off = FailureSimulator(_capped_graph(uplink_mbps=1.0), network_gating=False)
    off.set_baseline_flows([("Pub", "T1", "Sub")])
    assert res.impact.composite_impact == pytest.approx(_fail(off, "Pub").impact.composite_impact)


def test_losing_both_cores_still_starves():
    sim = _capped_sim()
    res = sim.simulate(FailureScenario(target_ids=["R1", "R2"], seed=0))
    assert "Sub" in res.cascaded_failures


def test_simulation_service_threads_network_gating():
    from saag.infrastructure.memory_repo import MemoryRepository
    from saag.simulation.service import SimulationService

    _graph_, sim = SimulationService(MemoryRepository(), network_gating=True)._failure_sim()
    assert sim.network_gating is True
    _graph_, sim = SimulationService(MemoryRepository())._failure_sim()
    assert sim.network_gating is False
