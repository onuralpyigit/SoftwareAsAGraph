# Software-as-a-Graph (SaG)

**Predict which components in a distributed system will cause the most damage when they fail — using only its architecture, before it is deployed.**

[![Python 3.11](https://img.shields.io/badge/python-3.11-blue.svg)](https://www.python.org/downloads/)
[![Next.js 14](https://img.shields.io/badge/Next.js-14-black)](https://nextjs.org/)
[![React 18](https://img.shields.io/badge/React-18-61DAFB)](https://react.dev/)
[![Neo4j 5.x](https://img.shields.io/badge/neo4j-5.x-green.svg)](https://neo4j.com/)
[![Docker](https://img.shields.io/badge/docker-compose-blue)](https://www.docker.com/)
![License](https://img.shields.io/badge/license-Apache--2.0-green)

[Architecture](ARCHITECTURE.md) | [Development Guide](CLAUDE.md) | [Methodology docs](docs/) | [Reproduction package](reproduce/)

---

## Table of Contents

1. [The Problem](#the-problem)
2. [The Eight Capabilities](#the-eight-capabilities)
3. [Quick Start](#quick-start)
4. [Reproducing the Published Results](#reproducing-the-published-results)
5. [Validation Gates](#validation-gates)
6. [Empirical Results](#empirical-results)
7. [Applying SaG to Your Own System](#applying-sag-to-your-own-system)
8. [RM Quality Model in Brief](#rm-quality-model-in-brief)
9. [Anti-Pattern Detection in Brief](#anti-pattern-detection-in-brief)
10. [Python SDK](#python-sdk)
11. [Further Reading](#further-reading)
12. [License](#license)

---

## The Problem

In distributed publish-subscribe systems (ROS 2/DDS, Apache Kafka, MQTT), some components are structurally far more critical than others — when they fail, failures cascade. Finding these weak points traditionally requires either runtime monitoring, which adds latency to systems that are often safety-critical, or waiting for a production incident, by which point the data loss or service disruption has already happened.

> [!IMPORTANT]
> **Core insight:** a component's position in the dependency graph reliably predicts its real-world failure impact — with no runtime data.

SaG operationalizes that insight as a framework: it models the architecture as a weighted directed graph, ranks components by predicted failure impact, verifies the ranking against simulated ground truth, and prescribes fixes — all before deployment.

---

## The Eight Capabilities

Each capability is a pipeline stage with its own CLI script, SDK method, and methodology document. `Generate` is an offline prep step used for experiments and benchmarks; real deployments start at **Model**.

| # | Capability | What it does | Key output | Docs |
|:--|:---|:---|:---|:---|
| — | *Generate (prep)* | Synthesizes a pub-sub topology for experiments, benchmarks, and CI regression | Topology JSON | [graph-generation.md](docs/graph-generation.md) |
| 1 | **Model** | Imports topology JSON into Neo4j as a weighted directed graph $G = (V, E, \tau_V, \tau_E, w)$; derives logical `DEPENDS_ON` edges via seven rules (the seventh only for modelled switches/routers); computes QoS-derived weights | $G_{\text{structural}}$, $G_{\text{analysis}}(l)$ | [graph-model.md](docs/graph-model.md) |
| 2 | **Analyze** | Deterministic, closed-form. Computes the 53-field structural metric vector $M(v)$ (19 of its fields feed the RM composite) — and nothing else | $M(v)$ metric vector | [structural-analysis.md](docs/structural-analysis.md) |
| 3 | **Predict** | Pathway B: Blast-radius forecasting via closed-form structural references (direct-dependent count $\text{InDeg}$, $\text{Analytic } I^*$, rate-weighted queue expansion) or learned GNN rankers ($\text{GAT-P-QoS}$, HGT, hybrids); computes deterministic ISO-RM composite $Q^*(v)$ as input feature and zero-checkpoint fallback | Structural & GNN ranks, Top-K shortlist | [prediction.md](docs/prediction.md) |
| 4 | **Diagnose** | Pathway A: deterministic ISO-RM dimension scores $Q^*(v)$ and 5-level classification, grounded in ISO/IEC 25010/25019; detects 19 anti-patterns; generates natural-language explanations; links to stage 3's ranking via the Triage Bridge to map Top-K risks to stakeholder actions — needs no GNN checkpoint (zero-GNN cold start) | RM/$Q^*(v)$ scores, Triage profile, anti-pattern report | [diagnosis.md](docs/diagnosis.md) |
| 5 | **Simulate** | Injects faults and propagates cascades over the raw structural graph to obtain ground-truth impact — training labels for stage 3 and the offline oracle for stage 6 | $I^*(v)$ composite and per-dimension $I_R, I_M$ (itself $\alpha\cdot I_{FT}+(1-\alpha)\cdot I_A$) | [failure-simulation.md](docs/failure-simulation.md) |
| 6 | **Validate** | Correlates predictions against simulated ground truth: Spearman $\rho$, Kendall $\tau$, F1, predictive gain, bootstrap CIs, Wilcoxon — scored against seven gates | Statistical evidence of predictive validity | [validation.md](docs/validation.md) |
| 7 | **Prescribe** | Generates stakeholder-oriented architectural edits (topic splits, host reallocations, QoS upgrades) and accepts each only if a closed-loop counterfactual simulation confirms the improvement | `PrescribeResult` with baseline vs. mutated SRI | [prescription.md](docs/prescription.md) |
| 8 | **Visualize** | Renders network graphs, dependency matrices, cascade heatmaps, and RM radar charts | Self-contained `dashboard.html` | [visualization.md](docs/visualization.md) |

```
                              ┌────────────────────────────────────────┐
                              │ Topology JSON / Architecture Artifacts │
                              └───────────────────┬────────────────────┘
                                                  │ [Step 1: Model]
                                                  ▼
                               ┌──────────────────────────────────────┐
                               │  Neo4j Graph (G_struct & G_analysis) │
                               └───────┬──────────────────────┬───────┘
                                       │                      │
                                       │                      ▼ [Step 5: Simulate] (Offline Oracle)
                                       │             ┌───────────────────────────────────┐
                                       │             │ Discrete-Event Failure Simulator  │
                                       │             │ (FaultInjector / FailureSimulator)│
                                       │             │  • Operates on G_structural       │
                                       │             │  • Produces labels I*(v), I_comp  │
                                       │             └─────────┬───────────────┬─────────┘
                                       ▼ [Step 2: Analyze]     │ (trains)      │ (ground-truth)
                         ┌─────────────────────────────┐       │               │
                         │  StructuralAnalysisResult   │       │               │
                         │  (53-field Metric Vector M) │       │               │
                         └──────┬───────────────┬──────┘       │               │
                                │               │              │               │
              [Step 3: Predict]      [Step 4: Diagnose]                        │
                                │               │                              │
                                ▼               ▼                              │
                     ┌──────────────────┐ ┌───────────────────────────┐        │
                     │NodeCriticalityGNN│ │ QualityAnalyzer (ISO RM)  │        │
                     │(16-D QoS / HGT)  │ │ • Fault Tolerance (FT)    │        │
                     │• Forecasts I*(v) │ │ • Availability (SPOF)     │        │
                     └────────┬─────────┘ │ • Maintainability (CQP)   │        │
                              │           └─────────────┬─────────────┘        │
                              ▼                         │                      │
                     ┌──────────────────┐               │                      │
                     │Top-K Risk Targets│               │                      │
                     └────────┬─────────┘               │                      │
                              │                         │                      │
                              └──► [ Triage Bridge ] ───┘                      │
                                           │                                   │
                                           ▼ [Step 4: Diagnose Output]         │
                             ┌───────────────────────────┐                     │
                             │ Scoped Diagnosis & Smells │                     │
                             │ (SPOF, Hubs, Explanations)│                     │
                             └─────────────┬─────────────┘                     │
                                           │                                   │
                                           ├───────────────────────────────────┼──► [Step 6: Validate]
                                           ▼ [Step 7: Prescribe]               │    (Spearman ρ, F1,
                             ┌───────────────────────────┐                     │     Validation Gates)
                             │ Remediation Gating Engine │                     │
                             │ (DevOps / Arch / Dev)     │                     │
                             └─────────────┬─────────────┘                     │
                                           │ candidate edit Δ(G)               │
                                           ▼                                   │
                             ┌───────────────────────────┐                     │
                             │ In-Memory Counterfactual  │                     │
                             │ Simulation (κ·σ_seed gate)│                     │
                             └─────────────┬─────────────┘                     │
                                           │ [Verified Improvement]            │
                                           ▼ [Step 8: Visualize]               │
                             ┌───────────────────────────┐                     │
                             │ Interactive SMART / HTML  │                     │
                             │ Visualisation Dashboard   │                     │
                             └───────────────────────────┘
```

The analytical pipeline is an explicit dual-pathway architecture: **Step 3 (Predict, Pathway B)** isolates critical risk hotspots ($\hat{I}^*$), while **Step 4 (Diagnose, Pathway A)** grounds root-cause attribution in ISO/IEC 25010/25019 standards. The **Triage Bridge** channels Step 3's high-risk components into Step 4's targeted root-cause profiles, and Step 7 applies closed-loop counterfactual simulation on $G_{\text{structural}}$ before accepting any remediation. See [ARCHITECTURE.md](ARCHITECTURE.md#system-pipeline--data-flow) for package boundaries, formal contracts, and sequencing rules.

---

## Quick Start

### Prerequisites

- **Python 3.11** — the only version exercised in CI. `pyproject.toml` declares `>=3.9`, but that is untested.
- **Neo4j 5.x** — required for the Model stage and anything downstream of it. No GDS or APOC plugins are needed.
- **Node.js 18+** — only if you want the SMART web dashboard.

### 1. Install

The `torch-scatter` / `torch-sparse` wheels must come from PyG's index before the extras are installed. This is the sequence CI uses ([tests.yml](.github/workflows/tests.yml)):

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install --upgrade pip

pip install torch==2.5.0 --index-url https://download.pytorch.org/whl/cpu
pip install torch-scatter==2.1.2 torch-sparse==0.6.18 \
  -f https://data.pyg.org/whl/torch-2.5.0+cpu.html

pip install -e ".[all]"
```

### 2. Start Neo4j

```bash
docker run -d --name neo4j -p 7474:7474 -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/password neo4j:5.23.0
```

> [!WARNING]
> `neo4j` / `password` are local-development defaults. Change them before any shared deployment. Note that the Python CLI reads the `NEO4J_URI`, `NEO4J_USERNAME` (falling back to `NEO4J_USER`), and `NEO4J_PASSWORD` environment variables directly — it does not load the root `.env` file, which is consumed only by Docker Compose and Next.js.

### 3. Run the pipeline

```bash
# All stages (Model → Analyze → Simulate → Predict → Diagnose → Validate → Prescribe → Visualize)
python cli/run.py --all --layer system

# Or one stage at a time; see the CLI table in ARCHITECTURE.md
python cli/import_graph.py --input data/system.json --clear
python cli/analyze_graph.py --layer system
python cli/predict_graph.py --layer system     # Step 3 (Pathway B) + Step 4 bundled by default
python cli/visualize_graph.py --layer system --output output/dashboard.html --open
```

On a first run there is no GNN checkpoint, so `run.py` skips the Predict stage entirely rather than crashing — but the Diagnose stage still runs (RM scores, anti-patterns, explanation need no checkpoint at all). `predict_graph.py` itself never runs a GNN unless `--gnn-model` is passed — it always computes deterministic RM scoring, with Diagnose bundled in unless `--no-diagnose` is given (see `saag-diagnose` for Step 4 standalone). To enable the GNN, run Simulate to produce labels, train, then pass the checkpoint **directory**:

```bash
python cli/simulate_graph.py fault-inject --input data/system.json --export-json   # FaultInjector labels → output/simulation/impact_scores.json
python cli/train_graph.py --layer system --simulated output/simulation/impact_scores.json
python cli/predict_graph.py --layer system --gnn-model models/gnn_checkpoints
```

### 4. Optional — the full stack in Docker

```bash
docker compose up --build
```

One container publishes the SMART dashboard on `:7000`, the FastAPI server on `:8000` (`/docs` for OpenAPI), and Neo4j on `:7474` / `:7687`.

### 5. Run the tests

```bash
make test                    # standard developer test suite (<15s)
make check                   # pre-merge gate: lint + fast tests
pytest                       # full suite; needs no live Neo4j
pytest --quick               # fast developer run skipping slow tests
pytest -m "not integration"  # skip anything requiring a database
```

---

## Reproducing the Published Results

[`reproduce/`](reproduce/) is a self-contained package that regenerates every table and figure in the paper. It has its own [README](reproduce/README.md), [EXPERIMENTS.md](reproduce/EXPERIMENTS.md), pinned [Dockerfile](reproduce/Dockerfile), and [Makefile](reproduce/Makefile).

```bash
# Exact environment (recommended)
docker build -t sag-jss -f reproduce/Dockerfile .
docker run --rm -v $(pwd)/results:/workspace/results sag-jss

# Or locally, after the install above
make -f reproduce/Makefile smoke-test   # ~15-30 min, 50 epochs, 2 seeds
make -f reproduce/Makefile all          # ~6-12 h CPU, 5 seeds
```

| Target | Produces | Notes |
|:---|:---|:---|
| `block0` | W1 QoS pipeline audit | **Go/no-go gate** — must pass before `table3`. Runs `tests/test_qos_pipeline_audit.py` and `tests/test_baselines.py` |
| `table3` | `results/table3_main_results.tex` | Main results: 7 core domains × 6 variants × 5 seeds (210 runs). Depends on `block0`; `--resume` makes it restartable |
| `table4` | `results/table4_loso_results.tex` | **LOSO** — leave-one-scenario-out, measures the cross-domain generalization gap |
| `kfold` | `results/table4_kfold_results.tex` | **Per-domain repeated k-fold** — the in-domain protocol. Opt-in, not part of `all`: ~5× more model fits than LOSO for the same seed count |
| `figure4` | `results/figure4_stratified_rho.pdf` | Per-node-type stratified $\rho$ |
| `figure5` | `output/atm_case_study/attention_subgraph.pdf` | ATM attention-weight case study |
| `jss-figures` | `docs/research/jss/latex/figures/Figure_1..3.pdf`, `Figure_S1..S2.pdf` | The three manuscript figures plus the two supplementary ones ([docs/research/jss/latex/](docs/research/jss/latex/)) |
| `all` | everything above except `kfold` | |

Budget roughly 6–12 h on 8 CPU cores, or 1–2 h on a CUDA GPU. Ablations (`reproduce/qos_label_ablation.py`, `threshold_sensitivity.py`, `ahp_sensitivity.py`, `reversed_projection_ablation.py`, `hardening_budget.py`, `convergent_validity.py`) run separately and are documented in `reproduce/EXPERIMENTS.md`.

Shell orchestrators for longer sweeps live in [`scripts/`](scripts/): `verify_pipeline.sh` (end-to-end smoke), `run_main_table.sh`, `populate_loso_cache.sh`, `train_all_variants.sh`, `run_production_pipeline.sh`, `recalibrate.sh`.

---

## Validation Gates & Two-Tiered Triage

Stage 6 scores each run against validation gates defined in [`ValidationTargets`](saag/validation/models.py#L10) and evaluated in [`ValidationService`](saag/validation/service.py#L713); full definitions are in [validation.md](docs/validation.md).

| Tier | Gate | Threshold | Focus |
|:---|:---|:---|:---|
| 1 | G1 — Spearman $\rho$ | $\ge 0.70$ | Monotonic ranking accuracy |
| 1 | G2 — F1 | $\ge 0.75$ | Critical component capture rate |
| 1 | G3 — Precision | $\ge 0.80$ | High-precision triage accuracy |
| 1 | G4 — Top-5 overlap | $\ge 0.60$ | Severe blast-radius capture |
| 2 | G5 — Predictive gain over degree baseline | $> 0.03$ | Structural signal addition |
| 2 | G6 — Weighted $\kappa_{\text{CTA}}$ | $\ge 0.70$ | Maintainability agreement |
| 3 | G8 — Bottleneck precision | $\ge 0.70$ | Single point of failure detection |

### Practical Guidance: The Two-Tiered Triage Protocol
As demonstrated in the JSS paper (§8.1, Figure 5), recovering 80% of the true critical set under reachability cascade $I^*$ requires reviewing approximately 40–45% of Applications by the best rankers. A gate designed to catch the majority of critical services would flag nearly half the system, making reachability ranking **unsuitable as an automated pass/fail blocking CI/CD gate**. SaG instead recommends a **two-tiered triage protocol**:
1. **Tier 1 (Commit Stage):** Compute training-free closed-form metrics (publish–subscribe afferent coupling $\text{InDeg}$ and rate-weighted queue expansion) in milliseconds, surfacing the top-5 critical services as an informational, non-blocking note in PR reviews.
2. **Tier 2 (Staging / Sprint Review):** Run discrete-event simulation ($I^*, I_{\text{dyn}}$) on the Tier-1 shortlisted candidates within a fixed inspection budget, reserving architectural refactoring (replication, topic splitting, QoS hardening) for verified bottlenecks.

---

## Empirical Results

Evaluated on the benchmark corpus in [`data/scenarios/`](data/scenarios/): twelve synthetic architectures forming the inductive cross-validation folds (1,321 Applications, 2,461 nodes, 10,918 edges), plus five hand-authored models of production open-source systems (Autoware ROS 2, Online Boutique, Train-Ticket, Home Assistant, EdgeX Foundry; 351 nodes, 700 edges) withheld from all training folds. See [docs/scenario.md](docs/scenario.md) for full provenance and manifests.

### 1. Leave-One-Scenario-Out (LOSO) Cross-Validation (JSS Table 3)

The primary benchmark evaluates ranking accuracy across 12 unseen architectures under Leave-One-Scenario-Out (LOSO) cross-validation against the primary reachability oracle $I^*$:

| Ranker Type | Model / Method | Evaluation Substrate | Mean LOSO $\rho$ [95% CI] | Active $\rho_{>0}$ | Overlap@$K$ |
|:---|:---|:---|:---:|:---:|:---:|
| **Reference** *(Rule Restatements)* | **Analytic $I^*$** | Dependency Graph | **0.808** $[0.755, 0.853]$ | 0.631 | 0.536 |
| | **InDeg** *(Afferent Coupling)* | Dependency Graph | **0.764** $[0.674, 0.840]$ | 0.516 | 0.504 |
| | **Reach** *(Transitive)* | Dependency Graph | **0.732** $[0.674, 0.782]$ | 0.286 | 0.344 |
| **Baseline** *(Comparator)* | **Topo-QoS** | Dependency Graph | **0.553** $[0.443, 0.657]$ | 0.280 | 0.388 |
| | Topo-QoS (corrected) | Dependency Graph | 0.533 $[0.404, 0.650]$ | — | — |
| **Learned** *(Dependency Graph)* | **GAT-P-QoS** | Dependency Graph | **0.748** $[0.704, 0.789]$ | 0.440 | 0.454 |
| | HGT-P-QoS | Dependency Graph | 0.514 $[0.392, 0.636]$ | 0.237 | 0.380 |
| **Learned & Hybrid** *(Raw Multigraph)*| GAT-QoS | Raw Multigraph | 0.635 $[0.567, 0.696]$ | 0.338 | 0.438 |
| | HGT-QoS | Raw Multigraph | 0.622 $[0.547, 0.690]$ | 0.312 | 0.426 |
| | Hybrid-HGT | Raw Multigraph | 0.657 $[0.572, 0.733]$ | 0.345 | 0.435 |
| | **Hybrid-GAT** | Raw Multigraph | **0.683** $[0.603, 0.753]$ | 0.362 | 0.450 |

*(Note: $\text{GAT-P-QoS}$ reaches $\rho = 0.772$ as a five-seed ensemble; see JSS Table 4).*

### 2. Independent Simulation Oracles (JSS Table 4)

- **Queue-Flow Simulator ($I_{\text{dyn}}$):** Requiring 12.7 CPU-hours of SimPy simulation to label the corpus, $I_{\text{dyn}}$ is dominated by first-order delivered-rate loss. A closed-form **Rate-Weighted First-Order Expansion** reaches $\rho = \mathbf{0.830} \; [0.778, 0.872]$ in **milliseconds** without training. It outperforms learned surrogate models trained on its labels ($\text{GBM-P-QoS}\to\text{dyn}$ at $\rho = 0.799$; rate-fed GNNs at $\le 0.665$), and learners started from the formula capture no additional signal.
- **Multi-Criteria Simulator ($I_{\text{comp}}$):** Measures multi-objective fragmentation and flow loss ($0.35\,\text{RL} + 0.25\,\text{FR} + 0.25\,\text{TL} + 0.15\,\text{FD}$). Training-free scores aligned with fragmentation rank highest (raw total degree $\rho = 0.719$, $\text{Topo-QoS}$ $\rho = 0.702$), while pure learned rankers collapse ($\le 0.334$).

### 3. Key Takeaways

1. **Representation dominates model complexity:** The derived dependency graph ($G_{\text{dep}}$, Rules 1 & 5) supplies the active signal. On the raw multigraph, messages never reach Applications, resulting in null confirmatory contrasts ($p = 0.266$).
2. **Learning vs. Afferent Coupling:** Counting direct dependents ($\text{InDeg}$, $\rho = 0.764$) is not significantly different from the best learned model ($\text{GAT-P-QoS}$, $\rho = 0.748$). No learned model shows additional predictive skill beyond simple dependency derivation on these first-order oracles.
3. **Hybrids beat the baseline, not their base learners:** Hybrids improve over the weak $\text{Topo-QoS}$ baseline ($+0.103$ and $+0.130$), but not over their own base learners. Given $\text{InDeg}$ as prior, hybrids simply reproduce it ($\pm 0.012$).
4. **Zero-Shot Transfer Boundaries:** On five open-source system models, learned models filter inert components well ($\rho \approx 0.81$), but fail to discriminate active propagating components ($\rho_{>0} \le 0.342$), where only transitive reach ($\text{Reach}$, $\rho_{>0} = 0.871$) restates the cascade structure.
5. **Cost & Green AI Feasibility:** Computing $\text{InDeg}$ runs in $0.4$–$15.6$ ms ($17$–$176\times$ cheaper than $I^*$ simulation). For $I_{\text{dyn}}$, the rate-weighted reference runs in milliseconds ($\approx 1$ J), whereas upfront simulation ($355.6$ Wh) and GNN training ($0.22$ kWh) are never amortized across continuous CI/CD runs. Training-free dependency analysis is the definitive low-cost, energy-efficient choice for routine pre-deployment review.

---

## Applying SaG to Your Own System

Real deployments skip `Generate` and start at **Model** with a topology JSON describing your architecture: `nodes`, `brokers`, `topics` (with QoS policy), `applications`, and `libraries`. The schema and a worked example are in [graph-model.md](docs/graph-model.md); [`data/system.json`](data/system.json) is a 50-application reference instance, and [`examples/run_worked_example.py`](examples/run_worked_example.py) verifies weight and `DEPENDS_ON` derivation end to end in memory.

The graph model maps to any pub-sub middleware:

| Graph concept | ROS 2 / DDS | Apache Kafka | MQTT |
|:---|:---|:---|:---|
| **Application** | ROS node | Producer / consumer | MQTT client |
| **Topic** | ROS topic | Kafka topic | MQTT topic |
| **Broker** | DDS participant | Kafka broker | MQTT broker |
| **Infrastructure Node** | Host / container | Broker host | Broker server |
| **Library** | ROS package dependency | Maven artifact | Paho client library |

---

## RM Quality Model in Brief

Criticality here is a **Quality-in-Use** construct in the ISO/IEC 25019:2023 (SQuaRE) sense: the degree to which the failure, latency or degradation of a component — directly or transitively — reduces the system's capacity to enable its stakeholders to achieve their goals. It is a *consequence*, carrying no estimate of how likely that failure is. The formal definitions (D1–D4), and how this differs from FMECA criticality, assigned integrity levels such as SIL/ASIL, and topological critical-node detection, are in [criticality.md](docs/criticality.md).

The construct spans all three SQuaRE quality views, and they are kept apart deliberately: criticality is **computed** from internal quality evidence (topology plus static code metrics), **validated** against simulated external quality (service delivered under fault), and **defined** on Quality-in-Use ([three views](docs/criticality.md#30-three-quality-views-internal-external-and-quality-in-use)).

It decomposes into two ISO/IEC 25010:2023 characteristics computed on the derived dependency graph, where edges point from *dependent* to *dependency*. Reliability is **hierarchical**: its Fault Tolerance and Availability sub-characteristics are scored individually and combined via a declared blend. Each characteristic/sub-characteristic identifies the failure **mechanism**; the Quality-in-Use characteristic that attribute's loss threatens is the **harm** ([full binding](docs/criticality.md#35-binding-rm-to-external-quality-dependability-and-quality-in-use)).

The same dimensions score components and relationships alike: the dimension fixes the harm, the scope fixes the mechanism.

| Dimension | For a component | For a relationship | External quality attribute | Quality-in-Use harm | Stakeholder |
|:---|:---|:---|:---|:---|:---|
| **R — Reliability** (hierarchical) | Combines the two rows below via α=0.36 | Combines the two rows below | Reliability | Efficiency, Effectiveness, Satisfaction | Reliability engineer / DevOps / SRE |
| ↳ **FT — Fault Tolerance** | Its failure spreads to transitive dependents | It carries the spread between endpoints | Reliability → fault tolerance | Efficiency, then Satisfaction | Reliability engineer |
| ↳ **A — Availability** | Its loss partitions the dependency graph | It is the only route, both endpoints healthy | Reliability → availability | Effectiveness, Freedom from risk | DevOps / SRE |
| **M — Maintainability** | It resists safe change | It forces both sides to change together | Maintainability → modularity, modifiability | Efficiency (engineering) | Software architect |

Vulnerability/Security was scored as a third dimension in an earlier revision of this framework and has been **retired outright** — not folded into another dimension — because its ground-truth evidence was the weakest of the (then) four dimensions and no fault-model instrument could validate it by construction ([full rationale](docs/criticality.md#35-binding-rm-to-external-quality-dependability-and-quality-in-use)). These now map onto two of the standard dependability attributes; **safety is not covered** — no hazard class or functional integrity field exists in the schema, so these scores locate structural exposure and cannot discharge a safety argument.

Each cell traces to one of the sub-definitions D1.R, D1.FT, D1.A, D1.M (components) and their D2 counterparts (relationships) in [criticality.md](docs/criticality.md#43-the-rm-decomposition-model), where each is stated as D1 or D2 restricted to a single mechanism.

The composite is $R(v) = 0.36\, FT(v) + 0.64\, A(v)$, then $Q(v) = 0.80\, R(v) + 0.20\, M(v)$. These are DECLARED constants — not AHP output — algebraically re-derived from the retired 4-D composite (A=0.43, R=0.24, M=0.17, V=0.16) by dropping Vulnerability and renormalising ([full derivation](docs/structural-analysis.md#5-composite-criticality-score-q)). An equal-weight baseline ($w_R=w_M=0.5$, $\alpha=0.5$) is available via `--equal-weights` for comparison.

Scores map to five tiers using adaptive box-plot thresholds derived from the system's own score distribution: **CRITICAL** above the upper fence $Q_3 + 1.5 \cdot IQR$, then **HIGH** above $Q_3$, **MEDIUM** above the median, **LOW** above $Q_1$, **MINIMAL** at or below $Q_1$. Below 12 components the classifier falls back to fixed percentiles (top 10% → CRITICAL).

The per-dimension formulas, all Tier-1 metric definitions, and the composite's re-parameterisation derivation are in [structural-analysis.md](docs/structural-analysis.md) and [criticality.md](docs/criticality.md).

---

## Anti-Pattern Detection in Brief

Stage 4 (Diagnose) audits its own RM output against a catalog of **19** structural anti-patterns — 5 CRITICAL (SPOF, SYSTEMIC_RISK, GOD_COMPONENT, FAILURE_HUB, COMPOUND_RISK), 5 HIGH (CYCLE, BRIDGE_EDGE, BOTTLENECK_EDGE, BROKER_OVERLOAD, DEEP_PIPELINE), and 9 MEDIUM. (TARGET and EXPOSURE, keyed on the now-retired Security dimension, were retired along with it — the catalog was 21 before that.) Trigger conditions and remediations for each are specified in [antipatterns.md](docs/antipatterns.md) and [remediation.md](docs/remediation.md); the catalog itself lives in [`antipattern_detector.py`](saag/analysis/antipattern_detector.py#L35).

```bash
python cli/detect_antipatterns.py --layer system --output output/antipatterns.json
```

> [!TIP]
> Exit codes make this a pre-merge gate: **0** clean, **1** warnings/smells, **2** critical or high patterns detected.

---

## Python SDK

`saag.Pipeline` is a fluent builder; stages execute in dependency order regardless of the order you chain them.

```python
import saag

result = (
    saag.Pipeline.from_json("data/system.json", clear=True)
        .analyze(layer="app")                      # structural metrics only
        .simulate(layer="app", mode="exhaustive")  # ground-truth labels (offline oracle)
        .predict()                                 # Pathway B: GNN ranking (if checkpoint exists) or RM fallback
        .diagnose(k=10)                            # Pathway A: RM scores + anti-patterns + Triage Bridge (Top-10)
        .validate()                                # statistical validation
        .prescribe()                               # counterfactual-verified remediations
        .visualize(output="output/report.html")
        .run()
)

for layer, v in result.validation.layers.items():
    print(f"{layer}: rho={v.spearman_rho:.3f}  F1={v.f1_score:.3f}")

if result.diagnosis and result.diagnosis.triage:
    print(f"Top-{result.diagnosis.triage.k} High-Risk Components identified via Triage Bridge:")
    for entry in result.diagnosis.triage.entries:
        print(f"  [{entry.rank}] {entry.component_id} ({entry.level}): {entry.priority_action} -> Roles: {', '.join(entry.roles)}")

if result.prescription:
    print(f"SRI improvement = {result.prescription.sri_improvement:.4f}")
```

| Class | Import path | Purpose |
|:---|:---|:---|
| [Pipeline](saag/pipeline.py#L12) | `saag.Pipeline` | Fluent builder that sequences and runs the stages |
| [Client](saag/client.py#L14) | `saag.Client` | Step-by-step service façade for finer control |
| [AnalysisResult](saag/models.py#L161) | `saag.AnalysisResult` | Stage 2 — structural metrics vector $M(v)$ |
| [PredictionResult](saag/models.py#L215) | `saag.PredictionResult` | Stage 3 — GNN ranks (or RM fallback) |
| [DiagnosisResult](saag/models.py#L366) | `saag.DiagnosisResult` | Stage 4 — RM scores, anti-patterns, explanation, triage result |
| [TriageResult](saag/analysis/triage.py#L31) | `saag.analysis.triage.TriageResult` | Stage 4 — Scoped Top-K root-cause profiles and stakeholder roles |
| [ValidationResult](saag/models.py#L421) | `saag.ValidationResult` | Stage 6 — per-layer correlations and gate outcomes |
| [PrescribeResult](saag/prescription/models.py#L212) | `saag.prescription.PrescribeResult` | Stage 7 — accepted policy, per-edit verdicts and SRI delta |
| [DiagnosticUseCase](saag/usecases/diagnostic.py#L22) | `saag.usecases.DiagnosticUseCase` | Pathway A application interactor (ISO-RM quality attribution) |
| [PredictiveUseCase](saag/usecases/predictive.py#L17) | `saag.usecases.PredictiveUseCase` | Pathway B application interactor (HGT blast-radius forecasting) |
| [TriageUseCase](saag/usecases/triage.py#L12) | `saag.usecases.TriageUseCase` | Triage Bridge application interactor (scoping diagnosis to Top-K) |

More runnable examples — including a round-trip persistence check and per-stage ATM walkthroughs — are in [`examples/`](examples/).

---

## Further Reading

Routed by what you're trying to do — each doc also links forward/back along the pipeline itself (Model → Analyze → Predict → Diagnose → Simulate → Validate → Prescribe → Visualize).

| I want to... | Start here |
|---|---|
| **Run this on my own system** | [cli-pipeline-guide.md](docs/cli-pipeline-guide.md) (every flag, every stage) → [graph-model.md](docs/graph-model.md) (input format) |
| **Understand what a score means** | [structural-analysis.md](docs/structural-analysis.md) §10–11 (the metric catalogue and RM formulas) → [criticality.md](docs/criticality.md) (what "criticality" is defined to mean, and its validity limits) |
| **Look up a quality attribute, coefficient, or constant** | [quality-model.md](docs/quality-model.md) (the RM models layer by layer: every attribute, the measure that operationalizes it, its coefficient and provenance, for components and edges alike) |
| **Find and fix architectural problems** | [diagnosis.md](docs/diagnosis.md) (RM root-cause attribution + Triage Bridge) → [antipatterns.md](docs/antipatterns.md) (the 19-pattern catalog) → [remediation.md](docs/remediation.md) (which patterns are auto-fixable) → [prescription.md](docs/prescription.md) (the closed-loop verifier) |
| **Check the evidence behind a claim** | [failure-simulation.md](docs/failure-simulation.md) (the two ground-truth engines) → [validation.md](docs/validation.md) (the statistical battery and gates) → [scenario.md](docs/scenario.md) (which scenario backs which published number) |
| **Reproduce the published results** | [reproduce/README.md](reproduce/README.md) and [reproduce/EXPERIMENTS.md](reproduce/EXPERIMENTS.md) (the protocol) → [scenario.md](docs/scenario.md) (the corpus) |
| **Extend or contribute to the framework** | [ARCHITECTURE.md](ARCHITECTURE.md) (package boundaries, data flow) → [CLAUDE.md](CLAUDE.md) (conventions, test-enforced invariants) |
| **Generate synthetic topologies** | [graph-generation.md](docs/graph-generation.md) |
| **Read GNN prediction or dashboard internals** | [prediction.md](docs/prediction.md) · [diagnosis.md](docs/diagnosis.md) · [visualization.md](docs/visualization.md) · [statistics.md](docs/statistics.md) |

**Formal specifications** — [SRS](docs/requirements/SRS.md) · [SAD](docs/design/SAD.md) · [SDD](docs/design/SDD.md) · [STD](docs/tests/STD.md) · [SAR](docs/tests/SAR.md) · [User Manual](docs/user/SUM.md)

**Research** — [reproduce/](reproduce/) for the reproduction package; [docs/research/](docs/research/) for paper sources; [docs/research/methodology/](docs/research/methodology/) for the literature positioning, empirical-validation detail, and construct-validity material split out of the practitioner docs above to keep those short.

The primary research contribution is the demonstration that topological graph metrics reliably predict real-world failure impact without runtime instrumentation. Supporting contributions: the six dependency derivation rules; the RM decomposition; the MPCI (Multi-Path Coupling Index) metric; the directed $AP_c$ single-point-of-failure score; adaptive box-plot classification; and the structural independence guarantee separating the predictor from the simulation oracle.

---

## License

Apache-2.0. See [`LICENSE`](LICENSE).

> [!WARNING]
> The `LICENSE` file currently contains **GPL-3.0** text and `pyproject.toml` declares `MIT` — neither matches the intended Apache-2.0 license. Both need to be corrected; this is tracked as a known discrepancy, not a documentation error.
