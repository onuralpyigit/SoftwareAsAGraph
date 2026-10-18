"""saag-deploy and the --deploy stage of cli/run.py."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.deployment_helpers import spec, topology

ROOT = Path(__file__).resolve().parents[1]


def _run(args, **kw):
    return subprocess.run([sys.executable, *args], cwd=ROOT, capture_output=True, text=True,
                          env={"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin"}, **kw)


def test_spec_is_required():
    res = _run(["cli/deploy_graph.py", "-i", "data/network/atm_two_tier_system.json"])
    assert res.returncode == 2 and "--spec" in res.stderr


def test_run_deploy_needs_a_spec():
    res = _run(["cli/run.py", "-i", "data/network/atm_two_tier_system.json", "--deploy"])
    assert res.returncode == 2 and "--deployment-spec" in res.stderr


def test_infeasible_spec_exits_2_with_a_diagnosis(tmp_path):
    (tmp_path / "topo.json").write_text(json.dumps(topology()))
    (tmp_path / "spec.json").write_text(json.dumps(spec(defaults={"node": {"cpu": 0.5, "mem_mb": 4096, "p": 0.02}})))
    res = _run(["cli/deploy_graph.py", "-q", "-i", str(tmp_path / "topo.json"), "--spec", str(tmp_path / "spec.json"),
                "-o", str(tmp_path / "out.json")])
    assert res.returncode == 2
    assert "total cpu demand" in res.stdout
    assert json.loads((tmp_path / "out.json").read_text())["status"] == "infeasible"


def test_invalid_spec_exits_2(tmp_path):
    (tmp_path / "topo.json").write_text(json.dumps(topology()))
    (tmp_path / "spec.json").write_text(json.dumps({"apps": {"ghost": {}}}))
    res = _run(["cli/deploy_graph.py", "-q", "-i", str(tmp_path / "topo.json"), "--spec", str(tmp_path / "spec.json")])
    assert res.returncode == 2 and "apps.ghost: not in the topology" in res.stdout


@pytest.mark.slow
def test_end_to_end_on_the_two_tier_example(tmp_path):
    pytest.importorskip("yaml")
    out, placed = tmp_path / "deployment.json", tmp_path / "placed.json"
    res = _run(["cli/deploy_graph.py", "-q", "-i", "data/network/atm_two_tier_system.json",
                "--spec", "data/deployment/atm_two_tier_deployment.yaml", "--ties", "2", "--front-points", "3",
                "-o", str(out), "--placed-topology", str(placed)], timeout=900)
    assert res.returncode == 0, res.stdout + res.stderr
    result = json.loads(out.read_text())
    assert result["oracle"]["engine"] == "FailureSimulator" and result["oracle"]["replica_aware"] is True
    assert result["oracle"]["network_gating"] is True                       # the topology has devices
    names = {c["name"] for c in result["candidates"]}
    assert {"indeg_spread", "ffd", "k8s_spread", "random", "traffic_only"} <= names
    assert result["recommended"] in names

    topo = json.loads(placed.read_text())
    hot = {a["id"] for a in topo["applications"] if a.get("hotstandby")}
    copies = {}
    for e in topo["relationships"]["runs_on"]:
        copies[e["from"]] = copies.get(e["from"], 0) + 1
    assert all(copies[a["id"]] == (2 if a["id"] in hot else 1) for a in topo["applications"])
