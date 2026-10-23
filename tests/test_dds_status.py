"""
DDS-semantics QoS arm and DDS standard status counting
(saag/simulation/runtime/dds_status.py).
"""

import json
import logging
import math
import random

import networkx as nx
import pytest

pytest.importorskip("simpy")

from saag.simulation.calibration import TelemetryTrace  # noqa: E402
from saag.simulation.message_flow_simulator import MessageFlowSimulator  # noqa: E402
from saag.simulation.runtime.dds_status import (  # noqa: E402
    STATUSES,
    DeadlineMonitor,
    rxo_compatible,
)
from saag.validation.fidelity import compare_dds_status  # noqa: E402

logging.disable(logging.WARNING)

pytestmark = pytest.mark.filterwarnings("error::pytest.PytestUnhandledThreadExceptionWarning")


def chain(deadline_ms=250, pub_qos=None, sub_qos=None, rel="RELIABLE"):
    """A (sensor) -> t1 -> B (processor) -> t2 -> C, 10 Hz, deadline on both topics."""
    g = nx.DiGraph()
    g.add_node("A", type="Application", app_type="sensor")
    g.add_node("B", type="Application", app_type="processor")
    g.add_node("C", type="Application", app_type="actuator")
    for t in ("t1", "t2"):
        g.add_node(t, type="Topic", frequency=10, deadline_ms=deadline_ms,
                   qos={"reliability": rel, "durability": "VOLATILE"})
    g.add_edge("A", "t1", type="PUBLISHES_TO", **({"qos_profile": pub_qos} if pub_qos else {}))
    g.add_edge("B", "t1", type="SUBSCRIBES_TO", **({"qos_profile": sub_qos} if sub_qos else {}))
    g.add_edge("B", "t2", type="PUBLISHES_TO")
    g.add_edge("C", "t2", type="SUBSCRIBES_TO")
    return g


def run(g, **kw):
    kw.setdefault("duration", 40.0)
    kw.setdefault("seed", 3)
    kw.setdefault("rng_streams", "per_process")
    return MessageFlowSimulator(g, **kw).run()


def status(r, name, key, window="total"):
    return r.dds_status["statuses"].get(name, {}).get(key, {}).get(window, 0)


# ── pure helpers ─────────────────────────────────────────────────────────────

def test_rxo_truth_table():
    assert rxo_compatible({"reliability": "BEST_EFFORT"}, {"reliability": "RELIABLE"}) == (False, ["reliability"])
    assert rxo_compatible({"reliability": "RELIABLE"}, {"reliability": "BEST_EFFORT"})[0]
    assert rxo_compatible({"durability": "VOLATILE"}, {"durability": "TRANSIENT_LOCAL"}) == (False, ["durability"])
    assert rxo_compatible({"durability": "PERSISTENT"}, {"durability": "TRANSIENT"})[0]
    assert rxo_compatible({"deadline_ms": 200}, {"deadline_ms": 100}) == (False, ["deadline"])
    assert rxo_compatible({"deadline_ms": None}, {"deadline_ms": 100}) == (False, ["deadline"])
    assert rxo_compatible({"deadline_ms": 50}, {"deadline_ms": 100})[0]
    assert rxo_compatible({"deadline_ms": 50}, {"deadline_ms": None})[0]


def test_deadline_monitor_matches_brute_force():
    rng = random.Random(7)
    segs = [(0.0, 10.0, "pre"), (10.0, math.inf, "post")]
    for _ in range(200):
        period = rng.choice([0.05, 0.1, 0.3, 1.0])
        ts = sorted(rng.uniform(0, 20) for _ in range(rng.randint(1, 30)))
        mon = DeadlineMonitor(period, segs)
        for t in ts:
            mon.arrival(t)
        mon.stop(20.0)
        pre = post = 0
        last = ts[0]
        for t in ts[1:] + [20.0]:
            k = 1
            while last + k * period <= t:
                if last + k * period < 10:
                    pre += 1
                else:
                    post += 1
                k += 1
            last = t
        assert (mon.counts["pre"], mon.counts["post"]) == (pre, post)


def test_rti_aliases_follow_the_connext_catalogue():
    for name, (entity, kind, alias) in STATUSES.items():
        if alias is None:
            continue
        prefix = "dds_data_reader_" if entity == "reader" else "dds_data_writer_"
        assert alias.startswith(prefix), name
        assert alias.endswith("_total") == (kind == "counter"), name


# ── the DDS arm ──────────────────────────────────────────────────────────────

def test_dds_deadline_counts_misses_and_drops_nothing():
    g = chain()
    eng = run(g, fault_node="A", behavior_model="reactive", dds_status=True)
    dds = run(g, fault_node="A", behavior_model="reactive", qos_semantics="dds")
    # A dies at 20 s: B's t1 reader misses every 250 ms period for 20 s; B then
    # stalls, so C misses too and B's t2 writer misses its offered deadline.
    # A dies at 20 s. B's t1 reader misses its 250 ms period until A's lease
    # expires (1 s: 4 misses), then the instance has no writers and DDS stops
    # counting. B stalls but stays alive, so C keeps missing t2's deadline for
    # the rest of the run, and B's t2 writer misses its offered deadline.
    for r in (eng, dds):
        assert status(r, "requested_deadline_missed", "reader|t1|B", "post") == 4
        assert status(r, "requested_deadline_missed", "reader|t2|C", "post") >= 70
        assert status(r, "offered_deadline_missed", "writer|t2|B", "post") >= 70
        assert status(r, "requested_deadline_missed", "reader|t1|B", "pre") == 0
    assert sum(t.total_dropped_deadline for t in dds.topic_stats.values()) == 0
    assert dds.runtime_config["qos_semantics"] == "dds"


