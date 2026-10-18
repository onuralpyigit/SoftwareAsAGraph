"""
Physical network model: hosts, switches/routers and the CONNECTS_TO links between them.

Pure functions over plain ids, shared by both repositories, the simulation
engines and the GNN data preparation so the network semantics cannot drift
apart between them (the same reason ``core.models.compute_topic_weight`` lives
in core).

Representation (docs/graph-model.md §5.2):

* ``Node`` hosts and ``NetworkDevice`` switches/routers are the network vertices.
* ``CONNECTS_TO`` is a cable: stored once, carried both ways. Optional link
  properties are ``from_interface``, ``to_interface`` and ``capacity_mbps``
  (absent capacity = unbounded). Parallel links between one pair add up (LAG).
* A pub-sub flow travels pub host -> routing broker host -> sub host (brokerless
  topics: pub host -> sub host) along shortest paths, split evenly over
  equal-cost paths (ECMP). Each publisher emits ``size x frequency`` of its
  topic, as the message-flow simulator drives it.

Everything here is opt-in by data: with no NetworkDevice and no capacity on any
link, the derivation rule returns nothing and no load is computed.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from saag.core.models import TOPIC_DEFAULT_FREQUENCY_HZ, compute_lifted_edge_weight

#: Component types that make up the physical network.
NETWORK_TYPES: Tuple[str, ...] = ("Node", "NetworkDevice")

#: Roles a NetworkDevice may declare.
DEVICE_ROLES: Tuple[str, ...] = ("switch", "router")

#: Input properties a CONNECTS_TO link may carry (all optional).
LINK_PROPERTIES: Tuple[str, ...] = ("from_interface", "to_interface", "capacity_mbps")

Link = FrozenSet[str]
Flow = Tuple[str, str, str]   # (publisher, topic, subscriber)


def link_key(a: str, b: str) -> Link:
    """Direction-free key of the cable between *a* and *b*."""
    return frozenset((a, b))


def topic_load_mbps(attrs: Mapping) -> float:
    """Offered load of one publisher on a topic, in Mbps (size x frequency).

    Same attribute precedence as ``topic_weight_from_node_attrs`` for the size,
    and the ``topic_frequency`` spelling the repositories flatten to.
    """
    size = attrs.get("size", attrs.get("message_size", 1024)) or 1024
    freq = attrs.get("frequency", attrs.get("topic_frequency"))
    if freq is None:
        freq = TOPIC_DEFAULT_FREQUENCY_HZ
    return max(0.0, float(size)) * max(0.0, float(freq)) * 8.0 / 1e6


def place(hosts: Sequence[str], down: Iterable[str] = ()) -> Optional[str]:
    """The host a component's traffic leaves from: its lowest-id live host.

    Backend-independent on purpose (Neo4j returns RUNS_ON in no stable order).
    None when every host is down.
    """
    down = set(down)
    live = [h for h in hosts if h not in down]
    return min(live) if live else None


class Fabric:
    """Undirected CONNECTS_TO graph over the network vertices."""

    def __init__(self, vertices: Iterable[str], links: Iterable[Tuple[str, str, Optional[float]]]):
        self.vertices: Set[str] = set(vertices)
        self.adj: Dict[str, Set[str]] = defaultdict(set)
        self.capacity: Dict[Link, Optional[float]] = {}
        for a, b, cap in links:
            if a == b:
                continue
            self.vertices.update((a, b))
            self.adj[a].add(b)
            self.adj[b].add(a)
            key = link_key(a, b)
            if key not in self.capacity:
                self.capacity[key] = cap
            elif self.capacity[key] is None or cap is None:
                self.capacity[key] = None
            else:
                self.capacity[key] += cap

    @property
    def has_capacity(self) -> bool:
        return any(c is not None for c in self.capacity.values())

    def islands(self, down: Iterable[str] = (), cut: Iterable[Link] = ()) -> Dict[str, int]:
        """Island index of every live vertex; down vertices are in no island."""
        down, cut = set(down), set(cut)
        island: Dict[str, int] = {}
        n_islands = 0
        for start in sorted(self.vertices):
            if start in island or start in down:
                continue
            idx, n_islands = n_islands, n_islands + 1
            island[start] = idx
            stack = [start]
            while stack:
                cur = stack.pop()
                for nxt in self.adj.get(cur, ()):
                    if nxt in island or nxt in down or link_key(cur, nxt) in cut:
                        continue
                    island[nxt] = idx
                    stack.append(nxt)
        return island

    def _shortest_dag(self, src: str, down: Set[str], cut: Set[Link]):
        """BFS distances and shortest-path counts from *src*."""
        dist, sigma = {src: 0}, {src: 1}
        queue = deque([src])
        while queue:
            u = queue.popleft()
            for v in sorted(self.adj.get(u, ())):
                if v in down or link_key(u, v) in cut:
                    continue
                if v not in dist:
                    dist[v] = dist[u] + 1
                    sigma[v] = 0
                    queue.append(v)
                if dist[v] == dist[u] + 1:
                    sigma[v] += sigma[u]
        return dist, sigma

    def ecmp_shares(self, src: str, dst: str, down: Set[str], cut: Set[Link],
                    _cache: Optional[dict] = None) -> Optional[Dict[Tuple[str, str], float]]:
        """Fraction of a src->dst demand on each directed hop, ECMP-split.

        Empty dict for src == dst, None when dst is unreachable.
        """
        if src == dst:
            return {}
        if _cache is not None and src in _cache:
            dist, sigma = _cache[src]
        else:
            dist, sigma = self._shortest_dag(src, down, cut)
            if _cache is not None:
                _cache[src] = (dist, sigma)
        if dst not in dist:
            return None
        through = {dst: 1.0}
        shares: Dict[Tuple[str, str], float] = {}
        frontier = [dst]
        while frontier:
            nxt_frontier = []
            for v in frontier:
                for u in self.adj.get(v, ()):
                    if u in down or link_key(u, v) in cut:
                        continue
                    if dist.get(u) == dist[v] - 1:
                        s = through[v] * sigma[u] / sigma[v]
                        shares[(u, v)] = shares.get((u, v), 0.0) + s
                        if u not in through:
                            through[u] = 0.0
                            nxt_frontier.append(u)
                        through[u] += s
            frontier = sorted(nxt_frontier)
        return shares


@dataclass
class NetworkState:
    """Where traffic goes and how much of it arrives, for one failure state."""

    islands: Dict[str, int]
    link_load: Dict[Link, float] = field(default_factory=dict)
    link_util: Dict[Link, Optional[float]] = field(default_factory=dict)
    #: Undelivered fraction per flow due to the network: 1.0 = no path at all,
    #: in between = congestion, 0.0 = intact. Flows absent from the map are not
    #: on the network (an endpoint has no host) or lost logically (no broker).
    flow_loss: Dict[Flow, float] = field(default_factory=dict)
    #: Total load on each vertex's links, Mbps.
    vertex_load: Dict[str, float] = field(default_factory=dict)

    def max_util(self, vertex: str, fabric: Fabric) -> float:
        utils = [self.link_util.get(link_key(vertex, n)) for n in fabric.adj.get(vertex, ())]
        return max((u for u in utils if u is not None), default=0.0)


def evaluate(
    fabric: Fabric,
    flows: Iterable[Flow],
    hosts_of: Mapping[str, Sequence[str]],
    live_routers: Mapping[str, Sequence[str]],
    brokered: Iterable[str],
    topic_load: Mapping[str, float],
    down: Iterable[str] = (),
    cut: Iterable[Link] = (),
    with_load: bool = True,
) -> NetworkState:
    """Route every pub-sub flow over *fabric* and measure what arrives.

    ``live_routers[t]`` are topic *t*'s brokers that are still up; ``brokered``
    are the topics that have any broker configured at all. A brokered topic
    with no live broker is a logical loss, not a network one, so it gets no
    entry here. The routing broker of a flow is the lowest-id live broker the
    publisher and the subscriber can both reach.

    With ``with_load`` False only reachability is decided (flow_loss is 0 or 1).
    """
    down, cut = set(down), set(cut)
    brokered = set(brokered)
    islands = fabric.islands(down, cut)
    state = NetworkState(islands=islands)

    def host(comp: str) -> Tuple[bool, Optional[str]]:
        """(on_network, host). host None + on_network = its hosts are all down."""
        if comp in fabric.vertices:
            return True, (None if comp in down else comp)
        hosts = [h for h in hosts_of.get(comp, ()) if h in fabric.vertices]
        if not hosts:
            return False, None
        return True, place(hosts, down)

    def linked(ha: Optional[str], hb: Optional[str]) -> bool:
        if ha is None or hb is None or ha not in islands:
            return False
        return islands[ha] == islands.get(hb)

    # demand legs, deduplicated, and the legs each flow rides on
    legs: Dict[tuple, Tuple[str, str, float]] = {}
    flow_legs: Dict[Flow, List[tuple]] = {}
    for pub, topic, sub in flows:
        on_p, hp = host(pub)
        on_s, hs = host(sub)
        load = topic_load.get(topic, 0.0)
        if topic in brokered:
            routers = sorted(live_routers.get(topic, ()))
            if not routers:
                continue
            placed = [(b,) + host(b) for b in routers]
            if not on_p and not on_s and not any(on for _, on, _ in placed):
                continue
            chosen = None
            for b, on_b, hb in placed:
                ok_up = not (on_p and on_b) or linked(hp, hb)
                ok_down = not (on_b and on_s) or linked(hb, hs)
                if ok_up and ok_down:
                    chosen = (b, on_b, hb)
                    break
            if chosen is None:
                state.flow_loss[(pub, topic, sub)] = 1.0
                continue
            b, on_b, hb = chosen
            mine = []
            if on_p and on_b:
                key = ("up", pub, topic, b)
                legs[key] = (hp, hb, load)
                mine.append(key)
            if on_b and on_s:
                key = ("down", b, topic, sub)
                legs[key] = (hb, hs, load)
                mine.append(key)
        else:
            if not (on_p and on_s):
                continue
            if not linked(hp, hs):
                state.flow_loss[(pub, topic, sub)] = 1.0
                continue
            key = ("p2p", pub, topic, sub)
            legs[key] = (hp, hs, load)
            mine = [key]
        flow_legs[(pub, topic, sub)] = mine

    if not with_load:
        for flow in flow_legs:
            state.flow_loss[flow] = 0.0
        return state

    # route the legs and accumulate link load
    cache: dict = {}
    leg_shares: Dict[tuple, Dict[Tuple[str, str], float]] = {}
    for key in sorted(legs):
        src, dst, mbps = legs[key]
        shares = fabric.ecmp_shares(src, dst, down, cut, cache) or {}
        leg_shares[key] = shares
        for (u, v), s in shares.items():
            lk = link_key(u, v)
            state.link_load[lk] = state.link_load.get(lk, 0.0) + s * mbps
    for lk, cap in fabric.capacity.items():
        load = state.link_load.get(lk, 0.0)
        state.link_util[lk] = (load / cap) if cap else (None if cap is None else float("inf"))
    for lk, load in state.link_load.items():
        for v in lk:
            state.vertex_load[v] = state.vertex_load.get(v, 0.0) + load

    def leg_delivered(key: tuple) -> float:
        """Fraction of the leg arriving when congested links drop their excess."""
        shares = leg_shares[key]
        if not shares:
            return 1.0
        src, dst, _ = legs[key]
        out: Dict[str, float] = defaultdict(float)
        succ: Dict[str, List[str]] = defaultdict(list)
        for (u, v), s in shares.items():
            out[u] += s
            succ[u].append(v)
        depth = {src: 0}
        queue = deque([src])
        while queue:
            u = queue.popleft()
            for v in succ[u]:
                if v not in depth:
                    depth[v] = depth[u] + 1
                    queue.append(v)
        arriving: Dict[str, float] = defaultdict(float, {src: 1.0})
        for (u, v), s in sorted(shares.items(), key=lambda kv: (depth[kv[0][0]], kv[0])):
            util = state.link_util.get(link_key(u, v))
            factor = 1.0 if util is None or util <= 1.0 else 1.0 / util
            arriving[v] += arriving[u] * (s / out[u]) * factor
        return arriving[dst]

    delivered = {key: leg_delivered(key) for key in legs}
    for flow, mine in flow_legs.items():
        frac = 1.0
        for key in mine:
            frac *= delivered[key]
        state.flow_loss[flow] = max(0.0, 1.0 - frac)
    return state


def derive_node_to_device(
    fabric: Fabric,
    devices: Iterable[str],
    node_deps: Iterable[Tuple[str, str, float]],
) -> Dict[Tuple[str, str], Tuple[float, int]]:
    """Rule 7 (``node_to_device``): host B depends on a device every path to its dependencies crosses.

    For each ``node_to_node`` dependency B -> A whose hosts share an island of
    the intact fabric, every NetworkDevice whose removal separates B from A
    becomes a dependency of B. Weight is the worst-case lift of the carried
    dependencies (as Rules 3-4), ``path_count`` their number. Hosts are never
    targets, so a host-only mesh derives nothing.

    Returns {(B, device): (weight, path_count)}.
    """
    devices = sorted(d for d in devices if d in fabric.vertices)
    if not devices:
        return {}
    base = fabric.islands()
    deps = [(b, a, w) for b, a, w in node_deps
            if b != a and b in base and a in base and base[b] == base[a]]
    carried: Dict[Tuple[str, str], List[float]] = defaultdict(list)
    for d in devices:
        without = fabric.islands(down={d})
        for b, a, w in deps:
            if without.get(b) != without.get(a):
                carried[(b, d)].append(w)
    return {k: (compute_lifted_edge_weight(ws), len(ws)) for k, ws in sorted(carried.items())}


def baseline_load(
    vertices: Iterable[str],
    links: Iterable[Tuple[str, str, Optional[float]]],
    publishers_of: Mapping[str, Sequence[str]],
    subscribers_of: Mapping[str, Sequence[str]],
    routers_of: Mapping[str, Sequence[str]],
    hosts_of: Mapping[str, Sequence[str]],
    topic_attrs: Mapping[str, Mapping],
) -> Tuple[Fabric, NetworkState]:
    """Phase 5c: route the healthy system's direct pub-sub flows and measure link load.

    Only components with their own RUNS_ON host are on the network, so
    library-mediated publishing is not routed.
    """
    fabric = Fabric(vertices, links)
    flows = sorted(
        (p, t, s)
        for t, pubs in publishers_of.items()
        for p in set(pubs)
        for s in set(subscribers_of.get(t, ()))
        if p != s
    )
    state = evaluate(
        fabric, flows, hosts_of, routers_of,
        brokered=[t for t, r in routers_of.items() if r],
        topic_load={t: topic_load_mbps(a) for t, a in topic_attrs.items()},
    )
    return fabric, state


def vertex_load_metrics(fabric: Fabric, state: NetworkState) -> Dict[str, Dict[str, float]]:
    """``max_link_utilization`` and ``link_load_mbps`` of every network vertex."""
    return {
        v: {
            "max_link_utilization": state.max_util(v, fabric),
            "link_load_mbps": state.vertex_load.get(v, 0.0),
        }
        for v in sorted(fabric.vertices)
    }
