"""
saag/prescription/mutator.py

Applies a compiled policy Delta(G) to the flat JSON topology export.

Pure: takes JSON in, returns new JSON out. No repository, no simulation. The
mutation is done on the export rather than in the database so a candidate can be
evaluated without contaminating the target store or paying transaction overhead.
"""
import copy
from typing import Any, Dict, List

from .models import PrescriptionPolicy, TopicSplit


def apply_policy(original_json: Dict[str, Any], policy: PrescriptionPolicy) -> Dict[str, Any]:
    """Return a copy of ``original_json`` with the policy's edits applied.

    Operators run QoS -> reallocation -> split. The order matters: sub-topics
    are deep-copied from the topic they replace, so a hardened topic passes its
    hardened QoS on to its sub-topics.
    """
    mutated = copy.deepcopy(original_json)
    _harden_qos(mutated, policy)
    _reallocate_nodes(mutated, policy)
    _split_topics(mutated, policy)
    return mutated


def _harden_qos(mutated: Dict[str, Any], policy: PrescriptionPolicy) -> None:
    """Rewrite the transport contract of each upgraded topic, nested and flat."""
    upgrades = {u.topic: u for u in policy.qos_upgrades}
    if not upgrades:
        return

    for topic in mutated.get("topics", []):
        upgrade = upgrades.get(topic["id"])
        if upgrade is None:
            continue
        qos = topic.setdefault("qos", {})
        qos["reliability"] = upgrade.target_reliability
        qos["durability"] = upgrade.target_durability
        # The flat properties are what the layer projections read.
        topic["qos_reliability"] = upgrade.target_reliability
        topic["qos_durability"] = upgrade.target_durability


def _reallocate_nodes(mutated: Dict[str, Any], policy: PrescriptionPolicy) -> None:
    """Move each reallocated process onto a fresh node cloned from its old host."""
    # Keyed by (process, source host): a replicated process runs on several
    # hosts, and moving it off one must leave its other placements alone.
    reallocations = {(r.component, r.from_node): r for r in policy.node_reallocations}
    if not reallocations:
        return

    relationships = mutated.setdefault("relationships", {})

    # Re-point RUNS_ON at the new host, remembering which node each clone came from.
    clone_sources: Dict[str, str] = {}
    runs_on: List[Dict[str, Any]] = []
    for rel in relationships.get("runs_on", []):
        realloc = reallocations.get((rel.get("from"), rel.get("to")))
        if realloc is None:
            runs_on.append(rel)
            continue
        runs_on.append({
            "from": realloc.component,
            "to": realloc.to_node,
            "weight": rel.get("weight", 1.0),
        })
        clone_sources[realloc.to_node] = realloc.from_node
    relationships["runs_on"] = runs_on

    # Materialise the new nodes as copies of the host they were split off.
    nodes = mutated.setdefault("nodes", [])
    by_id = {n["id"]: n for n in nodes}
    for new_node_id, source_node_id in clone_sources.items():
        source = by_id.get(source_node_id)
        if source is None:
            continue
        clone = copy.deepcopy(source)
        clone["id"] = new_node_id
        clone["name"] = f"Node {new_node_id}"
        nodes.append(clone)

    # Mirror the original host's CONNECTS_TO links so reachability is preserved.
    # The clone gets links of the same capacity, but on new ports: interface
    # names belong to the original cable.
    connects_to: List[Dict[str, Any]] = []
    for conn in relationships.get("connects_to", []):
        connects_to.append(conn)
        carried = {k: v for k, v in conn.items()
                   if k not in ("from", "to", "weight", "from_interface", "to_interface")}
        for new_node_id, source_node_id in clone_sources.items():
            if conn.get("from") == source_node_id:
                connects_to.append({
                    "from": new_node_id,
                    "to": conn.get("to"),
                    "weight": conn.get("weight", 1.0),
                    **carried,
                })
            if conn.get("to") == source_node_id:
                connects_to.append({
                    "from": conn.get("from"),
                    "to": new_node_id,
                    "weight": conn.get("weight", 1.0),
                    **carried,
                })
    relationships["connects_to"] = connects_to


def _split_topics(mutated: Dict[str, Any], policy: PrescriptionPolicy) -> None:
    """Replace each split topic with one sub-topic per publisher and rewire."""
    splits = {s.topic: s for s in policy.topic_splits}
    if not splits:
        return

    topics: List[Dict[str, Any]] = []
    for topic in mutated.get("topics", []):
        split = splits.get(topic["id"])
        if split is None:
            topics.append(topic)
            continue
        for publisher in split.publishers:
            sub_topic = copy.deepcopy(topic)
            sub_topic["id"] = f"{split.topic}_{publisher}"
            sub_topic["name"] = f"{topic.get('name', split.topic)} for {publisher}"
            topics.append(sub_topic)
    mutated["topics"] = topics

    relationships = mutated.setdefault("relationships", {})
    # A publisher moves to its own sub-topic; subscribers and brokers fan out to
    # all of them, so no subscriber loses a channel it previously had.
    relationships["publishes_to"] = _retarget(
        relationships.get("publishes_to", []), splits, per_source=True)
    for key in ("subscribes_to", "routes"):
        relationships[key] = _retarget(
            relationships.get(key, []), splits, per_source=False)


def _retarget(
    relationships: List[Dict[str, Any]],
    splits: Dict[str, TopicSplit],
    *,
    per_source: bool,
) -> List[Dict[str, Any]]:
    """Re-point relationships whose target was split onto the new sub-topics.

    ``per_source`` sends each edge to the single sub-topic named after its own
    source (the publisher case); otherwise it fans out to every sub-topic.
    Relationships pointing at an unsplit target are passed through untouched.
    """
    retargeted: List[Dict[str, Any]] = []
    for rel in relationships:
        topic_id = rel.get("to")
        split = splits.get(topic_id)
        if split is None:
            retargeted.append(rel)
            continue
        source = rel.get("from")
        suffixes = [source] if per_source else split.publishers
        retargeted.extend(
            {
                "from": source,
                "to": f"{topic_id}_{suffix}",
                "weight": rel.get("weight", 1.0),
            }
            for suffix in suffixes
        )
    return retargeted
