"""
Runtime fault library and the controller that executes it inside a SimPy run.

`MessageFlowSimulator` historically injected one permanent crash-stop of one
component at ``fault_time``. Real outages start as gray failures, recover, fail
over and hit the deployment (brokers, hosts, links) rather than a single
process. `RuntimeController` owns all of that for one run:

* the fault schedule (`FaultSpec`), with recovery for bounded faults;
* the deployment in the data path (``deployment_model="stations"``): broker
  stations on ROUTES with failover after a detection lease, host CPU shared by
  co-located applications (RUNS_ON), and -- with ``network_gating`` --
  host/link reachability over CONNECTS_TO;
* hot-standby failover for components that declare ``hotstandby``;
* a binned delivery timeline from which recovery time is measured.

Nothing here runs unless the simulator is asked for it: with no faults list, no
deployment model, no hot standby and the declared workload, the controller is
not constructed and the engine takes its historical code path.

Only raw structural edges are read (ROUTES, RUNS_ON, CONNECTS_TO, PUBLISHES_TO,
SUBSCRIBES_TO).

`RuntimeFaultMode` is deliberately separate from `models.FailureMode`, which
belongs to `FailureSimulator` (the Validate-stage oracle); extending that enum
would change the oracle's semantics.
"""

from __future__ import annotations

import math
import random
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Set, Tuple

from saag.core.network import NETWORK_TYPES, Fabric, link_key, place

from .._stats import derive_seed


class RuntimeFaultMode(str, Enum):
    CRASH = "crash"                       # permanent crash-stop (the historical fault)
    TRANSIENT_CRASH = "transient_crash"   # crash, then restart after duration_s
    FREEZE = "freeze"                     # stops serving and emitting; queues keep filling
    SLOWDOWN = "slowdown"                 # service time x k (k fixed or log-normal)
    OMISSION = "omission"                 # drops each emission with probability p
    BABBLING = "babbling"                 # publishes at k x its rate
    BUFFER_LEAK = "buffer_leak"           # reader queue capacity shrinks by beta/s
    REDELIVERY_STORM = "redelivery_storm"  # re-sends its retained history `copies` times
    LINK_FLAP = "link_flap"               # Gilbert-Elliott good/bad (cut) link


#: Modes that end on their own and must declare a duration.
BOUNDED_MODES = frozenset({RuntimeFaultMode.TRANSIENT_CRASH, RuntimeFaultMode.FREEZE})

DEPLOYMENT_MODELS: Tuple[str, ...] = ("none", "stations")


@dataclass(frozen=True)
class FaultSpec:
    """One fault: what, how, when, for how long.

    ``target`` is a component id, or ``"a|b"`` for the CONNECTS_TO link between
    a and b (LINK_FLAP only). ``duration_s=None`` lasts until the end of the run.

    ``params`` by mode:

    =================  ===========================================================
    SLOWDOWN           ``k`` (default 4.0), or ``k_mu`` / ``k_sigma`` for
                       k ~ LogNormal(mu, sigma) drawn once per fault
    OMISSION           ``p`` (default 0.5)
    BABBLING           ``k`` (default 5.0)
    BUFFER_LEAK        ``beta`` slots lost per second (default 10.0)
    REDELIVERY_STORM   ``copies`` (default 1)
    LINK_FLAP          ``mean_good_s`` (default 2.0), ``mean_bad_s`` (default 0.5)
    =================  ===========================================================
    """

    target: str
    mode: RuntimeFaultMode = RuntimeFaultMode.CRASH
    start_s: float = 0.0
    duration_s: Optional[float] = None
    params: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "mode", RuntimeFaultMode(self.mode))
        if self.mode in BOUNDED_MODES and self.duration_s is None:
            raise ValueError(f"{self.mode.value} needs a duration_s")
        if self.duration_s is not None and self.duration_s <= 0:
            raise ValueError(f"duration_s must be positive, got {self.duration_s!r}")
        if self.start_s < 0:
            raise ValueError(f"start_s must be >= 0, got {self.start_s!r}")
        if self.mode == RuntimeFaultMode.LINK_FLAP and "|" not in self.target:
            raise ValueError("link_flap targets a link: 'a|b'")

    @property
    def end_s(self) -> Optional[float]:
        return None if self.duration_s is None else self.start_s + self.duration_s

    def to_dict(self) -> Dict[str, Any]:
        return {"target": self.target, "mode": self.mode.value, "start_s": self.start_s,
                "duration_s": self.duration_s, "params": dict(self.params)}

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "FaultSpec":
        return cls(target=d["target"], mode=d.get("mode", "crash"),
                   start_s=float(d.get("start_s", 0.0)),
                   duration_s=d.get("duration_s"), params=d.get("params", {}))


