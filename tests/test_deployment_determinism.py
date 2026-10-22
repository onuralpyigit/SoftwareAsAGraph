"""Placements must not depend on PYTHONHASHSEED (MILP column order is built from sorted lists)."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

SCRIPT = """
import json
from saag.deployment.service import DeploymentService
from saag.infrastructure.memory_repo import MemoryRepository
from tests.deployment_helpers import spec, topology
repo = MemoryRepository()
repo.save_graph(topology(brokers=("B1", "B2")), clear=True)
res = DeploymentService(repo).optimize(spec(), seeds=(1,), ties=2, front_points=3).to_dict()
print(json.dumps([[c["name"], c["placement"], c["traffic"], c["blast_exp"], c["held_out"]["expected_impact"]]
                  for c in res["candidates"]] + [res["recommended"]]))
"""


@pytest.mark.slow
def test_result_is_independent_of_hash_seed():
    outputs = set()
    for seed in ("0", "1", "4242"):
        env = {**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": str(ROOT)}
        out = subprocess.run([sys.executable, "-c", SCRIPT], cwd=ROOT, env=env, check=True,
                             capture_output=True, text=True).stdout.strip().splitlines()[-1]
        outputs.add(out)
    assert len(outputs) == 1
    assert json.loads(outputs.pop())[-1] is not None
