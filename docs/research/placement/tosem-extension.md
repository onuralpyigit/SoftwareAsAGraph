# Stage 2: TOSEM Extension with a Fault-Injection Testbed

**Builds on** the ICSA study ([PREREGISTRATION.md](PREREGISTRATION.md), [draft.md](draft.md)).

**What Stage 2 removes.** The ICSA paper's two largest threats are:
- **internal oracles:** the proxy and both oracles come from one team and one model ([oracles.md §3](oracles.md#3-where-the-independence-stops));
- **synthetic priors:** no model carries measured resource data ([priors.md](priors.md)).

Stage 2 replaces both with measurement on running publish–subscribe middleware.

**Journal policy.** Check TOSEM's current policy on extended conference papers before planning the text. A substantial share of new material and a statement of the delta are typically required. The delta table in §6 is written to satisfy that.

---

## 1. New research questions

- **RQ5, oracle fidelity.** Does O1 rank placements the way the testbed does? The measure is Spearman ρ over placements, per system, between simulated and measured expected impact.
- **RQ6, H1 on the testbed.** Does the RQ2 verdict (superior, equivalent or inconclusive) hold when impact is measured rather than simulated? This uses the systems that run on the testbed. Their number is small, so this is a replication with effect sizes and CIs, not a new significance test.
- **RQ7, gray failures.** Does any ranking of placements change under failures the simulator cannot express? These are slowdown, packet loss and CPU throttling.
- **RQ8, model fidelity.** For EdgeX, which can be deployed for real, how far does the hand-authored model's dependency structure differ from what the running system actually exchanges?

## 2. Testbed architecture

### 2.1 Hosts and failure domains

- **Hosts.** Every SaG Node becomes a separate VM, with dedicated, pinned vCPUs and its own memory. Containers on one shared kernel are not used for the confirmatory runs, because emulated hosts would then share fate through the scheduler and the kernel. Kind or k3d is acceptable for development only.
- **Failure domains.** Each domain is a group of VMs behind its own virtual bridge.
  - A domain outage powers off the group, or detaches the bridge.
  - The two variants are reported separately: crash-stop vs partition.
- **Orchestration.** k3s across the VMs. A placement becomes per-replica Deployments with `nodeName`/`nodeSelector` pinning, generated from `saag-deploy --placed-topology`.

### 2.2 Component stand-ins (the generated workload)

Real code exists for only one of the systems (EdgeX, §2.4). The rest run as **stand-ins generated from the topology JSON**:

- **Behaviour.**
  - Each Application becomes one process that subscribes to its declared topics.
  - It publishes each declared topic at the topic's `frequency`, with a payload of the topic's `size`.
  - Libraries are folded into the applications that use them, as in the model.
- **Input gating.** A stand-in publishes only while its gating rule (`any`, `all` or `k_of_n`, the same rules as in [oracles.md §4](oracles.md#4-o2-the-input-gating-switch-to-be-built)) is satisfied by fresh inputs.
  - An input is fresh if a message arrived within its topic's `deadline_ms`, or within three periods when no deadline is declared.
  - Source applications (no inputs) always publish.
- **Replicas.** Hot-standby replicas both publish, with a logical publisher id and a sequence number. Subscribers de-duplicate on (logical id, seq). This matches `replica_aware` semantics: a component survives while any copy is up. Any residual difference, such as failover gaps, is measured and reported, not hidden.
- **Middleware variants.**
  - **MQTT (Mosquitto):** each SaG Broker becomes a Mosquitto container. This variant runs for all systems, because its broker semantics match the model's ROUTES edges directly.
  - **ROS 2 / DDS:** rclpy or rclcpp with Cyclone DDS, for Autoware and the generated AV scenario. The model's DDS router and discovery brokers become a Zenoh router or a DDS discovery server.

**What stand-ins cannot show.** Stand-ins validate the *communication and failure structure*, not real component behaviour or real resource demand. The paper states this, and §2.4 is the counterweight.

### 2.3 Fault campaigns

| Fault | Mechanism | In the simulator? |
|---|---|---|
| Host crash | VM power-off | Yes (O1 host event) |
| Domain crash | Power-off of the VM group | Yes (O1 domain event) |
| Domain partition | Bridge detach / iptables drop | Partly (`network_gating`) |
| Slowdown | tc netem delay on the host's interface | No (RQ7) |
| Omission | tc netem loss | No (RQ7) |
| CPU starvation | cgroup cpu.max throttle on the VM's services | No (RQ7) |

Each fault is injected after a warm-up. Its effect is measured over a fixed window, the fault is cleared, and the system is reset by redeploying. Repetitions: R = 10 per (placement, event). The final R is fixed in a Stage 2 preregistration after a pilot measures its variance.

### 2.4 EdgeX: a deployed system

EdgeX Foundry ships container images and compose files, and it has a real message bus (Redis Streams or MQTT). It is deployed for real, with virtual device services generating readings. It is placed across the VMs according to each arm's placement and subjected to the same fault campaigns.

EdgeX adds two things the stand-ins cannot:
1. **Measured resource footprints** (CPU and memory per service at steady state), which replace the priors for EdgeX.
2. **A model-fidelity check (RQ8).** The topics and subscriptions actually observed on the bus are compared with the hand-authored `realworld_edgex` model, reporting missing and extra edges and the resulting change in the derived dependency graph.

## 3. Measurement

For each subscriber stand-in and each message, log the receipt time, the publisher's logical id and the sequence number.

- **App down:** not publishing because its gating rule fails, or its host is down.
- **Testbed impact of an event:** the share of Applications down, averaged over the window. The QoS-weighted analogue of O1's composite uses the same `w(topic)`.
- **Deadline-miss rate** per topic: the fraction of inter-arrival gaps that exceed `deadline_ms`.
- **Mission-thread completion:** for declared end-to-end chains (for example sensing → perception → planning → actuation on Autoware), the fraction of the window in which the sink receives data that is fresh along the whole chain.
  - Chains are declared before any run.
  - This label is not a function of in-degree, so it also answers the circularity concern raised in the JSS work.
- **Expected testbed impact:** Σ_e p(e) · impact(e), using the same p(e) as the spec.

## 4. Calibration protocol (RQ5)

**Per system:**
- Placements scored: all eight arms, plus 10 random feasible placements.
- **Primary statistic:** Spearman ρ between O1's E[impact] and the testbed's E[impact] over those placements, with a bootstrap CI.
- **Secondary statistics:** the same for O2, and for the proxy's blast_exp. The proxy's ρ is the reference a useful oracle must exceed.
- **Agreement on the RQ2 verdict:** the sign and beyond-noise status of CASCADE vs `indeg_spread`, compared between simulator and testbed.

**Freezing.** All of this is written into a Stage 2 preregistration before the first fault is injected.

## 5. Build list (Stage 2, under `testbed/`)

1. `testbed/standin/`: the stand-in generator (topology JSON → container images and configs), MQTT and ROS 2 variants, gating, replica de-duplication.
2. `testbed/infra/`: VM provisioning, with pinned vCPUs, per-domain bridges and a k3s join.
3. `testbed/deploy.py`: placement → k3s manifests, from `--placed-topology`.
4. `testbed/faults.py`: the fault campaigns in §2.3, with fault timing logged.
5. `testbed/collect.py`: receipt logs → per-event impact, deadline misses and mission-thread completion.
6. `testbed/edgex/`: compose-to-k3s mapping, footprint measurement, bus capture for RQ8.
7. `reproduce/testbed_calibration.py`: the RQ5 and RQ6 analysis.
8. A Stage 2 preregistration, written in this directory as an amendment chapter.

## 6. Delta over the ICSA paper

| | ICSA (Stage 1) | TOSEM (Stage 2) |
|---|---|---|
| Oracle | O1 and O2, both internal | + measured impact on real middleware (RQ5, RQ6) |
| Failure modes | Crash-stop host and domain | + partition, slowdown, omission, CPU starvation (RQ7) |
| Resource data | Pre-registered priors | + measured EdgeX footprints |
| Model validity | Hand-authored models assumed | + model-vs-deployment comparison for EdgeX (RQ8) |
| Labels | Component-impact composite | + mission-thread completion |
| Systems | 17 models | Same, plus a deployed EdgeX |
