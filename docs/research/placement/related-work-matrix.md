# Related-Work Matrix: Graph-Based Pre-Deployment Placement

Working notes for a candidate paper on placing applications onto nodes under resource
constraints and topic-based (pub-sub) dependencies, with the goal of improving reliability,
availability and fault tolerance **before deployment**.

> **Status (2026-10-08): preliminary.** Built from one web-search pass. Rows marked ✅ were
> read in full (so far C1, C2, C3 and C5). All other rows were read **at abstract or catalog level only**. A cell marked `n/v` means *not verified*: the
> abstract did not say, and the full text has not been read. Before citing any row, read
> the full text and promote it to ✅ in the *Verified* column. Not finding a gap in this
> pass does **not** mean the gap exists (see [§5](#5-open-verification-tasks)).

---

## 1. Matrix

Column legend:
- **Graph model**: what the paper represents as a graph, if anything.
- **Decision**: the variable being optimized.
- **Objective(s)**: what is optimized.
- **Failure model**: how failures are represented.
- **Method**: how the placement is computed.
- **Evaluation**: how the result is checked.
- **Real sys.**: whether a real or industrial system was used.

### 1.1 Closest competitors (must be positioned against explicitly)

| # | Work | Venue / year | Domain | Graph model | Decision | Objective(s) | Failure model | Method | Evaluation | Real sys. | Verified |
|---|---|---|---|---|---|---|---|---|---|---|---|
| C1 | Jammal, Kanso & Shami, **CHASE: Component High Availability-Aware Scheduler in Cloud Computing Environment** | IEEE CLOUD 2015, pp. 477–484 (DOI 10.1109/CLOUD.2015.70) | Cloud IaaS, multi-tier web apps (OpenStack) | **No explicit graph.** UML class model with component types, redundancy groups (active/standby) and two dependency kinds: *sponsoring* (functional, e.g. App→DB) and *synchronization* (active↔standby). **Dependencies are fixed tenant input** | Component → VM → server, within delay zone D0–D4 (VM / server / rack / DC / inter-DC) | Maximize component availability (high MTTF, low MTTR) subject to CPU-core and memory capacity, network delay, co-location/anti-location | Hierarchical failure scopes (VM, server, rack, DC), each with MTTF/MTTR/recovery time, so failures are correlated through shared infrastructure. Failover to redundant replicas. **One-hop sponsor→dependent impact only; no transitive cascade** | Criticality ordering, then greedy filter pipeline: capacity → delay → availability (co-/anti-location, pick max-availability server) → redundancy placement → VM mapping. Locally optimal. MILP in the earlier ICC 2015 "digest" paper | **Analytic downtime from sampled MTTF/MTTR** (no fault injection). Small: 20 comps / 2 DCs / 4 racks / 50 servers / 2 apps; within 10% of MILP; downtime −48/−34/−31% (D3/D2/D0–D1) and −94% (D4) vs Nova core/RAM filters. Large: 100 comps / 4 DCs / 16 racks / 1000 servers / 10 apps; 99.981–99.99% availability vs 99.07–99.27% for a redundancy-agnostic scheduler | ~ OpenStack + Eclipse/Papyrus prototype that is **demonstrated, not evaluated**; all results synthetic | ✅ |
| C2 | Tekinerdogan & Celik, **Architecting Feasible Deployment Alternatives for Publish-Subscribe Systems** (Deploy-PS) | Int. J. Comput. Softw. Eng. 2:117, 2017, 11 pp., open access (DOI 10.15344/2456-4451/2017/117). Generalizes their HLA work (S-IDE, JSS 86, 2013; TOMACS 23(3), 2013) and Deploy-DDS (ECSA Workshops 2014) | Generic pub-sub (HLA, DDS via OMG UML profile) | **EMF metamodel, not a graph.** Four parts: application (participants, data-exchange types, pub/sub relations), physical resources (nodes, processors, network), execution configuration (instance counts, publication update rates, per-node execution cost), deployment. Pub/sub relations are turned into a **static** set E of communicating participant pairs | Participant instance → node (binary a_ip) | Capacitated task allocation: **min Σ_i Σ_p a_ip·x_ip + Σ_(i,j)∈E Σ_p a_ip(1−a_jp)·c_ij**, s.t. each participant on exactly one node and Σ_i m_i·a_ip ≤ M_p. c_ij grows with communication frequency and is **zero when i, j share a node**. CPU power C_p appears in the prose but **not in the formulation** | **None.** No failure, reliability or availability term anywhere; "redundancy" appears only as a generic benefit in the introduction | Authors explicitly do not design an algorithm. A **genetic-algorithm heuristic** is the "sample realization" (stated in related work only). Eclipse/EMF/GMF tool with 8 sub-tools (design, generation, analysis, comparison) | One synthetic case: simulated city, **1390 participants on 4 nodes**, vs **one** manual expert deployment: −15% total execution cost, −25% total memory. No communication-cost result, no runtime, no repetitions | — (simulation case study; earlier traffic, electronic-warfare and DDS ATMS cases are cited from prior work, not re-reported) | ✅ |
| C3 | Dougherty, White, Schmidt (Vanderbilt / Virginia Tech), Kegley & Preston (Lockheed Martin Aeronautics), **Deployment Optimization for Embedded Flight Avionics Systems** (ScatterD) | CrossTalk (issue n/v; author copies © 2010). Archival algorithm paper: White, Dougherty, Thompson & Schmidt, *ScatterD: Spatial deployment optimization with hybrid heuristic/evolutionary algorithms*, ACM TAAS 6(3):1–25, 2011 (DOI 10.1145/2019583.2019585; abstract only, objective there is **power**) | Integrated avionics on a fighter aircraft: ARINC 653 time/space partitioning, hard real-time harmonic periods, pub/sub messaging replacing cyclic executives | **No explicit graph.** Tasks with processor/memory demands and real-time periods, plus pairwise communication volumes. Bandwidth is consumed **only by communicating pairs that are not co-located** | Software task → processor (homogeneous processors) | **Minimize number of processors and total network bandwidth**. Constraints: rate-monotonic schedulability (response-time analysis before every placement), memory and other resources, co-location constraints, optional hardware budget. How the two objectives are combined is not stated | **None.** Fault tolerance is named only as one of the concerns that single-concern bin-packing handles; network saturation is cited as a cause of catastrophic failure, which motivates the bandwidth term. No replicas, no failure scopes | Hybrid: a metaheuristic (**GA or particle swarm**) chooses the **packing order** of a few seed tasks; heuristic bin-packing with a schedulability check then places everything, so search stays inside the feasible region. Open source (Ascent Design Studio) | One legacy production deployment: **14 → 8 processors (−42.8%)** and bandwidth **1.83·10⁸ → −4.39·10⁷ bytes (−24%)**, the same for both GA and PSO variants. Task count, runtime and repetitions not reported; no baseline other than the legacy deployment (the TAAS paper reports power gains of 6–240% over bin-packing, GA and PSO) | ✓ real Lockheed Martin avionics deployment data (via the SPRUCE challenge portal). Analysis is offline; the article does not report the optimized deployment being run | ✅ |
| C4 | Meedeniya, Buhnová, Aleti & Grunske, **Reliability-driven deployment optimization for embedded systems** | JSS 84(5):835–846, 2011 (DOI 10.1016/j.jss.2011.01.004; metadata confirmed via OpenAlex). Swinburne CS3 + Masaryk University. **Paywalled: no open-access copy found (2026-10-08)** | Embedded / automotive | Software and hardware architecture annotated with reliability attributes | Component → hardware node (hardware fixed) | Per-service reliability (multi-objective) | Per-service reliability; propagation n/v | Evolutionary algorithm, Pareto set | n/v | n/v | ❌ |
| C5 | Malek, Medvidovic & Mikic-Rakic, **An Extensible Framework for Improving a Distributed Software System's Deployment Architecture** (DIF; tool DeSi; middleware Prism-MW) | IEEE TSE 38(1):73–100, 2012 (DOI 10.1109/TSE.2011.3). Precursors: DeSi (Component Deployment 2004), decentralized availability redeployment (Component Deployment 2005), WOSS 2004 | Mobile/pervasive distributed systems (emergency response, wireless sensor monitoring) | **Set-based model, not a graph:** hosts, components, physical network links, **logical interaction links** (frequency, event size), services, users. Interactions are fixed input; only their *cost* depends on where the endpoints sit | Component → host (binary x_ch, search space size H^C (hosts^components)), with `loc` (allowed hosts) and `colloc` (must / must-not share a host, e.g. to keep primary and backup apart) constraints. Initial deployment **and** runtime redeployment | Maximize overallUtil, the sum of user utility functions over (service, QoS dimension). QoS set is open; the paper instantiates **availability, latency, communication security, energy**. Constraints are generic; memory is the worked example | **Availability = fraction of successfully completed inter-component interactions**, driven by **network-link reliability** (stated as the main failure source in mobile systems). The exact formula is in Fig. 5, an image that did not extract. Host failures are not modelled in the instantiation; no propagation along interactions | MINLP; MIP linearization with C²·H² auxiliary variables plus a branching-priority heuristic; greedy (most important service first, with a component-swap heuristic); GA with genes grouped by service and crossover only at service borders; parallel GA. Separate analysis of redeployment time vs utility gain | Objective = evaluation measure (the same analytic models). Synthetic DeSi scenarios, 35 problems per size: greedy/GA within **10.8 ± 2.3% / 9.6 ± 3.1%** of the MIP optimum over 105 problems; MINLP failed on ~20% of problems beyond 20 components / 10 hosts. Compared against an *unbiased average* of 10,000 random valid deployments. Field: MIDAS (30 components) GA in ~40 s vs 4.5 h manual, solutions 23% better than manual; runtime redeployments gave ~30% response-time and ≥40% energy improvements; EDS up to 105 components / 41 hosts, GA ~4 min, greedy ~5 min | ✓ two third-party application families (MIDAS, EDS) actually deployed and redeployed on Prism-MW | ✅ |

### 1.2 Architecture-optimization tools (SE baselines)

| # | Work | Venue / year | Domain | Graph model | Decision | Objective(s) | Failure model | Method | Evaluation | Real sys. | Verified |
|---|---|---|---|---|---|---|---|---|---|---|---|
| A1 | Aleti, Buhnova, Grunske, Koziolek & Meedeniya, **Software Architecture Optimization Methods: A Systematic Literature Review** | IEEE TSE 39, 2013 (DOI 10.1109/TSE.2012.64) | Survey (188 papers) | — | — | — | — | Taxonomy | — | — | ❌ (volume/pages need checking) |
| A2 | Aleti, Björnander, Grunske & Meedeniya, **ArcheOpterix** | ICSE 2009 workshop (MOMPES vs. WoSQ: sources disagree) | Embedded, AADL models | AADL architecture | Deployment | Multiple quality attributes | n/v | Evolutionary, Pareto | Experiments from initial deployments | n/v | ❌ |
| A3 | Koziolek, Koziolek & Reussner, **PerOpteryx** (tactics-guided) | QoSA-ISARCS 2011, pp. 33–42; earlier Martens et al., WOSP/SIPEW 2010 | Component-based (Palladio models) | Palladio Component Model | Allocation, server configuration, component selection | Performance, reliability, cost | Model-based reliability (PCM) | NSGA-II with architectural tactics | Business reporting system, ABB control system | ✅ ABB | ❌ |
| A4 | Nazari, Thiruvady, Aleti & Moser, **A mixed integer linear programming model for reliability optimisation in the component deployment problem** | J. Oper. Res. Soc. 67(8):1050–1060, 2016 (DOI 10.1057/jors.2015.119) | Component deployment (same group as C4) | n/v | Component → hardware host | Reliability | n/v | **MILP** (linearizes the non-linear reliability function); benchmarked against earlier heuristics/metaheuristics | n/v | n/v | ❌ |

### 1.3 Automotive and embedded deployment

| # | Work | Venue / year | Decision | Objective(s) | Failure model | Method | Note for us | Verified |
|---|---|---|---|---|---|---|---|---|
| E1 | **Reliability Analysis of Gracefully Degrading Automotive Systems** (arXiv 2305.07401) | arXiv, 2023 | Task → ECU | MTTF | Exposure to failure sources | "Predecessor heuristic": put a task on the ECU that hosts its predecessors | **Co-location *raised* MTTF.** This contradicts spread-by-default and goes to the heart of our co-location vs isolation question | ❌ |
| E2 | Klobedanz et al., **Fault-tolerant deployment of real-time software in AUTOSAR ECU networks** | IESS 2013 | SW component → ECU, plus reconfiguration | Fault tolerance (compensating ECU failures) | ECU failure | Initial deployment + reconfigurations computed at design time | Pre-deployment, but no dependency cascade | ❌ |
| E3 | Mahmud et al. (Mälardalen/KTH), fault-tolerant AUTOSAR allocation | n/v | SW → ECU | Power, subject to timing and reliability | n/v | ILP | Strong ILP baseline formulation | ❌ |
| E4 | Automated SW→HW allocation under ISO 26262 (arXiv 2505.07881) | arXiv, 2025 | SW → HW | Development cost, critical-chain execution time | Safety levels (ASIL) and ASIL decomposition | MILP | Positions itself against C4 | ❌ |
| E5 | Daghsen et al., multi-objective AUTOSAR allocation | SAE 2012 | SW → ECU | CPU load, network load, response time | None | Multi-objective evolutionary algorithm | — | ❌ |

### 1.4 Fog, edge and cloud placement

| # | Work | Venue / year | Graph model | Objective(s) | Failure model | Method | Real sys. | Verified |
|---|---|---|---|---|---|---|---|---|
| F1 | Brogi & Forti, **QoS-aware deployment of IoT applications through the fog** (FogTorch); FogTorchΠ (ICFEC 2017); cost extension (2018) | 2017–2018 | Application components + infrastructure links | QoS assurance (latency, bandwidth), resource use, cost | Variation in link QoS (Monte Carlo) | Search for eligible deployments (NP-hard) | Smart-agriculture scenario | ❌ |
| F2 | Lera, Guerrero & Juiz, **Availability-aware service placement policy in fog computing based on graph partitions** | IEEE IoT J. 6(2):3641–3651, 2019 (DOI 10.1109/JIOT.2018.2889511) | Device communities; application transitive closures | Availability + QoS | Device unavailability | Community detection + first-fit decreasing (per a third-party summary) | Simulation (n/v) | ❌ |
| F3 | **Reliability-aware proactive placement of microservice-based IoT applications in fog computing** | IEEE TMC, 2024 (DOI 10.1109/TMC.2024.3394486) | k-out-of-n serial-parallel service model | Reliability vs cost | **Independent and correlated** failures | Hybrid particle swarm + NSGA-II; Monte Carlo reliability | n/v | ❌ |
| F4 | Zhang, Chen, Lu & Huang, **Network-aware reliability modeling and optimization for microservice placement** (arXiv 2405.18001) | arXiv, 2024 | Microservice dependency graph + network paths; "critical nodes" | Reliability (up to 29% fewer failures reported); bandwidth | Node and path failures; backup paths | Integer non-linear program, SRP/SRP-S heuristics | Simulation | ❌ |
| F5 | Cheng, Nguyen & Bhargava, **Resilient edge service placement under demand and node failure uncertainties** (arXiv 2107.04748) | arXiv | n/v | Robust cost | Node failures | Adaptive robust optimization | n/v | ❌ |
| F6 | **IRENE** | CLOSER 2020 | n/v | Availability, interference | n/v | Genetic algorithm | n/v | ❌ |
| F7 | Le Duc et al., survey of ML for reliable resource provisioning in edge-cloud | ACM CSUR, 2020 | — | — | — | Survey | — | ❌ |
| F8 | Pongthawornkamol, Nahrstedt & Wang, **Reliability and timeliness analysis of fault-tolerant distributed publish/subscribe systems** | USENIX ICAC 2013 | Pub-sub (analytic model) | Reliability and timeliness; covers broker deployment and reliability optimization (per abstract) | Component failures + recovery | Analytic model | n/v | ❌ |
| F9 | **Algorithms for optimal replica placement under correlated failure in hierarchical failure domains** (arXiv 1701.01539) | arXiv, 2017 | Hierarchical failure domains | Explicit objective for replica placement under correlated failure | Correlated (domain) failures | n/v | n/v | ❌ |
| F10 | **Failure-resilient and carbon-efficient deployment of microservices over the cloud-edge continuum** (arXiv 2601.04123) | arXiv, 2026 | n/v | Resilience + carbon | Historical failure data → soft affinity / anti-affinity / avoid constraints | Constraint generation, then placement | n/v | ❌ |
| F11 | Zhai, Chen, Wolinsky & Ford, **Heading off correlated failures through Independence-as-a-Service** (INDaaS) | OSDI 2014 | Fault graphs over network/hardware/software dependencies; "risk groups" | Audit the independence of redundant deployments (no placement search) | Hidden shared dependencies | Pluggable dependency acquisition + auditing | ✓ three case studies | ❌ |

### 1.5 Learned (GNN / RL) placement

| # | Work | Venue / year | Graph model | Method | Claim | Verified |
|---|---|---|---|---|---|---|
| L1 | Lera & Guerrero, **Multi-objective application placement in fog computing using GNN-based RL** | J. Supercomputing (Springer), 2024 | Task dependency DAG | Graph Isomorphism Network + PPO | Pareto set comparable to alternatives in milliseconds instead of hours; synthetic DAG dataset released | ❌ |
| L2 | **EP-NCO** (arXiv 2606.25553) | preprint, 2026 | Infrastructure graph + application graph | GNN + RL | 46–50% lower response time than GA/PSO (simulation) | ❌ |
| L3 | Dependent microservice deployment in multi-access edge computing (Temple) | n/v | Microservice dependencies | n/v | Handles heterogeneous servers | ❌ |

### 1.6 Practice and scheduler-level baselines

| # | Work | Relevance | Verified |
|---|---|---|---|
| K1 | Kubernetes default scheduler; `topologySpreadConstraints` and pod anti-affinity | Default "spread to limit how far a failure reaches." **Mandatory baseline** | — (documentation) |
| K2 | Resilient microservice scoring plugin (SciTePress, 2026) | Kubernetes scoring plugin over six availability metrics; replicates critical services across independent failure domains. No dependency graph (n/v) | ❌ |
| K3 | QONNECT (arXiv 2510.09851) | QoS-aware placement, migration and failover across distributed clusters | ❌ |
| K4 | Cluster-wide Kubernetes placement plugin (arXiv 2608.06987) | Argues per-pod greedy scoring is not enough and proposes a global view | ❌ |

---

## 2. Positioning on the dimensions we care about

✓ = yes, ~ = partly, — = no, n/v = not verified.

| Dimension | C1 CHASE | C2 Deploy-PS | C3 ScatterD | C4 Meedeniya | C5 DIF | A3 PerOpteryx | F3 TMC'24 | F4 Zhang | L1 GNN-RL | **Ours (target)** |
|---|---|---|---|---|---|---|---|---|---|---|
| Pub-sub / topic-level model | — | ✓ | ~ (pub-sub avionics; model is task-to-task bandwidth, no topics) | — | ~ (MIDAS is partly pub-sub; model has no topics) | — | — | — | — | **✓** |
| Resource constraints (CPU/mem) | ✓ | ~ (memory only in the formulation) | ✓ (+ real-time schedulability) | n/v | ✓ | ✓ | n/v | ~ | ✓ | **✓** |
| Explicit dependency graph | ~ (UML; one-hop sponsor relations) | ~ (static communicating-pair set) | ~ (pairwise communication volumes) | ~ | ~ (interaction links, no dependency semantics) | ~ | ~ | ✓ | ✓ | **✓** |
| Dependency graph **re-derived per placement** (co-location creates dependencies) | — | — | — | — | — | — | — | — | — | **✓** |
| Reliability/availability objective | ✓ | — | — | ✓ | ✓ (one of several QoS) | ✓ | ✓ | ✓ | n/v | **✓** |
| Correlated failures from co-location | ✓ (VM/server/rack/DC scopes) | — | — | n/v | — (failures are network links) | n/v | ✓ | ~ | — | **✓** |
| **Cascading propagation** along dependencies | ~ (one hop only) | — | — | n/v | — | — | — | ~ | — | **✓** |
| Counterfactual simulation as acceptance test (seed-noise bound) | — | — | — | — | — | — | — | — | — | **✓** |
| Pareto front over communication cost vs failure impact | — | — (single summed cost) | ~ (processors + bandwidth; combination not stated) | ✓ | ~ (scalarized by user utilities) | ✓ | ✓ | ~ | ✓ | **✓** |
| Real or industrial system | ~ (prototype, synthetic eval) | — (simulated city) | ✓ (Lockheed Martin data) | n/v | ✓ (MIDAS, EDS) | ✓ | n/v | — | — | **required** |

---

## 3. Candidate novelty claims (to be defended)

1. **Placement as a graph edit on the structural graph.** Moving an application changes a
   `RUNS_ON` edge. Re-deriving `DEPENDS_ON` (the six rules,
   [docs/graph-model.md](../../graph-model.md)) changes the failure-impact landscape. In
   C1, C2, C3 and C5 (all verified from the full text) the dependency or interaction model is
   fixed input, not a function of the placement. The same appears true of C4 and F1–F4, but
   only from their abstracts.
2. **The objective is cascade impact on the derived graph, not a per-service reliability
   formula.** C4 and A3 use analytic reliability models. F3 and F4 use path- or
   k-out-of-n-based reliability.
3. **Verify before recommending.** A candidate placement is accepted only if its reduction
   in counterfactual impact exceeds κ·σ_seed at every propagation threshold, reusing
   [EditVerifier](../../../saag/prescription/verifier.py). No row above has this acceptance
   criterion.
4. **Co-location vs isolation as an empirical question, not an assumption.** E1 (co-location
   helps) and K1 (spreading helps) disagree. Characterising *when* each wins on pub-sub
   graphs is a finding in its own right.

**C1 (CHASE) full-text verdict, 2026-10-08:**

- **Claim 1 survives.** CHASE's sponsoring and synchronization dependencies are fixed tenant
  input in a UML model. Placement never creates or removes a dependency. Co-location only
  changes which *infrastructure failure scope* two components share.
- **Claim 2 survives.** Criticality is one hop and analytic: per component `MTTF × MTTR`, and
  for a sponsor SC with dependents DeC, `Degradation = MTTF_SC × OT_DeC` and
  `Outage = MTTF_SC × (OT_DeC − MTTR_SC)`, weighted by the redundancy model. Nothing
  propagates past a direct dependent.
- **Claim 3 survives.** The evaluation computes downtime from the same MTTF/MTTR quantities
  the scheduler optimizes. There is no fault injection and no independent oracle, so the
  objective and the evaluation measure are the same thing. Name this explicitly in the paper,
  because it is the circularity we must avoid ourselves.
- **Claim 4 must engage CHASE directly.** CHASE already gives a *pairwise rule* for
  co-location vs isolation: **co-locate** a dependent with its sponsor when the dependent
  cannot tolerate the sponsor's recovery time (outage tolerance < recovery time); otherwise
  **anti-locate**, and always anti-locate redundant replicas. Our claim must be that this
  pairwise rule is wrong or insufficient on **transitive** pub-sub chains, where co-locating
  A with B also exposes everything downstream of B. That has to be shown, not asserted.
- **CHASE is also a direct baseline** (see §4). Its rule needs a per-dependent tolerance time
  and a per-sponsor recovery time. `Topic.deadline_ms` is a possible tolerance analogue. The
  only recovery time we have is the single global `mean_recovery_time` in the event
  simulator's config ([saag/simulation/models.py](../../../saag/simulation/models.py)), not
  a per-component attribute.

**C2 (Deploy-PS) full-text verdict, 2026-10-08:**

- **Claims 1–3 survive cleanly.** Pub/sub relations are compiled once into a static set of
  communicating pairs. There is no failure model of any kind and no independent evaluation:
  the "generated vs manual" comparison is scored by the tool's own cost evaluator.
- **C2 is the cost pole of claim 4, and that makes it useful.** Its objective makes
  communication cost **zero for co-located pairs** and has no failure term, so on its own it
  pushes communicating participants together. That is exactly the co-location bias whose
  failure-side price we want to measure. Framing: *Deploy-PS (and CTAP in general) is the
  cost-only end of our Pareto front; we add the failure-impact axis it lacks.*
- **Prior-work proximity.** Deploy-PS and its predecessors (S-IDE in JSS 2013, TOMACS 2013,
  Deploy-DDS at ECSA-W 2014) are Turkish/Dutch software-architecture work on pub-sub/DDS
  deployment, which overlaps your own community and target venues. Expect these authors, or
  their readers, as reviewers. Cite the whole line, not just the 2017 paper.
- **Weaknesses we can contrast with, carefully and factually:** a single 4-node case study
  against one manual baseline; no repetitions; CPU power named but not modelled; solver
  unspecified in the method section; and the deployment-alternative counts in Table 2
  (8334 / 11112 / 13890 for 1390 participants on 6 / 8 / 10 nodes) are close to
  participants × nodes, not nodes^participants. The counting function (their Fig. 3) did not
  survive text extraction, so check the figure before raising that point.

**C5 (DIF) full-text verdict, 2026-10-08:**

- **This is the strongest software-engineering precedent, and it is a TSE paper.** It already
  does placement of components onto hosts for **availability** (among other QoS dimensions),
  with exact (MIP), greedy and genetic solvers, constraints that force or forbid sharing a
  host, and **two real application families that were actually deployed**. Any claim that
  "deployment optimization for availability" is new is dead on arrival.
- **Claims 1–3 still survive.** Interactions are fixed input. Availability is the fraction
  of successful interactions given **network-link reliability**; host failures are not
  modelled and nothing propagates along interactions. The utility being optimized is also
  the evaluation measure; there is no independent check.
- **The real threat is DIF's extensibility.** DIF says any QoS dimension that can be
  quantitatively estimated can be plugged in, and its related work names stochastic
  reliability models as possible plug-ins. A reviewer can argue *"cascade impact is just
  another qValue function inside DIF."* The answer has to be that the contribution is
  **what the objective measures and what we find with it**: a dependency graph derived per
  placement, correlated host failure, transitive cascades, verification against seed noise,
  and the co-location vs isolation result. A new optimization framework is not the
  contribution. Do not pitch the search machinery as novel.
- **Claim 4 gets sharper.** In DIF's instantiation, failures live on network links, so
  putting interacting components on the same host can only remove failure exposure. Check
  this against the Fig. 5 formula, which is an image. Together with C2 (zero communication
  cost when co-located), that makes two established frameworks whose models structurally
  favour co-location. CHASE (C1) is the only one that pushes back, and only one hop deep.
- **Scope difference to state up front.** DIF handles initial deployment *and* runtime
  redeployment, including redeployment cost. Ours is pre-deployment only, so justify why
  getting the first placement right matters, e.g. safety-critical or embedded systems where
  redeploying at runtime is not allowed.
- **Reuse:** DIF's *unbiased average* (mean utility of 10,000 random valid deployments) is
  a good, citable form of our random-placement floor.

**C3 (ScatterD) full-text verdict, 2026-10-08:** (CrossTalk article only; the ACM TAAS paper
has been seen at abstract level)

- **Claims 1–3 survive cleanly.** No failure model of any kind, communication volumes are
  fixed input, and the only comparison is against the legacy deployment.
- **This is the strongest motivating example for the paper.** On a real Lockheed Martin
  avionics system, the optimizer **consolidated 14 processors into 8 (−42.8%)** and cut
  bandwidth by 24% *by co-locating communicating tasks*, with no account of what now fails
  together. ARINC 653 partitioning isolates software in time and memory but not against the
  processor itself failing. That is exactly the question our method answers: *what did the
  consolidation do to failure impact?* Present it carefully. ScatterD never claimed to address
  reliability, so the point is that the field's industrial success stories leave this
  unmeasured, not that ScatterD was wrong.
- **Claim 4: a third co-location-favouring model.** C2 (zero communication cost when
  co-located), C3 (zero bandwidth when co-located, plus minimize processors) and, pending the
  Fig. 5 check, C5 (link failures only) all push communicating components together. C1 is the
  only counterweight, and only one hop deep.
- **Possible experiment:** a ScatterD-style consolidation (minimize processors, then
  bandwidth) is a natural extra baseline. Run it on the scenario corpus and measure the
  failure impact of the consolidated placements with our simulator. If impact rises
  measurably, that is a headline result; if it doesn't, that is worth knowing early.
- **Solver idea worth borrowing:** searching over *packing orders* rather than raw
  assignments keeps every candidate feasible. It fits a constrained search where each
  candidate must also pass the counterfactual check.

---

## 4. Baselines implied by this matrix

| Baseline | Stands for | Notes |
|---|---|---|
| Kubernetes default + topology spread | K1 | Cheapest credible baseline |
| CHASE-style greedy filter pipeline | C1 | Criticality order → capacity → delay → co-/anti-location rule (tolerance vs sponsor recovery time) → max-availability host → anti-located replicas. Needs tolerance and recovery-time inputs |
| Capacitated task allocation (ILP) | C2, E3 | C2's exact objective: Σ a_ip·x_ip + Σ_(i,j)∈E Σ_p a_ip(1−a_jp)·c_ij under memory capacity, with c_ij from topic publication rates. Linearizes to a standard ILP. It is the cost-only anchor of the Pareto front, and it isolates what the failure-impact term adds |
| NSGA-II over allocations | C4, A2, A3, F3 | Same search, analytic reliability objective instead of cascade impact |
| Predecessor co-location heuristic | E1 | The opposite of spreading |
| Consolidation (min processors, then bandwidth) | C3 | ScatterD-style: GA/PSO over packing order + bin-packing with capacity checks. Tests what aggressive consolidation does to failure impact |
| Random feasible placement | C5 | Sanity floor. Report it as C5's *unbiased average*: the mean over many random valid placements, not a single draw |

---

## 5. Open verification tasks

- [x] Read the full text of C1 (CHASE). Done 2026-10-08; see §3.
- [x] Read the full text of C2 (Deploy-PS). Done 2026-10-08; see §3.
- [x] Read the full text of C3 (ScatterD, CrossTalk article). Done 2026-10-08; see §3.
- [ ] **Read C4 (Meedeniya et al., JSS 2011) through institutional access.** Closed access:
      OpenAlex and Semantic Scholar report no open PDF, Academia.edu returns 403, and the
      Swinburne repository copy has moved without a file. Save the PDF in the scratchpad or
      here and the row can be filled in. Questions the full text must answer: the per-service
      reliability model (DTMC or closed form?); whether node *and* link failures are modelled;
      whether failure propagates between services; the evolutionary algorithm used;
      constraints; the case study (automotive?), its scale and baselines.
- [ ] Also behind paywalls, and relevant once C4 is read: the same group's QoSA 2010 paper,
      "Architecture-Driven Reliability and Energy Optimization for Complex Embedded Systems"
      (DOI 10.1007/978-3-642-13821-8_6; redundancy allocation in ArcheOpterix, automotive
      case study), and Meedeniya, Aleti & Bühnová, "Redundancy Allocation in Automotive
      Systems using Multi-objective Optimisation" (2009, multi-objective ant colony
      optimization over reliability, cost and response time).
- [ ] Then read the full text of A3, E1, F3, F4. Fill in every `n/v`.
- [ ] Read ScatterD's archival paper (White, Dougherty, Thompson & Schmidt, ACM TAAS 6(3),
      2011). It is the one to cite for the algorithm. Also find the CrossTalk issue and year,
      and the related BLITZ bin-packing paper from the same group (ICSE 2009, on a production
      flight avionics system).
