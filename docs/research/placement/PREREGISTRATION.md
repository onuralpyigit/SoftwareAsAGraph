# Pre-registration: Does Cascade-Aware Placement Pay Off?

**Status: DRAFT, not yet frozen.**

- **What freezing means.** The file is registered when it is committed with this line changed to `FROZEN <date> <commit>`. That commit must come before any study number exists.
- **The only runs allowed before freezing** are the pilot runs in §7. They measure noise only, never an effect.
- **What is exploratory.** Anything not listed below as primary or secondary is exploratory and must be reported as such.

Companions:
- [oracles.md](oracles.md): what each scoring function measures;
- [priors.md](priors.md): how deployment specs are generated;
- [outline.md](outline.md) and [draft.md](draft.md): the paper.

---

## 1. Motivation

The read-only probes of 2026-10-18 ([related-work-matrix.md §7](related-work-matrix.md#7-probe-evidence-2026-10-18)) and the deployment stage's two worked examples ([docs/deployment.md §4](../../deployment.md#4-held-out-verification-and-the-recommendation-rule)) found the following. All used n = 3 seeds and at most 3 scenarios.

- **Consolidation.** Consolidating onto fewer hosts lowers expected impact mostly because fewer hosts can fail. That is not a pub-sub finding.
- **Locality.** At a fixed host count, locality *lowered* E[I_comp] by 10–18%. A worst-case tension appeared in 2 of 6 cells.
- **Budgeted anti-affinity.** In-degree won 2 of 3 scenarios, random won the third, and cascade-marginal selection never won.
- **On `atm_system`,** the planner found no reliability gain beyond noise. ρ(proxy, oracle) was +0.24.
- **On `atm_two_tier_system`,** it did find one.

These observations motivate the study. They are not its evidence, and none of them is re-used as a result.

## 2. Population

**The unit of analysis is the scenario.** It is not the spec draw, the seed or the component. Pairing at (scenario × draw) or (scenario × seed) would be pseudo-replication and is forbidden.

| Block | Scenarios | n |
|---|---|---|
| Generated, `role: evaluation` in [MANIFEST.json](../../../data/scenarios/MANIFEST.json) | av, iot_smart_city, financial_trading, healthcare, hub_and_spoke, microservices, enterprise, telecom_ran, industrial_scada, realtime_gaming, logistics_fleet | 11 |
| ATM family, collapsed to one unit | `atm_system` (8 hosts). The tiny, medium, large and xlarge variants are used for RQ4 scaling only | 1 |
| Hand-authored open-source models | realworld_autoware_ros2, realworld_cloud_microservices, realworld_edgex, realworld_homeassistant, realworld_trainticket | 5 |
| **Total** | | **17** |

**Excluded:** the fixtures (`tiny_system`, `xlarge_system`, `integration_hub_migration_system`) and `atm_two_tier_system`. The last is pilot only (§7) and is not in the corpus.

**Naming.** The five open-source models are called *hand-authored models of* those systems, never "authentic" or "real". TrainTicket is RPC-based in reality, and the paper says so.

## 3. Arms

All arms see the same spec. With `use_all_hosts: true` they use the same host count.

| Arm | Definition |
|---|---|
| **CASCADE** (treatment) | The point with minimum proxy `blast_exp` subject to traffic ≤ (1 + ε) · traffic(`k8s_spread`), with **ε = 0.10**. If no such point is feasible, take the minimum-traffic feasible point and record this. |
| `indeg_spread` (**primary comparator**) | `heuristics.py`: components in decreasing dependent count, spread across domains. |
| `k8s_spread` | Least-loaded greedy, domain first, then host. |
| `ffd` | First-fit decreasing; concentrates load. |
| `chase` | The CHASE rule (§3.1). |
| `deploy_ps` | Traffic-only optimum (the ε-front endpoint). Cost-only; RQ1. |
| `scatterd` | Lexicographic: minimum number of open hosts, then traffic. It uses fewer hosts, so it is **descriptive only** (RQ1b). |
| `random` | Mean over **N = 50** random feasible placements per draw: DIF's "unbiased average". Descriptive. |

**Solver for CASCADE, `deploy_ps` and `scatterd`.**
- **Hosts ≤ 16:** the exact MILP.
  - `time_limit` 600 s per solve, `time_budget` 1800 s, `probe_limit` 30 s.
  - On a time limit the incumbent is used, and the gap is recorded as a covariate.
- **Hosts > 16:** the incremental local search (commit 45fa62a8, to be merged first). Same front rule, 5 restarts from the baselines.

**Tied optima.** Up to **5** further placements are enumerated whose proxy value equals CASCADE's selected value, within 1e-6 relative, under the same traffic cap:
- by no-good cuts (MILP), or
- by distinct restarts (heuristic).

CASCADE's held-out value is the **mean over this tied set**, because an operator cannot tell its members apart. The minimum and maximum are reported as a sensitivity check.

### 3.1 CHASE operationalisation

- **Dependent u's tolerance toward sponsor v:** the minimum `deadline_ms` over the topics u receives from v. With no declared deadline, tolerance = ∞.
- **Sponsor v's recovery time:**
  - replicated v: the failover time `R_f`;
  - unreplicated v: the host MTTR `R_h` = 1 h.
- **Rule:** co-locate u with v if tolerance < recovery, otherwise anti-locate. Replicas are always anti-located.
- **Placement:** the CHASE greedy pipeline. Order by criticality, filter by capacity and deadline, apply the rule as soft affinity scores, then pick the least-loaded host.
- **Primary setting:** `R_f` = 1 s. The levels {50 ms, 10 s} are swept in RQ4.
- **Degeneracy.** For unreplicated sponsors, MTTR far exceeds millisecond deadlines, so the rule tends toward "co-locate every dependency". The share of pairs the rule co-locates is reported as a finding, not hidden.

## 4. Oracles, seeds and noise

- **O1 (primary):** `FailureSimulator(replica_aware=True, qos_weighting=True)` with any-of gating. This is today's `verify.py`.
- **O2 (secondary):** the same, with `input_gating="k_of_n"` (k = ⌈n/2⌉).
- **Proxy-aligned bound:** `input_gating="all"` is reported descriptively and is never tested ([oracles.md §4](oracles.md#4-o2-the-input-gating-switch-to-be-built)).
- **Seeds:** `42, 123, 456, 789, 2024, 7, 99, 314, 2718, 31337` (ten). κ = 1.
- **Per-instance noise bar:** η = max(κ · √(σ_X² + σ_B²), tie spread), exactly as in `select_recommendation` ([verify.py](../../../saag/deployment/verify.py)).
- `FaultInjector` is not used ([CLAUDE.md](../../../CLAUDE.md), *Deployment reliability claims are held-out*).

## 5. Primary hypothesis (RQ2)

**H1.** In the primary cell ([priors.md §1](priors.md#1-primary-cell-h1-is-tested-here-only)), CASCADE has lower held-out E[O1] than `indeg_spread`.

**Effect per scenario.**
- X_s = the median over the D = 5 draws of the tied-set mean of the seed-mean E[O1]. B_s is the same quantity for `indeg_spread`.
- d_s = (B_s − X_s) / B_s.
- A scenario with B_s = 0 is excluded and reported.

**Test.**
- One-sided Wilcoxon signed-rank test on {d_s} at α = 0.05, over n = 17.
- **Effect sizes:** the Hodges–Lehmann median of d with a 95% CI, and the matched-pairs rank-biserial correlation.

**Equivalence.**
- TOST: two one-sided Wilcoxon tests on d_s − δ < 0 and d_s + δ > 0, each at α = 0.05.
- δ is fixed by the pilot (§7).

**Decision table.** All three outcomes are publishable, and the headline sentence is chosen by this table, not after the fact.

| Superiority p < 0.05 | TOST p < 0.05 | Outcome | Headline |
|---|---|---|---|
| yes | — | **Superior** | Cascade-aware placement lowers held-out impact by HL = … [CI] beyond in-degree spreading. |
| no | yes | **Equivalent** | Cascade-aware placement is equivalent to in-degree spreading within ±δ. Under these models, transitive cascade-awareness does not pay off. |
| no | no | **Inconclusive** | The data cannot distinguish them at n = 17. Report the CI and the beyond-noise counts. |

## 6. Secondary family (Holm, α = 0.05, two-sided Wilcoxon on the same unit)

| ID | Contrast | Metric |
|---|---|---|
| S1 | CASCADE vs `k8s_spread` | E[O1] |
| S2 | CASCADE vs `chase` (R_f = 1 s) | E[O1] |
| S3 | CASCADE vs `ffd` | E[O1] |
| S4 | CASCADE vs `indeg_spread` | E[O2] |
| S5 | CASCADE vs `indeg_spread` | max O1 |
| S6 | `deploy_ps` vs `k8s_spread` (RQ1: does cost-only placement cost reliability at a matched host count?) | E[O1] |

**Beyond-noise count.** This is reported for H1 and S1–S3 and is not part of the Holm family.
- Per scenario: a *win* if (B_s − X_s) > η_s, a *loss* if < −η_s, otherwise a *tie*. Here η_s is the median over draws.
- A two-sided sign test on wins against losses.
- This is `reliability_claim` turned into a population statistic.

**RQ3 (descriptive, no p-values).**
- The share of η contributed by tie spread versus seed noise.
- The fraction of instances in which the proxy-optimal claim survives held-out verification.
- Per-scenario ρ(proxy, O1) and ρ(proxy, O2) over the unique candidate placements, with bootstrap 95% CIs.

**RQ4 (exploratory, effect sizes and CIs only).**
- d_s against each moderator:
  - chain depth;
  - mean reverse-closure size;
  - hotstandby fraction;
  - the sweep factors in [priors.md §2](priors.md#2-sweep-rq4-exploratory-only);
  - host count across the ATM size variants.
- The `random` and `scatterd` curves.
- The `all`-gating bound.
- The CHASE co-location share.

**Sensitivity analyses (pre-registered, reported regardless of outcome).**
- Leave-one-block-out: H1 re-run without the generated block, and again without the hand-authored block.
- The hand-authored block alone, n = 5. It can reach one-sided p = 0.031 only if all 5 scenarios win (§8), so it is **descriptive**.
- CASCADE's tied-set minimum and maximum in place of its mean.
- Instances where the MILP hit its time limit, excluded.

## 7. Pilot and δ

**Pilot instances:**
- `atm_two_tier_system` with [atm_two_tier_deployment.yaml](../../../data/deployment/atm_two_tier_deployment.yaml);
- `atm_system` in the primary cell, D = 5.

**What the pilot computes:**
- **δ** = the median over pilot instances of η / B. This is a noise ratio, never an effect.
- The floor is **3%**.

Because δ uses no d_s, keeping `atm_system` in the population does not leak an effect into the test.

**Recording.** The pilot's δ is written into this section in the freezing commit.

## 8. Attainable-p floor (exact Wilcoxon signed-rank)

"k smallest losses" means the losing scenarios are the k with the smallest |d_s|. Larger losses cost much more.

| n | one-sided, 0 losses | 1 smallest loss | 2 smallest | 3 smallest | two-sided, 0 losses | 1 | 2 | 3 |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 5 | 0.031 | 0.062 | 0.16 | 0.41 | 0.062 | 0.12 | 0.31 | 0.81 |
| 12 | 0.00024 | 0.00049 | 0.0012 | 0.0034 | 0.00049 | 0.00098 | 0.0024 | 0.0068 |
| 17 | 7.6e-06 | 1.5e-05 | 3.8e-05 | 0.00011 | 1.5e-05 | 3.1e-05 | 7.6e-05 | 0.00021 |
| 24 | 6e-08 | 1.2e-07 | 3e-07 | 8.3e-07 | 1.2e-07 | 2.4e-07 | 6e-07 | 1.7e-06 |

## 9. Exclusions and failures

- **Infeasible draw.** If any arm is infeasible in a draw, the draw is dropped for *all* arms. That arm's infeasibility rate is reported.
- **Infeasible scenario.** A scenario with no feasible draw is excluded and reported, together with its `diagnosis`.
- **Pre-registered behaviour is not a deviation.** CASCADE falling back to minimum traffic (§3) is recorded per instance and is not a deviation.
- **Seeds and parameters are fixed.** No seed, ε, κ, D, δ, prior or arm definition may change after freezing without an amendment in §10.

## 10. Artifacts

- **Runner:** `reproduce/placement_study.py`, with fingerprinted resume.
- **Analysis:** `reproduce/placement_significance.py`.
- **Outputs:** `results/placement_study/`. This directory is untracked; the artifacts are archived on Zenodo.
- **Manuscript checks:** every number in [draft.md](draft.md) is an `{{artifact:key}}` placeholder checked by `reproduce/reconcile_manuscript.py`.

**To be built and tested before freezing.** None of this exists yet, and each item must have tests.

1. **Merge the local search.** Merge 45fa62a8 (incremental local search with swap moves) into `saag/deployment/heuristics.py`.
2. **Spec generator.** Add `saag/deployment/spec_gen.py` and `data/deployment/priors.yaml` ([priors.md §4](priors.md#4-generator-to-be-built)).
3. **CASCADE's selection rule.** Add a blast-minimizing solve under a traffic cap to `milp.py`, plus tie enumeration at that capped point. Today's ε-front minimizes traffic under a blast cap, and ties exist only at the minimum-blast end.
4. **Cost-only arms.**
   - `deploy_ps`: the minimum-traffic anchor, which already exists.
   - `scatterd`: host-open binaries in `milp.py`, plus FFD with a traffic local search in `heuristics.py`.
5. **The `chase` baseline.** Add it to `heuristics.py`, add `recovery_ms` to `spec.py`, and add `tests/test_deployment_chase.py`.
6. **The `input_gating` switch.** Add it to `FailureSimulator` and thread it through `SimulationService` and `verify.py`, with `tests/test_input_gating.py` ([oracles.md §4](oracles.md#4-o2-the-input-gating-switch-to-be-built)).
7. **Moderator features** in `instance.py`: chain depth, mean reverse-closure size and hotstandby fraction.
8. **Runner and analysis.** `reproduce/placement_study.py` and `reproduce/placement_significance.py`.
9. **Manuscript checks.** Add `{{artifact:key}}` checks for [draft.md](draft.md) to `reproduce/reconcile_manuscript.py`.

## 11. Amendments

*None yet.*
