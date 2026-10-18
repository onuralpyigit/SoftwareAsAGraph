"""saag.core.network: the shared physical-network model (routing, load, Rule 7)."""

import pytest

from saag.core.network import (
    Fabric, derive_node_to_device, evaluate, link_key, place, topic_load_mbps,
)


def _two_racks(cores=("R1",), host_cap=None, uplink_cap=None) -> Fabric:
    """H1,H2 behind S1; H3 behind S2; S1/S2 uplinked to every core."""
    links = [("H1", "S1", host_cap), ("H2", "S1", host_cap), ("H3", "S2", host_cap)]
    for r in cores:
        links += [("S1", r, uplink_cap), ("S2", r, uplink_cap)]
    return Fabric(["H1", "H2", "H3", "S1", "S2", *cores], links)


def test_topic_load_is_size_times_frequency():
    assert topic_load_mbps({"size": 1000, "frequency": 100}) == pytest.approx(0.8)
    assert topic_load_mbps({"message_size": 1000, "topic_frequency": 10}) == pytest.approx(0.08)
    assert topic_load_mbps({"size": 1000}) == pytest.approx(0.008)   # 1 Hz default


def test_place_is_lowest_live_host():
    assert place(["N2", "N1"]) == "N1"
    assert place(["N2", "N1"], down={"N1"}) == "N2"
    assert place(["N1"], down={"N1"}) is None


def test_parallel_links_add_capacity_and_unbounded_wins():
    f = Fabric([], [("A", "B", 10.0), ("B", "A", 5.0), ("A", "C", 1.0), ("A", "C", None)])
    assert f.capacity[link_key("A", "B")] == 15.0
    assert f.capacity[link_key("A", "C")] is None


def test_islands_respect_down_vertices_and_cut_links():
    f = _two_racks()
    assert len(set(f.islands().values())) == 1
    isl = f.islands(down={"R1"})
    assert isl["H1"] == isl["H2"] != isl["H3"]
    assert "R1" not in isl
    isl = f.islands(cut={link_key("H3", "S2")})
    assert isl["H3"] != isl["H1"]


def test_hops_counts_shortest_paths_and_skips_down_vertices():
    f = _two_racks(cores=("R1", "R2"))
    hops = f.hops("H1")
    assert hops["H1"] == 0 and hops["H2"] == 2 and hops["H3"] == 4
    assert "H3" not in f.hops("H1", down={"R1", "R2"})


def test_ecmp_splits_evenly_over_two_cores():
    f = _two_racks(cores=("R1", "R2"))
    shares = f.ecmp_shares("H1", "H3", set(), set())
    assert shares[("S1", "R1")] == pytest.approx(0.5)
    assert shares[("S1", "R2")] == pytest.approx(0.5)
    assert shares[("H1", "S1")] == pytest.approx(1.0)
    assert f.ecmp_shares("H1", "H1", set(), set()) == {}
    assert f.ecmp_shares("H1", "H3", {"R1", "R2"}, set()) is None


def _pubsub(fabric, flows, routers, down=(), **kw):
    hosts = {"Pub": ["H1"], "Pub2": ["H2"], "B1": ["H1"], "B2": ["H3"],
             "Sub": ["H3"], "Sub2": ["H3"], "Sub3": ["H2"]}
    brokered = {t for t, r in routers.items() if r is not None}
    live = {t: [b for b in (r or []) if b not in down] for t, r in routers.items()}
    return evaluate(fabric, flows, hosts, live, brokered, {"T": 8.0, "D": 4.0}, down=down, **kw)


def test_broker_leg_is_counted_once_across_subscribers():
    f = _two_racks(cores=("R1",))
    flows = [("Pub2", "T", "Sub"), ("Pub2", "T", "Sub2"), ("Pub2", "T", "Sub3")]
    st = _pubsub(f, flows, {"T": ["B1"]})
    # Pub2 (H2) -> B1 (H1) once; B1 -> Sub, Sub2 (H3) twice; B1 -> Sub3 (H2) once.
    assert st.link_load[link_key("H2", "S1")] == pytest.approx(8.0 + 8.0)
    assert st.link_load[link_key("S1", "R1")] == pytest.approx(16.0)
    assert all(v == 0.0 for v in st.flow_loss.values())


def test_same_host_flow_adds_no_load():
    f = _two_racks()
    st = _pubsub(f, [("Pub", "D", "B1")], {"D": None})   # both on H1, brokerless
    assert st.link_load == {}
    assert st.flow_loss[("Pub", "D", "B1")] == 0.0


