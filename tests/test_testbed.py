"""
Emulated testbed (saag/testbed/), calibrator (saag/simulation/calibration.py)
and the fidelity report (saag/validation/fidelity.py).

Everything here runs without Docker or Cyclone DDS: the stub applications run
in-process over `LocalTransport`. The DDS transport itself is exercised only
by the ``testbed``-marked test, which needs ``cyclonedds`` installed.
"""

import json
import logging
import random

import pytest

pytest.importorskip("simpy")

from saag.simulation.calibration import (  # noqa: E402
    TelemetryCalibrator,
    TelemetryTrace,
    fit_service_time,
    infer_trigger_policy,
    make_event,
)
from saag.simulation.message_flow_simulator import MessageFlowSimulator  # noqa: E402
from saag.simulation.runtime.faults import FaultSpec  # noqa: E402
from saag.testbed import orchestrate  # noqa: E402
from saag.testbed.chaos import execute, plan_actions  # noqa: E402
from saag.testbed.compose import compose_spec, container_name  # noqa: E402
from saag.testbed.dds import dds_name, qos_spec  # noqa: E402
from saag.testbed.deployment import DEFAULT_HOST, build_deployment  # noqa: E402
from saag.validation.fidelity import (  # noqa: E402
    compare_fault_response,
    compare_steady_state,
    stalled_in_trace,
)

logging.disable(logging.WARNING)


def scenario(tmp_path, brokers=True):
    """S (sensor, 20 Hz) -> t1 -> P (processor) -> t2 -> K (actuator), on two hosts."""
    data = {
        "nodes": [{"id": "H1", "name": "H1"}, {"id": "H2", "name": "H2"}],
        "brokers": [{"id": "BR", "name": "BR"}] if brokers else [],
        "topics": [
            {"id": "t1", "name": "t1", "frequency": 20, "size": 64,
             "qos": {"reliability": "RELIABLE", "durability": "VOLATILE",
                     "transport_priority": "HIGH"}, "deadline_ms": 500, "history_depth": 10},
            {"id": "t2", "name": "t2", "frequency": 20, "size": 64,
             "qos": {"reliability": "BEST_EFFORT", "durability": "VOLATILE"}},
        ],
        "applications": [
            {"id": "S", "name": "S", "app_type": "sensor"},
            {"id": "P", "name": "P", "app_type": "processor"},
            {"id": "K", "name": "K", "app_type": "actuator"},
        ],
        "libraries": [],
        "relationships": {
            "publishes_to": [{"from": "S", "to": "t1"}, {"from": "P", "to": "t2"}],
            "subscribes_to": [{"from": "P", "to": "t1"}, {"from": "K", "to": "t2"}],
            "routes": ([{"from": "BR", "to": "t1"}, {"from": "BR", "to": "t2"}] if brokers else []),
            "runs_on": [{"from": "S", "to": "H1"}, {"from": "P", "to": "H2"},
                        {"from": "K", "to": "H2"}] + ([{"from": "BR", "to": "H1"}] if brokers else []),
            "connects_to": [{"from": "H1", "to": "H2"}],
            "uses": [],
        },
    }
    p = tmp_path / "chain_system.json"
    p.write_text(json.dumps(data))
    return p


# ── Deployment, QoS, compose ─────────────────────────────────────────────────

def test_deployment_plan(tmp_path):
    plan = build_deployment(scenario(tmp_path))
    assert plan["hosts"]["H1"] == {"apps": ["S"], "brokers": ["BR"]}
    assert plan["apps"]["P"]["policy"] == "all_of"
    assert plan["apps"]["S"]["policy"] == "periodic"
    assert plan["apps"]["P"]["inputs"] == ["t1"]
    # Staleness: max(500 ms deadline, 3 periods of 20 Hz) = 0.5 s.
    assert plan["apps"]["P"]["staleness_s"]["t1"] == pytest.approx(0.5)
    assert plan["apps"]["P"]["service_mean_s"] == pytest.approx(0.65 / 20)
    assert plan["topics"]["t1"]["routers"] == ["BR"]
    assert plan["links"] == [["H1", "H2"]] or plan["links"] == [("H1", "H2")]
    assert DEFAULT_HOST not in plan["hosts"]


