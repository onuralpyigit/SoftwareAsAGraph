"""
saag/core/graph_io.py — build a NetworkX graph from a topology JSON
===================================================================

Both loaders below used to live in ``cli/`` as underscore-private helpers, and
fifteen modules under ``reproduce/`` imported them by those private names
(``from cli.loso_evaluate import _build_graph_from_json``). That inverted the
dependency the architecture describes: the replication package reached into the
presentation layer for a core primitive, and a rename in a CLI script would have
broken the paper's reproduction with no test to catch it.

They are kept as **two** functions rather than merged. They produce the same
nodes and edges but not the same node attributes -- fingerprinting both over
``atm_system``, ``av_system`` and ``realworld_edgex`` gives identical
node/edge counts and different attribute hashes -- so collapsing them would
silently change what every downstream artifact was computed from. Which one a
caller wants is a real choice:

* :func:`build_graph_from_json` takes a parsed topology dict. This is the one the
  evaluation harnesses use.
* :func:`load_graph` takes a path and additionally handles the per-entity
  ``type`` field carried by the five real-world topologies, which collides with
  the ``type=`` keyword the other builder passes.

``cli/loso_evaluate.py`` and ``cli/simulate_graph.py`` re-export these under
their original private names, so existing imports keep working.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import logging

import networkx as nx

from saag.core.models import QoSPolicy, topic_weight_from_node_attrs
from saag.core.network import LINK_PROPERTIES

logger = logging.getLogger("saag.core.graph_io")

_TOPIC_MEDIATED_EDGES = ("PUBLISHES_TO", "SUBSCRIBES_TO", "ROUTES")


def _project_topic_qos_onto_edges(g: nx.DiGraph) -> None:
    """Inherit each Topic's w(t) and QoS profile onto its incident pub/sub edges.

    Mirrors the ``SET r.weight = t.weight`` inheritance the repositories perform
    on import (see ``Neo4jRepository._calculate_intrinsic_weights``). Topology
    JSON carries QoS on Topic *nodes* only and states no edge attributes at all,
    so without this pass every pub/sub edge reaching a consumer has
    ``weight=1.0`` and ``qos_profile={}`` — constant across the whole graph.
    That is what made the GNN's QoS edge dimensions carry no signal.

    Existing non-default values are left alone, so a topology that does state
    edge-level QoS keeps it.
    """
    for u, v, data in g.edges(data=True):
        etype = (data.get("type") or data.get("etype") or "").upper()
        if etype not in _TOPIC_MEDIATED_EDGES:
            continue

        # Topic is the target on PUBLISHES_TO/SUBSCRIBES_TO/ROUTES, but tolerate
        # either orientation rather than silently skipping a reversed edge.
        topic = v if g.nodes.get(v, {}).get("type") == "Topic" else u
        attrs = g.nodes.get(topic, {})
        if attrs.get("type") != "Topic":
            continue

        if not data.get("qos_profile"):
            data["qos_profile"] = QoSPolicy.from_node_attrs(attrs).to_dict()
        # An explicit ``weight: 1.0`` counts as absent, not as a stated value.
        # The real-world architecture adapters emit ``weight: 1.0`` on every
        # edge while the synthetic generator omits the key entirely, so a bare
        # ``"weight" not in data`` guard applied the QoS weight to generated
        # topologies and skipped it on transcribed ones -- leaving all five
        # open-source systems with constant edge weights, which is precisely the
        # signal-free condition this pass exists to prevent. 1.0 is the default
        # rather than a meaningful contract, so it is safe to overwrite; any
        # other stated value is still honoured.
        existing = data.get("weight")
        if existing is None or abs(float(existing) - 1.0) < 1e-9:
            data["weight"] = topic_weight_from_node_attrs(attrs)


def build_graph_from_json(topology: Dict[str, Any]) -> nx.DiGraph:
    """Lightweight builder, peer of cli/simulate_graph.py:_load_graph fallback path."""
    g = nx.DiGraph()

    type_buckets = [
        ("applications", "Application"),
        ("brokers", "Broker"),
        ("topics", "Topic"),
        ("nodes", "Node"),
        ("libraries", "Library"),
        ("network_devices", "NetworkDevice"),
    ]
    for key, type_label in type_buckets:
        for entity in topology.get(key, []):
            # ``type`` is excluded from the splat, not just ``id``/``name``: the
            # three real-world topologies carry a per-entity ``type`` field, and
            # letting it through collided with the keyword above
            # (``TypeError: got multiple values for keyword argument 'type'``),
            # so this builder raised on every one of them. The canonical bucket
            # label wins because the node-type contract downstream
            # (``resolve_eval_keys``, ``networkx_to_hetero_data``) is keyed on it;
            # where both are present they agree anyway.
            g.add_node(
                entity["id"],
                type=type_label,
                name=entity.get("name", entity["id"]),
                **{k: v for k, v in entity.items() if k not in ("id", "name", "type")},
            )

    rels = topology.get("relationships", {}) or {}

    edge_buckets = [
        (rels.get("publishes_to", []) + topology.get("publishes", []), "PUBLISHES_TO"),
        (rels.get("subscribes_to", []) + topology.get("subscribes", []), "SUBSCRIBES_TO"),
        (rels.get("routes", []) + topology.get("routes", []), "ROUTES"),
        (rels.get("runs_on", []) + topology.get("runs_on", []), "RUNS_ON"),
        (rels.get("connects_to", []) + topology.get("connects_to", []), "CONNECTS_TO"),
        (rels.get("uses", []) + topology.get("uses", []), "USES"),
        (rels.get("depends_on", []), "DEPENDS_ON"),
    ]
    for items, type_label in edge_buckets:
        for r in items:
            src = (
                r.get("source") or r.get("from")
                or r.get("application_id") or r.get("topic_id")
                or r.get("node_id") or r.get("broker_id")
            )
            dst = (
                r.get("target") or r.get("to")
                or r.get("topic_id") or r.get("broker_id")
                or r.get("application_id") or r.get("node_id")
            )
            if src and dst and src != dst:
                attrs: Dict[str, Any] = {
                    "type": r.get("type", type_label),
                    "qos_profile": r.get("qos_profile", {}),
                }
                # Only pin a weight the topology actually stated, so the
                # projection below can tell "unset" from "deliberately 1.0".
                if r.get("weight") is not None:
                    attrs["weight"] = float(r["weight"])
                if type_label == "CONNECTS_TO":
                    attrs.update({k: r[k] for k in LINK_PROPERTIES if k in r})
                g.add_edge(src, dst, **attrs)

    _project_topic_qos_onto_edges(g)
    for _, _, data in g.edges(data=True):
        data.setdefault("weight", 1.0)
    return g


def load_graph(input_path: Path):
    """
    Load a SaG graph from a scenario JSON file and return a NetworkX DiGraph.
    """
    import networkx as nx

    with open(input_path) as fh:
        data = json.load(fh)

    g = nx.DiGraph()
    g.graph["id"] = input_path.stem

    # Nodes.
    # ``type`` is excluded from the splat, not just ``id``/``name``: the five
    # real-world topologies carry a per-entity ``type`` field, and letting it
    # through collided with the keyword below (``TypeError: got multiple values
    # for keyword argument 'type'``), so this loader raised on every one of
    # them — which is why no real-world system had fault-injection labels. The
    # canonical bucket label wins because the node-type contract downstream
    # (``--node-types``, the injector's own filters) is keyed on it; where both
    # are present they agree anyway. Same fix as
    # ``cli/loso_evaluate.py:_build_graph_from_json``.
    _SPLAT_EXCLUDE = ("id", "name", "type")
    for bucket, type_label in (
        ("applications", "Application"),
        ("brokers", "Broker"),
        ("topics", "Topic"),
        ("nodes", "Node"),
        # Libraries must be added explicitly. Without this they are still
        # created implicitly by their USES edges, but with type=None — so they
        # never match a --node-types filter and silently receive no ground
        # truth at all.
        ("libraries", "Library"),
        ("network_devices", "NetworkDevice"),
    ):
        for entity in data.get(bucket, []):
            g.add_node(entity["id"], type=type_label,
                       name=entity.get("name", entity["id"]),
                       **{k: v for k, v in entity.items()
                          if k not in _SPLAT_EXCLUDE})

    # Edges
    # Support both flat and nested 'relationships' structure
    rels = data.get("relationships", {})
    
    # 1. PUBLISHES_TO
    pub_list = data.get("publishes", []) + data.get("publish_edges", []) + rels.get("publishes_to", [])
    for pub in pub_list:
        app_id = pub.get("application_id") or pub.get("source") or pub.get("from")
        topic_id = pub.get("topic_id") or pub.get("target") or pub.get("to")
        if app_id and topic_id:
            g.add_edge(app_id, topic_id, type="PUBLISHES_TO",
                       rate_hz=pub.get("rate_hz", 10.0),
                       qos_profile=pub.get("qos_profile", {}))

    # 2. SUBSCRIBES_TO
    sub_list = data.get("subscribes", []) + data.get("subscribe_edges", []) + rels.get("subscribes_to", [])
    for sub in sub_list:
        app_id = sub.get("application_id") or sub.get("source") or sub.get("from")
        topic_id = sub.get("topic_id") or sub.get("target") or sub.get("to")
        if app_id and topic_id:
            g.add_edge(app_id, topic_id, type="SUBSCRIBES_TO",
                       qos_profile=sub.get("qos_profile", {}))

    # 3. ROUTES
    # Handle both dict-based broker_routes and list-based relationships["routes"]
    for broker_id, topic_ids in (data.get("broker_routes") or {}).items():
        if isinstance(topic_ids, list):
            for tid in topic_ids:
                g.add_edge(broker_id, tid, type="ROUTES")
    
    for route in rels.get("routes", []):
        src = route.get("source") or route.get("broker_id") or route.get("from")
        tgt = route.get("target") or route.get("topic_id") or route.get("to")
        if src and tgt:
            g.add_edge(src, tgt, type="ROUTES")

    # 4. RUNS_ON (important for physical fault propagation)
    for run in rels.get("runs_on", []):
        src = run.get("source") or run.get("application_id") or run.get("from")
        tgt = run.get("target") or run.get("node_id") or run.get("to")
        if src and tgt:
            g.add_edge(src, tgt, type="RUNS_ON")

    # 5. CONNECTS_TO
    for conn in rels.get("connects_to", []):
        src = conn.get("source") or conn.get("from")
        tgt = conn.get("target") or conn.get("to")
        if src and tgt:
            g.add_edge(src, tgt, type="CONNECTS_TO",
                       **{k: conn[k] for k in LINK_PROPERTIES if k in conn})

    # 6. USES
    # Note: Important for library-mediated publishing/subscribing
    for use in rels.get("uses", []) or data.get("uses", []):
        src = use.get("source") or use.get("from")
        tgt = use.get("target") or use.get("to")
        if src and tgt:
            g.add_edge(src, tgt, type="USES")

    # Topology JSON states QoS on Topic nodes only, so without this the pub/sub
    # edges reach the injector with an empty qos_profile and a constant weight.
    _project_topic_qos_onto_edges(g)

    logger.info("Graph loaded via fallback: %d nodes, %d edges",
                len(g.nodes), len(g.edges))
    return g


#: Original private names, kept so the CLI modules can re-export without churn.
_build_graph_from_json = build_graph_from_json
_load_graph = load_graph