class RuntimeController:
    """Executes faults and deployment semantics for one `MessageFlowSimulator` run."""

    def __init__(
        self,
        env,
        sim,
        failed_nodes: Set[str],
        fanouts: Mapping[str, Any],
        stations: Mapping[str, Any],
        sub_queues: Mapping[Tuple[str, str], Any],
        pub_edges: List[Tuple[str, str, dict]],
        sub_edges: List[Tuple[str, str, dict]],
        faults: List[FaultSpec],
        primary_target: Optional[str],
    ) -> None:
        self.env = env
        self.sim = sim
        self.graph = sim.graph
        self.failed = failed_nodes
        self.fanouts = fanouts
        self.stations = stations
        self.sub_queues = sub_queues
        self.faults = list(faults)
        self.primary = primary_target
        self.deployment = sim.deployment_model == "stations"

        self.node_type = {n: d.get("type") for n, d in self.graph.nodes(data=True)}
        self.publishers_of: Dict[str, List[str]] = defaultdict(list)
        self.readers_of: Dict[str, List[str]] = defaultdict(list)
        self.publications: Dict[str, Set[str]] = defaultdict(set)
        for src, tgt, _ in pub_edges:
            self.publishers_of[tgt].append(src)
            self.publications[src].add(tgt)
        for src, tgt, _ in sub_edges:
            self.readers_of[tgt].append(src)

        # ── Deployment (RUNS_ON, ROUTES, CONNECTS_TO) ───────────────────────
        self.hosts_of: Dict[str, List[str]] = defaultdict(list)
        self.routers_of: Dict[str, List[str]] = defaultdict(list)
        links: List[Tuple[str, str, Optional[float]]] = []
        for src, tgt, data in self.graph.edges(data=True):
            etype = data.get("type")
            if etype == "RUNS_ON":
                self.hosts_of[src].append(tgt)
            elif etype == "ROUTES" and tgt in fanouts:
                self.routers_of[tgt].append(src)
            elif etype == "CONNECTS_TO":
                links.append((src, tgt, data.get("capacity_mbps")))
        for comp in self.hosts_of:
            self.hosts_of[comp].sort()
        for t in self.routers_of:
            self.routers_of[t].sort()
        self.residents: Dict[str, Set[str]] = defaultdict(set)
        for comp, hosts in self.hosts_of.items():
            for h in hosts:
                self.residents[h].add(comp)

        net_vertices = [n for n, t in self.node_type.items() if t in NETWORK_TYPES]
        # Opt-in, as everywhere else in the repo: the corpus's CONNECTS_TO is a
        # sparse host mesh, not a complete network, so treating it as the only
        # way between hosts would partition hosts it never meant to isolate.
        self.fabric = (Fabric(net_vertices, links)
                       if (self.deployment and sim.network_gating and links) else None)
        self.cut: Set[frozenset] = set()
        self._islands: Optional[Dict[str, int]] = None

        #: Current routing broker per brokered topic (None: no live broker).
        self.router_of: Dict[str, Optional[str]] = {
            t: (rs[0] if rs else None) for t, rs in self.routers_of.items()}
        self.broker_stations: Dict[str, Any] = {}
        if self.deployment:
            self._build_broker_stations()
            self._share_host_cpu()

        # ── Fault state ─────────────────────────────────────────────────────
        #: Why each component is down; it comes back when the set empties.
        self.down_causes: Dict[str, Set[str]] = defaultdict(set)
        self.frozen: Set[str] = set()
        self.rate_mult: Dict[str, float] = {}
        self.omission: Dict[str, float] = {}
        self._recovery_events: Dict[str, Any] = {}
        self._thaw_events: Dict[str, Any] = {}
        self.ever_failed: Set[str] = set()
        self.lost_at_broker = 0
        #: DDS status recorder, set by the simulator when ``dds_status`` is on.
        self.status: Any = None
        self.events: List[Dict[str, Any]] = []

        # ── Timeline (recovery measurement) ─────────────────────────────────
        self.bin_s = float(sim.timeline_bin_s)
        self.expected_bins: Dict[int, int] = defaultdict(int)
        self.delivered_bins: Dict[int, int] = defaultdict(int)

        self._rng = random.Random(derive_seed(sim.seed, "runtime-controller"))
        for i, spec in enumerate(self.faults):
            env.process(self._fault_process(i, spec))

    # ── Deployment construction ─────────────────────────────────────────────

    def _build_broker_stations(self) -> None:
        """One service station per broker, sized from the load it routes."""
        from ..message_flow_simulator import ServiceStation

        load: Dict[str, float] = defaultdict(float)
        for t, br in self.router_of.items():
            if br is not None:
                load[br] += self.sim.topic_aggregate_rate(t)
        for br in sorted({b for rs in self.routers_of.values() for b in rs}):
            declared = self.graph.nodes.get(br, {}).get("capacity_msgs_s")
            calibrated = False
            if declared:
                service = 1.0 / float(declared)
            elif self.sim._calibration_active and load.get(br, 0.0) > 0:
                service = self.sim.target_utilization / load[br]
                calibrated = True
            else:
                service = self.sim.default_processing_time_s
            self.broker_stations[br] = ServiceStation(
                env=self.env, subscriber_id=br, service_time_s=service,
                distribution=(self.sim.service_distribution
                              if self.sim._calibration_active else "uniform"),
                calibrated=calibrated, measure_from=self.sim.warmup_s,
            )

    def _share_host_cpu(self) -> None:
        """Co-located applications queue for one host CPU (RUNS_ON).

        Capacity is the host's declared ``cpu_cores``; absent that, one core
        per resident station, so the host is no busier at baseline than its
        residents were alone and contention appears only when a fault (a
        babbling neighbour, a slowdown, a failover) adds work.
        """
        import simpy

        by_host: Dict[str, List[str]] = defaultdict(list)
        for app in sorted(self.stations):
            hosts = self.hosts_of.get(app)
            if hosts:
                by_host[place(hosts)].append(app)
        self.host_cpu: Dict[str, Any] = {}
        for host, apps in sorted(by_host.items()):
            cores = self.graph.nodes.get(host, {}).get("cpu_cores")
            capacity = int(cores) if cores else len(apps)
            res = simpy.PriorityResource(self.env, capacity=max(1, capacity))
            self.host_cpu[host] = res
            for app in apps:
                self.stations[app].resource = res

    # ── Hooks called from the SimPy processes ───────────────────────────────

    def interval(self, app: str, base: float) -> float:
        """Inter-emission interval after any babbling multiplier."""
        k = self.rate_mult.get(app)
        return base / k if k else base

    def withhold(self, app: str, rng: random.Random) -> bool:
        """True when a frozen or omitting publisher skips this emission."""
        if app in self.frozen:
            return True
        p = self.omission.get(app)
        return bool(p) and rng.random() < p

    def is_frozen(self, app: str) -> bool:
        return app in self.frozen

    def thawed(self, app: str):
        return self._thaw_events[app]

    def recovered(self, app: str):
        """Event that fires when *app* comes back (never, for a permanent crash)."""
        if app not in self._recovery_events:
            self._recovery_events[app] = self.env.event()
        return self._recovery_events[app]

    def on_demand(self, topic: str, now: float) -> None:
        readers = sum(1 for r in self.readers_of.get(topic, ()) if r != self.primary)
        self.expected_bins[int(now // self.bin_s)] += readers

    def on_delivered(self, app: str, window_time: float) -> None:
        if app != self.primary:
            self.delivered_bins[int(window_time // self.bin_s)] += 1

    def publish(self, fanout, msg) -> None:
        """Route *msg* through its broker (if any) and fan it out."""
        gate = self._gate(msg.publisher_id) if self.fabric is not None else None
        if not self.deployment or not self.routers_of.get(msg.topic_id):
            fanout.publish(msg, self.failed, gate=gate)
            return
        broker = self.router_of.get(msg.topic_id)
        if broker is None or broker in self.failed:
            # Until the lease expires the writers keep sending to the dead
            # broker; after it, a topic with no live broker has nowhere to go.
            self.lost_at_broker += 1
            fanout.publish_lost(msg)
            return
        if gate is not None and not self._reachable(msg.publisher_id, broker):
            self.lost_at_broker += 1
            fanout.publish_lost(msg)
            return
        self.env.process(self._broker_forward(broker, fanout, msg))

    def _broker_forward(self, broker: str, fanout, msg):
        station = self.broker_stations[broker]
        with station.resource.request() as req:
            yield req
            service = station.sample_service_time(self._rng)
            if service > 0:
                station.record_service(service)
                yield self.env.timeout(service)
        if broker in self.failed:
            self.lost_at_broker += 1
            fanout.publish_lost(msg)
            return
        gate = (lambda sub: self._reachable(broker, sub)) if self.fabric is not None else None
        fanout.publish(msg, self.failed, gate=gate)

    # ── Reachability over CONNECTS_TO ───────────────────────────────────────

    def _host(self, comp: str) -> Optional[str]:
        if self.fabric is not None and comp in self.fabric.vertices:
            return comp
        hosts = [h for h in self.hosts_of.get(comp, ()) if self.fabric and h in self.fabric.vertices]
        return place(hosts, self.failed) if hosts else None

    def _reachable(self, a: str, b: str) -> bool:
        ha, hb = self._host(a), self._host(b)
        if ha is None or hb is None:
            return True   # not on the modelled network: no network constraint
        if self._islands is None:
            self._islands = self.fabric.islands(self.failed, self.cut)
        ia, ib = self._islands.get(ha), self._islands.get(hb)
        return ia is not None and ia == ib

    def _gate(self, publisher: str) -> Callable[[str], bool]:
        return lambda sub: self._reachable(publisher, sub)

    def _network_changed(self) -> None:
        self._islands = None

    # ── Kill / revive ───────────────────────────────────────────────────────

    def _kill(self, comp: str, cause: str) -> None:
        self.down_causes[comp].add(cause)
        if comp in self.failed:
            return
        self.failed.add(comp)
        self.ever_failed.add(comp)
        if self.status is not None:
            self.status.down(comp, self.env.now)
        self._recovery_events.setdefault(comp, self.env.event())
        self._log("down", comp, cause)
        if self.node_type.get(comp) in NETWORK_TYPES:
            self._network_changed()
            for r in sorted(self.residents.get(comp, ())):
                if self.sim.replica_aware and not all(
                        h in self.failed for h in self.hosts_of.get(r, ())):
                    continue
                self._kill(r, f"host:{comp}")
        if self.node_type.get(comp) == "Broker":
            self.env.process(self._reroute_after_lease(comp))
        if self.sim.hot_standby and self._has_standby(comp):
            self.env.process(self._failover(comp))

    def _revive(self, comp: str, cause: str) -> None:
        causes = self.down_causes.get(comp, set())
        causes.discard(cause)
        if causes or comp not in self.failed:
            return
        self.failed.discard(comp)
        self._log("up", comp, cause)
        if self.status is not None:
            self.status.up(comp, self.env.now)
        ev = self._recovery_events.pop(comp, None)
        if ev is not None and not ev.triggered:
            ev.succeed()
        if self.node_type.get(comp) in NETWORK_TYPES:
            self._network_changed()
            for r in sorted(self.residents.get(comp, ())):
                self._revive(r, f"host:{comp}")
        if self.node_type.get(comp) == "Broker":
            self._reroute_all()

    def _has_standby(self, comp: str) -> bool:
        flag = self.graph.nodes.get(comp, {}).get("hotstandby")
        return bool(flag) and str(flag).lower() not in ("false", "0", "no")

    def _failover(self, comp: str):
        """A hot standby takes over after the detection delay."""
        yield self.env.timeout(self.sim.failover_delay_s)
        if comp in self.failed:
            self._log("failover", comp, "hotstandby")
            self.down_causes[comp] = {"standby"}
            self._revive(comp, "standby")

    def _lease(self, topics: Iterable[str]) -> float:
        rates = [self.sim.topic_aggregate_rate(t) for t in topics]
        rate = max(rates) if rates else 0.0
        return max(1.0, 3.0 / rate) if rate > 0 else 1.0

    def _reroute_after_lease(self, broker: str):
        routed = [t for t, b in self.router_of.items() if b == broker]
        yield self.env.timeout(self._lease(routed))
        self._reroute_all()

    def _reroute_all(self) -> None:
        for t, rs in self.routers_of.items():
            live = [b for b in rs if b not in self.failed]
            new = live[0] if live else None
            if new != self.router_of.get(t):
                self._log("reroute", t, f"{self.router_of.get(t)}->{new}")
                self.router_of[t] = new

    def _log(self, what: str, comp: str, detail: str) -> None:
        self.events.append({"t": round(self.env.now, 6), "event": what,
                            "component": comp, "detail": detail})

    # ── Fault processes ─────────────────────────────────────────────────────

    def _targets(self, comp: str) -> List[str]:
        """A host's gray failure degrades every resident; otherwise just comp."""
        if self.node_type.get(comp) in NETWORK_TYPES:
            return sorted(self.residents.get(comp, ()))
        return [comp]

    def _fault_process(self, i: int, spec: FaultSpec):
        env = self.env
        yield env.timeout(max(0.0, spec.start_s - env.now))
        mode, cause = spec.mode, f"fault{i}"
        p = spec.params
        self._log("fault", spec.target, mode.value)

        if mode in (RuntimeFaultMode.CRASH, RuntimeFaultMode.TRANSIENT_CRASH):
            self._kill(spec.target, cause)
            if mode == RuntimeFaultMode.TRANSIENT_CRASH:
                yield env.timeout(spec.duration_s)
                self._revive(spec.target, cause)
                yield from self._storm_on_recovery(spec)
            return

        if mode == RuntimeFaultMode.FREEZE:
            targets = self._targets(spec.target)
            for t in targets:
                self.frozen.add(t)
                self._thaw_events[t] = env.event()
            yield env.timeout(spec.duration_s)
            for t in targets:
                self.frozen.discard(t)
                ev = self._thaw_events.pop(t)
                ev.succeed()
            return

        if mode == RuntimeFaultMode.SLOWDOWN:
            if "k_mu" in p:
                k = math.exp(self._rng.gauss(float(p["k_mu"]), float(p.get("k_sigma", 0.5))))
            else:
                k = float(p.get("k", 4.0))
            stations = [self.stations[t] for t in self._targets(spec.target) if t in self.stations]
            if spec.target in self.broker_stations:
                stations.append(self.broker_stations[spec.target])
            self._log("slowdown_k", spec.target, f"{k:.4f}")
            for st in stations:
                st.slowdown *= k
            yield from self._until_end(spec)
            for st in stations:
                st.slowdown /= k
            return

        if mode in (RuntimeFaultMode.OMISSION, RuntimeFaultMode.BABBLING):
            book = self.omission if mode == RuntimeFaultMode.OMISSION else self.rate_mult
            value = float(p.get("p", 0.5)) if mode == RuntimeFaultMode.OMISSION else float(p.get("k", 5.0))
            targets = self._targets(spec.target)
            for t in targets:
                book[t] = value
            yield from self._until_end(spec)
            for t in targets:
                book.pop(t, None)
            return

        if mode == RuntimeFaultMode.BUFFER_LEAK:
            beta = float(p.get("beta", 10.0))
            queues = [q for (tid, sid), q in self.sub_queues.items()
                      if sid in self._targets(spec.target)]
            end = spec.end_s
            while end is None or env.now < end:
                yield env.timeout(1.0 / beta)
                for q in queues:
                    q.capacity_override = max(1, q.capacity - 1)
            for q in queues:
                q.capacity_override = None
            return

        if mode == RuntimeFaultMode.REDELIVERY_STORM:
            self._storm(spec.target, int(p.get("copies", 1)))
            return

        if mode == RuntimeFaultMode.LINK_FLAP:
            a, b = spec.target.split("|", 1)
            key = link_key(a, b)
            good, bad = float(p.get("mean_good_s", 2.0)), float(p.get("mean_bad_s", 0.5))
            end = spec.end_s
            while end is None or env.now < end:
                yield env.timeout(self._rng.expovariate(1.0 / good))
                self.cut.add(key)
                self._network_changed()
                self._log("link_bad", spec.target, "")
                yield env.timeout(self._rng.expovariate(1.0 / bad))
                self.cut.discard(key)
                self._network_changed()
                self._log("link_good", spec.target, "")
            return

    def _until_end(self, spec: FaultSpec):
        """Wait out the fault; an unbounded one holds until the run ends."""
        if spec.duration_s is not None:
            yield self.env.timeout(spec.duration_s)
        else:
            yield self.env.event()   # never triggered

    def _storm_on_recovery(self, spec: FaultSpec):
        copies = int(spec.params.get("storm_copies", 0))
        if copies > 0:
            self._storm(spec.target, copies)
        return
        yield  # pragma: no cover - makes this a generator

    def _storm(self, comp: str, copies: int) -> None:
        """Flush retained history to every live reader `copies` times.

        The pub-sub form of a retry storm: on reconnect or restart, RELIABLE
        writers re-send what they hold. The samples keep their original creation
        time, so most miss their deadline -- but each one still costs service
        time at the reader, which is what makes the storm hurt.
        """
        from ..message_flow_simulator import Message

        topics = sorted(self.publications.get(comp, set())
                        | {t for t, b in self.router_of.items() if b == comp})
        for t in topics:
            fo = self.fanouts[t]
            for _ in range(max(0, copies)):
                for original in list(fo._history):
                    msg = Message(msg_id=original.msg_id, topic_id=t,
                                  publisher_id=original.publisher_id,
                                  created_at=original.created_at,
                                  payload_size_bytes=original.payload_size_bytes,
                                  window_at=self.env.now)
                    for sub_id, sq in fo._queues.items():
                        if sub_id in self.failed:
                            continue
                        fo.stats.replayed_total += 1
                        if sq._try_put(msg, fo.stats):
                            fo.stats.replayed_enqueued += 1
        self._log("storm", comp, f"copies={copies}")

    # ── Results ─────────────────────────────────────────────────────────────

    def time_to_recover(self, pre_until: float, after: float, tolerance: float = 0.95) -> Optional[float]:
        """Seconds after *after* until a timeline bin regains ``tolerance`` x the pre-fault rate."""
        def rate(b: int) -> Optional[float]:
            e = self.expected_bins.get(b, 0)
            return self.delivered_bins.get(b, 0) / e if e else None

        warm = int(self.sim.warmup_s // self.bin_s)
        pre = [r for b in range(warm, int(pre_until // self.bin_s))
               if (r := rate(b)) is not None]
        if not pre:
            return None
        target = tolerance * (sum(pre) / len(pre))
        first = int(math.ceil(after / self.bin_s))
        last = int(self.sim.duration // self.bin_s)
        for b in range(first, last):
            r = rate(b)
            if r is not None and r >= target:
                return round(b * self.bin_s - after, 6)
        return None

    def labeled_components(self) -> Set[str]:
        """Brokers and hosts this run can genuinely observe, beyond apps."""
        if not self.deployment:
            return set()
        brokers = {b for rs in self.routers_of.values() for b in rs}
        hosts = {h for h, rs in self.residents.items() if rs}
        return brokers | hosts
