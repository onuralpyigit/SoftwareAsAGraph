"""
Tests for Step 1: Graph Model Construction

Validates the formal definitions from docs/graph-model.md:
    - Definition 1: Graph model G = (V, E, τ_V, τ_E, L, w, QoS)
    - Definition 2: Dependency derivation rules (Rules 1-4)
    - Definition 3: Layer projection π_l
    - Definition 4: Graph constraints (C1-C5)
    - §1.5: Weight calculation with minimum floor
"""

import math
import pytest

from saag.core import (
    QoSPolicy, MIN_TOPIC_WEIGHT,
    Application, Topic, Broker, Node, Library,
    AnalysisLayer, LAYER_DEFINITIONS, SIMULATION_LAYERS,
    DEPENDENCY_TO_LAYER, resolve_layer,
)


# =========================================================================
# §1.5 QoS Weight Calculation
# =========================================================================

class TestQoSPolicy:
    """Tests for QoSPolicy weight calculation matching §1.5 scoring table."""

    def test_default_qos_weight(self):
        """Default QoS (BEST_EFFORT, VOLATILE, MEDIUM) → ~0.046 (0.24*0 + 0.62*0 + 0.14*0.33)."""
        policy = QoSPolicy()
        assert policy.calculate_weight() == pytest.approx(0.14 * 0.33, abs=0.005)

    def test_maximum_qos_weight(self):
        """Maximum QoS (RELIABLE + PERSISTENT + URGENT) → 1.0 (0.3*1 + 0.4*1 + 0.3*1)."""
        policy = QoSPolicy(
            reliability="RELIABLE",
            durability="PERSISTENT",
            transport_priority="URGENT",
        )
        assert policy.calculate_weight() == pytest.approx(1.0, abs=0.01)

    def test_lowest_qos_weight(self):
        """Lowest QoS (BEST_EFFORT, VOLATILE, LOW) → 0.0."""
        policy = QoSPolicy(
            reliability="BEST_EFFORT",
            durability="VOLATILE",
            transport_priority="LOW",
        )
        assert policy.calculate_weight() == pytest.approx(0.0, abs=0.01)

    # --- Individual QoS attribute tests (justified weights) ---

    @pytest.mark.parametrize("reliability, durability, transport_priority, expected", [
        ("RELIABLE", "VOLATILE", "LOW", 0.24),           # reliability weight = 0.24
        ("BEST_EFFORT", "PERSISTENT", "LOW", 0.62),      # durability weight = 0.62
        ("BEST_EFFORT", "TRANSIENT_LOCAL", "LOW", 0.31),  # transient local (0.5) * 0.62 = 0.31
        ("BEST_EFFORT", "TRANSIENT", "LOW", 0.372),      # transient (0.6) * 0.62 = 0.372
        ("BEST_EFFORT", "VOLATILE", "URGENT", 0.14),     # urgent (1.0) * priority weight (0.14) = 0.14
        ("BEST_EFFORT", "VOLATILE", "HIGH", 0.0924),     # high (0.66) * 0.14 ≈ 0.0924
        ("BEST_EFFORT", "VOLATILE", "MEDIUM", 0.0462),   # medium (0.33) * 0.14 ≈ 0.0462
        ("BEST_EFFORT", "VOLATILE", "LOW", 0.0),         # low priority adds 0
    ])
    def test_individual_qos_attribute_weight(self, reliability, durability, transport_priority, expected):
        policy = QoSPolicy(reliability=reliability, durability=durability, transport_priority=transport_priority)
        assert policy.calculate_weight() == pytest.approx(expected, abs=0.01)

    # --- Serialization ---

    def test_to_dict(self):
        policy = QoSPolicy(reliability="RELIABLE", durability="PERSISTENT", transport_priority="URGENT")
        d = policy.to_dict()
        # Both temporal-contract keys are always emitted, matching
        # Topic.to_dict(); a conditional key made "declared 10" and
        # "defaulted 10" indistinguishable on the round-trip.
        assert d == {
            "reliability": "RELIABLE",
            "durability": "PERSISTENT",
            "transport_priority": "URGENT",
            "deadline_ms": None,
            "history_depth": 10,
        }

    def test_from_dict(self):
        policy = QoSPolicy.from_dict({"reliability": "RELIABLE", "durability": "TRANSIENT"})
        assert policy.reliability == "RELIABLE"
        assert policy.durability == "TRANSIENT"
        assert policy.transport_priority == "MEDIUM"  # default
        assert policy.deadline_ms is None
        assert policy.history_depth == 10

    def test_deadline_and_history_depth(self):
        p1 = QoSPolicy.from_dict({"deadline_ms": 50.0, "history_depth": 20})
        assert p1.deadline_ms == 50.0
        assert p1.history_depth == 20
        d1 = p1.to_dict()
        assert d1["deadline_ms"] == 50.0
        assert d1["history_depth"] == 20

        p2 = QoSPolicy.from_node_attrs({"qos_deadline_ms": 100.0, "qos_history_depth": 5})
        assert p2.deadline_ms == 100.0
        assert p2.history_depth == 5


