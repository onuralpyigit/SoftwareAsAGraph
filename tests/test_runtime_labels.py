"""
Runtime label sweep (saag/simulation/runtime/labels.py) and FaultInjector's
opt-in ``failure_model="threshold_cdf"``.
"""

import logging

import networkx as nx
import pytest

pytest.importorskip("simpy")

from saag.simulation.fault_injector import FaultInjector  # noqa: E402
from saag.simulation.runtime.labels import (  # noqa: E402
    LABEL_NAMES,
    _Topology,
    ref_delivery_first_order,
    ref_flow_policy,
    ref_flow_reach,
    runtime_labels,
)
from saag.simulation.runtime.uncertainty import rank_stability, sample_settings  # noqa: E402

logging.disable(logging.WARNING)


def chain():
    g = nx.DiGraph()
    g.add_node("A", type="Application", app_type="sensor")
    g.add_node("B", type="Application", app_type="processor")
    g.add_node("C", type="Application", app_type="actuator", criticality="HIGH")
    g.add_node("D", type="Application", app_type="monitor")
    for t in ("t1", "t2"):
        g.add_node(t, type="Topic", frequency=10)
    g.add_edge("A", "t1", type="PUBLISHES_TO")
    g.add_edge("B", "t1", type="SUBSCRIBES_TO")
    g.add_edge("D", "t1", type="SUBSCRIBES_TO")
    g.add_edge("B", "t2", type="PUBLISHES_TO")
    g.add_edge("C", "t2", type="SUBSCRIBES_TO")
    return g


SETTINGS = {"duration": 30.0, "deployment_model": "none"}


@pytest.fixture(scope="module")
def labels():
    return runtime_labels(chain(), seeds=[1, 2], settings=SETTINGS)


def test_sweep_shape(labels):
    assert set(labels["records"]) == {"A", "B", "C", "D"}
    assert labels["labeled_dimensions"] == ["i_delivery", "i_delivery_consumer",
                                            "i_flow", "i_timeliness"]
    assert labels["records"]["A"]["i_recovery"] is None      # permanent fault
    assert set(labels["label_stability"]) == set(labels["labeled_dimensions"])
    assert labels["settings"]["estimator"] == "paired"


def test_sinks_are_not_harmless(labels):
    """I* scores a pure sink 0.0; i_flow charges a dead actuator its own loss."""
    r = labels["records"]
    # Sinks C (HIGH, weight 3) and D (LOW, weight 1): C dead -> 3/4 of flow lost.
    assert r["C"]["i_flow"]["mean"] == pytest.approx(0.75, abs=0.02)
    assert r["D"]["i_flow"]["mean"] == pytest.approx(0.25, abs=0.02)
    fi = FaultInjector(chain(), seeds=[1]).run(node_types=["Application"]).records
    assert fi["C"].impact_score == 0.0


def test_reactive_source_dominates(labels):
    r = labels["records"]
    assert r["A"]["i_flow"]["mean"] > r["B"]["i_flow"]["mean"] > 0.5


def test_references():
    topo = _Topology(chain())
    # A silences t1 directly: B and D (2 of 3 reader-copies, rate-equal) lose it.
    assert ref_delivery_first_order(topo, "A") == pytest.approx(2 / 3)
    # B's own readership leaves the denominator: C's 10 Hz of D's + C's 20 Hz.
    assert ref_delivery_first_order(topo, "B") == pytest.approx(1 / 2)
    assert ref_flow_reach(topo, "A") == pytest.approx(1.0)
    assert ref_flow_policy(topo, chain(), "A") == pytest.approx(1.0)
    # B stalls nothing upstream; C loses everything, D nothing.
    assert ref_flow_policy(topo, chain(), "B") == pytest.approx(0.75)
    assert ref_flow_reach(topo, "D") == pytest.approx(0.25)


def test_open_loop_delivery_equals_its_first_order_reference():
    """The Eq. 7 identity: without propagation, I_dyn is the first-order term."""
    out = runtime_labels(chain(), seeds=[1], settings={**SETTINGS, "behavior_model": "open_loop"})
    for c, rec in out["records"].items():
        assert rec["i_delivery"]["mean"] == pytest.approx(rec["ref_delivery_first_order"], abs=0.03), c