def test_unbounded_links_have_no_utilisation_and_no_loss():
    st = _pubsub(_two_racks(), [("Pub", "T", "Sub")], {"T": ["B1"]})
    assert all(u is None for u in st.link_util.values())
    assert st.flow_loss[("Pub", "T", "Sub")] == 0.0


def test_congested_link_drops_its_excess():
    f = _two_racks(cores=("R1",), uplink_cap=4.0)   # 8 Mbps over 4 Mbps uplinks
    st = _pubsub(f, [("Pub", "T", "Sub")], {"T": ["B1"]})
    assert st.link_util[link_key("S1", "R1")] == pytest.approx(2.0)
    # two congested hops in series, each delivering 1/2
    assert st.flow_loss[("Pub", "T", "Sub")] == pytest.approx(0.75)


def test_ecmp_halves_the_congestion_and_failover_restores_it():
    f = _two_racks(cores=("R1", "R2"), uplink_cap=4.0)
    st = _pubsub(f, [("Pub", "T", "Sub")], {"T": ["B1"]})
    assert st.flow_loss[("Pub", "T", "Sub")] == pytest.approx(0.0)
    st = _pubsub(f, [("Pub", "T", "Sub")], {"T": ["B1"]}, down={"R2"})
    assert st.link_load[link_key("S1", "R1")] == pytest.approx(8.0)
    assert st.flow_loss[("Pub", "T", "Sub")] == pytest.approx(0.75)


def test_unreachable_flow_is_blocked_and_dead_broker_is_logical():
    f = _two_racks()
    st = _pubsub(f, [("Pub", "T", "Sub")], {"T": ["B1"]}, down={"R1"})
    assert st.flow_loss[("Pub", "T", "Sub")] == 1.0
    st = _pubsub(f, [("Pub", "T", "Sub")], {"T": ["B1"]}, down={"B1"})
    assert ("Pub", "T", "Sub") not in st.flow_loss


def test_flow_picks_a_broker_both_ends_can_reach():
    f = _two_racks()
    # B1 on H1 is cut off from the subscriber, B2 on H3 is not reachable from Pub either;
    # with the uplink cut, no broker serves both ends.
    st = _pubsub(f, [("Pub", "T", "Sub")], {"T": ["B1", "B2"]}, cut={link_key("S2", "R1")})
    assert st.flow_loss[("Pub", "T", "Sub")] == 1.0
    st = _pubsub(f, [("Pub", "T", "Sub")], {"T": ["B1", "B2"]})
    assert st.flow_loss[("Pub", "T", "Sub")] == 0.0


def test_reachability_only_mode_skips_load():
    f = _two_racks(uplink_cap=1.0)
    st = _pubsub(f, [("Pub", "T", "Sub")], {"T": ["B1"]}, with_load=False)
    assert st.link_load == {}
    assert st.flow_loss[("Pub", "T", "Sub")] == 0.0


# ── Rule 7 ────────────────────────────────────────────────────────────────────

def test_rule7_single_core_separates_cross_rack_dependencies():
    f = _two_racks(cores=("R1",))
    deps = [("H1", "H3", 0.6), ("H1", "H2", 0.4), ("H3", "H1", 0.9)]
    edges = derive_node_to_device(f, ["S1", "S2", "R1"], deps)
    assert edges[("H1", "R1")] == (0.6, 1)
    assert edges[("H1", "S1")] == (0.6, 2)           # same-rack and cross-rack both cross S1
    assert edges[("H3", "R1")] == (0.9, 1)
    assert ("H1", "S2") in edges and ("H3", "S1") in edges


def test_rule7_redundant_core_derives_no_core_edge():
    f = _two_racks(cores=("R1", "R2"))
    edges = derive_node_to_device(f, ["S1", "S2", "R1", "R2"], [("H1", "H3", 0.6)])
    assert ("H1", "R1") not in edges and ("H1", "R2") not in edges
    assert ("H1", "S1") in edges and ("H1", "S2") in edges


def test_rule7_needs_devices_and_a_connected_pair():
    mesh = Fabric(["N1", "N2", "N3"], [("N1", "N2", None), ("N2", "N3", None)])
    assert derive_node_to_device(mesh, [], [("N1", "N3", 0.5)]) == {}
    f = Fabric(["H1", "H2", "S1", "S2"], [("H1", "S1", None), ("H2", "S2", None)])
    assert derive_node_to_device(f, ["S1", "S2"], [("H1", "H2", 0.5)]) == {}
