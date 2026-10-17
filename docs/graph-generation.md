# Graph Generation

**Offline synthetic graph generation: what it produces, how the two generation modes differ, and how the generator pipeline works internally.**

[README](../README.md) | → [Step 1: Model (Import)](graph-model.md)

For the full CLI flag reference (`generate_graph.py`, `batch`, `validate`), see [cli-pipeline-guide.md — Stage 0](cli-pipeline-guide.md#stage-0-offline-prep--generate). For scenario file details and validation objectives, see [scenario.md](scenario.md).

---

## Table of Contents

1. [Overview](#1-overview)
2. [Architecture of the Generation Stack](#2-architecture-of-the-generation-stack)
3. [Graph Model — Node and Edge Types](#3-graph-model--node-and-edge-types)
4. [Generation Modes](#4-generation-modes)
5. [Scenario Configuration Files](#5-scenario-configuration-files)
6. [The `StatisticalGraphGenerator` Pipeline](#6-the-statisticalgraphgenerator-pipeline)
7. [Output Format](#7-output-format)
8. [Programmatic API](#8-programmatic-api)
9. [Known Limitations and Open Issues](#9-known-limitations-and-open-issues)

---

## 1. Overview

Synthetic graph generation is an offline input preparation stage that supports the core 6-step Software-as-a-Graph (SaG) analytical pipeline:

```
Offline Input Prep (Generate) → Model / Import (Step 1) → Analyze (Step 2) → Predict (Step 3) → Diagnose (Step 4) → Simulate (Step 5) → Validate (Step 6) → Prescribe (Step 7) → Visualize (Step 8)
```

Its role is to produce a **synthetic publish-subscribe system topology** in JSON format that can be loaded into Neo4j and subsequently subjected to structural analysis and failure simulation. The generator is self-contained: it requires no running database, no external service, and no runtime monitoring data. A single deterministic seed produces an identical dataset on every invocation.

The generator is used for two distinct purposes in the project:
- **Validation** — producing the synthetic datasets over which Spearman $\rho$ and F1 scores are measured. Reproducibility via seed is essential here.
- **Benchmarking** — producing datasets of controlled scale (tiny through xlarge) to measure pipeline throughput and algorithmic complexity.

---

## 2. Architecture of the Generation Stack

The generation functionality is organized in four layers, each with a single responsibility:

```
cli/generate_graph.py              ← CLI entry point (single-graph, batch, and validate subcommands)
cli/common/dispatcher.py           ← dispatch_generate() — bridges CLI args to service
cli/common/batch_generation.py     ← run_batch_generation() — batch dataset generation logic
cli/common/dataset_validation.py   ← run_dataset_validation() — topology-class validation
tools/generation/service.py        ← GenerationService / generate_graph() convenience fn
tools/generation/generator.py      ← StatisticalGraphGenerator (core logic)
tools/generation/models.py         ← GraphConfig, SCALE_PRESETS, statistical structs
tools/generation/datasets.py       ← Domain name pools, QoS scenario mappings
```

The same service layer is exposed via the FastAPI router at `POST /api/v1/graph/generate`, so the web UI and the CLI share identical generation logic with no duplication.

---

## 3. Graph Model — Node and Edge Types

A generated graph contains five node types and six structural edge types.

### 3.1 Node Types

| Type | ID Prefix | Description |
|:---|:---|:---|
| `Application` (CSU) | `A{n}` | Software component that publishes and/or subscribes to topics; includes 3-tier criticality (`HIGH`, `MEDIUM`, `LOW`) and dual-node redundancy (`hotstandby`) |
| `Library` | `L{n}` | Shared software library used by one or more applications |
| `Broker` | `B{n}` | Message broker / DDS participant that routes topics |
| `Node` (Infrastructure) | `N{n}` | Physical or virtual host running applications and brokers |
| `NetworkDevice` (optional) | `SW{n}` / `RT{n}` | Top-of-rack switch / core router; generated only when the config has a `network:` block (§5.1) |
| `Topic` | `T{n}` | Named communication channel carrying typed messages; includes size, QoS policies (reliability, durability, transport priority, deadline, history depth), frequency (Hz), and ground-truth criticality (`HIGH`, `MEDIUM`, `LOW`) |

### 3.2 Structural Edge Types

| Edge | From → To | Meaning |
|:---|:---|:---|
| `PUBLISHES_TO` | Application/Library $\rightarrow$ Topic | Publishes messages on this topic |
| `SUBSCRIBES_TO` | Application/Library $\rightarrow$ Topic | Consumes messages from this topic |
| `ROUTES` | Broker $\rightarrow$ Topic | Broker is responsible for routing this topic |
| `RUNS_ON` | Application/Broker $\rightarrow$ Node | Component is deployed on this infrastructure node |
| `USES` | Application/Library $\rightarrow$ Library | Component or library depends on this shared library (transitive chains also generated, 30% probability per library) |
| `CONNECTS_TO` | Node $\rightarrow$ Node, or Node $\rightarrow$ switch $\rightarrow$ router | Network link (undirected cable). Default: random host mesh (30% probability per pair, `--connection-density`). With a `network:` block: a two-tier switched network whose links carry `from_interface`, `to_interface` and optionally `capacity_mbps` |

These six edge types constitute the **structural graph $G_{\text{structural}}$**, which is used by the simulation stage (Step 5) to trace failure propagation. A separate **analysis graph $G_{\text{analysis}}$** is derived from $G_{\text{structural}}$ by computing `DEPENDS_ON` edges, which are used exclusively by Steps 2, 3, and 4 (analysis, prediction, and diagnosis). The separation ensures that prediction and simulation remain independent.

---

## 4. Generation Modes

### 4.1 Scale-Preset Mode

The fastest way to generate a graph. Six named presets are built in ([tools/generation/models.py](../tools/generation/models.py) `SCALE_PRESETS`):

| Preset | Applications | Topics | Brokers | Nodes | Libraries | Typical Use |
|:---|:---:|:---:|:---:|:---:|:---:|:---|
| `tiny` | 5 | 5 | 1 | 2 | 2 | Unit tests |
| `small` | 15 | 10 | 2 | 4 | 5 | Quick checks |
| `medium` | 50 | 30 | 3 | 8 | 10 | Development |
| `large` | 150 | 100 | 6 | 20 | 30 | Integration tests |
| `jumbo` | 300 | 120 | 10 | 40 | 50 | Large-scale benchmarks (matches scenario_07 counts) |
| `xlarge` | 500 | 300 | 10 | 50 | 100 | Performance benchmarks |

In scale-preset mode, QoS values for each topic are sampled uniformly from the full option space, and node placement, publish/subscribe wiring, and library usage use the same cluster-affinity logic as statistical mode (§6) but with no distribution targets to steer toward. Total edge counts are seed-dependent; expect roughly 4–10$\times$ the total node count in edges.

```bash
PYTHONPATH=. python cli/generate_graph.py --scale medium --seed 42 --output output/graph.json
```

### 4.2 Statistical-Config Mode (YAML)

The primary mode for validation work. A YAML configuration file fully specifies exact component counts plus statistical distributions for node loading, pub/sub fan-in/out, QoS categories, and criticality (see §5 for the schema). When loaded, the generator uses clamped-Gaussian sampling (`StatisticalMetric`) for continuous quantities and weighted-list sampling for categorical ones, producing topologies whose structural properties closely match the declared distributions.

```bash
PYTHONPATH=. python cli/generate_graph.py --config data/scenarios/scenario_01_autonomous_vehicle.yaml \
       --output output/av_system.json
```

`--scale` and `--config` are mutually exclusive.

### 4.3 Domain and Scenario Enrichment

Two optional flags add realistic naming and domain-matched QoS mappings to either generation mode:

- **`--domain`** selects a domain name pool: `av`, `iot`, `finance`, `healthcare`, `hub-and-spoke`, `microservices`, `enterprise`, `atm` (defined in [tools/generation/datasets.py](../tools/generation/datasets.py) `DOMAIN_DATASETS`). Application, library, broker, and topic names are drawn from domain-specific lists (e.g. `path-planner`, `object-detector`) instead of generic `App-{n}` identifiers; system-hierarchy labels are similarly domain-specific.
- **`--scenario`** selects a QoS mapping table (same choice list) that overrides the statistical distribution for each topic's QoS based on name pattern matching — e.g. under the `av` scenario, topics whose names contain `cmd` receive `RELIABLE`/`HIGHEST` QoS regardless of the general distribution.

Both flags accept the same eight values; an unrecognized string is rejected by argparse (`--domain`/`--scenario` are `choices=[...]`-constrained), it does not silently fall back. Domain and scenario can be combined with either `--scale` or `--config`; a YAML config's own `domain:`/`scenario:` fields are used automatically if the CLI flags are omitted.

---

## 5. Scenario Configuration Files

Each `data/scenarios/scenario_*.yaml` file is a self-contained specification for one validation scenario, passed via `cli/generate_graph.py --config`. For the full list of scenarios, their stress parameters, and topology-class objectives, see [scenario.md](scenario.md).

### 5.1 YAML Schema Reference

Every scenario YAML follows this structure (see [scenario_01_autonomous_vehicle.yaml](../data/scenarios/scenario_01_autonomous_vehicle.yaml) for a complete real example):

```yaml
graph:
  seed: <integer>              # Determines full reproducibility
  domain: <string>             # Optional: av | iot | finance | healthcare | hub-and-spoke | microservices | enterprise | atm
  scenario: <string>           # Optional: same values, QoS mapping table
  connection_density: <float>  # Optional: probability of connects_to edges (default 0.3)
  network:                     # Optional: switched network instead of the host mesh
    topology: two_tier         #   hosts -> top-of-rack switches -> core routers
    hosts_per_rack: <int>      #   default 4 (hosts are shuffled into racks)
    core_routers: <int>        #   default 2; every switch uplinks to every core
    host_link_mbps: <float>    #   optional host<->switch capacity (absent = unbounded)
    uplink_mbps: <float>       #   optional switch<->router capacity
  intra_cluster_coupling: <float> # Optional: p_intra for cluster-biased sampling (default 0.65)

  counts:
    nodes: <int>
    applications: <int>
    libraries: <int>
    topics: <int>
    brokers: <int>

  application_stats:
    direct_publish_count: { mean, median, std, min, max, q1, q3, iqr }
    direct_subscribe_count: { ... }
    total_publish_count_including_libraries: { ... }   # takes priority over direct_* when present
    total_subscribe_count_including_libraries: { ... }
    app_criticality_distribution:
      category_counts: { HIGH: <int>, MEDIUM: <int>, LOW: <int> }
      # (+ total_count, mode, mode_count, mode_percentage — see StatisticalMetric-style fields above)
    app_hotstandby_distribution:
      category_counts: { true: <int>, false: <int> }
      # (+ the same StatisticalMetric-style fields)
    # Both pools are read for their tier *counts* only — which app receives
    # which tier is decided by the ranking in _assign_criticality_two_pass().

  library_stats:
    applications_using_this_library: { mean, median, std, min, max, q1, q3, iqr }
    direct_publish_count: { ... }
    direct_subscribe_count: { ... }

  topic_stats:
    topic_size_bytes: { mean, median, std, min, max, q1, q3, iqr }
    applications_publishing_to_this_topic: { ... }
    applications_subscribing_to_this_topic: { ... }

  qos_stats:
    qos_durability_distribution:
      category_counts: { volatile: <int>, transient_local: <int>, transient: <int>, persistent: <int> }
    qos_reliability_distribution:
      category_counts: { best_effort: <int>, reliable: <int> }
    qos_transport_priority_distribution:
      category_counts: { low: <int>, medium: <int>, high: <int>, critical: <int> }
```

`node_stats.applications_per_node` is accepted for backward compatibility (its presence still flips `metadata.generation_mode` to `"statistical"`) but is not read by the generator — node loading falls out of the cluster-affine `RUNS_ON` placement in §6, not a direct distribution target.

Four pub/sub wiring strategies are selected by which stats section is present, in this priority order: `total_publish_count_including_libraries` > `direct_publish_count` (application-level) > `applications_publishing_to_this_topic` (topic-level) > uniform random fallback when no stats are configured at all. Only one strategy runs per generation.

### 5.2 Writing a New Scenario

- **Copy Template** — Copy the closest existing scenario file and rename it `scenario_NN_<name>.yaml`.
- **Set Unique Seed** — Set `graph.seed` to a value not used by another scenario.
- **Scale Component Counts** — Adjust `graph.counts` using the presets table in §4.1 as a reference point.
- **Tune Statistical Distributions** — Configure distributions to reflect specific topological properties (e.g. increase subscriber fan-out to test Reliability, or reduce broker count to force SPOFs).
- **Configure QoS Distributions** — Set QoS durability/reliability/priority category counts to match the target domain profile.
- **Document expected outcomes** — Write a comment block at the top of the file describing the scenario's validation objective (see [scenario.md](scenario.md) for the expected-topology-class taxonomy).

---

## 6. The `StatisticalGraphGenerator` Pipeline

Located at [tools/generation/generator.py](../tools/generation/generator.py). `generate()` runs as an ordered sequence of phase methods, each with a single responsibility; `GenerationService` instantiates and calls this class and should be used instead of calling it directly.

### 6.1 Pass 1 — Entities

| Phase | Method | Produces |
|:---|:---|:---|
| Infrastructure | `_build_infrastructure` | `Node`, `Broker` entities |
| Topics | `_build_topics` | `Topic` entities: QoS, size, domain-aware frequency, deadline SLA, history depth, noisy criticality |
| Hierarchy clusters | `_assign_clusters` | Pre-assigns apps/libs/topics to `css_name` clusters so later wiring is structurally coherent, not an independent label |
| Applications | `_build_apps` | `Application` entities (criticality defaulted to `MEDIUM` and hotstandby to `False`; both are assigned for real in Pass 2, once the topology exists) |
| Libraries | `_build_libs` | `Library` entities and the cluster→libs grouping |

**Topic frequency** is sampled by `_sample_topic_frequency()` from a per-domain log-uniform range (`_DOMAIN_FREQ_BOUNDS`), then snapped to the nearest of a fixed Hz set. **Topic deadline** (`deadline_ms`) is anchored to the message period $T = 1000/f$ and scaled by a slack factor, while **history depth** (`history_depth`) reflects buffering requirements under DDS `KEEP_LAST`. Criticality shifts *where* each draw is centred but never *which* values are reachable: the slack factor is triangular over a support shared by all three tiers, every tier can declare no deadline at all, and the depth ladder overlaps between tiers. That overlap is load-bearing — with criticality-disjoint bands, `deadline_ms · f / 1000` inverted the label exactly and handed the GNN a closed form for the ~17 % label noise below (enforced by `tests/test_generation_service.py::TestTemporalContractNonInvertibility`). **Topic criticality** is derived from the QoS weight via a threshold table (`HIGH`, `MEDIUM`, `LOW`), then has ~17% label noise injected (`_derive_topic_criticality_with_noise()`) so a GNN cannot recover it from QoS features via lookup alone — it must use structural context. These draws use an isolated RNG stream (`topic_attr_rng`), so changing them never perturbs the main topology RNG and existing seeded outputs for unrelated fields stay stable.

### 6.2 Pass 2 — Relationships

Edges are constructed in dependency order because later steps consume earlier ones (e.g. `PUBLISHES_TO` wiring needs each app's *inherited* library-publish count, which needs `USES` edges to already exist):

1. **`RUNS_ON`** (apps) — `_assign_apps_to_nodes()`: 70% cluster-affine placement, so functionally related apps share infrastructure (this matters for node-level betweenness / SPOF detection).
2. **`ROUTES`** — `_wire_routes()`: each topic gets 1–2 brokers (30% chance of a redundant second broker); a guard assigns any unrouted broker a topic round-robin so no broker is invisible to ROUTES-based metrics.
3. **`RUNS_ON`** (brokers) — `_rewrite_broker_placement()`: places each broker on a node in the plurality cluster of the topics it routes, co-locating brokers with the apps they serve.
4. **`USES`** and library-direct pub/sub — `_wire_uses()`: lib→lib transitive dependencies (30% chance per library), then, if configured, direct library `PUBLISHES_TO`/`SUBSCRIBES_TO` edges, then app→lib usage.
5. **`PUBLISHES_TO`/`SUBSCRIBES_TO`** (apps) — `_wire_pubsub()` dispatches to one of four mutually-exclusive strategies (§5.1's priority order). All four apply cluster-biased sampling (`_sample_biased`, `p_intra` = `intra_cluster_coupling`) and QoS-affinity steering (`_qos_preferred_topics()`: gateway/controller apps draw from `RELIABLE`/`HIGH` topics first, sensors from `BEST_EFFORT`/`LOW`).
6. **`CONNECTS_TO`** — `_wire_connects()`: probabilistic node mesh, guarded against a fully disconnected result. With a `network:` block, `_wire_fabric()` then replaces the mesh with a two-tier switched network: hosts in racks of `hosts_per_rack` behind `SW{i}`, and every switch uplinked to every `RT{j}` (there are no cores when everything fits in one rack). The mesh is still drawn first, and the fabric uses its own RNG stream (`seed + 54321`). The output is therefore a **mesh twin**: identical to the same YAML without `network:`, except for `connects_to`, `network_devices` and `metadata.network`. See [`data/network/`](../data/network/) for an example, kept out of `data/scenarios/` so it doesn't join the evaluation corpus.
7. **Post-topology passes** — `_apply_post_topology()`: guarantees every app has at least one pub/sub edge (isolated apps would otherwise inflate F1 scores trivially), then assigns 3-tier criticality (`HIGH`, `MEDIUM`, `LOW`) and correlated `hotstandby: bool` redundancy (with dual-node host placement) via `_assign_criticality_two_pass()`, now that the topology is known. Apps are ranked by a composite score — connected-topic QoS urgency (0.50, itself `0.6·w(t) + 0.4·Topic.criticality` blended as `0.55·max + 0.45·mean` over the app's pub/sub topics), operational `app_type` semantics (0.25: controller > gateway > processor > actuator > sensor > monitor), and normalised pub/sub degree (0.25) — plus a small seeded jitter to break ties. The tier counts come from the scenario's `app_criticality_distribution`, and `hotstandby` is granted to the top of the same ranking, so redundancy correlates with criticality by construction.

### 6.3 Code-Metrics Generation

Every `Application` and `Library` node carries a `code_metrics` block feeding the RM Maintainability $M(v)$ penalty, generated from type-specific archetype ranges (`_CODE_METRICS_PARAMS`, `_LIB_CODE_METRICS_PARAMS`):
- **Size** — `total_loc`, `total_classes`, `total_methods`, `total_fields`.
- **Complexity** — `total_wmc`, `avg_wmc`, `max_wmc`.
- **Cohesion** — `avg_lcom`, `max_lcom`.
- **Coupling** — `avg_cbo`, `max_cbo`, `avg_rfc`, `max_rfc`, `avg_fanin`, `max_fanin`, `avg_fanout`, `max_fanout`.

### 6.4 System-Hierarchy Assignment

Every application and library carries a `system_hierarchy` block (`csc_name`, `csci_name`, `css_name`, `csms_name`) representing its position in the MIL-STD-498 decomposition hierarchy, drawn from `SYSTEM_HIERARCHY_POOLS` for the configured domain (or `GENERIC_HIERARCHY_POOL` when no domain is set). `css_name` is the same value used for hierarchy-cluster pre-assignment in §6.1 — it is not an independent random draw.

### 6.5 QoS and Contract Assignment

Topics receive categorical values for durability, reliability, and transport priority, along with continuous temporal SLA contracts (`deadline_ms`, anchored to the publishing period $T = 1000/f$) and queue capacity limits (`history_depth`, matching DDS `KEEP_LAST`, pushed up by reliability/durability and down by publish rate). Neither is drawn from criticality-exclusive ranges — see §6.1. The QoS weight (`QoSPolicy.calculate_weight()`) drives both the criticality threshold lookup (§6.1) and, downstream in analysis, the QSPOF Availability $A(v)$ term — topics carrying `PERSISTENT + RELIABLE + CRITICAL` traffic receive the maximum QoS weight ($1.0$).

---

## 7. Output Format

The generator produces a single JSON file. Field values below are from an actual `--scale tiny --seed 1 --domain av --scenario av` run (some fields randomized further for illustration):

```json
{
  "metadata": {
    "scale": { "apps": 5, "topics": 5, "brokers": 1, "nodes": 2, "libs": 2 },
    "seed": 1,
    "generation_mode": "random",
    "domain": "av",
    "scenario": "av"
  },
  "nodes": [
    { "id": "N0", "name": "nav-computer" }
  ],
  "brokers": [
    { "id": "B0", "name": "zenoh-router" }
  ],
  "topics": [
    {
      "id": "T0",
      "name": "goal_pose",
      "size": 8192,
      "qos": {
        "durability": "TRANSIENT_LOCAL",
        "reliability": "RELIABLE",
        "transport_priority": "HIGH",
        "deadline_ms": 1000.0,
        "history_depth": 50
      },
      "frequency": 1.0,
      "criticality": "HIGH",
      "deadline_ms": 1000.0,
      "history_depth": 50
    }
  ],
  "applications": [
    {
      "id": "A0",
      "name": "state-estimator",
      "version": "2.3.3",
      "app_type": "service",
      "role": ["Engineer", "Supervisor"],
      "criticality": "HIGH",
      "hotstandby": true,
      "system_hierarchy": {
        "csc_name": "Robotic Systems",
        "csci_name": "Control Software",
        "css_name": "Path Planning",
        "csms_name": "AV"
      },
      "code_metrics": {
        "size":       { "total_loc": 1198, "total_classes": 14, "total_methods": 123, "total_fields": 28 },
        "complexity": { "total_wmc": 199, "avg_wmc": 14.24, "max_wmc": 41 },
        "cohesion":   { "avg_lcom": 47.81, "max_lcom": 203.4 },
        "coupling":   { "avg_cbo": 4.24, "max_cbo": 6, "avg_rfc": 26.99, "max_rfc": 66,
                        "avg_fanin": 2.91, "max_fanin": 7, "avg_fanout": 5.95, "max_fanout": 12 }
      }
    }
  ],
  "libraries": [
    {
      "id": "L0",
      "name": "nav-core",
      "version": "1.1.4",
      "system_hierarchy": { "csc_name": "Autonomous Vehicle Platform", "csci_name": "Perception Software",
                             "css_name": "Sensor Fusion", "csms_name": "AV" },
      "code_metrics": { "size": { "total_loc": 2544, "total_classes": 19, "total_methods": 235, "total_fields": 71 },
                        "complexity": { "total_wmc": 300, "avg_wmc": 15.81, "max_wmc": 24 },
                        "cohesion": { "avg_lcom": 57.84, "max_lcom": 210.6 },
                        "coupling": { "avg_cbo": 12.13, "max_cbo": 22, "avg_rfc": 39.89, "max_rfc": 80,
                                      "avg_fanin": 4.03, "max_fanin": 11, "avg_fanout": 4.06, "max_fanout": 11 } }
    }
  ],
  "relationships": {
    "runs_on":      [{ "from": "A0", "to": "N1" }],
    "routes":       [{ "from": "B0", "to": "T0" }],
    "publishes_to": [{ "from": "A1", "to": "T0" }],
    "subscribes_to":[{ "from": "A1", "to": "T0" }],
    "connects_to":  [{ "from": "N1", "to": "N0" }],
    "uses":         [{ "from": "L1", "to": "L0" }]
  }
}
```

Note `role` is a list of user-facing role tags (drawn from `Operative`/`Engineer`/`Analyst`/`Administrator`/`Supervisor`), not a pub/sub direction indicator; `Node` and `Broker` carry no fields beyond `id`/`name` (no `cpu_cores`, `memory_gb`, or similar — infrastructure capacity is not modeled by the generator).

---

## 8. Programmatic API

```python
from tools.generation import GenerationService, load_config, generate_graph
from tools.generation.models import GraphConfig, SCALE_PRESETS

# Generate using a scale preset with custom connection density
data = generate_graph(scale="medium", seed=42, connection_density=0.15)

# Load configuration from a YAML file
config = load_config(Path("data/scenarios/scenario_01_autonomous_vehicle.yaml"))
service = GenerationService(config=config)
data = service.generate()

# Inspect available presets
for name, preset in SCALE_PRESETS.items():
    print(f"{name}: {preset}")
```

---

## 9. Known Limitations and Open Issues

*Document last updated: July 2026. Maintained alongside `tools/generation/` and `cli/generate_graph.py`.*

| # | Area | Description | Status |
|:---|:---|:---|:---|
| 1 | `--domain` validation | Constrained by argparse `choices=[...]`; an unsupported string is rejected outright rather than silently falling back. | Clarified (an earlier draft of this doc claimed a silent fallback) |
| 2 | `generation_mode` field | `metadata.generation_mode` is `"statistical"` only when the YAML config has at least one `*_stats` section (including `node_stats`, which is otherwise unused — see §5.1). A config with only `counts` produces `generation_mode: "random"`. | Open |
| 3 | Pub/sub duplicate edges | The four wiring strategies do not deduplicate against each other's output: the same `(app, topic)` pair can appear twice if paths overlap. The import pipeline deduplicates on ingest; `validate_and_clean_schema()` also deduplicates within each relationship type before the file is written. | Open |
| 4 | Broker guard semantics | The unrouted-broker guard (round-robin assignment) is deterministic but can assign a stranded broker to an already over-routed topic, skewing broker betweenness scores when there are many brokers and few topics. | Open |
| 5 | Domain frequency-bounds keys | `_DOMAIN_FREQ_BOUNDS` was keyed on descriptive domain names (`autonomous_vehicle`, `iot_smart_city`, `financial_trading`, `hub_and_spoke`) that never matched the real `--domain` values (`av`, `iot`, `finance`, `hub-and-spoke`), so those domains' topic frequencies silently used the generic 0.1–100 Hz range instead of a domain-matched one. | **Resolved** (July 2026) |

Earlier resolved items (cluster-affine hierarchy assignment, infrastructure/broker placement, criticality two-pass assignment, role-constraint enforcement, QoS–topology coherence, topic-attribute leakage prevention) are no longer listed here — see git history for `tools/generation/generator.py` for that record.
