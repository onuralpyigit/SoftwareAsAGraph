"""
fault_injector.py
─────────────────
Pub-sub-aware BFS cascade fault injector for the SaG pipeline.

PURPOSE
───────
Produces a proxy *ground-truth* impact score I(v) for every node v by
simulating its failure and tracing the resulting cascade through the
publish-subscribe dependency graph.  I(v) is used as the target variable
when computing Spearman ρ between the topology-derived Q(v) predictor and
observed impact.

This is the SOLE engine that produces the I*(v) ground truth reported in
docs/research/jss/draft.md Section 7.5 (the HGT/HGT-QoS/GAT/GAT-QoS/
Topo/Topo-QoS evaluation). Do not confuse this with the differently-named
"impact"/"I(v)" quantities elsewhere in this package -- e.g.
saag.simulation.models.ImpactMetrics.composite_impact and its IR(v)/IM(v)/
IA(v)/IS(v) properties (AHP-weighted RM metrics from FailureSimulator, used
for an unrelated quality-attribution framework) -- which are different
formulas over different targets and are never used as HGT/GAT ground truth.

ALGORITHM
─────────
For each candidate node v (Application or Broker by default):

  Wave 0 – Direct orphaning
    • Remove v from the graph.
    • Both publisher loss and router loss are fractional, not all-or-nothing:
      a topic served by two live brokers loses half its feed when one dies.
      Redundancy therefore softens the blow rather than nullifying it.
      [FIX: BUG-FI-1]

  Cascade propagation (waves 1, 2, …)
    Each wave runs two phases:

    Phase A — direct DEPENDS_ON propagation. A dependent fails outright when
      the dependency is a Library (dependency_type == "app_to_lib", or the
      source node is itself typed Library); other DEPENDS_ON arcs do not
      propagate.
    Phase B — topic-mediated soft propagation. Each topic's feed loss L(t) is
      max(publisher loss, router loss): the rate-weighted share of its
      publishers that are down, against the share of its routing brokers that
      are down. Either channel alone silences the topic, so they combine as a
      weakest link — a topic with no publishers (or no brokers) contributes
      0.0 from that side rather than being scored on the other side alone.
      A subscriber's loss is the mean L(t) over its feeds, scaled by the
      topic QoS factor;
      once that exceeds *propagation_threshold* it fails with probability
      min(1, loss/threshold) × depth_damp, where depth_damp decays by 0.15 per
      wave down to a floor of 0.25.

    Repeat until fixpoint or cascade_depth_limit is reached.

  I(v) computation
    • I(v) = mean over all subscribers of the continuous, rate-weighted and
      QoS-scaled feed-loss fraction — not a count of fully lost feeds.

MULTI-SEED STABILITY
────────────────────
When multiple seeds are supplied, each seed drives the per-wave Bernoulli
failure draws of the subscribers whose feed loss crossed the threshold (there
is no tie-breaking shuffle; wave 0 draws at probability 1.0, so it is
deterministic).  I(v) is the mean across seeds;
impact_score_std is the standard deviation.  The cascade trace recorded is
from the seed whose impact score is closest to the mean (median-representative
seed).  [FIX: DESIGN-FI-2]
"""

from __future__ import annotations

import logging
import math
import random
import statistics
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

import networkx as nx

from .simulation_results import (
    CascadeWave,
    FaultInjectionRecord,
    FaultInjectionResult,
)
from saag.core.models import QoSPolicy, topic_weight_from_node_attrs
from saag.core.network import NETWORK_TYPES, Fabric, evaluate

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# I*(v) simulation-softening constants (see docs/research/jss/draft.md,
# Section 7.5 "Ground Truth: Two Oracles" -- the canonical values reported there).
# ─────────────────────────────────────────────────────────────────────────────

#: Recommended multi-seed set for stable, reproducible I*(v) estimates.
RECOMMENDED_SEEDS: List[int] = [42, 123, 456, 789, 2024]

#: Topic QoS factors applied to a subscriber's feed-loss fraction during cascade softening.
RELIABLE_QOS_FACTOR: float = 1.2
HIGH_PRIORITY_QOS_FACTOR: float = 1.15
MEDIUM_PRIORITY_QOS_FACTOR: float = 1.05


# ─────────────────────────────────────────────────────────────────────────────
# Graph-index helper
# ─────────────────────────────────────────────────────────────────────────────

