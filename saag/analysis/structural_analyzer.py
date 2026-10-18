"""
Structural Analyzer

Computes raw topological metrics from graph data using NetworkX.
Supports multi-layer analysis by filtering based on DEPENDS_ON relationship types.

Metrics computed per component:
    Centrality : PageRank, Reverse PageRank, Betweenness, Harmonic Closeness,
                 Eigenvector (with Katz fallback)
    Degree     : In-degree, Out-degree, Total degree (raw + normalized)
    Resilience : Clustering coefficient, Articulation point flag,
                 Bridge count, Bridge ratio
    Weights    : Component weight, Dependency weight in/out
    Pub-Sub    : pubsub_degree, pubsub_betweenness, broker_exposure
                 (derived from raw PUBLISHES_TO/SUBSCRIBES_TO/ROUTES edges)

Metrics computed per edge:
    Edge betweenness (with inverted weights), Bridge flag

Metrics computed per graph:
    Node/edge count, Density, Connected components, Diameter, Avg path length,
    Assortativity, Avg clustering, AP count, Bridge count

Layer filtering:
    Each AnalysisLayer defines which dependency_types and component_types to
    include, so the same analyzer can serve app, infra, mw, or system views.

Weight semantics:
    DEPENDS_ON edge weights represent dependency strength (importance).
    - PageRank, Eigenvector/Katz: weights used as-is (importance)
    - Betweenness, Edge Betweenness: weights inverted (1/w) for distance semantics
    - Harmonic Closeness: no weights (topological proximity)

Usage:
    analyzer = StructuralAnalyzer()
    result = analyzer.analyze(graph_data, layer=AnalysisLayer.APP)
"""

from __future__ import annotations

import logging
import math
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Any, Optional, Set, Tuple

import networkx as nx
from networkx.utils import reverse_cuthill_mckee_ordering

from saag.core.layers import (
    AnalysisLayer,
    LAYER_DEFINITIONS,
    get_layer_definition,
)
from saag.core.metrics import StructuralMetrics, EdgeMetrics, GraphSummary
from saag.core.models import QoSPolicy

from .graph_ops import (
    articulation_points_disconnected,
    bridges_disconnected,
    build_distance_graph,
)


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

from .models import StructuralAnalysisResult


# ---------------------------------------------------------------------------
# Tuning constants
# ---------------------------------------------------------------------------

#: CQP v7 weights — (loc, cyclomatic complexity, instability, LCOM).
_CQP_WEIGHTS = (0.10, 0.35, 0.30, 0.25)

#: Subscriber count at which publisher-SPOF severity saturates.
_PSPOF_SUB_SATURATION = 5.0


#: Phase-time collector, active only inside :func:`record_phases`.
_PHASE_SINK: Optional[Dict[str, float]] = None


@contextmanager
def record_phases() -> Iterator[Dict[str, float]]:
    """Accumulate each phase's wall-clock seconds into the yielded dict (cost reconciliation)."""
    global _PHASE_SINK
    previous, _PHASE_SINK = _PHASE_SINK, {}
    try:
        yield _PHASE_SINK
    finally:
        _PHASE_SINK = previous


@contextmanager
def _phase(logger: logging.Logger, label: str) -> Iterator[None]:
    """Log the start and wall-clock duration of one analysis phase."""
    t0 = time.perf_counter()
    logger.info("  %s…", label)
    yield
    dt = time.perf_counter() - t0
    if _PHASE_SINK is not None:
        _PHASE_SINK[label] = _PHASE_SINK.get(label, 0.0) + dt
    logger.info("  %s done (%.2fs)", label, dt)


# ---------------------------------------------------------------------------
# Layer subgraph extraction (pure function)
# ---------------------------------------------------------------------------

def extract_layer_subgraph(
    graph_data: Any,
    layer: AnalysisLayer,
) -> nx.DiGraph:
    """
    Build a NetworkX DiGraph containing only the components and DEPENDS_ON
    edges relevant to *layer*.

    The graph_data object is expected to provide:
      - .components  : iterable of component data objects with .id, .component_type
      - .edges       : iterable of edge data objects with .source_id, .target_id,
                       .dependency_type, .weight (DEPENDS_ON edges)

    Returns an empty DiGraph when no matching data is found.
    """
    defn = get_layer_definition(layer)
    G = nx.DiGraph()

    # Collect allowed component IDs
    allowed_ids: Set[str] = set()
    for comp in graph_data.components:
        if comp.component_type in defn.component_types:
            allowed_ids.add(comp.id)
            props = getattr(comp, "properties", {}).copy()
            # Extract name from properties (fallback to id if not present)
            name = props.pop("name", comp.id)
            # Extract code-quality fields from props (written there by get_graph_data)
            code_qual = {
                "loc": props.pop("loc", 0),
                "cyclomatic_complexity": props.pop("cyclomatic_complexity", 0.0),
                "coupling_afferent": props.pop("coupling_afferent", 0),
                "coupling_efferent": props.pop("coupling_efferent", 0),
                "lcom": props.pop("lcom", 0.0),
            }
            # Extract subscriber_count for Topic nodes
            subscriber_count = 0
            if comp.component_type == "Topic":
                subscriber_count = props.pop("subscriber_count", 0)

            G.add_node(
                comp.id,
                component_type=comp.component_type,
                name=name,
                weight=getattr(comp, "weight", 1.0),
                subscriber_count=subscriber_count,
                **code_qual,
                **props,
            )

    # Add DEPENDS_ON edges whose dependency_type matches the layer
    for edge in graph_data.edges:
        if edge.dependency_type not in defn.dependency_types:
            continue
        if edge.source_id not in allowed_ids or edge.target_id not in allowed_ids:
            continue
        G.add_edge(
            edge.source_id,
            edge.target_id,
            dependency_type=edge.dependency_type,
            weight=getattr(edge, "weight", 1.0),
            path_count=getattr(edge, "path_count", 1),
        )

    return G


