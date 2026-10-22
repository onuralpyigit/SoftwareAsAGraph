"""
Write a runnable testbed: Dockerfile, docker-compose.yml and deployment.json.

One container per host (``saag-<host>``), all on one bridge network so DDS
discovery works, each running `saag.testbed.agent` for its residents. The
compose file is written as JSON, which is valid YAML and needs no YAML library.
Containers get ``NET_ADMIN`` so `saag.testbed.chaos` can impair links with
``tc netem``.

The testbed directory is its own build context: `write_testbed` copies the
``saag`` package into it, so a build never ships the (large) repository to the
Docker daemon and the image is pinned to the code that wrote the plan.

Building the image downloads the base image and ``cyclonedds`` from PyPI; that
happens only when the caller runs ``docker compose up --build``.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any, Dict

#: Pinned at authoring time; bump deliberately.
CYCLONEDDS_VERSION = "11.0.1"
BASE_IMAGE = "python:3.11-slim"

DOCKERFILE = f"""\
FROM {BASE_IMAGE}
RUN apt-get update && apt-get install -y --no-install-recommends iproute2 \\
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir cyclonedds=={CYCLONEDDS_VERSION} networkx numpy scipy
WORKDIR /app
COPY saag /app/saag
ENV PYTHONPATH=/app PYTHONUNBUFFERED=1
"""


def container_name(host: str) -> str:
    return "saag-" + re.sub(r"[^a-zA-Z0-9_.-]", "-", host)


def compose_spec(plan: Dict[str, Any], duration_s: float, seed: int = 0) -> Dict[str, Any]:
    services = {}
    for host in plan["hosts"]:
        services[container_name(host)] = {
            "build": {"context": ".", "dockerfile": "Dockerfile"},
            "image": "saag-testbed:latest",
            "container_name": container_name(host),
            "hostname": container_name(host),
            "cap_add": ["NET_ADMIN"],
            "networks": ["saag"],
            "volumes": ["./deployment.json:/cfg/deployment.json:ro",
                        "./telemetry:/telemetry", "./control:/control"],
            "command": ["python", "-m", "saag.testbed.agent", "--deployment", "/cfg/deployment.json",
                        "--host", host, "--telemetry", "/telemetry", "--control", "/control",
                        "--duration", str(duration_s), "--seed", str(seed)],
        }
    return {"name": f"saag-{plan['scenario']}".lower(), "services": services,
            "networks": {"saag": {"driver": "bridge"}}}


def write_testbed(plan: Dict[str, Any], out_dir: Path, duration_s: float, seed: int = 0) -> Path:
    out_dir = Path(out_dir)
    for sub in ("telemetry", "control"):
        (out_dir / sub).mkdir(parents=True, exist_ok=True)
    (out_dir / "deployment.json").write_text(json.dumps(plan, indent=2, sort_keys=True))
    (out_dir / "Dockerfile").write_text(DOCKERFILE)
    pkg = Path(__file__).resolve().parents[1]
    shutil.rmtree(out_dir / "saag", ignore_errors=True)
    shutil.copytree(pkg, out_dir / "saag", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (out_dir / "docker-compose.yml").write_text(
        json.dumps(compose_spec(plan, duration_s, seed), indent=2))
    return out_dir