class _PubSubIndex:
    """
    Lightweight read-only index over the pub-sub layer of the SaG graph.

    Builds O(1) lookup structures from the raw NetworkX graph so the
    cascade loops spend no time on edge-type filtering.
    """

    def __init__(self, g: nx.DiGraph) -> None:
        # topic_id → set of publisher app_ids
        self.topic_publishers: Dict[str, Set[str]] = defaultdict(set)
        # topic_id → set of subscriber app_ids
        self.topic_subscribers: Dict[str, Set[str]] = defaultdict(set)
        # app_id → set of published topic_ids
        self.app_publishes: Dict[str, Set[str]] = defaultdict(set)
        # app_id → set of subscribed topic_ids
        self.app_subscribes: Dict[str, Set[str]] = defaultdict(set)
        # broker_id → set of topic_ids it routes
        self.broker_routes: Dict[str, Set[str]] = defaultdict(set)
        # topic_id → set of broker_ids that route it (inverse of broker_routes)
        self.topic_routers: Dict[str, Set[str]] = defaultdict(set)
        # host_id → set of component_ids deployed on it (inverse of RUNS_ON)
        self.host_residents: Dict[str, Set[str]] = defaultdict(set)
        # component_id → host_ids it RUNS_ON
        self.hosts_of: Dict[str, List[str]] = defaultdict(list)
        # CONNECTS_TO links, read as undirected cables
        self.net_links: List[Tuple[str, str, Optional[float]]] = []
        # node metadata
        self.node_type: Dict[str, str] = {}
        self.node_name: Dict[str, str] = {}

        for node, data in g.nodes(data=True):
            # Check both 'type' (library) and 'ntype' (validation CLI)
            self.node_type[node] = data.get("type") or data.get("ntype") or "Unknown"
            self.node_name[node] = data.get("name") or data.get("label") or node

        for src, tgt, data in g.edges(data=True):
            etype = (data.get("type") or data.get("etype") or "").upper()
            if etype == "PUBLISHES_TO":
                self.topic_publishers[tgt].add(src)
                self.app_publishes[src].add(tgt)
            elif etype == "SUBSCRIBES_TO":
                self.topic_subscribers[tgt].add(src)
                self.app_subscribes[src].add(tgt)
            elif etype == "ROUTES":
                self.broker_routes[src].add(tgt)
                self.topic_routers[tgt].add(src)
            elif etype == "RUNS_ON":
                self.host_residents[tgt].add(src)
                self.hosts_of[src].append(tgt)
            elif etype == "CONNECTS_TO":
                self.net_links.append((src, tgt, data.get("capacity_mbps")))

        self.all_subscribers: Set[str] = {
            a for a, subs in self.app_subscribes.items() if subs
        }
        self.all_topics: Set[str] = (
            set(self.topic_publishers.keys()) | set(self.topic_subscribers.keys())
        )

    def publishers_of(self, topic: str) -> Set[str]:
        return self.topic_publishers.get(topic, set())

    def subscribers_of(self, topic: str) -> Set[str]:
        return self.topic_subscribers.get(topic, set())

    def topics_routed_by(self, broker: str) -> Set[str]:
        return self.broker_routes.get(broker, set())

    def live_routers_of(self, topic: str, failed: Set[str]) -> Set[str]:
        """Return brokers that route *topic* and are not in *failed*."""
        return self.topic_routers.get(topic, set()) - failed

    def residents_of(self, host: str) -> Set[str]:
        """Return the components deployed on *host* via RUNS_ON."""
        return self.host_residents.get(host, set())


# ─────────────────────────────────────────────────────────────────────────────
# Fault Injector
# ─────────────────────────────────────────────────────────────────────────────

