"""
Runtime fault library and deployment-in-the-data-path (saag/simulation/runtime/).

Each test pins one fault mode or deployment semantic on a small hand-built
system. The defaults never construct the controller; that is pinned by
tests/test_simulation_golden_defaults.py.
"""

import logging

import networkx as nx
import pytest

pytest.importorskip("simpy")

from saag.simulation._stats import percentile  # noqa: E402
from saag.simulation.message_flow_simulator import MessageFlowSimulator  # noqa: E402
from saag.simulation.runtime.faults import FaultSpec, RuntimeFaultMode  # noqa: E402
from saag.simulation.runtime.workload import MMPP, apply_zipf_rates  # noqa: E402

logging.disable(logging.WARNING)

D = 40.0


def system(brokers=("BR1",), hosts_a=("H1",), hosts_b=("H2",), hosts_c=("H2",),
           links=(), cores=None, hotstandby=False):
    """A -> t1 -> B -> t2 -> C, brokers routing both topics, apps on hosts."""
    g = nx.DiGraph()
    g.add_node("A", type="Application", app_type="sensor", hotstandby=hotstandby)
    g.add_node("B", type="Application", app_type="processor")
    g.add_node("C", type="Application", app_type="actuator")
    for t in ("t1", "t2"):
        g.add_node(t, type="Topic", frequency=10)
    g.add_edge("A", "t1", type="PUBLISHES_TO")
    g.add_edge("B", "t1", type="SUBSCRIBES_TO")
    g.add_edge("B", "t2", type="PUBLISHES_TO")
    g.add_edge("C", "t2", type="SUBSCRIBES_TO")
    for b in brokers:
        g.add_node(b, type="Broker")
        g.add_edge(b, "t1", type="ROUTES")
        g.add_edge(b, "t2", type="ROUTES")
    hosts = set(hosts_a) | set(hosts_b) | set(hosts_c) | {h for link in links for h in link}
    for h in sorted(hosts):
        g.add_node(h, type="Node", **({"cpu_cores": cores} if cores else {}))
    for app, hs in (("A", hosts_a), ("B", hosts_b), ("C", hosts_c)):
        for h in hs:
            g.add_edge(app, h, type="RUNS_ON")
    for a, b in links:
        g.add_edge(a, b, type="CONNECTS_TO")
    return g


def run(g, faults=None, **kw):
    kw.setdefault("deployment_model", "stations")
    return MessageFlowSimulator(g, duration=D, seed=3, faults=faults,
                                rng_streams="per_process", **kw).run()


def post(r, app):
    return r.subscriber_stats[app].received_post_fault


def crash(target, start=20.0, mode="crash", duration=None, **params):
    return FaultSpec(target, mode, start, duration, params)


# ── Brokers on the data path ─────────────────────────────────────────────────

def test_single_broker_crash_silences_its_topics():
    r = run(system(), [crash("BR1")])
    assert post(r, "B") == 0 and post(r, "C") == 0
    assert "BR1" in r.labeled_node_ids


def test_broker_failover_after_lease():
    r = run(system(brokers=("BR1", "BR2")), [crash("BR1")])
    events = [e for e in r.fault_event.runtime_events if e["event"] == "reroute"]
    assert events and all(e["t"] == pytest.approx(21.0) for e in events)  # 1 s lease
    # ~19 of the 20 post-fault seconds are served by BR2.
    assert 160 <= post(r, "B") <= 200


def test_broker_is_unobservable_without_stations():
    r = run(system(), [crash("BR1")], deployment_model="none")
    assert post(r, "B") > 150
    assert "BR1" not in r.labeled_node_ids


# ── Hosts ────────────────────────────────────────────────────────────────────

def test_host_crash_kills_residents():
    r = run(system(), [crash("H1")])
    assert post(r, "B") == 0
    assert "A" in r.fault_event.cascade_silenced_publishers
    assert "H1" in r.labeled_node_ids


def test_replica_aware_host_crash_spares_replicated_component():
    g = system(hosts_a=("H1", "H3"))
    assert post(run(g, [crash("H1")]), "B") == 0
    assert post(run(g, [crash("H1")], replica_aware=True), "B") > 150


def test_colocation_contention_slowdown_hurts_the_neighbour():
    """B and C share one core on H2; slowing B delays C's service too.

    At rho = 0.3 each the shared core runs at 0.6. Slowing B 2.5x pushes it past
    1.0, so C -- whose own service time never changed -- queues behind B.
    """
    g = system(cores=1)
    kw = dict(target_utilization=0.3)
    base = run(g, [crash("A", start=D + 1)], **kw)     # a fault that never fires
    slow = run(g, [crash("B", mode="slowdown", k=2.5)], **kw)
    p95 = lambda r: percentile(r.topic_stats["t2"].latency_samples, 95)  # noqa: E731
    assert p95(slow) > 3 * p95(base)
    # Alone on its own host, C does not notice B's slowdown.
    apart = system(hosts_c=("H3",), cores=1)
    assert p95(run(apart, [crash("B", mode="slowdown", k=2.5)], **kw)) < 1.5 * p95(base)


# ── Fault modes ──────────────────────────────────────────────────────────────

def test_transient_crash_recovers():
    r = run(system(), [crash("A", mode="transient_crash", duration=5.0)])
    assert 100 <= post(r, "B") <= 160            # ~15 of 20 post seconds
    assert r.fault_event.time_to_recover_s is not None
    assert r.fault_event.time_to_recover_s <= 2.0