# =========================================================================
# §1.5 Topic Weight with Minimum Floor
# =========================================================================

class TestTopicWeight:
    """Tests for Topic.calculate_weight() with minimum weight floor."""

    def test_minimum_weight_floor(self):
        """A topic with genuinely zero signal on every term still gets ε = MIN_TOPIC_WEIGHT.

        ``Topic.__post_init__`` always auto-assigns a nonzero generator frequency
        (>= 1 Hz) when none is supplied, so a *realistic* topic never actually
        reaches the floor (see ``test_realistic_tiny_topic_no_longer_floors``
        below) — the floor clamp is exercised directly here via
        ``compute_topic_weight`` with an explicit near-zero frequency instead.
        """
        from saag.core.models import compute_topic_weight

        qos = QoSPolicy(reliability="BEST_EFFORT", durability="VOLATILE", transport_priority="LOW")
        weight = compute_topic_weight(qos, size=0, frequency=1e-9)
        assert weight == pytest.approx(MIN_TOPIC_WEIGHT, abs=1e-6)
        assert weight > 0.0, "Topic weight must never be zero"

    def test_realistic_tiny_topic_no_longer_floors(self):
        """1-byte, LOW-QoS topic with the generator's auto-assigned frequency.

        SizeNorm's envelope moved from an unstated ~1 EiB (KiB-based /50.0
        divisor) to a documented 1 MiB (TOPIC_SIZE_ENVELOPE_BYTES), so alpha's
        declared 15% budget is no longer negligible even at the smallest
        payload — this topic no longer collapses to the floor the way it did
        before the rescale.
        """
        topic = Topic(
            id="t0", name="minimal", size=1,
            qos=QoSPolicy(reliability="BEST_EFFORT", durability="VOLATILE", transport_priority="LOW"),
        )
        assert topic.calculate_weight() > MIN_TOPIC_WEIGHT + 0.005

    def test_small_topic_weight(self):
        """1 KB topic with default QoS."""
        topic = Topic(id="t1", name="small", size=1024)
        weight = topic.calculate_weight()
        assert 0.115 < weight < 0.125

    def test_medium_topic_weight(self):
        """64 KB topic with RELIABLE QoS."""
        topic = Topic(
            id="t2", name="medium",
            size=65536,
            qos=QoSPolicy(reliability="RELIABLE"),
        )
        weight = topic.calculate_weight()
        assert 0.37 < weight < 0.40

    def test_max_topic_weight(self):
        """Maximum QoS + large size."""
        topic = Topic(
            id="t3", name="max",
            size=1_048_576,  # 1 MB
            qos=QoSPolicy(reliability="RELIABLE", durability="PERSISTENT", transport_priority="URGENT"),
        )
        weight = topic.calculate_weight()
        assert weight == pytest.approx(0.977, abs=0.02)

    def test_size_score_formula(self):
        """Verify w = max(MIN, beta*QoS + alpha*size_norm + psi*freq_norm) exactly.

        Reads SizeNorm/FreqNorm through ``compute_size_norm``/``compute_freq_norm``
        rather than reimplementing the envelope math inline, so this test cannot
        drift from the shipped formula the way an earlier version of it did.
        """
        from saag.core.models import (
            TOPIC_QOS_WEIGHT_BETA, TOPIC_SIZE_WEIGHT_ALPHA, TOPIC_FREQ_WEIGHT_PSI,
            compute_size_norm, compute_freq_norm,
        )
        for size in (64, 1024, 65536):
            topic = Topic(
                id="t", name="test", size=size,
                qos=QoSPolicy(reliability="BEST_EFFORT", durability="VOLATILE", transport_priority="LOW"),
            )
            weight = topic.calculate_weight()
            expected = max(
                MIN_TOPIC_WEIGHT,
                TOPIC_QOS_WEIGHT_BETA * 0.0 +
                TOPIC_SIZE_WEIGHT_ALPHA * compute_size_norm(topic.size) +
                TOPIC_FREQ_WEIGHT_PSI * compute_freq_norm(topic.frequency)
            )
            assert weight == pytest.approx(expected, abs=0.001), f"Failed for size={size}"

    def test_weight_range(self):
        """All weights must be in [MIN_TOPIC_WEIGHT, 1.0]."""
        configs = [
            (1, QoSPolicy(reliability="BEST_EFFORT", durability="VOLATILE", transport_priority="LOW")),
            (256, QoSPolicy()),
            (65536, QoSPolicy(reliability="RELIABLE", durability="TRANSIENT", transport_priority="HIGH")),
            (1_048_576, QoSPolicy(reliability="RELIABLE", durability="PERSISTENT", transport_priority="URGENT")),
            (10**15, QoSPolicy(reliability="RELIABLE", durability="PERSISTENT", transport_priority="URGENT")), # massive
        ]
        for size, qos in configs:
            topic = Topic(id="t", name="test", size=size, qos=qos)
            weight = topic.calculate_weight()
            assert MIN_TOPIC_WEIGHT <= weight <= 1.0, f"Weight {weight} out of range for size={size}"