class FaultInjector:
    """
    Runs systematic single-node fault injection across a SaG graph and
    produces I(v) ground-truth impact scores for every injected node.

    Parameters
    ----------
    graph : nx.DiGraph
        The full SaG graph exported by GraphExporter.  Must contain
        PUBLISHES_TO and SUBSCRIBES_TO edges (and optionally ROUTES for
        brokers).
    seeds : list[int], optional
        Seeds for multi-seed stability testing.  Default [42].
        Recommended: RECOMMENDED_SEEDS (module-level constant, [42, 123, 456, 789, 2024]).
    cascade_depth_limit : int, optional
        Maximum cascade waves.  0 = unlimited.  Default 0.
    propagation_threshold : float, optional
        Fraction of a subscriber's mean feed loss at or above which it may fail
        (stochastically, scaled by depth damping).  Default 0.2 — a subscriber
        becomes eligible to fail once it loses ~20% of its feed.  This is an
        aggressive setting; raise toward 1.0 for a conservative
        "only-on-total-starvation" cascade.  For nodes requiring all feeds
        (e.g. ATM ConflictDetector needing T_radar AND T_tracks), 0.5 is more
        appropriate.
    qos_factor_mode : {"ladder", "wt", "none"}, optional
        How a topic's QoS scales its feed-loss fraction.  Default ``"ladder"``
        reproduces the published constants (§3.2).  ``"wt"`` uses the same
        w(t) the rest of the codebase derives QoS weights from — which, unlike
        the ladder, lets durability participate.  ``"none"`` disables QoS
        scaling entirely and is the topology-only arm of the label ablation.
    qos_factor_kappa : float, optional
        Sensitivity of the ``"wt"`` mode.  Default 0.5.
    strict_labels : bool, optional
        Raise instead of warning when an injected type scores 0.0 everywhere.
        Off by default because Broker labels are legitimately degenerate in
        several corpus scenarios; on for callers where a degenerate stratum
        means the run is wrong rather than the system is (see
        ``reproduce/passive_stratum_labels.py``).
    depth_damp_step, depth_damp_floor : float, optional
        Per-wave decay of the failure probability, ``max(floor, 1 - wave *
        step)``.  Defaults 0.15 and 0.25 reproduce the published labels; they
        are exposed only for the oracle-sensitivity sweep.
    network_gating : bool, optional
        Count a subscriber's feed as lost while the CONNECTS_TO network (hosts,
        switches, routers) no longer connects it to any live publisher through a
        live routing broker.  Gives Node and NetworkDevice failures labels of
        their own.  Reachability only (no congestion).  Off by default, which
        keeps the published labels; a no-op on graphs without CONNECTS_TO.
    replica_aware : bool, optional
        A Node outage kills a resident only when every host it RUNS_ON is
        down, so a hot-standby component survives the loss of one replica.
        Single-fault injection means that happens only for single-hosted
        residents.  Off by default, which keeps the published labels: any one
        host failure kills a multi-hosted component.
    failure_model : {"step", "threshold_cdf"}, optional
        ``"step"`` (default) reproduces the published labels: a subscriber
        whose feed loss reaches ``propagation_threshold`` fails with
        probability ``depth_damp`` -- the ``min(1, loss/threshold)`` factor is
        always 1 there, so the loss magnitude past the threshold never matters
        and the label has a cliff at the threshold.
        ``"threshold_cdf"`` gives every subscriber a latent tolerance
        ``theta ~ U(threshold_range)``, drawn once per seed. It may fail
        (probability ``depth_damp``) once its loss reaches theta, and it is
        re-drawn only when its loss increases -- not on every wave, which made
        the old odds depend on how long the cascade ran elsewhere. Averaged
        over seeds, P(fail) = F_theta(loss) * depth_damp: the cliff becomes the
        threshold distribution's CDF, and the seed spread now measures
        threshold uncertainty. ``propagation_threshold`` is unused in this mode.
    threshold_range : (float, float), optional
        Support of theta under ``"threshold_cdf"``. Default (0.1, 0.5).
    """

    #: Accepted values for ``qos_factor_mode``.
    QOS_FACTOR_MODES = ("ladder", "wt", "none")
    #: Accepted values for ``failure_model``.
    FAILURE_MODELS = ("step", "threshold_cdf")

    def __init__(
        self,
        graph: nx.DiGraph,
        seeds: Optional[List[int]] = None,
        cascade_depth_limit: int = 0,
        propagation_threshold: float = 0.2,
        qos_factor_mode: str = "ladder",
        qos_factor_kappa: float = 0.5,
        strict_labels: bool = False,
        depth_damp_step: float = 0.15,
        depth_damp_floor: float = 0.25,
        network_gating: bool = False,
        replica_aware: bool = False,
        failure_model: str = "step",
        threshold_range: tuple = (0.1, 0.5),
    ) -> None:
        if failure_model not in self.FAILURE_MODELS:
            raise ValueError(
                f"failure_model must be one of {self.FAILURE_MODELS}, got {failure_model!r}")
        lo, hi = (float(x) for x in threshold_range)
        if not 0.0 <= lo < hi <= 1.0:
            raise ValueError(f"threshold_range must satisfy 0 <= lo < hi <= 1, got {threshold_range!r}")
        self.failure_model = failure_model
        self.threshold_range = (lo, hi)
        if qos_factor_mode not in self.QOS_FACTOR_MODES:
            raise ValueError(
                f"qos_factor_mode must be one of {self.QOS_FACTOR_MODES}, "
                f"got {qos_factor_mode!r}"
            )
        self.qos_factor_mode = qos_factor_mode
        self.qos_factor_kappa = qos_factor_kappa
        self.strict_labels = strict_labels
        self.depth_damp_step = depth_damp_step
        self.depth_damp_floor = depth_damp_floor
        self.replica_aware = replica_aware
        self.graph = graph.copy()
        
        # Derive DEPENDS_ON edges dynamically if they are missing
        has_depends_on = any((d.get("type") or d.get("etype") or "").upper() == "DEPENDS_ON" for _, _, d in self.graph.edges(data=True))
        if not has_depends_on:
            from collections import defaultdict
            topic_pubs = defaultdict(set)
            topic_subs = defaultdict(set)
            pub_qos = {}
            sub_qos = {}
            uses_rels = []
            for src, tgt, d in self.graph.edges(data=True):
                etype = (d.get("type") or d.get("etype") or "").upper()
                if etype == "PUBLISHES_TO":
                    topic_pubs[tgt].add(src)
                    if "qos_profile" in d:
                        pub_qos[(src, tgt)] = d["qos_profile"]
                elif etype == "SUBSCRIBES_TO":
                    topic_subs[tgt].add(src)
                    if "qos_profile" in d:
                        sub_qos[(src, tgt)] = d["qos_profile"]
                elif etype == "USES":
                    uses_rels.append((src, tgt))
            # Subscriber depends on publisher (via shared topic)
            for topic, publishers in topic_pubs.items():
                for subscriber in topic_subs[topic]:
                    for publisher in publishers:
                        if subscriber != publisher:
                            qp = pub_qos.get((publisher, topic)) or sub_qos.get((subscriber, topic)) or {}
                            self.graph.add_edge(subscriber, publisher, type="DEPENDS_ON", dependency_type="app_to_app", weight=1.0, qos_profile=qp)
            # App depends on library (uses relationship)
            for app, lib in uses_rels:
                self.graph.add_edge(app, lib, type="DEPENDS_ON", dependency_type="app_to_lib", weight=1.0)
                
        self.seeds = seeds or [42]
        self.cascade_depth_limit = cascade_depth_limit
        self.propagation_threshold = max(0.0, min(1.0, propagation_threshold))
        self._index = _PubSubIndex(self.graph)
        self._fabric: Optional[Fabric] = None
        if network_gating and self._index.net_links:
            self._fabric = Fabric(
                [n for n, t in self._index.node_type.items() if t in NETWORK_TYPES],
                self._index.net_links,
            )
        self._baseline_cut = self._network_cut(set())
        self._topic_weights = self._compute_topic_weights()
        self._mean_topic_weight = (
            sum(self._topic_weights.values()) / len(self._topic_weights)
            if self._topic_weights else 0.0
        )

    def _compute_topic_weights(self) -> Dict[str, float]:
        """w(t) for every topic, resolved from whichever QoS shape the graph carries.

        Prefers a ``weight`` already written by the repositories; otherwise derives
        it from the topic's QoS and payload size so research loaders — which pass
        raw topology JSON through untouched — get the same number.
        """
        weights: Dict[str, float] = {}
        for topic in self._index.all_topics:
            data = self.graph.nodes.get(topic, {})
            existing = data.get("weight")
            if isinstance(existing, (int, float)) and existing > 0:
                weights[topic] = float(existing)
            else:
                weights[topic] = topic_weight_from_node_attrs(data)
        return weights

    def _qos_factor(self, topic: str) -> float:
        """Multiplier applied to a topic's feed-loss fraction, per ``qos_factor_mode``.

        ``ladder`` is the published form: fixed multipliers keyed off reliability
        and transport priority.  It ignores durability, which carries the largest
        AHP sub-weight (0.62) of the three QoS dimensions.

        ``wt`` scales by how far the topic's w(t) sits from the system mean, so all
        three QoS dimensions and payload size participate through the single weight
        the rest of the codebase already agrees on.
        """
        if self.qos_factor_mode == "none":
            return 1.0

        if self.qos_factor_mode == "wt":
            w = self._topic_weights.get(topic, self._mean_topic_weight)
            return max(0.0, 1.0 + self.qos_factor_kappa * (w - self._mean_topic_weight))

        node_data = self.graph.nodes.get(topic, {})
        qos = QoSPolicy.from_node_attrs(node_data)
        factor = 1.0
        if qos.reliability.upper() == "RELIABLE":
            factor *= RELIABLE_QOS_FACTOR
        priority = qos.transport_priority.upper()
        if priority in ("HIGH", "CRITICAL", "URGENT", "HIGHEST"):
            factor *= HIGH_PRIORITY_QOS_FACTOR
        elif priority == "MEDIUM":
            factor *= MEDIUM_PRIORITY_QOS_FACTOR
        return factor

    # ── Public API ──────────────────────────────────────────────────────────

    def run(
        self,
        node_types: Optional[List[str]] = None,
        node_ids: Optional[List[str]] = None,
    ) -> FaultInjectionResult:
        """
        Run fault injection for all eligible nodes.

        Parameters
        ----------
        node_types : list[str], optional
            Node types to inject.  Default: ["Application", "Broker"].
        node_ids : list[str], optional
            Explicit node IDs to inject (overrides node_types filter).
        """
        if node_types is None:
            node_types = ["Application", "Broker"]

        if node_ids:
            candidates = [n for n in node_ids if n in self.graph.nodes]
        else:
            candidates = [
                n
                for n, d in self.graph.nodes(data=True)
                if d.get("type", "") in node_types
            ]

        n_apps = sum(
            1 for _, d in self.graph.nodes(data=True) if d.get("type") == "Application"
        )
        n_brokers = sum(
            1 for _, d in self.graph.nodes(data=True) if d.get("type") == "Broker"
        )

        result = FaultInjectionResult(
            graph_id=self.graph.graph.get("id", ""),
            total_application_nodes=n_apps,
            total_broker_nodes=n_brokers,
            total_subscribers=len(self._index.all_subscribers),
            seeds_used=self.seeds,
            labeler="FaultInjector",
            labeled_node_types=sorted(node_types),
            # Nodes the sweep never touched. Recorded explicitly so downstream
            # consumers can distinguish "simulated, impact was zero" from
            # "never simulated" instead of collapsing both to a 0.0 label.
            unlabeled_node_ids=sorted(set(self.graph.nodes) - set(candidates)),
        )

        logger.info(
            "Fault injection: %d candidates | %d subscriber(s) | seeds=%s | "
            "propagation_threshold=%.2f",
            len(candidates),
            len(self._index.all_subscribers),
            self.seeds,
            self.propagation_threshold,
        )

        for node_id in candidates:
            rec = self._inject_node(node_id)
            result.add_record(rec)
            logger.debug(
                "  %-30s  I(v)=%.4f  depth=%d  orphaned=%d  impacted=%d  std=%.4f",
                node_id,
                rec.impact_score,
                rec.cascade_depth,
                rec.total_orphaned_topics,
                rec.total_impacted_subscribers,
                rec.impact_score_std,
            )

        result.finalise()
        if self.failure_model != "step":
            result.labeler_config = {"failure_model": self.failure_model,
                                     "threshold_range": list(self.threshold_range)}
        result.label_stability = self._compute_label_stability(result)
        self._record_degenerate_types(result)
        if self.strict_labels and result.degenerate_node_types:
            raise ValueError(
                "degenerate labels: "
                f"{', '.join(result.degenerate_node_types)} scored I(v)=0 for "
                "every node. Under strict_labels this is an error, not a "
                "measurement."
            )
        logger.info("Fault injection complete.  %d records.", result.total_nodes_injected)
        return result

    @staticmethod
    def _compute_label_stability(result: FaultInjectionResult, k_frac: float = 0.20) -> Dict[str, Any]:
        """Quantify how reproducible these labels are across seeds.

        Every record already carries its per-seed scores; this turns them into a
        noise floor for the whole label set. Reported alongside the labels so a
        downstream rho can be read against its ceiling instead of in isolation:
        labels that only agree with themselves at rho=0.91 cannot support a claim
        of rho=0.95 predictive accuracy.

        Returns mean per-node std, the worst pairwise test-retest Spearman across
        seeds, and the worst top-K Jaccard overlap.
        """
        seeds = result.seeds_used or []
        records = list(result.records.values())
        stability: Dict[str, Any] = {
            "n_seeds": len(seeds),
            "k_frac": k_frac,
            "n_nodes": len(records),
        }
        if not records:
            return stability

        stability["mean_std"] = round(
            sum(r.impact_score_std for r in records) / len(records), 6
        )
        stability["max_std"] = round(max(r.impact_score_std for r in records), 6)

        if len(seeds) < 2:
            # Single seed: no spread to measure. Say so rather than reporting a
            # perfect-looking 1.0 that would overstate label quality.
            stability["test_retest_spearman"] = None
            stability["topk_jaccard"] = None
            stability["note"] = "single seed — label stability not measured"
            return stability

        try:
            from scipy.stats import spearmanr
        except ImportError:  # pragma: no cover - scipy is a core dependency
            stability["note"] = "scipy unavailable — correlation not computed"
            return stability

        per_seed = [
            [r.seed_impact_scores.get(s, 0.0) for r in records] for s in seeds
        ]
        k = max(1, int(round(len(records) * k_frac)))

        def _top_k(scores: List[float]) -> set:
            order = sorted(range(len(scores)), key=lambda i: -scores[i])
            return set(order[:k])

        rhos: List[float] = []
        jaccards: List[float] = []
        for i in range(len(seeds)):
            for j in range(i + 1, len(seeds)):
                rho = spearmanr(per_seed[i], per_seed[j]).correlation
                if not math.isnan(rho):
                    rhos.append(float(rho))
                jaccards.append(len(_top_k(per_seed[i]) & _top_k(per_seed[j])) / k)

        # Report the worst pair, not the mean: the ceiling is set by the weakest
        # agreement, and averaging hides it.
        stability["test_retest_spearman"] = round(min(rhos), 6) if rhos else None
        stability["topk_jaccard"] = round(min(jaccards), 6) if jaccards else None
        return stability

    @staticmethod
    def _record_degenerate_types(result: FaultInjectionResult) -> None:
        """Flag node types whose entire label set came out zero.

        A type that scores 0.0 everywhere is almost never a finding about the
        system — it means the cascade has no path to express that type's failure.
        Training on such a block teaches the model to predict a constant, so it
        must be visible rather than silently averaged in.

        This used to warn only. A log line is discarded by any batch run, which
        is how a degenerate block reaches a table unnoticed — the same shape of
        defect as a stale cache. The types are now recorded on the result and
        travel into the artifact; callers that cannot tolerate one (see
        ``strict_labels``) turn it into an error.

        Broker labels are legitimately degenerate in several corpus scenarios, so
        this is deliberately not fatal by default.
        """
        by_type: Dict[str, List[float]] = defaultdict(list)
        for rec in result.records.values():
            by_type[rec.node_type].append(rec.impact_score)

        for node_type, scores in sorted(by_type.items()):
            if scores and max(scores) <= 1e-9:
                result.degenerate_node_types.append(node_type)
                logger.warning(
                    "DEGENERATE LABELS: all %d '%s' nodes scored I(v)=0. The cascade "
                    "cannot express this type's failure, so these are not measurements. "
                    "Exclude '%s' from --node-types, or they will train the model "
                    "toward a constant.",
                    len(scores), node_type, node_type,
                )

    # ── Core injection logic ─────────────────────────────────────────────────

    def _inject_node(self, node_id: str) -> FaultInjectionRecord:
        node_type = self._index.node_type.get(node_id, "Unknown")
        node_name = self._index.node_name.get(node_id, node_id)

        seed_scores: Dict[int, float] = {}
        seed_records: List["_SingleSeedResult"] = []

        for seed in self.seeds:
            sr = self._cascade(node_id, node_type, seed)
            seed_scores[seed] = sr.impact_score
            seed_records.append(sr)

        mean_score = sum(seed_scores.values()) / len(seed_scores)
        std_score = (
            statistics.stdev(seed_scores.values()) if len(seed_scores) > 1 else 0.0
        )

        # DESIGN-FI-2 FIX: select the seed closest to the mean as primary trace
        primary = min(seed_records, key=lambda sr: abs(sr.impact_score - mean_score))

        return FaultInjectionRecord(
            node_id=node_id,
            node_type=node_type,
            node_name=node_name,
            impact_score=round(mean_score, 6),
            total_orphaned_topics=len(primary.all_orphaned_topics),
            total_impacted_subscribers=len(primary.impacted_subscribers),
            total_subscribers=len(self._index.all_subscribers),
            cascade_depth=primary.cascade_depth,
            directly_orphaned_topics=sorted(primary.directly_orphaned_topics),
            all_orphaned_topics=sorted(primary.all_orphaned_topics),
            impacted_subscriber_ids=sorted(primary.impacted_subscribers),
            per_subscriber_feed_loss={
                sub: round(v, 4)
                for sub, v in primary.per_subscriber_feed_loss.items()
            },
            cascade_waves=primary.waves,
            seed_impact_scores=seed_scores,
            impact_score_std=round(std_score, 6),
        )

    def _network_cut(self, failed: Set[str]) -> Dict[str, Set[str]]:
        """subscriber → topics it still subscribes to but the network no longer delivers.

        A feed is cut when the topic has live publishers (and, if brokered, live
        routing brokers) yet no live path joins any of them to the subscriber.
        Subscribers that failed in the cascade keep their cut feeds -- the cut is
        why they failed -- but one whose own host is down is not cut off by the
        network, so it is left out. Empty without network gating.
        """
        if self._fabric is None:
            return {}
        idx = self._index

        def host_down(comp: str) -> bool:
            hosts = idx.hosts_of.get(comp)
            return bool(hosts) and all(h in failed for h in hosts)

        flows = [
            (p, t, s)
            for t in sorted(idx.all_topics)
            for p in sorted(idx.publishers_of(t) - failed)
            for s in sorted(idx.subscribers_of(t))
            if p != s and not host_down(s)
        ]
        state = evaluate(
            self._fabric, flows, idx.hosts_of,
            live_routers={t: sorted(idx.live_routers_of(t, failed)) for t in idx.all_topics},
            brokered=[t for t in idx.all_topics if idx.topic_routers.get(t)],
            topic_load={}, down=failed & self._fabric.vertices, with_load=False,
        )
        reached: Set[Tuple[str, str]] = set()
        blocked: Set[Tuple[str, str]] = set()
        for (p, t, s), loss in state.flow_loss.items():
            (blocked if loss >= 1.0 else reached).add((s, t))
        cut: Dict[str, Set[str]] = defaultdict(set)
        for s, t in sorted(blocked - reached):
            cut[s].add(t)
        return cut

    def _cascade(self, node_id: str, node_type: str, seed: int) -> "_SingleSeedResult":
        """
        Run one softened cascade simulation with a specific seed.
        
        Phase A: Stochastic propagation through DEPENDS_ON edges.
        Phase B: QoS-weighted and rate-weighted soft propagation through pub-sub channels.
        """
        rng = random.Random(seed)
        idx = self._index
        # threshold_cdf: latent per-subscriber tolerances, drawn up front in
        # sorted order so the wave draws below see the same stream position
        # whatever the cascade does.
        theta: Dict[str, float] = {}
        tested_at: Dict[str, float] = {}
        if self.failure_model == "threshold_cdf":
            lo, hi = self.threshold_range
            theta = {s: lo + (hi - lo) * rng.random() for s in sorted(idx.all_subscribers)}

        failed_nodes: Set[str] = {node_id}
        orphaned_topics: Set[str] = set()
        directly_orphaned: Set[str] = set()
        impacted_subscribers: Set[str] = set()
        waves: List[CascadeWave] = []
        wave_idx = 0

        # Helper to calculate publisher rate_hz
        def get_rate_hz(pub, topic):
            edge_data = self.graph.get_edge_data(pub, topic)
            if edge_data:
                return edge_data.get("rate_hz", 10.0)
            return 10.0

        get_qos_factor = self._qos_factor

        # Network gating: feeds the live network no longer delivers count as fully
        # lost. Feeds the healthy network never delivered are not this failure's.
        cut_cache: Dict[frozenset, Dict[str, Set[str]]] = {}

        def network_cut() -> Dict[str, Set[str]]:
            if self._fabric is None:
                return {}
            key = frozenset(failed_nodes)
            if key not in cut_cache:
                now = self._network_cut(failed_nodes)
                cut_cache[key] = {
                    s: ts - self._baseline_cut.get(s, set())
                    for s, ts in now.items() if ts - self._baseline_cut.get(s, set())
                }
            return cut_cache[key]

        def feed_loss(topic_loss: Dict[str, float], cut: Dict[str, Set[str]], sub: str, topic: str) -> float:
            if topic in cut.get(sub, ()):
                return 1.0
            return topic_loss.get(topic, 0.0)

        def compute_topic_loss() -> Dict[str, float]:
            """Soft per-topic feed loss given the current `failed_nodes` state."""
            loss: Dict[str, float] = {}
            for topic in idx.all_topics:
                # A failed Topic — broker partition, or configuration corruption
                # that severs the channel itself — delivers nothing regardless of
                # how many publishers survive. Without this branch the loss was
                # derived purely from failed publishers and routers, so injecting
                # a Topic scored 0.0 and the type was unlabelled.
                if topic in failed_nodes:
                    loss[topic] = 1.0
                    continue
                # Publisher-side and router-side loss are independent ways to
                # silence the same channel, so they combine as a weakest-link
                # max() rather than as alternatives. This used to be an
                # if/else — router loss counted only for topics with no
                # publisher — which made a Broker outage invisible on every
                # topic that had a live publisher. Since every corpus topic is
                # both published to and routed, Broker labels collapsed onto
                # "does this broker happen to route one of the few
                # publisher-less topics", and three scenarios scored every
                # Broker at exactly 0.0. `FailureSimulator._impact_throughput`
                # has always used max(pub_loss, broker_loss); this is the same
                # semantics.
                #
                # Blast radius, measured A/B over all twelve cached scenarios:
                # Application (1290 nodes), Library and Topic labels are
                # bit-identical, because injecting one of those types never puts
                # a Broker in `failed_nodes`, so router_loss stays 0.0 and
                # max(pub_loss, 0) == pub_loss. Broker labels change throughout
                # (24/65 non-zero before, 65/65 after). *Node labels change too*:
                # a Node outage kills its residents via RUNS_ON (see the
                # `residents_of` branch in `_cascade`), and a resident Broker is
                # exactly what makes router_loss non-zero — so this is the one
                # injected type besides Broker whose labels move.
                # `results/passive_stratum_labels.json` was regenerated for that
                # reason; no manuscript number depends on it.
                publishers = idx.publishers_of(topic)
                if publishers:
                    # Sorted for the same reason the subscriber loop below is:
                    # float addition is not associative, so summing an unordered
                    # set gives a result whose last bits depend on
                    # PYTHONHASHSEED. Those bits decide `sub_loss >= threshold`
                    # for any subscriber sitting exactly on the boundary — a
                    # k/n feed-loss fraction lands on 0.2 exactly — and a flipped
                    # comparison shifts every subsequent RNG draw in the wave.
                    ordered_pubs = sorted(publishers)
                    total_rate = sum(get_rate_hz(p, topic) for p in ordered_pubs)
                    if total_rate > 0:
                        failed_rate = sum(get_rate_hz(p, topic) for p in ordered_pubs if p in failed_nodes)
                        pub_loss = failed_rate / total_rate
                    else:
                        failed_pubs = publishers & failed_nodes
                        pub_loss = len(failed_pubs) / len(publishers)
                else:
                    pub_loss = 0.0

                # A brokerless (DDS direct) topology has no routing tier to
                # lose, so an absent router set contributes nothing rather than
                # scoring as total loss.
                routers = idx.topic_routers.get(topic, set())
                router_loss = (
                    len(routers & failed_nodes) / len(routers) if routers else 0.0
                )

                loss[topic] = max(pub_loss, router_loss)

                # Apply QoS factor and clamp to [0, 1]
                loss[topic] = min(1.0, loss[topic] * get_qos_factor(topic))
            return loss

        frontier = [node_id]

        # Wave 0 for a host outage: every component deployed on the Node dies
        # with it, simultaneously. This is the RUNS_ON blast of Rule 6, and it is
        # the only failure mode in this engine that starts with more than one
        # dead component. For every other injected type `residents_of` is empty,
        # so this cannot perturb their labels. Replica-aware, a resident with a
        # live replica on another host survives.
        if node_type == "Node":
            residents = idx.residents_of(node_id) - failed_nodes
            if self.replica_aware:
                residents = {r for r in residents
                             if all(h in failed_nodes for h in idx.hosts_of[r])}
            residents = sorted(residents)
            failed_nodes.update(residents)
            frontier.extend(residents)

        while frontier:
            if self.cascade_depth_limit and wave_idx >= self.cascade_depth_limit:
                break
            
            # Stochastic dampening factor based on depth
            depth_damp = max(self.depth_damp_floor, 1.0 - wave_idx * self.depth_damp_step)

            next_frontier = []
            wave_new_orphaned: Set[str] = set()
            wave_new_impacted: Set[str] = set()
            wave_new_failed: List[str] = []

            # --- Phase A: Direct DEPENDS_ON propagation ---
            for u in frontier:
                for v, _, data in self.graph.in_edges(u, data=True):
                    if v in failed_nodes:
                        continue
                    
                    edge_type = (data.get("type") or data.get("etype") or "").upper()
                    if edge_type != "DEPENDS_ON":
                        continue

                    # Library dependencies fail deterministically (shared blast
                    # radius); all other DEPENDS_ON edges are unaffected here —
                    # non-library propagation happens via topic-mediated Phase B.
                    dep_type = data.get("dependency_type")
                    if dep_type == "app_to_lib" or idx.node_type.get(u) == "Library":
                        failed_nodes.add(v)
                        wave_new_failed.append(v)
                        next_frontier.append(v)

            # --- Phase B: Topic-mediated Soft QoS/Rate-weighted Propagation ---
            # 1. Compute soft topic feed loss based on failed publishers
            topic_loss = compute_topic_loss()
            cut = network_cut()

            # 2. Update orphaned/directly_orphaned sets based on fractional feed loss
            for topic, loss in topic_loss.items():
                if loss > 1e-6 and topic not in orphaned_topics:
                    orphaned_topics.add(topic)
                    wave_new_orphaned.add(topic)
                    if wave_idx == 0:
                        directly_orphaned.add(topic)
                    
                    # Track impacted subscribers
                    for sub in idx.subscribers_of(topic):
                        if sub in failed_nodes:
                            continue
                        if sub not in impacted_subscribers:
                            wave_new_impacted.add(sub)
                            impacted_subscribers.add(sub)
            for sub in sorted(cut):
                if sub not in failed_nodes and sub not in impacted_subscribers:
                    wave_new_impacted.add(sub)
                    impacted_subscribers.add(sub)

            # 3. Stochastic failure of impacted subscribers based on continuous average feed loss
            # Sorted because `all_subscribers` is a set: iteration order over a
            # set is salted by PYTHONHASHSEED, and each iteration below consumes
            # a draw from `rng` — so an unsorted iteration would hand different
            # subscribers different draws across processes even at a fixed
            # seed, making I*(v) irreproducible. See failure_simulator.py's
            # `_derive_seed` docstring and `simulate_exhaustive`'s `sorted(...)`
            # for the same defense in the sibling engine.
            for sub in sorted(idx.all_subscribers):
                if sub in failed_nodes:
                    continue
                all_feeds = idx.app_subscribes.get(sub, set())
                if not all_feeds:
                    continue
                sub_loss = sum(feed_loss(topic_loss, cut, sub, t) for t in sorted(all_feeds)) / len(all_feeds)
                
                if self.failure_model == "threshold_cdf":
                    if (sub_loss > 1e-6 and sub_loss >= theta[sub]
                            and sub_loss > tested_at.get(sub, -1.0) + 1e-12):
                        tested_at[sub] = sub_loss
                        if rng.random() < depth_damp:
                            failed_nodes.add(sub)
                            wave_new_failed.append(sub)
                            next_frontier.append(sub)
                elif sub_loss >= self.propagation_threshold and sub_loss > 1e-6:
                    # Failure probability scaled by propagation_threshold
                    prob = min(1.0, sub_loss / max(1e-6, self.propagation_threshold)) * depth_damp
                    if rng.random() < prob:
                        failed_nodes.add(sub)
                        wave_new_failed.append(sub)
                        next_frontier.append(sub)

            if not next_frontier and not wave_new_orphaned:
                break
                
            waves.append(
                CascadeWave(
                    wave_index=wave_idx,
                    newly_orphaned_topics=sorted(wave_new_orphaned),
                    newly_impacted_subscribers=sorted(wave_new_impacted),
                    newly_failed_publishers=wave_new_failed,
                )
            )
            frontier = next_frontier
            wave_idx += 1

        # --- I(v) computation: Mean continuous feed loss across all subscribers ---
        # Recompute once more against the final `failed_nodes` state (including
        # subscribers failed in the last wave) rather than reusing the loop's
        # last in-progress `topic_loss`, which predates that wave's failures.
        final_topic_loss = compute_topic_loss()
        final_cut = network_cut()
        per_sub_loss: Dict[str, float] = {}
        for sub in sorted(idx.all_subscribers):
            all_feeds = idx.app_subscribes.get(sub, set())
            if not all_feeds:
                per_sub_loss[sub] = 0.0
                continue
            per_sub_loss[sub] = sum(
                feed_loss(final_topic_loss, final_cut, sub, t) for t in sorted(all_feeds)) / len(all_feeds)

        total_subs = len(idx.all_subscribers)
        # `per_sub_loss` is now insertion-ordered by sorted subscriber id, so this
        # sum is order-stable across processes as well.
        impact_score = sum(per_sub_loss.values()) / total_subs if total_subs else 0.0

        return _SingleSeedResult(
            impact_score=impact_score,
            directly_orphaned_topics=directly_orphaned,
            all_orphaned_topics=orphaned_topics,
            impacted_subscribers=impacted_subscribers,
            per_subscriber_feed_loss=per_sub_loss,
            cascade_depth=wave_idx,
            waves=waves,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Internal result container for a single seed run
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class _SingleSeedResult:
    impact_score: float
    directly_orphaned_topics: Set[str]
    all_orphaned_topics: Set[str]
    impacted_subscribers: Set[str]
    per_subscriber_feed_loss: Dict[str, float]
    cascade_depth: int
    waves: List[CascadeWave]