def test_sweep_requires_paired_estimator():
    with pytest.raises(ValueError):
        runtime_labels(chain(), settings={"estimator": "windows"})


def test_transient_fault_reports_recovery():
    out = runtime_labels(chain(), candidates=["A"], seeds=[1], settings=SETTINGS,
                         fault={"mode": "transient_crash", "duration_s": 3.0})
    rec = out["records"]["A"]["i_recovery"]
    assert rec is not None and rec["mean"] <= 2.0


def test_parallel_matches_serial():
    a = runtime_labels(chain(), seeds=[1, 2], settings=SETTINGS, workers=1)
    b = runtime_labels(chain(), seeds=[1, 2], settings=SETTINGS, workers=2)
    assert a["records"] == b["records"]


def test_uncertainty_helpers():
    import random
    draw = sample_settings(random.Random(0))
    assert 0.45 <= draw["target_utilization"] <= 0.85
    assert draw["service_distribution"] in ("exponential", "uniform")
    assert sample_settings(random.Random(0)) == draw
    st = rank_stability([{"a": 1.0, "b": 0.5, "c": 0.1}, {"a": 0.9, "b": 0.6, "c": 0.0}])
    assert st["worst_spearman"] == pytest.approx(1.0)
    assert st["n_draws"] == 2


def test_label_names_cover_records(labels):
    assert set(LABEL_NAMES) <= set(labels["records"]["A"])


# ── FaultInjector threshold_cdf ──────────────────────────────────────────────

def fan(k_lost, n=10):
    """Publishers P0..P{n-1} -> one topic each -> one relay R -> topic -> sink S.

    Failing a Library L used by k of them kills those k publishers, so R's
    feed loss is exactly k/n and the threshold behaviour can be read off.
    """
    g = nx.DiGraph()
    g.add_node("L", type="Library")
    for i in range(n):
        g.add_node(f"P{i}", type="Application")
        g.add_node(f"t{i}", type="Topic")
        g.add_edge(f"P{i}", f"t{i}", type="PUBLISHES_TO")
        g.add_edge("R", f"t{i}", type="SUBSCRIBES_TO")
        if i < k_lost:
            g.add_edge(f"P{i}", "L", type="USES")
    g.add_node("R", type="Application")
    g.add_node("tr", type="Topic")
    g.add_node("S", type="Application")
    g.add_edge("R", "tr", type="PUBLISHES_TO")
    g.add_edge("S", "tr", type="SUBSCRIBES_TO")
    return g


def _relay_fail_rate(k, **kw):
    seeds = list(range(200))
    res = FaultInjector(fan(k), seeds=seeds, qos_factor_mode="none", **kw).run(node_ids=["L"])
    # I = mean(R's loss k/10, S's loss): (k/10 + 1)/2 >= 0.55 when R failed,
    # k/20 <= 0.25 when it survived.
    rec = res.records["L"]
    return sum(1 for s in seeds if rec.seed_impact_scores[s] > 0.5) / len(seeds)


def test_step_model_has_a_cliff():
    assert _relay_fail_rate(1) == 0.0          # loss 0.1 < 0.2
    assert _relay_fail_rate(2) == 1.0          # loss 0.2: wave 0 fails at p=1
    assert _relay_fail_rate(9) == 1.0


def test_threshold_cdf_is_graded():
    rates = [_relay_fail_rate(k, failure_model="threshold_cdf") for k in (1, 2, 3, 4, 5)]
    # P(fail) = F_theta(k/10) with theta ~ U(0.1, 0.5): 0, .25, .5, .75, 1.
    for got, want in zip(rates, (0.0, 0.25, 0.5, 0.75, 1.0)):
        assert got == pytest.approx(want, abs=0.1)


def test_threshold_cdf_records_its_config():
    res = FaultInjector(fan(3), seeds=[1], failure_model="threshold_cdf").run(node_ids=["L"])
    assert res.to_dict()["labeler_config"] == {"failure_model": "threshold_cdf",
                                               "threshold_range": [0.1, 0.5]}
    assert "labeler_config" not in FaultInjector(fan(3), seeds=[1]).run(node_ids=["L"]).to_dict()
    with pytest.raises(ValueError):
        FaultInjector(fan(3), failure_model="soft")
    with pytest.raises(ValueError):
        FaultInjector(fan(3), threshold_range=(0.5, 0.2))
