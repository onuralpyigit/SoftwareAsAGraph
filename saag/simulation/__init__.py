"""
Simulation Package
==================

Two independent engine stacks live here. They both emit something called
"impact" and the two quantities are **not** interchangeable — each owns one
pipeline stage, and mixing them inside a stage is a contract violation
enforced by `tests/test_groundtruth_contract.py`.

Predict stage — supervised labels
    `FaultInjector` is the canonical labeler. It produces `impact_scores.json`
    → I*(v), a scalar per node, over multiple seeds with per-node variance:
    the properties a training label needs. Runs on a raw `networkx.DiGraph`
    and reports through `FaultInjectionResult`.

Validate stage — quality oracle
    `FailureSimulator` is the canonical oracle. It produces `ImpactMetrics`,
    the RM decomposition (reliability, hierarchical over fault-tolerance and
    availability sub-characteristics, / maintainability) that the
    `saag/validation/` gates are written against. Runs on a `SimulationGraph`
    and is orchestrated by `SimulationService`.

Supporting engines
    `EventSimulator`            discrete-event pub-sub run; supplies the
                                baseline flows the flow-disruption term needs.
    `MessageFlowSimulator`      SimPy continuous-time run with DDS QoS
                                enforcement; timing and delivery analysis.
    `ChangePropagationSimulator`    IM(v) sub-metrics, on the transposed
                                    DEPENDS_ON graph.
    `TrafficSimulator`          closed-form bandwidth calculator (talks to
                                Neo4j directly; see docs/failure-simulation.md
                                §12 for the caveat).

Runtime engine (opt-in)
    `runtime/` extends `MessageFlowSimulator` with reactive trigger policies,
    the deployment on the data path, a fault library, bursty workloads and the
    runtime label sweep; `calibration` maps observed telemetry onto simulator
    parameters. Defaults reproduce the published engine bit for bit
    (tests/test_simulation_golden_defaults.py).

Simulation never reads derived `DEPENDS_ON` edges from the analysis graph —
it works on raw structural edges. Enforced by
`tests/test_independence_guarantee.py`.
"""

from .service import SimulationService
from .graph import SimulationGraph

# Validate-stage oracle
from .failure_simulator import FailureSimulator
from .event_simulator import EventSimulator
from .change_propagation import ChangePropagationSimulator, ChangePropagationResult

# Predict-stage labeler
from .fault_injector import FaultInjector
from .simulation_results import (
    FaultInjectionResult,
    FaultInjectionRecord,
    MessageFlowResult,
    TopicFlowStats,
    SubscriberFlowStats,
    FaultEventRecord,
    CascadeWave,
    LatencySignal,
    TrafficSignal,
    ErrorSignal,
    SaturationSignal,
    GoldenSignalsReport,
)



def __getattr__(name: str):
    """
    Resolve `MessageFlowSimulator` on first use.

    It is the only engine here that needs SimPy, which is not a declared
    dependency — importing it eagerly would make the whole package unimportable
    wherever SimPy is absent, instead of failing only for message-flow runs.
    """
    if name == "MessageFlowSimulator":
        from .message_flow_simulator import MessageFlowSimulator
        return MessageFlowSimulator
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


from .models import (
    EventScenario,
    EventResult,
    FailureScenario,
    FailureResult,
    RuntimeMetrics,
    ImpactMetrics,
    MonteCarloResult,
    CascadeEvent,
    ComponentState,
    FailureMode,
    CascadeRule,
    EventType,
    RuntimeTelemetryProfile,
)

__all__ = [
    # Orchestration
    "SimulationService",
    "SimulationGraph",
    # Engines — Validate stage
    "FailureSimulator",
    "EventSimulator",
    "ChangePropagationSimulator",
    # Engines — Predict stage
    "FaultInjector",
    "MessageFlowSimulator",
    # Results — Validate stage
    "EventScenario",
    "EventResult",
    "FailureScenario",
    "FailureResult",
    "RuntimeMetrics",
    "ImpactMetrics",
    "MonteCarloResult",
    "CascadeEvent",
    "ChangePropagationResult",
    # Results — Predict stage
    "FaultInjectionResult",
    "FaultInjectionRecord",
    "MessageFlowResult",
    "TopicFlowStats",
    "SubscriberFlowStats",
    "FaultEventRecord",
    "CascadeWave",
    "LatencySignal",
    "TrafficSignal",
    "ErrorSignal",
    "SaturationSignal",
    "GoldenSignalsReport",
    # Enums / config
    "ComponentState",
    "FailureMode",
    "CascadeRule",
    "EventType",
    "RuntimeTelemetryProfile",
]
