"""
Runtime-behaviour extensions to `MessageFlowSimulator`.

Everything here is opt-in. `MessageFlowSimulator` imports these modules only
when a non-default ``behavior_model`` / ``deployment_model`` / ``faults`` /
``workload_model`` is requested, and its defaults reproduce the published
I_dyn(v) bit for bit (``tests/test_simulation_golden_defaults.py``).

``behavior``   when an application is willing to publish: input-driven
               trigger policies and the HEALTHY / DEGRADED / STALLED state
               that turn a single fault into a multi-hop cascade.
``faults``     the runtime fault library (gray failures, transient crashes,
               storms, link flapping).
``workload``   arrival-process models beyond the declared periodic rates.
``uncertainty`` parameter sampling for pre-deployment runs, where every
               runtime parameter is a guess.
``labels``     the runtime label sweep and its order-k references.

Like the rest of `saag/simulation/`, these modules read only raw structural
edges (PUBLISHES_TO, SUBSCRIBES_TO, ROUTES, RUNS_ON, CONNECTS_TO), never
derived DEPENDS_ON.
"""
