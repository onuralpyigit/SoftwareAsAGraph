"""Replica-aware host failure: a hot-standby component dies only with its last host.

Micro topology used throughout: publisher P replicated on N1 and N2 feeds
subscriber S on N3 through topic T (brokerless).
"""

import copy
import json
from pathlib import Path

import pytest

from saag.core.graph_io import build_graph_from_json
from saag.core.models import ComponentData, EdgeData, GraphData
from saag.infrastructure.memory_repo import MemoryRepository
from saag.simulation.failure_simulator import FailureSimulator
from saag.simulation.fault_injector import FaultInjector
from saag.simulation.graph import SimulationGraph
from saag.simulation.models import FailureScenario
from saag.simulation.service import SimulationService


def _topology(p_hosts=("N1", "N2")):
    return {
        "nodes": [{"id": n, "name": n} for n in ("N1", "N2", "N3")],
        "brokers": [],
        "topics": [{"id": "T", "name": "T"}],
        "applications": [{"id": "P", "name": "P"}, {"id": "S", "name": "S"}],
        "libraries": [],
        "relationships": {
            "publishes_to": [{"from": "P", "to": "T"}],
            "subscribes_to": [{"from": "S", "to": "T"}],
            "routes": [],
            "runs_on": [{"from": "P", "to": h} for h in p_hosts] + [{"from": "S", "to": "N3"}],
            "connects_to": [],
            "uses": [],
        },
    }


def _sim_graph(p_hosts=("N1", "N2")) -> SimulationGraph:
    comps = [ComponentData(n, "Node") for n in ("N1", "N2", "N3")]
    comps += [ComponentData("P", "Application"), ComponentData("S", "Application"),
              ComponentData("T", "Topic")]
    edges = [EdgeData("P", h, "Application", "Node", "runs_on", "RUNS_ON") for h in p_hosts]
    edges += [
        EdgeData("S", "N3", "Application", "Node", "runs_on", "RUNS_ON"),
        EdgeData("P", "T", "Application", "Topic", "publishes_to", "PUBLISHES_TO"),
        EdgeData("S", "T", "Application", "Topic", "subscribes_to", "SUBSCRIBES_TO"),
    ]
    return SimulationGraph(GraphData(components=comps, edges=edges))


def _injector_scores(topology, replica_aware, types=("Node",)):
    injector = FaultInjector(graph=build_graph_from_json(topology), seeds=[42],
                             replica_aware=replica_aware)
    return {k: r.impact_score for k, r in injector.run(node_types=list(types)).records.items()}


def _fail(replica_aware, *targets, p_hosts=("N1", "N2")):
    sim = FailureSimulator(_sim_graph(p_hosts), replica_aware=replica_aware)
    return sim.simulate(FailureScenario(target_ids=list(targets), seed=0))


# ── FaultInjector (I*) ─────────────────────────────────────────────────────

def test_injector_losing_one_replica_host_starves_nobody():
    assert _injector_scores(_topology(), replica_aware=True)["N2"] == pytest.approx(0.0)


def test_injector_legacy_semantics_kill_the_replica():
    assert _injector_scores(_topology(), replica_aware=False)["N2"] == pytest.approx(1.0)


def test_injector_single_hosted_component_still_dies_with_its_host():
    assert _injector_scores(_topology(p_hosts=("N2",)), replica_aware=True)["N2"] == pytest.approx(1.0)


# ── FailureSimulator (I_comp) ──────────────────────────────────────────────

def test_simulator_losing_one_replica_host_keeps_the_publisher():
    res = _fail(True, "N2")
    assert "P" not in res.cascaded_failures
    assert "S" not in res.cascaded_failures
    assert res.impact.reachability_loss == pytest.approx(0.0)


def test_simulator_losing_every_replica_host_kills_the_publisher():
    res = _fail(True, "N1", "N2")
    assert "P" in res.cascaded_failures
    assert "S" in res.cascaded_failures


def test_simulator_legacy_semantics_kill_the_replica():
    assert "P" in _fail(False, "N2").cascaded_failures


def test_surviving_replica_stays_attached_to_its_live_host():
    """P's last declared host is N2; the legacy projection only knows that one."""
    graph = _sim_graph()
    graph.fail_component("N2")
    assert {"N1"} in graph.active_connected_components()
    assert graph.active_connected_components(all_hosts=True) == [{"N1", "P", "S", "N3"}]
    assert _fail(True, "N2").impact.fragmentation == pytest.approx(0.0)


def test_simulation_service_threads_replica_aware():
    _graph, sim = SimulationService(MemoryRepository(), replica_aware=True)._failure_sim()
    assert sim.replica_aware is True
    _graph, sim = SimulationService(MemoryRepository())._failure_sim()
    assert sim.replica_aware is False


# ── Corpus: the flag is opt-in and touches Node labels only ───────────────

SCENARIOS = ["atm", "av"]
INJECTED = ["Application", "Broker", "Library", "Topic", "Node"]


def _corpus(name):
    return json.loads(Path(f"data/scenarios/{name}_system.json").read_text())


def _single_hosted(topology):
    """The same system with every component kept on its first declared host."""
    topology = copy.deepcopy(topology)
    seen, runs_on = set(), []
    for rel in topology["relationships"]["runs_on"]:
        if rel["from"] not in seen:
            seen.add(rel["from"])
            runs_on.append(rel)
    topology["relationships"]["runs_on"] = runs_on
    return topology


def _all_labels(topology, **kw):
    fi = FaultInjector(graph=build_graph_from_json(topology), seeds=[42], **kw).run(node_types=INJECTED)
    repo = MemoryRepository()
    repo.save_graph(topology, clear=True)
    fs = SimulationService(repo, **kw).run_failure_simulation_exhaustive(layer="system", seed=42)
    return ({k: r.to_dict() for k, r in fi.records.items()},
            {r.target_id: r.to_dict() for r in fs})


@pytest.mark.parametrize("name", SCENARIOS)
def test_flag_off_is_the_default(name):
    assert _all_labels(_corpus(name)) == _all_labels(_corpus(name), replica_aware=False)


@pytest.mark.parametrize("name", SCENARIOS)
def test_flag_on_moves_only_node_labels(name):
    topology = _corpus(name)
    nodes = {n["id"] for n in topology["nodes"]}
    off, on = _all_labels(topology), _all_labels(topology, replica_aware=True)
    for off_labels, on_labels in zip(off, on):
        assert {k: v for k, v in off_labels.items() if k not in nodes} == \
               {k: v for k, v in on_labels.items() if k not in nodes}
    # The corpus does replicate, so the flag is not a silent no-op on it.
    assert off[0] != on[0]


@pytest.mark.parametrize("name", SCENARIOS)
def test_flag_is_a_no_op_without_replicas(name):
    topology = _single_hosted(_corpus(name))
    assert _all_labels(topology) == _all_labels(topology, replica_aware=True)
