"""
SimulationGraph's DEPENDS_ON projection (Rules 1-7, docs/graph-model.md §4.4).

`saag/simulation/` never reads derived edges, so it re-derives the projection from
raw structural edges. These tests pin that derivation to the authoritative one in
`MemoryRepository` and cover each rule in isolation.

Regression context: the derivation used to implement one rule (direct `USES`) and
returned the subscribed *Topic* instead of that topic's publishers, so Applications
were sinks in the transposed graph and IM(v) was identically 0.0 for every component
the validation scores. See tests/test_maintainability_dimension.py.
"""
import json

import pytest

from saag.core.models import compute_effective_edge_weight
from saag.infrastructure.memory_repo import MemoryRepository
from saag.simulation.graph import SimulationGraph
from saag.usecases.model_graph import ModelGraphUseCase

#: Scenarios exercising every rule: brokers, colocation, and library chains.
PARITY_SCENARIOS = ("atm", "microservices", "hub_and_spoke", "av")


def _empty_graph():
    return {
        "metadata": {"seed": 1},
        "nodes": [], "brokers": [], "topics": [], "applications": [], "libraries": [],
        "relationships": {
            "runs_on": [], "routes": [], "publishes_to": [],
            "subscribes_to": [], "connects_to": [], "uses": [],
        },
    }


def _build(data) -> SimulationGraph:
    repo = MemoryRepository()
    repo.save_graph(data, clear=True)
    return SimulationGraph(repo.get_graph_data(include_raw=True))


def _arcs(graph: SimulationGraph):
    """{(dependent, dependency, dependency_type)} — weights dropped."""
    return {(s, t, d) for s, t, _, d in graph.get_dependency_edges()}


def _weight_of(graph: SimulationGraph, src, tgt, dep_type) -> float:
    return next(w for s, t, w, d in graph.get_dependency_edges()
                if (s, t, d) == (src, tgt, dep_type))


# ---------------------------------------------------------------------------
# Per-rule derivation
# ---------------------------------------------------------------------------

def test_rule1_subscriber_depends_on_publisher():
    """Rule 1 — the arc runs subscriber -> publisher, against the data flow."""
    data = _empty_graph()
    data["topics"] = [{"id": "T", "name": "T", "weight": 0.5}]
    data["applications"] = [{"id": "PUB", "name": "PUB"}, {"id": "SUB", "name": "SUB"}]
    data["relationships"]["publishes_to"] = [{"from": "PUB", "to": "T"}]
    data["relationships"]["subscribes_to"] = [{"from": "SUB", "to": "T"}]

    arcs = _arcs(_build(data))
    assert ("SUB", "PUB", "app_to_app") in arcs
    # The Topic is never a DEPENDS_ON endpoint — this was the original bug.
    assert not any("T" in (s, t) for s, t, _ in arcs)


def test_rule2_participant_depends_on_routing_broker():
    data = _empty_graph()
    data["topics"] = [{"id": "T", "name": "T", "weight": 0.5}]
    data["brokers"] = [{"id": "B", "name": "B"}]
    data["applications"] = [{"id": "PUB", "name": "PUB"}]
    data["relationships"]["publishes_to"] = [{"from": "PUB", "to": "T"}]
    data["relationships"]["routes"] = [{"from": "B", "to": "T"}]

    assert ("PUB", "B", "app_to_broker") in _arcs(_build(data))


def test_rules3_and_4_lift_onto_hosting_nodes():
    data = _empty_graph()
    data["topics"] = [{"id": "T", "name": "T", "weight": 0.5}]
    data["brokers"] = [{"id": "B", "name": "B"}]
    data["nodes"] = [{"id": "N1", "name": "N1"}, {"id": "N2", "name": "N2"}]
    data["applications"] = [{"id": "PUB", "name": "PUB"}, {"id": "SUB", "name": "SUB"}]
    data["relationships"]["publishes_to"] = [{"from": "PUB", "to": "T"}]
    data["relationships"]["subscribes_to"] = [{"from": "SUB", "to": "T"}]
    data["relationships"]["routes"] = [{"from": "B", "to": "T"}]
    data["relationships"]["runs_on"] = [
        {"from": "PUB", "to": "N1"}, {"from": "SUB", "to": "N2"}, {"from": "B", "to": "N1"},
    ]

    arcs = _arcs(_build(data))
    assert ("N2", "N1", "node_to_node") in arcs       # SUB@N2 depends on PUB@N1
    assert ("N2", "B", "node_to_broker") in arcs
    # Same node on both ends is not a node_to_node dependency.
    assert ("N1", "N1", "node_to_node") not in arcs


def test_rule5_app_to_lib_uses_harmonic_coupling():
    data = _empty_graph()
    data["applications"] = [{"id": "A", "name": "A"}]
    data["libraries"] = [{"id": "L", "name": "L"}]
    data["relationships"]["uses"] = [{"from": "A", "to": "L"}]

    g = _build(data)
    assert ("A", "L", "app_to_lib") in _arcs(g)
    w_a = g.components["A"].weight
    w_l = g.components["L"].weight
    assert _weight_of(g, "A", "L", "app_to_lib") == pytest.approx(
        2.0 * w_a * w_l / (w_a + w_l), abs=1e-9
    )


def test_rule6_colocated_brokers_are_bidirectional():
    data = _empty_graph()
    data["nodes"] = [{"id": "N", "name": "N"}]
    data["brokers"] = [{"id": "B1", "name": "B1"}, {"id": "B2", "name": "B2"}]
    data["relationships"]["runs_on"] = [{"from": "B1", "to": "N"}, {"from": "B2", "to": "N"}]

    arcs = _arcs(_build(data))
    assert ("B1", "B2", "broker_to_broker") in arcs
    assert ("B2", "B1", "broker_to_broker") in arcs


