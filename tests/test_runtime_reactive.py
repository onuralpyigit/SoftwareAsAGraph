"""
Reactive runtime behaviour in MessageFlowSimulator (saag/simulation/runtime/).

The open-loop engine cannot carry a fault past one hop: a starved relay keeps
publishing on schedule. These tests pin the reactive model's propagation, the
trigger policies, per-process RNG streams and the paired estimator, and that
the defaults still behave exactly as before.
"""

import logging

import networkx as nx
import pytest

pytest.importorskip("simpy")

from saag.simulation.message_flow_simulator import (  # noqa: E402
    MessageFlowSimulator,
    surviving_delivery_rate,
)
from saag.simulation.runtime.behavior import (  # noqa: E402
    ReactiveBehavior,
    TriggerPolicy,
    policy_for,
    staleness_s,
)

logging.disable(logging.WARNING)

DURATION = 40.0


def chain():
    """A -> t1 -> B -> t2 -> C, plus a broker and a host that this engine ignores."""
    g = nx.DiGraph()
    g.add_node("A", type="Application", app_type="sensor")
    g.add_node("B", type="Application", app_type="processor")
    g.add_node("C", type="Application", app_type="actuator")
    for t in ("t1", "t2"):
        g.add_node(t, type="Topic", frequency=10)
    g.add_node("BR", type="Broker")
    g.add_edge("A", "t1", type="PUBLISHES_TO")
    g.add_edge("B", "t1", type="SUBSCRIBES_TO")
    g.add_edge("B", "t2", type="PUBLISHES_TO")
    g.add_edge("C", "t2", type="SUBSCRIBES_TO")
    g.add_edge("BR", "t1", type="ROUTES")
    g.add_edge("BR", "t2", type="ROUTES")
    return g


def fusion(trigger):
    """S1, S2 -> F -> sink; F fuses both inputs under `trigger`."""
    g = nx.DiGraph()
    for s in ("S1", "S2"):
        g.add_node(s, type="Application", app_type="sensor")
    g.add_node("F", type="Application", trigger=trigger)
    g.add_node("K", type="Application", app_type="actuator")
    for t in ("ta", "tb", "tout"):
        g.add_node(t, type="Topic", frequency=10)
    g.add_edge("S1", "ta", type="PUBLISHES_TO")
    g.add_edge("S2", "tb", type="PUBLISHES_TO")
    g.add_edge("F", "ta", type="SUBSCRIBES_TO")
    g.add_edge("F", "tb", type="SUBSCRIBES_TO")
    g.add_edge("F", "tout", type="PUBLISHES_TO")
    g.add_edge("K", "tout", type="SUBSCRIBES_TO")
    return g


def run(g, fault, **kw):
    return MessageFlowSimulator(g, duration=DURATION, fault_node=fault, seed=7, **kw).run()


def received_post(result, app):
    return result.subscriber_stats[app].received_post_fault


# ── Propagation ──────────────────────────────────────────────────────────────

def test_open_loop_keeps_one_hop_semantics():
    """The historical engine: C stays fed when the source dies, so I(A) < I(B)."""
    g = chain()
    ra, rb = run(g, "A"), run(g, "B")
    assert received_post(ra, "C") > 0
    assert ra.fault_event.i_dyn < rb.fault_event.i_dyn
    assert ra.runtime_config == {}


def test_reactive_source_fault_silences_the_sink():
    g = chain()
    ra = run(g, "A", behavior_model="reactive")
    fe = ra.fault_event
    # C keeps receiving only B's last few messages while B's staleness window
    # (3 periods of t1) runs out.
    assert received_post(ra, "C") <= 5
    assert fe.stalled_apps == ["B"]
    assert 0.0 <= fe.time_to_stall_s["B"] <= 0.5
    assert fe.cascade_hops == 1
    rb = run(g, "B", behavior_model="reactive")
    # Losing the source now costs as much as losing the relay (the residual is
    # B's last messages, delivered inside its staleness window).
    assert fe.i_dyn == pytest.approx(rb.fault_event.i_dyn, abs=0.02)
    assert ra.runtime_config == {"behavior_model": "reactive", "staleness_periods": 3.0}


def test_reactive_sink_fault_stalls_nobody():
    r = run(chain(), "C", behavior_model="reactive")
    assert r.fault_event.stalled_apps == []
    assert received_post(r, "B") > 0


# ── Trigger policies ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("trigger, sink_fed", [("all_of", False), ("any_of", True)])
def test_fusion_policy(trigger, sink_fed):
    r = run(fusion(trigger), "S1", behavior_model="reactive")
    assert (received_post(r, "K") > 50) is sink_fed
    assert (r.fault_event.stalled_apps == ["F"]) is (not sink_fed)


