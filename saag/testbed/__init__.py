"""
Emulated DDS testbed: run a scenario as a real pub-sub system and record it.

The simulators score themselves against oracles the framework computes; this
package produces *observed* behaviour to compare them with (see
`saag.validation.fidelity`). A scenario JSON becomes a runnable deployment:

``deployment``  scenario -> deployment plan: hosts and their residents, topics
                with their DDS QoS, each application's trigger policy, inputs,
                staleness windows and service time, brokers' routes.
``runtime``     transport-agnostic stub applications (`AppRuntime`) and broker
                relays (`BrokerRelay`), plus an in-process `LocalTransport`
                used by the tests.
``dds``         the Cyclone DDS transport and the corpus-QoS -> DDS-QoS mapping.
``agent``       the per-host process run inside each container.
``compose``     writes Dockerfile + docker-compose.yml + deployment.json.
``chaos``       turns `FaultSpec`s into timed docker / control-file actions.
``collect``     loads the agents' JSON-lines telemetry as a `TelemetryTrace`.

DDS has no broker, so a Broker is emulated as a relay process: writers publish
to an ingress partition, the routing broker republishes to the egress
partition readers use. That keeps ROUTES semantics -- and makes a broker
outage real -- on a brokerless middleware.

The stub applications run the same trigger policies as the simulator's
reactive model (`saag.simulation.runtime.behavior`), so a fidelity gap is a gap
in timing, queueing and transport, not in the behavioural model.

This package depends on `saag.simulation`, never the other way round, and its
optional dependency (``cyclonedds``, extra ``testbed``) is imported only by the
DDS transport.
"""