def test_transient_crash_with_reactive_relay_resumes():
    r = run(system(), [crash("A", mode="transient_crash", duration=5.0)],
            behavior_model="reactive")
    assert post(r, "C") > 100
    assert r.fault_event.stalled_apps == []      # B stalled, then recovered


def test_freeze_buffers_then_drains():
    r = run(system(), [crash("B", mode="freeze", duration=3.0)])
    # B serves nothing while frozen, but its queue keeps the samples and it
    # catches up afterwards: few are lost outright.
    assert post(r, "B") >= 180


def test_omission_halves_the_feed():
    r = run(system(), [crash("A", mode="omission", p=0.5)])
    assert 70 <= post(r, "B") <= 130


def test_babbling_multiplies_publication():
    r = run(system(), [crash("A", mode="babbling", k=3.0)])
    assert r.topic_stats["t1"].published_post == pytest.approx(600, rel=0.05)


def test_buffer_leak_drives_overflows():
    """Freeze B so its queue fills, while the buffer leaks below its depth."""
    r = run(system(), [crash("B", mode="freeze", duration=15.0),
                       crash("B", mode="buffer_leak", beta=20.0)])
    assert r.topic_stats["t1"].queue_overflows_post > 0


def test_redelivery_storm_resends_history():
    r = run(system(), [crash("A", mode="redelivery_storm", copies=3)])
    assert r.topic_stats["t1"].replayed_total == 3 * 10   # history_depth 10, 1 reader


def test_link_flap_drops_cross_host_traffic():
    """Brokerless, so A's samples cross the H1-H2 link directly."""
    g = system(brokers=(), hosts_a=("H1",), hosts_b=("H2",), hosts_c=("H2",),
               links=[("H1", "H2")])
    flap = run(g, [crash("H1|H2", mode="link_flap", mean_good_s=1.0, mean_bad_s=1.0)])
    b = flap.subscriber_stats["B"]
    assert b.received_post_fault < 0.8 * b.received_pre_fault
    # C is co-located with B: the link never carries its feed.
    c = flap.subscriber_stats["C"]
    assert c.received_post_fault >= 0.6 * c.received_pre_fault
    assert any(e["event"] == "link_bad" for e in flap.fault_event.runtime_events)


def test_host_crash_partitions_over_the_network():
    """A brokerless path through H1 is cut when H1 goes down, killing nothing else."""
    g = system(brokers=(), hosts_a=("H3",), hosts_b=("H2",), hosts_c=("H2",),
               links=[("H3", "H1"), ("H1", "H2")])
    r = run(g, [crash("H1")])
    assert post(r, "B") == 0
    assert "A" not in r.fault_event.cascade_silenced_publishers


def test_hot_standby_takes_over():
    g = system(hotstandby=True)
    r = run(g, [crash("A")], hot_standby=True, failover_delay_s=2.0)
    events = [e for e in r.fault_event.runtime_events if e["event"] == "failover"]
    assert events and events[0]["t"] == pytest.approx(22.0)
    assert 150 <= post(r, "B") <= 190
    assert post(run(g, [crash("A")]), "B") == 0


# ── Spec validation and records ──────────────────────────────────────────────

def test_fault_spec_validation():
    with pytest.raises(ValueError):
        FaultSpec("A", "transient_crash", 1.0)            # needs a duration
    with pytest.raises(ValueError):
        FaultSpec("A", "link_flap", 1.0)                  # needs 'a|b'
    with pytest.raises(ValueError):
        FaultSpec("A", "melt", 1.0)
    spec = FaultSpec.from_dict({"target": "A", "mode": "slowdown", "start_s": 2, "params": {"k": 2}})
    assert spec.mode is RuntimeFaultMode.SLOWDOWN and spec.to_dict()["params"] == {"k": 2}
    with pytest.raises(ValueError):
        MessageFlowSimulator(system(), fault_node="A", faults=[crash("A")])


def test_fault_node_becomes_a_crash_spec_under_the_runtime():
    r = MessageFlowSimulator(system(), duration=D, fault_node="BR1", seed=3,
                             deployment_model="stations").run()
    assert r.runtime_config["faults"] == [crash("BR1").to_dict()]
    assert post(r, "B") == 0


def test_paired_estimator_with_runtime_faults():
    cache = {}
    r = run(system(brokers=("BR1", "BR2")), [crash("BR2")], estimator="paired",
            baseline_cache=cache)
    # BR2 routes nothing until BR1 fails, so losing it is harmless.
    assert r.fault_event.i_dyn_paired == pytest.approx(0.0, abs=1e-12)


# ── Workload ─────────────────────────────────────────────────────────────────

def test_mmpp_keeps_the_declared_mean_rate():
    r = run(system(), None, deployment_model="none", workload_model="mmpp",
            burst_ratio=4.0, burst_fraction=0.1, mean_burst_s=1.0)
    published = r.topic_stats["t1"].total_published
    assert published == pytest.approx(10 * D, rel=0.15)
    assert r.runtime_config["workload_model"] == "mmpp"


def test_mmpp_validation():
    with pytest.raises(ValueError):
        MMPP(burst_ratio=6.0, burst_fraction=0.2)
    with pytest.raises(ValueError):
        MMPP(burst_fraction=0.0)


def test_zipf_preserves_total_rate():
    g = system()
    g.add_node("t3", type="Topic", frequency=10)
    z = apply_zipf_rates(g)
    rates = sorted((z.nodes[t]["frequency"] for t in ("t1", "t2", "t3")), reverse=True)
    assert sum(rates) == pytest.approx(30.0)
    assert rates[0] == pytest.approx(2 * rates[1])
    assert g.nodes["t1"]["frequency"] == 10          # input graph untouched