- [ ] Read the C2 predecessors, which are stronger venues than C2 itself:
      Çelik & Tekinerdogan, "S-IDE: A tool framework for optimizing deployment architecture
      of High Level Architecture based simulation systems," JSS 86:2520–2541, 2013;
      Çelik, Tekinerdogan & Imre, "Deriving feasible deployment alternatives for parallel and
      distributed simulation systems," ACM TOMACS 23(3), Art. 18, 2013;
      Celik, Koksal & Tekinerdogan, "Deploy-DDS," ECSA Workshops 2014, Art. 35.
- [x] Add and read DIF (C5). Done 2026-10-08; see §3.
- [ ] Recover DIF's Fig. 5 (the availability, latency, security and energy formulas, an image
      in the PDF). It decides whether co-located interactions count as always successful,
      which is the basis of the claim-4 argument in §3.
- [ ] Read DIF's availability-specific predecessor: Malek, Mikic-Rakic & Medvidovic, "A
      Decentralized Redeployment Algorithm for Improving the Availability of Distributed
      Systems," Component Deployment 2005. Also forward-snowball DIF; it is the most likely
      ancestor of any later deployment-for-availability work in TSE or ICSE.
- [ ] Look up Kang, He & Wei, "An effective iterated greedy algorithm for reliability-oriented
      task allocation in distributed computing systems," JSS 2013 (C2's reference lists the
      volume as 73, which does not match a 2013 JSS volume; verify).
