"""Scenario -> deployment plan for the emulated testbed."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List

from saag.core.graph_io import load_graph
from saag.core.models import QoSPolicy
from saag.simulation.runtime.behavior import STALENESS_PERIODS, policy_for, staleness_s

#: Host given to components that declare no RUNS_ON.
DEFAULT_HOST = "h_default"


def _rate(attrs: Dict[str, Any]) -> float:
    try:
        return float(attrs.get("frequency") or attrs.get("topic_frequency") or 10.0)
    except (TypeError, ValueError):
        return 10.0


def build_deployment(
    scenario: Path,
    target_utilization: float = 0.65,
    staleness_periods: float = STALENESS_PERIODS,
) -> Dict[str, Any]:
    """Resolve everything an agent needs, once, so all agents agree on it."""
    g = load_graph(Path(scenario))
    ntype = {n: d.get("type") for n, d in g.nodes(data=True)}
    pubs, subs, routers, hosts_of = (defaultdict(list) for _ in range(4))
    for u, v, d in g.edges(data=True):
        t = d.get("type")
        if t == "PUBLISHES_TO":
            pubs[v].append(u)
        elif t == "SUBSCRIBES_TO":
            subs[v].append(u)
        elif t == "ROUTES":
            routers[v].append(u)
        elif t == "RUNS_ON":
            hosts_of[u].append(v)

    topics: Dict[str, Any] = {}
    for t, d in sorted(g.nodes(data=True)):
        if d.get("type") != "Topic":
            continue
        qos = QoSPolicy.from_node_attrs(d)
        topics[t] = {
            "rate_hz": _rate(d),
            "reliability": qos.reliability,
            "durability": qos.durability,
            "deadline_ms": d.get("deadline_ms"),
            "history_depth": d.get("history_depth"),
            "priority": qos.transport_priority,
            "size": int(d.get("size") or 64),
            "publishers": sorted(pubs.get(t, [])),
            "routers": sorted(routers.get(t, [])),
        }

    published = {t for t, ps in pubs.items() if ps}
    apps: Dict[str, Any] = {}
    for a in sorted(n for n, t in ntype.items() if t in ("Application", "Library")):
        out = sorted(t for t, ps in pubs.items() if a in ps and t in topics)
        ins = sorted(t for t, ss in subs.items() if a in ss and t in topics)
        if not out and not ins:
            continue
        required = sorted((set(ins) & published) - set(out))
        load = sum(topics[t]["rate_hz"] for t in ins)
        apps[a] = {
            "publishes": out,
            "subscribes": ins,
            "inputs": required,
            "policy": "periodic" if not required else (
                g.nodes[a].get("trigger") or policy_for(g.nodes[a]).kind),
            "staleness_s": {t: staleness_s(g.nodes[t], topics[t]["rate_hz"], staleness_periods)
                            for t in required},
            "service_mean_s": (target_utilization / load) if load > 0 else 0.0,
            "per_publisher_rate": {t: topics[t]["rate_hz"] / len(topics[t]["publishers"])
                                   for t in out},
            "phase_index": {t: topics[t]["publishers"].index(a) for t in out},
            "hotstandby": bool(g.nodes[a].get("hotstandby")),
            # Every host it RUNS_ON: one instance each, lowest-id live one active.
            "hosts": sorted(hosts_of.get(a, [])) or [DEFAULT_HOST],
        }

    brokers: Dict[str, Any] = {}
    for b in sorted(n for n, t in ntype.items() if t == "Broker"):
        routed = sorted(t for t, rs in routers.items() if b in rs and t in topics)
        load = sum(topics[t]["rate_hz"] for t in routed)
        brokers[b] = {"routes": routed,
                      "service_mean_s": (target_utilization / load) if load > 0 else 0.0}

    hosts: Dict[str, Dict[str, List[str]]] = defaultdict(lambda: {"apps": [], "brokers": []})
    # A multi-hosted application runs an instance on every host (active /
    # standby, see runtime.AppRuntime), so a host outage takes it down only when
    # all of its hosts are down -- `replica_aware` semantics. Brokers stay on
    # their first host: their redundancy is the router set of each topic.
    for a in apps:
        for h in apps[a]["hosts"]:
            hosts[h]["apps"].append(a)
    for b in brokers:
        for h in (sorted(hosts_of.get(b, [])) or [DEFAULT_HOST])[:1]:
            hosts[h]["brokers"].append(b)

    return {
        "schema_version": "1.0",
        "scenario": g.graph.get("id", Path(scenario).stem),
        "target_utilization": target_utilization,
        "staleness_periods": staleness_periods,
        "topics": topics,
        "apps": apps,
        "brokers": brokers,
        "hosts": {h: hosts[h] for h in sorted(hosts)},
        "links": sorted({tuple(sorted((u, v))) for u, v, d in g.edges(data=True)
                         if d.get("type") == "CONNECTS_TO"}),
    }
