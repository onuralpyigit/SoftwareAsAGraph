# Placement Study: Proxy and Held-Out Oracles

**Companion to** [PREREGISTRATION.md](PREREGISTRATION.md). This file fixes what each scoring function measures, why the oracles count as held out from the optimizer, and where that independence stops.

---

## 1. The three scoring functions

| | Proxy (optimized) | O1 (primary oracle) | O2 (robustness oracle) |
|---|---|---|---|
| Code | `Instance.down_in` / `proxy_blast` ([instance.py:184](../../../saag/deployment/instance.py#L184)); the MILP's `_coverage` ([milp.py:240](../../../saag/deployment/milp.py#L240)) | `FailureSimulator` through `SimulationService.run_failure_events` ([verify.py](../../../saag/deployment/verify.py)) | Same as O1, with `input_gating="k_of_n"` (to be built, §4) |
| Graph read | Derived `app_to_app` and `app_to_broker` arcs. Rule 6 and the node-level arcs are excluded | Raw structural edges only: PUBLISHES_TO, SUBSCRIBES_TO, ROUTES, RUNS_ON, CONNECTS_TO. Never DEPENDS_ON | Same as O1 |
| Input semantics | **All-of.** A component is down if *any* component it depends on is down (reverse-dependency closure) | **Any-of.** A subscriber's impact is the *minimum* over its feeds, so one healthy feed keeps it alive ([failure_simulator.py:1097](../../../saag/simulation/failure_simulator.py#L1097)) | **k-of-n.** The k-th smallest feed impact, with k = ⌈n/2⌉ |
| Propagation | Transitive closure, deterministic | Transitive: a starved subscriber is re-queued and starves its own topics. It is stochastic through `_edge_prob` | Same as O1 |
| Value per event | Number of Applications down (binary each) | QoS-weighted `composite_impact` in [0, 1] | Same as O1 |
| Replicas | A component is down only if every copy is inside the event | `replica_aware=True`: a component is as impacted as its least-impacted host | Same as O1 |
| Brokers | Through `app_to_broker` arcs | `_cascade_broker_to_topic`: min over the routing brokers ([failure_simulator.py:1081](../../../saag/simulation/failure_simulator.py#L1081)) | Same as O1 |
| Events and probabilities | Every host, plus every domain with ≥ 2 hosts; p from the spec | Identical event set and p | Identical |
| Noise | None | Seed noise σ_seed over the pre-registered seeds | Same as O1 |

`FaultInjector` (I\*) is **not** used anywhere in the study. The deployment invariant in [CLAUDE.md](../../../CLAUDE.md) grades placement claims only on `FailureSimulator(replica_aware=True)`. I\* is also unsuitable on its own terms:
- its first wave equals in-degree;
- it saturates near 0.95 for host failures on `av` ([related-work-matrix.md §7.2](related-work-matrix.md#7-probe-evidence-2026-10-18)).

`MessageFlowSimulator` (I_dyn) is not used either, because it ignores RUNS_ON and cannot score a placement.

## 2. Why O1 counts as held out

The optimizer minimizes the proxy, and O1 is a different function in three ways:

1. **Gating.** The proxy uses all-of, O1 uses any-of.
2. **Magnitude.** The proxy counts components down. O1 scores partial, QoS-weighted impact.
3. **Stochasticity.** O1 propagates each edge with a probability.

A placement that is proxy-optimal can therefore be O1-mediocre. On the `atm_system` spec, ρ(proxy, O1) over the unique candidate placements was +0.24 ([docs/deployment.md §4](../../deployment.md)). Part of that gap is the gating mismatch, not weakness in the optimizer. The paper must say so, rather than present the low correlation as a pure finding about cascade-awareness.

## 3. Where the independence stops

- **One author, one graph model.** The proxy, O1 and O2 were written by the same team over the same structural graph, so they share the modelling assumptions:
  - crash-stop host failure;
  - no queueing at hosts;
  - routing through the first broker.
- **What Stage 1 may claim.** It may claim results "relative to two pre-specified cascade models", not results about real systems. That wording is fixed for the abstract and the threats section.
- **External independence** arrives only with the Stage 2 testbed ([tosem-extension.md](tosem-extension.md)), where placements are scored by message loss under real middleware.

## 4. O2: the input-gating switch (to be built)

**Specification.**
- Add `input_gating ∈ {"any", "all", "k_of_n"}` to `FailureSimulator.__init__`. The default is `"any"`.
- In `_cascade_topic_to_subscriber`, replace the `min()` over feeds:
  - `any`: min, which is today's behaviour;
  - `all`: max;
  - `k_of_n`: the k-th smallest, with k = ⌈n/2⌉.
- An optional per-Application raw attribute `input_gating` overrides the global setting.
- Thread the setting through `SimulationService` and `verify.score_placements`.

**Guards.**
- **Regression.** `input_gating="any"` must reproduce today's O1 outputs exactly, so no published JSS number moves. This is enforced by a new `tests/test_input_gating.py`.
- **Independence.** The switch reads only feed impacts already held in `state.impact`. It never touches DEPENDS_ON, and `tests/test_independence_guarantee.py` must keep passing.

**Circularity rule.** `all` gating reproduces the proxy's semantics, so an oracle run with it is close to circular. It may be reported only as a *proxy-aligned upper bound* and never as confirmatory evidence. The confirmatory oracles are O1 (`any`) and O2 (`k_of_n`).

## 5. Metrics read from each oracle

- **E[I]:** Σ_e p(e) · I(e), averaged over seeds. This is the primary metric.
- **max I:** the worst event, averaged over seeds. This is a secondary metric.
- **σ_seed:** the standard deviation over seeds of the per-seed E[I]. It is an input to the noise bar η.