- [ ] Read the CHASE predecessor: Jammal, Kanso & Shami, "High Availability-Aware
      Optimization Digest for Applications Deployment in Cloud," IEEE ICC 2015 (the MILP
      formulation and the UML model).
- [ ] Snowball the availability-aware placement work cited by CHASE. These are likely
      reviewer picks, especially the DSN ones:
      Jung et al., performance- and availability-aware regeneration (DSN 2010);
      Li et al., "Improving Availability of Cloud-Based Applications through Deployment
      Choices" (IEEE CLOUD 2013);
      Lu et al., uncertainty in deployment decisions for availability (IEEE CLOUD 2013);
      Machida et al., redundant VM placement (NOMS 2010);
      Harper et al., DynaPlan (DSN-W 2011);
      Bin et al., "Guaranteeing High Availability Goals for Virtual Machine Placement"
      (ICDCS 2011).
- [ ] Forward-snowball CHASE (who cites it, 2016 onward). Check especially for any follow-up
      that adds transitive dependencies.
- [ ] Check the TSE 2013 volume/issue/pages (A1) and the ArcheOpterix workshop venue (A2).
- [ ] Find the original 2017 FogTorch venue (F1) and E3's year and venue.
- [ ] Run a **structured search** (Scopus / IEEE Xplore / ACM DL) with a recorded
      protocol, so that the gap claims in §3 rest on more than one web pass. Suggested
      terms: `"deployment optimization" AND (reliability OR availability)`,
      `"component allocation" AND "publish-subscribe"`,
      `"correlated failure" AND placement AND dependency`, `"broker placement" AND reliability`.
