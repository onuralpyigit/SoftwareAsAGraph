# Does Cascade-Aware Placement Pay Off? A Held-Out Evaluation of Pre-Deployment Component Placement for Publish–Subscribe Systems

**Status:** Stage 1 draft for the ICSA research track.
- **No result exists yet.** Every `{{artifact:key}}` is a placeholder that `reproduce/reconcile_manuscript.py` will fill and check against `results/placement_study/`.
- **Authority.** The protocol is [PREREGISTRATION.md](PREREGISTRATION.md). Where this draft and the preregistration disagree, the preregistration wins.
- **Supporting docs:** structure in [outline.md](outline.md), oracle semantics in [oracles.md](oracles.md), spec priors in [priors.md](priors.md).
- `[TODO-RW]` marks a related-work statement that waits on a full-text read listed in [related-work-matrix.md §5](related-work-matrix.md#5-open-verification-tasks).

---

## Abstract

Placement tools for distributed systems optimize communication cost or analytic availability. Deploy-PS targets publish–subscribe and DDS systems, ScatterD targets avionics, and DIF is a general framework. In each, the quantity optimized is also the quantity reported. In a publish–subscribe system, a placement also decides which components fail together, and the dependency graph derived from topics and brokers carries those failures transitively.

We ask whether optimizing placement for transitive cascade impact pays off when the result is judged by an oracle it was not optimized on. We formulate placement as a mixed-integer program over a first-order cascade proxy with all-of input semantics. Every candidate is then verified on a held-out, replica-aware failure simulator with any-of input semantics. A reliability claim must clear both seed noise and the spread across the solver's tied optima.

Across {{artifact:n_scenarios}} publish–subscribe models with pre-registered resource priors, we compare cascade-aware placements, at a matched host count, against:
- cost-only placement;
- in-degree spreading;
- Kubernetes-style spreading;
- CHASE-style one-hop rules.

{{artifact:h1.headline}} {{artifact:rq1.headline}} Solver tie choice accounts for {{artifact:rq3.tie_share}} of the verification noise, and {{artifact:rq3.claim_survival}} of proxy-optimal claims survive held-out verification. We release the planner, the spec generator, the priors and the verification harness.

*`h1.headline` is selected by the decision table in [PREREGISTRATION.md §5](PREREGISTRATION.md#5-primary-hypothesis-rq2), not written after the fact.*

**Keywords:** deployment optimization, publish–subscribe, cascading failures, held-out evaluation, software architecture, pre-registration.

---

## 1. Introduction

Deciding which host each component runs on is one of the few architectural decisions that is both cheap to change before deployment and expensive to change after it. In embedded and safety-critical publish–subscribe systems, it may not be changeable at runtime at all. ROS 2 vehicles, avionics partitions and industrial control cells are examples.

Software engineering has optimized this decision for two decades. Its main levers are:
- communication cost and resource use (Deploy-PS [C2], ScatterD [C3]);
- analytic availability or reliability (DIF [C5], Meedeniya et al. [C4], the ArcheOpterix and PerOpteryx lines [A2, A3]).

**The cost-driven models favour co-location.**
- Deploy-PS charges nothing for communication between co-located participants.
- ScatterD minimizes processors and then bandwidth. On a Lockheed Martin avionics system it consolidated 14 processors into 8 and cut bandwidth by 24% [C3].
- In DIF's availability instantiation, failures live on network links, so co-locating interacting components can only remove failure exposure [C5] `[TODO-RW: confirm against DIF Fig. 5]`.

None of these models asks what the consolidated placement does when a host fails. CHASE [C1] is the counterweight. Its pairwise rule co-locates a dependent with its sponsor only when the dependent cannot tolerate the sponsor's recovery time, and it always anti-locates replicas. But CHASE reasons one hop deep.

**Publish–subscribe systems propagate failures further than one hop.** A host failure removes every component on it. Topics those components published go silent. Subscribers that depended on them stop publishing in turn. The dependency graph that carries this cascade is not fixed input: it is *derived* from topics, brokers and placement. Moving one component changes which components share fate.

That suggests a straightforward method: optimize placement for the transitive cascade impact of host and failure-domain outages. Whether it *pays off* is an empirical question, and the evidence so far is mixed.
- An automotive study found that co-locating a task with its predecessors raised MTTF [E1] `[TODO-RW: E1 full text]`.
- Our own pilot probes found that, at a fixed host count, locality mostly *lowered* expected impact, and that ranking by in-degree matched cascade-aware selection ([related-work-matrix.md §7](related-work-matrix.md#7-probe-evidence-2026-10-18); n = 3 seeds, at most 3 scenarios, not re-used here).

The question also carries a methodological trap that the prior work does not avoid. When the evaluation measure is the optimization objective, any optimizer wins by construction. CHASE, Deploy-PS and DIF all evaluate with their own objective.

**This paper** answers the question with a pre-registered, held-out study.
- **The optimizer** sees a deliberately simple proxy: first-order reachability with all-of input semantics.
- **The judge** is a different model: a replica-aware failure simulator with any-of input semantics, QoS-weighted impact and stochastic propagation.
- **A claim** must beat both the best feasible baseline and in-degree spreading by more than the larger of seed noise and the spread across the solver's tied optima.

We ask:

- **RQ1. What does cost-only placement cost in reliability, at a matched host count?**
- **RQ2. Does cascade-aware placement beat in-degree spreading on held-out impact?** This is the primary, pre-registered test, with an equivalence alternative.
- **RQ3. How noisy is verification?** How much of a measured gain is solver tie choice, how many proxy-optimal claims survive, and how well does the proxy track the oracle?
- **RQ4. When does cascade-awareness pay off?** The moderators are chain depth, replica budget, the failure-domain structure, capacity tightness and the oracle's gating rule.

**Contributions.**
1. A held-out evaluation of transitive-cascade-aware placement against cost-only, count-based, scheduler-style and one-hop baselines, on {{artifact:n_scenarios}} models.
2. A verification protocol that turns "the optimizer found something better" into a claim with a noise bar, including the solver's arbitrary pick among tied optima.
3. {{artifact:c3.sentence}}
4. An open artifact: `saag-deploy`, priors, generated specs, the runner and the analysis.

{{artifact:intro.outcome_paragraph}}

## 2. Motivating Example

Our hand-authored model of an Autoware (ROS 2) vehicle stack has 32 applications, 3 brokers and 24 topics on six ECUs. It is a model of the system's communication structure, not a running Autoware deployment. Its authored placement looks like this:

- **ECU-Sensing-FPGA:** the sensor drivers (`velodyne_node_container`, `camera_driver_node`, `gnss_poser_node`, `imu_corrector_node`, `radar_tracks_msgs_converter`) and `pointcloud_preprocessor`.
- **ECU-Perception-GPU:** perception (`lidar_centerpoint_node`, `multi_object_tracker`, `detected_object_validation`, `occupancy_grid_map_node`).
- **ECU-Main-Brain:** 16 components, mostly localization, planning and control, plus the system monitor and the DDS router.
- **ECU-Vehicle-Actuation:** the command gate, the CAN interface and the emergency handlers.

**The placement freedom is narrower than a scheduler's view suggests.** A sensor driver cannot leave the ECU wired to its sensor, and `lidar_centerpoint_node` needs the GPU, so those are pinned ([priors.md §1](priors.md#1-primary-cell-h1-is-tested-here-only)). The real decision is how to place the 16-component planning and control stack that the authored placement consolidates on one ECU. Consolidation is cheap in traffic. Whether it is cheap in failure impact depends on how far a failure of ECU-Main-Brain propagates and which components survive elsewhere.

**The example also shows why the oracle's input semantics matter.** `multi_object_tracker` subscribes to both lidar-based objects and radar objects.
- Under the proxy's all-of semantics, losing the lidar path kills it.
- Under the simulator's any-of semantics, the radar feed keeps it alive.

A placement optimized under one assumption and judged under the other can lose its advantage. This is the gap our verification protocol measures (RQ3).

**Fig. 1** (to draw): the authored placement, with the cascade of an ECU-Main-Brain outage under each semantics. The values come from {{artifact:fig1.*}}.

## 3. Background and Related Work

**SE deployment optimization.**
- DIF [C5] places components onto hosts for several QoS dimensions, availability among them, using exact (MIP), greedy and genetic solvers, and it was evaluated on two deployed application families. Its interactions are fixed input, its availability model counts link failures, and its utility is also its evaluation measure.
- Meedeniya et al. [C4] optimize reliability-driven deployment for embedded systems `[TODO-RW: C4 full text, paywalled]`.
- Nazari et al. [A4] give a MILP for the same problem family.
- Aleti et al. survey the field [A1].

We do not claim a new optimization framework. A reviewer could reasonably call a cascade-impact objective "another QoS function inside DIF". What this paper adds is the *measurement*: a derived dependency graph that changes with the placement, correlated host and domain failures, transitive propagation, and a held-out verdict with a noise bar.

**Publish–subscribe deployment.**
- Deploy-PS [C2] generalizes the authors' HLA and DDS deployment work (S-IDE) to publish–subscribe systems. It compiles topic relations into communicating pairs, minimizes a single summed cost with zero cost for co-located pairs, and has no failure model.
- Pongthawornkamol et al. [F8] analyze the reliability and timeliness of fault-tolerant pub-sub systems analytically `[TODO-RW: F8 full text]`.

**Embedded consolidation.** ScatterD [C3] consolidated a real avionics system with no failure model. It never claimed to address reliability. Our point is that consolidation success stories leave failure impact unmeasured, not that ScatterD was wrong.

**Availability-aware cloud and fog placement.**
- CHASE [C1] schedules multi-tier applications with a one-hop co-location rule.
- INDaaS [F11] audits whether redundant deployments are independent.
- F2 (Lera et al.) and F4 (Zhang et al.) use transitive closures and dependency-graph "critical nodes" respectively `[TODO-RW: F2, F4 full texts; until then no "first" claim]`.
- Replica placement under hierarchical correlated failure is studied in [F9].

**Practice.** The Kubernetes default scheduler is traffic-unaware. Its default topology spreading and least-allocated scoring bias it toward *spreading*, and co-location needs explicit pod affinity [K1]. `[TODO-RW: cite the scheduler documentation version.]`

**Table 1** condenses the positioning matrix ([related-work-matrix.md §2](related-work-matrix.md#2-positioning-on-the-dimensions-we-care-about)).

## 4. Approach

### 4.1 System and spec model

The topology is the structural graph of the SaG model:
- Applications, Libraries, Brokers, Topics and Nodes;
- edges PUBLISHES_TO, SUBSCRIBES_TO, ROUTES, USES, RUNS_ON and (optionally) CONNECTS_TO.

The topology carries no resource data, so a separate *deployment spec* supplies it ([docs/deployment.md §2](../../deployment.md#2-deployment-spec)):
- per-host capacity, failure probability and failure domain;
- per-domain failure probability;
- per-component demand, replica count, and allowed or forbidden hosts;
- latency;
- anti-affinity and a utilization cap.

Memory demand includes each subscribed topic's history buffer, history depth × message size.

### 4.2 Placement as a structural edit

A placement assigns each component one or more hosts, with one primary. It is an edit of the RUNS_ON edges. The derived dependency graph is a function of the structural graph, so it changes with the placement. This is why the dependency structure is not fixed input here, unlike in DIF, CHASE or Deploy-PS.

### 4.3 The cascade proxy

**Failure events.** Every single host, plus every failure domain with at least two hosts. Each event *e* has probability *p(e)* from the spec.

**When a component is down in *e*.**
- Its own copies all lie inside *e*, or
- it transitively depends on a component that is down. The proxy uses the placement-independent arcs `app_to_app` and `app_to_broker`.

This is **all-of** semantics: one lost input kills the dependent.

**The proxy's two values:**
- blast_exp = Σ_e p(e) · |Applications down in e|;
- blast_max = max_e |Applications down in e|.

### 4.4 Exact formulation

The binary variables are:
- x_{c,n}: a copy of c runs on host n;
- y_{c,n}: c's primary runs on n.

The continuous variable z_{e,c} ∈ [0,1] means "c is down in e". Its coverage constraints are:

  z_{e,c} ≥ Σ_{n∈e} x_{c,n} − (r_c − 1),  z_{e,v} ≥ z_{e,u} for every dependency arc v → u.

Under minimization these are exact: `tests/test_deployment_milp.py` checks them against brute force.

**Hard constraints:**
- replica counts;
- domain or host anti-affinity;
- CPU and memory capacity;
- failover-safe topic deadlines, meaning every publisher copy reaches every subscriber copy through every routing-broker copy within the deadline;
- optionally, using every host.

**Traffic.** Traffic is cross-host bytes per second. It uses linear indicators over products of binaries. These McCormick-style constraints are exact for binaries, not a relaxation.

**Solving.**
- The front between blast_exp and traffic is traced with ε-constraints, not weighted sums, so unsupported Pareto points are not lost.
- Brokers are decision variables in the first stage of each solve and fixed afterwards.
- Up to five further placements tied at the selected proxy value are enumerated with no-good cuts.

**Scale.** The exact model is solved with HiGHS up to 16 hosts. Beyond that, an incremental local search with swap and primary-rotation moves is used, and no optimality is claimed. A scaling probe found no incumbent at 32 or 48 hosts within 600 s ([docs/deployment.md §3](../../deployment.md#3-optimization-model)).

### 4.5 Held-out verification

Every candidate and every baseline is re-scored on oracles the optimizer never sees ([oracles.md](oracles.md)).

- **O1** is a replica-aware failure simulator over the raw structural edges. It never reads derived dependencies. Its semantics are:
  - **any-of:** a subscriber survives while any feed survives;
  - **stochastic** edge propagation;
  - **QoS-weighted** composite impact in [0, 1].
- **O2** is the same simulator with k-of-n (majority) gating.
- **Not confirmatory:** an all-of variant reproduces the proxy's semantics, so it is reported only as a proxy-aligned bound.

**Noise bar.** For a candidate X against a baseline B, the bar is

  η = max( κ · √(σ_X² + σ_B²), tie spread ),

where σ is the standard deviation over seeds and the tie spread is the held-out range across the proxy-tied optima.

**Claim rule.** A reliability claim requires beating the best feasible baseline *and* in-degree spreading by more than η.

**Fig. 2** (to draw): the plan → verify → recommend pipeline.

## 5. Study Design

The full protocol is [PREREGISTRATION.md](PREREGISTRATION.md). In summary:

**Population.** 17 scenarios in three blocks:
- 11 generated evaluation scenarios;
- the ATM case study, with its size variants collapsed to one unit;
- 5 hand-authored models of open-source systems: Autoware, an e-commerce cloud microservice mesh, EdgeX, Home Assistant and TrainTicket. TrainTicket is RPC-based in reality, and its model abstracts its calls as topics.

**Unit of analysis.** The scenario: the median over 5 generated specs of the mean over 10 seeds.

**Priors.** [priors.md](priors.md).
- Demands are drawn independently of the topology, so the capacity model does not quietly favour in-degree spreading.
- Capacities are sized to 70% utilization, and every host is used.
- Hosts are grouped into domains of two, with r = p_domain / p_host = 0.25.
- Hardware-bound components in the hand-authored models are pinned.

**Arms** (Table 3):
- **CASCADE:** minimum blast_exp subject to traffic ≤ 1.10 × traffic of `k8s_spread`. It is scored as the mean over its tied optima.
- `indeg_spread`: the primary comparator.
- `k8s_spread`.
- `ffd`.
- `chase`.
- `deploy_ps`: traffic-only.
- `scatterd`: minimum hosts, then traffic. It uses fewer hosts, so it is descriptive.
- `random`: the mean over 50 feasible placements.

**Primary test.** A one-sided Wilcoxon signed-rank test on d_s = (B_s − X_s)/B_s, plus an equivalence test (TOST) within ±δ.
- δ is fixed from pilot *noise*: {{artifact:prereg.delta}}.
- The three possible outcomes (superior, equivalent, inconclusive) are mapped to headline sentences in advance.

**Secondary family.** Six contrasts under Holm. Beyond-noise win/tie/loss counts. RQ3 is descriptive and RQ4 is exploratory.

## 6. Results

*Written only after `reproduce/placement_significance.py` has run on the frozen protocol.*

### 6.1 RQ1: The reliability cost of cost-only placement

{{artifact:rq1.text}}
- **Fig. 3:** E[O1] against host count for `scatterd`, `ffd` and `deploy_ps`.
- **S6:** {{artifact:s6.result}}.

### 6.2 RQ2: Does cascade-awareness pay off?

H1: HL = {{artifact:h1.hl}} [{{artifact:h1.ci}}], one-sided p = {{artifact:h1.p}}, TOST p = {{artifact:h1.tost_p}}, rank-biserial r = {{artifact:h1.rbc}}. The outcome is **{{artifact:h1.outcome}}**.

Beyond-noise W/T/L vs `indeg_spread`: {{artifact:h1.wtl}}.

**Table 5:** S1–S5, Holm-adjusted. **Fig. 4:** the paired d_s with the ±δ band.

### 6.3 RQ3: How noisy is verification?

{{artifact:rq3.text}}
- **Fig. 5:** the η decomposition, claim survival, and ρ(proxy, O1) and ρ(proxy, O2) per scenario.

### 6.4 RQ4: When does it pay off?

{{artifact:rq4.text}}
- **Fig. 6:** a forest plot of d against the moderators and sweep factors.
- CHASE co-location share: {{artifact:rq4.chase_colocate_share}}.

## 7. Discussion

*Keyed to the decision-table outcome.*

- **If equivalent:** an architect gains nothing measurable from a cascade model over the much simpler in-degree spreading rule. The place to spend effort is the replica budget and the failure-domain layout.
- **If superior:** the moderators of RQ4 say where the gain concentrates.

**Either way, a planner should report** its noise bar, its tie spread and its proxy–oracle correlation. A placement tool that does not is reporting its own objective back to the user.

## 8. Threats to Validity

**Construct.**
- **The oracles are internal.** O1 and O2 were written by the same team as the proxy, over the same structural model. They differ from it in gating, magnitude and stochasticity, but they share its assumptions: crash-stop failures, no host queueing, and routing through the first broker. Results hold *relative to these two cascade models*. External grounding is the TOSEM extension's testbed ([tosem-extension.md](tosem-extension.md)).
- **The priors are synthetic.** No model carries measured resource or failure data. The priors are frozen before any run, swept in RQ4, and their correlation with in-degree is disclosed ([priors.md §3](priors.md#3-correlation-disclosure-required-in-the-paper)).
- **The gating mismatch** between the proxy and O1 is a designed property, but it also caps how well any proxy optimum can transfer. RQ3 reports it.

**Internal.**
- **The MILP gap.** Exact solving stops near 16 hosts, so larger scenarios use a heuristic with no optimality guarantee. The MILP gap is a covariate, and a sensitivity analysis excludes time-limited instances.
- **Tie enumeration** stops at five placements, so the tie spread is a lower bound.

**External.**
- **One generator family** produced 11 of the 17 scenarios. A leave-one-block-out analysis is pre-registered.
- **The five hand-authored models** are models, not deployments.
- **Failure modes** are host and domain crash-stop only. There are no gray failures.

**Conclusion validity.**
- **n = 17** gives a one-sided floor of p = 7.6e-06. The hand-authored block alone (n = 5) can reach one-sided p = 0.031 only if all five scenarios win, so it is reported descriptively.
- **Multiplicity.** Holm is applied across the secondary family, and there is exactly one primary test.

## 9. Conclusion

{{artifact:conclusion.text}}

The TOSEM extension replaces the internal oracles with fault injection on a containerized publish–subscribe testbed and the synthetic priors with measured footprints ([tosem-extension.md](tosem-extension.md)).

---

## References (keys from the related-work matrix; full entries in [related-work-matrix.md §1](related-work-matrix.md#1-matrix))

- [A1] Aleti et al., TSE 2013.
- [A2] ArcheOpterix, 2009.
- [A3] PerOpteryx, QoSA-ISARCS 2011.
- [A4] Nazari et al., JORS 2016.
- [C1] CHASE, IEEE CLOUD 2015.
- [C2] Deploy-PS, 2017.
- [C3] ScatterD, CrossTalk.
- [C4] Meedeniya et al., JSS 2011.
- [C5] DIF, TSE 2012.
- [E1] arXiv 2305.07401.
- [F2] Lera et al., IEEE IoT J. 2019.
- [F4] Zhang et al., arXiv 2405.18001.
- [F8] Pongthawornkamol et al., ICAC 2013.
- [F9] arXiv 1701.01539.
- [F11] INDaaS, OSDI 2014.
- [K1] Kubernetes scheduler documentation.
