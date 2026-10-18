"""
Flat ⇄ nested translation between topology JSON and graph-store properties.

Graph stores hold scalar properties only, so the nested ``code_metrics`` and
``system_hierarchy`` sub-objects of the topology JSON are flattened on import
(``flatten_*``) and rebuilt on export (``reconstruct_*``). Both directions read
the same field tables below, so a new metric is added in exactly one place.
"""

from typing import Any, Dict, Iterable, List, Optional
from saag.core.models import (
    ComponentData,
    GraphData,
    QoSPolicy,
    canonical_criticality,
)
from saag.core.network import LINK_PROPERTIES

#: Flat property name -> nested key, grouped by ``code_metrics`` section.
CODE_METRIC_FIELDS: Dict[str, Dict[str, str]] = {
    "size": {
        "cm_total_loc": "total_loc", "cm_total_classes": "total_classes",
        "cm_total_methods": "total_methods", "cm_total_fields": "total_fields",
    },
    "complexity": {
        "cm_total_wmc": "total_wmc", "cm_avg_wmc": "avg_wmc", "cm_max_wmc": "max_wmc",
    },
    "cohesion": {
        "cm_avg_lcom": "avg_lcom", "cm_max_lcom": "max_lcom",
    },
    "coupling": {
        "cm_avg_cbo": "avg_cbo", "cm_max_cbo": "max_cbo", "cm_avg_rfc": "avg_rfc",
        "cm_max_rfc": "max_rfc", "cm_avg_fanin": "avg_fanin", "cm_max_fanin": "max_fanin",
        "cm_avg_fanout": "avg_fanout", "cm_max_fanout": "max_fanout",
    },
    "quality": {
        "sqale_debt_ratio": "sqale_debt_ratio", "bugs": "bugs",
        "vulnerabilities": "vulnerabilities", "duplicated_lines_density": "duplicated_lines_density",
    },
}

#: Flat property names of the system-decomposition hierarchy.
HIERARCHY_FIELDS = ("csms_name", "css_name", "csc_name", "csci_name")


def _coerce(value: Any, cast: Any) -> Optional[Any]:
    """Cast *value* with *cast*, returning ``None`` when it is absent or junk."""
    if value is None:
        return None
    try:
        return cast(value)
    except (ValueError, TypeError):
        return None


