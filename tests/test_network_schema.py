"""NetworkDevice vertices and CONNECTS_TO link properties survive import, storage and export."""

import copy
import json
from pathlib import Path

import pytest

from saag.core.graph_io import build_graph_from_json, load_graph
from saag.infrastructure.memory_repo import MemoryRepository
from saag.infrastructure.neo4j_repo import STRUCTURAL_RELATIONSHIPS

EXAMPLE = Path("data/network/atm_two_tier_system.json")


def _topology():
    """Pub on H1 and broker on H2 (rack S1), sub on H3 (rack S2), one core R1."""
    return {
        "metadata": {"seed": 1},
        "nodes": [{"id": h, "name": h} for h in ("H1", "H2", "H3")],
        "network_devices": [
            {"id": "S1", "name": "tor-1", "role": "switch"},
            {"id": "S2", "name": "tor-2", "role": "switch"},
            {"id": "R1", "name": "core", "role": "router"},
        ],
        "brokers": [{"id": "B1", "name": "b"}],
        "topics": [{"id": "T1", "name": "t", "size": 125000, "frequency": 10}],
        "applications": [{"id": "A1", "name": "pub"}, {"id": "A2", "name": "sub"}],
        "libraries": [],
        "relationships": {
            "publishes_to": [{"from": "A1", "to": "T1"}],
            "subscribes_to": [{"from": "A2", "to": "T1"}],
            "routes": [{"from": "B1", "to": "T1"}],
            "runs_on": [{"from": "A1", "to": "H1"}, {"from": "B1", "to": "H2"},
                        {"from": "A2", "to": "H3"}],
            "connects_to": [
                {"from": "H1", "to": "S1", "from_interface": "eth0", "to_interface": "port0",
                 "capacity_mbps": 100},
                {"from": "H2", "to": "S1", "capacity_mbps": 100},
                {"from": "H3", "to": "S2", "capacity_mbps": 100},
                {"from": "S1", "to": "R1", "capacity_mbps": 5},
                {"from": "S2", "to": "R1", "capacity_mbps": 5},
            ],
            "uses": [],
        },
    }


def _repo(topology) -> MemoryRepository:
    repo = MemoryRepository()
    repo.save_graph(copy.deepcopy(topology), clear=True)
    return repo


def test_memory_repository_round_trips_devices_and_link_properties():
    exported = _repo(_topology()).export_json()
    assert [(d["id"], d["role"]) for d in exported["network_devices"]] == [
        ("S1", "switch"), ("S2", "switch"), ("R1", "router")]
    first = exported["relationships"]["connects_to"][0]
    assert first == {"from": "H1", "to": "S1", "weight": 1.0, "from_interface": "eth0",
                     "to_interface": "port0", "capacity_mbps": 100}


def test_phase_5c_routes_baseline_load_over_the_network():
    """10 Mbps of T1 goes H1 -> S1 -> H2 (to the broker) and H2 -> S1 -> R1 -> S2 -> H3."""
    repo = _repo(_topology())
    load = {frozenset((l["from"], l["to"])): l for l in repo.data["relationships"]["connects_to"]}
    assert load[frozenset(("H1", "S1"))]["load_mbps"] == pytest.approx(10.0)
    assert load[frozenset(("H2", "S1"))]["load_mbps"] == pytest.approx(20.0)
    assert load[frozenset(("S1", "R1"))]["utilization"] == pytest.approx(2.0)
    devices = {d["id"]: d for d in repo.data["network_devices"]}
    assert devices["R1"]["max_link_utilization"] == pytest.approx(2.0)
    assert devices["S1"]["link_load_mbps"] == pytest.approx(40.0)


def test_derived_load_is_not_exported():
    exported = _repo(_topology()).export_json()
    for link in exported["relationships"]["connects_to"]:
        assert "load_mbps" not in link and "utilization" not in link


def test_device_weight_is_lifted_from_dependent_hosts():
    repo = _repo(_topology())
    assert all(d["weight"] == 0.01 for d in repo.data["network_devices"])   # Phase 5a floor
    repo.derive_dependencies()
    hosts = {n["id"]: n["weight"] for n in repo.data["nodes"]}
    devices = {d["id"]: d["weight"] for d in repo.data["network_devices"]}
    assert devices["R1"] == pytest.approx(hosts["H3"])
    assert devices["S2"] == pytest.approx(hosts["H3"])


def test_mesh_corpus_is_untouched():
    """No devices, no capacities: no network_devices key, no Phase 5c, no Rule 7."""
    topology = json.loads(Path("data/scenarios/atm_system.json").read_text())
    repo = _repo(topology)
    repo.derive_dependencies()
    exported = repo.export_json()
    assert "network_devices" not in exported
    assert all(set(l) == {"from", "to", "weight"} for l in exported["relationships"]["connects_to"])
    assert not any(e["dependency_type"] == "node_to_device"
                   for e in repo.data["relationships"]["depends_on"])
    assert not any("max_link_utilization" in n for n in repo.data["nodes"])


@pytest.mark.parametrize("builder", [build_graph_from_json, lambda t: None])
def test_graph_builders_type_devices_and_keep_link_properties(builder, tmp_path):
    topology = _topology()
    if builder(topology) is None:
        path = tmp_path / "net.json"
        path.write_text(json.dumps(topology))
        g = load_graph(path)
    else:
        g = builder(topology)
    assert g.nodes["R1"]["type"] == "NetworkDevice"
    assert g.nodes["R1"]["role"] == "router"
    assert g.edges["H1", "S1"]["capacity_mbps"] == 100
    assert g.edges["H1", "S1"]["from_interface"] == "eth0"


def test_neo4j_connects_to_accepts_device_endpoints():
    """A Node-only label pattern would silently MATCH nothing for device links."""
    _rel, src_labels, tgt_labels = STRUCTURAL_RELATIONSHIPS["connects_to"]
    assert "NetworkDevice" in src_labels.split("|")
    assert "NetworkDevice" in tgt_labels.split("|")


@pytest.mark.integration
def test_neo4j_round_trip_matches_memory():
    from saag.infrastructure.neo4j_repo import Neo4jRepository

    topology = _topology()
    try:
        neo = Neo4jRepository()
        neo.save_graph(copy.deepcopy(topology), clear=True)
    except Exception as exc:  # pragma: no cover - needs a live database
        pytest.skip(f"Neo4j unavailable: {exc}")
    neo.derive_dependencies()
    mem = _repo(topology)
    mem.derive_dependencies()

    def arcs(gd):
        return {(e.source_id, e.target_id, e.dependency_type, round(e.weight, 9))
                for e in gd.edges if e.relation_type == "DEPENDS_ON"}

    assert arcs(neo.get_graph_data()) == arcs(mem.get_graph_data())
    links = {(e.source_id, e.target_id): e.properties
             for e in neo.get_graph_data(include_raw=True).edges if e.relation_type == "CONNECTS_TO"}
    assert links[("S1", "R1")]["utilization"] == pytest.approx(2.0)
    assert links[("H1", "S1")]["from_interface"] == "eth0"
