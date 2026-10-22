"""
Golden snapshot of the two labelling engines at their default arguments.

Every runtime-simulation option (reactive behaviour, deployment stations, the
fault library, threshold marginalisation, ...) is opt-in. This test is the
guarantee that the defaults still reproduce the published I*(v) and I_dyn(v)
values bit for bit, so a new option cannot leak into the JSS labels.

Regenerate only on purpose, after a deliberate change to the default oracle:

    SAAG_REGEN_GOLDEN=1 pytest tests/test_simulation_golden_defaults.py
"""

import json
import logging
import os
from pathlib import Path

import pytest

from saag.core.graph_io import load_graph
from saag.simulation import MessageFlowSimulator
from saag.simulation.fault_injector import FaultInjector, RECOMMENDED_SEEDS

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = Path(__file__).parent / "golden" / "simulation_defaults.json"

I_STAR_SCENARIOS = ("av_system", "microservices_system")
I_DYN_SCENARIO = "tiny_system"
I_DYN_DURATION = 30.0


def _graph(name):
    return load_graph(ROOT / "data" / "scenarios" / f"{name}.json")


def _i_star():
    out = {}
    for name in I_STAR_SCENARIOS:
        res = FaultInjector(_graph(name), seeds=RECOMMENDED_SEEDS).run(
            node_types=["Application", "Broker", "Node"])
        out[name] = {n: r.impact_score for n, r in sorted(res.records.items())}
    return out


def _i_dyn():
    g = _graph(I_DYN_SCENARIO)
    apps = sorted(n for n, d in g.nodes(data=True) if d.get("type") == "Application")
    out = {}
    for node in apps[:3]:
        res = MessageFlowSimulator(g, duration=I_DYN_DURATION, fault_node=node, seed=42).run()
        fe = res.fault_event
        out[node] = {
            "i_dyn": fe.i_dyn,
            "delivery_rate_before": fe.delivery_rate_before,
            "delivery_rate_after": fe.delivery_rate_after,
            "total_messages_delivered": res.total_messages_delivered,
            "latency_p95_after": fe.latency_p95_after,
        }
    return out


def _compute():
    logging.disable(logging.WARNING)
    try:
        return {"i_star": _i_star(), "i_dyn": _i_dyn()}
    finally:
        logging.disable(logging.NOTSET)


@pytest.fixture(scope="module")
def current():
    return _compute()


def test_golden_defaults(current):
    if os.environ.get("SAAG_REGEN_GOLDEN"):
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(json.dumps(current, indent=1, sort_keys=True) + "\n")
    expected = json.loads(GOLDEN.read_text())
    # Round-trip through JSON so float formatting cannot differ from the file.
    assert json.loads(json.dumps(current)) == expected