def _normalize_role(value: Any) -> List[str]:
    """Coerce an Application ``role`` to a list (accepts a bare string)."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return value
    return ["Operative"]


def reconstruct_metadata_dict(props: Dict[str, Any]) -> Dict[str, Any]:
    """
    Reconstruct graph metadata from flattened properties.
    Handles mapping from storage-optimized keys back to nested structure.
    """
    if not props:
        return {}
        
    return {
        "scale": {
            "apps": props.get("scale_apps", 0),
            "topics": props.get("scale_topics", 0),
            "brokers": props.get("scale_brokers", 0),
            "nodes": props.get("scale_nodes", 0),
            "libs": props.get("scale_libs", 0)
        },
        "seed": props.get("seed"),
        "generation_mode": props.get("generation_mode"),
        "domain": props.get("domain"),
        "scenario": props.get("scenario")
    }

def reconstruct_component_dict(comp: ComponentData) -> Dict[str, Any]:
    """
    Reconstruct a component dictionary with nested sub-objects (system_hierarchy, code_metrics)
    from ComponentData properties.
    """
    props = comp.properties
    # Base fields
    res = {"id": comp.id, "name": props.get("name", comp.id), "weight": comp.weight}
    
    # 1. System Hierarchy reconstruction (empty strings are not exported)
    sh = {key: props[key] for key in HIERARCHY_FIELDS if props.get(key)}
    if sh:
        res["system_hierarchy"] = sh

    # 2. Code Metrics reconstruction, dropping sections with no stored fields
    cm = {}
    for section, fields in CODE_METRIC_FIELDS.items():
        section_data = {
            nest_key: props[flat_key]
            for flat_key, nest_key in fields.items()
            if flat_key in props
        }
        if section_data:
            cm[section] = section_data
    if cm:
        res["code_metrics"] = cm

    # 3. Type-specific properties
    res.update(_RECONSTRUCT_BY_TYPE.get(comp.component_type, lambda _: {})(props))
    return res


def _reconstruct_topic(props: Dict[str, Any]) -> Dict[str, Any]:
    # Preserve uppercase QoS values — the canonical format and weight calculations
    # both use uppercase (RELIABLE, TRANSIENT_LOCAL, HIGH, etc.). Lowercasing here
    # would cause silent weight mismatches on round-trip import.
    qos_data: Dict[str, Any] = {
        "reliability": props.get("qos_reliability", "BEST_EFFORT"),
        "durability": props.get("qos_durability", "VOLATILE"),
        "transport_priority": props.get("qos_transport_priority", "MEDIUM"),
    }
    # ``qos_deadline_ms`` / ``qos_history_depth`` are the canonical flattened
    # names (matching the other qos_* properties); the unprefixed spellings are
    # still accepted so externally-authored node dumps keep importing.
    deadline = _coerce(
        QoSPolicy._first_present(props.get("qos_deadline_ms"), props.get("deadline_ms")), float
    )
    history = _coerce(
        QoSPolicy._first_present(props.get("qos_history_depth"), props.get("history_depth")), int
    )
    if deadline is not None:
        qos_data["deadline_ms"] = deadline
    if history is not None:
        qos_data["history_depth"] = history

    res: Dict[str, Any] = {
        "size": props.get("size", 256),
        "qos": qos_data,
    }
    # Restore derived fields if present; backend caller is responsible for
    # ensuring they are populated (import backfill or frontend recompute).
    frequency = QoSPolicy._first_present(props.get("topic_frequency"), props.get("frequency"))
    if frequency is not None:
        res["frequency"] = frequency
    criticality = QoSPolicy._first_present(
        props.get("topic_criticality"), props.get("criticality")
    )
    if criticality is not None:
        res["criticality"] = canonical_criticality(criticality)

    if deadline is not None:
        res["deadline_ms"] = deadline
    if history is not None:
        res["history_depth"] = history
    return res


def _reconstruct_application(props: Dict[str, Any]) -> Dict[str, Any]:
    res = {
        "app_type": props.get("app_type", "service"),
        "role": _normalize_role(props.get("role", ["Operative"])),
        "criticality": canonical_criticality(props.get("criticality")),
        "hotstandby": props.get("hotstandby", False),
    }
    if props.get("version"):
        res["version"] = props["version"]
    return res


def _present_keys(props: Dict[str, Any], keys: Iterable[str]) -> Dict[str, Any]:
    """Carry over only the keys actually stored on the vertex."""
    return {key: props[key] for key in keys if key in props}


#: Per-vertex-type reconstruction of the properties not shared by all types.
_RECONSTRUCT_BY_TYPE = {
    "Topic": _reconstruct_topic,
    "Application": _reconstruct_application,
    "Library": lambda props: {"version": props["version"]} if props.get("version") else {},
    "Node": lambda props: _present_keys(props, ("ip_address", "cpu_cores", "memory_gb", "os_type")),
    "Broker": lambda props: _present_keys(props, ("type", "max_connections", "host")),
    "NetworkDevice": lambda props: _present_keys(props, ("role",)),
}

def reconstruct_export_payload(graph_data: GraphData, metadata: Dict[str, Any]) -> Dict[str, Any]:
    """
    Assemble the final export JSON payload from GraphData and metadata.
    Ensures consistent category mapping and relationship placement.
    """
    data = {
        "metadata": metadata,
        "nodes": [], "brokers": [], "topics": [], 
        "applications": [], "libraries": [],
        "relationships": {
            "runs_on": [], "routes": [], "publishes_to": [],
            "subscribes_to": [], "connects_to": [], "uses": [],
            "depends_on": [] # include pre-computed dependencies
        }
    }
    
    # Category mapping for components
    type_to_category = {
        "Node": "nodes",
        "Broker": "brokers",
        "Topic": "topics",
        "Application": "applications",
        "Library": "libraries",
        "NetworkDevice": "network_devices",
    }
    
    # Process components
    for comp in graph_data.components:
        category = type_to_category.get(comp.component_type)
        if not category:
            continue
        # Only systems that model switches/routers carry the key at all.
        data.setdefault(category, [])
            
        comp_dict = reconstruct_component_dict(comp)
        data[category].append(comp_dict)
        
    # Process edges
    for edge in graph_data.edges:
        rel_key = edge.relation_type.lower()
        if rel_key in data["relationships"]:
            edge_dict = {"from": edge.source_id, "to": edge.target_id, "weight": edge.weight}
            
            # Include metadata for DEPENDS_ON
            if edge.relation_type == "CONNECTS_TO":
                edge_dict.update(_present_keys(edge.properties, LINK_PROPERTIES))
            if edge.relation_type == "DEPENDS_ON":
                edge_dict.update({
                    "dependency_type": edge.dependency_type,
                    "path_count": edge.path_count
                })
            
            data["relationships"][rel_key].append(edge_dict)
            
    return data

def flatten_metadata(metadata: Dict[str, Any]) -> Dict[str, Any]:
    """
    Flatten nested metadata into storage-optimized properties.
    """
    if not metadata:
        return {}
        
    scale = metadata.get("scale", {})
    return {
        "seed": metadata.get("seed"),
        "generation_mode": metadata.get("generation_mode", "unknown"),
        "domain": metadata.get("domain"),
        "scenario": metadata.get("scenario"),
        "scale_apps": scale.get("apps", 0),
        "scale_topics": scale.get("topics", 0),
        "scale_brokers": scale.get("brokers", 0),
        "scale_nodes": scale.get("nodes", 0),
        "scale_libs": scale.get("libs", 0),
    }

def flatten_component(comp: Dict[str, Any], comp_type: str) -> Dict[str, Any]:
    """
    Flatten nested component data (code_metrics, system_hierarchy) 
    into storage-optimized properties.
    """
    # 1. Base fields
    res = {
        "id": comp["id"],
        "name": comp.get("name", comp["id"]),
    }
    if "weight" in comp:
        res["weight"] = comp["weight"]

    # 2. System Hierarchy (every field is written, absent ones as empty strings)
    sh = comp.get("system_hierarchy") or {}
    for key in HIERARCHY_FIELDS:
        res[key] = sh.get(key, "")

    # 3. Code Metrics — every field is written so vertices have a uniform schema
    cm = comp.get("code_metrics") or {}
    for section, fields in CODE_METRIC_FIELDS.items():
        section_data = cm.get(section, {})
        for flat_key, nest_key in fields.items():
            value = section_data.get(nest_key, 0)
            # Averages, ratios and densities are fractional; counts stay as-is
            is_fractional = any(t in nest_key for t in ("avg", "ratio", "density"))
            res[flat_key] = float(value) if is_fractional else value

    # 3.1 Additional flat aliases for common analysis fields (Issue 7 hardening)
    # These ensure compatibility with extract_layer_subgraph and basic Cypher queries.
    if cm:
        res["loc"] = cm.get("size", {}).get("total_loc", 0)
        res["cyclomatic_complexity"] = float(cm.get("complexity", {}).get("avg_wmc", 0.0))
        res["lcom"] = float(cm.get("cohesion", {}).get("avg_lcom", 0.0))
        res["coupling_afferent"] = int(cm.get("coupling", {}).get("avg_fanin", 0))
        res["coupling_efferent"] = int(cm.get("coupling", {}).get("avg_fanout", 0))

    # 4. Type-specific properties
    res.update(_FLATTEN_BY_TYPE.get(comp_type, lambda _: {})(comp))
    return res


def _flatten_topic(comp: Dict[str, Any]) -> Dict[str, Any]:
    qos = comp.get("qos", comp.get("qos_policy", {}))
    res = {
        "size": comp.get("size", 256),
        "qos_reliability": qos.get("reliability", "BEST_EFFORT"),
        "qos_durability": qos.get("durability", "VOLATILE"),
        "qos_transport_priority": qos.get("transport_priority", "MEDIUM"),
    }
    # Only include derived/optional fields if explicitly present in the source data
    if comp.get("frequency") is not None:
        res["topic_frequency"] = comp["frequency"]
    if comp.get("criticality") is not None:
        res["topic_criticality"] = canonical_criticality(comp["criticality"])

    # One canonical flattened name per field, under the existing qos_* prefix —
    # deadline and history are DDS QoS policies like the three enums above.
    deadline = _coerce(
        QoSPolicy._first_present(comp.get("deadline_ms"), qos.get("deadline_ms")), float
    )
    if deadline is not None:
        res["qos_deadline_ms"] = deadline

    history = _coerce(
        QoSPolicy._first_present(comp.get("history_depth"), qos.get("history_depth")), int
    )
    if history is not None:
        res["qos_history_depth"] = history

    return res


def _flatten_application(comp: Dict[str, Any]) -> Dict[str, Any]:
    res = {
        "app_type": comp.get("app_type", "service"),
        "role": _normalize_role(comp.get("role", ["Operative"])),
        "version": comp.get("version", ""),
    }
    # Only include optional classification fields if explicitly present in the source data
    res.update(_present_keys(comp, ("hotstandby",)))
    if comp.get("criticality") is not None:
        res["criticality"] = canonical_criticality(comp["criticality"])
    return res


#: Per-vertex-type flattening of the properties not shared by all types.
_FLATTEN_BY_TYPE = {
    "Topic": _flatten_topic,
    "Application": _flatten_application,
    "Library": lambda comp: {"version": comp.get("version", "")},
    "Node": lambda comp: {
        "ip_address": comp.get("ip_address", ""),
        "cpu_cores": comp.get("cpu_cores", 0),
        "memory_gb": comp.get("memory_gb", 0),
        "os_type": comp.get("os_type", "linux"),
    },
    "Broker": lambda comp: {
        "type": comp.get("type", "mqtt"),
        "max_connections": comp.get("max_connections", 0),
        "host": comp.get("host", ""),
    },
    "NetworkDevice": lambda comp: {"role": comp.get("role", "switch")},
}