def test_qos_mapping():
    spec = qos_spec({"reliability": "RELIABLE", "durability": "TRANSIENT_LOCAL",
                     "history_depth": 5, "deadline_ms": 50, "priority": "HIGH"})
    assert spec == {"reliability": "RELIABLE", "durability": "TRANSIENT_LOCAL",
                    "history_keep_last": 5, "transport_priority": 2, "deadline_ms": 50.0}
    assert "deadline_ms" not in qos_spec({"reliability": "best_effort"})
    assert qos_spec({})["history_keep_last"] == 100
    assert dds_name("radar/tracks-1") == "saag_radar_tracks_1"


def test_compose_spec(tmp_path):
    plan = build_deployment(scenario(tmp_path))
    spec = compose_spec(plan, 30.0)
    assert sorted(spec["services"]) == ["saag-H1", "saag-H2"]
    svc = spec["services"][container_name("H2")]
    assert "NET_ADMIN" in svc["cap_add"]
    assert svc["command"][svc["command"].index("--host") + 1] == "H2"


# ── Chaos plans ──────────────────────────────────────────────────────────────

def test_chaos_plan_docker(tmp_path):
    plan = build_deployment(scenario(tmp_path))
    faults = [FaultSpec("H1", "transient_crash", 5.0, 3.0),
              FaultSpec("P", "slowdown", 2.0, 4.0, {"k": 3}),
              FaultSpec("P", "buffer_leak", 1.0),
              FaultSpec("H1|H2", "link_flap", 1.0, 10.0)]
    cp = plan_actions(faults, plan, 30.0)
    docker = [a.argv for a in cp.actions if a.kind == "docker"]
    assert ["docker", "kill", "saag-H1"] in docker and ["docker", "start", "saag-H1"] in docker
    controls = [(a.t, a.target, a.control) for a in cp.actions if a.kind == "control"]
    assert (2.0, "P", {"slowdown": 3.0}) in controls and (6.0, "P", {}) in controls
    assert [u["mode"] for u in cp.unsupported] == ["buffer_leak"]
    assert any("netem" in a.argv for a in cp.actions if a.argv)
    assert [a.t for a in cp.actions] == sorted(a.t for a in cp.actions)


def test_chaos_plan_local_expands_hosts(tmp_path):
    plan = build_deployment(scenario(tmp_path))
    cp = plan_actions([FaultSpec("H1", "crash", 4.0), FaultSpec("H1|H2", "link_flap", 1.0)],
                      plan, 30.0, local=True)
    assert sorted(a.target for a in cp.actions) == ["BR", "S"]
    assert cp.unsupported[0]["mode"] == "link_flap"


def test_execute_writes_controls_and_runs_commands(tmp_path):
    plan = build_deployment(scenario(tmp_path))
    cp = plan_actions([FaultSpec("P", "freeze", 0.0, 1.0), FaultSpec("H1", "crash", 0.0)], plan, 5.0)
    ran = []
    log = execute(cp, tmp_path, t0=0.0, runner=lambda argv, **kw: ran.append(argv),
                  sleep=lambda s: None, clock=lambda: 100.0)
    assert ran == [["docker", "kill", "saag-H1"]]
    assert json.loads((tmp_path / "P.json").read_text()) == {}        # last action: reset
    assert len(log) == 3


# ── In-process runtime end to end ────────────────────────────────────────────

