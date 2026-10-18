# CLAUDE.md Development Guide

**Quick-reference guide for LLM coding guidelines, build commands, test suites, and project conventions.**

[README](README.md) | [Architecture](ARCHITECTURE.md)

---

## Table of Contents

1. [LLM Coding Guidelines](#llm-coding-guidelines)
2. [Command Line Reference](#command-line-reference)
3. [Project Conventions & Standards](#project-conventions--standards)
4. [Invariants](#invariants)
5. [Known Gotchas](#known-gotchas)

---

## LLM Coding Guidelines

These guidelines represent best-practice rules for AI coding assistants working on the repository. They prioritize caution, correctness, and simplicity.

### Think Before Coding
- **State assumptions explicitly** — if any requirements are ambiguous, clarify them with the user before writing code.
- **Surface tradeoffs** — present alternative implementations or designs rather than making a choice silently.
- **Push back when warranted** — if a simpler solution or a pre-existing function can achieve the task, explain it.
- **Stop when confused** — identify what is unclear, stop execution, and ask the user for clarification.

### Simplicity First
- **Avoid speculative features** — implement the minimum code required to solve the task. Do not add abstractions for single-use logic.
- **Keep it concise** — avoid adding unrequested configurability or overcomplicated error handling for impossible scenarios.
- **Review for complexity** — if a file or function becomes unnecessarily verbose, refactor it to its simplest equivalent form.

### Surgical Changes
- **Minimize diff blast-radius** — touch only the lines of code directly related to the user request.
- **Avoid unrelated styling or cleanup** — do not "improve" adjacent formatting, docstrings, or logic that is not broken.
- **Observe style parity** — match the established conventions, naming patterns, and file structure of the code you are editing.
- **Prune orphans** — clean up unused imports, variables, and functions introduced by your changes. Do not delete pre-existing dead code unless explicitly requested.

### Goal-Driven Execution
- **Establish success criteria** — transform requirements into testable validation steps (e.g. reproducing a bug in a test first).
- **Run verification loops** — execute tests before, during, and after implementing changes to verify that existing features do not break.

---

## Command Line Reference

Install, Docker, and frontend commands are documented once, in [README §Quick Start](README.md#quick-start) — do not re-derive them here; link instead.

### Pipeline Entry Points
Nine CLI scripts have console-script entry points (`pyproject.toml`); the rest run as `python cli/<script>.py` (or `PYTHONPATH=. python cli/<script>.py` if not installed editable):

```bash
saag --all --layer system              # cli/run.py — full pipeline
saag-import  --input data/system.json --clear
saag-analyze --layer system
saag-predict  --layer system           # Step 3 (Pathway B) + Step 4 bundled by default
saag-diagnose --layer system           # Step 4 (Pathway A) alone — no GNN checkpoint required
saag-simulate fault-inject --input data/system.json --export-json
saag-validate report --input data/system.json --qos
saag-visualize --layer system --output output/dashboard.html

# No entry point — invoke directly:
python cli/train_graph.py --layer system --simulated output/simulation/impact_scores.json  # labels from saag-simulate fault-inject; required
python cli/prescribe_graph.py --layer system
python cli/benchmark.py
```

Full flag reference for every script: [docs/cli-pipeline-guide.md](docs/cli-pipeline-guide.md). Package/service boundaries: [ARCHITECTURE.md](ARCHITECTURE.md).

### Pytest & Verification Commands
Tests run from the project root and do not require a live Neo4j instance unless marked `integration`:

```bash
make test                     # standard developer test suite (skips live db & slow tests)
make test-fast                # fast pre-merge suite (<15s, runs pytest --quick)
make check                    # pre-merge gate: lint check + fast tests
make test-all                 # run all tests including slow (skips integration)
make test-integration         # run live Neo4j integration tests

pytest                        # full suite
pytest --quick                # fast pre-merge run (skips slow tests)
pytest -x                     # halt on first failure
pytest -k "reliability"       # pattern match
pytest tests/test_analysis_service.py tests/test_prescription.py
pytest -m "not integration"   # skip anything needing a live database
pytest -m slow                # run only the slow-marked tests
```

### Pre-Merge Checklist
Before merging or updating `main`:
1. Run `make check` (runs `ruff check` and `pytest --quick`).
2. Run `make test-all` or `pytest -m "not integration"` to ensure full test suite passes.
3. Ensure no new warning regressions or broken invariants (`test_independence_guarantee.py`, `test_groundtruth_contract.py`, etc.).


### Reproduction Package
Experiment sweeps and paper tables/figures live under `reproduce/`, with their own Makefile:

```bash
make -f reproduce/Makefile block0       # go/no-go gate — run before anything else
make -f reproduce/Makefile smoke-test   # fast sanity check, ~15-30 min
make -f reproduce/Makefile table3       # main results table
make -f reproduce/Makefile table4       # LOSO cross-domain generalization
make -f reproduce/Makefile kfold        # per-domain k-fold (opt-in, not part of `all`)
```

See [reproduce/README.md](reproduce/README.md) and [reproduce/EXPERIMENTS.md](reproduce/EXPERIMENTS.md) for the full protocol.

---

## Project Conventions & Standards

### Architecture & Design Patterns
- **Hexagonal Architecture** — The core SDK in [saag/](saag/) isolates business logic from databases and clients. Use Cases in [saag/usecases/](saag/usecases/) act as the boundary interface between presentation layers (FastAPI, CLI) and core domain services. This includes standard pipeline stage interactors (`ModelGraphUseCase`, `AnalyzeGraphUseCase`, `PredictGraphUseCase`, `SimulateGraphUseCase`, `ValidateGraphUseCase`, `PrescribeGraphUseCase`, `VisualizeGraphUseCase`, `MultiLayerAnalysisUseCase`) and dedicated pathway interactors (`DiagnosticUseCase` for Pathway A, `PredictiveUseCase` for Pathway B, and `TriageUseCase` for the Triage Bridge).
- **Repository Pattern** — Data operations are mapped to the `IGraphRepository` interface. Production runs use [Neo4jRepository](saag/infrastructure/neo4j_repo.py), whereas unit tests use the mock [MemoryRepository](saag/infrastructure/memory_repo.py). Core services must never depend directly on Neo4j classes.
- **REST Presenters (target state, largely applied)** — API routers in [api/routers/](api/routers/) delegate serialization to presenters in [api/presenters/](api/presenters/). `graph.py`, `simulation.py`, `analysis.py`, `prediction.py`, `validation.py`, and the triage endpoint in `prediction.py` (via `triage_presenter.py`) do this fully; `components.py` does it partially (`serialize_critical_edge`). `classification.py`, `statistics.py`, `traffic.py`, and `health.py` still serialize inline. When touching one of those four, move it to a presenter rather than adding more inline serialization.

### Layer Projections & Dependency Derivation
- **Analysis Layers** — The codebase uses four canonical layers (`app`, `infra`, `mw`, `system`) defined in [saag/core/layers.py](saag/core/layers.py). The `app` layer includes both `Application` and `Library` nodes.
- **Logical Dependencies** — Physical pub-sub linkages are transformed into `DEPENDS_ON` edges using the seven rules implemented in `Neo4jRepository` (Rule 7, `node_to_device`, fires only when the topology declares `NetworkDevice` switches/routers). In the dependency graph, edge direction points from the *dependent* to its *dependency*.
- **Physical Network (opt-in)** — `CONNECTS_TO` is an undirected cable between `Node`s and `NetworkDevice` switches/routers, with optional `from_interface` / `to_interface` / `capacity_mbps`. Routing, load and the Rule 7 separator test live once in [saag/core/network.py](saag/core/network.py) and are shared by both repositories, both simulators and the GNN data preparation. Everything network-specific is a no-op on the host-mesh corpus (no devices, no capacities), and `network_gating` defaults to off in `FailureSimulator`, `FaultInjector`, `SimulationService` and `GraphEvaluator`. Examples go in `data/network/`, never in `data/scenarios/`.
- **Simulation Substrate** — Failure simulations in [saag/simulation/](saag/simulation/) operate strictly on raw structural edges ($G_{\text{structural}}$), not derived dependency edges ($G_{\text{analysis}}$).

### Documentation Hyperlinking Standards
- **Relative Portability** — When referencing files, folders, or code elements inside documentation, always use standard relative Markdown links. Avoid using absolute `file:///` URLs to maintain portability across different workspaces and Git hosting systems.
- **Line Ranges** — Link directly to symbol line boundaries (e.g. `[Pipeline](saag/pipeline.py#L12)`) when referencing code elements to assist LLM navigation.

---

## Invariants

These are enforced by tests — breaking one will fail CI, not just review:

- **Simulation never reads derived edges.** `saag/simulation/` operates strictly on raw structural edges, never `DEPENDS_ON`. Enforced by `tests/test_independence_guarantee.py`.
- **`FaultInjector` and `FailureSimulator` are not interchangeable.** `FaultInjector` is the Predict-stage labeler; `FailureSimulator` is the Validate-stage oracle. Never substitute one for the other within a stage. Enforced by `tests/test_groundtruth_contract.py`.
- **`IM(v)` is derived, never read.** `SimulationGraph.get_dependency_edges()` re-derives all seven `DEPENDS_ON` rules (docs/graph-model.md §4.4) from raw structural edges, because `saag/simulation/` may not consume the analysis graph's derived edges. Its arc set must stay equal to `MemoryRepository`'s — enforced by `tests/test_simulation_dependency_derivation.py`. It implemented one rule for a long time, and because the rules it was missing are exactly the ones that give Applications, Brokers and Nodes any dependents, `IM(v)` was identically 0.0 for every component the validation scores. `tests/test_maintainability_dimension.py::TestMaintainabilityOracleIsLive` is the regression guard.
- **A gate value of `None` means "never measured".** `RELEASE_GATES` / `REPORTED_GATES` in [saag/validation/models.py](saag/validation/models.py) are the single declaration of the gate set; `evaluate_gate` returns `None` rather than coercing an unmeasured metric to 0.0. Never reintroduce a `.get(metric, 0.0)` in `_evaluate_gates` — that is what reported κ_CTA and BP as *failures* for as long as the maintainability oracle was dead.
- **Predict does not import Simulate.** The dependency runs the other way at the pipeline level (Simulate produces training labels; Predict consumes them from disk), not as a live import. Enforced by `tests/test_predict_simulate_separation.py`.
- **Pathway A and Pathway B parameter independence.** Pathway A (`DiagnosticUseCase` / ISO-RM) and Pathway B (`PredictiveUseCase` / HGL) share no parameters; neither is fitted to the other's output. Pathway A operates deterministically on structural metrics without ML or simulation runtime access.
- **Triage Bridge join semantics.** The Triage bridge (`saag.analysis.triage.triage()` / `TriageUseCase`) scopes Pathway A's root-cause diagnosis to Pathway B's Top-*K* shortlist by joining on component ID. It never reads root causes off GNN weights directly. In zero-GNN cold-start mode, it falls back cleanly to ranking by RM $Q^*(v)$. Enforced by `tests/test_triage.py`.
- **Closed-loop counterfactual verification.** Step 7, Prescribe (`EditVerifier`) accepts candidate edits $\Delta(G)$ only if the counterfactual failure-impact reduction exceeds seed noise ($\Delta I > \kappa \cdot \sigma_{\text{seed}}$) across *every* propagation threshold. Enforced by `tests/test_prescription.py`.
- **Layer names are exactly `app` / `infra` / `mw` / `system`.** Defined once in [saag/core/layers.py](saag/core/layers.py); `app` includes both `Application` and `Library` nodes.
- **Committed datasets are exactly what their configs generate.** Every `data/scenarios/*_system.json` must regenerate byte-identically from its `scenario_*.yaml`, and `data/scenarios/MANIFEST.json` must match what is on disk. Enforced by `tests/test_scenario_corpus.py`. After changing the generator, regenerate the corpus, refresh the manifest, and rebuild the caches (`make -f reproduce/Makefile cache`) — a stale `output/loso_cache/` silently outranks the datasets in `reproduce/main_table.py` and has published wrong numbers before.
- **A quality-in-use scalarisation is a reweighting of RM, never an independent score.** `saag/core/quality_model.py`'s `QIU_PROJECTION` is row-stochastic by construction, so any domain-weighted projection onto quality-in-use harm is algebraically identical to scoring the same RM vector under different composite weights (`M^T omega`). Do not compute a "quality-in-use score" and report it as a quantity distinct from a reweighted `Q(v)`. Enforced by `tests/test_quality_model.py::TestQiuCollapseEquivalence`.
- **The RM composite is a re-parameterisation of the retired 4-D AHP composite, not an independently invented weighting.** `r_alpha=0.36` and `(q_reliability=0.80, q_maintainability=0.20)` are DECLARED constants algebraically derived from the old AHP vector (A=0.43, R=0.24, M=0.17, V=0.16) by dropping Vulnerability and renormalising — `w_R*r_alpha ≈ 0.24/0.84`, `w_R*(1-r_alpha) ≈ 0.43/0.84`, `w_M ≈ 0.17/0.84`, each within `abs=0.003` of the rounded 2-s.f. shipped values. Do not re-tune these independently of that derivation. Enforced by `tests/test_quality_model.py::TestCompositeReparameterisation`.
- **The Topic QoS sub-weights are AHP-consistent, not arbitrary.** `QoSPolicy.W_RELIABILITY=0.24`/`W_DURABILITY=0.62`/`W_PRIORITY=0.14` in [saag/core/models.py](saag/core/models.py) (the runtime source of truth for `w(topic)`) must match the geometric-mean priority vector of `AHPMatrices.criteria_topic_qos`'s Saaty pairwise-comparison matrix within `abs=0.01`, and that matrix's consistency ratio must stay below 0.10 (and, per `tests/test_ahp_shrinkage.py`'s non-degeneracy check, above `0.005` — a CR near zero on a 3x3 matrix with non-trivial off-diagonal ratios means the matrix was solved backward from the target vector rather than stated independently, which was true of an earlier version of this matrix). Do not edit either side (the constants or the matrix) without keeping the other in sync. Enforced by `tests/test_ahp_shrinkage.py::test_topic_qos_matrix_reproduces_shipped_weights`.
- **Order-$k$ Reference Criterion on Oracles.** Simulation oracles ($I^*, I_{\text{dyn}}$) are low-order by construction. Analytical rankers that truncate a simulator's own computation ($T_k(O)$ under simplifications S1–S5: expectation, unscaled severity, no queueing, uniform rates, wave support) are formal *references* (e.g., afferent coupling $\text{InDeg}$ for $I^*$, rate-weighted expansion for $I_{\text{dyn}}$). They measure oracle self-recovery, not external predictive skill, and must be reported as descriptive baselines rather than claimed as empirical discoveries.
- **Two-Tiered Triage Protocol over Blocking CI Gates.** Reachability cascade rankings require flagging 40–45% of all applications to achieve 80% recall of the critical set (JSS §8.1). They must never be configured as automated blocking pass/fail gates in CI/CD pipelines; operational deployments follow Tier 1 (non-blocking informational note in pull requests) and Tier 2 (targeted staging-time simulation on shortlisted candidates).

## Known Gotchas

Documented so they aren't mistaken for correctly-working code; not fixed in this pass:

| Symptom | Cause |
|:---|:---|
| Editing `.env` doesn't change local `python cli/*.py` behavior | Nothing in the Python code loads `.env` — it is consumed only by Docker Compose and Next.js |
| `cm_avg_cbo` and `cm_avg_rfc` show up in the UI and in exported graphs but never move a criticality score | Ingested, flattened and persisted, but no scoring code reads them — `CQP(v)` only consumes `loc`, `cyclomatic_complexity`, `instability_code` (from `avg_fanin`/`avg_fanout`), and `lcom`. See [structural-analysis.md §9.2](docs/structural-analysis.md#92-exact-rm-scoring-formulas). For the full scoring/detection/GNN-feature/descriptive role of every `StructuralMetrics` field, see [`saag/core/metric_registry.py`](saag/core/metric_registry.py) rather than re-deriving it by hand. |
| A host mesh without `network_gating` partitions asymmetrically | The legacy `FailureSimulator._cascade_network` follows *outgoing* `CONNECTS_TO` only, and the generator emits mesh links lower→higher index, so the highest-index host never isolates anyone. Fixing it moves published Node `I_comp`; `network_gating=True` replaces the rule with undirected reachability. |
| Severing a `CONNECTS_TO` edge does not change fragmentation | `SimulationGraph._build_active_undirected_graph` ignores `_failed_edges`. Under `network_gating` the reachability terms still see the cut; fixing fragmentation would move every published edge-criticality label. |
| The GNN never sees a publish edge when the same app also subscribes to that topic | `build_graph_from_json` builds an `nx.DiGraph`, so `(app, topic)` PUBLISHES_TO and SUBSCRIBES_TO collapse into one edge (the later, SUBSCRIBES_TO, wins). 14 such pairs on `atm`. Every native-graph GNN feature and the GNN's network-load features are computed on the collapsed graph. |

| `MemoryRepository` and `Neo4jRepository` disagree on Rule-1 edge weights | `memory_repo._collapse_paths` unions topic weights over a *multiset* (`for topics in kinds.values() for t in topics`), so a topic reachable both directly and through a library is counted twice. Neo4j MERGEs each path kind separately and keeps the max, so it does not. Measured divergence: 0 affected pairs on `atm`, 64 on `microservices` (Δw ≤ 0.248), 139 on `av` (Δw ≤ 0.374). Fixing it moves `dependency_weight_out` → `Q(v)` → published tables, so it has not been fixed in passing. `SimulationGraph.get_dependency_edges()` deliberately matches Neo4j, not memory_repo. |

REV and RCL (Reverse Eigenvector, Reverse Closeness) were `V(v)`'s other two terms and are no longer computed at all — not a gotcha, just gone. Do not add `w_in`/QoS-weighted In-Degree to this table: `dependency_weight_in` was retired from its `V(v)`/QADS role but was repurposed, not orphaned — it is a live Topic-only `FT(v)` input (`saag/analysis/analyzer.py`'s Topic branch of `_compute_rm`). See [structural-analysis.md §7.1](docs/structural-analysis.md#81-fault-tolerance-inputs-ft).
