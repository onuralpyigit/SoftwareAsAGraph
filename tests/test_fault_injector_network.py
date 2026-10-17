"""FaultInjector network gating: I* labels for switches, routers and partitions."""

import json
from pathlib import Path

import pytest

from saag.core.graph_io import build_graph_from_json
from saag.simulation.fault_injector import FaultInjector


def _topology(cores=("R1",)):
    """Pub + broker on H1 behind S1; Sub on H2 behind S2; S1/S2 joined by the cores."""
    links = [{"from": "H1", "to": "S1"}, {"from": "H2", "to": "S2"}]
    links += [{"from": s, "to": r} for s in ("S1", "S2") for r in cores]
    return {
        "nodes": [{"id": "H1", "name": "H1"}, {"id": "H2", "name": "H2"}],
        "network_devices": [{"id": d, "name": d, "role": "switch"} for d in ("S1", "S2")]
                           + [{"id": r, "name": r, "role": "router"} for r in cores],
        "brokers": [{"id": "B1", "name": "B1"}],
        "topics": [{"id": "T1", "name": "T1"}],
        "applications": [{"id": "Pub", "name": "Pub"}, {"id": "Sub", "name": "Sub"}],
        "libraries": [],
        "relationships": {
            "publishes_to": [{"from": "Pub", "to": "T1"}],
            "subscribes_to": [{"from": "Sub", "to": "T1"}],
            "routes": [{"from": "B1", "to": "T1"}],
            "runs_on": [{"from": "Pub", "to": "H1"}, {"from": "B1", "to": "H1"},
                        {"from": "Sub", "to": "H2"}],
            "connects_to": links,
            "uses": [],
        },
    }


def _scores(topology, gating, types=("NetworkDevice", "Node")):
    injector = FaultInjector(graph=build_graph_from_json(topology), seeds=[42],
                             network_gating=gating)
    return {k: r.impact_score for k, r in injector.run(node_types=list(types)).records.items()}


def test_router_failure_starves_the_cross_rack_subscriber_only_when_gated():
    assert _scores(_topology(), gating=True)["R1"] == pytest.approx(1.0)
    assert _scores(_topology(), gating=False)["R1"] == pytest.approx(0.0)


def test_redundant_core_gives_no_label():
    scores = _scores(_topology(cores=("R1", "R2")), gating=True)
    assert scores["R1"] == pytest.approx(0.0)
    assert scores["S2"] == pytest.approx(1.0)      # the subscriber's own ToR is still a SPOF


def test_host_labels_are_unchanged_when_hosts_are_leaves():
    """In a switched fabric a host routes nobody's traffic: gating adds nothing to H2."""
    on, off = _scores(_topology(), True), _scores(_topology(), False)
    assert on["H1"] == pytest.approx(off["H1"])
    assert on["H2"] == pytest.approx(off["H2"])


def test_gating_off_reproduces_corpus_labels():
    topology = json.loads(Path("data/scenarios/atm_system.json").read_text())
    types = ["Application", "Broker", "Library", "Node"]
    default = FaultInjector(graph=build_graph_from_json(topology), seeds=[42]).run(node_types=types)
    explicit = FaultInjector(graph=build_graph_from_json(topology), seeds=[42],
                             network_gating=False).run(node_types=types)
    assert {k: r.impact_score for k, r in default.records.items()} == \
           {k: r.impact_score for k, r in explicit.records.items()}


def test_example_dataset_labels_devices():
    topology = json.loads(Path("data/network/atm_two_tier_system.json").read_text())
    scores = _scores(topology, gating=True)
    tors = [d["id"] for d in topology["network_devices"] if d["role"] == "switch"]
    cores = [d["id"] for d in topology["network_devices"] if d["role"] == "router"]
    assert all(scores[t] > 0.0 for t in tors)      # a ToR isolates its whole rack
    assert all(scores[c] == pytest.approx(0.0) for c in cores)   # two cores: redundant