- [ ] Do backward/forward snowballing from A1, C1 and C4. The A1 survey's 188 papers are the
      SE-side backbone.
- [ ] Check for **DDS- and ROS 2-specific** placement work beyond C2/C3 (for example, the
      transport-selection framework of De Marchi & Bombieri, 2024, and FogROS2-Config).
- [ ] Read F8 (Pongthawornkamol et al., ICAC 2013). It is the only pub-sub-specific
      reliability work with a deployment angle found so far (added 2026-10-18), so it is
      the most direct threat to the "pub-sub/topic-level model" row of §2.
- [ ] Read A4 (Nazari et al., JORS 2016). An exact MILP for reliability-driven component
      deployment already exists, so "we formulate an exact MILP" cannot be a contribution.

---

## 6. What the framework already provides (and lacks)

| Needed | Status in repo |
|---|---|
| `RUNS_ON` structural edges, Node layer | ✓ (`infra` layer, [saag/core/layers.py](../../../saag/core/layers.py)) |
| Re-derivation of `DEPENDS_ON` from raw edges | ✓ [SimulationGraph](../../../saag/simulation/graph.py) |
| Host-split / reallocation operator | ✓ [mutator.py](../../../saag/prescription/mutator.py), [rules.py](../../../saag/prescription/rules.py) (reactive, single edit) |
| Counterfactual acceptance test | ✓ [verifier.py](../../../saag/prescription/verifier.py) |
| Node resource capacities, application resource demands | **✗** `Node` has no attributes ([saag/core/models.py](../../../saag/core/models.py)). Adding them requires a generator change, corpus regeneration and a manifest refresh |
| Constructive (whole-system) placement search | **✗** |
| Oracle independent of the optimization objective | **✗** Needs a decision: real deployment with failure injection, or a held-out oracle |