# ---------------------------------------------------------------------------
# Parity with the authoritative derivation
# ---------------------------------------------------------------------------

def test_rule7_hosts_depend_on_the_devices_that_separate_them():
    """Rule 7 — a single core router is on every cross-rack path; redundant cores are not."""
    def network(cores):
        data = _empty_graph()
        data["nodes"] = [{"id": h, "name": h} for h in ("H1", "H2")]
        data["network_devices"] = [{"id": d, "name": d, "role": "switch"} for d in ("S1", "S2")]
        data["network_devices"] += [{"id": r, "name": r, "role": "router"} for r in cores]
        data["topics"] = [{"id": "T", "name": "T"}]
        data["applications"] = [{"id": "P", "name": "P"}, {"id": "S", "name": "S"}]
        rels = data["relationships"]
        rels["publishes_to"] = [{"from": "P", "to": "T"}]
        rels["subscribes_to"] = [{"from": "S", "to": "T"}]
        rels["runs_on"] = [{"from": "P", "to": "H1"}, {"from": "S", "to": "H2"}]
        rels["connects_to"] = [{"from": "H1", "to": "S1"}, {"from": "H2", "to": "S2"}]
        rels["connects_to"] += [{"from": s, "to": r} for s in ("S1", "S2") for r in cores]
        return _build(data)

    single = network(["R1"])
    assert {(s, t) for s, t, d in _arcs(single) if d == "node_to_device"} == {
        ("H2", "S2"), ("H2", "R1"), ("H2", "S1"),
    }
    assert _weight_of(single, "H2", "R1", "node_to_device") == pytest.approx(
        _weight_of(single, "H2", "H1", "node_to_node"))
    # The router inherits the weight of the host that depends on it.
    assert single.components["R1"].weight == pytest.approx(single.components["H2"].weight)

    redundant = network(["R1", "R2"])
    assert not {t for s, t, d in _arcs(redundant) if d == "node_to_device"} & {"R1", "R2"}
    assert redundant.components["R1"].weight == pytest.approx(0.01)


def test_device_weight_matches_repository():
    """SimulationGraph re-derives device weights; they must equal the repository's Phase 5b."""
    repo = MemoryRepository()
    with open("data/network/atm_two_tier_system.json") as fh:
        ModelGraphUseCase(repo).execute(json.load(fh), clear=True)
    graph = SimulationGraph(repo.get_graph_data(include_raw=True))
    repo.derive_dependencies()
    for device in repo.data["network_devices"]:
        assert graph.components[device["id"]].weight == pytest.approx(device["weight"])


@pytest.mark.parametrize("path", [
    *(f"data/scenarios/{s}_system.json" for s in PARITY_SCENARIOS),
    "data/network/atm_two_tier_system.json",
])
def test_derivation_matches_repository_edge_set(path):
    """The simulation-side projection must agree arc-for-arc with MemoryRepository.

    Set equality on (dependent, dependency, type). Weights are deliberately allowed
    to diverge on pairs whose topics are reachable through more than one path kind:
    see test_collapse_does_not_double_count_shared_topics.
    """
    repo = MemoryRepository()
    with open(path) as fh:
        ModelGraphUseCase(repo).execute(json.load(fh), clear=True)

    derived = _arcs(SimulationGraph(repo.get_graph_data(include_raw=True)))

    repo.derive_dependencies()
    reference = {(r["from"], r["to"], r["dependency_type"])
                 for r in repo.data["relationships"]["depends_on"]}

    assert derived == reference


def test_collapse_does_not_double_count_shared_topics():
    """A topic reachable directly *and* via a library enters the union once.

    `memory_repo._collapse_paths` flattens its per-kind dict into a multiset, so such
    a topic is counted twice there. `Neo4jRepository` MERGEs each kind separately and
    keeps the max, which is what this derivation matches. Measured divergence between
    the two adapters: 0 affected pairs on atm, 64 on microservices (dw <= 0.248), 139
    on av (dw <= 0.374).
    """
    data = _empty_graph()
    data["topics"] = [{"id": "T", "name": "T", "weight": 0.5}]
    data["applications"] = [{"id": "PUB", "name": "PUB"}, {"id": "SUB", "name": "SUB"}]
    data["libraries"] = [{"id": "L", "name": "L"}]
    # SUB reaches T both directly and through the library L that it uses.
    data["relationships"]["publishes_to"] = [{"from": "PUB", "to": "T"}]
    data["relationships"]["subscribes_to"] = [
        {"from": "SUB", "to": "T"}, {"from": "L", "to": "T"},
    ]
    data["relationships"]["uses"] = [{"from": "SUB", "to": "L"}]

    g = _build(data)
    topic_weight = g.components["T"].weight
    assert _weight_of(g, "SUB", "PUB", "app_to_app") == pytest.approx(
        compute_effective_edge_weight([topic_weight]), abs=1e-9
    ), "shared topic entered the probabilistic union more than once"


def test_derivation_ignores_failed_edges():
    """IM(v) asks about the intact architecture, so severed links must not change it."""
    repo = MemoryRepository()
    with open("data/scenarios/atm_system.json") as fh:
        ModelGraphUseCase(repo).execute(json.load(fh), clear=True)
    graph_data = repo.get_graph_data(include_raw=True)

    before = _arcs(SimulationGraph(graph_data))

    severed = SimulationGraph(graph_data)
    topic, subs = next(iter(severed._subscribers.items()))
    severed._failed_edges.add((subs[0][0], topic))
    severed._dependency_edges = None  # force a fresh derivation

    assert _arcs(severed) == before
