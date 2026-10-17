"""Generator ``network:`` block: a two-tier switched fabric as an opt-in mesh twin."""

import json
from collections import Counter
from pathlib import Path

import pytest

from scripts.write_scenario_manifest import canonical_sha256
from tools.generation import GenerationService, load_config
from tools.generation.models import NetworkConfig

EXAMPLE_YAML = Path("data/network/scenario_atm_two_tier.yaml")
EXAMPLE_JSON = Path("data/network/atm_two_tier_system.json")
MESH_YAML = Path("data/scenarios/scenario_10_atm_system.yaml")


@pytest.fixture(scope="module")
def fabric():
    return GenerationService(config=load_config(EXAMPLE_YAML)).generate()


def test_committed_example_regenerates(fabric):
    assert canonical_sha256(fabric) == canonical_sha256(json.loads(EXAMPLE_JSON.read_text()))


def test_generation_is_deterministic(fabric):
    assert GenerationService(config=load_config(EXAMPLE_YAML)).generate() == fabric


def test_two_tier_shape(fabric):
    devices = {d["id"]: d["role"] for d in fabric["network_devices"]}
    tors = [d for d, role in devices.items() if role == "switch"]
    cores = [d for d, role in devices.items() if role == "router"]
    assert (len(tors), len(cores)) == (2, 2)          # 8 hosts / 4 per rack, 2 cores
    links = fabric["relationships"]["connects_to"]
    host_links = Counter(l["from"] for l in links if l["to"] in tors)
    assert set(host_links) == {n["id"] for n in fabric["nodes"]}
    assert set(host_links.values()) == {1}            # every host single-homed to one ToR
    uplinks = {(l["from"], l["to"]) for l in links if l["to"] in cores}
    assert uplinks == {(t, c) for t in tors for c in cores}
    assert all(l["capacity_mbps"] == 30 for l in links if l["to"] in cores)
    assert all(l["capacity_mbps"] == 100 and l["from_interface"] == "eth0"
               for l in links if l["to"] in tors)


def test_fabric_is_a_mesh_twin(fabric):
    """Everything but the network is what the same YAML without ``network:`` generates."""
    mesh = GenerationService(config=load_config(MESH_YAML)).generate()
    assert {k for k in fabric if fabric[k] != mesh.get(k)} == {"metadata", "relationships", "network_devices"}
    assert {k for k in fabric["metadata"] if fabric["metadata"][k] != mesh["metadata"].get(k)} == {"network"}
    assert {k for k in fabric["relationships"]
            if fabric["relationships"][k] != mesh["relationships"][k]} == {"connects_to"}


def test_single_rack_has_no_core():
    config = load_config(MESH_YAML)
    config.network = NetworkConfig(hosts_per_rack=8)
    out = GenerationService(config=config).generate()
    assert [d["role"] for d in out["network_devices"]] == ["switch"]


def test_network_config_rejects_unknown_topology():
    with pytest.raises(ValueError):
        NetworkConfig.from_dict({"topology": "fat_tree"})