**Boundary with AuSE:** AuSE verifies *individual edits* to an existing deployment after
the fact. This paper *synthesizes a whole placement* under resource constraints. Both
manuscripts must state this explicitly.

---

## 7. Probe evidence (2026-10-18)

Two read-only probes test claim 4 and the ScatterD question from §3 against the existing
oracles. Neither changes `saag/`, the corpus or any published label. Results are written to
`results/`, which is gitignored.

- [reproduce/placement_objective_probe.py](../../../reproduce/placement_objective_probe.py)
  places one host per component. It then scores Node injection under I* and I_comp, with
  uniform p over hosts, using 3 placement seeds.
- [reproduce/placement_redundancy_probe.py](../../../reproduce/placement_redundancy_probe.py)
  adds replicas and correlated failures. Hosts are grouped into domains of 2, and the probe
  runs on the opt-in `replica_aware` flag.

**7.1 Replica semantics decide the answer.** With the flag off (the published semantics),
both oracles kill a replicated component when *any* one of its hosts fails. A real flag run
matches an emulated all-hosts-down run exactly (max |Δ| = 0 on atm and av). The probe then
places replicas cross-domain (anti-affinity) instead of in-domain and measures the change in
I_comp, meaning cross_domain − same_domain, under domain outages:

| scenario | flag off: mean / max | flag on: mean / max |
|---|---|---|
| atm | +0.160 / +0.095 (worse, 6/6) | −0.169 / −0.142 (better, 6/6) |
| av | +0.097 / +0.063 (worse, 6/6) | −0.123 / −0.146 (better, 6/6) |

