"""Small topologies and specs shared by the deployment-planning tests.

Micro topology: four hosts in two failure domains (D0 = N1, N2; D1 = N3, N4).
Broker B routes T1 (P -> S1, 5 ms deadline) and T2 (S1 -> S2). T3 is
brokerless (P -> S2). P is a hot standby, so it needs two copies.
"""

import copy
import itertools
from typing import Dict, Iterator, List

from saag.deployment.instance import Instance, Placement
from saag.deployment.service import _dependency_arcs
from saag.deployment.spec import bind_spec

HOSTS = ["N1", "N2", "N3", "N4"]
DOMAINS = {"N1": "D0", "N2": "D0", "N3": "D1", "N4": "D1"}


def topology(brokers=("B",), broker_hosts=None) -> Dict:
    brokers = list(brokers)
    broker_hosts = broker_hosts or {b: "N1" for b in brokers}
    routes = [{"from": brokers[0], "to": "T1"}, {"from": brokers[-1], "to": "T2"}]
    return {
        "nodes": [{"id": h, "name": h} for h in HOSTS],
        "brokers": [{"id": b, "name": b} for b in brokers],
        "topics": [
            {"id": "T1", "name": "T1", "size": 1024, "frequency": 10, "deadline_ms": 5.0, "history_depth": 4},
            {"id": "T2", "name": "T2", "size": 256, "frequency": 10},
            {"id": "T3", "name": "T3", "size": 64, "frequency": 1},
        ],
        "applications": [
            {"id": "P", "name": "P", "hotstandby": True},
            {"id": "S1", "name": "S1"},
            {"id": "S2", "name": "S2"},
        ],
        "libraries": [],
        "relationships": {
            "publishes_to": [{"from": "P", "to": "T1"}, {"from": "S1", "to": "T2"}, {"from": "P", "to": "T3"}],
            "subscribes_to": [{"from": "S1", "to": "T1"}, {"from": "S2", "to": "T2"}, {"from": "S2", "to": "T3"}],
            "routes": routes,
            "runs_on": ([{"from": "P", "to": "N1"}, {"from": "P", "to": "N3"},
                         {"from": "S1", "to": "N2"}, {"from": "S2", "to": "N4"}]
                        + [{"from": b, "to": broker_hosts[b]} for b in brokers]),
            "connects_to": [],
            "uses": [],
        },
    }


def spec(**overrides) -> Dict:
    out = {
        "defaults": {"node": {"cpu": 4, "mem_mb": 4096, "p": 0.02}, "domain": {"p": 0.01},
                     "app": {"cpu": 1.0, "mem_mb": 256}, "broker": {"cpu": 1.0, "mem_mb": 512}},
        "nodes": {h: {"domain": d} for h, d in DOMAINS.items()},
        "latency": {"intra_domain_ms": 1.0, "inter_domain_ms": 3.0},
    }
    for key, value in overrides.items():
        out[key] = value if not isinstance(value, dict) else {**out.get(key, {}), **value}
    return out


def instance(topo=None, spc=None) -> Instance:
    topo = copy.deepcopy(topo or topology())
    return Instance(topo, bind_spec(spc or spec(), topo), _dependency_arcs(topo))


def all_placements(inst: Instance) -> Iterator[Placement]:
    """Every assignment of copies to allowed hosts (ordered: primary first), feasible or not."""
    options: List[List[List[str]]] = []
    for c in inst.comps:
        options.append([list(p) for p in itertools.permutations(inst.allowed[c], inst.r[c])])
    for combo in itertools.product(*options):
        yield dict(zip(inst.comps, combo))


def feasible(inst: Instance, pl: Placement) -> bool:
    return not any(inst.violations(pl).values())