# =========================================================================
# Entity Tests
# =========================================================================

class TestEntities:
    """Tests for graph entity domain models."""

    def test_application_to_dict(self):
        app = Application(id="A1", name="Sensor", role=["pub"], app_type="driver", criticality="HIGH", hotstandby=True, version="1.0")
        d = app.to_dict()
        assert d["id"] == "A1"
        assert d["role"] == ["pub"]
        assert d["criticality"] == "HIGH"
        assert d["hotstandby"] is True
        assert "priority" not in d
        assert d["version"] == "1.0"

        # Boolean backward compatibility for criticality
        app_legacy = Application(id="A2", name="Sensor2", criticality=True)
        assert app_legacy.criticality == "HIGH"
        app_legacy_false = Application(id="A3", name="Sensor3", criticality=False)
        assert app_legacy_false.criticality == "LOW"

    def test_application_defaults(self):
        app = Application(id="A1", name="Service")
        assert app.role == ["Operative"]
        assert app.app_type == "service"
        assert app.criticality == "MEDIUM"
        assert app.hotstandby is False
        assert not hasattr(app, "priority")

    def test_broker_to_dict(self):
        broker = Broker(id="B1", name="DDS-0")
        d = broker.to_dict()
        assert d["id"] == "B1"
        assert d["name"] == "DDS-0"

    def test_node_to_dict(self):
        node = Node(id="N1", name="Host-0")
        d = node.to_dict()
        assert d["id"] == "N1"

    def test_library_with_version(self):
        lib = Library(id="L1", name="NavLib", version="2.1")
        d = lib.to_dict()
        assert d["version"] == "2.1"

    def test_library_without_version(self):
        lib = Library(id="L1", name="NavLib")
        d = lib.to_dict()
        assert d["version"] is None

    def test_topic_to_dict(self):
        topic = Topic(id="T1", name="/sensor/lidar", size=8192)
        d = topic.to_dict()
        assert d["size"] == 8192
        assert "qos" in d
        assert "deadline_ms" in d
        assert "history_depth" in d
        assert d["history_depth"] == 10

    def test_topic_deadline_and_history_depth(self):
        topic = Topic(id="T2", name="/control/cmd", size=512, deadline_ms=25.0, history_depth=50)
        assert topic.deadline_ms == 25.0
        assert topic.history_depth == 50
        assert topic.qos.deadline_ms == 25.0
        assert topic.qos.history_depth == 50
        d = topic.to_dict()
        assert d["deadline_ms"] == 25.0
        assert d["history_depth"] == 50


