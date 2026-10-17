"""GNN features for the physical network: devices are a type of their own, old graphs unchanged."""

import json
import math
from pathlib import Path

import pytest

from saag.core.graph_io import build_graph_from_json
from saag.prediction.data_preparation import networkx_to_hetero_data, node_type_dim


def _hetero(path):
    return networkx_to_hetero_data(build_graph_from_json(json.loads(Path(path).read_text()))).hetero_data


def test_device_free_graph_is_unchanged():
    data = _hetero("data/scenarios/atm_system.json")
    assert "NetworkDevice" not in data.node_types
    attr = data["Node", "CONNECTS_TO", "Node"].edge_attr
    assert attr[:, 0].tolist() == pytest.approx([1.0] * len(attr))
    assert attr[:, 1].tolist() == pytest.approx([math.log2(2) / math.log2(17)] * len(attr))


def test_devices_get_their_own_type_and_features():
    data = _hetero("data/network/atm_two_tier_system.json")
    x = data["NetworkDevice"].x
    assert x.shape == (4, node_type_dim("NetworkDevice"))
    utilisation, transit = x[:, 18], x[:, 19]
    assert ((utilisation > 0) & (utilisation <= 1)).all()
    assert transit.max().item() == pytest.approx(1.0)


def test_network_link_weight_slot_is_headroom():
    data = _hetero("data/network/atm_two_tier_system.json")
    headroom = data["NetworkDevice", "CONNECTS_TO", "NetworkDevice"].edge_attr[:, 0]
    assert (headroom < 1.0).all() and (headroom >= 0.0).all()


def test_untyped_device_is_not_relabelled_application():
    """Before NetworkDevice existed, an unknown type silently became an Application."""
    data = _hetero("data/network/atm_two_tier_system.json")
    assert len(data["Application"].x) == 26


def test_baseline_capacity_is_unchanged_by_the_optional_type():
    """Baselines size one projection per NODE_TYPE_TO_DIM entry; devices must not be one."""
    from saag.prediction.data_preparation import NODE_TYPE_TO_DIM
    assert "NetworkDevice" not in NODE_TYPE_TO_DIM