def test_k_of_n_degrades_instead_of_stalling():
    r = run(fusion("k_of_n:2"), "S1", behavior_model="reactive")
    # One of two required inputs left: F emits about half its schedule.
    k = r.subscriber_stats["K"]
    ratio = k.received_post_fault / k.received_pre_fault
    assert 0.4 < ratio < 0.6
    assert r.fault_event.stalled_apps == []


def test_policy_resolution():
    assert policy_for({"app_type": "sensor"}).kind == "periodic"
    assert policy_for({"app_type": "controller"}).kind == "all_of"
    assert policy_for({"app_type": "gateway"}).kind == "any_of"
    assert policy_for({}).kind == "any_of"
    assert policy_for({"app_type": "sensor", "trigger": "k_of_n:3"}) == TriggerPolicy("k_of_n", 3)
    with pytest.raises(ValueError):
        TriggerPolicy.parse("sometimes")
    with pytest.raises(ValueError):
        TriggerPolicy.parse("k_of_n:0")


def test_staleness_window():
    assert staleness_s({"deadline_ms": 500}, 10.0) == pytest.approx(0.5)
    # A deadline shorter than three periods is floored: it cannot be met at all.
    assert staleness_s({"deadline_ms": 50}, 10.0) == pytest.approx(0.3)
    assert staleness_s({}, 10.0) == pytest.approx(0.3)
    assert staleness_s({"deadline_ms": None}, 2.0) == pytest.approx(1.5)


def test_unpublished_topic_is_not_a_required_input():
    g = fusion("all_of")
    g.add_node("ghost", type="Topic", frequency=10)
    g.add_edge("F", "ghost", type="SUBSCRIBES_TO")
    b = ReactiveBehavior.build(
        g, {"F": ["ta", "tb", "ghost"]}, {"F": {"tout"}, "S1": {"ta"}, "S2": {"tb"}},
        {"ta": 10.0, "tb": 10.0, "ghost": 10.0})
    assert sorted(b.apps["F"].inputs) == ["ta", "tb"]


def test_input_goes_stale_exactly_at_the_window():
    b = ReactiveBehavior.build(
        fusion("all_of"), {"F": ["ta", "tb"]}, {"F": {"tout"}, "S1": {"ta"}, "S2": {"tb"}},
        {"ta": 10.0, "tb": 10.0})
    b.on_delivery("F", "ta", 1.0)
    b.on_delivery("F", "tb", 1.0)
    assert b.emit_share("F", 1.299) == 1.0        # inside the 3-period (0.3 s) window
    assert b.emit_share("F", 1.301) == 0.0        # past it


def test_self_published_topic_is_not_a_required_input():
    b = ReactiveBehavior.build(
        fusion("all_of"), {"F": ["ta", "tout"]}, {"F": {"tout"}, "S1": {"ta"}},
        {"ta": 10.0, "tout": 10.0})
    assert list(b.apps["F"].inputs) == ["ta"]


# ── RNG streams and the paired estimator ─────────────────────────────────────

def test_paired_per_process_no_op_fault_is_exactly_zero():
    """A fault that touches no traffic (a broker, invisible to this engine)."""
    r = run(chain(), "BR", rng_streams="per_process", estimator="paired")
    assert r.fault_event.i_dyn_paired == 0.0


def test_paired_baseline_is_cached_across_faults():
    cache = {}
    g = chain()
    run(g, "A", estimator="paired", rng_streams="per_process", baseline_cache=cache)
    run(g, "B", estimator="paired", rng_streams="per_process", baseline_cache=cache)
    assert list(cache) == [(7, DURATION / 2)]


def test_surviving_rate_matches_the_windowed_rate():
    r = run(chain(), "B", behavior_model="reactive")
    fe = r.fault_event
    for window, expected in (("pre", fe.delivery_rate_before), ("post", fe.delivery_rate_after)):
        got = surviving_delivery_rate(r.topic_stats, r.subscriber_stats, "B", window)
        assert got == pytest.approx(expected, abs=1e-12)


def test_default_dict_has_no_runtime_keys():
    d = run(chain(), "A").to_dict()
    assert "runtime_config" not in d
    assert "i_dyn_paired" not in d["fault_event"]
    assert "stalled_apps" not in d["fault_event"]


def test_per_topic_i_dyn_excludes_the_faulted_reader():
    """B is t1's only reader: with B gone, t1 has no surviving demand at all."""
    r = run(chain(), "B")
    assert "t1" not in r.fault_event.per_topic_i_dyn
    assert r.fault_event.per_topic_i_dyn["t2"] == pytest.approx(1.0)


@pytest.mark.parametrize("kw", [{"behavior_model": "x"}, {"rng_streams": "x"}, {"estimator": "x"}])
def test_rejects_unknown_options(kw):
    with pytest.raises(ValueError):
        MessageFlowSimulator(chain(), **kw)