# =========================================================================
# Definition 3: Layer Projection
# =========================================================================

class TestLayerDefinitions:
    """Tests for layer projection definitions (Definition 3)."""

    def test_all_layers_defined(self):
        """All four canonical layers must be defined."""
        for layer in AnalysisLayer:
            assert layer in LAYER_DEFINITIONS
            assert layer in SIMULATION_LAYERS

    def test_app_layer_projection(self):
        """π_app: only Application and Library components, along with their dependencies."""
        defn = LAYER_DEFINITIONS[AnalysisLayer.APP]
        assert defn.component_types == frozenset({"Application", "Library"})
        assert defn.dependency_types == frozenset({"app_to_app", "app_to_lib"})
        assert defn.types_to_analyze == frozenset({"Application", "Library"})
        assert defn.quality_focus == "reliability"

    def test_infra_layer_projection(self):
        """π_infra: hosts and switches/routers, node_to_node and node_to_device dependencies."""
        defn = LAYER_DEFINITIONS[AnalysisLayer.INFRA]
        assert defn.component_types == frozenset({"Node", "NetworkDevice"})
        assert defn.dependency_types == frozenset({"node_to_node", "node_to_device"})
        assert defn.quality_focus == "availability"

    def test_mw_layer_projection(self):
        """π_mw: includes App+Broker+Node for edges, but only Broker in results."""
        defn = LAYER_DEFINITIONS[AnalysisLayer.MW]
        assert "Application" in defn.component_types
        assert "Broker" in defn.component_types
        assert "Node" in defn.component_types
        assert defn.types_to_analyze == frozenset({"Broker"})
        assert defn.dependency_types == frozenset({"app_to_broker", "node_to_broker", "broker_to_broker"})
        assert defn.quality_focus == "maintainability"

    def test_system_layer_projection(self):
        """π_system: all components, all dependency types."""
        defn = LAYER_DEFINITIONS[AnalysisLayer.SYSTEM]
        assert len(defn.component_types) == 6
        assert len(defn.dependency_types) == 7

    def test_canonical_layer_resolution(self):
        """Canonical names resolve correctly."""
        assert resolve_layer("app") == AnalysisLayer.APP
        assert resolve_layer("infra") == AnalysisLayer.INFRA
        assert resolve_layer("mw") == AnalysisLayer.MW
        assert resolve_layer("system") == AnalysisLayer.SYSTEM

    def test_legacy_alias_resolution(self):
        """Legacy aliases resolve to canonical layers."""
        assert resolve_layer("application") == AnalysisLayer.APP
        assert resolve_layer("infrastructure") == AnalysisLayer.INFRA
        assert resolve_layer("app_broker") == AnalysisLayer.MW
        assert resolve_layer("middleware") == AnalysisLayer.MW
        assert resolve_layer("complete") == AnalysisLayer.SYSTEM
        assert resolve_layer("all") == AnalysisLayer.SYSTEM

    def test_invalid_layer_raises(self):
        """Unknown layer names raise ValueError."""
        with pytest.raises(ValueError, match="Unknown layer"):
            resolve_layer("nonexistent")

    def test_dependency_to_layer_mapping(self):
        """Every DEPENDS_ON subtype maps to the correct layer."""
        assert DEPENDENCY_TO_LAYER["app_to_app"] == AnalysisLayer.APP
        assert DEPENDENCY_TO_LAYER["node_to_node"] == AnalysisLayer.INFRA
        assert DEPENDENCY_TO_LAYER["app_to_broker"] == AnalysisLayer.MW
        assert DEPENDENCY_TO_LAYER["node_to_broker"] == AnalysisLayer.MW
        assert DEPENDENCY_TO_LAYER["app_to_lib"] == AnalysisLayer.APP
        assert DEPENDENCY_TO_LAYER["broker_to_broker"] == AnalysisLayer.MW


