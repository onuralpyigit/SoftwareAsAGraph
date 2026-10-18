"""
Neo4j Graph Repository Adapter

Implements IGraphRepository using Neo4j as the backend, building the graph model
of docs/graph-model.md in five phases split across two stages:

    save_graph()           Phase 1  Entity import (V)
                           Phase 2  Structural edge import (E_S)
                           Phase 3  QoS-based topic weights (w)
                           Phase 5a Aggregate vertex weights

    derive_dependencies()  Phase 4  DEPENDS_ON derivation (E_D, Rules 1–7)
                           Phase 5b DEPENDS_ON edge weight finalization
"""

from __future__ import annotations
import logging
from typing import Dict, Any, List, Optional, Tuple

try:
    from neo4j import GraphDatabase
except ImportError:
    GraphDatabase = None

from saag.core.ports.graph_repository import IGraphRepository
from saag.core.layers import get_layer_definition, resolve_layer
from saag.core.models import (
    ComponentData, EdgeData, GraphData, QoSPolicy,
    MIN_TOPIC_WEIGHT, TOPIC_QOS_WEIGHT_BETA,
    TOPIC_SIZE_WEIGHT_ALPHA, TOPIC_FREQ_WEIGHT_PSI,
    TOPIC_SIZE_NORM_DIVISOR, TOPIC_FREQ_NORM_DIVISOR,
    TOPIC_DEFAULT_FREQUENCY_HZ,
    COMPONENT_POWER_MEAN_P,
    LIB_FANOUT_GAMMA,
)
from . import config
from saag.core.utils import serialization
from saag.core.network import (
    LINK_PROPERTIES, Fabric, baseline_load, derive_node_to_device, vertex_load_metrics,
)

#: Vertex labels of the graph model, in import order.
COMPONENT_LABELS = ["Node", "Broker", "Topic", "Application", "Library", "NetworkDevice"]

#: Structural relationship type -> (source labels, target label). The label
#: constraints let Neo4j use the uniqueness indexes when matching endpoints.
STRUCTURAL_RELATIONSHIPS = {
    "runs_on": ("RUNS_ON", "Application|Broker", "Node"),
    "routes": ("ROUTES", "Broker", "Topic"),
    "publishes_to": ("PUBLISHES_TO", "Application|Library", "Topic"),
    "subscribes_to": ("SUBSCRIBES_TO", "Application|Library", "Topic"),
    "connects_to": ("CONNECTS_TO", "Node|NetworkDevice", "Node|NetworkDevice"),
    "uses": ("USES", "Application|Library", "Library"),
}

#: DEPENDS_ON subtypes produced by the seven derivation rules.
DEPENDENCY_TYPES = [
    "app_to_app", "app_to_lib", "app_to_broker",
    "node_to_node", "node_to_broker", "broker_to_broker", "node_to_device",
]

#: Code-quality and system-hierarchy properties shared by Application and Library
#: vertices. Kept in one list so both import queries cannot drift apart.
_SHARED_COMPONENT_PROPERTIES = [
    "csc_name", "csci_name", "css_name", "csms_name",
    "cm_total_loc", "cm_total_classes", "cm_total_methods", "cm_total_fields",
    "cm_total_wmc", "cm_avg_wmc", "cm_max_wmc",
    "cm_avg_lcom", "cm_max_lcom",
    "cm_avg_cbo", "cm_max_cbo", "cm_avg_rfc", "cm_max_rfc",
    "cm_avg_fanin", "cm_max_fanin", "cm_avg_fanout", "cm_max_fanout",
    "sqale_debt_ratio", "bugs", "vulnerabilities", "duplicated_lines_density",
    "loc", "cyclomatic_complexity", "coupling_afferent", "coupling_efferent", "lcom",
]


def _set_clause(var: str, properties: List[str]) -> str:
    """Render ``SET n.prop = row.prop, ...`` for a list of property names."""
    return ",\n                ".join(f"{var}.{prop} = row.{prop}" for prop in properties)


def _set_if_present_clause(var: str, properties: List[str]) -> str:
    """Render conditional SETs that skip properties absent from the source data."""
    return "\n".join(
        f"            FOREACH (_ IN CASE WHEN row.{prop} IS NOT NULL THEN [1] ELSE [] END |\n"
        f"                SET {var}.{prop} = row.{prop})"
        for prop in properties
    )


def create_repository(uri=None, user=None, password=None):
    """Create a Neo4jRepository from params or environment."""
    if GraphDatabase is None:
        raise ImportError(
            "The 'neo4j' driver is required to use Neo4jRepository. "
            "Install it with: pip install neo4j"
        )
    return Neo4jRepository(
        uri=uri or config.get_default_uri(),
        user=user or config.get_default_username(),
        password=password or config.get_default_password(),
    )


