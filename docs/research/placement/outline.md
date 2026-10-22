# Outline: Does Cascade-Aware Placement Pay Off?

**Working title.** *Does Cascade-Aware Placement Pay Off? A Held-Out Evaluation of Pre-Deployment Component Placement for Publish–Subscribe Systems*

**Target.** The ICSA research track (Stage 1). Check the current call for papers for the page limit, format and deadline before allocating space; the budget below assumes about 10 pages plus references. The TOSEM extension is planned in [tosem-extension.md](tosem-extension.md).

**Type of paper.** An empirical study with a pre-registered protocol ([PREREGISTRATION.md](PREREGISTRATION.md)). It is *not* a new-optimizer paper. The MILP is a means to an exact cascade-aware arm, not a contribution.

**Rules carried over from the 2026-10-19 review of the earlier draft:**
- No number without an artifact. Every number is an `{{artifact:key}}` placeholder until `reconcile_manuscript.py` fills and checks it.
- No invented components or metrics: no "SRI", no "P-GOA", no testbed claims in Stage 1.
- The five open-source models are *hand-authored models*, not "authentic" systems.
- Kubernetes' default scheduler is traffic-unaware and spread-biased. Co-location requires explicit affinity rules, so it is not the default.
- The McCormick constraints on products of binaries are exact, not a relaxation.
- Optimality is claimed only where the MILP closed its gap.
- No "first" claim until the full texts of F2 (Lera et al., which uses transitive closures for availability) and F4 (Zhang et al., critical nodes on a microservice dependency graph) have been read ([related-work-matrix.md §1.4](related-work-matrix.md#14-fog-edge-and-cloud-placement)).

---

## Contributions

- **C1.** A pre-registered, held-out evaluation of transitive-cascade-aware placement against cost-only (Deploy-PS, ScatterD), count-based (in-degree spreading), scheduler-style (`k8s_spread`) and one-hop (CHASE) rules. It uses 17 pub-sub models and pre-registered resource priors.
- **C2.** A verification protocol. A reliability claim must beat both the best feasible baseline and in-degree spreading by more than the larger of seed noise and the spread across the solver's tied optima.
- **C3.** Evidence on when co-location helps and when it hurts, across the moderators of RQ4.
- **C4.** An open artifact: `saag-deploy`, the spec generator and priors, the study runner, and the analysis.

## Section plan

| § | Section | Content | Pages |
|---|---|---|---|
| 1 | Introduction | Cost-driven placement tools favour co-location: Deploy-PS (zero cost when co-located), ScatterD (14→8 processors on a real avionics system, with no failure model) and DIF (link failures only). CHASE is the one counterweight, and it is one hop deep. Pub-sub systems propagate failures transitively. The question; RQ1–RQ4; the pre-registered outcome table; contributions | 1.25 |
| 2 | Motivating example | The hand-authored Autoware model. Sensor drivers are pinned to ECU-Sensing-FPGA, and 16 planning, localization and control components share ECU-Main-Brain. The free decision is how to spread that consolidated stack. `multi_object_tracker` subscribes to both lidar objects and radar objects, so it shows why the gating rule matters: an all-of proxy kills it on lidar loss, an any-of oracle does not. **Fig. 1** | 0.75 |
| 3 | Background and related work | SE deployment optimization (DIF, Meedeniya, the PerOpteryx line, Aleti's survey); pub-sub and DDS deployment (Deploy-PS and S-IDE); avionics consolidation (ScatterD); availability-aware cloud placement (CHASE, INDaaS); Kubernetes topology spread. **Table 1**: the positioning matrix, condensed from [related-work-matrix.md §2](related-work-matrix.md#2-positioning-on-the-dimensions-we-care-about) | 1.25 |
| 4 | Approach | 4.1 System and spec model. 4.2 Placement as a RUNS_ON edit, with the dependency graph re-derived. 4.3 The first-order proxy (all-of). 4.4 The MILP: replicas, capacity, anti-affinity, failover-safe deadlines, and the ε-constraint front; tie enumeration by no-good cuts. 4.5 Held-out verification: O1 and O2, the noise bar η, and the claim rule. **Fig. 2**: the pipeline. **Table 2**: proxy vs O1 vs O2 ([oracles.md §1](oracles.md#1-the-three-scoring-functions)) | 2.0 |
| 5 | Study design | The RQs and H1, the arms, the population (17 units in 3 blocks), the priors and primary cell, the statistics, and the decision table. All of it mirrors the preregistration. **Table 3**: arms. **Table 4**: priors | 1.25 |
| 6 | Results | 6.1 RQ1: consolidation cost. **Fig. 3**: E[O1] against host count for `scatterd` and `ffd`, with the matched-host contrast S6. 6.2 RQ2: H1 and S1–S5. **Fig. 4**: paired d_s per scenario with the HL CI and the ±δ band. **Table 5**: Holm-adjusted results and beyond-noise W/T/L. 6.3 RQ3: verification noise. **Fig. 5**: η decomposition and claim survival; ρ(proxy, O) per scenario. 6.4 RQ4: moderators. **Fig. 6**: forest plot of d against the moderators and sweep factors; the CHASE co-location share | 2.25 |
| 7 | Discussion | When to co-locate and when to spread. Guidance for architects, keyed to the outcome in the decision table. What a planner should report: the noise bar, the tie spread, and the proxy–oracle ρ | 0.5 |
| 8 | Threats to validity | Construct: internal oracles, synthetic priors, the proxy–oracle gating mismatch, first-broker routing. Internal: the solver time limit (the MILP gap as a covariate), tie enumeration that cannot be exhaustive. External: one generator family, five hand-authored models, crash-stop failures only. Conclusion: n = 17, attainable-p floors, Holm | 0.5 |
| 9 | Conclusion | The answer to the question, and the TOSEM testbed roadmap | 0.25 |

## RQ → evidence map

| RQ | Confirmatory test | Figure / table | Artifact key prefix |
|---|---|---|---|
| RQ1 consolidation cost | S6 (Holm) | Fig. 3, Table 5 | `rq1.*` |
| RQ2 does cascade-awareness pay off | H1 (primary), S1–S5 (Holm), TOST | Fig. 4, Table 5 | `h1.*`, `s1.*` … `s5.*` |
| RQ3 verification noise | none (descriptive) | Fig. 5 | `rq3.*` |
| RQ4 moderators | none (exploratory) | Fig. 6 | `rq4.*` |

## Writing order

1. §4 and §5 can be written now from the code and the preregistration. A draft is in [draft.md](draft.md).
2. §3 needs the open full-text reads in [related-work-matrix.md §5](related-work-matrix.md#5-open-verification-tasks), at least Meedeniya (C4) and the DIF Fig. 5 formula.
3. §1 and §2 are drafted now. Their last paragraph waits for the outcome row of the decision table.
4. §6 and §7 are written only after the analysis script has run on the frozen protocol.