Under the published semantics, anti-affinity is scored as harmful. Any placement result
computed without the flag would recommend the opposite of standard practice.

**7.2 I\* is a weak placement objective.**
- On av, Node I* is ≈0.95 for every host and ≈0.96 for every domain, whatever the placement.
  It cannot discriminate between placements.
- Under domain outages, max I* never moves with replica policy.
- I_comp with the legacy mesh rule scores empty hosts above zero.
- Neither oracle is a clean placement score as it stands.

**7.3 Locality vs expected impact (claim 4).**

Consolidation trades expected impact for worst case:
- Consolidating onto one host gives the lowest expected I* and the highest worst-case I* on
  atm, av and realworld_autoware_ros2. On atm, E[I*] is 0.125 vs 0.532 for round-robin,
  while max I* is 1.000 vs 0.775.
- Spearman ρ(locality, E[I*]) is −0.86, −0.60 and −0.85, and ρ(locality, max I*) is +0.66,
  +0.59 and +0.61.

**Caveat.** Most of the expected-impact gain comes from *using fewer hosts*: empty hosts
contribute 0 under uniform p. That is the textbook series-system effect, not a pub-sub
finding. A claim needs a comparison at a fixed number of used hosts. The closest such
comparison, colocate vs round_robin (both use every host), favours colocation:
- under I_comp, on 3/3 scenarios (0.260 vs 0.271, 0.232 vs 0.243, 0.316 vs 0.359);
- under I*, on 2/3 scenarios (on av colocation is worse, 0.953 vs 0.914).

The effect is small.

**7.4 Does redundancy create the tension?** Only weakly.
- On atm under I* (flag on, domain outages), the ranking flips once replicas are
  anti-affine. Without replicas, colocate beats round_robin on E[I*] (0.660 vs 0.680). With
  cross-domain replicas, round_robin wins (0.609 vs 0.655) at a higher cross-domain traffic
  share (0.76 vs 0.67).
- I_comp shows no flip: colocate wins in both arms.
- The probe's cost proxy counts primary-to-primary traffic only, so it gives replica
  placement no cost at all. Anti-affinity therefore looks free. A real tension needs a cost
  for replica synchronization traffic and replica capacity.
- n = 3 seeds and 2 scenarios. Treat this as directional.

**Implications for §3.**
- Claim 4 survives only in its narrow form: *the risk measure, not the topology, decides
  whether co-location helps.* It must be shown at a fixed number of used hosts, and with a
  replica cost model.
- Every placement experiment must run with `replica_aware=True`, and the paper must say so.
- The oracle used to evaluate a placement must not be the one it was optimized on. I* is
  disqualified on av-like topologies by saturation.