def test_engine_arm_still_drops_late_samples():
    """A deadline below the contended latency: engine drops, dds delivers."""
    g = chain(deadline_ms=60)
    eng = run(g, target_utilization=0.9)
    dds = run(g, target_utilization=0.9, qos_semantics="dds")
    assert sum(t.total_dropped_deadline for t in eng.topic_stats.values()) > 0
    assert sum(t.total_dropped_deadline for t in dds.topic_stats.values()) == 0
    assert dds.system_delivery_rate > eng.system_delivery_rate


def test_incompatible_pair_does_not_communicate():
    g = chain(pub_qos={"reliability": "BEST_EFFORT"}, sub_qos={"reliability": "RELIABLE"})
    eng = run(g)
    dds = run(g, qos_semantics="dds")
    assert eng.subscriber_stats["B"].total_received > 300
    assert dds.subscriber_stats["B"].total_received == 0
    assert status(dds, "requested_incompatible_qos", "reader|t1|B") == 1
    assert dds.dds_status["incompatible_pairs"]["reader|t1|B"] == ["A:reliability"]


def test_keep_last_replaces_the_oldest_best_effort_sample():
    """A frozen BEST_EFFORT reader with a 5-deep history: dds replaces, engine rejects."""
    from saag.simulation.runtime.faults import FaultSpec
    g = chain(rel="BEST_EFFORT")
    for t in ("t1", "t2"):
        g.nodes[t]["history_depth"] = 5
    freeze = [FaultSpec("B", "freeze", 10.0, 5.0)]
    eng = run(g, faults=freeze, qos_mode="contracts", dds_status=True)
    dds = run(g, faults=freeze, qos_mode="contracts", qos_semantics="dds")
    assert status(eng, "sample_rejected", "reader|t1|B") > 0
    assert status(eng, "cache_replaced_dropped_samples", "reader|t1|B") == 0
    assert status(dds, "sample_rejected", "reader|t1|B") == 0
    assert status(dds, "cache_replaced_dropped_samples", "reader|t1|B") > 0


def test_alive_count_drops_one_lease_after_a_crash():
    r = run(chain(), fault_node="A", dds_status=True, liveliness_lease_s=2.0)
    nb = r.dds_status["statuses"]["liveliness_alive_count"]["reader|t1|B"]
    assert nb["pre"] == 1 and nb["post"] == 0
    # not_alive_count is the middleware's to report, never a simulated zero.
    assert "liveliness_not_alive_count" not in r.dds_status["statuses"]


def test_reader_deadline_stops_when_its_writers_are_gone():
    """DDS stops deadline monitoring for an instance with no live writers:
    once A is gone (plus the lease), B's t1 reader stops accumulating misses."""
    r = run(chain(), fault_node="A", dds_status=True, liveliness_lease_s=1.0)
    # 20 s outage at a 250 ms period would be 80 misses; the lease bounds it to 4.
    assert status(r, "requested_deadline_missed", "reader|t1|B", "post") == 4


def test_sample_lost_on_a_cut_network_path():
    from saag.simulation.runtime.faults import FaultSpec
    g = chain()
    for h in ("H1", "H2"):
        g.add_node(h, type="Node")
    g.add_edge("A", "H1", type="RUNS_ON")
    g.add_edge("B", "H2", type="RUNS_ON")
    g.add_edge("C", "H2", type="RUNS_ON")
    g.add_edge("H1", "H2", type="CONNECTS_TO")
    r = run(g, faults=[FaultSpec("H1|H2", "link_flap", 10.0, 20.0)],
            deployment_model="stations", network_gating=True, dds_status=True)
    assert status(r, "sample_lost", "reader|t1|B") > 0
    assert status(r, "sample_lost", "reader|t2|C") == 0       # same host as B


def test_status_off_changes_nothing():
    a = run(chain(), fault_node="A")
    b = run(chain(), fault_node="A", dds_status=True)
    assert a.dds_status is None and "dds_status" not in a.to_dict()
    assert a.fault_event.i_dyn == b.fault_event.i_dyn
    assert a.total_messages_delivered == b.total_messages_delivered


def test_status_events_and_fidelity_comparison():
    r = run(chain(), fault_node="A", behavior_model="reactive", qos_semantics="dds",
            record_events=True)
    trace = TelemetryTrace.from_records(r.events, start_s=0.0, end_s=40.0)
    counts = trace.status_counts()
    assert counts["requested_deadline_missed"]["reader|t2|C"] >= 70
    # A simulated trace has no shutdown to exclude, hence no end margin.
    rep = compare_dds_status(trace, trace, split_at=20.0, end_margin_s=0.0)
    d = rep["statuses"]["requested_deadline_missed"]
    assert d["run"]["fired_jaccard"] == 1.0 and d["after_split"]["median_rel_error_common"] == 0.0
    assert d["rti_alias"] == "dds_data_reader_deadline_missed_total"
    assert trace.status_counts(until=20.0)["requested_deadline_missed"].get("reader|t2|C", 0) == 0


def test_dds_timeliness_label():
    from saag.simulation.runtime.labels import runtime_labels
    out = runtime_labels(chain(), candidates=["A", "C"], seeds=[1],
                         settings={"duration": 30.0, "deployment_model": "none",
                                   "qos_semantics": "dds"})
    # Losing the source starves both downstream readers of their deadlines.
    assert out["records"]["A"]["i_timeliness"]["mean"] > 1.0
    assert out["records"]["C"]["i_timeliness"]["mean"] == pytest.approx(0.0, abs=0.1)


def test_result_json_round_trip():
    r = run(chain(), fault_node="A", qos_semantics="dds")
    d = json.loads(json.dumps(r.to_dict()))
    assert d["dds_status"]["statuses"]["requested_deadline_missed"]["reader|t1|B"]["post"] == 4
