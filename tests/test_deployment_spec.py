"""Deployment spec loading, validation, defaults and latency resolution."""

import json
import math

import pytest

from saag.deployment.spec import DeploymentSpecError, bind_spec, load_spec
from tests.deployment_helpers import spec, topology


def test_defaults_fill_every_value():
    b = bind_spec(spec(), topology())
    assert b.hosts == ["N1", "N2", "N3", "N4"]
    assert b.host_cpu["N1"] == 4 and b.host_mem["N3"] == 4096 and b.host_p["N2"] == 0.02
    assert b.replicas == {"P": 2, "S1": 1, "S2": 1, "B": 1}      # hotstandby -> 2
    assert b.allowed["S1"] == ["N1", "N2", "N3", "N4"]
    assert b.anti_affinity == "domain" and b.use_all_hosts is False and b.max_utilization == 1.0
    assert b.domain_p == {"D0": 0.01, "D1": 0.01}


def test_node_without_domain_is_its_own_domain():
    s = spec()
    s["nodes"] = {"N1": {"domain": "D0"}, "N2": {"domain": "D0"}}
    b = bind_spec(s, topology())
    assert b.domain["N3"] == "N3" and b.domain["N4"] == "N4"


def test_replicas_pin_and_forbid():
    b = bind_spec(spec(apps={"S1": {"replicas": 2, "pin": ["N1", "N2", "N3"], "forbid": ["N2"]}}), topology())
    assert b.replicas["S1"] == 2
    assert b.allowed["S1"] == ["N1", "N3"]


def test_every_problem_is_reported_at_once():
    s = spec(apps={"ghost": {"cpu": 1}, "S1": {"cpu": -1, "replicas": 0, "pin": ["N1"], "forbid": ["N1"]}},
             nodes={"N1": {"p": 2.0, "domain": "D0"}}, constraints={"max_utilization": 1.5})
    with pytest.raises(DeploymentSpecError) as err:
        bind_spec(s, topology())
    text = "\n".join(err.value.problems)
    for fragment in ("apps.ghost: not in the topology", "app S1: cpu", "app S1: replicas",
                     "pin and forbid leave no allowed Node", "node N1: p", "max_utilization"):
        assert fragment in text
    assert len(err.value.problems) >= 6


def test_unknown_keys_and_sections_are_rejected():
    with pytest.raises(DeploymentSpecError) as err:
        load_spec({"nodez": {}, "apps": {"S1": {"cpus": 2}}})
    assert "unknown section 'nodez'" in str(err.value)
    assert "apps.S1: unknown key 'cpus'" in str(err.value)


def test_node_missing_without_defaults_is_an_error():
    s = spec()
    del s["defaults"]["node"]
    s["nodes"] = {"N1": {"cpu": 4, "mem_mb": 4096, "p": 0.01}, "N2": {"cpu": 4}}
    with pytest.raises(DeploymentSpecError) as err:
        bind_spec(s, topology())
    assert "node N3: not in the spec and no defaults.node given" in err.value.problems
    assert "node N2: missing 'p'" in err.value.problems
    assert not any(p.startswith("node N1") for p in err.value.problems)


def test_latency_constants_by_domain():
    b = bind_spec(spec(), topology())
    assert b.latency["N1"]["N1"] == 0.0
    assert b.latency["N1"]["N2"] == 1.0
    assert b.latency["N1"]["N3"] == 3.0
    assert b.latency_source == "constants"


def test_latency_matrix_overrides_and_is_symmetric():
    b = bind_spec(spec(latency={"matrix_ms": {"N1": {"N3": 9.0}}}), topology())
    assert b.latency["N1"]["N3"] == b.latency["N3"]["N1"] == 9.0
    assert b.latency["N2"]["N4"] == 3.0
    assert b.latency_source == "mixed"


def test_latency_from_hops_and_unreachable_is_inf():
    topo = topology()
    topo["network_devices"] = [{"id": "SW", "name": "SW", "role": "switch"}]
    topo["relationships"]["connects_to"] = [{"from": h, "to": "SW"} for h in ("N1", "N2", "N3")]
    b = bind_spec(spec(latency={"per_hop_ms": 0.5}), topo)
    assert b.latency["N1"]["N2"] == 1.0                 # N1 -> SW -> N2
    assert math.isinf(b.latency["N1"]["N4"])            # N4 has no link
    assert b.latency_source == "hops"


def test_yaml_and_json_specs_are_equal(tmp_path):
    yaml = pytest.importorskip("yaml")
    s = spec(apps={"S1": {"cpu": 2.0}})
    (tmp_path / "s.json").write_text(json.dumps(s))
    (tmp_path / "s.yaml").write_text(yaml.safe_dump(s))
    a = bind_spec(str(tmp_path / "s.json"), topology())
    b = bind_spec(str(tmp_path / "s.yaml"), topology())
    assert a == b


def test_domain_named_like_another_node_is_rejected():
    s = spec(nodes={"N1": {"domain": "N2"}})
    with pytest.raises(DeploymentSpecError, match="same name as a Node"):
        bind_spec(s, topology())