@pytest.fixture(scope="module")
def local_run(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("tb")
    d = orchestrate.build(scenario(tmp), tmp / "tb", duration_s=6.0)
    orchestrate.run(d, 6.0, [FaultSpec("S", "crash", 3.0)], mode="local")
    return d


def test_local_run_produces_telemetry(local_run):
    meta = json.loads((local_run / "run.json").read_text())
    assert meta["chaos_log"][0]["control"] == "S"
    trace = TelemetryTrace.load((local_run / "telemetry").glob("*.jsonl"))
    kinds = {e["kind"] for e in trace.events}
    assert {"publish", "deliver"} <= kinds


def test_local_run_cascades_like_the_simulator(local_run):
    from saag.testbed.collect import load_run

    observed = load_run(local_run)
    # P stops emitting once t1 has been stale for 0.5 s; K then hears nothing.
    p_emits = observed.times("publish", "P")
    assert p_emits and max(p_emits) < 3.0 + 1.0
    assert stalled_in_trace(observed, 3.0) - {"S"} == {"P"}
    sim = orchestrate.simulate_like(local_run)
    assert stalled_in_trace(sim, 3.0) - {"S"} == {"P"}


def test_local_fidelity_report(local_run):
    rep = orchestrate.fidelity(local_run, warmup_s=0.5)
    m = rep["steady_state"]["metrics"]
    assert m["topic_rate"]["n_compared"] == 2
    assert m["topic_rate"]["median_rel_error"] < 0.15
    assert rep["fault_response"]["stall_jaccard"] == 1.0
    assert (local_run / "fidelity.json").exists()


def test_calibrate_and_resimulate(local_run):
    prof = orchestrate.calibrate(local_run, warmup_s=0.5)
    assert prof["msg_rate_per_sec"]["t1"] == pytest.approx(20.0, rel=0.25)
    assert prof["trigger_policy"].get("S") == "periodic"
    assert "P" in prof["service_time_dist"]
    assert prof["edge_failure_correlation"] == {} and prof["custom_starvation_bounds"] == {}
    rep = orchestrate.fidelity(local_run, calibrated=True, warmup_s=0.5)
    assert rep["calibrated"] is True


# ── Calibrator round trip on a simulated trace ───────────────────────────────

def chain_graph(trigger="all_of", f=20):
    import networkx as nx
    g = nx.DiGraph()
    g.add_node("S1", type="Application", app_type="sensor")
    g.add_node("S2", type="Application", app_type="sensor")
    g.add_node("F", type="Application", trigger=trigger)
    g.add_node("K", type="Application", app_type="actuator")
    for t in ("ta", "tb", "tout"):
        g.add_node(t, type="Topic", frequency=f)
    g.add_edge("S1", "ta", type="PUBLISHES_TO")
    g.add_edge("S2", "tb", type="PUBLISHES_TO")
    g.add_edge("F", "ta", type="SUBSCRIBES_TO")
    g.add_edge("F", "tb", type="SUBSCRIBES_TO")
    g.add_edge("F", "tout", type="PUBLISHES_TO")
    g.add_edge("K", "tout", type="SUBSCRIBES_TO")
    return g


def simulated_trace(trigger, faults=None, duration=60.0):
    res = MessageFlowSimulator(chain_graph(trigger), duration=duration, seed=5, faults=faults,
                               behavior_model="reactive", rng_streams="per_process",
                               record_events=True).run()
    return res, TelemetryTrace.from_records(res.events, start_s=0.0, end_s=duration)


def test_round_trip_recovers_rates_and_service_means():
    res, trace = simulated_trace("all_of")
    prof = TelemetryCalibrator().calibrate(trace)
    for t in ("ta", "tb", "tout"):
        assert prof.msg_rate_per_sec[t] == pytest.approx(20.0, rel=0.05)
    for app in ("F", "K"):
        fit = prof.service_time_dist[app]
        assert fit["mean_s"] == pytest.approx(res.service_time_s[app], rel=0.10)
        assert fit["dist"] == "exponential"


@pytest.mark.parametrize("trigger", ["all_of", "any_of"])
def test_policy_inference_identifies_the_trigger(trigger):
    # S1 drops out for 10 s (partial outage), later both drop (full outage).
    faults = [FaultSpec("S1", "transient_crash", 10.0, 10.0),
              FaultSpec("S1", "transient_crash", 30.0, 10.0),
              FaultSpec("S2", "transient_crash", 30.0, 10.0)]
    _, trace = simulated_trace(trigger, faults)
    policy, evidence = infer_trigger_policy(trace, "F")
    assert policy == trigger, evidence
    assert infer_trigger_policy(trace, "S1")[0] == "periodic"


def test_policy_not_inferred_without_an_outage():
    _, trace = simulated_trace("all_of", duration=20.0)
    policy, evidence = infer_trigger_policy(trace, "F")
    assert policy is None and "reason" in evidence


def test_calibrated_profile_overrides_the_simulator():
    _, trace = simulated_trace("all_of", duration=20.0)
    prof = TelemetryCalibrator(infer_policies=False).calibrate(trace)
    prof.msg_rate_per_sec["ta"] = 5.0
    prof.trigger_policy["F"] = "any_of"
    res = MessageFlowSimulator(chain_graph("all_of"), duration=20.0, seed=5,
                               behavior_model="reactive", telemetry_profile=prof,
                               faults=[FaultSpec("S1", "crash", 10.0)]).run()
    assert res.topic_stats["ta"].total_published == pytest.approx(5.0 * 10, abs=3)
    # Under the overridden any_of policy F keeps going on tb alone.
    assert res.subscriber_stats["K"].received_post_fault > 100


def test_fit_service_time():
    rng = random.Random(1)
    assert fit_service_time([rng.expovariate(10) for _ in range(2000)])["dist"] == "exponential"
    assert fit_service_time([0.1 + 0.001 * rng.random() for _ in range(200)])["dist"] == "lognormal"
    assert fit_service_time([0.1, 0.2]) is None


def test_trace_parsing_and_export(tmp_path):
    res, _ = simulated_trace("all_of", duration=5.0)
    n = res.export_events(tmp_path / "sim.jsonl")
    trace = TelemetryTrace.load([tmp_path / "sim.jsonl"])
    assert len(trace.events) == n > 0
    rec = make_event(1.5, "saag.deliver", "A", "t", latency_ms=3.0)
    assert rec["attributes"]["saag.latency_ms"] == 3.0
    assert TelemetryTrace.from_records([rec, {"name": "other", "time_unix_s": 1}]).events[0]["app"] == "A"
    with pytest.raises(ValueError):
        MessageFlowSimulator(chain_graph(), duration=1.0).run().export_events(tmp_path / "x")


# ── Fidelity helpers ─────────────────────────────────────────────────────────

def test_steady_state_comparison_flags_mismatch():
    _, a = simulated_trace("all_of", duration=20.0)
    fast = MessageFlowSimulator(chain_graph("all_of", f=30), duration=20.0, seed=5,
                                behavior_model="reactive", record_events=True).run()
    b = TelemetryTrace.from_records(fast.events)
    same = compare_steady_state(a, a)
    assert same["metrics"]["topic_rate"]["max_rel_error"] == 0.0
    diff = compare_steady_state(a, b)
    assert diff["metrics"]["topic_rate"]["median_rel_error"] == pytest.approx(1 / 3, abs=0.05)
    assert diff["metrics"]["topic_rate"]["share_within_tolerance"] == 0.0


def test_fault_response_comparison():
    r = compare_fault_response({"a": 3, "b": 2, "c": 1, "d": 0}, {"a": 2.5, "b": 2, "c": 0.5, "d": 0.1},
                               {"x", "y"}, {"y"})
    assert r["spearman"] == pytest.approx(1.0)
    assert r["stall_jaccard"] == 0.5 and r["stalled_only_in_simulation"] == ["x"]
    assert compare_fault_response({"a": 1}, {"a": 1})["spearman"] is None


@pytest.mark.testbed
def test_dds_transport_round_trip():
    pytest.importorskip("cyclonedds")
    import threading
    from saag.testbed.dds import DdsTransport
    tr = DdsTransport(domain_id=91)
    got = threading.Event()
    tr.subscribe("t", "direct", {"reliability": "RELIABLE"}, lambda s: got.set())
    w = tr.writer("t", "direct", {"reliability": "RELIABLE"})
    for i in range(20):
        w({"msg_id": i, "publisher": "p", "created": 0.0, "size": 1})
        if got.wait(0.1):
            break
    tr.close()
    assert got.is_set()
