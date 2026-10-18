"""
In-Memory Graph Repository Adapter

Implements IGraphRepository using in-memory storage for testing.
"""

import math
from collections import defaultdict
from typing import Dict, Any, List, Optional

from saag.core.ports.graph_repository import IGraphRepository
from saag.core.models import (
    GraphData, ComponentData, EdgeData, QoSPolicy, compute_topic_weight,
    compute_effective_edge_weight, compute_harmonic_coupling, compute_power_mean_weight,
    compute_lifted_edge_weight,
    LIB_FANOUT_GAMMA, MIN_TOPIC_WEIGHT,
)
from saag.core.utils import serialization
from saag.core.network import (
    LINK_PROPERTIES, Fabric, baseline_load, derive_node_to_device,
    vertex_load_metrics,
)

#: Vertex collection -> vertex type label, in import order.
COMPONENT_TYPES: Dict[str, str] = {
    "nodes": "Node",
    "brokers": "Broker",
    "topics": "Topic",
    "applications": "Application",
    "libraries": "Library",
    "network_devices": "NetworkDevice",
}

#: Structural relationship keys, i.e. every relationship except derived DEPENDS_ON.
STRUCTURAL_RELATIONSHIPS = (
    "runs_on", "routes", "publishes_to", "subscribes_to", "connects_to", "uses",
)

#: Vertex types that participate in messaging and may consume libraries.
MESSAGING_TYPES = ("Application", "Library")

#: Maximum USES hops followed when deriving library-mediated dependencies,
#: matching the `USES*1..3` bound of the Cypher derivation rules.
USES_CHAIN_DEPTH = 3


def _empty_store() -> Dict[str, Any]:
    """A fresh, empty graph store."""
    return {
        "metadata": {},
        **{key: [] for key in COMPONENT_TYPES},
        "relationships": {key: [] for key in (*STRUCTURAL_RELATIONSHIPS, "depends_on")},
    }


