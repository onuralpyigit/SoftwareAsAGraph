"""
Graph Layer Definitions

Unified layer definitions for analysis and simulation.
This is the **canonical** layer definition used by all CLI scripts and services.

Corresponds to Layer Projections in docs/graph-model.md §5:

    π_l(G) = G_l = (V_l, E_l, τ_V|_l, τ_E|_l, w|_l)

Analysis Layers project DEPENDS_ON relationships:
    π_app    → {app_to_app, app_to_lib}                        Analyse Applications and Libraries
    π_infra  → {node_to_node, node_to_device}                  Analyse Nodes and NetworkDevices
    π_mw     → {app_to_broker, node_to_broker, broker_to_broker}  Analyse Brokers
    π_system → all seven subtypes                              Analyse all components

Scope constraint: The app layer includes app_to_lib edges so that shared-library
blast-radius risk is visible when running --layer app. Without this, a Library used by
N applications has DG_in = 0 in the app projection and R(Library) ≈ 0 regardless of
fan-out. Engineers running --layer app (not --layer system) would receive no signal about
library SPOFs. Library nodes appear in both component_types and analyze_types for the app
layer; they are not pure pub-sub participants but their failure semantics (simultaneous
multi-consumer blast) are distinct from app_to_app sequential cascades.

Simulation Layers use RAW structural relationships (PUBLISHES_TO, RUNS_ON, etc.)
for failure cascade and event propagation simulation. This implements the
G_structural vs G_analysis distinction described in docs/graph-model.md §6.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, FrozenSet, List, Any, Optional


# ---------------------------------------------------------------------------
# Unified Layer Enum
# ---------------------------------------------------------------------------

class AnalysisLayer(Enum):
    """
    Enumeration of graph layers for both analysis and simulation.

    Each layer represents a specific architectural view:
        APP    — Service-level dependencies  (π_app)
        INFRA  — Infrastructure topology     (π_infra)
        MW     — Middleware/broker coupling   (π_mw)
        SYSTEM — Complete system view         (π_system)
    """
    APP = "app"
    INFRA = "infra"
    MW = "mw"
    SYSTEM = "system"

    @classmethod
    def from_string(cls, value: str) -> AnalysisLayer:
        """
        Convert a string to AnalysisLayer, supporting common aliases.
        
        Canonical names: app, infra, mw, system
        Legacy aliases:  application, infrastructure, app_broker, middleware,
                         broker, complete, all
        """
        _ALIASES: Dict[str, AnalysisLayer] = {
            # Legacy aliases (used in older code / neo4j_repo)
            "application": cls.APP,
            "infrastructure": cls.INFRA,
            "app_broker": cls.MW,
            "middleware": cls.MW,
            "mw-app": cls.MW,
            "mw-infra": cls.MW,
            "broker": cls.MW,
            "brokers": cls.MW,
            "complete": cls.SYSTEM,
            "all": cls.SYSTEM,
        }
        key = value.lower().strip()
        if key in _ALIASES:
            return _ALIASES[key]
        try:
            return cls(key)
        except ValueError:
            valid = sorted({l.value for l in cls} | set(_ALIASES))
            raise ValueError(f"Unknown layer '{value}'. Valid: {valid}")


# Backward compatibility alias
SimulationLayer = AnalysisLayer


# ---------------------------------------------------------------------------
# Analysis Layer Definition
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LayerDefinition:
    """
    Definition of a graph analysis layer (π_l).
    
    Maps to the layer projection table in docs/graph-model.md §5:
        T_l  → component_types   (vertex types included in the projection)
        T_a  → analyze_types     (vertex types that appear in analysis results)
        D_l  → dependency_types  (DEPENDS_ON subtypes included in projection)
    
    Note: For the middleware layer, T_l includes Application and Node vertices
    to preserve incoming edges to Broker vertices, but only Broker components
    appear in analysis results (T_a = {Broker}).
    
    Attributes:
        name:             Human-readable layer name
        description:      Layer purpose and analysis strategy
        component_types:  T_l — vertex labels included when building subgraph
        dependency_types: D_l — DEPENDS_ON subtypes included
        focus_metrics:    Key topological metrics for this layer
        quality_focus:    Primary RM quality dimension
        analyze_types:    T_a — component types in analysis results.
                          Defaults to *component_types* when ``None``.
    """
    name: str
    description: str
    component_types: FrozenSet[str]
    dependency_types: FrozenSet[str]
    focus_metrics: tuple
    quality_focus: str
    analyze_types: Optional[FrozenSet[str]] = None

    @property
    def types_to_analyze(self) -> FrozenSet[str]:
        """Component types that should appear in analysis results (T_a)."""
        return self.analyze_types if self.analyze_types else self.component_types

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "component_types": sorted(self.component_types),
            "analyze_types": sorted(self.types_to_analyze),
            "dependency_types": sorted(self.dependency_types),
            "focus_metrics": list(self.focus_metrics),
            "quality_focus": self.quality_focus,
        }


# ---------------------------------------------------------------------------
# Analysis Layer Registry
# ---------------------------------------------------------------------------

LAYER_DEFINITIONS: Dict[AnalysisLayer, LayerDefinition] = {
    AnalysisLayer.APP: LayerDefinition(
        name="Application Layer",
        description=(
            "π_app: Analyse Applications and Libraries via app_to_app and app_to_lib "
            "dependencies for reliability. Library nodes are included so that shared-library "
            "blast-radius risk (simultaneous multi-consumer failure) is visible at this layer. "
            "Scope constraint: app_to_lib blast semantics differ from app_to_app sequential "
            "cascades — Library RM scores reflect fan-out severity, not propagation depth."
        ),
        component_types=frozenset({"Application", "Library"}),
        dependency_types=frozenset({"app_to_app", "app_to_lib"}),
        focus_metrics=("pagerank", "reverse_pagerank", "in_degree", "betweenness"),
        quality_focus="reliability",
        analyze_types=frozenset({"Application", "Library"}),
    ),
    AnalysisLayer.INFRA: LayerDefinition(
        name="Infrastructure Layer",
        description=(
            "π_infra: Analyse Nodes via node_to_node dependencies, and the switches/routers "
            "between them via node_to_device dependencies, for availability"
        ),
        component_types=frozenset({"Node", "NetworkDevice"}),
        dependency_types=frozenset({"node_to_node", "node_to_device"}),
        focus_metrics=("betweenness", "clustering", "articulation_point", "bridges"),
        quality_focus="availability",
    ),
    AnalysisLayer.MW: LayerDefinition(
        name="Middleware Layer",
        description=(
            "π_mw: Analyse Brokers via app_to_broker, node_to_broker, and broker_to_broker "
            "dependencies for maintainability. Includes Application and Node vertices in the "
            "subgraph to preserve incoming edges, but only Broker components appear in results. "
            "broker_to_broker edges capture colocation risk: two brokers sharing a physical node "
            "have symmetric shared-fate dependency; both get elevated DG_in."
        ),
        component_types=frozenset({"Application", "Broker", "Node"}),
        dependency_types=frozenset({"app_to_broker", "node_to_broker", "broker_to_broker"}),
        focus_metrics=("in_degree", "pagerank", "betweenness", "clustering"),
        quality_focus="maintainability",
        analyze_types=frozenset({"Broker"}),
    ),
    AnalysisLayer.SYSTEM: LayerDefinition(
        name="Complete System",
        description="π_system: Analyse all components across all seven dependency subtypes",
        component_types=frozenset({"Application", "Broker", "Node", "Topic", "Library", "NetworkDevice"}),
        dependency_types=frozenset({"app_to_app", "app_to_lib", "app_to_broker", "node_to_node", "node_to_broker", "broker_to_broker", "node_to_device"}),
        focus_metrics=("pagerank", "betweenness", "articulation_point", "clustering"),
        quality_focus="overall",
    ),
}

# Backward compatibility alias
LAYERS = LAYER_DEFINITIONS


# ---------------------------------------------------------------------------
# Simulation Layer Definition
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SimulationLayerDefinition:
    """
    Definition of a simulation layer using raw structural relationships.
    
    Unlike analysis layers which use derived DEPENDS_ON edges (G_analysis),
    simulation layers operate on the full structural graph (G_structural)
    with raw relationships like PUBLISHES_TO, RUNS_ON, etc. This enables
    realistic failure cascade simulation through actual communication paths.
    
    See docs/graph-model.md §6 for the G_structural vs G_analysis distinction.
    
    Attributes:
        name:             Human-readable layer name
        description:      What this layer simulates
        component_types:  Vertex labels included when building the simulation graph
        analyze_types:    Component types to analyze in simulation results
        relationships:    Raw relationship types (PUBLISHES_TO, RUNS_ON, etc.)
        cascade_rules:    Types of cascade propagation (physical, logical, network)
        focus_metrics:    Key simulation metrics for this layer
    """
    name: str
    description: str
    component_types: FrozenSet[str]
    analyze_types: FrozenSet[str]
    relationships: FrozenSet[str]
    cascade_rules: FrozenSet[str]
    focus_metrics: tuple

    @property
    def types_to_analyze(self) -> FrozenSet[str]:
        """Component types that should appear in simulation results."""
        return self.analyze_types

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "component_types": sorted(self.component_types),
            "analyze_types": sorted(self.analyze_types),
            "relationships": sorted(self.relationships),
            "cascade_rules": sorted(self.cascade_rules),
            "focus_metrics": list(self.focus_metrics),
        }


# ---------------------------------------------------------------------------
# Simulation Layer Registry
# ---------------------------------------------------------------------------

SIMULATION_LAYERS: Dict[AnalysisLayer, SimulationLayerDefinition] = {
    AnalysisLayer.APP: SimulationLayerDefinition(
        name="Application Layer",
        description="Simulate applications via PUBLISHES_TO/SUBSCRIBES_TO/USES relationships",
        component_types=frozenset({"Application", "Topic", "Library"}),
        analyze_types=frozenset({"Application"}),
        relationships=frozenset({"PUBLISHES_TO", "SUBSCRIBES_TO", "USES"}),
        cascade_rules=frozenset({"logical"}),
        focus_metrics=("throughput", "delivery_rate", "latency", "message_drops"),
    ),
    AnalysisLayer.INFRA: SimulationLayerDefinition(
        name="Infrastructure Layer",
        description="Simulate nodes and switches/routers via RUNS_ON/CONNECTS_TO relationships",
        component_types=frozenset({"Node", "NetworkDevice", "Application", "Broker"}),
        analyze_types=frozenset({"Node", "NetworkDevice"}),
        relationships=frozenset({"RUNS_ON", "CONNECTS_TO"}),
        cascade_rules=frozenset({"physical", "network"}),
        focus_metrics=("reachability_loss", "fragmentation", "cascade_depth"),
    ),
    AnalysisLayer.MW: SimulationLayerDefinition(
        name="Middleware Layer",
        description="Simulate brokers via ROUTES/PUBLISHES_TO/SUBSCRIBES_TO relationships",
        component_types=frozenset({"Broker", "Topic", "Application"}),
        analyze_types=frozenset({"Broker"}),
        relationships=frozenset({"ROUTES", "PUBLISHES_TO", "SUBSCRIBES_TO"}),
        cascade_rules=frozenset({"logical"}),
        focus_metrics=("throughput", "routing_efficiency", "message_drops"),
    ),
    AnalysisLayer.SYSTEM: SimulationLayerDefinition(
        name="Complete System",
        description="Simulate all components across all relationship types",
        component_types=frozenset({"Application", "Broker", "Node", "Topic", "Library", "NetworkDevice"}),
        analyze_types=frozenset({"Application", "Broker", "Node", "NetworkDevice"}),
        relationships=frozenset({"PUBLISHES_TO", "SUBSCRIBES_TO", "ROUTES", "RUNS_ON", "CONNECTS_TO", "USES"}),
        cascade_rules=frozenset({"physical", "logical", "network"}),
        focus_metrics=("throughput", "reachability_loss", "fragmentation", "composite_impact"),
    ),
}


# ---------------------------------------------------------------------------
# Dependency-to-Layer Mapping
# ---------------------------------------------------------------------------

#: Maps each DEPENDS_ON subtype to its primary analysis layer.
#: Used by validators and reporters to associate dependencies with layers.
DEPENDENCY_TO_LAYER: Dict[str, AnalysisLayer] = {
    "app_to_app": AnalysisLayer.APP,
    "app_to_lib": AnalysisLayer.APP,
    "node_to_node": AnalysisLayer.INFRA,
    "node_to_device": AnalysisLayer.INFRA,
    "app_to_broker": AnalysisLayer.MW,
    "node_to_broker": AnalysisLayer.MW,
    "broker_to_broker": AnalysisLayer.MW,
}


# ---------------------------------------------------------------------------
# Analysis Layer Helpers
# ---------------------------------------------------------------------------

def get_layer_definition(layer: AnalysisLayer) -> LayerDefinition:
    """Get the analysis definition for a specific layer."""
    return LAYER_DEFINITIONS[layer]


# Backward compatibility alias
get_layer = get_layer_definition


def get_all_layers() -> List[AnalysisLayer]:
    """All layers (including system)."""
    return list(AnalysisLayer)


def get_primary_layers() -> List[AnalysisLayer]:
    """The four primary layers analysed when no explicit layer list is given."""
    return [AnalysisLayer.APP, AnalysisLayer.INFRA, AnalysisLayer.MW, AnalysisLayer.SYSTEM]


def list_layers() -> str:
    """Return a formatted string describing all available layers."""
    lines = ["Available analysis layers:", ""]
    for layer in AnalysisLayer:
        defn = LAYER_DEFINITIONS[layer]
        deps = ", ".join(sorted(defn.dependency_types))
        analyze = ", ".join(sorted(defn.types_to_analyze))
        lines.extend([
            f"  {layer.value:8} — {defn.name}",
            f"             Analyses:     {analyze}",
            f"             Dependencies: {deps}",
            f"             Focus:        {defn.quality_focus}",
            "",
        ])
    return "\n".join(lines)


def resolve_layer(value: str) -> AnalysisLayer:
    """Resolve a string to an AnalysisLayer (supports canonical names and aliases)."""
    return AnalysisLayer.from_string(value)


# ---------------------------------------------------------------------------
# Simulation Layer Helpers
# ---------------------------------------------------------------------------

def get_simulation_layer_definition(layer: AnalysisLayer) -> SimulationLayerDefinition:
    """Get the simulation definition for a specific layer."""
    return SIMULATION_LAYERS[layer]


def get_simulation_layers() -> List[AnalysisLayer]:
    """Get all available simulation layers."""
    return list(SIMULATION_LAYERS.keys())