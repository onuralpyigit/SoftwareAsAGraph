# CLI Pipeline Execution Guide

## Prerequisites & Setup

### Neo4j

Start a local Neo4j instance (default Bolt URI `bolt://localhost:7687`, user `neo4j`, password `password`). Ensure the database is reachable before running stages that require it (Steps 1–7 unless `--input` is provided for file-mode utilities).

### Python Environment

All CLI scripts must be invoked from the project root with the project root on `PYTHONPATH`:

```bash
PYTHONPATH=. python cli/<script>.py [args]
```

Some scripts (e.g. `generate_graph.py`) add the project root to `sys.path` when run directly, but `PYTHONPATH=.` is required for consistent imports across every stage.

### Dependencies

Install the project's Python dependencies (including `torch`, `torch-geometric`, `networkx`, `numpy`, `scipy`, `pyyaml`, `saag`).

### Environment Variables

Neo4j credentials can be set via environment variables to avoid repeating flags:

```bash
export NEO4J_URI=bolt://localhost:7687
export NEO4J_USERNAME=neo4j
export NEO4J_PASSWORD=password
```

All CLI scripts read these via `cli/common/arguments.py`. `NEO4J_USER` also
works as a fallback, but `NEO4J_USERNAME` is what `.env`, `docker-compose.yml`,
and the API (`saag/infrastructure/config.py`) use — set that one if you're
running the CLI and the API/Docker stack against the same credentials.

---

## Quick Start

First-time runs should train a GNN checkpoint before using `--all`:

```bash
# 1. Generate + import + analyze + simulate
PYTHONPATH=. python cli/run.py --all --layer system

# 2. Produce FaultInjector training labels (training never simulates on its own)
PYTHONPATH=. python cli/simulate_graph.py fault-inject --input data/system.json --output output/simulation/ --export-json

# 3. Train GNN checkpoint
PYTHONPATH=. python cli/train_graph.py --layer system --simulated output/simulation/impact_scores.json --output output/gnn_checkpoints/best_model

# 4. Full pipeline with predictions
PYTHONPATH=. python cli/run.py --all --layer system --gnn-model output/gnn_checkpoints/best_model
```

The master orchestrator `cli/run.py --all` emits a first-run guard warning if `--gnn-model` is absent and no default checkpoint exists at `output/gnn_checkpoints/best_model`.

---

## Stage 0: Offline Prep — Generate

**Script:** `cli/generate_graph.py`  
**Purpose:** Produce synthetic pub-sub topology JSON files.

```bash
PYTHONPATH=. python cli/generate_graph.py --scale medium --output output/graph.json
```

or with a domain:

```bash
PYTHONPATH=. python cli/generate_graph.py --scale small --domain atm --output data/atm_system.json
```

### Arguments

| Flag | Default | Choices / Type | Description |
|------|---------|----------------|-------------|
| `--scale` | `None` (falls back to `medium`) | `tiny`, `small`, `medium`, `large`, `jumbo`, `xlarge` | Scale preset |
| `--config` | `None` | Path | YAML configuration file (mutually exclusive with `--scale`) |
| `--output` | `output/graph.json` | Path | Output JSON path |
| `--seed` | `42` | int | Random seed |
| `--domain` | `None` | `av`, `iot`, `finance`, `healthcare`, `hub-and-spoke`, `microservices`, `enterprise`, `atm` | Realistic naming domain |
| `--scenario` | `None` | same as `--domain` | Scenario mapping for QoS generation |
| `--connection-density` | `0.3` | float | Probability of `connects_to` edges |
| `--verbose` / `-v` | `False` | flag | Debug logging |
| `--quiet` / `-q` | `False` | flag | Suppress console output |

### Subcommands

- **`batch`** — generate multiple scenario datasets.
  ```bash
  PYTHONPATH=. python cli/generate_graph.py batch --input-dir data/ --output-dir output/ --seeds 42,123,456 --manifest batch_manifest.json --report batch_report.json
  ```
- **`validate`** — topology-class validation for scenarios.
  ```bash
  PYTHONPATH=. python cli/generate_graph.py validate --input-dir output/ --report validation_report.json
  ```

### Output

- `output/graph.json` (or path given by `--output`)

---

## Step 1: Model — Import & Export

**Scripts:** `cli/import_graph.py`, `cli/export_graph.py`  
**Purpose:** Load JSON topology into Neo4j, derive `DEPENDS_ON` relationships, and optionally export back.

### Import

```bash
PYTHONPATH=. python cli/import_graph.py --input output/graph.json --clear --dry-run
```

**Arguments** (shared Neo4j + runtime flags apply):

| Flag | Default | Description |
|------|---------|-------------|
| `--input` | required | Input JSON file |
| `--clear` | `False` | Clear existing DB before import |
| `--dry-run` | `False` | Validate input without importing |
| `--output` / `-o` | `None` | Save import statistics JSON |

Default Neo4j connection flags: `--uri bolt://localhost:7687`, `--user neo4j`, `--password password`.

### Export

```bash
PYTHONPATH=. python cli/export_graph.py --output output/exported_graph.json --format analysis --layer system --include-structural
```

**Arguments:**

| Flag | Default | Choices | Description |
|------|---------|---------|-------------|
| `--output` / `-o` | required | Path | Output JSON path |
| `--format` | `persistence` | `persistence`, `analysis` | Export format |
| `--include-structural` | `False` | flag | Include `RUNS_ON`, `ROUTES`, etc. in `analysis` format |
| `--layer` | `system` | `app`, `infra`, `mw`, `system` | Layer for `analysis` format |