# =========================================================================
# Simulation Layer Tests
# =========================================================================

class TestSimulationLayers:
    """Tests for simulation layer definitions (G_structural)."""

    def test_app_simulation_uses_raw_relationships(self):
        """App simulation uses PUBLISHES_TO/SUBSCRIBES_TO, not DEPENDS_ON."""
        defn = SIMULATION_LAYERS[AnalysisLayer.APP]
        assert "PUBLISHES_TO" in defn.relationships
        assert "SUBSCRIBES_TO" in defn.relationships
        assert defn.analyze_types == frozenset({"Application"})

    def test_infra_simulation_uses_physical_relationships(self):
        """Infra simulation uses RUNS_ON/CONNECTS_TO."""
        defn = SIMULATION_LAYERS[AnalysisLayer.INFRA]
        assert "RUNS_ON" in defn.relationships
        assert "CONNECTS_TO" in defn.relationships
        assert defn.analyze_types == frozenset({"Node", "NetworkDevice"})

    def test_mw_simulation_uses_routing_relationships(self):
        """MW simulation uses ROUTES + pub/sub."""
        defn = SIMULATION_LAYERS[AnalysisLayer.MW]
        assert "ROUTES" in defn.relationships
        assert defn.analyze_types == frozenset({"Broker"})

    def test_system_simulation_uses_all_relationships(self):
        """System simulation includes all 6 structural relationship types."""
        defn = SIMULATION_LAYERS[AnalysisLayer.SYSTEM]
        assert len(defn.relationships) == 6


# =========================================================================
# MIN_TOPIC_WEIGHT Constant
# =========================================================================

class TestConstants:
    """Tests for module-level constants."""

    def test_min_topic_weight_is_positive(self):
        assert MIN_TOPIC_WEIGHT > 0.0

    def test_min_topic_weight_is_small(self):
        """ε should be small enough not to distort relative rankings."""
        assert MIN_TOPIC_WEIGHT < 0.1


# =========================================================================
# Neo4j Import & Derivation Tests
# =========================================================================

from saag.infrastructure.neo4j_repo import Neo4jRepository

@pytest.fixture(scope="module")
def neo4j_repo():
    """Connect to local Neo4j for integration tests."""
    repo = Neo4jRepository(uri="bolt://localhost:7687", user="neo4j", password="password")
    try:
        repo._run_query("RETURN 1")
    except Exception:
        pytest.skip("Neo4j is not available at bolt://localhost:7687")
    yield repo
    repo.close()

