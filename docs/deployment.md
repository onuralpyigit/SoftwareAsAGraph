# Pre-deployment Planning: Deploy

**Map Applications and Brokers to Nodes under resource, replica, failure-domain, pinning and topic-deadline constraints, then verify every candidate on the replica-aware failure simulator before recommending one.**

[README](../README.md) | [Prescribe](prescription.md) | [Failure Simulation](failure-simulation.md) | [CLI guide](cli-pipeline-guide.md#pre-deployment-deploy)

> The stage **plans** a deployment. It never deploys anything and never writes to the repository.

---

## Table of Contents

1. [What This Stage Does](#1-what-this-stage-does)
2. [Deployment Spec](#2-deployment-spec)
3. [Optimization Model](#3-optimization-model)
4. [Held-out Verification and the Recommendation Rule](#4-held-out-verification-and-the-recommendation-rule)
5. [Programmatic API](#5-programmatic-api)
6. [Result Schema](#6-result-schema)
7. [Commands](#7-commands)
8. [Known Limitations](#8-known-limitations)

---

## 1. What This Stage Does

The topology says which components exist and how they communicate. It does not say what each Node can hold, how often it fails, or which Nodes fail together. A **deployment spec** supplies those facts (§2). The stage then works in three steps:

1. **Solve.** An exact MILP finds placements that satisfy every hard constraint. It traces a Pareto front that trades **cross-host traffic** against **expected blast radius** (§3).
2. **Verify.** Each candidate is re-scored on a different oracle: `FailureSimulator` with `replica_aware=True`, run over every single-host and every failure-domain outage. The same scoring runs on five baselines (§4).
3. **Recommend.** The stage recommends the least-traffic candidate that the simulator does not rank worse than the best baseline. It claims a reliability gain only when that gain clears the measured noise (§4).

**Why the verification step exists.** The optimizer's blast term is a first-order dependency proxy. The placement probes ([related-work matrix §7](research/placement/related-work-matrix.md#7-probe-evidence-2026-10-18)) found two problems with it:
- An exact optimizer on this proxy did not beat dependency-count spreading on a held-out oracle.
- Which of several proxy-tied optima the solver returns can flip the sign of the comparison.

So every reliability claim is graded by the simulator, never by the proxy that produced it. Most of the time the honest answer is *"feasible, less traffic, no reliability gain beyond noise"*, and the stage says exactly that.

### Module layout

| File | Role |
|:---|:---|
| [spec.py](../saag/deployment/spec.py) | `load_spec`, `bind_spec`, `resolve_latency`, `DeploymentSpecError` |
| [instance.py](../saag/deployment/instance.py) | `Instance`: placement-independent problem data and placement metrics |
| [milp.py](../saag/deployment/milp.py) | `Model` (sparse builder over `scipy.optimize.milp`/HiGHS) and `DeploymentMILP` |
| [heuristics.py](../saag/deployment/heuristics.py) | Baselines, `local_search`, and `heuristic_front` (the fallback for large models) |
| [verify.py](../saag/deployment/verify.py) | `apply_placement`, `score_placements`, `select_recommendation` |
| [models.py](../saag/deployment/models.py) | `Candidate`, `HeldOutScore`, `Diagnosis`, `DeploymentResult` |
| [service.py](../saag/deployment/service.py) | `DeploymentService.optimize`: the orchestration |

---

## 2. Deployment Spec

The spec is YAML or JSON. YAML needs PyYAML. Examples are in [data/deployment/](../data/deployment/).

```yaml
version: 1
description: free text
defaults:
  node:   {cpu: 16, mem_mb: 32768, p: 0.02}   # topology Nodes not listed under `nodes`
  domain: {p: 0.005}                          # probability a whole failure domain goes down
  app:    {cpu: 1.0, mem_mb: 512}             # replicas default to 2 for hotstandby apps, else 1
  broker: {cpu: 2.0, mem_mb: 2048}            # brokers are placed by the optimizer unless pinned
nodes:
  N0: {cpu: 16, mem_mb: 32768, p: 0.03, domain: rack-0}
domains:
  rack-0: {p: 0.004}
latency:
  per_hop_ms: 0.1          # used when the topology has CONNECTS_TO
  intra_domain_ms: 0.5     # used otherwise
  inter_domain_ms: 5.0
  matrix_ms: {N0: {N5: 1.7}}   # explicit pairs override both; filled symmetrically
apps:
  A1: {cpu: 2.0, mem_mb: 1024, replicas: 2, forbid: [N7]}
  A9: {pin: [N0, N3]}      # pin = the set of allowed Nodes
brokers:
  B0: {pin: [N2]}
constraints:
  anti_affinity: domain    # domain: replicas in distinct failure domains; host: distinct Nodes only
  use_all_hosts: false     # require every Node to carry at least one copy
  max_utilization: 1.0     # capacity headroom factor in (0, 1]
```

**Rules**

- **Validation is all-at-once.** Every problem is collected, then a single `DeploymentSpecError` lists them all. Checked:
  - unknown sections, keys and ids;
  - `cpu`, `mem_mb` > 0;
  - `p` in [0, 1];
  - `replicas` is an integer ≥ 1;
  - `pin` minus `forbid` is not empty;
  - a domain name equal to another Node's id.
- **Nodes without a domain** form singleton domains. A singleton domain adds no extra failure event.
- **Memory demand** is `mem_mb` plus a history buffer for each subscribed topic, `history_depth × size`. Libraries publish and subscribe on behalf of the applications that use them.
- **Latency precedence:**
  1. an explicit `matrix_ms` entry;
  2. otherwise CONNECTS_TO hop count × `per_hop_ms`, computed with [`Fabric.hops`](../saag/core/network.py); an unreachable pair is `inf`;
  3. otherwise the intra- or inter-domain constant.

  The same host is always 0 ms. `spec_summary.latency_source` records which rule applied.
- **Deadlines** come from a topic's `deadline_ms`, read at the top level first and then from `qos`.

The spec lives outside the topology on purpose. The topology schema, Neo4j persistence and the byte-identical scenario corpus are unchanged, so example specs go in `data/deployment/`, never in `data/scenarios/`.

---

## 3. Optimization Model

`DeploymentMILP` ([milp.py](../saag/deployment/milp.py)) solves the following model with HiGHS.

| Variables | Meaning |
|:---|:---|
| `x[c,n]` ∈ {0,1} | A copy of component *c* runs on Node *n* (fixed to 0 where *c* is not allowed) |
| `y[c,n]` ∈ {0,1} | *c*'s primary copy runs on *n* |
| `z[e,c]` ∈ [0,1] | *c* is down in failure event *e*: every single host, plus every domain with ≥ 2 hosts |
| `t` | The worst-case number of Applications down |

**Hard constraints.** Each family is named, so an infeasible model can say which family failed.

- **replicas:** Σₙ x = r(c); Σₙ y = 1; y ≤ x.
- **anti_affinity:** at most one copy of a replicated component per failure domain.
- **capacity_cpu, capacity_mem:** Σ demand · x ≤ `max_utilization` · capacity, for every Node. Brokers consume capacity too.
- **deadline:** failover-safe. For every copy of the publisher, every copy of the routing broker, and every copy of the subscriber, publisher → broker → subscriber latency must stay within the topic's deadline. Brokerless topics use the direct pair.
- **use_all_hosts:** optional.

**Blast coverage (replica-aware, exact).**
- z[e,c] ≥ Σ_{n∈e} x[c,n] − (r(c) − 1): a component is down only when all of its copies are inside the event.
- z[e,v] ≥ z[e,u] for every derived dependency arc v → u. Only the placement-independent types `app_to_app` and `app_to_broker` are used. Rule 6 `broker_to_broker` is excluded because it comes from the *authored* broker hosts, and the failure events already model co-hosted fate sharing.

At the optimum, z is exactly "*c* is down in *e*". `tests/test_deployment_milp.py` checks this against brute-force enumeration.

**Objectives**

| Name | Definition |
|:---|:---|
| `blast_exp` | Σₑ p(e) · (Applications down in e) |
| `blast_max` | maxₑ (Applications down in e) |
| `traffic` | Cross-host bytes/s. Each publisher primary and each subscriber copy pays B(t) = size × frequency when it is not on the routing broker's primary host. A brokerless pair pays when the subscriber copy is not on the publisher's primary host. |
| balance (`--balance`) | Peak cpu/memory utilisation, used as the final tie-break |

Latency-weighted traffic is reported for each candidate but is not optimised.

**Candidates**

| Name | How it is produced |
|:---|:---|
| `front_0` … `front_{K-1}` | The ε-constraint front, from minimum `blast_exp` to minimum `traffic`. Both ends are lexicographic. Interior points minimise traffic subject to `blast_exp ≤ ε`. |
| `tie_1` … `tie_T` | Further placements at the minimum `blast_exp`, found with no-good cuts. Their held-out spread measures how much the solver's arbitrary pick matters. |
| `max_blast` | Minimises `blast_max`, then `blast_exp`, then `traffic` |

Identical placements are folded into one candidate, which keeps the other names as `aliases`.

**Brokers are decision variables.** That makes the traffic term behave like a quadratic assignment problem, and HiGHS cannot close the gap on a tie-break stage that may also move brokers. So the solves are split:
- The first objective of every lexicographic solve chooses broker hosts freely, and is exact.
- Later stages refine the application placement with those broker hosts fixed.
- Interior front points try the broker sets of both anchors and keep the cheaper result.
- Ties use the minimum-blast anchor's broker set.

On the two-tier atm topology with uniform Node specs, free brokers halved the minimum `blast_exp` (1.10 against 2.20 with brokers pinned to their authored hosts).

**Determinism.** Every set is iterated as a sorted list, so the result does not depend on `PYTHONHASHSEED` (`tests/test_deployment_determinism.py`). A solve that stops at its time limit is marked `deterministic: false`.

**Limits and fallback**
- `--time-limit` applies per solve, inside an overall `--time-budget`. Solves that do not fit are listed in `skipped`.
- With `--solver auto`, the heuristic path takes over when the model would exceed `--max-nonzeros`, or when the first anchor finds no placement. The heuristic path is a weighted-sum local search over the baselines; it has status `heuristic` and makes no optimality claim.
- `--solver milp` raises instead of falling back. `--solver heuristic` forces the heuristic path.

**Infeasibility**
- Sufficient conditions are checked before building the model:
  - total demand above capacity;
  - fewer allowed Nodes, or domains, than replicas;
  - a deadline pair with no feasible host triple at all.
- If the model itself is infeasible, an elastic solve relaxes every hard-constraint family with slack variables. It reports per-family counts and the 20 rows that most need relaxing, each tagged with its topic, publisher, subscriber, broker and host.
- The CLI exits with code 2 in this case.

**HiGHS presolve.** The presolve bundled with scipy 1.15 can declare a feasible model infeasible after a lexicographic cap is added. Every infeasible verdict is therefore re-checked once with presolve off; the solve info records this as `presolve_retry`.

---

## 4. Held-out Verification and the Recommendation Rule

**Oracle.** Each placement is applied to the topology. `apply_placement` replaces RUNS_ON for the placed components, primary first, and drops the derived DEPENDS_ON edges. The result is loaded into a fresh `MemoryRepository` and simulated with:

```python
SimulationService(repo, qos_weighting=True, network_gating=g, replica_aware=True).run_failure_events(events, seed)
```

- `run_failure_events` fails each event's Nodes together. Unlike `run_failure_simulation`, it primes the discrete-event baseline first, so the flow-disruption term is live.
- `g` is on exactly when the topology has NetworkDevices, unless `--network-gating` overrides it.
- `FaultInjector`, the Predict-stage labeler, is never used here. `tests/test_deployment_verify.py` enforces this.

| Reported per candidate | Definition |
|:---|:---|
| `expected_impact` | Σₑ p(e) · I(e), with I the composite impact averaged over the seeds |
| `worst_impact` | maxₑ I(e) |
| `sigma_seed` | Standard deviation of `expected_impact` across seeds |
| Load concentration | Peak utilisation, hosts used, normalised entropy of copies per host |

**Baselines**

| Baseline | Strategy |
|:---|:---|
| `random` | A random allowed Node for each copy |
| `ffd` | First-fit-decreasing bin packing, which concentrates load |
| `k8s_spread` | Topology spread: least-loaded domain, then least-loaded Node |
| `indeg_spread` | Dependency-count spreading; always scored |
| `traffic_only` | The traffic-optimal MILP placement, with no reliability term |
| `authored` | The topology's own RUNS_ON, when every component has one |

The greedy baselines keep the replica, pin, capacity and anti-affinity families exact. They report any deadline they could not meet instead of hiding it.

**Recommendation rule** (`select_recommendation`)

1. **B\*** is the feasible baseline with the lowest `expected_impact`.
2. A candidate's **noise bar** is η = max(κ · √(σ_c² + σ_B\*²), tie spread). The tie spread is the held-out range across the proxy-tied optima, used as a floor because the solver's pick among them is arbitrary.
3. The verdict against B\* is `better`, `tied` or `worse` beyond η.
4. **Recommended** = the feasible optimizer candidate that is not `worse`, with the lowest (traffic, expected impact, peak utilisation). If every candidate is worse, B\* itself is recommended.
5. **`reliability_claim`** is true only if the recommendation is `better` than B\* **and** also beats `indeg_spread` beyond η.

When there is no claim, the `rationale` says so and quotes the traffic change against B\*. A note is appended when B\* scores well partly by concentrating load onto fewer hosts, which is the FFD effect the probes found.

**Two worked examples**

| Example | Result |
|:---|:---|
| `atm_system` + [atm_system_deployment.json](../data/deployment/atm_system_deployment.json) (the §7.7 probe setting) | No claim. Tie spread 0.0065, ρ(proxy, oracle) = +0.24, 38% less cross-host traffic than `indeg_spread`. This reproduces the probe's verdict through the product. |
| `atm_two_tier_system` + [atm_two_tier_deployment.yaml](../data/deployment/atm_two_tier_deployment.yaml) | Claim. With brokers free, the traffic-optimal placement chosen by blast tie-break beats `traffic_only` and `indeg_spread` on the simulator, well beyond a tie spread of 4e-5. |

---

## 5. Programmatic API

```python
from saag import Client, Pipeline
from saag.deployment import apply_placement

client = Client(repo=repo)                       # Neo4j or MemoryRepository
result = client.deploy("data/deployment/atm_two_tier_deployment.yaml",
                       front_points=5, ties=5, seeds=(42, 123, 456), kappa=1.0)
print(result.recommended, result.reliability_claim, result.rationale)
placed = apply_placement(repo.export_json(), result.recommended_candidate.placement)

# Inside a pipeline (reads only the topology; does not need analyze())
res = Pipeline.from_json("topology.json", repo=repo).deploy("spec.yaml").run()
res.deployment.to_dict()
```

`DeploymentService(repo).optimize(spec, **opts)` is the service-level entry point. `DeployGraphUseCase` wraps it for the hexagonal boundary.

---

## 6. Result Schema

`DeploymentResult.to_dict()` contains the following fields. Non-finite floats are written as `null`.

| Field | Content |
|:---|:---|
| `status` | `optimal`, `time_limit`, `heuristic` or `infeasible` |
| `solver` | `highs`, `heuristic` or `none` |
| `candidates[]` | Each has `name`, `kind` (front/tie/max/heuristic/baseline), `placement` (component → hosts, primary first), `traffic`, `cross_host_share`, `cross_domain_share`, `latency_weighted`, `blast_exp`, `blast_max`, `load`, `violations`, `pareto`, `aliases`, `solve`, `held_out`, `verdict`, `margin` and `noise_bar` |
| `recommended`, `best_baseline` | Candidate names |
| `reliability_claim`, `rationale` | The verdict and its explanation |
| `noise_floor`, `tie_spread`, `baseline_spread` | The noise measurements behind the verdict |
| `proxy_oracle_rho` | Spearman ρ of `blast_exp` against held-out expected impact, over unique placements |
| `front_flat` | True when the front has no blast range |
| `oracle` | Engine, flags, seeds, events and probabilities |
| `spec_summary` | Summary of the bound spec |
| `diagnosis` | Only when infeasible: `prechecks`, `families`, `culprits` |
| `skipped`, `seconds` | Solves that did not fit, and timings |

---

## 7. Commands

```bash
saag-deploy -i data/network/atm_two_tier_system.json \
    --spec data/deployment/atm_two_tier_deployment.yaml \
    -o output/deployment.json --placed-topology output/placed.json

# As a pipeline stage (with --all it runs only when a spec is given)
saag -i data/scenarios/atm_system.json --deploy --deployment-spec data/deployment/atm_system_deployment.json
```

`--placed-topology` writes the recommended placement applied to the topology, ready for `saag-import`. Flags are listed in the [CLI guide](cli-pipeline-guide.md#pre-deployment-deploy).

---

## 8. Known Limitations

- **First-order proxy.** The blast term counts reachability on the derived dependency graph. It does not capture partial degradation, rates or queueing. That is why verification exists, and why `proxy_oracle_rho` is reported (+0.24 to +0.45 on the examples).
- **Traffic routing.** Traffic goes through a topic's first routing broker (by id). The simulator routes a component's traffic from its lowest-id live host, not from the primary the optimizer chose.
- **Broker search.** Brokers are optimised exactly only in the first stage of each solve (§3).
- **Re-analysis of placed topologies.** `MemoryRepository` derives Rules 3–4 with `host_of` last-host-wins, so a replicated component's node-level dependencies use one host.
- **Where the tie spread is measured.** Only at the minimum-blast end of the front.
- **Replica cost.** Replica state-sync traffic is not priced. Replicas pay only for their own subscriptions.
- **Neo4j export.** The Neo4j path relies on `export_json()` keeping `deadline_ms`, `history_depth` and `hotstandby`. This was verified on `MemoryRepository` only.
- **Model size.** Large topologies (enterprise and xlarge, 40–50 hosts) are expected to exceed the nonzero guard and use the heuristic path.