# ---------------------------------------------------------------------------
# Phase results
#
# Each dataclass below is the output of one phase of StructuralAnalyzer.analyze.
# They exist to keep the orchestrator readable — the fields are unpacked into
# StructuralMetrics by _build_component_metrics.
# ---------------------------------------------------------------------------

@dataclass
class _Centrality:
    """Phase 2 — centrality measures on G, G^T and the inverted-weight G_dist."""
    pagerank: Dict[str, float]
    reverse_pagerank: Dict[str, float]
    betweenness: Dict[str, float]
    closeness: Dict[str, float]
    eigenvector: Dict[str, float]
    edge_betweenness: Dict[Tuple[str, str], float]


@dataclass
class _Coupling:
    """Phase 3 — path-count and fan-out derived metrics."""
    mpci: Dict[str, float] = field(default_factory=dict)
    path_complexity: Dict[str, float] = field(default_factory=dict)
    fan_out_criticality: Dict[str, float] = field(default_factory=dict)
    topic_frequency_hz: Dict[str, float] = field(default_factory=dict)


@dataclass
class _Resilience:
    """Phase 5 — undirected resilience structure (clustering, APs, bridges)."""
    undirected: nx.Graph
    clustering: Dict[str, float]
    articulation_points: Set[str]
    directed_aps: Set[str]
    bridges: Set[Tuple[str, str]]
    bridge_count: Dict[str, int]


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------