---

## Step 2: Analyze

**Script:** `cli/analyze_graph.py`  
**Purpose:** Compute structural metrics M(v) and the graph summary S(G) only — no RM/Q scoring, no anti-pattern detection. RM/Q scoring and anti-pattern detection belong to Step 4 (Diagnose); GNN ranking belongs to Step 3 (Predict). `analyze_graph.py` takes no RM-weighting flags because scoring is not this stage's job. See [structural-analysis.md §1](structural-analysis.md#1-executive-summary--at-a-glance).

```bash
PYTHONPATH=. python cli/analyze_graph.py --layer system
```

### Arguments

| Flag | Default | Choices / Type | Description |
|------|---------|----------------|-------------|
| `--layer` | `system` | `app`, `infra`, `mw`, `system`, `all`, comma-separated | Analysis layer(s). `all` runs `app`, `infra`, `mw`, `system` sequentially. |
| `--output` / `-o` | `None` | Path | Save analysis results JSON |

Multi-layer output filenames append the layer (e.g. `analysis_app.json`, `analysis_system.json`).

The RM-weighting flags (`--use-ahp`, `--equal-weights`, `--ahp-shrinkage`, `--norm`, `--winsorize`, `--sensitivity`) belong to Step 4 below.

---

## Step 3: Predict

**Script:** `cli/predict_graph.py` (`saag-predict`)  
**Purpose:** Pathway B — optional Heterogeneous Graph Transformer neural inference ranking components by predicted blast radius, always computing the deterministic ISO-RM composite first as its own input feature and zero-checkpoint fallback. Step 4 (Diagnose: anti-pattern detection, explanation, Triage Bridge) is bundled into this script by default for backward compatibility with its pre-split behaviour — pass `--no-diagnose` to run Step 3 alone.

```bash
# RM fallback (cold start, zero GNN) + Step 4 bundled (anti-patterns, Triage)
PYTHONPATH=. python cli/predict_graph.py --layer system --triage-k 10

# GNN inference with checkpoint + Step 4 bundled + Triage Bridge
PYTHONPATH=. python cli/predict_graph.py --layer system --gnn-model output/gnn_checkpoints/best_model --triage-k 10 --output output/prediction.json

# Step 3 alone — GNN ranking only, no anti-patterns/explanation/triage/exit-code gating
PYTHONPATH=. python cli/predict_graph.py --layer system --gnn-model output/gnn_checkpoints/best_model --no-diagnose
```

### Arguments

**Pathway B (Predictive / HGT) GNN Inference:**

| Flag | Description |
|------|-------------|
| `--gnn-model` | Path to trained checkpoint directory (enables HGT blast-radius forecasting) |

**Diagnose (Step 4) bundling:**

| Flag | Description |
|------|-------------|
| `--no-diagnose` | Run Step 3 alone — skip anti-pattern detection, explanation, Triage, and CI/CD exit-code gating |

The RM weighting, Triage bridge, and anti-pattern flags documented under Step 4 below are also accepted here and forwarded to the bundled Diagnose call (ignored when `--no-diagnose` is set).

**Output & Gating:**

| Flag | Description |
|------|-------------|
| `--output` / `-o` | Write full prediction JSON (includes RM profiles, GNN scores, and — unless `--no-diagnose` — the Triage shortlist) |
| `--output-antipatterns` | Write standalone anti-pattern report JSON (feeds `visualize_graph.py`); no-op with `--no-diagnose` |
| `--no-exit-code` | Always exit 0 (disable CI/CD gating) |

### Exit Codes (CI/CD Quality Gating)

- `0` — clean (no anti-patterns detected, or `--no-antipatterns` / `--no-diagnose` / `--no-exit-code`)
- `1` — MEDIUM anti-patterns detected (warnings/smells)
- `2` — HIGH or CRITICAL anti-patterns detected → blocks pre-merge deployment gate

---

## Step 3b: Train GNN

**Script:** `cli/train_graph.py`  
**Purpose:** Train a Heterogeneous Graph Transformer (HGT) to predict component criticality.

