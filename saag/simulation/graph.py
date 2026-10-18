"""
Simulation Graph

Graph representation for simulation using RAW structural relationships.
Works directly on PUBLISHES_TO, SUBSCRIBES_TO, ROUTES, RUNS_ON, CONNECTS_TO
relationships without deriving DEPENDS_ON.
"""

from __future__ import annotations
import logging
from typing import Dict, List, Set, Tuple, Any, Optional
from collections import defaultdict

import networkx as nx

from .models import ComponentState, ComponentInfo, TopicInfo
from saag.core.layers import SimulationLayer, SIMULATION_LAYERS
from saag.core.models import (
    MIN_TOPIC_WEIGHT, QoSPolicy,
    compute_effective_edge_weight, compute_harmonic_coupling, compute_lifted_edge_weight,
)
from saag.core.network import NETWORK_TYPES, Fabric, derive_node_to_device

#: Component types that participate in messaging dependencies (Rules 1, 2, 5).
#: Mirrors saag/infrastructure/memory_repo.py so the two derivations cannot drift.
MESSAGING_TYPES = ("Application", "Library")

#: Maximum USES hops followed when resolving which Applications reach a Library.
USES_CHAIN_DEPTH = 3


class SimulationGraph:
    """
    Graph representation for pub-sub system simulation.
    
    Works on RAW structural relationships:
        - Application -[PUBLISHES_TO]-> Topic
        - Application -[SUBSCRIBES_TO]-> Topic
        - Topic -[ROUTES]-> Broker (or Broker -[ROUTES]-> Topic)
        - Application -[RUNS_ON]-> Node
        - Broker -[RUNS_ON]-> Node
        - Node -[CONNECTS_TO]-> Node | NetworkDevice
        - NetworkDevice -[CONNECTS_TO]-> NetworkDevice
    
    This domain model assumes data is loaded via an external mechanism (GraphData).
    """
    
    def __init__(self, graph_data: Any = None):
        """
        Initialize simulation graph.
        
        Args:
            graph_data: Pre-loaded GraphData object containing components and edges.
        """
        self.logger = logging.getLogger(__name__)
        self._graph_data = graph_data
        
        # NetworkX graph for structural queries
        self.graph = nx.DiGraph()
        
        # Component registries
        self.components: Dict[str, ComponentInfo] = {}
        self.topics: Dict[str, TopicInfo] = {}
        
        # Relationship indices for fast lookups
        self._publishers: Dict[str, List[Tuple[str, float]]] = defaultdict(list)   # topic -> [(apps, weight)]
        self._subscribers: Dict[str, List[Tuple[str, float]]] = defaultdict(list)  # topic -> [(apps, weight)]
        self._routing: Dict[str, List[Tuple[str, float]]] = defaultdict(list)      # topic -> [(brokers, weight)]
        self._hosted_on: Dict[str, str] = {}                                       # comp -> node
        self._hosts: Dict[str, List[str]] = defaultdict(list)                      # node -> [comps]
        self._connections: Dict[str, List[Tuple[str, float]]] = defaultdict(list)  # node -> [(nodes, weight)]
        self._net_adj: Dict[str, Set[str]] = defaultdict(set)                      # CONNECTS_TO, undirected
        self._net_links: List[Tuple[str, str, Optional[float]]] = []               # (a, b, capacity_mbps)
        self._runs_on_all: Dict[str, List[str]] = defaultdict(list)                # comp -> every host
        self._fabric: Optional[Fabric] = None
        self._uses: Dict[str, List[str]] = defaultdict(list)                       # app/lib -> [libs]
        self._used_by: Dict[str, List[str]] = defaultdict(list)                    # lib -> [apps/libs]
        self._app_publishes: Dict[str, List[str]] = defaultdict(list)              # app -> [topics]
        self._app_subscribes: Dict[str, List[str]] = defaultdict(list)             # app -> [topics]

        # Severed relationships, as (source, target) pairs. Distinct from a
        # failed *component*: both endpoints stay up and every other link they
        # own keeps working — the partial-outage case that edge criticality is
        # about. Consulted by the relationship accessors below.
        self._failed_edges: Set[Tuple[str, str]] = set()

        #: Memoised DEPENDS_ON projection (Rules 1-7), built lazily by
        #: get_dependency_edges() and invalidated on load.
        self._dependency_edges: Optional[List[Tuple[str, str, float, str]]] = None
        
        # Load graph
        if graph_data:
            self._load_from_data(graph_data)
    
    def _load_from_data(self, graph_data: Any) -> None:
        """Load from pre-loaded GraphData object."""
        self.logger.info("Loading simulation graph from GraphData")
        
        # Load components
        for comp in graph_data.components:
            comp_id = comp.id if hasattr(comp, 'id') else comp.get('id')
            comp_type = comp.component_type if hasattr(comp, 'component_type') else comp.get('type')
            props = comp.properties if hasattr(comp, 'properties') else {}
            
            comp_weight = 1.0
            if hasattr(comp, "weight"):
                comp_weight = comp.weight
            elif isinstance(comp, dict):
                comp_weight = comp.get("weight", 1.0)
            else:
                comp_weight = props.get("weight", 1.0)

            if comp_type == "Topic":
                # Resolve through QoSPolicy so both the flat (qos_transport_priority)
                # and nested (qos: {...}) attribute shapes are honoured. Reading the
                # flat keys directly made qos_priority always fall back to its default.
                qos = QoSPolicy.from_node_attrs(props)
                self.topics[comp_id] = TopicInfo(
                    id=comp_id,
                    name=props.get("name", comp_id),
                    message_size=props.get("message_size", props.get("size", 1024)),
                    qos_reliability=qos.reliability,
                    qos_durability=qos.durability,
                    qos_priority=qos.transport_priority,
                    weight=comp_weight,
                    deadline_ms=qos.deadline_ms,
                    history_depth=qos.history_depth,
                )
            
            self.components[comp_id] = ComponentInfo(
                id=comp_id,
                type=comp_type,
                weight=comp_weight,
                properties=props
            )
            self.graph.add_node(comp_id, type=comp_type)
        
        # Load edges
        for edge in graph_data.edges:
            src = edge.source_id if hasattr(edge, 'source_id') else edge.get('source')
            tgt = edge.target_id if hasattr(edge, 'target_id') else edge.get('target')
            rel = edge.relation_type if hasattr(edge, 'relation_type') else edge.get('relation_type', 'UNKNOWN')
            weight = edge.weight if hasattr(edge, 'weight') else edge.get('weight', 1.0)
            edge_props = getattr(edge, 'properties', None) or {}
            
            self.graph.add_edge(src, tgt, relation=rel, weight=weight)
            
            # Index by relationship type
            if rel == "PUBLISHES_TO":
                self._publishers[tgt].append((src, weight))
            elif rel == "SUBSCRIBES_TO":
                self._subscribers[tgt].append((src, weight))
            elif rel == "ROUTES":
                # ROUTES can be Topic -> Broker or Broker -> Topic
                # We index by Topic for fast provider lookup
                if src in self.topics:
                    self._routing[src].append((tgt, weight))
                elif tgt in self.topics:
                    self._routing[tgt].append((src, weight))
                else:
                    # Fallback to target-based indexing
                    self._routing[tgt].append((src, weight))
            elif rel == "RUNS_ON":
                self._hosted_on[src] = tgt
                self._hosts[tgt].append(src)  # Hosts don't strictly need weight for these cascades
                self._runs_on_all[src].append(tgt)
            elif rel == "CONNECTS_TO":
                self._connections[src].append((tgt, weight))
                self._net_adj[src].add(tgt)
                self._net_adj[tgt].add(src)
                self._net_links.append((src, tgt, edge_props.get("capacity_mbps")))
            elif rel == "USES":
                self._uses[src].append(tgt)
                self._used_by[tgt].append(src)

        self._dependency_edges = None
        self._fabric = None
        self._build_app_topic_index()

        # A switch/router carries the worst-case weight of the hosts depending on
        # it (Rule 7). Derived here, never read off the analysis graph -- the same
        # value the repositories assign in Phase 5b.
        devices = [c for c in self.components.values() if c.type == "NetworkDevice"]
        if devices:
            dependents: Dict[str, List[float]] = defaultdict(list)
            for src, tgt, _w, dep_type in self.get_dependency_edges():
                if dep_type == "node_to_device":
                    dependents[tgt].append(self.components[src].weight)
            for dev in devices:
                dev.weight = max(dependents.get(dev.id, []), default=MIN_TOPIC_WEIGHT)

    def _build_app_topic_index(self) -> None:
        """
        Invert the topic-keyed endpoint indices into app -> [topics].

        Ordering matters: the cascade draws one random number per topic in this
        list, so the sequence must stay exactly what the previous per-call scan
        produced — topics in `_publishers` / `_subscribers` insertion order, not
        in the order this app's own edges were read.
        """
        self._app_publishes = defaultdict(list)
        self._app_subscribes = defaultdict(list)

        for index, target in ((self._publishers, self._app_publishes),
                              (self._subscribers, self._app_subscribes)):
            for topic_id, endpoints in index.items():
                seen: Set[str] = set()
                for app_id, _weight in endpoints:
                    if app_id not in seen:
                        seen.add(app_id)
                        target[app_id].append(topic_id)


    # =========================================================================
    # State Management
    # =========================================================================
    
    def reset(self) -> None:
        """Reset all component states and metrics for a new simulation."""
        for comp in self.components.values():
            comp.state = ComponentState.ACTIVE
            comp.reset_metrics()
        self._failed_edges.clear()

    def fail_edge(self, source: str, target: str) -> None:
        """Sever one relationship, leaving both endpoints active."""
        self._failed_edges.add((source, target))

    def recover_edge(self, source: str, target: str) -> None:
        """Restore a severed relationship."""
        self._failed_edges.discard((source, target))

    def is_edge_active(self, source: str, target: str) -> bool:
        """True when the relationship carries traffic and both endpoints are up."""
        if (source, target) in self._failed_edges:
            return False
        return self.is_active(source) and self.is_active(target)


    def fail_component(self, comp_id: str) -> None:
        """Mark a component as failed."""
        if comp_id in self.components:
            self.components[comp_id].state = ComponentState.FAILED
    
    def recover_component(self, comp_id: str) -> None:
        """Recover a failed component."""
        if comp_id in self.components:
            self.components[comp_id].state = ComponentState.ACTIVE
    
    def is_active(self, comp_id: str) -> bool:
        """Check if a component is active (including degraded)."""
        comp = self.components.get(comp_id)
        if not comp:
            return False
        return comp.state in (ComponentState.ACTIVE, ComponentState.DEGRADED)
    
    def set_degraded(self, comp_id: str) -> None:
        """Mark a component as degraded."""
        if comp_id in self.components:
            self.components[comp_id].state = ComponentState.DEGRADED
    
    # =========================================================================
    # Graph Queries
    # =========================================================================
    
    def _live_endpoints(
        self, index: Dict[str, List[Tuple[str, float]]], topic_id: str
    ) -> List[str]:
        """Endpoints of *topic_id* in *index* whose component and edge are both live."""
        return [e[0] for e in index.get(topic_id, [])
                if self.is_active(e[0]) and (e[0], topic_id) not in self._failed_edges]

    def get_publishers(self, topic_id: str) -> List[str]:
        """Get all publishers for a topic (live component *and* live edge)."""
        return self._live_endpoints(self._publishers, topic_id)

    def get_subscribers(self, topic_id: str) -> List[str]:
        """Get all subscribers for a topic (live component *and* live edge)."""
        return self._live_endpoints(self._subscribers, topic_id)

    def has_configured_brokers(self, topic_id: str) -> bool:
        """
        Whether the topic has any ROUTES broker at all, regardless of state.

        Distinguishes a genuinely brokerless (DDS-style) topic from one whose
        brokers have all failed — `get_routing_brokers` returns [] for both.
        """
        return bool(self._routing.get(topic_id))

    def get_routing_brokers(self, topic_id: str) -> List[str]:
        """Get all brokers that route a topic (live component *and* live edge)."""
        return self._live_endpoints(self._routing, topic_id)
    
    def get_hosted_components(self, node_id: str) -> List[str]:
        """Get all components hosted on a node."""
        return self._hosts.get(node_id, [])
    
    def get_host_node(self, comp_id: str) -> Optional[str]:
        """Get the node that hosts a component."""
        return self._hosted_on.get(comp_id)
    
    def get_connected_nodes(self, node_id: str) -> List[str]:
        """Get nodes connected to a given node."""
        return [n[0] for n in self._connections.get(node_id, []) if self.is_active(n[0])]
    
    def has_network(self) -> bool:
        """Whether the topology declares any CONNECTS_TO link at all."""
        return bool(self._net_adj)

    def fabric(self) -> Fabric:
        """The intact CONNECTS_TO graph over every host and switch/router.

        Links are undirected: a cable carries traffic both ways whichever way
        round the topology happens to list it. Failure state is applied by the
        caller (``Fabric.islands`` / ``saag.core.network.evaluate``).
        """
        if self._fabric is None:
            self._fabric = Fabric(
                [cid for cid, c in self.components.items() if c.type in NETWORK_TYPES],
                self._net_links,
            )
        return self._fabric

    def get_all_hosts(self, comp_id: str) -> List[str]:
        """Every node a component RUNS_ON (hot-standby components have several)."""
        return self._runs_on_all.get(comp_id, [])

    def get_app_topics(self, app_id: str) -> Tuple[List[str], List[str]]:
        """
        Get topics an application publishes to and subscribes from.

        Served from an index built once at load time. This used to rescan every
        topic's full endpoint list on each call, which made it O(|E|) inside the
        cascade's inner loop.
        """
        return (self._app_publishes.get(app_id, []),
                self._app_subscribes.get(app_id, []))
    
    def get_pub_sub_paths(self, active_only: bool = True):  # -> List[Tuple[str, str, str]]
        """
        Get all publisher -> topic -> subscriber paths.
        
        Returns List of (publisher, topic, subscriber) tuples.
        """
        paths = []
        for topic_id in self.topics:
            publishers = [p[0] for p in self._publishers.get(topic_id, [])]
            subscribers = [s[0] for s in self._subscribers.get(topic_id, [])]
            
            if active_only:
                publishers = [p for p in publishers if self.is_active(p)]
                subscribers = [s for s in subscribers if self.is_active(s)]
                if not self.get_routing_brokers(topic_id):
                    continue
            
            for pub in publishers:
                for sub in subscribers:
                    paths.append((pub, topic_id, sub))
        return paths

    def get_weighted_pub_sub_paths(self, active_only: bool = True) -> List[Tuple[str, str, str, float]]:
        """
        Get all publisher -> topic -> subscriber paths with their remaining capacity.
        
        Capacity = min(
            perf(publisher),
            weight(pub->topic),
            max(perf(broker_i) * weight(broker_i->topic)),
            weight(sub->topic),
            perf(subscriber)
        )
        
        Returns:
            List of (publisher, topic, subscriber, capacity)
        """
        paths = []
        
        for topic_id in self.topics:
            topic_info = self.topics[topic_id]
            pubs_raw = self._publishers.get(topic_id, [])
            subs_raw = self._subscribers.get(topic_id, [])
            brokers_raw = self._routing.get(topic_id, [])
            
            # 1. Broker segment capacity (Max of any active broker path)
            broker_capacities = []
            for b_id, b_weight in brokers_raw:
                if active_only and (b_id, topic_id) in self._failed_edges:
                    continue
                if not active_only or self.is_active(b_id):
                    b_perf = self.components[b_id].performance
                    broker_capacities.append(b_perf * b_weight)
            
            broker_segment_capacity = max(broker_capacities) if broker_capacities else 1.0  # DDS direct
            
            if active_only and not broker_capacities:
                # In brokerless mode (DDS), paths are direct
                pass
            elif active_only and broker_segment_capacity <= 0:
                continue
                
            for p_id, p_weight in pubs_raw:
                p_perf = self.components[p_id].performance
                if active_only and p_perf <= 0:
                    continue
                if active_only and (p_id, topic_id) in self._failed_edges:
                    continue

                path_prefix_capacity = min(p_perf, p_weight, broker_segment_capacity)

                for s_id, s_weight in subs_raw:
                    s_perf = self.components[s_id].performance
                    if active_only and s_perf <= 0:
                        continue
                    if active_only and (s_id, topic_id) in self._failed_edges:
                        continue
                        
                    capacity = min(path_prefix_capacity, s_weight, s_perf)
                    if not active_only or capacity > 0:
                        paths.append((p_id, topic_id, s_id, capacity))
                        
        return paths

    def count_active_connected_components(self, all_hosts: bool = False):  # -> int
        """
        Count weakly-connected components in the active subgraph.

        Builds a temporary undirected graph from active components and their
        active relationships, then counts connected components. Used by
        FailureSimulator to compute true graph fragmentation rather than
        simple component loss ratio.

        Args:
            all_hosts: Attach a multi-hosted component to every host it RUNS_ON
                rather than only the last one it declares (replica-aware mode).

        Returns:
            Number of weakly-connected components among active components.
            Returns 0 if no active components exist.
        """

        active_graph = self._build_active_undirected_graph(all_hosts)
        if len(active_graph) == 0:
            return 0
        return nx.number_connected_components(active_graph)

    def active_connected_components(self, all_hosts: bool = False) -> List[Set[str]]:
        """The active subgraph's connected components as sets of component ids.

        Same projection as :meth:`count_active_connected_components`, exposed so
        callers can weight each island by what it carries instead of only
        counting islands.
        """

        active_graph = self._build_active_undirected_graph(all_hosts)
        if len(active_graph) == 0:
            return []
        return [set(c) for c in nx.connected_components(active_graph)]

    def _build_active_undirected_graph(self, all_hosts: bool = False):
        """Undirected projection over active Application/Broker/Node components.

        ``all_hosts`` looks past ``_hosted_on`` (last declared host only) so a
        component whose last host failed stays attached to a surviving replica host.
        """

        # Build undirected graph of active components
        active_graph = nx.Graph()

        # Add all active non-Topic components as nodes
        for comp_id, comp in self.components.items():
            if comp.state == ComponentState.ACTIVE and comp.type in ("Application", "Broker") + NETWORK_TYPES:
                active_graph.add_node(comp_id)
        
        if len(active_graph) == 0:
            return active_graph

        # Add edges for active relationships
        # RUNS_ON: app/broker <-> node
        if all_hosts:
            runs_on = [(c, h) for c, hosts in self._runs_on_all.items() for h in hosts]
        else:
            runs_on = self._hosted_on.items()
        for comp_id, node_id in runs_on:
            if comp_id in active_graph and node_id in active_graph:
                active_graph.add_edge(comp_id, node_id)
        
        # CONNECTS_TO: node <-> node
        for node_id, connected in self._connections.items():
            for neighbor_tuple in connected:
                neighbor_id = neighbor_tuple[0]
                if node_id in active_graph and neighbor_id in active_graph:
                    active_graph.add_edge(node_id, neighbor_id)
        
        # Pub/Sub paths through topics (app <-> app via shared topic)
        for topic_id in self.topics:
            topic_pubs = self._publishers.get(topic_id, [])
            topic_subs = self._subscribers.get(topic_id, [])
            topic_brokers = self._routing.get(topic_id, [])
            
            active_pubs = [p[0] for p in topic_pubs if p[0] in active_graph]
            active_subs = [s[0] for s in topic_subs if s[0] in active_graph]
            active_brokers = [b[0] for b in topic_brokers if b[0] in active_graph]
            
            if active_brokers:
                # Connect publishers and subscribers through brokers
                for pub in active_pubs:
                    for broker in active_brokers:
                        active_graph.add_edge(pub, broker)
                for sub in active_subs:
                    for broker in active_brokers:
                        active_graph.add_edge(sub, broker)
            else:
                # Brokerless (DDS): Connect publishers and subscribers directly via topic abstraction
                for pub in active_pubs:
                    for sub in active_subs:
                        active_graph.add_edge(pub, sub)

        return active_graph
    
    def get_library_usage(self) -> Dict[str, List[str]]:
        """
        Get library usage for all components.
        
        Returns:
            Dict mapping component ID to list of library IDs
        """
        return dict(self._uses)

    def get_uses_consumers(self, library_id: str) -> List[str]:
        """
        Get components (Applications or Libraries) that use a specific library.

        Args:
            library_id: ID of the library

        Returns:
            List of component IDs that have a USES relationship to the library
        """
        return self._used_by.get(library_id, [])

    def get_used_libraries(self, comp_id: str) -> List[str]:
        """
        Get the libraries a component uses (inverse of get_uses_consumers).

        Args:
            comp_id: ID of the Application or Library

        Returns:
            List of library IDs the component has a USES relationship to
        """
        return self._uses.get(comp_id, [])

    def get_node_allocations(self) -> Dict[str, List[str]]:
        """
        Get node allocations (Node -> [Apps]).
        
        Returns:
            Dict mapping node ID to list of allocated component IDs
        """
        return dict(self._hosts)

    def get_broker_routing(self) -> Dict[str, List[str]]:
        """
        Get broker routing (Broker -> [Topics]).

        The internal `_routing` index is keyed by *topic*, so it is inverted
        here to match the shape every consumer expects — the same shape as
        `IGraphRepository.get_broker_routing`.

        Returns:
            Dict mapping broker ID to list of routed topic IDs
        """
        routing: Dict[str, List[str]] = defaultdict(list)
        for topic_id, brokers in self._routing.items():
            for broker_id, _weight in brokers:
                routing[broker_id].append(topic_id)
        return dict(routing)

    def get_dependency_edges(self) -> List[Tuple[str, str, float, str]]:
        """
        Derive the whole DEPENDS_ON projection as (dependent, dependency, weight, type).

        Implements Rules 1-7 of docs/graph-model.md §4.4 from this graph's own raw
        structural indices. Derived here rather than read off the analysis graph:
        saag/simulation/ never consumes derived edges (see the module docstring and
        tests/test_independence_guarantee.py), so the projection is recomputed from
        PUBLISHES_TO / SUBSCRIBES_TO / ROUTES / RUNS_ON / USES / CONNECTS_TO.

        Direction is dependent -> dependency, the reverse of data flow: if A publishes
        to a topic B subscribes to, B depends on A.

        Reads the raw index dicts directly rather than the accessors, which consult
        ``_failed_edges``. IM(v) is a development-time question about the intact
        architecture, so a severed runtime link must not change the answer.

        Memoised; ``_load_from_data`` invalidates the cache.
        """
        if self._dependency_edges is not None:
            return self._dependency_edges

        types = {cid: c.type for cid, c in self.components.items()}
        topic_w = {
            tid: getattr(self.components[tid], "weight", MIN_TOPIC_WEIGHT)
            for tid in self.topics if tid in self.components
        }

        def endpoints(index: Dict[str, List[Tuple[str, float]]], topic: str) -> List[str]:
            return [comp for comp, _ in index.get(topic, [])]

        # Applications reaching each Library over 1..USES_CHAIN_DEPTH hops.
        lib_users: Dict[str, List[str]] = defaultdict(list)
        for app, app_type in types.items():
            if app_type != "Application":
                continue
            reached: Set[str] = set()
            frontier = [app]
            for _ in range(USES_CHAIN_DEPTH):
                frontier = [
                    lib for src in frontier
                    for lib in self._uses.get(src, []) if lib not in reached
                ]
                reached.update(frontier)
            for lib in reached:
                lib_users[lib].append(app)

        # (dependent, dependency, type) -> set of mediating topics.
        #
        # A *set*, deliberately: a topic reachable both directly and through a
        # library must enter the probabilistic union once. memory_repo.py's
        # _collapse_paths flattens a per-kind dict into a multiset instead, which
        # double-counts such topics (0 affected pairs on atm_system, 64 on
        # microservices with dw <= 0.248, 139 on av with dw <= 0.374). Neo4jRepository
        # does not share that bug -- it MERGEs each kind separately and keeps the
        # max -- so this matches Neo4j, not memory_repo.
        paths: Dict[Tuple[str, str, str], Set[str]] = defaultdict(set)

        for topic in set(self._publishers) | set(self._subscribers) | set(self._routing):
            publishers = endpoints(self._publishers, topic)
            subscribers = endpoints(self._subscribers, topic)
            pubs = [p for p in publishers if types.get(p) in MESSAGING_TYPES]
            subs = [s for s in subscribers if types.get(s) in MESSAGING_TYPES]

            # Rule 1 -- app_to_app: a subscriber depends on every publisher.
            for sub in subs:
                for pub in pubs:
                    if sub != pub:
                        paths[(sub, pub, "app_to_app")].add(topic)
            # ... and the same holds when either side reaches the topic through a
            # library it uses rather than through its own edge.
            for lib in subscribers:
                for app in lib_users.get(lib, []):
                    for pub in pubs:
                        if app != pub:
                            paths[(app, pub, "app_to_app")].add(topic)
            for lib in publishers:
                for app in lib_users.get(lib, []):
                    for sub in subs:
                        if sub != app:
                            paths[(sub, app, "app_to_app")].add(topic)

            # Rule 2 -- app_to_broker: a participant depends on every routing broker.
            participants = set(publishers) | set(subscribers)
            for broker in endpoints(self._routing, topic):
                if types.get(broker) != "Broker":
                    continue
                for comp in participants:
                    if types.get(comp) in MESSAGING_TYPES:
                        paths[(comp, broker, "app_to_broker")].add(topic)
                for lib in participants:
                    for app in lib_users.get(lib, []):
                        paths[(app, broker, "app_to_broker")].add(topic)

        edges: Dict[Tuple[str, str, str], float] = {
            key: compute_effective_edge_weight([topic_w.get(t, MIN_TOPIC_WEIGHT) for t in topics])
            for key, topics in paths.items()
        }

        # Rules 3 & 4 -- lift component dependencies onto the hosting nodes.
        # Worst-case max, not a probabilistic union: the lifted dependencies
        # aggregate over overlapping app and topic sets, so they are not
        # independent events and the union would saturate.
        node_node: Dict[Tuple[str, str], List[float]] = defaultdict(list)
        node_broker: Dict[Tuple[str, str], List[float]] = defaultdict(list)
        for (src, tgt, dep_type), weight in edges.items():
            src_node, tgt_node = self._hosted_on.get(src), self._hosted_on.get(tgt)
            if src_node and tgt_node and src_node != tgt_node:
                node_node[(src_node, tgt_node)].append(weight)
            if dep_type == "app_to_broker" and src_node:
                node_broker[(src_node, tgt)].append(weight)

        lifted: Dict[Tuple[str, str, str], float] = {
            (n1, n2, "node_to_node"): compute_lifted_edge_weight(ws)
            for (n1, n2), ws in node_node.items()
        }
        lifted.update({
            (node, broker, "node_to_broker"): compute_lifted_edge_weight(ws)
            for (node, broker), ws in node_broker.items()
        })
        edges.update(lifted)

        # Rule 7 -- node_to_device: a host depends on every switch/router that
        # separates it from a host it depends on. Nothing without NetworkDevices.
        devices = [cid for cid, t in types.items() if t == "NetworkDevice"]
        if devices:
            node_deps = [(n1, n2, w) for (n1, n2, d), w in lifted.items() if d == "node_to_node"]
            for (node, device), (w, _n) in derive_node_to_device(self.fabric(), devices, node_deps).items():
                edges[(node, device, "node_to_device")] = w

        # Rule 5 -- app_to_lib: harmonic coupling between user and library.
        for src, libs in self._uses.items():
            if types.get(src) not in MESSAGING_TYPES:
                continue
            for lib in libs:
                if types.get(lib) != "Library":
                    continue
                edges[(src, lib, "app_to_lib")] = compute_harmonic_coupling(
                    getattr(self.components[src], "weight", MIN_TOPIC_WEIGHT),
                    getattr(self.components[lib], "weight", MIN_TOPIC_WEIGHT),
                )

        # Rule 6 -- broker_to_broker: colocated brokers share their node's fate.
        for node_id, hosted in self._hosts.items():
            brokers = [c for c in hosted if types.get(c) == "Broker"]
            node_w = getattr(self.components.get(node_id), "weight", MIN_TOPIC_WEIGHT)
            for b1 in brokers:
                for b2 in brokers:
                    if b1 != b2:
                        key = (b1, b2, "broker_to_broker")
                        edges[key] = max(edges.get(key, 0.0), node_w)

        self._dependency_edges = [(s, t, w, d) for (s, t, d), w in edges.items()]
        return self._dependency_edges

    def get_depends_on_targets(self, comp_id: str) -> List[str]:
        """
        Get the components that `comp_id` depends on (outgoing DEPENDS_ON arcs).

        Thin view over :meth:`get_dependency_edges`; see it for the derivation.

        Returns:
            List of component IDs that `comp_id` depends on (may be empty).
        """
        return [tgt for src, tgt, _, _ in self.get_dependency_edges() if src == comp_id]
    
    # =========================================================================
    # Layer Filtering
    # =========================================================================
    
    def _layer_def(self, layer: str, warn: bool = False):
        """Resolve a layer name to its definition, falling back to 'system'."""
        try:
            sim_layer = SimulationLayer.from_string(layer)
        except ValueError:
            if warn:
                self.logger.warning(f"Unknown layer '{layer}', defaulting to 'system'")
            sim_layer = SimulationLayer.SYSTEM
        return SIMULATION_LAYERS[sim_layer]

    def get_components_by_layer(self, layer: str) -> List[str]:
        """
        Get component IDs included in a specific layer's simulation graph.

        Layers:
            - app: Application, Topic, Library components
            - infra: Node, Application, Broker components
            - mw: Broker, Topic, Application components
            - system: All components

        Args:
            layer: Layer name (app, infra, mw, system) or string alias

        Returns:
            List of component IDs included in the layer's graph
        """
        types = self._layer_def(layer, warn=True).component_types
        return [c.id for c in self.components.values() if c.type in types]

    def get_analyze_components_by_layer(self, layer: str) -> List[str]:
        """
        Get component IDs to analyze/report for a specific layer.

        This returns only the components that should be analyzed,
        not all components in the simulation graph.

        Args:
            layer: Layer name (app, infra, mw, system)

        Returns:
            List of component IDs to analyze
        """
        types = self._layer_def(layer).analyze_types
        return [c.id for c in self.components.values() if c.type in types]


    # =========================================================================
    # Summary Statistics
    # =========================================================================
    
    def get_summary(self) -> Dict[str, Any]:
        """Get summary statistics for the graph."""
        type_counts = defaultdict(int)
        for comp in self.components.values():
            type_counts[comp.type] += 1
        
        return {
            "total_nodes": len(self.components),
            "total_edges": self.graph.number_of_edges(),
            "component_types": dict(type_counts),
            "topics": len(self.topics),
            "pub_sub_paths": len(self.get_pub_sub_paths()),
            "active_components": sum(1 for c in self.components.values() if c.state == ComponentState.ACTIVE),
        }