class StructuralAnalyzer:
    """
    Analyses graph structure to compute topological metrics.

    Supports multi-layer analysis by filtering components and edges
    based on the specified AnalysisLayer's definition.
    """

    def __init__(self, damping_factor: float = 0.85) -> None:
        self.damping_factor = damping_factor
        self._logger = logging.getLogger(__name__)

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def analyze(
        self,
        graph_data: Any,
        layer: AnalysisLayer = AnalysisLayer.SYSTEM,
    ) -> StructuralAnalysisResult:
        """
        Compute structural metrics for *graph_data* filtered by *layer*.

        Phases (each delegates to the like-named private method):
            1. extract_layer_subgraph  — layer-filtered DiGraph G, plus G^T and G_dist
            2. _compute_centrality     — PageRank/RPR, betweenness, closeness, eigenvector
            3. _compute_coupling       — MPCI, path complexity, fan-out criticality
            4. _compute_reachability   — blast radius, cascade depth, AP_c/CDI scores
            5. _compute_resilience     — clustering, articulation points, bridges
            6. _compute_pubsub_metrics — bipartite app↔topic metrics + QoS profile
            7. _build_component_metrics / _build_edge_metrics / _build_summary
        """
        defn = get_layer_definition(layer)

        with _phase(self._logger, f"1. Building layer subgraph for '{layer.value}'"):
            G = extract_layer_subgraph(graph_data, layer)

        if G.number_of_nodes() == 0:
            return self._empty_result(layer)

        self._logger.info(
            "     Subgraph: %d nodes, %d edges", G.number_of_nodes(), G.number_of_edges()
        )

        # Filter components to analyse (e.g. MW only reports Brokers)
        analyze_types = defn.types_to_analyze
        n_nodes = G.number_of_nodes()

        # G^T is the failure-propagation direction; G_dist inverts weights so
        # distance-based algorithms treat strong dependencies as "close".
        G_rev = G.reverse()
        G_dist = build_distance_graph(G)

        with _phase(self._logger, "2. Computing centrality metrics"):
            centrality = self._compute_centrality(G, G_rev, G_dist, n_nodes)

        with _phase(self._logger, "3. Computing MPCI / fan-out criticality / path complexity"):
            coupling = self._compute_coupling(G, n_nodes)

        with _phase(self._logger, "4. Computing AP_c / CDI, blast radius, cascade depth"):
            ap_scores = self._compute_continuous_ap_scores(G)
            blast_radius, cascade_depth = self._compute_reachability(G)

        with _phase(self._logger, "5. Computing clustering coefficients and bridges"):
            resilience = self._compute_resilience(G)
        self._logger.info(
            "     %d articulation points, %d bridges",
            len(resilience.articulation_points), len(resilience.bridges),
        )

        with _phase(self._logger, "6. Computing pub-sub topology metrics"):
            pubsub_metrics = self._compute_pubsub_metrics(graph_data, set(G.nodes))
            qos_profile = self._collect_qos_profile(graph_data)

        with _phase(self._logger, "7. Assembling component and edge metrics"):
            components = self._build_component_metrics(
                G, analyze_types, n_nodes,
                centrality, coupling, resilience,
                ap_scores, blast_radius, cascade_depth, pubsub_metrics,
            )
            edge_metrics = self._build_edge_metrics(
                G, analyze_types, centrality.edge_betweenness, resilience.bridges,
            )
            rcm_order = self._rcm_order(resilience.undirected, G)
            summary = self._build_summary(
                G, layer, resilience.articulation_points, resilience.bridges,
            )
        self._logger.info("     %d components, %d edges reported", len(components), len(edge_metrics))

        return StructuralAnalysisResult(
            layer=layer,
            components=components,
            edges=edge_metrics,
            graph_summary=summary,
            graph=G,
            qos_profile=qos_profile,
            rcm_order=rcm_order,
        )

    # ------------------------------------------------------------------
    # Analysis phases
    # ------------------------------------------------------------------

    def _compute_centrality(
        self,
        G: nx.DiGraph,
        G_rev: nx.DiGraph,
        G_dist: nx.DiGraph,
        n_nodes: int,
    ) -> _Centrality:
        """
        Centrality measures with layer-appropriate weight semantics.

        PageRank and eigenvector use DEPENDS_ON weights directly (importance);
        betweenness uses G_dist (inverted weights, distance semantics); harmonic
        closeness is unweighted and normalised by (n-1).
        """
        closeness = nx.harmonic_centrality(G)
        if n_nodes > 1:
            closeness = {v: c / (n_nodes - 1) for v, c in closeness.items()}

        return _Centrality(
            pagerank=nx.pagerank(G, alpha=self.damping_factor, weight="weight"),
            reverse_pagerank=nx.pagerank(G_rev, alpha=self.damping_factor, weight="weight"),
            betweenness=nx.betweenness_centrality(G_dist, weight="weight", normalized=True),
            closeness=closeness,
            eigenvector=self._safe_eigenvector(G),
            edge_betweenness=nx.edge_betweenness_centrality(
                G_dist, weight="weight", normalized=True
            ),
        )

    @staticmethod
    def _compute_coupling(G: nx.DiGraph, n_nodes: int) -> _Coupling:
        """
        Path-count derived coupling metrics plus Topic fan-out criticality.

            MPCI(v)            = Σ_{e ∈ InEdges(v)} max(path_count(e) − 1, 0) / (|V| − 1)
            path_complexity(v) = mean(log2(1 + path_count(e))) over OutEdges(v)
            FOC(t)             = log1p(f(t)) · s(t) / max_{t'} [log1p(f(t')) · s(t')]

        FOC rationale: impact scales with message rate, not just structural
        reachability. log1p compresses the 0.001–10000 Hz span to ~0–9.2 while
        preserving strict monotonicity (log1p(0) == 0, so zero-Hz topics
        contribute zero regardless of subscriber count, as intended). When all
        topics share one frequency the ranking matches the structural baseline,
        because the shared log1p(f) factor cancels in normalisation.

        Q/I independence: frequency is a design-time scenario attribute, not a
        post-deployment measurement, so its presence in Q(v) does not violate
        the pre-deployment independence criterion.
        """
        result = _Coupling()

        for nid in G.nodes:
            raw_mpci = sum(
                max(data.get("path_count", 1) - 1, 0)
                for _u, _v, data in G.in_edges(nid, data=True)
            )
            result.mpci[nid] = raw_mpci / (n_nodes - 1) if n_nodes > 1 else 0.0

            out_edges = G.out_edges(nid, data=True)
            if out_edges:
                log_sum = sum(
                    math.log2(1 + data.get("path_count", 1)) for _, _, data in out_edges
                )
                result.path_complexity[nid] = log_sum / len(out_edges)
            else:
                result.path_complexity[nid] = 0.0

        topic_nodes = [n for n, d in G.nodes(data=True) if d.get("component_type") == "Topic"]
        if not topic_nodes:
            # FOC stays 0.0 for non-Topic nodes via the StructuralMetrics default
            return result

        raw_foc: Dict[str, float] = {}
        for n in topic_nodes:
            sub = G.nodes[n].get("subscriber_count", 0)
            # Accept both canonical frequency keys for backward compatibility
            hz = float(
                G.nodes[n].get("frequency", G.nodes[n].get("topic_frequency", 0.0)) or 0.0
            )
            result.topic_frequency_hz[n] = hz
            raw_foc[n] = math.log1p(hz) * sub

        max_foc = max(raw_foc.values(), default=1.0) or 1.0
        for n in topic_nodes:
            result.fan_out_criticality[n] = raw_foc[n] / max_foc

        return result

    @staticmethod
    def _compute_reachability(G: nx.DiGraph) -> Tuple[Dict[str, int], Dict[str, int]]:
        """
        Per-node descendant count (blast radius) and longest downstream path
        (cascade depth), both measured on the ego-subgraph rooted at the node.
        """
        blast_radius: Dict[str, int] = {}
        cascade_depth: Dict[str, int] = {}

        for nid in G.nodes:
            desc = nx.descendants(G, nid)
            blast_radius[nid] = len(desc)

            ego_G = G.subgraph(desc | {nid})
            try:
                cascade_depth[nid] = nx.dag_longest_path_length(ego_G)
            except nx.NetworkXUnfeasible:
                # Cycles present — fall back to max BFS depth from the root
                depths = nx.single_source_shortest_path_length(ego_G, nid)
                cascade_depth[nid] = max(depths.values()) if depths else 0

        return blast_radius, cascade_depth

    def _compute_resilience(self, G: nx.DiGraph) -> _Resilience:
        """
        Undirected resilience structure: clustering, articulation points and
        bridges (per connected component when the graph is disconnected), plus
        directed articulation points computed on G itself.
        """
        U = G.to_undirected()
        connected = nx.is_connected(U)

        art_points = (
            set(nx.articulation_points(U)) if connected
            else articulation_points_disconnected(U)
        )
        bridges = set(nx.bridges(U)) if connected else bridges_disconnected(U)

        bridge_count: Dict[str, int] = {}
        for (u, v) in bridges:
            bridge_count[u] = bridge_count.get(u, 0) + 1
            bridge_count[v] = bridge_count.get(v, 0) + 1

        return _Resilience(
            undirected=U,
            clustering=nx.clustering(U),
            articulation_points=art_points,
            directed_aps=self._compute_directed_articulation_points(G),
            bridges=bridges,
            bridge_count=bridge_count,
        )

    def _build_component_metrics(
        self,
        G: nx.DiGraph,
        analyze_types: Any,
        n_nodes: int,
        centrality: _Centrality,
        coupling: _Coupling,
        resilience: _Resilience,
        ap_scores: Dict[str, Dict[str, float]],
        blast_radius: Dict[str, int],
        cascade_depth: Dict[str, int],
        pubsub_metrics: Dict[str, Dict[str, float]],
    ) -> Dict[str, StructuralMetrics]:
        """Assemble StructuralMetrics per analysed node, then normalise code quality."""
        in_deg = dict(G.in_degree())
        out_deg = dict(G.out_degree())

        dep_weight_in: Dict[str, float] = {nid: 0.0 for nid in G.nodes}
        dep_weight_out: Dict[str, float] = {nid: 0.0 for nid in G.nodes}
        for u, v, data in G.edges(data=True):
            w = data.get("weight", 1.0)
            dep_weight_out[u] += w
            dep_weight_in[v] += w

        components: Dict[str, StructuralMetrics] = {}
        for nid in G.nodes:
            ntype = G.nodes[nid].get("component_type", "Unknown")
            if ntype not in analyze_types:
                continue

            raw_in = in_deg.get(nid, 0)
            raw_out = out_deg.get(nid, 0)
            total_raw = raw_in + raw_out
            bc = resilience.bridge_count.get(nid, 0)
            ps = pubsub_metrics.get(nid, {})

            # Raw code-quality values (0.0 for non-qualifying node types)
            # loc_norm / complexity_norm / lcom_norm store the RAW values here;
            # the post-loop normalization pass will overwrite them with [0,1] values.
            # Both Application and Library nodes carry code-quality attributes;
            # all other types (Broker, Node, Topic) get zeroed fields.
            is_cq_node = ntype in ("Application", "Library")
            raw_loc  = G.nodes[nid].get("loc", 0) if is_cq_node else 0
            raw_cc   = G.nodes[nid].get("cyclomatic_complexity", 0.0) if is_cq_node else 0.0
            raw_lcom = G.nodes[nid].get("lcom", 0.0) if is_cq_node else 0.0
            raw_ca   = G.nodes[nid].get("coupling_afferent", 0)
            raw_ce   = G.nodes[nid].get("coupling_efferent", 0)
            instability_val = raw_ce / max(raw_ca + raw_ce, 1) if is_cq_node and (raw_ca + raw_ce) > 0 else 0.0

            components[nid] = StructuralMetrics(
                id=nid,
                name=G.nodes[nid].get("name", nid),
                type=ntype,
                # Centrality
                pagerank=centrality.pagerank.get(nid, 0.0),
                reverse_pagerank=centrality.reverse_pagerank.get(nid, 0.0),
                betweenness=centrality.betweenness.get(nid, 0.0),
                closeness=centrality.closeness.get(nid, 0.0),
                eigenvector=centrality.eigenvector.get(nid, 0.0),
                # Degree (normalized)
                degree=total_raw / (2 * (n_nodes - 1)) if n_nodes > 1 else 0.0,
                in_degree=raw_in / (n_nodes - 1) if n_nodes > 1 else 0.0,
                out_degree=raw_out / (n_nodes - 1) if n_nodes > 1 else 0.0,
                # Degree (raw)
                in_degree_raw=raw_in,
                out_degree_raw=raw_out,
                # Resilience
                clustering_coefficient=resilience.clustering.get(nid, 0.0),
                is_articulation_point=nid in resilience.articulation_points,
                is_directed_ap=nid in resilience.directed_aps,
                ap_c_directed=ap_scores.get(nid, {}).get("ap_c_dir", 0.0),
                cdi=ap_scores.get(nid, {}).get("cdi", 0.0),
                blast_radius=blast_radius.get(nid, 0),
                cascade_depth=cascade_depth.get(nid, 0),
                is_isolated=(raw_in + raw_out) == 0,
                bridge_count=bc,
                bridge_ratio=bc / total_raw if total_raw > 0 else 0.0,
                # Pub-sub topology
                pubsub_degree=ps.get("pubsub_degree", 0.0),
                pubsub_betweenness=ps.get("pubsub_betweenness", 0.0),
                broker_exposure=ps.get("broker_exposure", 0.0),
                publisher_spof=ps.get("publisher_spof", 0.0),
                fan_out_criticality=coupling.fan_out_criticality.get(nid, 0.0),
                topic_frequency_hz=coupling.topic_frequency_hz.get(nid, 0.0),
                mpci=coupling.mpci.get(nid, 0.0),
                path_complexity=coupling.path_complexity.get(nid, 0.0),
                topic_subscriber_count=int(ps.get("topic_subscriber_count", 0)),
                topic_publisher_count=int(ps.get("topic_publisher_count", 0)),
                # Infrastructure Metrics
                ip_address=G.nodes[nid].get("ip_address", ""),
                cpu_cores=G.nodes[nid].get("cpu_cores", 0),
                memory_gb=G.nodes[nid].get("memory_gb", 0),
                os_type=G.nodes[nid].get("os_type", ""),
                broker_type=G.nodes[nid].get("type", ""),
                max_connections=G.nodes[nid].get("max_connections", 0),
                host=G.nodes[nid].get("host", ""),
                max_link_utilization=G.nodes[nid].get("max_link_utilization", 0.0),
                link_load_mbps=G.nodes[nid].get("link_load_mbps", 0.0),
                # Code quality — raw stored temporarily; post-loop normalisation fills them
                # loc_norm / complexity_norm / lcom_norm hold RAW values until the pass
                loc_norm=float(raw_loc),
                complexity_norm=raw_cc,
                lcom_norm=raw_lcom,
                instability_code=instability_val,
                code_quality_penalty=0.0,  # filled by _compute_code_quality_metrics
                # SonarQube / Quality Metrics
                sqale_debt_ratio=float(G.nodes[nid].get("sqale_debt_ratio", 0.0)),
                bugs=int(G.nodes[nid].get("bugs", 0)),
                vulnerabilities=int(G.nodes[nid].get("vulnerabilities", 0)),
                duplicated_lines_density=float(G.nodes[nid].get("duplicated_lines_density", 0.0)),
                # Weights
                weight=G.nodes[nid].get("weight", 1.0),
                dependency_weight_in=dep_weight_in.get(nid, 0.0),
                dependency_weight_out=dep_weight_out.get(nid, 0.0),
            )

        # Min-max scales loc_norm / complexity_norm / lcom_norm across each type's
        # population, then fills code_quality_penalty.
        self._compute_code_quality_metrics(components)
        return components

    @staticmethod
    def _build_edge_metrics(
        G: nx.DiGraph,
        analyze_types: Any,
        edge_betweenness: Dict[Tuple[str, str], float],
        bridges: Set[Tuple[str, str]],
    ) -> Dict[Tuple[str, str], EdgeMetrics]:
        """Assemble EdgeMetrics for edges with at least one analysed endpoint."""
        edge_metrics: Dict[Tuple[str, str], EdgeMetrics] = {}
        for (u, v), data in G.edges.items():
            u_type = G.nodes[u].get("component_type")
            v_type = G.nodes[v].get("component_type")
            if u_type not in analyze_types and v_type not in analyze_types:
                continue
            edge_metrics[(u, v)] = EdgeMetrics(
                source=u,
                target=v,
                source_type=u_type,
                target_type=v_type,
                dependency_type=data.get("dependency_type", "unknown"),
                weight=data.get("weight", 1.0),
                betweenness=edge_betweenness.get((u, v), 0.0),
                is_bridge=(u, v) in bridges or (v, u) in bridges,
            )
        return edge_metrics

    def _rcm_order(self, U: nx.Graph, G: nx.DiGraph) -> List[str]:
        """
        Reverse Cuthill-McKee node ordering (bandwidth minimisation).

        Used to reorder the dependency matrix so structural clusters surface.
        Falls back to insertion order if the ordering cannot be computed.
        """
        try:
            return list(reverse_cuthill_mckee_ordering(U))
        except Exception as e:
            self._logger.warning("RCM ordering failed: %s", e)
            return list(G.nodes)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_code_quality_metrics(
        components: Dict[str, "StructuralMetrics"],
    ) -> None:
        """
        Normalise code-quality fields in-place for Application and Library nodes.

        Application and Library nodes are normalised **independently** (separate
        population min-max passes) because their typical LOC / CC scales differ
        significantly.  Both use the same CQP formula:

            CQP = 0.10 * loc_norm + 0.35 * complexity_norm + 0.30 * instability_code + 0.25 * lcom_norm

        instability_code is already in [0,1] (Ce/(Ca+Ce)) — not re-normalised.

        All other component types (Broker, Node, Topic) are skipped; their
        code_quality_penalty stays 0.0.
        """
        W_LOC, W_CC, W_INS, W_LCOM = _CQP_WEIGHTS

        def _mm(values: list, lo: float, hi: float) -> list:
            """Min-max normalization, distinguishing the two zero-span cases.

            A zero span means min-max has no scale to work with, but it arises
            two ways that must not be treated alike:

            * **One genuine node** carrying a real measurement. There is no
              population to rank it against, so it keeps the max-norm 1.0 that
              preserves its complexity signal (Hardening Issue 5).
            * **A population with no variance** — in practice, many nodes that
              carry no ``code_metrics`` at all, so every raw value is 0.0. A
              constant carries no ranking information; scoring all of them as
              maximally penalised is indistinguishable from "uniformly the
              worst", which is what used to give every Library in the
              real-world scenarios ``CQP = 0.70`` on absent data.
            """
            span = hi - lo
            if span == 0:
                if len(values) == 1 and hi > 0.0:
                    return [1.0] * len(values)
                return [0.0] * len(values)
            return [(v - lo) / span for v in values]

        # Process each qualifying node type as an independent population
        for node_type in ("Application", "Library"):
            ids   = []
            locs  = []
            ccs   = []
            lcoms = []

            for nid, m in components.items():
                if m.type != node_type:
                    continue
                ids.append(nid)
                locs.append(m.loc_norm)        # holds raw LOC float at this point
                ccs.append(m.complexity_norm)  # holds raw CC float
                lcoms.append(m.lcom_norm)      # holds raw LCOM float

            if not ids:
                continue  # no nodes of this type in the layer

            norm_locs  = _mm(locs,  min(locs),  max(locs))
            norm_ccs   = _mm(ccs,   min(ccs),   max(ccs))
            norm_lcoms = _mm(lcoms, min(lcoms), max(lcoms))

            for idx, nid in enumerate(ids):
                m = components[nid]
                m.loc_norm        = norm_locs[idx]
                m.complexity_norm = norm_ccs[idx]
                m.lcom_norm       = norm_lcoms[idx]
                m.code_quality_penalty = (
                    W_LOC  * m.loc_norm
                    + W_CC   * m.complexity_norm
                    + W_INS  * m.instability_code
                    + W_LCOM * m.lcom_norm
                )

    @staticmethod
    def _compute_pubsub_metrics(
        graph_data: Any,
        allowed_ids: Set[str],
    ) -> Dict[str, Dict[str, float]]:
        """
        Compute pub-sub topology metrics for Application nodes by building a
        bipartite app↔topic graph from raw PUBLISHES_TO and SUBSCRIBES_TO edges.

        Also resolves broker exposure via ROUTES edges.

        Returns:
            Dict[app_id, {pubsub_degree, pubsub_betweenness, broker_exposure, publisher_spof}]
        """
        import networkx as nx

        PUBSUB_EDGE_TYPES = {"PUBLISHES_TO", "SUBSCRIBES_TO", "publishes_to", "subscribes_to"}
        ROUTES_EDGE_TYPES = {"ROUTES", "routes"}

        # Build bipartite app-topic undirected graph
        G_ps = nx.Graph()
        topic_to_brokers: Dict[str, Set[str]] = {}

        for edge in graph_data.edges:
            dep = getattr(edge, "dependency_type", "") or ""
            src, tgt = edge.source_id, edge.target_id

            if dep in PUBSUB_EDGE_TYPES:
                # Only include edges from allowed (layer-filtered) app nodes
                if src in allowed_ids:
                    G_ps.add_node(src, bipartite=0)
                    G_ps.add_node(tgt, bipartite=1)
                    G_ps.add_edge(src, tgt)

            elif dep in ROUTES_EDGE_TYPES:
                # Broker → Topic routing (for broker_exposure)
                topic_to_brokers.setdefault(tgt, set()).add(src)

        result: Dict[str, Dict[str, float]] = {}

        if G_ps.number_of_nodes() == 0:
            return result

        # Betweenness centrality on the bipartite graph
        try:
            bt = nx.betweenness_centrality(G_ps, normalized=True)
        except Exception:
            bt = {n: 0.0 for n in G_ps.nodes}

        # Max values for normalization
        max_deg = max((G_ps.degree(n) for n in G_ps.nodes if G_ps.nodes[n].get("bipartite") == 0), default=1)
        max_exp = max(
            (len(topic_to_brokers.get(t, set()))
             for n in G_ps.nodes if G_ps.nodes[n].get("bipartite") == 0
             for t in G_ps.neighbors(n)),
            default=1,
        ) or 1

        for node in G_ps.nodes:
            if G_ps.nodes[node].get("bipartite") != 0:
                continue  # skip topic nodes
            if node not in allowed_ids:
                continue

            deg = G_ps.degree(node)
            # broker_exposure: average number of distinct brokers routing
            # topics this app touches (normalised to [0,1])
            touched_topics = list(G_ps.neighbors(node))
            if touched_topics:
                broker_counts = [len(topic_to_brokers.get(t, set())) for t in touched_topics]
                avg_broker_exp = sum(broker_counts) / len(broker_counts)
            else:
                avg_broker_exp = 0.0

            result[node] = {
                "pubsub_degree": deg / max_deg if max_deg > 0 else 0.0,
                "pubsub_betweenness": bt.get(node, 0.0),
                "broker_exposure": avg_broker_exp / max_exp if max_exp > 0 else 0.0,
                "publisher_spof": 0.0,  # placeholder, calculated below
            }

        # --- Publisher SPOF calculation ---
        # PSPOF(v) = max(qos_weight(t) * min(sub_count(t)/5, 1)) for topics where v is sole pub
        topic_info: Dict[str, Dict[str, Any]] = {
            c.id: {
                "weight": c.weight,
                "sub_count": c.properties.get("subscriber_count", 0),
                "pub_count": c.properties.get("publisher_count", 0)
            }
            for c in graph_data.components if c.component_type == "Topic"
        }

        # Track which apps publish to which topics
        app_to_topics: Dict[str, List[str]] = {}
        for edge in graph_data.edges:
            if edge.dependency_type.upper() == "PUBLISHES_TO":
                app_to_topics.setdefault(edge.source_id, []).append(edge.target_id)

        for app_id in result:
            max_pspof = 0.0
            for topic_id in app_to_topics.get(app_id, []):
                ti = topic_info.get(topic_id)
                if not ti:
                    continue
                # If this app is the sole publisher and there are subscribers
                if ti["pub_count"] == 1 and ti["sub_count"] >= 1:
                    # Formula from Middleware 2026 validation strategy:
                    # qos_weight * min(sub_count / _PSPOF_SUB_SATURATION, 1.0)
                    # Note: sub_count >= 2 is common for SPOF, but even 1 sub is a risk
                    current_pspof = ti["weight"] * min(
                        ti["sub_count"] / _PSPOF_SUB_SATURATION, 1.0
                    )
                    max_pspof = max(max_pspof, current_pspof)
            
            result[app_id]["publisher_spof"] = max_pspof

        # --- Surface raw topic pub/sub counts (TOPIC_FANOUT / ORPHANED_TOPIC) ---
        # result is otherwise keyed by app ids only, so seeding topic ids here is collision-free.
        for topic_id, ti in topic_info.items():
            if topic_id not in allowed_ids:
                continue
            result[topic_id] = {
                "topic_subscriber_count": ti["sub_count"],
                "topic_publisher_count": ti["pub_count"],
            }

        return result

    @staticmethod
    def _collect_qos_profile(graph_data: Any) -> Dict[str, Any]:
        """
        Aggregate topic QoS distributions from graph_data for use by
        QoS-aware RM weight adjustment in QualityAnalyzer.

        Returns a dict:
            {
              "durability": {"volatile": N, "transient_local": N, ...},
              "reliability": {"best_effort": N, "reliable": N},
              "priority":    {"low": N, "medium": N, "high": N, "critical": N},
              "total_topics": N,
            }
        """
        durability: Dict[str, int] = {}
        reliability: Dict[str, int] = {}
        priority: Dict[str, int] = {}
        total = 0

        for comp in graph_data.components:
            if getattr(comp, "component_type", "") != "Topic":
                continue
            # Both repositories store Topic QoS flat (qos_reliability, ...);
            # reading only the nested "qos" dict left this profile empty for
            # every repository-loaded system. from_node_attrs reads either shape.
            qos = QoSPolicy.from_node_attrs(getattr(comp, "properties", {}) or {})

            dur = qos.durability.lower().replace(" ", "_")
            rel = qos.reliability.lower().replace(" ", "_")
            pri = qos.transport_priority.lower().replace(" ", "_")

            durability[dur] = durability.get(dur, 0) + 1
            reliability[rel] = reliability.get(rel, 0) + 1
            priority[pri] = priority.get(pri, 0) + 1
            total += 1

        return {
            "durability": durability,
            "reliability": reliability,
            "priority": priority,
            "total_topics": total,
        }

    def _safe_eigenvector(self, G: nx.DiGraph) -> Dict[str, float]:
        """
        Compute eigenvector centrality with Katz centrality fallback.

        Eigenvector centrality may fail to converge on directed acyclic
        graphs (DAGs) or nearly-acyclic graphs because the dominant
        eigenvalue doesn't exist. Katz centrality with attenuation
        factor α handles these cases gracefully.
        """
        try:
            return nx.eigenvector_centrality(G, max_iter=500, weight="weight")
        except (nx.PowerIterationFailedConvergence, nx.NetworkXException):
            self._logger.warning(
                "Eigenvector centrality did not converge; "
                "falling back to Katz centrality"
            )
            try:
                return nx.katz_centrality(G, alpha=0.01, beta=1.0, weight="weight")
            except (nx.NetworkXException, nx.PowerIterationFailedConvergence):
                self._logger.warning(
                    "Katz centrality also failed; returning zeros"
                )
                return {n: 0.0 for n in G.nodes}

    @staticmethod
    def _compute_directed_articulation_points(G: nx.DiGraph) -> Set[str]:
        """
        Identify directed articulation points (SPOFs) via BFS with node exclusion.

        A node v is a directed AP if its removal reduces the set of nodes
        reachable from all in-degree-0 roots.  Uses explicit exclusion in BFS
        rather than graph copies, eliminating the O(V+E) allocation per node.

        Complexity: O(|base_reachable| * (V+E)) vs the old O(V * (V+E)).
        """
        roots = [n for n in G.nodes if G.in_degree(n) == 0]
        if not roots:
            return set()

        # One BFS from all roots to establish the baseline reachable set
        base_reachable: Set[str] = set()
        for r in roots:
            if r not in base_reachable:
                stack = [r]
                base_reachable.add(r)
                while stack:
                    curr = stack.pop()
                    for nb in G.successors(curr):
                        if nb not in base_reachable:
                            base_reachable.add(nb)
                            stack.append(nb)

        if not base_reachable:
            return set()

        target = len(base_reachable) - 1
        directed_aps: Set[str] = set()

        for v in base_reachable:
            # BFS from roots, skipping v — no graph copy needed
            reachable: Set[str] = set()
            stack = []
            for r in roots:
                if r != v and r not in reachable:
                    reachable.add(r)
                    stack.append(r)
            while stack:
                curr = stack.pop()
                for nb in G.successors(curr):
                    if nb != v and nb not in reachable:
                        reachable.add(nb)
                        stack.append(nb)
            if len(reachable) < target:
                directed_aps.add(v)

        return directed_aps

    @staticmethod
    def _compute_continuous_ap_scores(
        G_dir: nx.DiGraph,
    ) -> Dict[str, Dict[str, float]]:
        """
        Compute continuous AP scores (AP_c) and CDI per node without graph copies.

        Key idea: run nx.articulation_points (Tarjan, O(V+E)) first to identify
        which nodes can actually fragment the graph.  ap_c_dir is 0.0 for every
        non-AP immediately — no BFS needed, and mathematically exact (a non-cut
        vertex cannot change component membership). CDI is different: it is a
        graded path-length-inflation measure, well-defined for any node in the
        main component, not just cut vertices — it is what lets a redundant but
        load-bearing node (e.g. a sole publisher in a 2-connected pub-sub mesh)
        register nonzero risk even though it fragments nothing. CDI is therefore
        computed for the whole main component, at AP-scale BFS cost per node.

        CDI uses BFS from a fixed 32-node sample rather than
        nx.average_shortest_path_length on each residual graph (which was O(V²)
        per node in the old code).

        Complexity: O(V+E) for Tarjan + O(|AP| * avg_BFS) for ap_c
                    + O(|main CC| * avg_BFS) for CDI
                    vs old O(V * (V+E)) due to per-node graph copies.
        """
        from collections import deque as _deque

        n = G_dir.number_of_nodes()
        if n <= 1:
            return {nid: {"ap_c_dir": 0.0, "cdi": 0.0} for nid in G_dir.nodes}

        G_undir = G_dir.to_undirected()
        G_T_undir = G_dir.reverse(copy=False).to_undirected()

        # ------------------------------------------------------------------ #
        # Identify undirected APs via Tarjan (O(V+E)) so non-APs             #
        # short-circuit to 0.0 without any further work.                      #
        # ------------------------------------------------------------------ #
        def _find_art_points(G_u: nx.Graph) -> Set[str]:
            if nx.is_connected(G_u):
                return set(nx.articulation_points(G_u))
            pts: Set[str] = set()
            for comp in nx.connected_components(G_u):
                sub = G_u.subgraph(comp)
                if len(sub) >= 3:
                    pts.update(nx.articulation_points(sub))
            return pts

        art_points_fwd = _find_art_points(G_undir)
        art_points_rev = _find_art_points(G_T_undir)
        all_aps = art_points_fwd | art_points_rev

        # ------------------------------------------------------------------ #
        # BFS component walk with one node excluded — no graph copy.          #
        # Returns the size of the largest CC after removing `excluded`.       #
        # ------------------------------------------------------------------ #
        def _largest_cc_excluding(G_u: nx.Graph, excluded: str) -> int:
            visited: Set[str] = {excluded}
            largest = 0
            for start in G_u.nodes:
                if start in visited:
                    continue
                visited.add(start)
                size = 1
                queue: deque = _deque([start])
                while queue:
                    curr = queue.popleft()
                    for nb in G_u.neighbors(curr):
                        if nb not in visited:
                            visited.add(nb)
                            queue.append(nb)
                            size += 1
                if size > largest:
                    largest = size
            return largest

        # ------------------------------------------------------------------ #
        # CDI baseline: BFS from top-16 degree nodes in the largest CC.      #
        # Fixed sample size keeps cost constant regardless of graph size.     #
        # Halved from 32 (pre-v4) now that CDI is computed for every node in #
        # the main component rather than only articulation points — the     #
        # per-node cost is paid roughly |main CC| times instead of |AP|      #
        # times, so the sample size is the main remaining lever on runtime.  #
        # ------------------------------------------------------------------ #
        _CDI_SAMPLE = 16
        ccs = list(nx.connected_components(G_undir))
        largest_cc_nodes = max(ccs, key=len) if ccs else set()
        G_main = G_undir.subgraph(largest_cc_nodes)

        def _avg_bfs_length(G_u: nx.Graph, sources: List[str]) -> float:
            total, count = 0, 0
            for s in sources:
                if s not in G_u:
                    continue
                lengths = nx.single_source_shortest_path_length(G_u, s)
                total += sum(lengths.values())
                count += len(lengths)
            return total / count if count else 0.0

        def _avg_bfs_length_excluding(
            G_u: nx.Graph, sources: List[str], excluded: str,
        ) -> float:
            """Same as _avg_bfs_length(G_u, sources) with `excluded` removed,
            but BFS's directly on G_u's adjacency instead of building a
            SubGraph view per call — the view's filtered-dict indirection is
            the dominant cost when this runs once per node in the main
            component rather than once per articulation point."""
            total, count = 0, 0
            for s in sources:
                if s == excluded or s not in G_u:
                    continue
                visited = {excluded, s}
                queue: deque = _deque([(s, 0)])
                while queue:
                    curr, dist = queue.popleft()
                    total += dist
                    count += 1
                    for nb in G_u.neighbors(curr):
                        if nb not in visited:
                            visited.add(nb)
                            queue.append((nb, dist + 1))
            return total / count if count else 0.0

        sample_nodes: List[str] = []
        baseline_avg = 0.0
        if len(largest_cc_nodes) > 1:
            # Ties on degree are broken by id. `largest_cc_nodes` is a set, so a
            # degree-only key left equal-degree nodes in set-iteration order --
            # salted by PYTHONHASHSEED -- and which of them made the cut changed
            # the baseline, every node's CDI and so Q(v) from one process to the
            # next (up to 4.4e-4 on atm_system).
            sample_nodes = sorted(
                largest_cc_nodes,
                key=lambda v: (-G_main.degree(v), str(v)),
            )[:_CDI_SAMPLE]
            baseline_avg = _avg_bfs_length(G_main, sample_nodes)

        # ------------------------------------------------------------------ #
        # Main loop.  ap_c_dir stays AP-gated: a non-cut vertex cannot        #
        # fragment the graph, so component-size fragmentation is genuinely    #
        # 0 for it — that is a mathematical fact of the measure, not a        #
        # short-circuit. CDI is NOT AP-gated: articulation-point membership   #
        # is a binary cut-vertex test, so gating CDI on it collapses every    #
        # redundant-but-load-bearing node (e.g. a sole publisher in a         #
        # 2-connected pub-sub mesh) to exactly 0, even though removing it     #
        # measurably lengthens paths for the sampled sources. Computing CDI   #
        # for every node in the main component turns it into that continuous #
        # redundancy-deficit signal; nodes outside the main component have    #
        # no sample baseline to compare against and keep cdi=0.0.            #
        # ------------------------------------------------------------------ #
        ap_scores: Dict[str, Dict[str, float]] = {}

        for nid in G_dir.nodes:
            if nid not in all_aps:
                ap_c_dir = 0.0
            else:
                # ap_c_out (forward undirected view)
                if nid in art_points_fwd:
                    largest_out = _largest_cc_excluding(G_undir, nid)
                    ap_c_out = 1.0 - largest_out / (n - 1)
                else:
                    ap_c_out = 0.0

                # ap_c_in (transposed undirected view)
                if nid in art_points_rev:
                    largest_in = _largest_cc_excluding(G_T_undir, nid)
                    ap_c_in = 1.0 - largest_in / (n - 1)
                else:
                    ap_c_in = 0.0

                ap_c_dir = max(ap_c_out, ap_c_in)

            # CDI — computed for every node in the main component, not just APs.
            cdi_val = 0.0
            if baseline_avg > 0.0 and nid in largest_cc_nodes:
                if nid in art_points_fwd:
                    post_nodes = [v for v in G_undir.nodes if v != nid]
                    post_ccs = list(nx.connected_components(G_undir.subgraph(post_nodes)))
                    fragments = len(post_ccs) > len(ccs)
                else:
                    fragments = False  # non-AP: cannot fragment, by definition

                if fragments:
                    cdi_val = 1.0  # fragmentation — worst case
                else:
                    post_sources = [s for s in sample_nodes if s != nid]
                    if post_sources and G_main.number_of_nodes() > 1:
                        after_avg = _avg_bfs_length_excluding(G_main, post_sources, nid)
                        cdi_val = min(max(0.0, (after_avg - baseline_avg) / baseline_avg), 1.0)

            ap_scores[nid] = {"ap_c_dir": ap_c_dir, "cdi": cdi_val}

        return ap_scores

    def _build_summary(
        self,
        G: nx.DiGraph,
        layer: AnalysisLayer,
        art_points: Set[str],
        bridges: Set[Tuple[str, str]],
    ) -> GraphSummary:
        """Compute graph-level statistics including assortativity."""
        U = G.to_undirected()
        cc = list(nx.connected_components(U))
        largest_cc = max(cc, key=len) if cc else set()

        diameter = 0
        avg_path = 0.0
        if len(largest_cc) >= 2:
            sub = G.subgraph(largest_cc)
            try:
                diameter = nx.diameter(sub.to_undirected())
                avg_path = nx.average_shortest_path_length(sub.to_undirected())
            except nx.NetworkXError:
                pass

        # Assortativity (Pearson correlation of degree-degree at edge endpoints)
        assortativity = 0.0
        try:
            assortativity = nx.degree_assortativity_coefficient(G)
        except (nx.NetworkXError, ValueError):
            pass

        # Average clustering
        avg_clustering = nx.average_clustering(U) if U.number_of_nodes() > 0 else 0.0

        # Node and edge type breakdowns
        node_types: Dict[str, int] = {}
        for _, data in G.nodes(data=True):
            t = data.get("component_type", "Unknown")
            node_types[t] = node_types.get(t, 0) + 1

        edge_types: Dict[str, int] = {}
        for _, _, data in G.edges(data=True):
            t = data.get("dependency_type", "unknown")
            edge_types[t] = edge_types.get(t, 0) + 1

        return GraphSummary(
            layer=layer.value,
            nodes=G.number_of_nodes(),
            edges=G.number_of_edges(),
            density=nx.density(G),
            avg_degree=sum(d for _, d in G.degree()) / G.number_of_nodes() if G.number_of_nodes() > 0 else 0.0,
            avg_clustering=avg_clustering,
            is_connected=nx.is_weakly_connected(G) if G.number_of_nodes() > 0 else False,
            num_components=len(cc),
            num_articulation_points=len(art_points),
            num_bridges=len(bridges),
            diameter=diameter,
            avg_path_length=avg_path,
            assortativity=assortativity,
            node_types=node_types,
            edge_types=edge_types,
        )

    @staticmethod
    def _empty_result(layer: AnalysisLayer) -> StructuralAnalysisResult:
        return StructuralAnalysisResult(
            layer=layer,
            components={},
            edges={},
            graph_summary=GraphSummary(
                layer=layer.value,
                nodes=0, edges=0, density=0.0,
                num_components=0, diameter=0, avg_path_length=0.0,
            ),
        )