class Neo4jRepository:
    """
    Neo4j adapter for the graph model.

    Handles all Neo4j-specific operations including:
    - Graph data import with constraint management
    - Weight computation for vertices and edges (docs/graph-model.md §4.3, §4.5)
    - Dependency derivation between components (§4.4, Rules 1–7)
    - Graph data retrieval with layer filtering (§5)
    """
    
    def __init__(
        self,
        uri: str = None,
        user: str = None,
        password: str = None,
        database: str = None
    ):
        """Initialize Neo4j repository."""
        if GraphDatabase is None:
            raise ImportError(
                "The 'neo4j' driver is required to use Neo4jRepository. "
                "Install it with: pip install neo4j"
            )
        uri = uri or config.get_default_uri()
        user = user or config.get_default_username()
        password = password or config.get_default_password()
        database = database or config.get_default_database()
        
        self.driver = GraphDatabase.driver(
            uri, auth=(user, password),
            notifications_min_severity="WARNING",
        )
        self.database = database
        self.logger = logging.getLogger(__name__)

    def __enter__(self) -> "Neo4jRepository":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()
    
    def close(self) -> None:
        """Close Neo4j driver connection."""
        self.driver.close()

    # ==========================================
    # Import Methods (Save)
    # ==========================================

    def save_graph(self, data: Dict[str, Any], clear: bool = False) -> None:
        """
        Import graph data into the repository within a single transaction.

        Runs Phases 1, 2, 3 and 5a: entities, structural edges, topic weights and
        aggregate vertex weights. DEPENDS_ON derivation runs later, in the
        pre-analysis stage — see derive_dependencies().
        """
        self.logger.info(f"Starting import. Clear DB: {clear}")
        
        # 0. Schema: Constraints run in their own transaction (Neo4j requirement)
        self._create_constraints()
        
        with self.driver.session(database=self.database) as session:
            session.execute_write(self._save_graph_tx, data, clear)

    def _save_graph_tx(self, tx: Any, data: Dict[str, Any], clear: bool) -> None:
        """Internal unit of work for save_graph transaction."""
        try:
            # 0. Clear
            if clear: 
                tx.run("MATCH (n) DETACH DELETE n")
            
            # 1. Import entities
            self._import_entities(data, tx)
            
            # 2. Import structural relationships
            self._import_relationships(data, tx)
            
            # 3. Compute intrinsic weights
            self._calculate_intrinsic_weights(tx)
            
            # 4. Compute aggregate component weights
            self._calculate_aggregate_weights(tx)

            # 5. Baseline network load (only when the network is modelled)
            self._calculate_network_load(tx)
            
            self.logger.info("Import completed successfully.")
            
        except Exception as e:
            self.logger.error(f"Import failed during phase orchestration: {e}")
            self.logger.critical(
                "Database may be in an inconsistent state. "
                "Recommendation: Re-run with clear=True to ensure reproducibility."
            )
            # Transaction will be rolled back by the session.execute_write context manager
            raise

    def derive_dependencies(self) -> None:
        """
        Pre-analysis stage: derive DEPENDS_ON relationships and finalise
        DEPENDS_ON edge weights.

        Must be called after save_graph() and before any analysis step. Applies
        Rules 1–7 (docs/graph-model.md §4.4), then gives the app_to_lib (Rule 5)
        and broker_to_broker (Rule 6) edges the component weights they could not
        carry when they were derived, and NetworkDevices their Rule 7 weight.
        """
        self.logger.info("Pre-analysis: deriving DEPENDS_ON relationships.")
        with self.driver.session(database=self.database) as session:
            session.execute_write(self._derive_dependencies_tx)
        self.logger.info("Pre-analysis: DEPENDS_ON derivation completed.")

    def _derive_dependencies_tx(self, tx: Any) -> None:
        """Transaction body for derive_dependencies."""
        try:
            self._derive_dependencies(tx)
            self._finalize_dependency_weights(tx)
        except Exception as e:
            self.logger.error(f"Pre-analysis dependency derivation failed: {e}")
            raise

    def _finalize_dependency_weights(self, tx: Any = None) -> None:
        """
        Phase 5b: set the DEPENDS_ON edge weights that depend on vertex weights.

        Separate from _calculate_aggregate_weights because those vertex weights
        are computed at import time, while the edges they flow onto only exist
        after derive_dependencies() has run.
        """
        # app_to_lib Edge Weights (harmonic coupling between App and Library)
        self._run_query("""
            MATCH (app)-[d:DEPENDS_ON {dependency_type: 'app_to_lib'}]->(lib:Library)
            WITH d, coalesce(app.weight, 0.01) as w_app, coalesce(lib.weight, 0.01) as w_lib
            SET d.weight = CASE WHEN (w_app + w_lib) > 0 THEN 2.0 * (w_app * w_lib) / (w_app + w_lib) ELSE 0.01 END
        """, tx=tx)

        # broker_to_broker Edge Weights (inherits from Node)
        self._run_query("""
            MATCH (b1:Broker)-[d:DEPENDS_ON {dependency_type: 'broker_to_broker'}]->(b2:Broker)
            MATCH (b1)-[:RUNS_ON]->(n:Node)<-[:RUNS_ON]-(b2)
            WITH d, max(n.weight) as node_w
            SET d.weight = coalesce(node_w, 0.01)
        """, tx=tx)

        # NetworkDevice weight (worst-case host depending on it, Rule 7)
        self._run_query("""
            MATCH (dev:NetworkDevice)
            OPTIONAL MATCH (n:Node)-[:DEPENDS_ON {dependency_type: 'node_to_device'}]->(dev)
            WITH dev, max(n.weight) as host_w
            SET dev.weight = CASE WHEN coalesce(host_w, 0.0) < 0.01 THEN 0.01 ELSE host_w END
        """, tx=tx)

    def _run_query(self, query: str, parameters: Dict = None, tx: Any = None) -> Any:
        """Execute a Cypher query, optionally within an existing transaction."""
        if tx:
            return tx.run(query, parameters or {}).consume()

        with self.driver.session(database=self.database) as session:
            return session.run(query, parameters or {}).consume()

    def _fetch(self, query: str, parameters: Dict = None, tx: Any = None) -> List[Any]:
        """Execute a Cypher query and materialise its records."""
        if tx:
            return list(tx.run(query, parameters or {}))

        with self.driver.session(database=self.database) as session:
            return list(session.run(query, parameters or {}))

    def _import_batch(self, data: List[Dict], query: str, tx: Any = None) -> int:
        """Import a batch of records using UNWIND, optionally within a transaction."""
        if not data:
            return 0

        summary = self._run_query(f"UNWIND $rows AS row {query}", {"rows": data}, tx=tx)
        return summary.counters.nodes_created + summary.counters.relationships_created

    def _validate_endpoints_exist(
        self, batch: List[Dict], key: str, rel_type: str, tx: Any = None
    ) -> None:
        """
        Fail the import when a relationship references an entity that was not created.

        OPTIONAL MATCH identifies precisely which rows refer to missing entities,
        so the error can name the offending ids instead of just failing to match.
        """
        offenders = self._fetch("""
            UNWIND $rows AS row
            OPTIONAL MATCH (src {id: row.from})
            OPTIONAL MATCH (tgt {id: row.to})
            WITH row, src, tgt
            WHERE src IS NULL OR tgt IS NULL
            RETURN row.from as src_id, src IS NOT NULL as src_exists,
                   row.to as tgt_id, tgt IS NOT NULL as tgt_exists
            LIMIT 100
        """, {"rows": batch}, tx=tx)

        errors = []
        for record in offenders:
            if not record["src_exists"]:
                errors.append(f"Source entity missing (id='{record['src_id']}')")
            if not record["tgt_exists"]:
                errors.append(f"Target entity missing (id='{record['tgt_id']}')")

        if errors:
            raise ValueError(
                f"Structural integrity violation in '{key}' ('{rel_type}') relationship: "
                f"Referenced entities must exist. Found {len(errors)} errors including: "
                f"{'; '.join(errors[:5])}"
            )

    def _create_constraints(self, tx: Any = None) -> None:
        """Create uniqueness constraints for all entity types."""
        for label in COMPONENT_LABELS:
            try:
                self._run_query(
                    f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:{label}) REQUIRE n.id IS UNIQUE",
                    tx=tx
                )
            except Exception as e:
                self.logger.warning(f"Could not create constraint for {label}: {e}")

    def _import_entities(self, data: Dict[str, Any], tx: Any = None) -> None:
        """Import all entity types (Phase 1)."""
        # 0. Import metadata provenance
        self._import_metadata(data.get("metadata", {}), tx=tx)
        
        # 1. Import components
        self._import_nodes(data.get("nodes", []), tx=tx)
        self._import_brokers(data.get("brokers", []), tx=tx)
        self._import_topics(data.get("topics", []), tx=tx)
        self._import_applications(data.get("applications", []), tx=tx)
        self._import_libraries(data.get("libraries", []), tx=tx)
        self._import_network_devices(data.get("network_devices", []), tx=tx)

    def _import_nodes(self, nodes_data: List[Dict[str, Any]], tx: Any = None) -> None:
        """Import compute nodes with infrastructure metadata."""
        nodes = [serialization.flatten_component(n, "Node") for n in nodes_data]
        self._import_batch(nodes, """
            MERGE (n:Node {id: row.id})
            SET n.name = row.name,
                n.ip_address = row.ip_address,
                n.cpu_cores = row.cpu_cores,
                n.memory_gb = row.memory_gb,
                n.os_type = row.os_type
        """, tx=tx)

    def _import_network_devices(self, devices_data: List[Dict[str, Any]], tx: Any = None) -> None:
        """Import switches and routers."""
        devices = [serialization.flatten_component(d, "NetworkDevice") for d in devices_data]
        self._import_batch(devices, """
            MERGE (d:NetworkDevice {id: row.id})
            SET d.name = row.name, d.role = row.role
        """, tx=tx)

    def _import_metadata(self, metadata: Dict[str, Any], tx: Any = None) -> None:
        """
        Store graph metadata (scale, seed, etc.) in a singleton :Metadata node.
        """
        if not metadata:
            return
            
        params = serialization.flatten_metadata(metadata)
        
        query = """
        MERGE (m:Metadata)
        SET m += $params
        """
        self._run_query(query, {"params": params}, tx=tx)

    def _import_brokers(self, brokers_data: List[Dict[str, Any]], tx: Any = None) -> None:
        """Import message brokers with middleware metadata."""
        brokers = [serialization.flatten_component(b, "Broker") for b in brokers_data]
        self._import_batch(brokers, """
            MERGE (b:Broker {id: row.id})
            SET b.name = row.name,
                b.type = row.type,
                b.max_connections = row.max_connections,
                b.host = row.host
        """, tx=tx)

    def _import_topics(self, topics_data: List[Dict[str, Any]], tx: Any = None) -> None:
        """Import topics with QoS policies and derived fields."""
        topics = [serialization.flatten_component(t, "Topic") for t in topics_data]
        # Always-present fields
        self._import_batch(topics, """
            MERGE (t:Topic {id: row.id})
            SET t.name = row.name, t.size = row.size,
                t.qos_reliability = row.qos_reliability,
                t.qos_durability = row.qos_durability,
                t.qos_transport_priority = row.qos_transport_priority
        """, tx=tx)
        # Conditionally set optional fields only when present in the source data
        self._import_batch(topics, f"""
            MATCH (t:Topic {{id: row.id}})
{_set_if_present_clause("t", ["topic_frequency", "topic_criticality", "qos_deadline_ms", "qos_history_depth"])}
        """, tx=tx)

    def _import_applications(self, apps_data: List[Dict[str, Any]], tx: Any = None) -> None:
        """Import applications with code metrics and hierarchy."""
        apps = [serialization.flatten_component(a, "Application") for a in apps_data]
        # Always-present fields
        self._import_batch(apps, f"""
            MERGE (a:Application {{id: row.id}})
            SET {_set_clause("a", ["name", "role", "app_type", "version"] + _SHARED_COMPONENT_PROPERTIES)}
        """, tx=tx)
        # Conditionally set optional classification fields only when present in the source data
        self._import_batch(apps, f"""
            MATCH (a:Application {{id: row.id}})
{_set_if_present_clause("a", ["criticality", "hotstandby"])}
        """, tx=tx)

    def _import_libraries(self, libs_data: List[Dict[str, Any]], tx: Any = None) -> None:
        """Import libraries with code metrics and hierarchy."""
        libs = [serialization.flatten_component(l, "Library") for l in libs_data]
        self._import_batch(libs, f"""
            MERGE (l:Library {{id: row.id}})
            SET {_set_clause("l", ["name", "version"] + _SHARED_COMPONENT_PROPERTIES)}
        """, tx=tx)


    def _import_relationships(self, data: Dict[str, Any], tx: Any = None) -> None:
        """
        Import structural relationships (Phase 2) with entity validation.

        Validates that referenced source and target entities exist before
        creating edges. Missing entities raise ValueError to trigger rollback.
        """
        rels = data.get("relationships", {})

        for key, (rel_type, src_labels, tgt_labels) in STRUCTURAL_RELATIONSHIPS.items():
            items = rels.get(key, [])
            if not items:
                continue

            batch = [{"from": r.get("from", r.get("source")),
                      "to": r.get("to", r.get("target"))} for r in items]
            if key == "connects_to":
                for row, r in zip(batch, items):
                    row["props"] = {k: r[k] for k in LINK_PROPERTIES if k in r}

            self._validate_endpoints_exist(batch, key, rel_type, tx=tx)

            # Create edges using label-optimized match
            query = f"""
                MATCH (a:{src_labels} {{id: row.from}}), (b:{tgt_labels} {{id: row.to}})
                MERGE (a)-[r:{rel_type}]->(b)
            """
            if key == "connects_to":
                query += "SET r += row.props\n"
            self._import_batch(batch, query, tx=tx)

        # Phase 2 post-step: Fan-out augmentation for Topic
        self._run_query("""
            MATCH (t:Topic)
            OPTIONAL MATCH (sub)-[:SUBSCRIBES_TO]->(t) WHERE sub:Application OR sub:Library
            WITH t, count(DISTINCT sub) as sub_count
            OPTIONAL MATCH (pub)-[:PUBLISHES_TO]->(t) WHERE pub:Application OR pub:Library
            WITH t, sub_count, count(DISTINCT pub) as pub_count
            SET t.subscriber_count = sub_count,
                t.publisher_count = pub_count
        """, tx=tx)

    @staticmethod
    def _score_case_cypher(attribute: str, scores: Dict[str, float]) -> str:
        """
        Render a Cypher CASE that maps one QoS attribute to its score.

        The branches are generated from the ``QoSPolicy`` tables rather than
        spelled out, so the Cypher scorer cannot drift from the Python one —
        the drift that once made a CRITICAL topic score like a LOW one.
        Values scoring 0.0 are left to the ELSE branch. The attribute is
        upper-cased and trimmed before matching (mirrors ``QoSPolicy._canon``)
        so a lowercase-authored value scores correctly instead of silently
        falling through to 0.0.
        """
        branches = " ".join(
            f"WHEN '{value}' THEN {score}"
            for value, score in scores.items() if score
        )
        normalized = f"toUpper(trim(coalesce({attribute}, '')))"
        return f"CASE {normalized} {branches} ELSE 0.0 END"

    def _get_qos_weight_cypher(self, topic_var: str) -> str:
        """
        Generate the Cypher expression for the Topic weight (docs/graph-model.md §4.3):

            w(t) = max(ε, β * QoS_score + α * size_norm + ψ * freq_norm)
        """
        beta = TOPIC_QOS_WEIGHT_BETA
        alpha = TOPIC_SIZE_WEIGHT_ALPHA
        psi = TOPIC_FREQ_WEIGHT_PSI
        # SizeNorm operates on raw bytes against TOPIC_SIZE_NORM_DIVISOR
        # (saag.core.models.compute_size_norm) rather than a KiB-based /50.0
        # divisor, which implied an unstated ~1 EiB envelope.
        log2_size = f"(log(1 + {topic_var}.size) / (log(2) * {TOPIC_SIZE_NORM_DIVISOR}))"
        log10_freq = f"(log(1 + {topic_var}.frequency) / (log(10) * {TOPIC_FREQ_NORM_DIVISOR}))"
        log10_default_freq = (
            f"(log(1 + {TOPIC_DEFAULT_FREQUENCY_HZ}) / (log(10) * {TOPIC_FREQ_NORM_DIVISOR}))"
        )

        r_score = self._score_case_cypher(f'{topic_var}.qos_reliability', QoSPolicy.RELIABILITY_SCORES)
        d_score = self._score_case_cypher(f'{topic_var}.qos_durability', QoSPolicy.DURABILITY_SCORES)
        p_score = self._score_case_cypher(f'{topic_var}.qos_transport_priority', QoSPolicy.PRIORITY_SCORES)

        qos_score = (
            f"({QoSPolicy.W_RELIABILITY} * {r_score} + "
            f"{QoSPolicy.W_DURABILITY} * {d_score} + "
            f"{QoSPolicy.W_PRIORITY} * {p_score})"
        )

        size_norm = (
            f"CASE WHEN {topic_var}.size <= 0 THEN 0.0 "
            f"WHEN {log2_size} > 1.0 THEN 1.0 "
            f"ELSE {log2_size} END"
        )

        # Missing frequency falls back to TOPIC_DEFAULT_FREQUENCY_HZ, a
        # declared constant (saag.core.models.compute_freq_norm) — not a
        # rate derived from reliability x priority, which fed the QoS term
        # back into the nominally independent frequency term.
        freq_norm = (
            f"CASE WHEN {topic_var}.frequency IS NOT NULL AND {topic_var}.frequency > 0 "
            f"THEN CASE WHEN {log10_freq} > 1.0 THEN 1.0 WHEN {log10_freq} < 0.0 THEN 0.0 ELSE {log10_freq} END "
            f"WHEN {topic_var}.topic_frequency IS NOT NULL AND {topic_var}.topic_frequency > 0 "
            f"THEN CASE WHEN (log(1 + {topic_var}.topic_frequency)/(log(10)*{TOPIC_FREQ_NORM_DIVISOR})) > 1.0 "
            f"THEN 1.0 ELSE (log(1 + {topic_var}.topic_frequency)/(log(10)*{TOPIC_FREQ_NORM_DIVISOR})) END "
            f"ELSE {log10_default_freq} END"
        )

        weighted_sum = f"({beta} * {qos_score} + {alpha} * {size_norm} + {psi} * {freq_norm})"

        # Apply the minimum weight floor: max(ε, weighted_sum)
        return (
            f"CASE WHEN {weighted_sum} < {MIN_TOPIC_WEIGHT} THEN {MIN_TOPIC_WEIGHT} "
            f"WHEN {weighted_sum} > 1.0 THEN 1.0 "
            f"ELSE {weighted_sum} END"
        )

    def _calculate_intrinsic_weights(self, tx: Any = None) -> None:
        """
        Phase 3: Compute intrinsic Topic weights and propagate them to edges.

            w(topic) = max(ε, β * QoS_score + α * size_norm + ψ * freq_norm)
        """
        qos_calc = self._get_qos_weight_cypher("t")

        # 1. Topic Weight
        self._run_query(f"MATCH (t:Topic) SET t.weight = {qos_calc}", tx=tx)

        # 2. Edge Weights (Inherit from Topic)
        self._run_query("MATCH ()-[r:PUBLISHES_TO|SUBSCRIBES_TO]->(t:Topic) SET r.weight = t.weight", tx=tx)

        # 3. ROUTES Edge Weights
        self._run_query("MATCH ()-[r:ROUTES]->(t:Topic) SET r.weight = t.weight", tx=tx)

        # 4. Edge QoS profile (also inherited from the Topic). Consumers that read
        # per-edge QoS rather than the scalar weight — notably the GNN edge-feature
        # encoder — see only edges, and no topology source states edge-level QoS.
        self._run_query(
            """
            MATCH ()-[r:PUBLISHES_TO|SUBSCRIBES_TO|ROUTES]->(t:Topic)
            SET r.qos_reliability        = t.qos_reliability,
                r.qos_durability         = t.qos_durability,
                r.qos_transport_priority = t.qos_transport_priority
            """,
            tx=tx,
        )

    def _calculate_aggregate_weights(self, tx: Any = None) -> None:
        """
        Phase 5a: propagate Topic weights up to Applications, Libraries,
        Brokers and Nodes, so that a component's importance reflects the most
        critical data it carries. See docs/graph-model.md §4.5.
        """
        # 1. Application Weight (Generalized Power Mean p=3)
        self._run_query(f"""
            MATCH (a:Application)
            OPTIONAL MATCH (a)-[:PUBLISHES_TO|SUBSCRIBES_TO]->(t:Topic)
            WITH a, collect(t.weight) as weights
            WITH a, size(weights) as cnt,
                 CASE WHEN size(weights) = 0 THEN 0.01
                      ELSE (reduce(s = 0.0, w IN weights | s + (coalesce(w, 0.01)^3.0)) / size(weights))^(1.0/3.0)
                 END as p_weight
            SET a.weight = CASE WHEN p_weight > 1.0 THEN 1.0 WHEN p_weight < 0.01 THEN 0.01 ELSE p_weight END
        """, tx=tx)

        # 2. Library Weight (propagated + fan-out multiplier)
        # Formula: min(1.0, base_w * (1 + γ * log2(1 + DG_in)))
        # Reflects simultaneous blast semantics: shared libraries are higher priority.
        self._run_query(f"""
            MATCH (l:Library)
            OPTIONAL MATCH (l)-[:PUBLISHES_TO|SUBSCRIBES_TO]->(t:Topic)
            WITH l, max(t.weight) as t_max
            OPTIONAL MATCH (app:Application)-[:USES]->(l)
            WITH l, t_max, max(app.weight) as a_max, count(app) as dg_in
            WITH l, 
                 CASE WHEN coalesce(t_max, 0.0) > coalesce(a_max, 0.0) 
                      THEN coalesce(t_max, 0.0) 
                      ELSE coalesce(a_max, 0.0) 
                 END as base_w, 
                 dg_in
            WITH l, base_w, (1.0 + {LIB_FANOUT_GAMMA} * log(1 + dg_in) / log(2)) as multiplier
            SET l.weight = CASE WHEN base_w <= 0 THEN 0.01
                                WHEN base_w * multiplier > 1.0 THEN 1.0
                                ELSE base_w * multiplier END
        """, tx=tx)

        # 1.5. Application Weight — library-mediated topics (second pass)
        # Apps with no direct pub/sub connections get weight 0.01 from step 1.
        # If they communicate exclusively through libraries, propagate the max
        # used-library weight so their importance is not invisible to RM scoring.
        self._run_query("""
            MATCH (a:Application)
            WHERE a.weight <= 0.01
            MATCH (a)-[:USES]->(l:Library)
            WITH a, max(l.weight) as max_lib_w
            SET a.weight = max_lib_w
        """, tx=tx)

        # 3. Broker Weight (Generalized Power Mean p=3)
        self._run_query(f"""
            MATCH (b:Broker)
            OPTIONAL MATCH (b)-[:ROUTES]->(t:Topic)
            WITH b, collect(t.weight) as weights
            WITH b, size(weights) as cnt,
                 CASE WHEN size(weights) = 0 THEN 0.01
                      ELSE (reduce(s = 0.0, w IN weights | s + (coalesce(w, 0.01)^3.0)) / size(weights))^(1.0/3.0)
                 END as p_weight
            SET b.weight = CASE WHEN p_weight > 1.0 THEN 1.0 WHEN p_weight < 0.01 THEN 0.01 ELSE p_weight END
        """, tx=tx)

        # 4. Node Weight (max hosted component weight)
        self._run_query("""
            MATCH (n:Node)
            OPTIONAL MATCH (c)-[:RUNS_ON]->(n) WHERE c:Application OR c:Broker
            WITH n, max(c.weight) as hosted_max
            SET n.weight = coalesce(hosted_max, 0.01)
        """, tx=tx)

        # 5. NetworkDevice Weight: floor here; Phase 5b lifts it once Rule 7 exists
        self._run_query("MATCH (d:NetworkDevice) SET d.weight = 0.01", tx=tx)

    def _fetch_network(self, tx: Any = None) -> Tuple[List[str], List[Tuple[str, str, Any]]]:
        """Every host and switch/router, and the CONNECTS_TO links with their capacity."""
        vertices = [r["id"] for r in self._fetch(
            "MATCH (v) WHERE v:Node OR v:NetworkDevice RETURN v.id as id", tx=tx)]
        links = [(r["src"], r["dst"], r["cap"]) for r in self._fetch("""
            MATCH (a)-[r:CONNECTS_TO]->(b)
            RETURN a.id as src, b.id as dst, r.capacity_mbps as cap
        """, tx=tx)]
        return vertices, links

    def _calculate_network_load(self, tx: Any = None) -> None:
        """Phase 5c: baseline link load, only when the network is modelled.

        Read back from the stored graph rather than the import payload, so a
        merge import (clear=False) is measured as a whole.
        """
        modelled = self._fetch("""
            OPTIONAL MATCH (d:NetworkDevice) WITH count(d) as devices
            OPTIONAL MATCH ()-[r:CONNECTS_TO]->() WHERE r.capacity_mbps IS NOT NULL
            RETURN devices, count(r) as capacities
        """, tx=tx)[0]
        if not (modelled["devices"] or modelled["capacities"]):
            return

        def index(rel: str) -> Dict[str, List[str]]:
            out: Dict[str, List[str]] = {}
            for r in self._fetch(f"MATCH (a)-[:{rel}]->(b) RETURN a.id as src, b.id as dst", tx=tx):
                out.setdefault(r["dst"], []).append(r["src"])
            return out

        hosts_of: Dict[str, List[str]] = {}
        for host, comps in index("RUNS_ON").items():
            for comp in comps:
                hosts_of.setdefault(comp, []).append(host)
        vertices, links = self._fetch_network(tx)
        fabric, state = baseline_load(
            vertices=vertices,
            links=links,
            publishers_of=index("PUBLISHES_TO"),
            subscribers_of=index("SUBSCRIBES_TO"),
            routers_of=index("ROUTES"),
            hosts_of=hosts_of,
            topic_attrs={r["id"]: {"size": r["size"], "topic_frequency": r["freq"]} for r in self._fetch(
                "MATCH (t:Topic) RETURN t.id as id, t.size as size, t.topic_frequency as freq", tx=tx)},
        )
        link_rows = []
        for key, load in state.link_load.items():
            a, b = sorted(key)
            link_rows.append({"a": a, "b": b, "load": load, "util": state.link_util.get(key)})
        self._import_batch(link_rows, """
            MATCH (x {id: row.a})-[r:CONNECTS_TO]-(y {id: row.b})
            SET r.load_mbps = row.load, r.utilization = row.util
        """, tx=tx)
        vertex_rows = [{"id": v, **m} for v, m in vertex_load_metrics(fabric, state).items()]
        self._import_batch(vertex_rows, """
            MATCH (v {id: row.id}) WHERE v:Node OR v:NetworkDevice
            SET v.max_link_utilization = row.max_link_utilization,
                v.link_load_mbps = row.link_load_mbps
        """, tx=tx)

    def _derive_node_to_device(self, tx: Any = None) -> None:
        """Rule 7 via the shared separator helper, written back with UNWIND."""
        devices = [r["id"] for r in self._fetch("MATCH (d:NetworkDevice) RETURN d.id as id", tx=tx)]
        if not devices:
            return
        node_deps = [(r["src"], r["dst"], r["w"]) for r in self._fetch("""
            MATCH (a:Node)-[d:DEPENDS_ON {dependency_type: 'node_to_node'}]->(b:Node)
            RETURN a.id as src, b.id as dst, d.weight as w
        """, tx=tx)]
        rows = [
            {"src": node, "dev": device, "w": w, "pc": n}
            for (node, device), (w, n) in derive_node_to_device(
                Fabric(*self._fetch_network(tx)), devices, node_deps).items()
        ]
        self._import_batch(rows, """
            MATCH (n:Node {id: row.src}), (dev:NetworkDevice {id: row.dev})
            MERGE (n)-[d:DEPENDS_ON {dependency_type: 'node_to_device'}]->(dev)
            SET d.weight = row.w, d.path_count = row.pc
        """, tx=tx)
        
    def _merge_dependency(
        self, match: str, source: str, target: str, dep_type: str, tx: Any = None
    ) -> None:
        """
        Aggregate the topics matched by ``match`` into one DEPENDS_ON edge.

        ``match`` must bind ``source``, ``target`` and a Topic ``t``. The edge
        carries the probabilistic effective coupling weight and the number of mediating topics.
        """
        self._run_query(f"""
            {match.strip()}
            WITH {source}, {target}, count(DISTINCT t) as path_count, collect(DISTINCT coalesce(t.weight, 0.01)) as weights
            WITH {source}, {target}, path_count,
                 CASE WHEN size(weights) = 0 THEN 0.01
                      ELSE 1.0 - reduce(p = 1.0, w IN weights | p * (1.0 - w))
                 END as effective_weight
            MERGE ({source})-[d:DEPENDS_ON {{dependency_type: '{dep_type}'}}]->({target})
            ON CREATE SET d.weight = CASE WHEN effective_weight < 0.01 THEN 0.01 WHEN effective_weight > 1.0 THEN 1.0 ELSE effective_weight END, d.path_count = path_count
            ON MATCH SET d.weight = CASE WHEN effective_weight > coalesce(d.weight, 0.0)
                                         THEN (CASE WHEN effective_weight > 1.0 THEN 1.0 ELSE effective_weight END) ELSE d.weight END,
                         d.path_count = CASE WHEN path_count > coalesce(d.path_count, 0)
                                             THEN path_count ELSE d.path_count END
        """, tx=tx)

    def _derive_dependencies(self, tx: Any = None) -> None:
        """
        Derive DEPENDS_ON relationships from structural edges (Phase 4).

        Implements Rules 1–7 from docs/graph-model.md §4.4. Rules 1 and 2 each
        run three times: once for directly attached components and twice for
        components reaching the topic through a USES chain of up to three hops.
        """
        # Rule 1: app_to_app — a subscriber depends on the publishers of its topics
        self._merge_dependency("""
            MATCH (subscriber)-[:SUBSCRIBES_TO]->(t:Topic)<-[:PUBLISHES_TO]-(publisher)
            WHERE subscriber <> publisher
              AND (subscriber:Application OR subscriber:Library)
              AND (publisher:Application OR publisher:Library)
        """, "subscriber", "publisher", "app_to_app", tx=tx)

        # Rule 1 (transitive): App-A -[USES*]-> Lib-X -[SUBSCRIBES_TO]-> T <-[PUBLISHES_TO]- App-B
        self._merge_dependency("""
            MATCH (app:Application)-[:USES*1..3]->(lib)-[:SUBSCRIBES_TO]->(t:Topic)<-[:PUBLISHES_TO]-(publisher)
            WHERE app <> publisher
              AND (publisher:Application OR publisher:Library)
        """, "app", "publisher", "app_to_app", tx=tx)

        # Rule 1 (transitive, reverse): App-A -[SUBSCRIBES_TO]-> T <-[PUBLISHES_TO]- Lib-Y <-[USES*]- App-B
        self._merge_dependency("""
            MATCH (subscriber)-[:SUBSCRIBES_TO]->(t:Topic)<-[:PUBLISHES_TO]-(lib)<-[:USES*1..3]-(app:Application)
            WHERE subscriber <> app
              AND (subscriber:Application OR subscriber:Library)
        """, "subscriber", "app", "app_to_app", tx=tx)

        # Rule 2: app_to_broker — a component depends on the brokers routing its topics
        self._merge_dependency("""
            MATCH (app)-[:PUBLISHES_TO|SUBSCRIBES_TO]->(t:Topic)<-[:ROUTES]-(broker:Broker)
            WHERE app:Application OR app:Library
        """, "app", "broker", "app_to_broker", tx=tx)

        # Rule 2 (transitive): the same, reached through a library chain
        self._merge_dependency("""
            MATCH (app:Application)-[:USES*1..3]->(lib)-[:PUBLISHES_TO|SUBSCRIBES_TO]->(t:Topic)<-[:ROUTES]-(broker:Broker)
        """, "app", "broker", "app_to_broker", tx=tx)

        # Rule 3: node_to_node — lifted from component-level app_to_app / app_to_broker dependencies.
        # Explicitly filtering to the source dependency types that carry RUNS_ON context,
        # making this resilient to rule reordering.
        self._run_query("""
            MATCH (a)-[d_ab:DEPENDS_ON]->(b),
                  (a)-[:RUNS_ON]->(n1:Node),
                  (b)-[:RUNS_ON]->(n2:Node)
            WHERE n1 <> n2
              AND d_ab.dependency_type IN ['app_to_app', 'app_to_broker']
            WITH n1, n2, max(d_ab.weight) as lifted_max, count(*) as dep_count
            MERGE (n1)-[d:DEPENDS_ON {dependency_type: 'node_to_node'}]->(n2)
            SET d.weight = coalesce(lifted_max, 0.01), d.path_count = dep_count
        """, tx=tx)
 
        # Rule 4: node_to_broker — lifted from hosted app broker usage
        self._run_query("""
            MATCH (app)-[dep:DEPENDS_ON {dependency_type: 'app_to_broker'}]->(broker:Broker),
                  (app)-[:RUNS_ON]->(n:Node)
            WITH n, broker, max(dep.weight) as lifted_max, count(*) as dep_count
            MERGE (n)-[d:DEPENDS_ON {dependency_type: 'node_to_broker'}]->(broker)
            SET d.weight = coalesce(lifted_max, 0.01), d.path_count = dep_count
        """, tx=tx)

        # Rule 7: node_to_device — a host depends on the switches/routers every
        # network path to its node_to_node dependencies crosses. A separator test
        # is not expressible in Cypher, so it is computed by the shared helper.
        self._derive_node_to_device(tx)
 
        # Rule 6: broker_to_broker — colocation dependency via shared node
        # Weight is a placeholder (0.01) here; Phase 5b overwrites it with the
        # shared Node's weight, which was computed back at import time.
        self._run_query("""
            MATCH (b1:Broker)-[:RUNS_ON]->(n:Node)<-[:RUNS_ON]-(b2:Broker)
            WHERE b1 <> b2
            WITH b1, b2, count(DISTINCT n) as path_count
            MERGE (b1)-[d:DEPENDS_ON {dependency_type: 'broker_to_broker'}]->(b2)
            SET d.path_count = path_count,
                d.weight = coalesce(d.weight, 0.01)
        """, tx=tx)
 
        # Rule 5: app_to_lib — app depends on shared library
        # Weight is a placeholder (0.01) here; Phase 5b overwrites it with the
        # consuming Application's weight, computed back at import time.
        self._run_query("""
            MATCH (app)-[:USES]->(lib:Library)
            WHERE app:Application OR app:Library
            WITH app, lib, count(*) as path_count
            MERGE (app)-[d:DEPENDS_ON {dependency_type: 'app_to_lib'}]->(lib)
            SET d.path_count = path_count,
                d.weight = coalesce(d.weight, 0.01)
        """, tx=tx)

    # ==========================================
    # Query Methods (Read)
    # ==========================================

    def get_graph_data(
        self,
        component_types: Optional[List[str]] = None,
        dependency_types: Optional[List[str]] = None,
        include_raw: bool = False,
    ) -> GraphData:
        """
        Retrieve graph data with optional type filtering.
        
        Args:
            component_types: Filter to specific vertex types (e.g., ["Application"])
            dependency_types: Filter to specific dependency subtypes (e.g., ["app_to_app"])
            include_raw: Include raw structural edges in addition to DEPENDS_ON
        """
        components = []
        edges = []

        with self.driver.session(database=self.database) as session:
            # Fetch components
            for comp_type in component_types or COMPONENT_LABELS:
                result = session.run(
                    f"MATCH (n:{comp_type}) RETURN n.id as id, n.name as name, n.weight as weight, "
                    f"labels(n)[0] as type, properties(n) as props"
                )
                for record in result:
                    props = dict(record["props"])
                    props.pop("id", None)
                    props.pop("weight", None)
                    # Ensure name is present, fallback to ID
                    props["name"] = record["name"] or record["id"]

                    components.append(ComponentData(
                        id=record["id"],
                        component_type=record["type"],
                        weight=record["weight"] or 1.0,
                        properties=props,
                    ))

            # Fetch DEPENDS_ON edges
            dep_filter = ""
            if dependency_types:
                types_str = ", ".join(f"'{t}'" for t in dependency_types)
                dep_filter = f" WHERE d.dependency_type IN [{types_str}]"

            result = session.run(
                f"MATCH (s)-[d:DEPENDS_ON]->(t){dep_filter} "
                f"RETURN s.id as src, t.id as tgt, labels(s)[0] as stype, "
                f"labels(t)[0] as ttype, d.dependency_type as dep_type, d.weight as weight, d.path_count as path_count"
            )
            for record in result:
                edges.append(EdgeData(
                    source_id=record["src"],
                    target_id=record["tgt"],
                    source_type=record["stype"],
                    target_type=record["ttype"],
                    dependency_type=record["dep_type"],
                    relation_type="DEPENDS_ON",
                    weight=record["weight"] or 1.0,
                    path_count=record["path_count"] or 1,
                ))

            # Optionally include raw structural edges
            if include_raw:
                for rel_type, _, _ in STRUCTURAL_RELATIONSHIPS.values():
                    result = session.run(
                        f"MATCH (s)-[r:{rel_type}]->(t) "
                        f"RETURN s.id as src, t.id as tgt, labels(s)[0] as stype, "
                        f"labels(t)[0] as ttype, r.weight as weight, properties(r) as props"
                    )
                    for record in result:
                        # Only network links carry input properties (interface, capacity, load).
                        props = (
                            {k: v for k, v in dict(record["props"]).items() if k != "weight"}
                            if rel_type == "CONNECTS_TO" else {}
                        )
                        edges.append(EdgeData(
                            source_id=record["src"],
                            target_id=record["tgt"],
                            source_type=record["stype"],
                            target_type=record["ttype"],
                            dependency_type=rel_type.lower(),
                            relation_type=rel_type,
                            weight=record["weight"] or 1.0,
                            properties=props,
                        ))

        return GraphData(components=components, edges=edges)

    def get_layer_data(self, layer: str) -> GraphData:
        """
        Retrieve graph data for a specific architectural layer (layer projection).

        Args:
            layer: Layer name — canonical (app, infra, mw, system) or
                   legacy alias (application, infrastructure, app_broker, complete)
        """
        defn = get_layer_definition(resolve_layer(layer))
        return self.get_graph_data(
            component_types=sorted(defn.component_types),
            dependency_types=sorted(defn.dependency_types),
        )

    def get_statistics(self) -> Dict[str, int]:
        """Retrieve counts of components and dependencies by type."""
        counts = {
            "total_nodes": "MATCH (n) WHERE NOT n:Metadata RETURN count(n) as c",
            "total_relationships": "MATCH ()-[r]->() RETURN count(r) as c",
        }
        for label in COMPONENT_LABELS:
            counts[f"{label.lower()}_count"] = f"MATCH (n:{label}) RETURN count(n) as c"
        for rel_type, _, _ in STRUCTURAL_RELATIONSHIPS.values():
            counts[f"{rel_type.lower()}_count"] = f"MATCH ()-[r:{rel_type}]->() RETURN count(r) as c"
        for dep_type in DEPENDENCY_TYPES:
            counts[f"{dep_type}_count"] = (
                f"MATCH ()-[r:DEPENDS_ON {{dependency_type: '{dep_type}'}}]->() RETURN count(r) as c"
            )

        with self.driver.session(database=self.database) as session:
            return {key: session.run(query).single()["c"] for key, query in counts.items()}

    def _get_metadata_dict(self) -> Dict[str, Any]:
        """
        Retrieve graph metadata from the :Metadata node and reconstruct the nested structure.
        """
        query = "MATCH (m:Metadata) RETURN properties(m) as props"
        with self.driver.session(database=self.database) as session:
            result = session.run(query)
            record = result.single()
            if not record:
                return {}
            
            return serialization.reconstruct_metadata_dict(record["props"])

    def export_json(self) -> Dict[str, Any]:
        """
        Export graph as JSON (compatible with data generation format).
        Consolidated via get_graph_data to ensure logic consistency.
        """
        # Fetch everything: all component types, all dependency types, and raw structural edges
        graph_data = self.get_graph_data(include_raw=True)
        metadata = self._get_metadata_dict()
        
        return serialization.reconstruct_export_payload(graph_data, metadata)

    def get_library_usage(self) -> Dict[str, int]:
        """Get library usage counts."""
        query = """
        MATCH (l:Library)<-[:USES]-(a:Application)
        RETURN l.id as id, count(a) as usage_count
        ORDER BY usage_count DESC
        """
        usage = {}
        with self.driver.session(database=self.database) as session:
            result = session.run(query)
            for record in result:
                usage[record["id"]] = record["usage_count"]
        return usage

    def get_node_allocations(self) -> Dict[str, List[str]]:
        """Get allocation of components to nodes."""
        query = """
        MATCH (n:Node)<-[:RUNS_ON]-(c)
        RETURN n.id as node_id, collect(c.id) as components
        """
        allocations = {}
        with self.driver.session(database=self.database) as session:
            result = session.run(query)
            for record in result:
                allocations[record["node_id"]] = record["components"]
        return allocations

    def get_broker_routing(self) -> Dict[str, List[str]]:
        """Get topics routed by each broker."""
        query = """
        MATCH (b:Broker)-[:ROUTES]->(t:Topic)
        RETURN b.id as broker_id, collect(t.id) as topics
        """
        routing = {}
        with self.driver.session(database=self.database) as session:
            result = session.run(query)
            for record in result:
                routing[record["broker_id"]] = record["topics"]
        return routing

    def get_components_with_filter(
        self, 
        component_type: Optional[str] = None, 
        min_weight: Optional[float] = None, 
        limit: int = 100
    ) -> Dict[str, Any]:
        """Get components from the graph with optional filtering."""
        with self.driver.session(database=self.database) as session:
            # Build query
            label_filter = f":{component_type}" if component_type else ""
            weight_filter = f"WHERE coalesce(n.weight, 0.0) >= {min_weight}" if min_weight is not None else ""
            
            query = f"""
            MATCH (n{label_filter})
            {weight_filter}
            RETURN n.id as id, labels(n)[0] as type, coalesce(n.weight, 1.0) as weight, properties(n) as props
            LIMIT $limit
            """
            
            result = session.run(query, limit=limit)
            components = []
            
            for record in result:
                props = dict(record["props"])
                props.pop("id", None)
                props.pop("weight", None)
                components.append({
                    "id": record["id"],
                    "weight": record["weight"],
                    **props,
                    "type": record["type"],
                })
                
            return {
                "count": len(components),
                "components": components
            }

    def get_edges_with_filter(
        self, 
        dependency_type: Optional[str] = None, 
        min_weight: Optional[float] = None, 
        limit: int = 100
    ) -> Dict[str, Any]:
        """Get edges from the graph with optional filtering."""
        with self.driver.session(database=self.database) as session:
            # Build query
            dep_filter = f"{{dependency_type: '{dependency_type}'}}" if dependency_type else ""
            weight_filter = f"WHERE coalesce(r.weight, 0.0) >= {min_weight}" if min_weight is not None else ""
            
            query = f"""
            MATCH (s)-[r:DEPENDS_ON{dep_filter}]->(t)
            {weight_filter}
            RETURN s.id as source, t.id as target, labels(s)[0] as source_type, 
                   labels(t)[0] as target_type, r.dependency_type as dependency_type, 
                   coalesce(r.weight, 1.0) as weight, properties(r) as props
            LIMIT $limit
            """
            
            result = session.run(query, limit=limit)
            edges = []
            
            for record in result:
                props = dict(record["props"])
                props.pop("weight", None)
                props.pop("dependency_type", None)
                edges.append({
                    "source": record["source"],
                    "target": record["target"],
                    "source_type": record["source_type"],
                    "target_type": record["target_type"],
                    "dependency_type": record["dependency_type"],
                    "weight": record["weight"],
                    **props
                })
                
            return {
                "count": len(edges),
                "edges": edges
            }

    def search_nodes(self, query: str, limit: int) -> List[Dict[str, Any]]:
        """Search for nodes by ID or label."""
        cypher_query = """
        MATCH (n)
        WHERE (n:Application OR n:Broker OR n:Node OR n:Topic OR n:Library)
            AND (toLower(n.id) CONTAINS toLower($search_term) OR toLower(COALESCE(n.name, n.id)) CONTAINS toLower($search_term))
        RETURN n.id AS id, labels(n)[0] AS type,
                COALESCE(n.name, n.id) AS label,
                COALESCE(n.weight, 1.0) AS weight
        ORDER BY n.id
        LIMIT $limit
        """
        
        with self.driver.session(database=self.database) as session:
            result = session.run(cypher_query, search_term=query, limit=limit)
            nodes = []
            for record in result:
                nodes.append({
                    "id": record["id"],
                    "type": record["type"],
                    "label": record["label"],
                    "weight": float(record["weight"])
                })
            return nodes

    def get_node_connections(self, node_id: str, fetch_structural: bool, depth: int) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Fetch connections for a specific node at specified depth.
        Returns (nodes, edges) as list of dicts.
        """
        # Determine relationship types based on view
        if fetch_structural:
            rel_types = "|".join(["PUBLISHES_TO", "SUBSCRIBES_TO", "RUNS_ON", "ROUTES", "CONNECTS_TO", "USES"])
            query = f"""
            MATCH (center {{id: $node_id}})
            MATCH path = (center)-[:{rel_types}*1..{depth}]-(connected)
            WITH DISTINCT connected
            RETURN connected.id AS id, labels(connected)[0] AS type,
                    COALESCE(connected.weight, 1.0) AS weight, properties(connected) AS props
            """
            
            edges_query = f"""
            MATCH (center {{id: $node_id}})
            MATCH path = (center)-[:{rel_types}*1..{depth}]-(n)
            WITH DISTINCT n
            WITH collect(DISTINCT n.id) + [$node_id] AS node_ids
            UNWIND node_ids AS node_id
            MATCH (s {{id: node_id}})-[r:{rel_types}]->(t)
            WHERE t.id IN node_ids
            RETURN DISTINCT s.id AS source_id, t.id AS target_id,
                    labels(s)[0] AS source_type, labels(t)[0] AS target_type,
                    type(r) AS relation_type, COALESCE(r.weight, 1.0) AS weight,
                    properties(r) AS props
            """
        else:
            query = f"""
            MATCH (center {{id: $node_id}})
            MATCH path = (center)-[:DEPENDS_ON*1..{depth}]-(connected)
            WITH DISTINCT connected
            RETURN connected.id AS id, labels(connected)[0] AS type,
                    COALESCE(connected.weight, 1.0) AS weight, properties(connected) AS props
            """
            
            edges_query = f"""
            MATCH (center {{id: $node_id}})
            MATCH path = (center)-[:DEPENDS_ON*1..{depth}]-(n)
            WITH DISTINCT n
            WITH collect(DISTINCT n.id) + [$node_id] AS node_ids
            UNWIND node_ids AS node_id
            MATCH (s {{id: node_id}})-[r:DEPENDS_ON]->(t)
            WHERE t.id IN node_ids
            RETURN DISTINCT s.id AS source_id, t.id AS target_id,
                    labels(s)[0] AS source_type, labels(t)[0] AS target_type,
                    COALESCE(r.dependency_type, 'unknown') AS dependency_type,
                    COALESCE(r.weight, 1.0) AS weight, properties(r) AS props
            """
        
        # Center node query
        center_node_query = """
        MATCH (center {id: $node_id})
        RETURN center.id AS id, labels(center)[0] AS type,
                COALESCE(center.weight, 1.0) AS weight, properties(center) AS props
        """
        
        with self.driver.session(database=self.database) as session:
            # Check center node
            result = session.run(center_node_query, node_id=node_id)
            components = []
            for record in result:
                props = dict(record["props"])
                props.pop("id", None)
                props.pop("weight", None)
                components.append({
                    "id": record["id"],
                    "weight": float(record["weight"]),
                    **props,
                    "type": record["type"],
                })
            
            if not components:
                return [], []

            # Fetch connected nodes
            result = session.run(query, node_id=node_id)
            for record in result:
                props = dict(record["props"])
                props.pop("id", None)
                props.pop("weight", None)
                components.append({
                    "id": record["id"],
                    "weight": float(record["weight"]),
                    **props,
                    "type": record["type"],
                })
            
            # Fetch edges
            result = session.run(edges_query, node_id=node_id)
            edges = []
            for record in result:
                props = dict(record["props"])
                props.pop("weight", None)
                props.pop("dependency_type", None)
                
                edge = {
                    "source": record["source_id"],
                    "target": record["target_id"],
                    "source_type": record["source_type"],
                    "target_type": record["target_type"],
                    "relation_type": record.get("relation_type", "DEPENDS_ON"),
                    "weight": float(record["weight"]),
                    **props
                }
                
                if "dependency_type" in record.keys():
                    edge["dependency_type"] = record["dependency_type"]
                
                edges.append(edge)
                
            return components, edges

    def get_topology_data(self, node_id: Optional[str], node_limit: int) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Fetch topology data with drill-down support."""
        with self.driver.session(database=self.database) as session:
            components = []
            edges = []
            
            if node_id:
                # First, determine the type of the selected node
                type_query = "MATCH (n {id: $node_id}) RETURN labels(n)[0] AS type"
                result = session.run(type_query, node_id=node_id)
                record = result.single()
                
                if not record:
                    raise ValueError(f"Node {node_id} not found")
                
                node_type = record["type"]
                self.logger.info(f"Node {node_id} is of type {node_type}")
                
                if node_type == "Node":
                    # Level 2: Show Applications and Brokers running on this Node
                    self._fetch_node_topology(session, components, edges, node_id)
                elif node_type == "Application":
                    # Level 3: Show Topics and Libraries related to this Application
                    self._fetch_application_topology(session, components, edges, node_id)
                elif node_type == "Broker":
                    # Level 3 for Broker: Show Topics it routes
                    self._fetch_broker_topology(session, components, edges, node_id)
                elif node_type == "Library":
                    # Level 3 for Library: Show Libraries it uses and Applications using it
                    self._fetch_library_topology(session, components, edges, node_id)
                elif node_type == "Topic":
                    # Level 3 for Topic: Show Brokers that route it and Applications that publish/subscribe
                    self._fetch_topic_topology(session, components, edges, node_id)
                else:
                    self._fetch_single_node(session, components, node_id)
            
            else:
                # Level 1: Full topology
                self._fetch_full_topology(session, components, edges, node_limit)
                
            return components, edges

    def _process_node_result(self, result, components):
        for record in result:
            props = dict(record["props"])
            name = props.get("name", record["id"])
            props.pop("id", None)
            props.pop("weight", None)
            props.pop("name", None)
            components.append({
                "id": record["id"],
                "label": name,
                "weight": float(record["weight"]),
                **props,
                "type": record["type"],
            })

    def _process_edge_result(self, result, edges, relation_type=None):
        for record in result:
            props = dict(record["props"])
            props.pop("weight", None)
            edges.append({
                "source": record["source_id"],
                "target": record["target_id"],
                "source_type": record["source_type"],
                "target_type": record["target_type"],
                "relation_type": relation_type or record.get("relation_type"),
                "weight": float(record["weight"]),
                **props
            })

    def _fetch_node_topology(self, session, components, edges, node_id):
        node_query = """
        MATCH (center:Node {id: $node_id})
        RETURN center.id AS id, labels(center)[0] AS type,
                COALESCE(center.weight, 1.0) AS weight, properties(center) AS props
        UNION
        MATCH (center:Node {id: $node_id})<-[:RUNS_ON]-(entity)
        WHERE entity:Application OR entity:Broker
        RETURN entity.id AS id, labels(entity)[0] AS type,
                COALESCE(entity.weight, 1.0) AS weight, properties(entity) AS props
        """
        self._process_node_result(session.run(node_query, node_id=node_id), components)
        
        edges_query = """
        MATCH (entity)-[r:RUNS_ON]->(center:Node {id: $node_id})
        WHERE entity:Application OR entity:Broker
        RETURN entity.id AS source_id, center.id AS target_id,
                labels(entity)[0] AS source_type, labels(center)[0] AS target_type,
                COALESCE(r.weight, 1.0) AS weight, properties(r) AS props
        """
        self._process_edge_result(session.run(edges_query, node_id=node_id), edges, "RUNS_ON")

    def _fetch_application_topology(self, session, components, edges, node_id):
        node_query = """
        MATCH (center:Application {id: $node_id})
        RETURN center.id AS id, labels(center)[0] AS type, COALESCE(center.weight, 1.0) AS weight, properties(center) AS props
        UNION
        MATCH (center:Application {id: $node_id})-[:PUBLISHES_TO|SUBSCRIBES_TO]->(topic:Topic)
        RETURN topic.id AS id, labels(topic)[0] AS type, COALESCE(topic.weight, 1.0) AS weight, properties(topic) AS props
        UNION
        MATCH (center:Application {id: $node_id})-[:USES]->(lib:Library)
        RETURN lib.id AS id, labels(lib)[0] AS type, COALESCE(lib.weight, 1.0) AS weight, properties(lib) AS props
        """
        self._process_node_result(session.run(node_query, node_id=node_id), components)
        
        edges_query = """
        MATCH (center:Application {id: $node_id})-[r:PUBLISHES_TO|SUBSCRIBES_TO|USES]->(target)
        WHERE target:Topic OR target:Library
        RETURN center.id AS source_id, target.id AS target_id, labels(center)[0] AS source_type, labels(target)[0] AS target_type,
                type(r) AS relation_type, COALESCE(r.weight, 1.0) AS weight, properties(r) AS props
        """
        self._process_edge_result(session.run(edges_query, node_id=node_id), edges)

    def _fetch_broker_topology(self, session, components, edges, node_id):
        node_query = """
        MATCH (center:Broker {id: $node_id})
        RETURN center.id AS id, labels(center)[0] AS type, COALESCE(center.weight, 1.0) AS weight, properties(center) AS props
        UNION
        MATCH (center:Broker {id: $node_id})-[:ROUTES]->(topic:Topic)
        RETURN topic.id AS id, labels(topic)[0] AS type, COALESCE(topic.weight, 1.0) AS weight, properties(topic) AS props
        """
        self._process_node_result(session.run(node_query, node_id=node_id), components)
        
        edges_query = """
        MATCH (center:Broker {id: $node_id})-[r:ROUTES]->(topic:Topic)
        RETURN center.id AS source_id, topic.id AS target_id, labels(center)[0] AS source_type, labels(topic)[0] AS target_type,
                COALESCE(r.weight, 1.0) AS weight, properties(r) AS props
        """
        self._process_edge_result(session.run(edges_query, node_id=node_id), edges, "ROUTES")

    def _fetch_library_topology(self, session, components, edges, node_id):
        node_query = """
        MATCH (center:Library {id: $node_id})
        RETURN center.id AS id, labels(center)[0] AS type, COALESCE(center.weight, 1.0) AS weight, properties(center) AS props
        UNION
        MATCH (center:Library {id: $node_id})-[:USES]->(lib:Library)
        RETURN lib.id AS id, labels(lib)[0] AS type, COALESCE(lib.weight, 1.0) AS weight, properties(lib) AS props
        UNION
        MATCH (app:Application)-[:USES]->(center:Library {id: $node_id})
        RETURN app.id AS id, labels(app)[0] AS type, COALESCE(app.weight, 1.0) AS weight, properties(app) AS props
        """
        self._process_node_result(session.run(node_query, node_id=node_id), components)
        
        edges_query = """
        MATCH (center:Library {id: $node_id})-[r:USES]->(lib:Library)
        RETURN center.id AS source_id, lib.id AS target_id, labels(center)[0] AS source_type, labels(lib)[0] AS target_type,
                COALESCE(r.weight, 1.0) AS weight, properties(r) AS props
        UNION
        MATCH (app:Application)-[r:USES]->(center:Library {id: $node_id})
        RETURN app.id AS source_id, center.id AS target_id, labels(app)[0] AS source_type, labels(center)[0] AS target_type,
                COALESCE(r.weight, 1.0) AS weight, properties(r) AS props
        """
        self._process_edge_result(session.run(edges_query, node_id=node_id), edges, "USES")

    def _fetch_topic_topology(self, session, components, edges, node_id):
        node_query = """
        MATCH (center:Topic {id: $node_id})
        RETURN center.id AS id, labels(center)[0] AS type, COALESCE(center.weight, 1.0) AS weight, properties(center) AS props
        UNION
        MATCH (broker:Broker)-[:ROUTES]->(center:Topic {id: $node_id})
        RETURN broker.id AS id, labels(broker)[0] AS type, COALESCE(broker.weight, 1.0) AS weight, properties(broker) AS props
        UNION
        MATCH (app:Application)-[:PUBLISHES_TO|SUBSCRIBES_TO]->(center:Topic {id: $node_id})
        RETURN app.id AS id, labels(app)[0] AS type, COALESCE(app.weight, 1.0) AS weight, properties(app) AS props
        """
        self._process_node_result(session.run(node_query, node_id=node_id), components)
        
        edges_query = """
        MATCH (broker:Broker)-[r:ROUTES]->(center:Topic {id: $node_id})
        RETURN broker.id AS source_id, center.id AS target_id, labels(broker)[0] AS source_type, labels(center)[0] AS target_type,
                type(r) AS relation_type, COALESCE(r.weight, 1.0) AS weight, properties(r) AS props
        UNION
        MATCH (app:Application)-[r:PUBLISHES_TO|SUBSCRIBES_TO]->(center:Topic {id: $node_id})
        RETURN app.id AS source_id, center.id AS target_id, labels(app)[0] AS source_type, labels(center)[0] AS target_type,
                type(r) AS relation_type, COALESCE(r.weight, 1.0) AS weight, properties(r) AS props
        """
        self._process_edge_result(session.run(edges_query, node_id=node_id), edges)

    def _fetch_single_node(self, session, components, node_id):
        node_query = """
        MATCH (n {id: $node_id})
        RETURN n.id AS id, labels(n)[0] AS type, COALESCE(n.weight, 1.0) AS weight, properties(n) AS props
        """
        self._process_node_result(session.run(node_query, node_id=node_id), components)

    def _fetch_full_topology(self, session, components, edges, node_limit):
        node_query = """
        MATCH (n) WHERE n:Node OR n:NetworkDevice
        RETURN n.id AS id, labels(n)[0] AS type, COALESCE(n.weight, 1.0) AS weight, properties(n) AS props
        ORDER BY COALESCE(n.weight, 0.0) DESC
        LIMIT $limit
        """
        self._process_node_result(session.run(node_query, limit=node_limit), components)
        
        # Helper to extract node IDs
        node_ids = [c["id"] for c in components]
        
        edges_query = """
        MATCH (s)-[r:CONNECTS_TO]->(t)
        WHERE s.id IN $node_ids AND t.id IN $node_ids
        RETURN s.id AS source_id, t.id AS target_id, labels(s)[0] AS source_type, labels(t)[0] AS target_type,
                type(r) AS relation_type, COALESCE(r.weight, 1.0) AS weight, properties(r) AS props
        ORDER BY COALESCE(r.weight, 0.0) DESC
        """
        self._process_edge_result(session.run(edges_query, node_ids=node_ids), edges)