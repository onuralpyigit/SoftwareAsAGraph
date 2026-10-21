# Placement Study: Deployment-Spec Priors

**Companion to** [PREREGISTRATION.md](PREREGISTRATION.md).

The topologies carry no resource or reliability data. Every Node is `{id, name, type, spec}` only, and no file in `data/` holds a failure probability or an MTTR. Each instance's deployment spec ([docs/deployment.md §2](../../deployment.md#2-deployment-spec)) is therefore *generated* from the priors below.

The priors are a construct-validity risk, so they are frozen before the first study run. Every value marked **PROVISIONAL** must be replaced by a cited value, or kept with a stated justification, at freeze time. Changing any of them after the first run requires an amendment.

---

## 1. Primary cell (H1 is tested here only)

| Prior | Primary value | Notes |
|---|---|---|
| Hosts | The topology's own Nodes, all used (`use_all_hosts: true`) | Fixes the host count, so every arm is compared at the same count (RQ1 confound) |
| Host capacity | Homogeneous. cpu and mem are each sized so that total demand / total capacity = **u = 0.7** | Total demand includes replicas, brokers and topic history buffers |
| `max_utilization` | 1.0 | Headroom comes from u |
| App cpu demand | LogNormal(μ = 0, σ = 0.5) cores, i.e. median 1 core; drawn independently of topology. **PROVISIONAL** | Decoupled on purpose, so the prior does not quietly favour `indeg_spread` |
| App mem demand | LogNormal(μ = ln 512, σ = 0.5) MB, drawn independently. **PROVISIONAL** | The spec adds `history_depth × size` per subscribed topic. That part *is* topology-derived (§3) |
| Broker demand | cpu 2.0, mem 2048 MB (the spec default) | Brokers are placed by the optimizer, not pinned |
| Failure domains | A random partition of hosts into domains of **2**, seeded per spec draw | A Node left over forms a singleton domain, which adds no extra event |
| Host p | 0.02 for every host. **PROVISIONAL** | E[I] is linear in p. With homogeneous hosts, only the ratio r = p_domain / p_host changes rankings |
| Domain p | r · p_host with **r = 0.25**. **PROVISIONAL** | Example specs use 0.004–0.005 against 0.02–0.03 |
| Replicas | Spec default: 2 for `hotstandby` components, else 1 | |
| Pins | Generated scenarios: none. Hand-authored models: components bound to hardware are pinned to their authored host. The list is fixed in `priors.yaml` at freeze. Candidates on autoware: `velodyne_node_container`, `camera_driver_node`, `gnss_poser_node`, `imu_corrector_node` and `radar_tracks_msgs_converter` on ECU-Sensing-FPGA; `can_bus_interface_node` on ECU-Vehicle-Actuation; `lidar_centerpoint_node` (GPU) on ECU-Perception-GPU | A sensor driver cannot move away from the interface it reads, so treating it as free would overstate the placement freedom |
| Anti-affinity | `domain` | Standard practice, and the reference point for RQ1 and RQ2 |
| Latency | No corpus topology has network devices, so: intra-domain 0.5 ms, inter-domain 5.0 ms, same host 0 ms. **PROVISIONAL** | Deadlines come from each topic's `deadline_ms` and are enforced failover-safe |
| Draws | **D = 5** spec draws per scenario. Draw seeds `1000 + d`, d = 0…4 | |

## 2. Sweep (RQ4, exploratory only)

Each factor is varied one at a time from the primary cell, with D = 5 draws per level.

| Factor | Levels | Why it matters |
|---|---|---|
| Utilization target u | 0.5, **0.7**, 0.85 | Tight capacity forces consolidation and removes placement freedom |
| r = p_domain / p_host | 0.1, **0.25**, 1.0 | Decides whether domain spread or host spread dominates E[I] |
| Domain size | **2**, 4 | Larger domains make anti-affinity more expensive |
| Replica budget | none (all `replicas: 1`), **default** | With no replicas, anti-affinity has nothing to act on |
| Demand prior | **independent**, rate-derived (cpu ∝ Σ topic frequency × size published and received) | The rate-derived prior is correlated with degree by construction (§3) |
| Host heterogeneity | **homogeneous**, two classes (capacity 0.5× and 1.5×, assigned alternately) | Heterogeneous hosts make some placements infeasible |

Bold marks the primary level.

## 3. Correlation disclosure (required in the paper)

For every scenario and draw, report Spearman ρ between in-degree (dependents in the derived graph) and:
- per-component cpu demand;
- per-component mem demand, including history buffers.

In the primary cell, cpu demand is independent by construction, so ρ is expected to be ≈ 0. Memory carries a topology-derived term, so it may not be. If the median |ρ| for mem exceeds 0.3, the paper must say that the capacity constraint itself favours spreading high-in-degree components.

## 4. Generator (to be built)

`saag/deployment/spec_gen.py` will read `data/deployment/priors.yaml`, a machine-readable copy of §1–§2, and emit one spec per (scenario, cell, draw). Generated specs go to `output/placement_study/specs/`, never to `data/scenarios/` (that corpus must regenerate byte-identically). The generator records its input hash in each spec's `description`, so the study runner can fingerprint and resume.