Labels come only from a `FaultInjector` file (`simulate_graph.py fault-inject`, see [Step 5](#step-5-simulate)); the script never runs a simulator itself and exits if `--simulated` is missing.

```bash
PYTHONPATH=. python cli/train_graph.py --layer system --simulated output/simulation/impact_scores.json --epochs 500 --hidden 128 --heads 8 --checkpoint output/gnn_checkpoints/
```

### Arguments

| Flag | Default | Type / Choices | Description |
|------|---------|----------------|-------------|
| `--layer` | `app` | `app`, `infra`, `mw`, `system` | System layer |
| `--structural` | `None` | Path | Skip Step 2, load pre-computed metrics JSON |
| `--simulated` | `None` | Path | **Required** (except `--variant topology_rm`). `FaultInjector` label file — the `impact_scores.json` written by `simulate_graph.py fault-inject`. `FailureSimulator` output is rejected |
| `--rm` | `None` | Path | Skip Step 4, load RM scores JSON |
| `--input` | `None` | Path | Topology JSON (raw structural graph, as in `loso_evaluate.py`). Required when `--structural` and `--rm` are both files; `--multi-scenario` dirs likewise need a `topology.json` |
| `--hidden` | `64` | int | Hidden dimension |
| `--heads` | `4` | int | Attention heads |
| `--layers` | `3` | int | GNN layers |
| `--dropout` | `0.2` | float | Dropout rate |
| `--epochs` | `300` | int | Max epochs |
| `--lr` | `3e-4` | float | Learning rate |
| `--patience` | `30` | int | Early stopping patience |
| `--train-ratio` | `0.6` | float | Train split |
| `--val-ratio` | `0.2` | float | Validation split |
| `--no-edge-model` | `False` | flag | Skip edge model |
| `--seeds` | `None` | ints | Seed list for stability validation |
| `--multi-scenario` | `False` | flag | Inductive training on all domain scenarios |
| `--mode` | `gnn` | `rm`, `gnn` | Evaluation path for final summary |
| `--variant` | `hetero_qos` | `hetero_qos`, `homo_unweighted`, `homo_scalar`, `topology_rm` | Model architecture variant |
| `--checkpoint` | `output/gnn_checkpoints` | Path | Checkpoint directory |
| `--output` | `None` | Path | Save result JSON |

---

## Step 4: Diagnose

**Script:** `cli/diagnose_graph.py` (`saag-diagnose`)  
**Purpose:** Pathway A — deterministic ISO-RM scoring, 19-pattern anti-pattern detection, natural-language explanation generation, and stakeholder-oriented root-cause attribution via the Triage Bridge. Needs no GNN checkpoint (zero-GNN cold start); when a Step 3 result is available it reuses that stage's RM pass rather than recomputing it.

```bash
# RM scoring + anti-patterns + Triage, no GNN checkpoint required
PYTHONPATH=. python cli/diagnose_graph.py --layer system --triage-k 10

# AHP-weighted RM composite, grouped by stakeholder role
PYTHONPATH=. python cli/diagnose_graph.py --layer system --use-ahp --triage-k 10 --by-stakeholder --output output/diagnosis.json
```

### Arguments

**Pathway A (Diagnostic / RM) Weighting:**

| Flag | Description |
|------|-------------|
| `--use-ahp` | AHP-derived dimension weights |
| `--equal-weights` | q_reliability=q_maintainability=0.5, r_alpha=0.5 — equal at every level of the RM composite (baseline ablation) |
| `--ahp-shrinkage` | λ blending factor (default `0.7`) |
| `--norm` | Normalization applied to Tier-1 metrics before the RM weighted sum: `robust` (rank-based, default), `minmax`, `zscore`, or `rank` |
| `--winsorize` | Cap raw metric values above the 95th percentile before normalization |
| `--sensitivity` | Run Kendall τ weight sensitivity analysis after scoring |

**The Triage Bridge (Quantitative Risk → Stakeholder Diagnosis):**

| Flag | Description |
|------|-------------|
| `--triage-k` | Shortlist the Top-K critical components (RM-ranked here; GNN-ranked when this diagnosis was joined to a Step 3 result that had a checkpoint) and print each one's RM root-cause diagnosis: pattern signature, elevated dimensions, priority action, and mapped stakeholder roles (`DevOps / SRE`, `Architect`, `Developer`) |
| `--by-stakeholder` | Group Triage remediation actions by stakeholder role |

**Anti-Pattern Detection (CI/CD Quality Gate):**

| Flag | Default | Description |
|------|---------|-------------|
| `--no-antipatterns` | `False` | Skip detection; exit code always 0 |
| `--severity` | `None` | Comma-separated filter: `critical`, `high`, `medium` |
| `--pattern` | `None` | Comma-separated pattern IDs (e.g. `SPOF,FAILURE_HUB,GOD_COMPONENT`) |
| `--catalog` | `False` | Print full 19-pattern catalog and exit |

**Output & Gating:**

| Flag | Description |
|------|-------------|
| `--output` / `-o` | Write full diagnosis JSON (RM profiles, anti-patterns, and Triage shortlist) |
| `--output-antipatterns` | Write standalone anti-pattern report JSON (feeds `visualize_graph.py`) |
| `--no-exit-code` | Always exit 0 (disable CI/CD gating) |

### Exit Codes (CI/CD Quality Gating)

- `0` — clean (no anti-patterns detected, or `--no-antipatterns` / `--no-exit-code`)
- `1` — MEDIUM anti-patterns detected (warnings/smells)
- `2` — HIGH or CRITICAL anti-patterns detected → blocks pre-merge deployment gate

---

## Step 5: Simulate

**Script:** `cli/simulate_graph.py`  
**Purpose:** Fault injection and discrete-event message-flow simulation.

```bash
PYTHONPATH=. python cli/simulate_graph.py fault-inject --input data/atm_system.json --output output/simulation/ --seeds 42,123,456,789,2024 --export-json
```

### Subcommands

#### `fault-inject`

Systematic BFS cascade fault injection → `impact_scores.json`.

| Flag | Default | Description |
|------|---------|-------------|
| `--input` | `None` | Graph JSON file |
| `--layer` | `None` | Resolves to `data/<layer>.json` if `--input` missing |
| `--output` | `output/simulation/` | Output directory |
| `--nodes` | `None` | Comma-separated node IDs |
| `--node-types` | `Application,Broker,Library` | Comma-separated node types. Do **not** add `Topic` or `Node` — the cascade cannot express their failure and every instance scores `I(v)=0`. |
| `--seeds` | `42,123,456,789,2024` | Comma-separated seeds. ≥ 2 required for the artifact's `label_stability` block to be measurable. |
| `--cascade-depth` | `0` | Max depth (0 = unlimited) |
| `--propagation-threshold` | `0.2` | Fraction of feed loss before cascade |
| `--failure-model` | `step` | `threshold_cdf`: per-subscriber latent tolerance θ ~ U(0.1, 0.5) instead of the cliff at the threshold ([failure-simulation.md §4.8](failure-simulation.md#48-opt-in-threshold_cdf-failure-model)). Default reproduces the published labels. |
| `--export-json` | `False` | Write JSON result files |

The emitted `impact_scores.json` is schema 2.1: it names its `labeler`, declares
`labeled_node_types` / `labeled_dimensions` / `unlabeled_node_ids`, and carries a
`label_stability` block giving the ceiling on any correlation computed against it. See
[failure-simulation.md §6.1](failure-simulation.md#131-impact_scoresjson-fault-injection-ground-truth).

#### `message-flow`

Discrete-event SimPy simulation.

| Flag | Default | Description |
|------|---------|-------------|
| `--input` | `None` | Graph JSON file |
| `--layer` | `None` | Resolves to `data/<layer>.json` |
| `--output` | `output/simulation/` | Output directory |
| `--duration` | `100.0` | Simulation duration (seconds) |
| `--fault-node` | `None` | Node ID to fault |
| `--fault-time` | `None` | Simulated fault time (default: `duration / 2`) |
| `--seed` | `42` | Random seed |
| `--default-rate` | `10.0` | Fallback publish rate (Hz) |
| `--default-queue-size` | `100` | Fallback broker queue capacity |
| `--export-json` | `False` | Write JSON result files |

Runtime-engine flags ([failure-simulation.md §7.11](failure-simulation.md#711-runtime-engine-reactive-behaviour-deployment--gray-failures-opt-in)); every default is the published engine:

| Flag | Default | Description |
|------|---------|-------------|
| `--behavior-model` | `open_loop` | `reactive`: publication gated on input freshness, so faults propagate |
| `--deployment-model` | `none` | `stations`: brokers and host CPUs on the data path; brokers and hosts labelled |
| `--rng-streams` | `shared` | `per_process`: common random numbers |
| `--estimator` | `windows` | `paired`: same-stream no-fault baseline, reports `i_dyn_paired` |
| `--replica-aware` | off | A host outage kills a multi-hosted component only when all its hosts are down |
| `--hot-standby` / `--failover-delay` | off / `1.0` | `hotstandby` components return after the delay |
| `--workload-model` | `declared` | `mmpp`: bursty arrivals, same mean rate |
| `--staleness-periods` | `3.0` | Reactive input-freshness window, in periods |
| `--fault-mode` | `crash` | Runtime fault mode for `--fault-node` (`transient_crash`, `freeze`, `slowdown`, `omission`, `babbling`, `buffer_leak`, `redelivery_storm`, `link_flap`) |
| `--fault-duration` | `None` | Duration of a bounded fault |
| `--fault-param KEY=VALUE` | — | Fault parameter (repeatable), e.g. `k=4`, `p=0.5` |
| `--faults FILE` | `None` | JSON list of `{target, mode, start_s, duration_s, params}`; replaces `--fault-node` |

#### `runtime-sweep`

Faults every candidate under the runtime engine and writes `runtime_labels.json`: `i_delivery`, `i_delivery_consumer`, `i_flow`, `i_timeliness`, `i_recovery` with per-seed values and label stability, plus the order-k references `ref_delivery_first_order`, `ref_flow_reach`, `ref_flow_policy`. Defaults are the runtime oracle (`reactive`, `stations`, `per_process`, paired). Flags: the runtime flags above, plus `--seeds` (`42,123,456`), `--nodes`, `--duration` (`60`), `--qos-mode`, `--target-utilization`, `--fault-mode` / `--fault-duration` / `--fault-param`, and `--workers` (one seed per worker).

#### `testbed`

`testbed {build,run,calibrate,fidelity} --output DIR` — the emulated DDS testbed ([failure-simulation.md §7.12](failure-simulation.md#712-emulated-dds-testbed-calibration--fidelity-opt-in)). `build --input SCENARIO --duration S` writes `deployment.json`, `Dockerfile` and `docker-compose.yml`. `run --duration S [--faults FILE] [--mode local|docker]` runs it; `local` is in-process and needs no containers, while `docker` builds an image that downloads `cyclonedds`. `calibrate` writes `profile.json`. `fidelity [--calibrated]` simulates the same run and writes `fidelity.json`.

#### `combined`

Run `fault-inject` then `message-flow` in sequence. Accepts all flags from both subcommands.

### Output Files

- `fault-inject` → `output/simulation/impact_scores.json`, `output/simulation/impact_scores_summary.txt`
- `message-flow` → `output/simulation/message_flow_results.json`, `output/simulation/message_flow_summary.txt`
- `runtime-sweep` → `output/simulation/runtime_labels.json`
- `testbed` → `<output>/deployment.json`, `docker-compose.yml`, `telemetry/*.jsonl`, `run.json`, `profile.json`, `fidelity.json`
- `combined` → all of the above

---

## Step 6: Validate

**Script:** `cli/validate_graph.py`  
**Purpose:** Statistically prove that topology-based Q(v) predictions agree with simulation-derived I(v).

### Subcommands

#### `single`

One-seed validation run.

```bash
PYTHONPATH=. python cli/validate_graph.py single --input data/system.json --qos
```

#### `sweep`

Multi-seed stability sweep.

```bash
PYTHONPATH=. python cli/validate_graph.py sweep --input data/system.json --qos
```

#### `report`

Full sweep + topology-class gates + node-type strata + JSON report.

```bash
PYTHONPATH=. python cli/validate_graph.py report --input data/system.json --output output/validation_report.json --qos
```

#### `compare`

Ablation study: topology-only vs QoS-enriched side-by-side. Produces a `--latex` table.

```bash
PYTHONPATH=. python cli/validate_graph.py compare --input data/system.json --latex --output output/ablation.json
```

#### `harness`

Methodological-guard validation on pre-computed JSON artifacts.

```bash
PYTHONPATH=. python cli/validate_graph.py harness \
    --predictions output/predictions.json \
    --ground-truth cascade=output/impact_scores.json \
    --ground-truth latency=output/latency_delta.json \
    --out output/harness_report.json
```

Append `:qos` to a ground-truth source to mark it QoS-coupled (triggers independence caveat).

### Common Arguments (all subcommands)

| Flag | Default | Description |
|------|---------|-------------|
| `--input` | required | Path to `system.json` |
| `--qos` | `False` | Enable QoS-weighted scoring |
| `--gnn-model` | `None` | Path to GNN checkpoint |
| `--top-k` | `20%` of nodes | K for classification metrics |
| `--seeds` | `42,123,456,789,2024` | Comma-separated seed list |
| `--cascade` | `5` | Cascade depth limit |
| `--bootstrap` | `2000` | Bootstrap resamples for CI |
| `--alpha` | `0.05` | Significance level |
| `--output` | `None` | Write JSON report |
| `--csv` | `False` | Write per-node CSV |
| `--latex` | `False` | Write LaTeX ablation table |
| `--verbose` | `False` | Print per-node scores |

### Output

- JSON report at `--output` path
- Optional CSV at `<output>_nodes.csv`
- Optional LaTeX at `<output>_table.tex`

---

## Step 7: Prescribe

**Script:** `cli/prescribe_graph.py` (no console-script entry point — invoke directly)
**Purpose:** Compile refactoring recommendations, verify each one in a counterfactual
simulation, and report the closed-loop resilience change.

```bash
PYTHONPATH=. python cli/prescribe_graph.py --input data/scenarios/atm_system.json --layer system
PYTHONPATH=. python cli/prescribe_graph.py --layer system --output output/prescribe.json

# Tune the per-edit acceptance filter
PYTHONPATH=. python cli/prescribe_graph.py --input data/scenarios/atm_system.json \
    --kappa 1.0 --thresholds 0.1 0.2 0.5 --seeds 42 123 456
```

### Arguments

| Flag | Default | Description |
|------|---------|-------------|
| `--input` / `-i` | `None` | Run against a topology JSON in memory instead of Neo4j |
| `--layer` | `app` | Analysis layer (`app`, `infra`, `mw`, `system`) |
| `--gnn-checkpoint` | `None` | Trained GNN checkpoint directory |
| `--kappa` | `1.0` | Acceptance multiple; an edit must beat `kappa * sigma_seed` at every threshold |
| `--seeds` | `42 123 456` | Simulation seeds used to estimate seed noise |
| `--thresholds` | `0.1 0.2 0.5` | Propagation thresholds the edit must clear at every value |
| `--output` | `None` | Write the full `PrescribeResult` JSON |

### Output

Candidate policy counts, per-edit accept/reject verdicts with the reason each rejection was
made, the applied subset, and baseline vs. mutated SRI. An empty applied set is a normal
outcome — it means no candidate cleared the acceptance filter.

Verification costs one exhaustive simulation sweep per (edit × threshold × seed), so runtime
scales with candidate count. See
[prescription.md — Cost Model](prescription.md#32-cost-model).

Batch across the seven benchmark scenarios:

```bash
PYTHONPATH=. python reproduce/run_prescribe_all.py --kappa 1.0 --output results/prescribe_all.json
```

---

## Pre-deployment: Deploy

**Script:** `cli/deploy_graph.py` (console script `saag-deploy`)
**Purpose:** Plan an application-to-node deployment from a deployment spec and verify every
candidate on the replica-aware failure simulator. It plans only; nothing is deployed. See
[deployment.md](deployment.md).

```bash
saag-deploy -i data/network/atm_two_tier_system.json \
    --spec data/deployment/atm_two_tier_deployment.yaml \
    -o output/deployment.json --placed-topology output/placed.json

# As a pipeline stage
saag -i data/scenarios/atm_system.json --deploy --deployment-spec data/deployment/atm_system_deployment.json
```

### Arguments

| Flag | Default | Description |
|------|---------|-------------|
| `--spec` / `-s` | *(required)* | Deployment spec, YAML or JSON ([format](deployment.md#2-deployment-spec)) |
| `--input` / `-i` | `None` | Run against a topology JSON in memory instead of Neo4j |
| `--front-points` | `5` | Points on the traffic-vs-blast front |
| `--ties` | `5` | Extra placements tied at minimum blast, used to measure solver-pick noise |
| `--balance` | `False` | Break remaining ties toward lower peak utilisation |
| `--time-limit` | `60` | Seconds per MILP solve |
| `--time-budget` | `600` | Seconds for all MILP solves together |
| `--probe-limit` | `30` | Seconds the first MILP stage gets to find any placement before `--solver auto` falls back |
| `--heuristic-budget` | `600` | Seconds for the heuristic search, on its own clock |
| `--max-nonzeros` | `150000` | Model size above which `--solver auto` uses the heuristic |
| `--solver` | `auto` | `auto` (MILP, heuristic fallback), `milp` or `heuristic` |
| `--baselines` | all four | Any of `random ffd k8s_spread indeg_spread` (`indeg_spread` is always scored) |
| `--seeds` | `42 123 456` | Failure-simulator seeds for the held-out score |
| `--kappa` | `1.0` | Noise-bar multiple of the seed standard deviation |
| `--network-gating` | `auto` | `auto` (on when the topology has NetworkDevices), `on` or `off` |
| `--output` / `-o` | `None` | Write the full `DeploymentResult` JSON |
| `--placed-topology` | `None` | Write the topology with the recommended placement applied (importable with `saag-import`) |

`cli/run.py` adds `--deploy` and `--deployment-spec FILE`. `--deploy` without a spec is an
error, and `--all` includes the stage only when `--deployment-spec` is given.

### Output

The CLI prints a candidate table with these columns: traffic, cross-host share, expected and
worst-case blast proxy, peak utilisation, hosts used, held-out `I_comp` expected ± σ and
worst case, and the verdict against the best baseline. It then prints the recommendation and
its rationale, and says whether a reliability gain is claimed.

Exit codes:

| Code | Meaning |
|------|---------|
| `0` | Success |
| `1` | Error |
| `2` | The spec is invalid, cannot be satisfied (`infeasible`, with prechecks or culprit constraints), or no feasible placement was found (`no_feasible`, with the closest candidate's violations) |

---

## Step 8: Visualize

**Script:** `cli/visualize_graph.py`  
**Purpose:** Generate multi-layer HTML dashboards.

```bash
PYTHONPATH=. python cli/visualize_graph.py --layer system -o output/dashboard.html
```

### Arguments

| Flag | Default | Description |
|------|---------|-------------|
| `--demo` | `False` | Generate demo dashboard (no Neo4j) |
| `--open` / `-b` | `False` | Open in browser after generation |
| `--layer` | `system` | Comma-separated layers |
| `--layers` | `None` | Explicit multi-layer flag (takes precedence over `--layer`) |
| `--no-network` | `False` | Exclude interactive network graphs |
| `--no-matrix` | `False` | Exclude dependency matrices |
| `--no-validation` | `False` | Exclude validation metrics |
| `--antipatterns` | `None` | Path to pre-calculated anti-pattern JSON report |
| `--multi-seed` | `[]` | Paths to per-seed validation JSON files |
| `--cascade-file` | `None` | QoS ablation experiment JSON |

---

## Utilities

### Benchmark

**Script:** `cli/benchmark.py`  
Runs the pipeline across scales and layers, collecting timing and accuracy metrics.

```bash
PYTHONPATH=. python cli/benchmark.py --scales tiny,small,medium --layers app,system --runs 3 --output results/benchmark
```

| Flag | Default | Description |
|------|---------|-------------|
| `--scales` | `None` | Comma-separated scales |
| `--config` | `None` | YAML configuration file |
| `--full-suite` | `False` | Run `tiny + small + medium` |
| `--layers` | `app,infra,mw,system` | Layers to benchmark |
| `--runs` | `1` | Runs per scenario |
| `--output` / `-o` | `results/benchmark` | Output directory |
| `--ndcg-k` | `10` | K for NDCG@K |
| `--spearman-target` | `0.70` | Pass threshold |
| `--f1-target` | `0.80` | Pass threshold |
| `--dry-run` | `False` | Print plan without executing |

### Detect Anti-Patterns

**Script:** `cli/detect_antipatterns.py`  
Standalone anti-pattern detector.

```bash
PYTHONPATH=. python cli/detect_antipatterns.py --layer system --output results/ap.json
```

| Flag | Description |
|------|-------------|
| `--catalog` | Print full pattern catalog and exit |
| `--pattern` | Filter by pattern ID (comma-separated) |
| `--severity` | Filter by severity (comma-separated) |
| `--use-ahp` | Use AHP weights |
| `--ahp-shrinkage` | λ factor (default `0.7`) |

Exit codes mirror `predict_graph.py`: `0` clean, `1` MEDIUM, `2` HIGH/CRITICAL.

### Statistics

**Script:** `cli/statistics_graph.py`  
Cross-cutting topology statistics (live Neo4j or standalone file mode).

```bash
PYTHONPATH=. python cli/statistics_graph.py --input output/dataset.json --chart topic_bandwidth app_balance --format minimal
```

Available charts: `topic_bandwidth`, `app_balance`, `topic_fanout`, `cross_node_heatmap`, `node_comm_load`, `domain_comm`, `criticality_io`, `lib_dependency`, `node_critical_density`, `domain_diversity`.

| Flag | Default | Choices | Description |
|------|---------|---------|-------------|
| `--input` / `-i` | `None` | Path | Standalone file mode (skips Neo4j) |
| `--chart` | all | registry IDs | Specific charts to compute |
| `--format` | `table` | `table`, `minimal`, `json` | Output format |
| `--output` / `-o` | `None` | Path | Save JSON results |

### Master Orchestrator

**Script:** `cli/run.py`  
Execute the full pipeline via the `saag` SDK.

```bash
PYTHONPATH=. python cli/run.py --all --layer system --gnn-model output/gnn_checkpoints/best_model --output output/result.json
```

| Flag | Description |
|------|-------------|
| `--all` | Run all stages sequentially |
| `--generate` | Run graph generation |
| `--analyze` | Run analysis |
| `--predict` | Run Predict (Pathway B: GNN ranking, requires checkpoint, or RM fallback) |
| `--diagnose` | Run Diagnose (Pathway A: RM scores + anti-patterns + explanation; no checkpoint required) |
| `--simulate` | Run simulation |
| `--validate` | Run validation |
| `--visualize` | Run visualization |
| `--input` / `-i` | Input JSON |
| `--config` | Generation YAML config |
| `--scale` | Generation scale preset |
| `--output-dir` | Directory for outputs |
| `--clear` | Clear Neo4j before import |
| `--use-ahp` | AHP weights |
| `--gnn-model` | GNN checkpoint path |
| `--triage-k` | Run the Triage bridge as part of diagnose (requires `--diagnose` or `--all` in the same invocation; `--all` alone does not imply a `--triage-k` value) |
| `--sim-mode` | `exhaustive` (default), `monte_carlo` |
| `--no-network`, `--no-matrix`, `--no-validation` | Visualization exclusions |

---

## First-Run Sequencing

Correct dependency order for a fresh project:

```
Generate → Import → Analyze → Simulate → Train → Predict → Diagnose → Validate → Prescribe → Visualize
```

Corresponding CLI commands:

```bash
# 0. Generate
PYTHONPATH=. python cli/generate_graph.py --scale medium --domain atm --output data/atm_system.json

# 1. Import
PYTHONPATH=. python cli/import_graph.py --input data/atm_system.json --clear

# 2. Analyze
PYTHONPATH=. python cli/analyze_graph.py --layer system

# 3. Simulate (fault-inject for ground-truth, then optional message-flow)
PYTHONPATH=. python cli/simulate_graph.py fault-inject --input data/atm_system.json --output output/simulation/ --export-json

# 3b. Train GNN on the fault-inject labels from step 3
PYTHONPATH=. python cli/train_graph.py --layer system --simulated output/simulation/impact_scores.json --output output/gnn_checkpoints/best_model

# 4. Predict
PYTHONPATH=. python cli/predict_graph.py --layer system --gnn-model output/gnn_checkpoints/best_model --no-diagnose

# 5. Diagnose (no checkpoint required — can also run before Predict/Simulate/Train)
PYTHONPATH=. python cli/diagnose_graph.py --layer system --triage-k 10 --output output/diagnosis.json

# 6. Validate
PYTHONPATH=. python cli/validate_graph.py report --input data/atm_system.json --output output/validation_report.json --qos

# 7. Prescribe
PYTHONPATH=. python cli/prescribe_graph.py --layer system

# 8. Visualize
PYTHONPATH=. python cli/visualize_graph.py --layer system -o output/dashboard.html --antipatterns results/ap.json
```

For repeated runs after GNN training, use the master orchestrator:

```bash
PYTHONPATH=. python cli/run.py --all --layer system --gnn-model output/gnn_checkpoints/best_model
```

---

## Layer Projections

The `--layer` flag accepts:

| Value | Description |
|-------|-------------|
| `app` | Application layer |
| `infra` | Infrastructure layer |
| `mw` | Middleware layer |
| `system` | Full system (default) |
| `all` | Run `app`, `infra`, `mw`, `system` sequentially |

Multiple layers can be specified as a comma-separated list: `--layer app,infra,system`.

Layer values are case-insensitive in most scripts.

---

## Advanced Workflows

### Batch Scenario Generation

```bash
PYTHONPATH=. python cli/generate_graph.py batch \
    --input-dir data/ \
    --output-dir output/ \
    --seeds 42,123,456,789,2024 \
    --manifest batch_manifest.json \
    --report batch_report.json
```

### Multi-Seed Stability Sweeps

Run validation across multiple random seeds:

```bash
PYTHONPATH=. python cli/validate_graph.py sweep \
    --input data/system.json \
    --seeds 42,123,456,789,2024 \
    --qos \
    --output output/sweep_report.json
```

### LOSO Cross-Validation

Use `cli/loso_evaluate.py` for leave-one-scenario-out evaluation across domain datasets.

A sweep is `folds x seeds` independent fits (12 x 5 = 60 per variant on the shipped
corpus), and the flags below control how they are scheduled rather than what they
compute. None of them changes a result: a fold reproduces bit-identically at any
`--jobs` and any `--torch-threads`.

| Flag | Default | What it does |
|:---|:---|:---|
| `--jobs N` | `1` | Run N (fold, seed) fits concurrently. Fits are latency-bound rather than compute-bound on graphs this size, so several workers share one GPU or CPU well. Measured on the 12-fold corpus: 253 s at `--jobs 1`, 73 s at `--jobs 6`, identical output. |
| `--torch-threads N` | `1` | Intra-op threads per fit. One is the fastest setting here, not a throttle: every tensor op is smaller than its own threading overhead, and one forward+backward on the enterprise scenario takes 1540 ms at 14 threads versus 54 ms at 1. |
| `--resume` | off | Reuse any completed fit whose stored fingerprint — configuration, cache contents, and the contents of the model/trainer/harness modules — still matches. Anything that does not match is re-run with its stale checkpoint deleted first, so an interrupted sweep restarts where it stopped rather than at zero. |
| `--no-preflight` | on | Skip the one-epoch probe fit that runs before the sweep. The probe exercises conversion, model construction, a training step, inference and metrics on the target device in a few seconds; without it an environment fault surfaces only after every fit has failed. |

`reproduce/loso_all_variants.py` forwards all four, and `make -f reproduce/Makefile
table4 JOBS=8` sets the first.

### Ablation Studies (Topology-only vs QoS-enriched)

```bash
PYTHONPATH=. python cli/validate_graph.py compare \
    --input data/system.json \
    --output output/ablation.json \
    --latex
```

The `compare` subcommand runs both QoS-off and QoS-on sweeps, computes Δρ, and produces a LaTeX table suitable for publication.

### CI/CD Anti-Pattern Gating

```bash
PYTHONPATH=. python cli/predict_graph.py --layer system \
    --severity critical,high \
    --output-antipatterns results/ap.json
```

`predict_graph.py` bundles Step 4 (Diagnose, where the anti-pattern gate lives) by default; use `cli/diagnose_graph.py` (`saag-diagnose`) instead when no GNN checkpoint is available or wanted for this gate.

- Exit `0`: no anti-patterns
- Exit `1`: MEDIUM patterns
- Exit `2`: HIGH / CRITICAL → blocks pipeline

### Benchmarking Across Scales

```bash
PYTHONPATH=. python cli/benchmark.py --scales tiny,small,medium,large,jumbo,xlarge \
    --layers app,infra,mw,system \
    --runs 3 \
    --spearman-target 0.80 \
    --f1-target 0.75
```

---

## Output Reference

| Stage | Default Output Path | Notes |
|-------|---------------------|-------|
| Generate | `output/graph.json` | Configurable via `--output` |
| Import | (Neo4j DB) | Stats via `--output` |
| Export | user-specified | `persistence` or `analysis` format |
| Analyze | propagated via console | Save with `--output` |
| Train | `output/gnn_checkpoints/` | Checkpoint directory |
| Predict | user-specified via `--output` | Anti-patterns via `--output-antipatterns`; both no-ops with `--no-diagnose` |
| Diagnose | user-specified via `--output` | Anti-patterns via `--output-antipatterns` |
| Simulate | `output/simulation/` | JSON + text summaries |
| Validate | user-specified via `--output` | Optional CSV / LaTeX |
| Prescribe | propagated via console | Save with `--output` (`PrescribeResult` JSON) |
| Visualize | `dashboard.html` | Configurable via `-o` |
| Batch | `output/<domain>_results/` | Per-scenario output directories |

---

## Troubleshooting

### `PYTHONPATH=.` Requirement

Direct invocation requires the project root on `PYTHONPATH`. Omitting it causes `ModuleNotFoundError` for `saag` and `cli` modules.

### Missing GNN Checkpoint for `--all`

`cli/run.py --all` (or `--predict`) requires a trained GNN checkpoint. If `--gnn-model` is not provided and `output/gnn_checkpoints/best_model` does not exist, the predict stage is skipped with a warning — the diagnose stage still runs, since RM scores, anti-patterns and explanations need no checkpoint at all. Label and train first to enable GNN ranking:

```bash
PYTHONPATH=. python cli/simulate_graph.py fault-inject --input data/system.json --output output/simulation/ --export-json
PYTHONPATH=. python cli/train_graph.py --layer system --simulated output/simulation/impact_scores.json --output output/gnn_checkpoints/best_model
```

### Neo4j Connection Issues

- Verify `bolt://localhost:7687` is reachable.
- Default credentials `neo4j` / `password` apply unless overridden with `--user` / `--password` or `NEO4J_USERNAME` / `NEO4J_PASSWORD`.
- Use `--dry-run` with `import_graph.py` to validate JSON without touching the database.

### Case-Sensitive QoS Values

QoS scenario names (`--domain`, `--scenario`) must match exactly: `av`, `iot`, `finance`, `healthcare`, `hub-and-spoke`, `microservices`, `enterprise`, `atm`.

### First-Run Guard Warning

The orchestrator warns when `--all` is used without a GNN checkpoint and skips the predict stage (the diagnose stage still runs). Follow the printed three-step sequence to generate data, train, and then run the full pipeline.

### Small Graphs and Unreliable Spearman ρ

`validate_graph.py` warns when fewer than 10 Application nodes are present; Spearman ρ has high variance on small `n`.

---

## Cross-References

- Graph generation details: [graph-generation.md](graph-generation.md)
- Graph model and topology: [graph-model.md](graph-model.md)
- Structural analysis: [structural-analysis.md](structural-analysis.md)
- Prediction and GNN: [prediction.md](prediction.md)
- Diagnosis and Triage Bridge: [diagnosis.md](diagnosis.md)
- Failure simulation: [failure-simulation.md](failure-simulation.md)
- Validation methodology: [validation.md](validation.md)
- Prescription: [prescription.md](prescription.md)
- Visualization: [visualization.md](visualization.md)
- Scenario management: [scenario.md](scenario.md)
- Statistics: [statistics.md](statistics.md)
- Anti-patterns: [antipatterns.md](antipatterns.md)