@pytest.mark.integration
class TestNeo4jGraphImport:
    def test_rule_5_derivation(self, neo4j_repo):
        """Test Rule 5: app_to_lib DEPENDS_ON edges are created correctly."""
        graph_data = {
            "applications": [
                {"id": "app1", "name": "App 1", "role": ["pub"], "app_type": "service"},
                {"id": "app2", "name": "App 2", "role": ["sub"], "app_type": "service"}
            ],
            "libraries": [
                {"id": "lib1", "name": "SharedLib"}
            ],
            "relationships": {
                "uses": [
                    {"from": "app1", "to": "lib1"},
                    {"from": "app2", "to": "lib1"}
                ]
            }
        }
        neo4j_repo.save_graph(graph_data, clear=True)
        neo4j_repo.derive_dependencies()

        edges = neo4j_repo.get_graph_data(dependency_types=["app_to_lib"]).edges
        assert len(edges) == 2
        sources = {e.source_id for e in edges}
        assert sources == {"app1", "app2"}
        assert all(e.target_id == "lib1" for e in edges)

    def test_library_in_degree(self, neo4j_repo):
        """Test DG_in(Library) = 2 after import."""
        # Using the same state from test_rule_5_derivation
        with neo4j_repo.driver.session(database=neo4j_repo.database) as session:
            result = session.run(
                "MATCH ()-[d:DEPENDS_ON {dependency_type:'app_to_lib'}]->(l:Library {id: 'lib1'}) "
                "RETURN count(d) as in_degree"
            )
            in_degree = result.single()["in_degree"]
            assert in_degree == 2
            
        lib = neo4j_repo.get_graph_data(component_types=["Library"]).components[0]
        # base_w = 0.01 (min weight, no topics/apps have real weights yet)
        # dg_in = 2
        # lib.weight = 0.01 * (1 + 0.15 * log2(1 + 2)) ≈ 0.012377
        assert lib.weight == pytest.approx(0.012377, abs=0.001)

    def test_broker_hybrid_weight(self, neo4j_repo):
        """Test broker hybrid weight calculation: Generalized Power Mean (p=3).

        w(t1) = compute_topic_weight(RELIABLE+PERSISTENT+URGENT, size=0) ~= 0.7600
        w(t2) = compute_topic_weight(RELIABLE+TRANSIENT_LOCAL+LOW, size=0) ~= 0.3850
        (no explicit frequency -> TOPIC_DEFAULT_FREQUENCY_HZ fallback for both)
        """
        graph_data = {
            "brokers": [
                {"id": "b1", "name": "Broker 1"}
            ],
            "topics": [
                {
                    "id": "t1", 
                    "size": 0, 
                    "qos": {"reliability": "RELIABLE", "durability": "PERSISTENT", "transport_priority": "URGENT"}
                },
                {
                    "id": "t2", 
                    "size": 0, 
                    "qos": {"reliability": "RELIABLE", "durability": "TRANSIENT_LOCAL", "transport_priority": "LOW"}
                }
            ],
            "relationships": {
                "routes": [
                    {"from": "b1", "to": "t1"},
                    {"from": "b1", "to": "t2"}
                ]
            }
        }
        neo4j_repo.save_graph(graph_data, clear=True)
        
        broker = neo4j_repo.get_graph_data(component_types=["Broker"]).components[0]
        # Power Mean (p=3) over routed topics: ((w(t1)^3 + w(t2)^3)/2)^(1/3) ≈ 0.6283
        assert broker.weight == pytest.approx(0.6283, abs=0.01)

    def test_application_hybrid_weight(self, neo4j_repo):
        """Test application weight calculation using Generalized Power Mean (p=3)."""
        graph_data = {
            "applications": [
                {"id": "app1", "name": "App 1"}
            ],
            "topics": [
                {
                    "id": "t1", 
                    "size": 0, 
                    "qos": {"reliability": "RELIABLE", "durability": "PERSISTENT", "transport_priority": "URGENT"}
                },
                {
                    "id": "t2", 
                    "size": 0, 
                    "qos": {"reliability": "RELIABLE", "durability": "TRANSIENT_LOCAL", "transport_priority": "LOW"}
                }
            ],
            "relationships": {
                "publishes_to": [
                    {"from": "app1", "to": "t1"},
                    {"from": "app1", "to": "t2"}
                ]
            }
        }
        neo4j_repo.save_graph(graph_data, clear=True)
        
        app = neo4j_repo.get_graph_data(component_types=["Application"]).components[0]
        # Power Mean (p=3) over attached topics: ((w(t1)^3 + w(t2)^3)/2)^(1/3) ≈ 0.6283
        # (same two topic QoS profiles as test_broker_hybrid_weight above.)
        assert app.weight == pytest.approx(0.6283, abs=0.01)