class MemoryRepository:
    """
    In-memory adapter implementing IGraphRepository.

    Useful for testing without Neo4j dependency.
    """

    def __init__(self) -> None:
        """Initialize in-memory repository."""
        self.data: Dict[str, Any] = _empty_store()
        self.derived_stats: Dict[str, int] = {}

    def close(self) -> None:
        """No-op for in-memory repository."""
        pass

    def derive_dependencies(self) -> None:
        """
        Pre-analysis stage: derive DEPENDS_ON relationships and finalise
        DEPENDS_ON edge weights.
        """
        self._derive_dependencies()
        self._finalize_dependency_weights()

    def _calculate_intrinsic_weights(self) -> None:
        """
        Phase 3: Compute intrinsic Topic weights and propagate them to edges.

        The formula itself lives in ``core.models.compute_topic_weight`` so this
        adapter, the Cypher adapter and the simulation engines cannot drift apart.
        """
        # 1. Topic weights
        for topic in self.data["topics"]:
            qos = QoSPolicy.from_node_attrs(topic)
            # save_graph() flattens components before this runs (step 2 above),
            # and flatten_component renames "frequency" to "topic_frequency"
            # (saag.core.utils.serialization) to match the Neo4j property name
            # -- so a bare topic.get("frequency") is always None here and every
            # topic silently took the missing-frequency default. Every other
            # frequency-reading consumer in the codebase (structural_analyzer,
            # message_flow_simulator, visualization/collector,
            # prediction/data_preparation, validation/service) already guards
            # against this rename; this was the one that did not.
            freq = topic.get("frequency", topic.get("topic_frequency"))
            # Negative sizes are clamped rather than rejected, matching the
            # `size <= 0 -> 0.0` branch of the Cypher size-norm expression.
            topic["weight"] = compute_topic_weight(
                qos, max(0, topic.get("size", 256)), frequency=freq
            )

        # 2. Edge weights and QoS profiles (both inherit from the Topic).
        # The profile matters as much as the scalar: downstream consumers that
        # read per-edge QoS (the GNN edge-feature encoder) see only edges, and a
        # topology JSON never states edge-level QoS.
        topic_attrs = {
            t["id"]: (t["weight"], QoSPolicy.from_node_attrs(t).to_dict())
            for t in self.data["topics"]
        }

        for rel_type in ("publishes_to", "subscribes_to", "routes"):
            for rel in self.data["relationships"].get(rel_type, []):
                inherited = topic_attrs.get(rel.get("to"))
                if inherited is not None:
                    rel["weight"], profile = inherited
                    rel.setdefault("qos_profile", profile)

    def _targets_by_source(self, rel_type: str) -> Dict[str, List[str]]:
        """Index one relationship type as ``source id -> [target ids]``."""
        index: Dict[str, List[str]] = {}
        for rel in self.data["relationships"].get(rel_type, []):
            index.setdefault(rel["from"], []).append(rel["to"])
        return index

    def _calculate_aggregate_weights(self) -> None:
        """
        Phase 5a: propagate Topic weights up to Applications,
        Libraries, Brokers and Nodes. See docs/graph-model.md §4.5.
        """
        topic_weights = {t["id"]: t.get("weight", 1.0) for t in self.data["topics"]}
        outgoing = {rel: self._targets_by_source(rel) for rel in STRUCTURAL_RELATIONSHIPS}

        def connected_topic_weights(comp_id: str, rel_types) -> List[float]:
            return [
                topic_weights[tgt]
                for rel_type in rel_types
                for tgt in outgoing[rel_type].get(comp_id, [])
                if tgt in topic_weights
            ]

        # 1. Applications — Generalized Power Mean (p=3) over directly connected topics
        for app in self.data["applications"]:
            app["weight"] = compute_power_mean_weight(
                connected_topic_weights(app["id"], ("publishes_to", "subscribes_to"))
            )

        # 2. Libraries — max(own topics, consuming apps) amplified by fan-out
        app_weights = {a["id"]: a.get("weight", MIN_TOPIC_WEIGHT) for a in self.data["applications"]}
        consumers: Dict[str, set] = {}
        for src, libs in self._targets_by_source("uses").items():
            for lib_id in libs:
                consumers.setdefault(lib_id, set()).add(src)

        for lib in self.data["libraries"]:
            consuming_apps = [c for c in consumers.get(lib["id"], set()) if c in app_weights]
            base_w = max(
                [
                    *connected_topic_weights(lib["id"], ("publishes_to", "subscribes_to")),
                    *(app_weights[a] for a in consuming_apps),
                ],
                default=0.0,
            )
            multiplier = 1.0 + LIB_FANOUT_GAMMA * math.log2(1.0 + len(consuming_apps))
            lib["weight"] = (
                max(MIN_TOPIC_WEIGHT, min(1.0, base_w * multiplier))
                if base_w > 0 else MIN_TOPIC_WEIGHT
            )

        # 3. Applications, second pass — apps reaching topics only through libraries
        # would otherwise stay at the floor and be invisible to RM scoring.
        lib_weights = {l["id"]: l.get("weight", MIN_TOPIC_WEIGHT) for l in self.data["libraries"]}
        for app in self.data["applications"]:
            if app.get("weight", 1.0) > MIN_TOPIC_WEIGHT:
                continue
            used = [lib_weights[l] for l in outgoing["uses"].get(app["id"], []) if l in lib_weights]
            if used:
                app["weight"] = max(used)

        # 4. Brokers — Generalized Power Mean (p=3) over routed topics
        for broker in self.data["brokers"]:
            broker["weight"] = compute_power_mean_weight(
                connected_topic_weights(broker["id"], ("routes",))
            )

        # 5. Nodes — worst-case hosted component
        component_weights = {
            **{a["id"]: a["weight"] for a in self.data["applications"]},
            **{b["id"]: b["weight"] for b in self.data["brokers"]},
        }
        hosted: Dict[str, List[float]] = {}
        for src, node_ids in outgoing["runs_on"].items():
            for node_id in node_ids:
                hosted.setdefault(node_id, []).append(
                    component_weights.get(src, MIN_TOPIC_WEIGHT)
                )

        for node in self.data["nodes"]:
            node["weight"] = max(hosted.get(node["id"], [MIN_TOPIC_WEIGHT]))

        # 6. Network devices — floor here; Phase 5b lifts them once Rule 7 exists
        for device in self.data["network_devices"]:
            device["weight"] = MIN_TOPIC_WEIGHT

    # ==========================================
    # Phase 5c: baseline network load
    # ==========================================

    def _has_network_model(self) -> bool:
        """Switches/routers or link capacities declared: the network is modelled."""
        return bool(self.data["network_devices"]) or any(
            rel.get("capacity_mbps") is not None
            for rel in self.data["relationships"]["connects_to"]
        )

    def _calculate_network_load(self) -> None:
        """Route the healthy pub-sub flows over CONNECTS_TO and store link load.

        Links get ``load_mbps`` / ``utilization``; hosts and devices get
        ``max_link_utilization`` / ``link_load_mbps``. A no-op unless the
        topology models its network (devices or capacities).
        """
        if not self._has_network_model():
            return
        hosts_of: Dict[str, List[str]] = {}
        for rel in self.data["relationships"]["runs_on"]:
            hosts_of.setdefault(rel["from"], []).append(rel["to"])
        links = self.data["relationships"]["connects_to"]
        fabric, state = baseline_load(
            vertices=[v["id"] for key in ("nodes", "network_devices") for v in self.data[key]],
            links=[(l["from"], l["to"], l.get("capacity_mbps")) for l in links],
            publishers_of=self._sources_by_target("publishes_to"),
            subscribers_of=self._sources_by_target("subscribes_to"),
            routers_of=self._sources_by_target("routes"),
            hosts_of=hosts_of,
            topic_attrs={t["id"]: t for t in self.data["topics"]},
        )
        for l in links:
            key = frozenset((l["from"], l["to"]))
            l["load_mbps"] = state.link_load.get(key, 0.0)
            if state.link_util.get(key) is not None:
                l["utilization"] = state.link_util[key]
        metrics = vertex_load_metrics(fabric, state)
        for key in ("nodes", "network_devices"):
            for v in self.data[key]:
                v.update(metrics.get(v["id"], {}))

    # ==========================================
    # Phase 4: DEPENDS_ON derivation (Rules 1–7)
    # ==========================================

    def _sources_by_target(self, rel_type: str) -> Dict[str, List[str]]:
        """Index one relationship type as ``target id -> [source ids]``."""
        index: Dict[str, List[str]] = {}
        for rel in self.data["relationships"].get(rel_type, []):
            index.setdefault(rel["to"], []).append(rel["from"])
        return index

    def _library_users(self) -> Dict[str, List[str]]:
        """Map each Library to the Applications reaching it over 1..3 USES hops."""
        uses = self._targets_by_source("uses")
        users: Dict[str, List[str]] = {}
        for app in self.data["applications"]:
            reached: set = set()
            frontier = [app["id"]]
            for _ in range(USES_CHAIN_DEPTH):
                frontier = [
                    lib for src in frontier
                    for lib in uses.get(src, []) if lib not in reached
                ]
                reached.update(frontier)
            for lib in reached:
                users.setdefault(lib, []).append(app["id"])
        return users

    def _derive_dependencies(self) -> None:
        """
        Derive DEPENDS_ON edges from structural edges (Rules 1–7, docs/graph-model.md §4.4).

        Direction convention: dependent -> dependency, the reverse of data flow. If
        Application A publishes to a Topic that Application B subscribes to, B depends on A
        (B *would* fail if A failed), so the edge is B -> A. Symmetrically, app_to_lib edges
        point from the using application to the library it uses. This is deliberate, not a
        bug: a DEPENDS_ON arrow is drawn against the direction message data actually flows.
        """
        types = {
            item["id"]: comp_type
            for key, comp_type in COMPONENT_TYPES.items()
            for item in self.data.get(key, [])
        }
        topic_weights = {t["id"]: t.get("weight", MIN_TOPIC_WEIGHT) for t in self.data["topics"]}
        publishers_of = self._sources_by_target("publishes_to")
        subscribers_of = self._sources_by_target("subscribes_to")
        brokers_of = self._sources_by_target("routes")
        lib_users = self._library_users()
        host_of = {rel["from"]: rel["to"] for rel in self.data["relationships"].get("runs_on", [])}

        edges = {
            **self._rule_app_to_app(types, topic_weights, publishers_of, subscribers_of, lib_users),
            **self._rule_app_to_broker(types, topic_weights, publishers_of, subscribers_of,
                                       brokers_of, lib_users),
        }
        edges.update(self._rules_lift_to_nodes(edges, host_of))
        edges.update(self._rule_node_to_device(edges))
        edges.update(self._rule_app_to_lib(types))
        edges.update(self._rule_broker_to_broker(types))

        self.data["relationships"]["depends_on"] = [
            {"from": src, "to": tgt, "dependency_type": dep_type, **edge}
            for (src, tgt, dep_type), edge in edges.items()
        ]

    @staticmethod
    def _collapse_paths(paths: Dict, dep_type: str, topic_weights: Dict[str, float]) -> Dict:
        """
        Collapse the per-pair topic sets of one rule into a single edge each.

        Weight is the probabilistic union (effective coupling) over every mediating
        topic: w_E = 1 - ∏ (1 - w(t)); ``path_count`` is the number of distinct mediating
        topics on the richest path kind.
        """
        return {
            (src, tgt, dep_type): {
                "weight": compute_effective_edge_weight(
                    [topic_weights[t] for topics in kinds.values() for t in topics]
                ),
                "path_count": max(len(topics) for topics in kinds.values()),
            }
            for (src, tgt), kinds in paths.items()
        }

    def _rule_app_to_app(self, types, topic_weights, publishers_of, subscribers_of, lib_users) -> Dict:
        """Rule 1 — a subscriber depends on every publisher of the topics it consumes."""
        paths = defaultdict(lambda: defaultdict(set))
        for topic, publishers in publishers_of.items():
            subscribers = subscribers_of.get(topic, [])
            pubs = [p for p in publishers if types.get(p) in MESSAGING_TYPES]
            subs = [s for s in subscribers if types.get(s) in MESSAGING_TYPES]

            for sub in subs:
                for pub in pubs:
                    if sub != pub:
                        paths[(sub, pub)]["direct"].add(topic)

            # The same dependency also holds when either side reaches the topic
            # through a library it uses rather than through its own edge.
            for lib in subscribers:
                for app in lib_users.get(lib, []):
                    for pub in pubs:
                        if app != pub:
                            paths[(app, pub)]["via_subscribed_lib"].add(topic)
            for lib in publishers:
                for app in lib_users.get(lib, []):
                    for sub in subs:
                        if sub != app:
                            paths[(sub, app)]["via_published_lib"].add(topic)

        return self._collapse_paths(paths, "app_to_app", topic_weights)

    def _rule_app_to_broker(self, types, topic_weights, publishers_of, subscribers_of,
                            brokers_of, lib_users) -> Dict:
        """Rule 2 — a component depends on every broker routing a topic it uses."""
        paths = defaultdict(lambda: defaultdict(set))
        for topic, brokers in brokers_of.items():
            participants = set(publishers_of.get(topic, []) + subscribers_of.get(topic, []))
            for broker in brokers:
                if types.get(broker) != "Broker":
                    continue
                for comp in participants:
                    if types.get(comp) in MESSAGING_TYPES:
                        paths[(comp, broker)]["direct"].add(topic)
                for lib in participants:
                    for app in lib_users.get(lib, []):
                        paths[(app, broker)]["via_lib"].add(topic)

        return self._collapse_paths(paths, "app_to_broker", topic_weights)

    @staticmethod
    def _rules_lift_to_nodes(edges: Dict, host_of: Dict[str, str]) -> Dict:
        """
        Rules 3 & 4 — lift component-level dependencies onto the nodes hosting them.

        node_to_node needs both endpoints hosted on *different* nodes; node_to_broker
        only needs the dependent app to be hosted, since the broker is the target.

        Weight is the worst-case lift (``compute_lifted_edge_weight``), matching
        ``Neo4jRepository``'s Rules 3-4. A probabilistic union is not valid here:
        the lifted dependencies are aggregates over overlapping sets of hosted
        apps and mediating topics, not independent events, and saturates almost
        every edge to ~1.0 on the committed corpus.
        """
        node_node: Dict = {}
        node_broker: Dict = {}
        for (src, tgt, dep_type), edge in edges.items():
            if dep_type not in ("app_to_app", "app_to_broker"):
                continue
            src_node, tgt_node = host_of.get(src), host_of.get(tgt)
            if src_node and tgt_node and src_node != tgt_node:
                node_node.setdefault((src_node, tgt_node), []).append(edge["weight"])
            if dep_type == "app_to_broker" and src_node:
                node_broker.setdefault((src_node, tgt), []).append(edge["weight"])

        lifted = {
            (n1, n2, "node_to_node"): {
                "weight": compute_lifted_edge_weight(weights),
                "path_count": len(weights),
            }
            for (n1, n2), weights in node_node.items()
        }
        lifted.update({
            (node, broker, "node_to_broker"): {
                "weight": compute_lifted_edge_weight(weights),
                "path_count": len(weights),
            }
            for (node, broker), weights in node_broker.items()
        })
        return lifted

    def _rule_app_to_lib(self, types) -> Dict:
        """
        Rule 5 — a component depends on every library it directly uses.

        The weight is a placeholder until ``_finalize_dependency_weights`` copies
        the consuming component's own weight onto the edge.
        """
        return {
            (rel["from"], rel["to"], "app_to_lib"): {"weight": MIN_TOPIC_WEIGHT, "path_count": 1}
            for rel in self.data["relationships"].get("uses", [])
            if types.get(rel["from"]) in MESSAGING_TYPES and types.get(rel["to"]) == "Library"
        }

    def _rule_broker_to_broker(self, types) -> Dict:
        """
        Rule 6 — brokers sharing a physical node share its fate (bidirectional).

        The weight is a placeholder until ``_finalize_dependency_weights`` copies
        the shared node's weight onto the edge.
        """
        brokers_per_node: Dict[str, List[str]] = {}
        for rel in self.data["relationships"].get("runs_on", []):
            if types.get(rel["from"]) == "Broker":
                brokers_per_node.setdefault(rel["to"], []).append(rel["from"])

        shared_nodes: Dict = {}
        for node_id, brokers in brokers_per_node.items():
            for b1 in brokers:
                for b2 in brokers:
                    if b1 != b2:
                        shared_nodes.setdefault((b1, b2), set()).add(node_id)

        return {
            (b1, b2, "broker_to_broker"): {"weight": MIN_TOPIC_WEIGHT, "path_count": len(nodes)}
            for (b1, b2), nodes in shared_nodes.items()
        }

    def _rule_node_to_device(self, edges: Dict) -> Dict:
        """Rule 7 — a host depends on a switch/router every path to its dependencies crosses."""
        devices = [d["id"] for d in self.data["network_devices"]]
        if not devices:
            return {}
        fabric = Fabric(
            [v["id"] for key in ("nodes", "network_devices") for v in self.data[key]],
            [(l["from"], l["to"], None) for l in self.data["relationships"]["connects_to"]],
        )
        node_deps = [
            (src, tgt, edge["weight"])
            for (src, tgt, dep_type), edge in edges.items() if dep_type == "node_to_node"
        ]
        return {
            (node, device, "node_to_device"): {"weight": w, "path_count": n}
            for (node, device), (w, n) in derive_node_to_device(fabric, devices, node_deps).items()
        }

    def _finalize_dependency_weights(self) -> None:
        """
        Phase 5b: give the Rule 5 and Rule 6 edges the component
        weights they could not carry when they were derived, and give
        NetworkDevices the weight of the hosts that depend on them (Rule 7).
        """
        comp_weights = {
            item["id"]: item.get("weight", MIN_TOPIC_WEIGHT)
            for key in COMPONENT_TYPES
            for item in self.data.get(key, [])
        }
        nodes_of = {
            src: set(targets)
            for src, targets in self._targets_by_source("runs_on").items()
        }

        for edge in self.data["relationships"].get("depends_on", []):
            if edge["dependency_type"] == "app_to_lib":
                # Harmonic mean between the consuming Application/Library and the used Library
                w_app = comp_weights.get(edge["from"], MIN_TOPIC_WEIGHT)
                w_lib = comp_weights.get(edge["to"], MIN_TOPIC_WEIGHT)
                edge["weight"] = compute_harmonic_coupling(w_app, w_lib)
            elif edge["dependency_type"] == "broker_to_broker":
                # Inherits the worst-case weight of the nodes both brokers share
                shared = nodes_of.get(edge["from"], set()) & nodes_of.get(edge["to"], set())
                edge["weight"] = max(
                    (comp_weights.get(n, MIN_TOPIC_WEIGHT) for n in shared),
                    default=MIN_TOPIC_WEIGHT,
                )

        # A switch/router inherits the worst-case weight of the hosts that depend on it
        dependents: Dict[str, List[float]] = {}
        for edge in self.data["relationships"].get("depends_on", []):
            if edge["dependency_type"] == "node_to_device":
                dependents.setdefault(edge["to"], []).append(
                    comp_weights.get(edge["from"], MIN_TOPIC_WEIGHT)
                )
        for device in self.data["network_devices"]:
            device["weight"] = max(dependents.get(device["id"], []), default=MIN_TOPIC_WEIGHT)

        # An app_to_lib edge still at the floor means _calculate_aggregate_weights()
        # never gave the consuming application a real weight — either it did not run
        # or a new rule was added without a finalization step. (broker_to_broker may
        # legitimately sit at the floor when the shared node itself does.)
        stale = [
            (e["from"], e["to"])
            for e in self.data["relationships"].get("depends_on", [])
            if e["dependency_type"] == "app_to_lib"
            and e.get("weight", MIN_TOPIC_WEIGHT) <= MIN_TOPIC_WEIGHT
        ]
        if stale:
            raise ValueError(
                f"_finalize_dependency_weights: {len(stale)} app_to_lib edge(s) still "
                f"carry the {MIN_TOPIC_WEIGHT} placeholder weight after finalization. "
                f"This means _calculate_aggregate_weights() did not set a real weight "
                f"for the consuming application. Affected edges: {stale[:5]}"
            )

    def save_graph(self, data: Dict[str, Any], clear: bool = False) -> None:
        """
        Save graph data to in-memory storage, performing normalization/flattening
        to simulate Neo4j persistence behavior (Phases 1–3 and 5).
        """
        if clear:
            self.data = _empty_store()

        # 1. Metadata
        if "metadata" in data:
            self.data["metadata"] = serialization.flatten_metadata(data["metadata"])

        # 2. Components — flattened to the scalar-property shape a graph store holds
        for key, comp_type in COMPONENT_TYPES.items():
            stored = {item["id"]: item for item in self.data[key]}
            for item in data.get(key, []):
                flattened = serialization.flatten_component(item, comp_type)
                existing = stored.get(flattened["id"])
                if existing:
                    existing.update(flattened)
                else:
                    self.data[key].append(flattened)
                    stored[flattened["id"]] = flattened

        # 3. Structural relationships
        for key in STRUCTURAL_RELATIONSHIPS:
            for item in data.get("relationships", {}).get(key, []):
                rel = {
                    "from": item.get("from", item.get("source")),
                    "to": item.get("to", item.get("target")),
                    "weight": item.get("weight", 1.0),
                }
                if key == "connects_to":
                    rel.update({k: item[k] for k in LINK_PROPERTIES if k in item})
                self.data["relationships"][key].append(rel)

        # 4. Topic fan-out augmentation (parity with Neo4j)
        subscribers_of = self._sources_by_target("subscribes_to")
        publishers_of = self._sources_by_target("publishes_to")
        for topic in self.data["topics"]:
            topic["subscriber_count"] = len(set(subscribers_of.get(topic["id"], [])))
            topic["publisher_count"] = len(set(publishers_of.get(topic["id"], [])))

        # 5. Weights
        self._calculate_intrinsic_weights()
        self._calculate_aggregate_weights()
        self._calculate_network_load()


    def get_graph_data(
        self,
        component_types: Optional[List[str]] = None,
        dependency_types: Optional[List[str]] = None,
        include_raw: bool = False,
    ) -> GraphData:
        """Retrieve graph data with optional type filtering."""
        components = []
        for key, comp_type in COMPONENT_TYPES.items():
            if component_types and comp_type not in component_types:
                continue

            for item in self.data.get(key, []):
                components.append(ComponentData(
                    id=item["id"],
                    component_type=comp_type,
                    weight=item.get("weight", 1.0),
                    properties={k: v for k, v in item.items() if k not in ("id", "weight")}
                ))

        id_to_type = {c.id: c.component_type for c in components}
        edges = []

        # 1. Fetch DEPENDS_ON edges
        for item in self.data["relationships"].get("depends_on", []):
            dep_type = item.get("dependency_type")
            if dependency_types and dep_type not in dependency_types:
                continue
            
            src_id = item["from"]
            tgt_id = item["to"]
            
            edges.append(EdgeData(
                source_id=src_id,
                target_id=tgt_id,
                source_type=id_to_type.get(src_id, "Unknown"),
                target_type=id_to_type.get(tgt_id, "Unknown"),
                dependency_type=dep_type,
                relation_type="DEPENDS_ON",
                weight=item.get("weight", 1.0),
                path_count=item.get("path_count", 1),
                properties={k: v for k, v in item.items() if k not in ["from", "to", "weight", "dependency_type", "path_count"]}
            ))

        # 2. Optionally include raw structural edges
        if include_raw:
            for rel_type, items in self.data.get("relationships", {}).items():
                if rel_type == "depends_on":
                    continue
                for item in items:
                    src_id = item.get("from", item.get("source"))
                    tgt_id = item.get("to", item.get("target"))
                    edges.append(EdgeData(
                        source_id=src_id,
                        target_id=tgt_id,
                        source_type=id_to_type.get(src_id, "Unknown"),
                        target_type=id_to_type.get(tgt_id, "Unknown"),
                        dependency_type=rel_type.lower(),
                        relation_type=rel_type.upper(),
                        weight=item.get("weight", 1.0),
                        path_count=1,
                        properties={k: v for k, v in item.items() if k not in ["from", "to", "source", "target", "weight"]}
                    ))
        
        return GraphData(components=components, edges=edges)

    def get_layer_data(self, layer: str) -> GraphData:
        """Retrieve graph data for a specific layer project."""
        from saag.core.layers import get_layer_definition, resolve_layer
        defn = get_layer_definition(resolve_layer(layer))
        return self.get_graph_data(
            component_types=defn.component_types,
            dependency_types=defn.dependency_types
        )

    def get_statistics(self) -> Dict[str, int]:
        """Retrieve graph statistics."""
        stats = {f"{key[:-1]}_count": len(self.data.get(key, [])) for key in COMPONENT_TYPES}
        stats["total_nodes"] = sum(stats.values())
        stats["total_relationships"] = sum(
            len(rels) for rels in self.data.get("relationships", {}).values()
        )
        return stats

    def export_json(self) -> Dict[str, Any]:
        """
        Export graph as JSON (compatible with data generation format).
        Consolidated via get_graph_data to ensure logic consistency and fidelity parity
        with the Neo4j persistence layer.
        """
        graph_data = self.get_graph_data(include_raw=True)
        return serialization.reconstruct_export_payload(graph_data, self.data["metadata"])
