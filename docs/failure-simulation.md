# Step 5: Simulate — Pre-Deployment Failure & Event Simulation

**Generate empirical ground-truth impact metrics ($I^*(v)$, $I_{\text{comp}}(v)$, $I_{\text{dyn}}(v)$, $I_M(v)$) through controlled cascade, structural, and discrete-event simulations to train, validate, and verify architectural dependability.**

← [Step 4: Diagnose](diagnosis.md) | [README](../README.md) | **Step 5: Simulate** | → [Step 6: Validate](validation.md)

---

## Table of Contents

1. [Overview & The Pre-Deployment Cold-Start Problem](#1-overview--the-pre-deployment-cold-start-problem)
2. [The Five Simulation Engines at a Glance](#2-the-five-simulation-engines-at-a-glance)
   - 2.1 [Decision Guide: Choose Your Simulator](#21-decision-guide-choose-your-simulator)
   - 2.2 [Demystifying Terminology: Fault vs. Failure vs. Event vs. Flow vs. Change](#22-demystifying-terminology-fault-vs-failure-vs-event-vs-flow-vs-change)
   - 2.3 [The Four Paper Oracles vs. One Supporting Engine](#23-the-four-paper-oracles-vs-one-supporting-engine)
   - 2.4 [Comparative Engine Matrix](#24-comparative-engine-matrix)
3. [Lifecycle Integration: Where Simulators Fit in SaG](#3-lifecycle-integration-where-simulators-fit-in-sag)
4. [Engine 1: `FaultInjector` (Fast Cascade Reachability — Primary Oracle $I^*$)](#4-engine-1-faultinjector-fast-cascade-reachability--primary-oracle-i)
   - 4.1 [Quick Facts](#41-quick-facts)
   - 4.2 [Intuition & Step-by-Step Wave Algorithm ($W_0, W_1, \dots$)](#42-intuition--step-by-step-wave-algorithm-w_0-w_1-dots)
   - 4.3 [Mathematical Formulation ($I^*(v)$)](#43-mathematical-formulation-iv)
   - 4.4 [Multi-Broker Redundancy & Cascade Thresholds](#44-multi-broker-redundancy--cascade-thresholds)
   - 4.5 [How to Run `FaultInjector`](#45-how-to-run-faultinjector)
   - 4.6 [Output Schema Snippet (`impact_scores.json`)](#46-output-schema-snippet-impact_scoresjson)
   - 4.7 [Academic Provenance & Label Stability](#47-academic-provenance--label-stability)
   - 4.8 [Opt-In `threshold_cdf` Failure Model](#48-opt-in-threshold_cdf-failure-model)
5. [Engine 2: `FailureSimulator` (Multi-Layer Structural & Quality Impact — Oracle $I_{\text{comp}}$)](#5-engine-2-failuresimulator-multi-layer-structural--quality-impact--oracle-i_textcomp)
   - 5.1 [Quick Facts](#51-quick-facts)
   - 5.2 [Intuition & Multi-Layer Structural Traversal](#52-intuition--multi-layer-structural-traversal)
   - 5.3 [Composite Impact Formulation ($I_{\text{comp}}(v)$) & Visual Breakdown](#53-composite-impact-formulation-i_textcompv--visual-breakdown)
   - 5.4 [ISO/IEC 25010 Quality Decompositions ($IR, IM, IA, IFT$)](#54-isoiec-25010-quality-decompositions-ir-im-ia-ift)
   - 5.5 [Flow Disruption & Baseline Flow Priming](#55-flow-disruption--baseline-flow-priming)
   - 5.6 [Relationship Criticality ($I_{\text{edge}}(u,v)$)](#56-relationship-criticality-i_textedgeuv)
   - 5.6a [Network Gating: Switches, Routers & Link Capacity](#56a-network-gating-switches-routers--link-capacity)
   - 5.6b [Replica-Aware Host Failure](#56b-replica-aware-host-failure)
   - 5.7 [How to Run `FailureSimulator`](#57-how-to-run-failuresimulator)
   - 5.8 [Academic Provenance & Quality Gating Roles](#58-academic-provenance--quality-gating-roles)
6. [Engine 3: `EventSimulator` (Built-In Discrete-Event Engine — Supporting Baseline)](#6-engine-3-eventsimulator-built-in-discrete-event-engine--supporting-baseline)
   - 6.1 [Quick Facts](#61-quick-facts)
   - 6.2 [Intuition & Zero-Dependency Event Loop](#62-intuition--zero-dependency-event-loop)
   - 6.3 [Poisson Failures, Recoveries & M/G/1 Arrivals](#63-poisson-failures-recoveries--mg1-arrivals)
   - 6.4 [Primary Role: Baseline Flow Generation for `FailureSimulator`](#64-primary-role-baseline-flow-generation-for-failuresimulator)
   - 6.5 [How to Run `EventSimulator`](#65-how-to-run-eventsimulator)
7. [Engine 4: `MessageFlowSimulator` (High-Fidelity Queuing & QoS — Dynamic Probe $I_{\text{dyn}}$)](#7-engine-4-messageflowsimulator-high-fidelity-queuing--qos--dynamic-probe-i_textdyn)
   - 7.1 [Quick Facts](#71-quick-facts)
   - 7.2 [Intuition & SimPy Architecture](#72-intuition--simpy-architecture)
   - 7.3 [Private Topic Queues vs. Shared Compute Server](#73-private-topic-queues-vs-shared-compute-server)
   - 7.4 [Runtime DDS QoS Contract Enforcement](#74-runtime-dds-qos-contract-enforcement)
   - 7.5 [Load Calibration ($\rho = 0.65$) & Operating Points](#75-load-calibration-rho--065--operating-points)
   - 7.6 [Dynamic Delivery Loss ($I_{\text{dyn}}(v)$) & The Contention-Relief Phenomenon](#76-dynamic-delivery-loss-i_textdynv--the-contention-relief-phenomenon)
   - 7.7 [JSS Study Simulation Protocol & Timing (Single-Component Lifecycle)](#77-jss-study-simulation-protocol--timing-single-component-lifecycle)
   - 7.8 [How to Run `MessageFlowSimulator`](#78-how-to-run-messageflowsimulator)
   - 7.9 [Output Schema Snippet (`message_flow_results.json`)](#79-output-schema-snippet-message_flow_resultsjson)
   - 7.10 [Academic Provenance: Convergent-Validity Research Probe](#710-academic-provenance-convergent-validity-research-probe)
   - 7.11 [Runtime Engine: Reactive Behaviour, Deployment & Gray Failures (Opt-In)](#711-runtime-engine-reactive-behaviour-deployment--gray-failures-opt-in)
   - 7.12 [Emulated DDS Testbed, Calibration & Fidelity (Opt-In)](#712-emulated-dds-testbed-calibration--fidelity-opt-in)
8. [Engine 5: `ChangePropagationSimulator` (Maintainability Reference — Oracle $I_M$)](#8-engine-5-changepropagationsimulator-maintainability-reference--oracle-i_m)
   - 8.1 [Quick Facts](#81-quick-facts)
   - 8.2 [Runtime Failure vs. Development-Time Interface Change](#82-runtime-failure-vs-development-time-interface-change)
   - 8.3 [Transposed Graph Traversal ($G^\top$) & Stop Conditions](#83-transposed-graph-traversal-gtop--stop-conditions)
   - 8.4 [Maintainability Impact ($I_M(v)$)](#84-maintainability-impact-i_mv)
   - 8.5 [How to Run `ChangePropagationSimulator`](#85-how-to-run-changepropagationsimulator)
   - 8.6 [Academic Provenance: Multi-Task Loss Dimension Masking](#86-academic-provenance-multi-task-loss-dimension-masking)
9. [Quality Model Alignment (ISO/IEC 25010 & 25019)](#9-quality-model-alignment-isoiec-25010--25019)
10. [Worked Examples](#10-worked-examples)
    - 10.1 [Air Traffic Management (ATM) Step-by-Step Cascade](#101-air-traffic-management-atm-step-by-step-cascade)
    - 10.2 [Autonomous Vehicle (AV) Multi-Oracle Stratification](#102-autonomous-vehicle-av-multi-oracle-stratification)
11. [Programmatic Python API Quickstart](#11-programmatic-python-api-quickstart)
12. [CLI Reference (`cli/simulate_graph.py`)](#12-cli-reference-clisimulate_graphpy)
13. [Output Schemas Reference](#13-output-schemas-reference)
14. [Methodological Boundaries & Design Invariants](#14-methodological-boundaries--design-invariants)
15. [What Comes Next](#15-what-comes-next)

---

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                             STEP 5 AT A GLANCE                              │
├───────────────────┬─────────────────────────────────────────────────────────┤
│ Primary Input     │ G_structural — the RAW physical multigraph only.        │
│                   │ Never the derived DEPENDS_ON edges. This boundary is    │
│                   │ what keeps labels independent of the features.          │
├───────────────────┼─────────────────────────────────────────────────────────┤
│ Core Suite        │ Five simulators in saag/simulation/, answering distinct │
│                   │ architectural questions at different fidelities.        │
├───────────────────┼─────────────────────────────────────────────────────────┤
│ Key Operations    │ 1. Crash each component in turn.                        │
│                   │ 2. Propagate cascade across physical, network, logical, │
│                   │    and software layers.                                 │
│                   │ 3. Record resulting operational damage.                 │
│                   │ 4. Average across 5 random seeds; verify stability.     │
├───────────────────┼─────────────────────────────────────────────────────────┤
│ Primary Outputs   │ • I*(v)     — primary GNN training label & ranking.     │
│                   │ • I_comp(v) — Validate quality gates & Prescribe edits. │
│                   │ • I_dyn(v)  — offline convergent-validity probe.        │
│                   │ • I_M(v)    — maintainability blast radius reference.   │
├───────────────────┼─────────────────────────────────────────────────────────┤
│ Pipeline Role     │ This is Step 5, but Step 3 CONSUMES its output offline. │
│                   │ The pipeline is a DAG: Simulate produces training       │
│                   │ labels offline, Predict reads them from disk.           │
├───────────────────┼─────────────────────────────────────────────────────────┤
│ Downstream Handoff│ • Step 3 (Predict): I*(v) supervised labels.            │
│                   │ • Step 6 (Validate): ground-truth comparison oracle.    │
│                   │ • Step 7 (Prescribe): counterfactual edit verification. │
└───────────────────┴─────────────────────────────────────────────────────────┘
```

> [!NOTE]
> **JSS Manuscript Mapping & Pipeline Nomenclature:**
> - **Eight Steps vs. Four Stages:** This repository numbers eight executable pipeline steps (Model, Analyze, Predict, Diagnose, Simulate, Validate, Prescribe, Visualize). The companion JSS manuscript describes a four-stage formulation (Typed Multigraph $\to$ QoS Dependency Projection $\to$ Heterogeneous Graph Learning $\to$ Explainable Quality Attribution).
> - **Two Evaluation Arms:** The documentation uses **Pathway B** for the learned ranking arm (`PredictiveUseCase`) and **Pathway A** for the deterministic diagnostic arm (`DiagnosticUseCase`). In the paper, these are termed the **Predictive Pathway** (§4) and the **Explanation Layer** (§5).
> - **Simulator-Derived Labels:** No oracle is validated against live production outages; all labels in this framework are simulator-derived under controlled graph configurations.

---

## 1. Overview & The Pre-Deployment Cold-Start Problem

In distributed, event-driven architectures (e.g., ROS 2 robotics, microservice meshes, aerospace flight controls, financial trading backbones), identifying critical components and architectural bottlenecks is vital **before deployment**.

In an existing production system, site reliability engineers inspect historical crash traces and distributed APM telemetry. However, **prior to deployment, no crash logs exist**:
- How do we know which microservice failure will bring down the vehicle?
- How do we know which message broker is a single point of failure?
- How do we train graph neural networks to predict blast radius without production outage data?

To solve this pre-deployment cold-start challenge, Software-as-a-Graph (SaG) executes **controlled pre-deployment simulations**:
1. We ingest the architectural graph manifest (nodes, topics, brokers, libraries, hardware hosts, and QoS delivery contracts).
2. We systematically inject synthetic component failures.
3. We observe and record the resulting cascade across physical, network, logical, and software layers.
4. The observed operational damage becomes an objective, empirical **ground-truth impact score** used to train predictive models, evaluate safety gates, and verify automated refactoring prescriptions.

```
┌──────────────────────────┐     Systematic Injection     ┌────────────────────────────┐
│   Architectural Graph    │  ─────────────────────────►  │    Ground-Truth Impact     │
│ (Nodes, Topics, Brokers) │      Simulation Engine       │ Scores: I*(v), I_comp(v)   │
└──────────────────────────┘                              └────────────────────────────┘
                                                                        │
                                                                        ▼
                                                          • Supervised GNN Training
                                                          • Empirical Safety Gating
                                                          • Counterfactual Verification
```

> [!IMPORTANT]
> **The Input–Label Independence Guarantee:**
> All simulation engines operate strictly on the **raw structural multigraph** ($G_{\text{structural}}$). They never read the derived logical dependencies (`DEPENDS_ON`) that predictive and explanatory algorithms consume. This strict architectural boundary prevents circular logic, label contamination, and data leakage.

---

## 2. The Five Simulation Engines at a Glance

Why does SaG provide **five distinct simulation engines** instead of a single catch-all simulator?

Because answering different engineering questions requires fundamentally different tradeoffs between **computational speed**, **granularity**, and **simulation fidelity**. An engine fast enough to generate training labels across thousands of nodes in seconds cannot simulate microsecond packet queues; conversely, a high-fidelity discrete-event queuing engine is far too computationally heavy for exhaustive training sweeps.

### 2.1 Decision Guide: Choose Your Simulator

Use this decision guide to select the appropriate engine for your task:

```mermaid
flowchart TD
    START(["What is your primary objective?"]) --> Q{"What do you need?"}
    
    Q -- "Train GNNs or fast ranking" --> FI["<b>1. FaultInjector</b><br>• Metric: I*(v)<br>• Cost: ~10 ms/node<br>• Use: GNN training labels & CLI benchmark"]
    Q -- "Safety gating or refactoring verification" --> FS["<b>2. FailureSimulator</b><br>• Metric: I_comp(v)<br>• Cost: ~50 ms/node<br>• Use: Multi-layer ISO gates & EditVerifier"]
    Q -- "Verify queues, drops & DDS QoS" --> MFS["<b>4. MessageFlowSimulator</b><br>• Metric: I_dyn(v)<br>• Cost: ~5–30 s/node (SimPy)<br>• Use: Offline convergent validity probe"]
    Q -- "Evaluate code ripple & API changes" --> CPS["<b>5. ChangePropagationSimulator</b><br>• Metric: I_M(v)<br>• Cost: <5 ms/node<br>• Use: Development-time blast radius on G^T"]
    Q -- "Zero-dependency event loop" --> ES["<b>3. EventSimulator</b><br>• Metric: Baseline flow set<br>• Cost: ~20 ms/run<br>• Use: Internal flow priming for FailureSimulator"]
```

---

### 2.2 Demystifying Terminology: Fault vs. Failure vs. Event vs. Flow vs. Change

The naming convention reflects the specific failure and temporal model of each engine:

| Engine | Focus Layer | What it Injects / Evaluates | Why this Name? |
|:---|:---|:---|:---|
| **`FaultInjector`** | Application & Pub-Sub Dataflow | Single-component crash causing feed starvation | Injects an isolated **fault** at an individual publisher or broker and observes cascading downstream starvation ($I^*$). |
| **`FailureSimulator`** | Multi-Layer Infrastructure | Systemic crash across physical, network, and software planes | Simulates systemic **failures** across ECUs, network links, brokers, and libraries, calculating composite quality loss ($I_{\text{comp}}$). |
| **`EventSimulator`** | Discrete-Event Pub-Sub | Message events (`PUBLISH`, `ROUTE`, `DELIVER`, `DROP`) | Built-in lightweight **event queue** operating without external dependencies, modeling stochastic Poisson failure/recovery. |
| **`MessageFlowSimulator`** | Real-Time DDS Queuing | Continuous packet flow, queue limits, and QoS contracts | Models continuous **message traffic flow** over a virtual SimPy timeline to observe queue occupancy, deadline misses, and packet loss ($I_{\text{dyn}}$). |
| **`ChangePropagationSimulator`** | Development-Time Codebase | API modifications, interface refactoring | Simulates upstream **software changes** propagating backward on transposed dependencies ($G^\top$) to quantify code maintenance ripple ($I_M$). |

---

### 2.3 The Four Paper Oracles vs. One Supporting Engine

In the JSS manuscript (§4.3), the simulation suite is formalized as **four component-level oracles and one relationship oracle**. `EventSimulator` serves as the internal engine that primes baseline flows for `FailureSimulator`:

| Engine Class | JSS §4.3 Oracle Name | Target Metric | Architectural Role | Status in Paper |
|:---|:---|:---:|:---|:---:|
| [`FaultInjector`](../saag/simulation/fault_injector.py) | **Cascade Reachability Oracle** | $I^*(v)$ | **Primary Continuous Target Oracle**: continuous GNN supervision and predictive ranking. | **Primary Oracle** |
| [`FailureSimulator`](../saag/simulation/failure_simulator.py) | **Multi-Metric Composite Oracle** | $I_{\text{comp}}(v)$ | Multi-layer quality gates (`ValidationService`) and counterfactual mutation sweeps (`EditVerifier`). | **Validate/Prescribe Oracle** |
| [`FailureSimulator`](../saag/simulation/failure_simulator.py) | **Relationship Removal Oracle** | $I_{\text{edge}}(u,v)$ | Single-edge severing sweeps: $I_{\text{edge}} = \bar{I}_{\text{comp}}(G \setminus \{(u,v)\}) - \bar{I}_{\text{comp}}(G)$. | **Edge Criticality Oracle** |
| [`MessageFlowSimulator`](../saag/simulation/message_flow_simulator.py) | **Dynamic Queue-Flow Oracle** | $I_{\text{dyn}}(v)$ | Offline behavioral validation probe ensuring graph scores reflect dynamic queuing. | **Research Probe** |
| [`ChangePropagationSimulator`](../saag/simulation/change_propagation.py) | **Change-Propagation Oracle** | $I_M(v)$ | Evolutionary maintainability blast radius reference. *Never a training label.* | **Maintainability Oracle** |
| [`EventSimulator`](../saag/simulation/event_simulator.py) | *Internal Event Engine* | Baseline flows | Internal baseline flow priming for `FailureSimulator`'s Flow Disruption metric. | *Supporting Utility* |

> [!CAUTION]
> **A result measured against one oracle is never transferred to another.** These represent distinct operational constructs on different scales; only their rank agreement is comparable. Every evaluation must state which oracle produced its labels.

---

### 2.4 Comparative Engine Matrix

```mermaid
flowchart TD
    G["Raw Structural Multigraph G"] --> ENGINES["Step 5: Simulation Suite"]

    subgraph StackA["Stack 1: Supervised Training Labeler (Predict Stage)"]
        ENGINES --> FI["FaultInjector<br>(saag/simulation/fault_injector.py)"]
        FI --> I_STAR["I*(v) Cascade Ground Truth<br>(Continuous Subscriber Feed Loss)"]
    end

    subgraph StackB["Stack 2: Multi-Layer Structural Quality Oracle (Validate & Prescribe Stages)"]
        ENGINES --> FS["FailureSimulator<br>(saag/simulation/failure_simulator.py)"]
        FS --> I_COMP["I_comp(v) Composite Loss<br>(Reachability, Fragmentation, Throughput, Flow)"]
        ENGINES --> ES["EventSimulator<br>(saag/simulation/event_simulator.py)"]
        ES -.->|Primes Baseline Flows| FS
    end

    subgraph StackC["Stack 3: Continuous-Time Discrete-Event Probe (Research Validation)"]
        ENGINES --> MFS["MessageFlowSimulator<br>(saag/simulation/message_flow_simulator.py)"]
        MFS --> I_DYN["I_dyn(v) Dynamic Delivery Loss<br>(SimPy Queues & QoS Contracts)"]
    end

    subgraph StackD["Stack 4: Software Evolution Reference (Maintainability)"]
        ENGINES --> CPS["ChangePropagationSimulator<br>(saag/simulation/change_propagation.py)"]
        CPS --> IM["I_M(v) Maintainability Impact<br>(Transposed Graph G^T Ripple)"]
    end
```

The table below summarizes the technical specifications of each engine:

| Engine | Core Question | Underlying Paradigm | Output Metric | Speed | Primary Role |
|:---|:---|:---|:---|:---:|:---|
| **`FaultInjector`** | *"If publisher or broker $v$ dies, which subscribers starve?"* | BFS cascade reachability ($O(V+E)$) | **$I^*(v)$**: Feed loss fraction | **Fast** (~10 ms/node) | **Predict**: Training labels.<br>**Validate**: CLI benchmark ($\rho \ge 0.70$). |
| **`FailureSimulator`** | *"What is the systemic loss across hosts, links, brokers, and libraries?"* | Multi-layer structural graph traversal | **$I_{\text{comp}}(v)$**: AHP composite loss | **Moderate** (~50 ms/node) | **Validate**: Release safety gates.<br>**Prescribe**: Refactoring verification. |
| **`EventSimulator`** | *"How do messages traverse the graph under Poisson failure/recovery?"* | Priority queue (`heapq`) event loop | **Flow Set**: Active paths, drops | **Fast** (~20 ms/run) | **Utility**: Primes baseline flows for `FailureSimulator`. |
| **`MessageFlowSimulator`** | *"How do queues, packet drops, and deadlines behave under DDS traffic?"* | Discrete-event queuing (SimPy) | **$I_{\text{dyn}}(v)$**: Dynamic delivery drop | **Detailed** (~5–30 s/node) | **Research**: Inter-oracle convergent validity ($\rho = 0.711$). |
| **`ChangePropagationSimulator`** | *"If an interface changes, how far does it ripple upstream?"* | Transposed dependency BFS on $G^\top$ | **$I_M(v)$**: Maintenance blast radius | **Instant** (<5 ms/node) | **Reference**: Software evolution & GNN dimension masking. |

---

### 2.5 The Order-$k$ Reference Criterion & First-Order Oracle Dynamics (JSS §4.4)

In the JSS study, simulators and rankers read the same declared architecture model. Without methodological discipline, a ranking may agree with a simulator simply because it *restates the simulator's propagation rule* rather than predicting impact from independent evidence. To separate predictive skill from rule restatement, SaG formalizes the **Order-$k$ Reference Criterion**:

#### 1. Formal Definition
Let an oracle $O$ compute the failure impact of removing component $v$ by propagating failures in sequential waves over its inputs, and let $T_k(O)$ denote the same computation halted after wave $k$. A ranking $R$ is an **order-$k$ reference** for $O$ ($k \ge 1$ or $k = \infty$) if $R$ equals $T_k(O)$ after at most a closed set of five simplifications:
- **(S1) Expectation**: Taking the expected outcome in place of seed-dependent stochastic propagation.
- **(S2) Unscaled Severity**: Omitting heuristic QoS severity multipliers ($\times 1.2$ for `RELIABLE`, etc.) applied to loss terms.
- **(S3) Dropped Queuing Mechanics**: Omitting queue buffers, drops, and deadline timers added on top of propagation.
- **(S4) Uniform Rates / Normalization**: Using uniform unit weights in place of per-component rates or normalization denominators.
- **(S5) Wave Support**: Counting the raw number of affected components (support of the wave) rather than summing weighted losses.

Each simplification removes exactly one simulation mechanism; none changes which components are reached or the causal direction of propagation.

#### 2. Classification of Rankers Under the Criterion

| Oracle | Reference ($T_k(O)$ under S1–S5) | Order | Metric / Formula | Status | LOSO $\rho$ |
|:---|:---|:---:|:---|:---:|:---:|
| **$I^*$ (Reachability)** | **Analytic $I^*$** | $k = 1$ | First-order expansion (S1, S2, S4) | Reference | **0.808** |
| | **InDeg** *(Afferent Coupling)* | $k = 1$ | Support of first wave (S5; direct-dependent count) | Reference | **0.764** |
| | **Reach** *(Transitive)* | $k = \infty$ | Support of untruncated BFS cascade (S1, S5) | Reference | **0.732** |
| **$I_{\text{dyn}}$ (Queue-Flow)** | **Rate-Weighted Expansion** | $k = 1$ | Delivered-rate loss without queuing (S3, S4; Eq.~7) | Reference | **0.830** |
| **All Oracles** | **GAT-P-QoS / HGT-QoS / Hybrids** | — | Learned representations; do not truncate simulator rules | **Predictor** | $0.622 - 0.748$ |
| | **Topo-QoS** | — | Shortest-path betweenness; does not truncate simulation | **Predictor** | $0.553$ |

#### 3. The Rate-Weighted First-Order Expansion Formula
For the computationally intensive queue-flow simulator ($I_{\text{dyn}}$), which required **12.7 CPU-hours** ($355.6$ Wh) to label the corpus, message propagation is dominated by the immediate loss of delivered messages to surviving subscribers. Its first-order truncation (Eq.~7 in the JSS paper) calculates the delivered-rate loss directly from declared publication rates $r(t)$:
$$I_{\text{dyn}}^{(1)}(v) = \sum_{t \in \text{pub}(v)} r(t) \cdot |\text{sub}(t)|$$
This closed-form reference executes in **less than 1 millisecond** per architecture ($\approx 1$ J), reaching $\rho = \mathbf{0.830} \; [0.778, 0.872]$ on unseen architectures. Learned surrogates trained on hours of simulation labels ($\text{GBM-P-QoS}\to\text{dyn}$ at $\rho = 0.799$, rate-fed GNNs at $\le 0.665$) fail to exceed this closed-form formula, demonstrating that declared message rates—not QoS contracts—drive queue-flow disruption.

#### 4. Methodological Consequence: Why Simulators Are First-Order by Construction
Because $I^*$ and $I_{\text{dyn}}$ propagate their primary damage in the first wave by construction, low-order analytical truncations capture their dominant signal. The JSS benchmark demonstrates that when simulators operate in this first-order regime, explicit dependency representation ($G_{\text{dep}}$) provides the signal directly, making complex graph learning unnecessary.

---

## 3. Lifecycle Integration: Where Simulators Fit in SaG

SaG enforces strict stage boundaries. Different simulation engines own different stages of the architectural lifecycle, and their outputs are **not interchangeable**:

```mermaid
flowchart LR
    subgraph Step5["Step 5: Simulation Suite"]
        FI["FaultInjector"]
        FS["FailureSimulator"]
        ES["EventSimulator"]
        MFS["MessageFlowSimulator"]
        CPS["ChangePropagationSimulator"]
    end

    subgraph Step3["Step 3: Predict"]
        HGT["HGT-QoS / GAT Training"]
    end

    subgraph Step6["Step 6: Validate"]
        VAL_CLI["CLI Benchmark (ρ ≥ 0.70)"]
        VAL_LIB["ValidationService (Release Gates)"]
        CONV["Convergent Validity Probe"]
    end

    subgraph Step7["Step 7: Prescribe"]
        EV["EditVerifier (Mutation Sweeps)"]
    end

    FI -->|"I*(v) Labels"| HGT
    FI -->|"I*(v) Ground Truth"| VAL_CLI
    ES -.->|"Baseline Flow Priming"| FS
    FS -->|"I_comp(v) Quality Metrics"| VAL_LIB
    FS -->|"Risk Delta ΔI_comp"| EV
    MFS -->|"I_dyn(v) Probe"| CONV
    CPS -->|"I_M(v) Metrics"| FS
```

### Stage Summary & Engine Contracts

1. **Step 3 (Predict Stage)**:
   `FaultInjector` produces scalar labels $I^*(v)$ across 5 seeds. GNNs (`NodeCriticalityGNN`, `HGT-QoS`) use $I^*(v)$ as their continuous supervised training target.
2. **Step 6 (Validate Stage)**:
   - **CLI Benchmark (`cli/validate_graph.py`)**: Uses `FaultInjector` to test whether GNN or structural predictions match empirical cascade reachability with rank correlation $\rho \ge 0.70$.
   - **Library Quality Gates (`ValidationService`)**: Uses `FailureSimulator` to evaluate the six shipped gates — three release gates (`spearman`, `overlap_at_q3`, `top5_overlap`) whose conjunction defines `passed`, and three reported informational gates.
   - **Convergent Validity Probe**: Uses `MessageFlowSimulator` to verify that topological risk correlates with dynamic message loss ($I_{\text{dyn}}$ vs. $I^*$, $\rho = 0.627$).
3. **Step 7 (Prescribe Stage)**:
   `EditVerifier` uses `FailureSimulator` to execute counterfactual failure simulations on proposed architectural refactorings, verifying that candidate edits yield net risk reduction ($\Delta I_{\text{comp}} > 0, \Delta \text{SRI} > 0$).
4. **Step 4 (Diagnose Stage)**:
   **Has strictly zero simulation access**. Anti-pattern detection and ISO-RM attribution are closed-form and deterministic, preserving the input–label independence guarantee.

---

## 4. Engine 1: `FaultInjector` (Fast Cascade Reachability — Primary Oracle $I^*$)

### 4.1 Quick Facts

| Dimension | Specification |
|:---|:---|
| **Source File** | [`saag/simulation/fault_injector.py`](../saag/simulation/fault_injector.py) |
| **JSS Oracle Name** | Cascade Reachability Oracle ($I^*(v)$) |
| **Output Metric** | $I^*(v) \in [0, 1]$ (Continuous subscriber feed-loss fraction) |
| **Algorithmic Complexity** | $O(V + E)$ (Wave-based BFS on pub-sub topology) |
| **Execution Cost** | ~10 ms per node (~$0.14$–$7.2\,\text{s}$ for a complete scenario sweep) |
| **Primary Consumer** | Step 3 (Predict Stage — GNN continuous training target) |

---

### 4.2 Intuition & Step-by-Step Wave Algorithm ($W_0, W_1, \dots$)

**Intuition**: When Publisher $A$ crashes, it stops publishing to Topic $T_1$. Consequently, all subscribers reading $T_1$ suffer feed starvation. If a subscriber depends heavily on that feed, it may also crash, cutting off further downstream topics and starving secondary subscribers.

```mermaid
flowchart TD
    W0["Wave 0: Injected Component v Fails & Removed"] --> PA["Phase A: Direct Library Propagation<br>(Library USES: deterministic crash, prob = 1.0)"]
    PA --> PB["Phase B: Topic-Mediated Feed Loss Calculation<br>L(t) = max(Publisher Loss, Router Loss) × QoS_factor"]
    PB --> EVAL["Subscriber Starvation Check<br>Does sub_loss(s) ≥ propagation_threshold?"]
    EVAL -- No --> SURV["Subscriber Survives<br>(Partial feed degradation recorded)"]
    EVAL -- Yes --> PROB["Stochastic Cascade Check<br>P_fail = min(1.0, sub_loss / threshold) × depth_damp"]
    PROB -- Fails --> W1["Wave 1+: Failed Subscriber Added to Frontier"]
    PROB -- Survives --> SURV
    W1 --> PA
```

1. **Wave 0 (Direct Failure)**: Target component $v$ is removed from the active topology.
2. **Phase A (Direct Library Dependencies)**: If an application depends on a failed `Library` (`app_to_lib`), that application crashes deterministically ($\text{probability} = 1.0$).
3. **Phase B (Topic-Mediated Feed Loss)**: For every topic $t$, continuous feed loss $L(t) \in [0, 1]$ is computed from dead publishers and dead routing brokers:
   $$L(t) = \max\left(\text{Publisher Loss}(t), \; \text{Router Loss}(t)\right) \times \text{QoS\_factor}(t)$$
   A subscriber $s$ calculates its mean feed loss across all subscribed topics:
   $$\text{sub\_loss}(s) = \frac{\sum_{t \in \text{subs}(s)} L(t)}{|\text{subs}(s)|}$$
4. **Stochastic Cascade & Depth Damping**: If $\text{sub\_loss}(s) \ge \text{propagation\_threshold}$ (default: `0.20`), the subscriber has a probability of cascading:
   $$P_{\text{fail}}(s) = \min\left(1.0, \; \frac{\text{sub\_loss}(s)}{\text{propagation\_threshold}}\right) \times \text{depth\_damp}, \quad \text{depth\_damp} = \max(0.25, \; 1.0 - \text{wave\_idx} \times 0.15)$$
5. **Termination**: Iteration terminates when no new components fail or `max_cascade_depth` is reached.

---

### 4.3 Mathematical Formulation ($I^*(v)$)

The ground-truth impact score $I^*(v)$ is the **mean continuous feed loss inflicted across all subscribers in the intact system**:

$$I^*(v) = \frac{1}{|\mathcal{S}_{\text{all}}|} \sum_{s \in \mathcal{S}_{\text{all}}} \text{sub\_loss}(s)$$

- If failing $v$ causes zero feed loss to any subscriber, $I^*(v) = 0.0$.
- If failing $v$ cuts off 100% of feeds to every subscriber, $I^*(v) = 1.0$.
- Because it tracks continuous feed loss rather than a coarse count of dead nodes, $I^*(v)$ captures partial operational degradation smoothly.

---

### 4.4 Multi-Broker Redundancy & Cascade Thresholds

- **Multi-Broker Redundancy**: If a topic is routed across $k$ redundant brokers, failing 1 broker results in fractional router loss:
  $$\text{Router Loss}(t) = \frac{1}{k}$$
  If $k=2$, losing one broker reduces feed availability by 50% rather than causing total partition.
- **Cascade Threshold (`--propagation-threshold`)**:
  - `0.2` (Default): Realistic sensitivity; a service losing $\ge 20\%$ of incoming data streams risks functional failure.
  - `0.5`: Moderate tolerance; suitable for multi-sensor fusion systems.
  - `1.0`: Extreme tolerance; a service only cascades if 100% of its feeds are severed.

---

### 4.5 How to Run `FaultInjector`

#### Python API
```python
from pathlib import Path
from saag.simulation.fault_injector import FaultInjector
from saag.core.graph_io import load_graph

graph = load_graph("data/scenarios/atm_system.json")

# Initialize injector with standard 5 seeds
injector = FaultInjector(
    graph=graph,
    seeds=[42, 123, 456, 789, 2024],
    propagation_threshold=0.20,
    qos_factor_mode="ladder"
)

result = injector.run(node_types=["Application", "Broker"])
result.save(Path("output/simulation/impact_scores.json"))

print(f"Top critical: {result.top_k_by_impact[0]['node_id']} "
      f"(I* = {result.top_k_by_impact[0]['impact_score']:.4f})")
```

#### CLI Command
```bash
python cli/simulate_graph.py fault-inject \
    --input data/scenarios/atm_system.json \
    --output output/simulation/ \
    --seeds 42,123,456,789,2024 \
    --propagation-threshold 0.2 \
    --qos-factor ladder \
    --export-json
```

---

### 4.6 Output Schema Snippet (`impact_scores.json`)

```json
{
  "schema_version": "2.1",
  "graph_id": "atm_system",
  "labeler": "FaultInjector",
  "seeds_used": [42, 123, 456, 789, 2024],
  "label_stability": {
    "n_seeds": 5,
    "mean_std": 0.020109,
    "test_retest_spearman": 0.97169,
    "topk_jaccard": 0.875
  },
  "records": {
    "RadarTracker": {
      "impact_score": 1.0,
      "impact_score_std": 0.0,
      "cascade_depth": 1,
      "impacted_subscribers": 3,
      "orphaned_topics": 2
    }
  }
}
```

---

### 4.7 Academic Provenance & Label Stability

> [!NOTE]
> **JSS Manuscript Findings (§4.3, §7.1, §7.5.1):**
> - **Label Noise Ceiling:** Re-running `FaultInjector` across 5 random seeds yields pairwise test-retest rank correlations of **$0.811$–$1.000$ (median $0.982$)**, establishing the ceiling of achievable GNN accuracy.
> - **Topological Nature of $I^*$:** Disabling QoS scaling entirely recovers component ranking at mean **$\rho = 0.965$** ($0.891$–$0.999$), indicating that $I^*$ is primarily a topological reachability functional with QoS acting at top-$K$ boundaries (Jaccard $0.678$).
> - **Simulation vs. Static Gate Cost:** Running a full 5-seed `FaultInjector` sweep costs **$0.14$–$7.2\,\text{s}$**, whereas static analysis costs **$0.04$–$82.7\,\text{s}$** (being $11\times$ slower on Enterprise mesh), proving that in-process cascade simulation is computationally cheaper than full static feature extraction.

### 4.8 Opt-In `threshold_cdf` Failure Model

Under the published `failure_model="step"`, a subscriber whose feed loss reaches `propagation_threshold` fails with probability $\min(1, \text{loss}/\theta)\cdot\text{damp}$. That expression is reached only when $\text{loss} \ge \theta$, so the $\min$ is always 1: the probability is just the depth damping (1.0 at wave 0). The loss magnitude past the threshold never matters, and the label has a cliff at $\theta = 0.2$. A surviving subscriber is also redrawn on every later wave, so its odds depend on how long the cascade runs elsewhere. Measured on five core scenarios, I* agrees with its own first wave at $\rho \ge 0.99$ on four of them (0.82 on microservices), and most nodes have zero seed spread.

`failure_model="threshold_cdf"` (CLI `--failure-model threshold_cdf`) gives every subscriber a latent tolerance $\theta_s \sim U(0.1, 0.5)$, drawn once per seed. The subscriber may fail (probability `depth_damp`) once its loss reaches $\theta_s$, and is redrawn only when its loss *increases*. Averaged over seeds, $P(\text{fail}) = F_\theta(\text{loss})\cdot\text{damp}$: the cliff becomes the threshold distribution's CDF, and the seed spread measures threshold uncertainty. The artifact records `labeler_config`. The default remains `step`, so published labels are unchanged (`tests/test_simulation_golden_defaults.py`). See `tests/test_runtime_labels.py` for the graded-vs-cliff check.

---

## 5. Engine 2: `FailureSimulator` (Multi-Layer Structural & Quality Impact — Oracle $I_{\text{comp}}$)

### 5.1 Quick Facts

| Dimension | Specification |
|:---|:---|
| **Source File** | [`saag/simulation/failure_simulator.py`](../saag/simulation/failure_simulator.py) |
| **JSS Oracle Name** | Multi-Metric Composite Oracle ($I_{\text{comp}}(v)$) & Relationship Removal Oracle ($I_{\text{edge}}$) |
| **Output Metric** | $I_{\text{comp}}(v) \in [0, 1]$ (AHP composite loss) + ISO/IEC 25010 decompositions ($IR, IM, IA, IFT$) |
| **Algorithmic Scope** | 4 Architectural Layers: Physical, Network, Middleware, Software |
| **Execution Cost** | ~50 ms per node |
| **Primary Consumers**| Step 6 (`ValidationService` release gates) & Step 7 (`EditVerifier` refactoring verification) |

---

### 5.2 Intuition & Multi-Layer Structural Traversal

Real distributed systems do not fail in the application layer alone. An ECU host processor can overheat, a network link can drop, a message broker can crash, or a shared library can fail. `FailureSimulator` traces failures across all four architectural planes:

```
[ Physical Layer ]     Host Compute Node (ECU) fails
                              │ (RUNS_ON)
                              ▼
[ Middleware Layer ]   Message Broker crashes
                              │ (ROUTES)
                              ▼
[ Logical Layer ]      Topics become unreachable, subscribers starve
                              │
[ Software Layer ]     Shared Library fails ──(USES)──► Application crashes
```

---

### 5.3 Composite Impact Formulation ($I_{\text{comp}}(v)$) & Visual Breakdown

`FailureSimulator` computes an overall composite damage score $I_{\text{comp}}(v) \in [0, 1]$ using Analytic Hierarchy Process (AHP) weights:

$$I_{\text{comp}}(v) = 0.35 \cdot \text{Reachability Loss} + 0.25 \cdot \text{Fragmentation} + 0.25 \cdot \text{Throughput Loss} + 0.15 \cdot \text{Flow Disruption}$$

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                   COMPOSITE STRUCTURAL LOSS (I_comp)                        │
├──────────────┬───────┬──────────────────────────────────────────────────────┤
│ 35% Weight   │  RL   │ Reachability Loss: % of end-to-end paths severed     │
│              │       │ RL = 1 - (Paths_post / Paths_pre)                    │
├──────────────┼───────┼──────────────────────────────────────────────────────┤
│ 25% Weight   │  FR   │ Infrastructure Fragmentation: Splits into partitions │
│              │       │ FR = 0.70·ΔConnectedComponents + 0.30·ΔStrandedMass  │
├──────────────┼───────┼──────────────────────────────────────────────────────┤
│ 25% Weight   │  TL   │ Throughput Loss: % of QoS message bandwidth lost     │
│              │       │ TL = 1 - (Throughput_post / Throughput_pre)          │
├──────────────┼───────┼──────────────────────────────────────────────────────┤
│ 15% Weight   │  FD   │ Flow Disruption: Broken flows vs. EventSimulator base│
│              │       │ FD = 1 - (ActiveFlows_post / ActiveFlows_pre)        │
└──────────────┴───────┴──────────────────────────────────────────────────────┘
```

---

### 5.4 ISO/IEC 25010 Quality Decompositions ($IR, IM, IA, IFT$)

- **$IFT(v)$ (Fault-Tolerance Impact)**: Directly measures dynamic cascade propagation:
  $$IFT(v) = 0.45 \cdot \text{Cascade Reach} + 0.35 \cdot \text{Weighted Cascade Impact} + 0.20 \cdot \text{Normalized Depth}$$
- **$IA(v)$ (Availability Impact)**: Evaluates infrastructure partition severity and stranded capacity:
  $$IA(v) = 0.50 \cdot \text{Weighted Reachability Loss} + 0.35 \cdot \text{Weighted Fragmentation} + 0.15 \cdot \text{Path-Breaking Throughput Loss}$$
- **$IR(v)$ (Reliability Impact)**: The balanced blend of Fault-Tolerance and Availability:
  $$IR(v) = r_\alpha \cdot IFT(v) + (1 - r_\alpha) \cdot IA(v) \quad (r_\alpha = 0.36)$$
- **$I_M(v)$ (Maintainability Impact)**: Evaluates architectural blast radius and ripple effects, populated via `ChangePropagationSimulator`.

---

### 5.5 Flow Disruption & Baseline Flow Priming

The Flow Disruption term ($15\%$ of $I_{\text{comp}}$) compares post-failure communication paths against an unperturbed baseline. Before running failure sweeps, baseline flows must be primed:

```python
SimulationService._prime_baseline_flows(sim_graph, failure_sim)
```

Priming executes a deterministically seeded run with `EventSimulator` under healthy conditions, ensuring that flow disruption measures genuine structural path severing rather than stochastic noise.

---

### 5.6 Relationship Criticality ($I_{\text{edge}}(u,v)$)

`FailureSimulator` also evaluates the criticality of individual architectural dependencies by simulating single-edge severing:

$$I_{\text{edge}}(u,v) = \bar{I}_{\text{comp}}\big(G \setminus \{(u,v)\}\big) - \bar{I}_{\text{comp}}(G)$$

This measures the loss inflicted when dependency $(u,v)$ is removed while both endpoints stay operational.

### 5.6a Network Gating: Switches, Routers & Link Capacity

By default the NETWORK cascade is the legacy host-isolation rule: a failed `Node` isolates mesh peers that have no other link. When the topology models its network (`NetworkDevice` switches/routers, `CONNECTS_TO` links with `capacity_mbps`; see [graph-model.md §5.2](graph-model.md#52-phase-2-structural-topology--degree-augmentation)), pass `network_gating=True` (`SimulationService(..., network_gating=True)`, `GraphEvaluator(network_gating=True)`, `--network-gating` on the CLI). Every live pub-sub flow is then routed over the live network (publisher host → routing broker host → subscriber host, shortest paths, ECMP split; [`saag/core/network.py`](../saag/core/network.py)):

- **Reachability (cascade).** A subscriber whose topic is still produced and routed, but which no live path connects to any publisher, starves (`network_unreachable:<id>`). This replaces the host-isolation rule. A feed the healthy network never delivered is not charged to the failure.
- **Congestion (measurement only).** If links declare capacity, the flows rerouted after a failure load the surviving links. A link with utilisation $u > 1$ delivers $1/u$ of what crosses it. A flow's network loss is measured **relative to its baseline**, $1 - (1-\ell)/(1-\ell_0)$, so a link that is already over-subscribed when healthy is not charged to every component. This loss scales the reachability, throughput and flow-disruption terms. It never fails a component, so it does not compound with the cascade.

Both are off by default. With gating off, a topology with devices still loads, but device failures only score fragmentation, and a warning is logged.

`FaultInjector(network_gating=True)` applies the reachability rule (not congestion) to $I^*$: a feed the live network no longer delivers counts as fully lost. Pass `--node-types ...,Node,NetworkDevice` to label hosts and devices.

### 5.6b Replica-Aware Host Failure

By default, both oracles treat a component that `RUNS_ON` several `Node`s (a hot-standby replica) as dying when **any one** of its hosts fails. That is how the published labels were produced. Under that rule a replica adds exposure instead of redundancy, so any placement, redundancy or anti-affinity comparison would be scored backwards.

Pass `replica_aware=True` to get the opposite rule. A host outage takes a component down only when **all** of its hosts are down. The flag is threaded the same way as `network_gating`: `FaultInjector(replica_aware=True)`, `FailureSimulator(replica_aware=True)`, `SimulationService(..., replica_aware=True)` and `GraphEvaluator(replica_aware=True)`. On the CLI it is `--replica-aware` on `saag-simulate fault-inject`.

- **`FaultInjector` ($I^*$).** In wave 0 of a `Node` injection, a resident fails only if every host in its `hosts_of` list is failed. Injection is single-fault, so in an $I^*$ sweep this means multi-hosted residents survive and single-hosted ones still die.
- **`FailureSimulator` ($I_{\text{comp}}$).** The physical cascade gives a hosted component the impact of its *least*-impacted host, $\min_{h \in \text{hosts}(c)} \text{impact}(h)$. This is the same `min()` rule that broker redundancy already uses. A joint outage, `FailureScenario(target_ids=["N1", "N2"])`, kills a component replicated on exactly those two hosts. The fragmentation projection also attaches a multi-hosted component to every host it declares. Without that, `SimulationGraph._hosted_on` keeps only the *last* declared host, so a surviving replica whose last host failed would be counted as a stranded island.
- **What it does not model.** A surviving replica carries the full load: there is no capacity loss and no failover delay. Topic semantics are unchanged. `FaultInjector` already scores a topic with several distinct publishers by rate-weighted fractional loss, and `FailureSimulator` averages publisher impact against the starvation bound. There is no k-of-n quorum rule. `SimulationGraph.get_dependency_edges()` still lifts Rules 3–4 through `_hosted_on`, because its arc set has to match `MemoryRepository` (see `tests/test_simulation_dependency_derivation.py`). The flag has no effect on `MessageFlowSimulator`, which does not model hosts.

With the flag off, every label is byte-identical to the pre-flag code. With the flag on, only `Node` labels move, and on a corpus with no replicas the flag is a no-op (`tests/test_replica_aware.py`). Measured $I^*$ for seeds 42/123/456 with the flag on (atm has 14 of 31 hosted components multi-hosted; av has 30 of 84): atm N0 0.396 → 0.000, N1 0.692 → 0.656, N3 0.562 → 0.491; av N1 0.947 → 0.032. The other `Node`s move by seed noise at most.

---

### 5.7 How to Run `FailureSimulator`

```python
from saag.simulation.graph import SimulationGraph
from saag.simulation.failure_simulator import FailureSimulator
from saag.simulation.service import SimulationService
from saag.core.graph_io import load_graph

graph_dict = load_graph("data/scenarios/atm_system.json", return_dict=True)
sim_graph = SimulationGraph(graph_dict)
fail_sim = FailureSimulator(sim_graph, qos_weighting=True)

# Prime unperturbed baseline flows via EventSimulator
SimulationService._prime_baseline_flows(sim_graph, fail_sim)

# Run exhaustive failure sweep
results = fail_sim.simulate_exhaustive(seed=42)
for r in results[:5]:
    print(f"Component: {r.target_id:<20} | I_comp: {r.impact.composite_impact:.4f} "
          f"| Reachability Loss: {r.impact.reachability_loss:.4f}")
```

---

### 5.8 Academic Provenance & Quality Gating Roles

> [!NOTE]
> **JSS Manuscript Findings (§4.3, §7.3.4):**
> - **Prohibited from Predictive Training:** $I_{\text{comp}}$ is strictly reserved for Validate-stage quality gates and Prescribe-stage verification; it is **never used as a GNN training target**.
> - **Simpson's Paradox Discovery:** When evaluated against $I_{\text{comp}}$, RM attribution correlation is $\rho = 0.566$ on Applications, $0.119$ on Brokers, and $0.244$ on Nodes, but pooling all types collapses the score to $\rho = 0.098$. This empirical finding mandated that all evaluations in the paper be strictly stratified by node type.
> - **AHP Weight Provenance:** The $(0.35, 0.25, 0.25, 0.15)$ weights originate from a rank-one comparison matrix, documenting historical provenance rather than an absolute mathematical proof.

---

## 6. Engine 3: `EventSimulator` (Built-In Discrete-Event Engine — Supporting Baseline)

### 6.1 Quick Facts

| Dimension | Specification |
|:---|:---|
| **Source File** | [`saag/simulation/event_simulator.py`](../saag/simulation/event_simulator.py) |
| **Dependencies** | **Zero external dependencies** (pure Python standard library `heapq`) |
| **Output Metrics** | Flow paths, message drop counts, delivery rate, P99 latency |
| **Execution Cost** | ~20 ms per run |
| **Primary Role** | Primes healthy baseline communication flows for `FailureSimulator` |

---

### 6.2 Intuition & Zero-Dependency Event Loop

While `MessageFlowSimulator` relies on SimPy, `EventSimulator` is a lightweight discrete-event simulator with an internal priority queue (`heapq`) ordered by virtual simulation time:

```mermaid
sequenceDiagram
    participant App as Publisher App
    participant Topic as Topic
    participant Broker as Message Broker
    participant Sub as Subscriber App

    App->>Topic: Event: PUBLISH (t=0.010)
    Topic->>Broker: Event: ROUTE (t=0.012)
    Broker->>Sub: Event: DELIVER (t=0.017)
    opt Reliable Delivery
        Sub-->>App: Event: ACK (t=0.022)
    end
```

- **`PUBLISH`**: An application creates a message and submits it to its target topic.
- **`ROUTE`**: A message broker ingests the message from the topic and schedules forwarding.
- **`DELIVER`**: The message arrives at the subscriber application.
- **`ACK`**: An acknowledgment is returned if the topic contract requires `RELIABLE` delivery.
- **`DROP`**: A message is dropped if queues overflow, brokers fail, or timeouts expire.

---

### 6.3 Poisson Failures, Recoveries & M/G/1 Arrivals

1. **Poisson Failure Process ($\lambda$)**: Components fail according to a homogeneous Poisson process with rate $\lambda$ (inter-arrival times follow $\text{Exp}(\lambda)$).
2. **Exponential Recovery ($1 / \mu_{\text{rec}}$)**: If `mean_recovery_time > 0`, a `RECOVER_COMPONENT` event restores the component after $\text{Exp}(1 / \mu_{\text{rec}})$ seconds.
3. **M/G/1 Queueing**: Setting `poisson_arrivals=True` replaces fixed message intervals with exponential inter-arrival times $\text{Exp}(1 / \Delta t)$.

---

### 6.4 Primary Role: Baseline Flow Generation for `FailureSimulator`

In the SaG architecture, `EventSimulator` is primarily invoked by `SimulationService._prime_baseline_flows()`. Running `simulate_all_publishers()` under healthy conditions records every active `(publisher, topic, subscriber)` path, which `FailureSimulator` caches to compute the **Flow Disruption** metric ($15\%$ of $I_{\text{comp}}$).

---

### 6.5 How to Run `EventSimulator`

```python
from saag.simulation.graph import SimulationGraph
from saag.simulation.event_simulator import EventSimulator
from saag.simulation.models import EventScenario

sim_graph = SimulationGraph(graph_dict)
event_sim = EventSimulator(sim_graph)

# Simulate 100 messages with Poisson component failures
result = event_sim.simulate(EventScenario(
    source_app="RadarTracker",
    num_messages=100,
    duration=10.0,
    failure_rate=0.1,         # λ = 0.1 failures/sec
    mean_recovery_time=2.0,   # Exp(1/2) recovery time
    poisson_arrivals=True
))

print(f"Messages published: {result.metrics.messages_published}")
print(f"Delivery rate: {result.metrics.delivery_rate:.2f}%")
print(f"P99 latency: {result.metrics.p99_latency * 1000:.2f} ms")
```

---

## 7. Engine 4: `MessageFlowSimulator` (High-Fidelity Queuing & QoS — Dynamic Probe $I_{\text{dyn}}$)

### 7.1 Quick Facts

| Dimension | Specification |
|:---|:---|
| **Source File** | [`saag/simulation/message_flow_simulator.py`](../saag/simulation/message_flow_simulator.py) |
| **JSS Oracle Name** | Dynamic Queue-Flow Oracle ($I_{\text{dyn}}(v)$) |
| **Underlying Engine**| **SimPy** discrete-event queuing framework |
| **Output Metric** | $I_{\text{dyn}}(v) \in [0, 1]$ (Dynamic delivery rate drop under fault) |
| **Execution Cost** | Detailed (~5–30 s per node) |
| **Primary Role** | Offline convergent-validity research probe (JSS §7.3.2) |

---

### 7.2 Intuition & SimPy Architecture

Static graphs evaluate topological connectivity, but they cannot model **time**, **buffer saturation**, or **message rates**.

**Intuition**: Consider an autonomous vehicle with an obstacle detection topic running at 50 Hz. Even if the network graph is fully connected, if a subscriber's input queue fills up, new obstacle messages will be dropped or delayed past their 20 ms deadline. `MessageFlowSimulator` models a virtual clock where publishers emit messages at specific frequencies, queues accumulate packets, and subscribers process messages under DDS QoS contracts.

```mermaid
flowchart TD
    subgraph Pubs["Publishers (Periodic Emission Processes)"]
        P1["Sensor Publisher A<br>(f = 50 Hz, High Priority)"]
        P2["Sensor Publisher B<br>(f = 10 Hz, Normal Priority)"]
    end

    subgraph Topics["DDS Topics (Distribution Layer)"]
        T1["Topic: /camera/raw<br>(history_depth = 10, RELIABLE)"]
        T2["Topic: /lidar/points<br>(history_depth = 5, BEST_EFFORT)"]
    end

    subgraph Sub["Subscriber Application: ObstacleDetector"]
        direction TB
        subgraph Queues["Private Per-Topic FIFO Queues (No Cross-Topic HOL Blocking)"]
            Q1["Queue 1 (/camera/raw)<br>[ msg | msg | msg ] (Cap: 10)"]
            Q2["Queue 2 (/lidar/points)<br>[ msg | msg ] (Cap: 5)"]
        end

        subgraph Server["Shared Compute ServiceStation"]
            SS["PriorityResource (Worker Capacity μ = 65 Hz)<br>Arbitrates execution by transport_priority"]
        end

        subgraph Telemetry["Runtime QoS Telemetry"]
            M1["• End-to-End Latency: t_processed - t_created<br>• Deadline SLA Check: latency > deadline_ms<br>• Dynamic Delivery Rate Drop: I_dyn(v)"]
        end
    end

    P1 -->|emit| T1
    P2 -->|emit| T2
    T1 -->|fan-out| Q1
    T2 -->|fan-out| Q2
    Q1 -->|competes| SS
    Q2 -->|competes| SS
    SS --> Telemetry
```

---

### 7.3 Private Topic Queues vs. Shared Compute Server

To model distributed messaging accurately, `MessageFlowSimulator` enforces two architectural design rules:

1. **Per-Subscriber Private Queues**: Each subscriber maintains an independent FIFO buffer for each topic it reads. This prevents **cross-topic head-of-line (HOL) blocking**: a high-volume diagnostic stream cannot fill or stall the buffer of an emergency-stop topic.
2. **Shared Compute ServiceStation**: All messages entering a subscriber are processed by a single `ServiceStation` (representing its CPU thread/core allocation). This creates realistic multi-topic resource contention when bursts arrive simultaneously.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│              PRIVATE TOPIC QUEUES vs. SHARED COMPUTE STATION                │
├──────────────────────────────────────┬──────────────────────────────────────┤
│  Incoming Topic Feeds                │  Subscriber Internal Architecture   │
├──────────────────────────────────────┼──────────────────────────────────────┤
│  Topic A (50 Hz, High Priority) ──►  │  [ Private Queue A (depth = 10) ] ─┐ │
│                                      │                                    ▼ │
│  Topic B (100 Hz, Low Priority) ──►  │  [ Private Queue B (depth = 5)  ] ─┼─► [ Shared Compute Server ]
│                                      │                                    ▲ │   (μ = 65 Hz, PriorityResource)
│  Topic C (10 Hz, Best Effort)  ──►  │  [ Private Queue C (depth = 5)  ] ─┘ │
├──────────────────────────────────────┴──────────────────────────────────────┤
│  Result: Queues isolate overflow; Server arbitrates CPU execution order.    │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

### 7.4 Runtime DDS QoS Contract Enforcement

The engine enforces five standard DDS QoS policies dynamically:

| QoS Policy | Simulation Enforcement Mechanism |
|:---|:---|
| **Reliability (`RELIABLE`)** | Queue overflow triggers **head-drop** (drops oldest sample to retain freshest data, matching DDS `KEEP_LAST`). |
| **Reliability (`BEST_EFFORT`)** | Queue overflow triggers **tail-drop** (drops incoming message immediately). |
| **History Depth (`history_depth`)** | Limits maximum queue buffer size. Overflow triggers head-drop or tail-drop. |
| **Transport Priority (`transport_priority`)** | Orders message processing in `ServiceStation` via `simpy.PriorityResource`. High-priority messages jump ahead of normal traffic. |
| **Deadline (`deadline_ms`)** | Measures end-to-end latency ($t_{\text{processed}} - t_{\text{created}}$). Latencies exceeding deadline log SLA violations. |

```mermaid
flowchart TD
    MSG["New Incoming Message arrives at Queue"] --> CHK{"Is Queue Full?<br>(len == history_depth)"}
    
    CHK -- "No (Buffer Available)" --> ENQ["Enqueue message at tail"]
    
    CHK -- "Yes (Buffer Overflow)" --> QOS{"Check Topic Reliability QoS"}
    
    QOS -- "RELIABLE (KEEP_LAST)" --> HD["<b>Head-Drop:</b> Evict OLDEST sample from front<br>Insert new sample at tail (Freshness Priority)"]
    QOS -- "BEST_EFFORT" --> TD["<b>Tail-Drop:</b> Drop incoming NEW sample immediately<br>(Buffer preserved intact)"]
    
    ENQ --> SCHED{"ServiceStation Available?"}
    HD --> SCHED
    
    SCHED -- "Yes" --> PROC["Process message by transport_priority"]
    SCHED -- "No" --> WAIT["Wait in PriorityQueue order"]
    
    PROC --> LAT["Compute Latency = t_done - t_created"]
    LAT --> DDL{"Latency > deadline_ms?"}
    DDL -- "Yes" --> SLA["Log Deadline SLA Violation"]
    DDL -- "No" --> DELIV["Mark Message Successfully Delivered"]
```

---

### 7.5 Load Calibration ($\rho = 0.65$) & Operating Points

In real systems, QoS contracts only matter under contention:
- If utilization $\rho < 0.20$, queues never fill up and QoS policies never trigger.
- If utilization $\rho > 0.85$, queues explode and results degrade into noise.

`MessageFlowSimulator` automatically calibrates subscriber service rates to operate at a consistent **65% target utilization**:
$$E[S_s] = \frac{\rho}{\Lambda_s} \quad \text{where } \rho = 0.65$$
This allows QoS policies to bind realistically while ensuring high test-retest reproducibility ($\rho_{\text{stability}} = 0.93$).

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    UTILIZATION REGIMES & LOAD CALIBRATION                   │
├───────────────────┬─────────────────────────────────────────────────────────┤
│  ρ < 0.20 (Idle)  │  Queues empty, no contention. Deadlines never missed.   │
│                   │  QoS policies remain inactive (Uninformative).          │
├───────────────────┼─────────────────────────────────────────────────────────┤
│  ρ = 0.65 (TARGET)│  ★ CALIBRATED OPERATING POINT:                          │
│                   │  Queues form realistically, priority preemption binds,  │
│                   │  deadline violations occur under load (High Stability). │
├───────────────────┼─────────────────────────────────────────────────────────┤
│  ρ > 0.85 (Heavy) │  Queue explosion, runaway packet drops.                 │
│                   │  Results degrade into stochastic queueing noise.        │
└───────────────────┴─────────────────────────────────────────────────────────┘
```

---

### 7.6 Dynamic Delivery Loss ($I_{\text{dyn}}(v)$) & The Contention-Relief Phenomenon

When evaluating component criticality, `MessageFlowSimulator` injects a failure at midpoint ($t = t_{\text{fault}}$) and measures the resulting drop in delivery rate:

$$I_{\text{dyn}}(v) = \text{DeliveryRate}_{\text{pre-fault}} - \text{DeliveryRate}_{\text{post-fault}}$$

$$\text{DeliveryRate} = \frac{\text{Total Messages Delivered}}{\sum_{t \in \text{Topics}} (\text{Published}(t) \times \text{Subscribers}(t))}$$

#### Why $I_{\text{dyn}}$ is Strictly 1-Dimensional: The Contention-Relief Paradox

During empirical testing, an interesting physical phenomenon was uncovered: **Contention Relief**.

When a high-volume publisher crashes, it stops sending messages. On heavily loaded downstream subscribers, the sudden removal of this traffic **clears the queues**, allowing remaining messages to be processed faster:
- **Tail Latency ($\Delta L_{p95}$) vs. Delivery Loss ($I_{\text{dyn}}$)**: $\rho = -0.499$ (negative correlation!)
- **Deadline Violations vs. Delivery Loss ($I_{\text{dyn}}$)**: $\rho = -0.418$ (negative correlation!)

```mermaid
flowchart LR
    subgraph PreFault["Phase 1: Pre-Fault (t < t_fault) — Congested"]
        P_ON["Heavy Publisher ACTIVE<br>(e.g. 100 msg/s)"] -->|floods| Q_FULL["Queues Congested<br>Latency: HIGH<br>Deadline Violations: FREQUENT"]
    end

    subgraph FaultEvent["Phase 2: Injected Fault (t = t_fault)"]
        CRASH["Heavy Publisher CRASHES<br>(Traffic severed)"]
    end

    subgraph PostFault["Phase 3: Post-Fault (t > t_fault) — Contention Relief"]
        Q_DRAIN["Queues Drain & Clear<br>Latency: DROPS (-49.9%)<br>Deadline Misses: VANISH (-41.8%)"]
        DAMAGE["Delivered Traffic: DROPS<br><b>Damage Score: I_dyn > 0</b>"]
    end

    PreFault --> FaultEvent --> PostFault
```

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                 THE CONTENTION-RELIEF CANCELLATION TRAP                     │
├─────────────────────────────────────────────────────────────────────────────┤
│  If SaG combined delivery loss and latency into an additive metric:         │
│                                                                             │
│        Damage = w1 · ΔDeliveryLoss (+)  +  w2 · ΔLatencyDrop (-)            │
│                                                                             │
│  The positive delivery loss and negative latency delta would CANCEL OUT!    │
│  A catastrophic publisher crash would produce an apparent Damage score ≈ 0. │
├─────────────────────────────────────────────────────────────────────────────┤
│  Resolution: I_dyn is kept strictly 1-dimensional (delivery rate loss).     │
│  Google SRE's Four Golden Signals (Latency, Traffic, Errors, Saturation)    │
│  are computed and reported as structured telemetry alongside I_dyn.        │
└─────────────────────────────────────────────────────────────────────────────┘
```

#### SRE Four Golden Signals Telemetry

To provide complete observability without compromising $I_{\text{dyn}}(v)$'s scalar integrity, `MessageFlowSimulator` calculates Google Site Reliability Engineering (SRE)'s Four Golden Signals:

1. **Latency**: End-to-end traversal latency ($t_{\text{e2e}}$) decomposed into queue waiting time ($t_{\text{wait}} = t_{\text{dequeue}} - t_{\text{created}}$) and CPU compute service time ($t_{\text{service}} = t_{\text{delivery}} - t_{\text{dequeue}}$), with p50, p95, and p99 percentiles.
2. **Traffic**: Demand and throughput metrics, including published rate (Hz), delivered rate (Hz), message throughput (B/s), and bandwidth utilization (Kbps).
3. **Errors**: Total drop and failure counts, drop rate, deadline violations, queue overflow discards, best-effort drops, and unserved demand when publishers crash.
4. **Saturation**: System-wide compute resource utilization ($\rho$), peak CPU load, time-weighted average queue depth ($\int q(t)dt / T$), buffer occupancy ratio, and fraction of saturated subscribers.

These signals are captured for the overall simulation run (`overall_signals`) and windowed for fault events (`signals_before` and `signals_after`), exposing the exact dynamics of contention-relief or cascading failure.

#### Diagnostic Entity Impact Telemetry

Alongside the Golden Signals, `FaultEventRecord` captures structured diagnostic counts of the architectural cascade:
- **`lost_topics_count`** and **`lost_critical_topics_count`**: Total topics orphaned when their last publisher crashed, and how many of those topics carried `HIGH` or `CRITICAL` criticality.
- **`lost_applications_count`** and **`lost_critical_applications_count`**: Downstream subscriber applications that lost feeds due to orphaned topics, and how many carried `HIGH` or `CRITICAL` criticality.

These metrics serve strictly as **diagnostic operational telemetry** for human triage and CLI reporting; they are deliberately excluded from the $I_{\text{dyn}}(v)$ scalar objective to protect the Input-Label Independence Guarantee and avoid label masking.

---

### 7.7 JSS Study Simulation Protocol & Timing (Single-Component Lifecycle)

In the JSS manuscript (§7.3.2 and Supplementary §S9), `MessageFlowSimulator` evaluates inter-oracle convergent validity across the 12 benchmark architectures via [`reproduce/convergent_validity.py`](../reproduce/convergent_validity.py).

#### The Two-Window Simulation Lifecycle

When measuring the dynamic criticality of an individual component $v$ (e.g., `fault_node="ConflictDetector"`), the simulator divides the virtual timeline into two equal observation windows:

```
Virtual Time (s)
0.0 s                             30.0 s                            60.0 s
├─────────────────────────────────┼─────────────────────────────────┤
│    PRE-FAULT WINDOW (30 s)      │    POST-FAULT WINDOW (30 s)     │
│  • Steady-state traffic         │  • Component v crashed & dead   │
│  • Calibrated load (ρ = 0.65)   │  • Traffic severed or drained   │
│  • Baseline delivery rate:      │  • Surviving delivery rate:     │
│    DeliveryRate_before          │    DeliveryRate_after           │
└─────────────────────────────────┴─────────────────────────────────┘
                                   ▲
                          FAULT INJECTION (t = 30.0 s)
```

1. **Pre-Fault Window ($t = 0.0\,\text{s} \dots 30.0\,\text{s}$)**:
   - Publishers emit periodic messages; queues accumulate packets under calibrated load ($\rho = 0.65$).
   - The engine logs unperturbed delivery to establish $\text{DeliveryRate}_{\text{pre-fault}}$.
2. **Fault Injection Point ($t = 30.0\,\text{s}$)**:
   - Component $v$ terminates immediately. If $v$ is a publisher, its emission processes stop.
3. **Post-Fault Window ($t = 30.0\,\text{s} \dots 60.0\,\text{s}$)**:
   - The simulation continues for another 30 seconds as downstream subscribers starve or queues drain.
   - The engine logs surviving traffic to compute $\text{DeliveryRate}_{\text{post-fault}}$.
4. **Dynamic Impact Metric**:
   $$I_{\text{dyn}}(v) = \text{DeliveryRate}_{\text{pre-fault}} - \text{DeliveryRate}_{\text{post-fault}}$$

#### How Long Do You Need to Simulate One Component Failure?

There is an important distinction between **Virtual Simulation Time** (virtual clock inside SimPy) and **Wall-Clock Time** (real execution time on CPU):

* **Virtual Simulation Time: 60.0 Virtual Seconds**:
  - In the JSS study, every candidate run is configured with **`duration = 60.0`** and **`fault_time = 30.0`**. 30 seconds of pre-fault execution is sufficient for queues to reach stationary operating depth under $\rho = 0.65$, and 30 seconds post-fault accurately captures delivery rate disruption.
  - *(Note: Standalone exploratory runs in the CLI default to 300.0 s, but the 12-fold paper benchmark standardizes on 60.0 s).*
* **Real Wall-Clock Execution Time: ~1.1 to 3.5 Seconds per Component**:
  - Because SimPy advances an event queue rather than sleeping in real time, 60 virtual seconds execute in a few seconds on a standard CPU:
    - **ATM System** (26 applications, ~112,000 delivered messages): **~1.15 seconds** wall-clock time per component.
    - **Autonomous Vehicle System** (32 applications, ~298,000 delivered messages): **~3.18 seconds** wall-clock time per component.
    - **Large / High-Rate Scenarios** (e.g., Enterprise Mesh): **~5 to 15 seconds** wall-clock time per component.
* **Why Sweeps are Computationally Heavy**:
  - Because discrete-event simulation cannot evaluate all nodes in a single pass, calculating $I_{\text{dyn}}$ requires **one independent run per candidate component**. Evaluating 30 candidate applications in an AV system takes $30 \times 3.2\,\text{s} \approx 96\,\text{seconds}$.
  - This computational footprint is why $I_{\text{dyn}}$ serves as an **offline research probe**, while `FaultInjector` (~10 ms/node) supplies continuous labels for GNN training.

#### Exact JSS Study Configuration Parameters

To reproduce the exact convergent validity values reported in JSS Table S3.1:

| Parameter | JSS Value | Operational Rationale |
|:---|:---:|:---|
| `duration` | `60.0` | 60 simulated seconds ensures queue steady-state before failure. |
| `fault_time` | `30.0` | Symmetrical pre-fault and post-fault measurement windows. |
| `qos_mode` | `"full"` | Enforces all DDS QoS policies (`RELIABLE` head-drop, priority preemption, deadlines). |
| `target_utilization` | `0.65` | Calibrates subscriber service rates ($E[S_s] = \rho / \Lambda_s$) to 65% target load. |
| `seed` | `42` | Single seed across the 12 folds; `{42, 123, 456}` used on a 3-fold subset for test-retest. |
| `eval_population` | `"application"` | Restricts evaluation to the Application stratum to prevent Simpson's paradox. |

---

### 7.8 How to Run `MessageFlowSimulator`

#### Python API (Exact JSS 60s Study Configuration)
```python
from pathlib import Path
from saag.simulation.message_flow_simulator import MessageFlowSimulator
from saag.core.graph_io import load_graph

graph = load_graph(Path("data/scenarios/atm_system.json"))

# Configure single component failure using exact JSS study settings
sim = MessageFlowSimulator(
    graph=graph,
    duration=60.0,                   # 60 virtual seconds
    fault_node="ConflictDetector",   # Component to crash
    fault_time=30.0,                  # Fault injected at midpoint
    seed=42,
    qos_mode="full",                  # Full DDS QoS contract enforcement
    target_utilization=0.65           # Calibrated 65% load operating point
)

result = sim.run()
result.save(Path("output/simulation/message_flow_results.json"))

if result.fault_event:
    dr_before = result.fault_event.delivery_rate_before
    dr_after = result.fault_event.delivery_rate_after
    i_dyn = dr_before - dr_after
    print(f"Faulted Component:          {result.fault_event.faulted_node_id}")
    print(f"Pre-Fault Delivery Rate:    {dr_before:.4f}")
    print(f"Post-Fault Delivery Rate:   {dr_after:.4f}")
    print(f"Dynamic Impact Score I_dyn: {i_dyn:.4f}")
```

#### CLI Command (Exact JSS 60s Profile)
```bash
python cli/simulate_graph.py message-flow \
    --input data/scenarios/atm_system.json \
    --output output/simulation/ \
    --duration 60 \
    --fault-node ConflictDetector \
    --fault-time 30 \
    --qos-mode full \
    --target-utilization 0.65 \
    --seed 42 \
    --export-json
```

#### Reproducing the Full Multi-Scenario JSS Paper Sweep
To execute the multi-scenario convergent validity evaluation comparing $I_{\text{dyn}}$, $I^*$, and $I_{\text{comp}}$:
```bash
PYTHONPATH=. python reproduce/convergent_validity.py --scenarios atm_system --duration 60
```

---

### 7.9 Output Schema Snippet (`message_flow_results.json`)

```json
{
  "schema_version": "2.0",
  "graph_id": "atm_system",
  "simulation_duration": 60.0,
  "system_delivery_rate": 0.9975,
  "qos_mode": "full",
  "target_utilization": 0.65,
  "golden_signals": {
    "latency": {
      "mean_e2e_ms": 2.14,
      "mean_queue_wait_ms": 1.02,
      "mean_service_time_ms": 1.12,
      "p50_ms": 2.10,
      "p95_ms": 4.50,
      "p99_ms": 6.80
    },
    "traffic": {
      "published_rate_hz": 120.0,
      "delivered_rate_hz": 119.7,
      "throughput_bytes_per_sec": 122572.8,
      "throughput_kbps": 980.58,
      "total_messages_published": 7200,
      "total_messages_delivered": 7182,
      "total_bytes_delivered": 7354368
    },
    "errors": {
      "total_errors": 18,
      "error_rate": 0.0025,
      "deadline_violations": 12,
      "queue_overflow_drops": 6,
      "best_effort_drops": 0,
      "unserved_demand": 0
    },
    "saturation": {
      "mean_cpu_utilization": 0.648,
      "peak_cpu_utilization": 0.820,
      "mean_queue_depth": 1.45,
      "mean_queue_occupancy": 0.145,
      "peak_queue_occupancy": 0.600,
      "saturated_subscriber_ratio": 0.0
    }
  },
  "fault_event": {
    "fault_time": 30.0,
    "faulted_node_id": "ConflictDetector",
    "delivery_rate_before": 0.9982,
    "delivery_rate_after": 0.9810,
    "delivery_rate_drop": 0.0172,
    "latency_p50_before": 2.1,
    "latency_p50_after": 2.0,
    "qos_violations_count": 0,
    "signals_before": { "latency": "...", "traffic": "...", "errors": "...", "saturation": "..." },
    "signals_after": { "latency": "...", "traffic": "...", "errors": "...", "saturation": "..." }
  }
}
```

---

### 7.10 Academic Provenance: Convergent-Validity Research Probe

> [!NOTE]
> **JSS Manuscript Findings (§7.3.2, Supplementary §S9, Table S3.1):**
> Across the twelve evaluation scenarios on the Application population, $I_{\text{dyn}}$ agrees with $I^*$ (`FaultInjector`) at:
> - **Mean Spearman $\rho = \mathbf{0.627}$** (range $0.186$–$0.953$)
> - **Kendall $\tau = 0.493$**
> - **Top-$K$ Jaccard = $0.370$** (against $0.111$ expected by chance)
> 
> Because this correlation is substantial but distinctly below $I^*$'s test-retest ceiling ($0.811$–$1.000$), it confirms that topological graph rankings reflect real runtime communication dynamics without being redundant with them.

### 7.11 Runtime Engine: Reactive Behaviour, Deployment & Gray Failures (Opt-In)

The published engine (§7.1–7.10) has three structural limits:

- **Publishers ignore their inputs.** A starved relay keeps publishing on schedule, so no fault travels more than one hop. That is why $I_{\text{dyn}}$ reduces in closed form to the rate-weighted first-order expansion (§2.5).
- **Brokers and hosts are not on the data path**, so faulting them is a no-op.
- **Permanent crash-stop is the only failure mode.**

On the chain `A → t1 → B → t2 → C`, the published engine gives $I_{\text{dyn}}(A) = 0.50 < I_{\text{dyn}}(B) = 1.00$, because C stays fully fed when the source dies.

The runtime engine, in [`saag/simulation/runtime/`](../saag/simulation/runtime/), lifts all three limits. Every part is an opt-in argument whose default is the published engine. [`tests/test_simulation_golden_defaults.py`](../tests/test_simulation_golden_defaults.py) pins I* and $I_{\text{dyn}}$ bit for bit at the defaults, and the runtime label settings use their own cache key.

| Option (`MessageFlowSimulator`) | Default | What it adds |
|:---|:---|:---|
| `behavior_model="reactive"` | `open_loop` | Publication is gated on input freshness by a per-application trigger policy: `periodic`, `all_of`, `any_of` or `k_of_n:K` (degraded emission). The policy comes from the `trigger` attribute, or by default from `app_type`: sensor → periodic; processor/controller → all_of; gateway, service, monitor and actuator → any_of. An input is fresh while its last *delivered* sample (one that met its deadline) is younger than $\max(\text{deadline}, 3/f_t)$. Topics nobody publishes and self-published topics are never required inputs. A fault now stalls downstream relays; the record reports `stalled_apps`, `time_to_stall_s` and `cascade_hops`. |
| `rng_streams="per_process"` | `shared` | One RNG stream per publisher/subscriber process (common random numbers). |
| `estimator="paired"` | `windows` | Adds a no-fault baseline on the same streams and windows, cached per `(seed, fault_time)`. It reports `i_dyn_paired` = baseline-post − fault-post delivery. With per-process streams, a fault that touches nothing scores exactly 0.0. |
| `deployment_model="stations"` | `none` | Brokers become service stations on ROUTES. On a broker outage, traffic fails over to the lowest-id live broker after a $\max(1, 3/f)$ s lease. Co-located applications share their host's CPU (`cpu_cores`, default one core per resident). Host outages kill residents (`replica_aware` respected), and CONNECTS_TO reachability gates delivery. Brokers and hosts become labelled. |
| `hot_standby=True` | off | Components declaring `hotstandby` come back `failover_delay_s` after a crash. |
| `faults=[FaultSpec, …]` | — | The fault library below; the first entry is the reported fault. |
| `workload_model="mmpp"` | `declared` | Two-state Markov-modulated Poisson arrivals with the declared mean rate (`burst_ratio`, `burst_fraction`, `mean_burst_s`). No corpus topic declares bursts today. `runtime.workload.apply_zipf_rates` is a sensitivity arm for the three flat-rate scenarios. |
| `record_events=True` | off | Per-message trace in the calibrator's OpenTelemetry-shaped format (`result.export_events(path)`). |
| `telemetry_profile=…` | — | Measured rates, service-time distributions and trigger policies override the declared ones (§7.12). |

**Fault library** (`saag.simulation.runtime.faults.RuntimeFaultMode`). This enum is separate from `FailureSimulator`'s `FailureMode`, so the Validate oracle's semantics are unchanged.

| Mode | Effect | Parameters |
|:---|:---|:---|
| `crash` | Permanent crash-stop; a host takes its residents with it | — |
| `transient_crash` | Crash, then restart after `duration_s`; subscribers resume | `storm_copies` on restart |
| `freeze` | Stops serving and emitting; queues keep filling (GC pause) | `duration_s` |
| `slowdown` | Service time × k; on a host, every resident; on a broker, its station | `k`, or `k_mu`/`k_sigma` (log-normal) |
| `omission` | Drops each emission with probability p | `p` |
| `babbling` | Publishes at k × its rate | `k` |
| `buffer_leak` | Reader queue capacity shrinks by β slots/s | `beta` |
| `redelivery_storm` | Re-sends retained history `copies` times (the pub-sub retry storm) | `copies` |
| `link_flap` | Gilbert-Elliott good/bad on CONNECTS_TO link `a\|b`; bad = cut | `mean_good_s`, `mean_bad_s` |

For a bounded primary fault, the record also reports `time_to_recover_s`: the time from the end of the fault until delivery regains 95% of its pre-fault rate, measured on a 1 s timeline. It also reports the controller's `runtime_events` log.

**Runtime labels** (`saag-simulate runtime-sweep`, [`runtime/labels.py`](../saag/simulation/runtime/labels.py)). Every candidate (Applications, plus Brokers and Nodes under `stations`) is faulted under the runtime oracle settings, which are reactive, stations, per-process streams and paired. The sweep writes `runtime_labels.json` with these labels:

- `i_delivery`: paired volume-weighted delivery loss.
- `i_delivery_consumer`: the same, averaged per subscriber.
- `i_flow`: criticality-weighted end-to-end loss at the sinks. A faulted sink loses everything, so terminal consumers are not harmless as they are under I*.
- `i_timeliness`: signed change in the deadline-miss share; it is never clamped, because contention relief is real.
- `i_recovery`: recovery time, for bounded faults only.

Under the Order-$k$ Reference Criterion (§2.5), each label ships with its own truncation:

- `ref_delivery_first_order`: Eq. 7, generalised to brokers and hosts.
- `ref_flow_reach`: full-propagation sink reach. It saturates on dense systems and is reported but not binding.
- `ref_flow_policy`: a deterministic stall fixpoint under the same trigger policies, with no timing, staleness or queueing. This is the reference `i_flow` must beat.

A check of the identity: with `behavior_model="open_loop"`, paired `i_delivery` matches `ref_delivery_first_order` at $\rho = 0.998$ on `atm_system`.

> [!WARNING]
> **Exploratory, not results.** The numbers below are first measurements on two scenarios, two seeds and 30 s runs, taken while building the engine. Any comparison that will be reported must be pre-registered first.
> - `i_flow` vs `ref_flow_policy`: $\rho = 0.86$ (atm), $0.80$ (microservices).
> - `i_flow` vs I*: $0.22$ (atm), $-0.28$ (microservices).
> - `i_delivery` vs its first-order reference: $0.87$ / $0.85$.
> - No application has $i_{\text{flow}} = 0$, against 5 (atm) and 13 (microservices) with $I^* = 0$.
>
> **Fault-free cost of the reactive model.** Queueing jitter at $\rho = 0.65$ occasionally opens delivery gaps wider than a staleness window, so relays flap. Fault-free publication is 0–5.5% below open-loop on the five scenarios checked. Only stalls lasting at least one staleness window (and ≥ 1 s) at the end of a run count as `stalled_apps`.

### 7.12 Emulated DDS Testbed, Calibration & Fidelity (Opt-In)

Every oracle above is scored against something the framework computes. [`saag/testbed/`](../saag/testbed/) produces *observed* behaviour to compare against: the scenario runs as a real pub-sub system, and the same `FaultSpec`s are injected into it.

- **Deployment plan** ([`testbed/deployment.py`](../saag/testbed/deployment.py)). Records hosts and their residents, each topic's DDS QoS, and each application's trigger policy, required inputs, staleness windows and calibrated service time. The policy and staleness functions are the same ones the reactive model uses, so a fidelity gap reflects timing, queueing and transport, not the behavioural model.
- **Runtime** ([`testbed/runtime.py`](../saag/testbed/runtime.py)). Stub applications run one publisher thread per output, one worker per application (the shared station), bounded per-topic queues with the corpus overflow policy, and deadline checks. A **Broker** is a relay process: writers publish to an `ingress` partition and the routing broker republishes to `egress`. The relay with the lowest id and a live heartbeat routes.
- **Transport.** [`testbed/dds.py`](../saag/testbed/dds.py) uses Cyclone DDS (extra `testbed`) and maps reliability, durability, KEEP_LAST depth, deadline and transport priority. `LocalTransport` runs the whole scenario in-process without containers.
- **Chaos** ([`testbed/chaos.py`](../saag/testbed/chaos.py)). Host faults become `docker kill/start/pause/update`. Application faults become control files the agent polls. `link_flap` becomes `tc netem`, an approximation that cuts all of one endpoint's traffic. `buffer_leak` and `redelivery_storm` are reported as unsupported.
- **Calibration** ([`saag/simulation/calibration.py`](../saag/simulation/calibration.py)). `TelemetryCalibrator` fits topic rates and per-application service-time distributions on the pre-fault steady state. It infers trigger policies from how emissions respond to input outages, and leaves a policy unset when the trace never shows one. It never writes `edge_failure_correlation` (a cascade *probability* in `FailureSimulator`, not a drop rate) or starvation bounds.
- **Fidelity** ([`saag/validation/fidelity.py`](../saag/validation/fidelity.py)). Compares steady state (topic rate, per-reader delivery rate, p50/p95 latency, deadline-miss share against declared tolerances) and fault response (impact rank agreement, stall-set Jaccard). It is a Tier-2 report, never a gate.

```bash
saag-simulate testbed build --input data/scenarios/atm_system.json --output output/testbed/atm --duration 60
saag-simulate testbed run --output output/testbed/atm --duration 60 --faults faults.json --mode local
saag-simulate testbed calibrate --output output/testbed/atm
saag-simulate testbed fidelity --output output/testbed/atm --calibrated
```

`--mode local` needs nothing beyond the Python environment. `--mode docker` builds an image that downloads `python:3.11-slim` and `cyclonedds==0.10.5`, and runs one container per host with `NET_ADMIN`. **The Docker/DDS path has not been exercised yet**; only the in-process path is covered by tests (`tests/test_testbed.py`; the DDS round trip is marked `testbed`).

---

## 8. Engine 5: `ChangePropagationSimulator` (Maintainability Reference — Oracle $I_M$)

### 8.1 Quick Facts

| Dimension | Specification |
|:---|:---|
| **Source File** | [`saag/simulation/change_propagation.py`](../saag/simulation/change_propagation.py) |
| **JSS Oracle Name** | Change-Propagation Oracle ($I_M(v)$) |
| **Traversal Direction** | **Upstream** on transposed dependency graph ($G^\top$) |
| **Output Metric** | $I_M(v) \in [0, 1]$ (Development-time maintainability blast radius) |
| **Execution Cost** | Instant (<5 ms per node) |
| **Primary Role** | Maintainability reference; grounds GNN dimension masking ($m = [1, 0]$) |

---

### 8.2 Runtime Failure vs. Development-Time Interface Change

| Dimension | Runtime Failure Simulation ($I^*, I_{\text{comp}}$) | Change Propagation Simulation ($I_M$) |
|:---|:---|:---|
| **Trigger** | Component $v$ crashes at runtime | Component $v$'s interface/code changes at development time |
| **Direction** | Follows downstream dataflow | Follows upstream dependencies on $G^\top$ |
| **Stop Conditions** | Queue absorption, broker redundancy | Loose coupling, stable architectural interfaces |
| **Output Metric** | Operational cascade damage ($I^*(v), I_{\text{comp}}(v)$) | Architectural maintenance blast radius ($I_M(v)$) |

---

### 8.3 Transposed Graph Traversal ($G^\top$) & Stop Conditions

If component $u$ depends on component $v$ ($u \xrightarrow{\text{DEPENDS\_ON}} v$), then modifying $v$ may force $u$ to adapt. Therefore, change propagates in the **reverse direction** of dependencies:

1. Build transposed dependency graph $G^\top$: invert every edge $(u \to v)$ into $(v \to u)$.
2. Execute BFS starting at modified component $v$.
3. At each encountered node $u$, evaluate two stopping conditions:
   - **Loose-Coupling Stop**: If edge weight $w(u \to v) < \theta_{\text{loose}}$ (default: `0.20`), the dependent absorbs the change without requiring code adaptations.
   - **Stable-Interface Stop**: If node instability $\text{Instability}(u) < \theta_{\text{stable}}$ (default: `0.20`, indicating many incoming and few outgoing dependencies), the component acts as an architectural boundary and absorbs change obligations.

---

### 8.4 Maintainability Impact ($I_M(v)$)

`ChangePropagationSimulator` blends three normalized structural metrics:

$$I_M(v) = 0.45 \cdot \text{Change Reach} + 0.35 \cdot \text{Weighted Change Impact} + 0.20 \cdot \text{Normalized Depth}$$

- **`change_reach`**: Fraction of system components reached by the change ripple.
- **`weighted_change_impact`**: Importance-weighted adaptation cost.
- **`normalized_change_depth`**: Maximum upstream propagation depth reached.

---

### 8.5 How to Run `ChangePropagationSimulator`

```python
from saag.simulation.change_propagation import ChangePropagationSimulator
from saag.analysis.analyzer import StructuralAnalyzer
from saag.core.graph_io import load_graph

graph = load_graph("data/scenarios/atm_system.json")
analysis_res = StructuralAnalyzer().analyze_graph(graph, layer="system")

cps = ChangePropagationSimulator(theta_loose=0.20, theta_stable=0.20)
maintainability_results = cps.run_all(analysis_res)

for node_id, res in list(maintainability_results.items())[:5]:
    print(f"Component: {node_id:<20} | IM: {res.maintainability_impact:.4f} "
          f"| Change Reach: {res.change_reach:.4f}")
```

---

### 8.6 Academic Provenance: Multi-Task Loss Dimension Masking

> [!NOTE]
> **JSS Manuscript Findings (§4.2.1, §4.3):**
> - **Dimension Masking ($m = [1, 0]$):** Because dynamic cascade simulation ($I^*$) observes runtime failure reachability rather than source-code maintainability, maintainability ground truth is unobserved during dynamic simulation sweeps. The GNN multi-task objective applies a boolean dimension mask $m = [m_R, m_M] = [1, 0]$ to ensure the maintainability head $\hat{M}(v)$ is not penalized or driven to zero during training.
> - **Prohibition as a Training Label:** $I_M(v)$ is computed from the transpose of the derived `DEPENDS_ON` projection. Using it as a training label would constitute circular supervision, directly violating the Input–Label Independence Guarantee.

---

## 9. Quality Model Alignment (ISO/IEC 25010 & 25019)

The SaG simulation suite maps observed metrics directly to international software quality standards:

| ISO/IEC 25010 Characteristic | Observed Simulation Attribute | Mathematical Formula / Metric | Responsible Simulator Engine |
|:---|:---|:---|:---|
| **Effectiveness & Reliability** | Feed-loss cascade reach & path severing | $I^*(v) = \frac{1}{|\mathcal{S}|} \sum \text{sub\_loss}(s)$ | `FaultInjector` |
| **Structural Reliability** | Publisher-to-subscriber path reachability loss | $\text{reachability\_loss} = 1 - \frac{\text{paths}_{\text{post}}}{\text{paths}_{\text{pre}}}$ | `FailureSimulator` |
| **Availability & Fault Tolerance** | Infrastructure fragmentation & stranded message volume | $\text{fragmentation} = 0.70 \Delta\text{CC} + 0.30 \Delta\text{Mass}$ | `FailureSimulator` |
| **Operational Capacity** | QoS-weighted lost message bandwidth | $\text{throughput\_loss} = 1 - \frac{\sum \text{rate}_{\text{post}}}{\sum \text{rate}_{\text{pre}}}$ | `FailureSimulator` |
| **Time Behavior & Performance** | Real-time traffic delivery drop under contention | $I_{\text{dyn}}(v) = \text{DR}_{\text{before}} - \text{DR}_{\text{after}}$ | `MessageFlowSimulator` |
| **Modularity & Maintainability** | Upstream code change ripple on transposed dependencies | $I_M(v) = 0.45 \text{Reach} + 0.35 \text{Impact} + 0.20 \text{Depth}$ | `ChangePropagationSimulator` |

---

## 10. Worked Examples

### 10.1 Air Traffic Management (ATM) Step-by-Step Cascade

Consider an Air Traffic Management architecture:

```
RadarTracker ──PUBLISHES_TO──► T_radar   ──SUBSCRIBES_TO──► ConflictDetector
             ──PUBLISHES_TO──► T_tracks  ──SUBSCRIBES_TO──► ConflictDetector, ATCWorkstation, FlightDataProcessor

FlightDataProcessor ──PUBLISHES_TO──► T_fpa ──SUBSCRIBES_TO──► ATCWorkstation
ConflictDetector    ──PUBLISHES_TO──► T_conflicts ──SUBSCRIBES_TO──► ATCWorkstation
ASTERIX_Broker      ──ROUTES────────► All Topics
```

#### Simulation Results & Architectural Rationale

| Component | $I^*(v)$ (`FaultInjector`) | Cascade Depth | Architectural Rationale |
|:---|:---:|:---:|:---|
| **`RadarTracker`** | **1.000** | 1 | Sole producer of primary radar feeds. Its failure starves `ConflictDetector` and `FlightDataProcessor`, triggering a cascade that eliminates all feeds to `ATCWorkstation`. |
| **`ASTERIX_Broker`** | **1.000** | 1 | Sole routing broker for the entire system; its failure partitions all publish-subscribe message paths. |
| **`ConflictDetector`** | **0.111** | 0 | Only publishes `T_conflicts`. When it fails, `ATCWorkstation` loses 1 out of 3 feeds (partial degradation; no cascade). |
| **`FlightDataProcessor`**| **0.111** | 0 | Only publishes `T_fpa`. `ATCWorkstation` loses 1 out of 3 feeds. |
| **`ATCWorkstation`** | **0.000** | 0 | Pure sink subscriber. Its failure causes zero downstream feed loss. |

---

### 10.2 Autonomous Vehicle (AV) Multi-Oracle Stratification

Evaluating an Autonomous Vehicle system ($|V| = 152$ nodes, $|E| = 730$ edges) across the simulation engines demonstrates how each oracle reveals different architectural layers:

| Architectural Layer / Stratum | Evaluated Count | Mean $I^*$ (`FaultInjector`) | Mean $I_{\text{comp}}$ (`FailureSimulator`) | Mean $I_{\text{dyn}}$ (`MessageFlow`) |
|:---|:---:|:---:|:---:|:---:|
| **Infrastructure (Compute ECUs)** | 8 | — *(unlabeled)* | **0.2713** | — *(unobservable)* |
| **Application (Shared Libraries)**| 20 | **0.9436** | 0.0000 | — *(unobservable)* |
| **Middleware (Message Brokers)**  | 4 | 0.4882 | **0.0945** | — *(unobservable)* |
| **Application (Microservices)**   | 80 | 0.1905 | 0.0119 | **0.1842** |
| **Entire System (Pooled)**        | 112 | 0.3821 | 0.0381 | — |

**Key Insights**:
- `FaultInjector` focuses on application feeds and shared libraries ($I^* = 0.9436$ for libraries because multiple microservices crash when a core library fails).
- `FailureSimulator` captures infrastructure crash impact ($I_{\text{comp}} = 0.2713$ on compute ECUs due to multi-process host termination).
- `MessageFlowSimulator` evaluates continuous runtime delivery loss over active microservices ($I_{\text{dyn}} = 0.1842$).

---

## 11. Programmatic Python API Quickstart

For programmatic scripting, the simulation engines can be orchestrated directly:

```python
from pathlib import Path
from saag.core.graph_io import load_graph
from saag.simulation.fault_injector import FaultInjector
from saag.simulation.message_flow_simulator import MessageFlowSimulator

graph = load_graph("data/scenarios/atm_system.json")

# 1. FaultInjector sweep for GNN ground-truth labels
injector = FaultInjector(graph, seeds=[42, 123, 456, 789, 2024])
fi_res = injector.run(node_types=["Application", "Broker"])
fi_res.save(Path("output/simulation/impact_scores.json"))

# 2. Discrete-event queue flow simulation
sim = MessageFlowSimulator(graph, duration=300.0, fault_node="ConflictDetector", fault_time=150.0)
mf_res = sim.run()
mf_res.save(Path("output/simulation/message_flow_results.json"))
```

---

## 12. CLI Reference (`cli/simulate_graph.py`)

Access the simulation CLI via `cli/simulate_graph.py`.

### 12.1 Shared Options
```bash
python cli/simulate_graph.py [SUBCOMMAND] [OPTIONS]

Shared Options:
  --input PATH      Path to scenario graph JSON (e.g., data/scenarios/atm_system.json)
  --output DIR      Directory for output artifacts (default: output/simulation/)
  --export-json     Export results to structured JSON files
  --verbose, -v     Enable debug logging
```

---

### 12.2 `fault-inject` Subcommand

Runs multi-seed cascade reachability simulation to generate $I^*(v)$ training labels:

```bash
python cli/simulate_graph.py fault-inject \
    --input data/scenarios/atm_system.json \
    --output output/simulation/ \
    --seeds 42,123,456,789,2024 \
    --propagation-threshold 0.2 \
    --qos-factor ladder \
    --export-json
```

**Key Arguments**:
- `--seeds`: Comma-separated list of random seeds (default: `42,123,456,789,2024`).
- `--propagation-threshold`: Cascade sensitivity threshold (default: `0.2`).
- `--nodes`: Specific component IDs to inject (default: all eligible nodes).
- `--node-types`: Filter node types (default: `Application,Broker`).
- `--network-gating`: Route deliveries over `CONNECTS_TO` (§5.6a); gives `Node` and `NetworkDevice` failures labels of their own.

---

### 12.3 `message-flow` Subcommand

Runs SimPy discrete-event message flow simulation:

```bash
python cli/simulate_graph.py message-flow \
    --input data/scenarios/atm_system.json \
    --output output/simulation/ \
    --duration 300 \
    --fault-node ConflictDetector \
    --fault-time 150 \
    --qos-mode full \
    --target-utilization 0.65 \
    --seed 42 \
    --export-json
```

**Key Arguments**:
- `--duration`: Simulation virtual run time in seconds (default: `300.0`).
- `--fault-node`: Component ID to crash during the run.
- `--fault-time`: Simulation timestamp when fault triggers (default: midpoint).
- `--qos-mode`: QoS enforcement level (`none`, `contracts`, `recovery`, `full`, `legacy`).
- `--target-utilization`: Calibrated subscriber utilization target (default: `0.65`).

---

### 12.4 `combined` Subcommand

Executes both `fault-inject` and `message-flow` sequentially in a single pass:

```bash
python cli/simulate_graph.py combined \
    --input data/scenarios/atm_system.json \
    --output output/simulation/ \
    --seeds 42,123,456 \
    --duration 300 \
    --fault-node ConflictDetector \
    --export-json
```

---

## 13. Output Schemas Reference

### 13.1 `impact_scores.json` (Fault Injection Ground Truth)

Generated by `FaultInjector`, this artifact supplies the $I^*(v)$ supervised training labels:

```json
{
  "schema_version": "2.1",
  "graph_id": "atm_system",
  "labeler": "FaultInjector",
  "seeds_used": [42, 123, 456, 789, 2024],
  "labeled_node_types": ["Application", "Broker"],
  "labeled_dimensions": ["composite", "reliability"],
  "unlabeled_node_ids": ["N0", "N1", "T_radar", "T_tracks"],
  "label_stability": {
    "n_seeds": 5,
    "mean_std": 0.020109,
    "max_std": 0.129898,
    "test_retest_spearman": 0.97169,
    "topk_jaccard": 0.875
  },
  "records": {
    "RadarTracker": {
      "impact_score": 1.0,
      "impact_score_std": 0.0,
      "cascade_depth": 1,
      "impacted_subscribers": 3,
      "orphaned_topics": 2
    },
    "ConflictDetector": {
      "impact_score": 0.1111,
      "impact_score_std": 0.0,
      "cascade_depth": 0,
      "impacted_subscribers": 1,
      "orphaned_topics": 1
    }
  }
}
```

---

### 13.2 `message_flow_results.json` (Dynamic Discrete-Event Results)

Generated by `MessageFlowSimulator`:

```json
{
  "schema_version": "2.0",
  "graph_id": "atm_system",
  "simulation_duration": 300.0,
  "system_delivery_rate": 0.9975,
  "qos_mode": "full",
  "target_utilization": 0.65,
  "utilization_mode": "per_subscriber",
  "measured_utilization": {
    "ConflictDetector": 0.6478,
    "ATCWorkstation": 0.6512
  },
  "fault_event": {
    "fault_time": 150.0,
    "faulted_node_id": "ConflictDetector",
    "delivery_rate_before": 0.9982,
    "delivery_rate_after": 0.9810,
    "delivery_rate_drop": 0.0172,
    "latency_p50_before": 2.1,
    "latency_p50_after": 2.0,
    "qos_violations_count": 0
  }
}
```

---

## 14. Methodological Boundaries & Design Invariants

| # | Boundary / Invariant | Methodological Scope & Handling |
|:---|:---|:---|
| **I1** | **Input–Label Independence Guarantee** | All simulation engines operate strictly on raw structural edges ($G_{\text{structural}}$), never reading derived `DEPENDS_ON` edges. Circular reasoning is structurally impossible. |
| **I2** | **Ground-Truth Non-Interchangeability** | $I^*(v)$ (`FaultInjector`) and $I_{\text{comp}}(v)$ (`FailureSimulator`) represent fundamentally different quantities. $I^*(v)$ is the supervised target for GNNs; $I_{\text{comp}}(v)$ is the structural quality oracle for validation gates. Mixing them is a contract violation enforced by tests. |
| **I3** | **Offline Oracle Separation** | GNN inference (Step 3) requires zero runtime simulation calls. Simulators run purely offline to generate labels or validate predictions. |
| **B1** | **1-Dimensional $I_{\text{dyn}}$ (Contention Relief)** | Failing high-rate components reduces downstream latency and deadline violations on remaining traffic ($\rho = -0.499$). Therefore, $I_{\text{dyn}}$ is kept strictly 1-dimensional (delivery rate drop). |
| **B2** | **Single-Fault Sweeps** | Simulators evaluate one component failure per run to calculate isolated individual criticality. Cascades model propagation depth rather than simultaneous disjoint failures. |
| **B3** | **Observable Entity Scope** | Discrete-event message queuing processes observe active publishers and subscribers. Passive network cables and hardware hosts are unobservable to message queues and are evaluated structurally by `FailureSimulator`. |

---

## 15. What Comes Next

With ground-truth simulation artifacts generated:
- **[Step 3: Predict](prediction.md)**: Train Heterogeneous Graph Transformers (`HGT-QoS`) using the $I^*(v)$ labels generated by `FaultInjector`.
- **[Step 6: Validate](validation.md)**: Statistically evaluate predicted criticality scores against simulated ground-truth metrics using Spearman rank correlation ($\rho \ge 0.70$) and top-$K$ identification ($F_1@K$).
- **[Step 7: Prescribe](prescription.md)**: Apply automated refactoring operators and verify net risk reduction ($\Delta I_{\text{comp}} > 0, \Delta \text{SRI} > 0$) using `FailureSimulator`.

---

← [Step 4: Diagnose](diagnosis.md) | → [Step 6: Validate](validation.md)
