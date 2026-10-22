"""
Stub applications and broker relays for the testbed, independent of transport.

`AppRuntime` behaves like one application of the simulator's reactive model,
in real time: one publisher thread per output topic, one worker thread that
serves every input (the shared station), bounded per-topic receive queues with
the corpus overflow policy, deadline checks on end-to-end latency, and the
trigger policy deciding whether an emission goes out. `BrokerRelay` forwards a
broker's routed topics from the ingress to the egress partition, with
lowest-id-live-broker failover on heartbeats.

Both obey a `Control` record (crash / freeze / slowdown / omission / babbling),
which the agent refreshes from a control file so `saag.testbed.chaos` can drive
application-level faults inside a container.

Telemetry goes through a `TelemetrySink` in the trace format of
`saag.simulation.calibration`.
"""

from __future__ import annotations

import json
import random
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Protocol, Tuple

from saag.core.models import QoSPolicy
from saag.simulation._stats import derive_seed
from saag.simulation.calibration import make_event

INGRESS, EGRESS, DIRECT = "ingress", "egress", "direct"
HEARTBEAT_TOPIC = "saag_broker_heartbeat"
HEARTBEAT_PERIOD_S = 0.2
BROKER_LEASE_S = 1.0
DEFAULT_QUEUE = 100


class Transport(Protocol):
    def writer(self, topic: str, partition: str, qos: Mapping[str, Any]) -> Callable[[Dict[str, Any]], None]: ...
    def subscribe(self, topic: str, partition: str, qos: Mapping[str, Any],
                  callback: Callable[[Dict[str, Any]], None]) -> None: ...
    def close(self) -> None: ...


class LocalTransport:
    """In-process transport: synchronous fan-out to every matching subscriber."""

    def __init__(self) -> None:
        self._subs: Dict[Tuple[str, str], List[Callable]] = {}
        self._lock = threading.Lock()

    def writer(self, topic, partition, qos):
        key = (topic, partition)

        def send(sample):
            with self._lock:
                callbacks = list(self._subs.get(key, ()))
            for cb in callbacks:
                cb(dict(sample))
        return send

    def subscribe(self, topic, partition, qos, callback):
        with self._lock:
            self._subs.setdefault((topic, partition), []).append(callback)

    def close(self):
        pass


class TelemetrySink:
    """Thread-safe JSON-lines writer (or in-memory list when *path* is None)."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path else None
        self.records: List[Dict[str, Any]] = []
        self._lock = threading.Lock()
        self._fh = open(self.path, "a") if self.path else None

    def emit(self, record: Dict[str, Any]) -> None:
        with self._lock:
            if self._fh:
                self._fh.write(json.dumps(record) + "\n")
            else:
                self.records.append(record)

    def close(self) -> None:
        with self._lock:
            if self._fh:
                self._fh.close()
                self._fh = None


@dataclass
class Control:
    crashed: bool = False
    frozen: bool = False
    slowdown: float = 1.0
    omission: float = 0.0
    babbling: float = 1.0

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "Control":
        return cls(**{k: d[k] for k in ("crashed", "frozen", "slowdown", "omission", "babbling") if k in d})


def topic_partitions(topic: Mapping[str, Any]) -> Tuple[str, str]:
    """(writer partition, reader partition) of a topic: brokered or direct."""
    return (INGRESS, EGRESS) if topic.get("routers") else (DIRECT, DIRECT)


class _Station:
    """Bounded per-topic queues served by one worker, highest priority first."""

    def __init__(self, topics: Mapping[str, Mapping[str, Any]]) -> None:
        self.cond = threading.Condition()
        self.queues: Dict[str, deque] = {t: deque() for t in topics}
        self.capacity = {t: int(spec.get("history_depth") or DEFAULT_QUEUE) for t, spec in topics.items()}
        self.reliable = {t: str(spec.get("reliability", "RELIABLE")).upper() != "BEST_EFFORT"
                         for t, spec in topics.items()}
        self.order = sorted(topics, key=lambda t: (
            -QoSPolicy.PRIORITY_SCORES.get(str(topics[t].get("priority", "MEDIUM")).upper(), 0.33), t))

    def put(self, topic: str, item) -> Optional[Any]:
        """Enqueue; returns the dropped item on overflow (or the new one, BEST_EFFORT)."""
        with self.cond:
            q = self.queues[topic]
            dropped = None
            if len(q) >= self.capacity[topic]:
                if not self.reliable[topic]:
                    return item
                dropped = q.popleft()
            q.append(item)
            self.cond.notify()
            return dropped

    def get(self, stop: threading.Event, timeout: float = 0.1):
        with self.cond:
            while not stop.is_set():
                for t in self.order:
                    if self.queues[t]:
                        return t, self.queues[t].popleft()
                self.cond.wait(timeout)
        return None


class AppRuntime:
    """One stub application (see module docstring)."""

    def __init__(self, app: str, plan: Mapping[str, Any], transport: Transport,
                 sink: TelemetrySink, seed: int = 0, clock: Callable[[], float] = time.time) -> None:
        self.app = app
        self.spec = plan["apps"][app]
        self.topics = plan["topics"]
        self.transport = transport
        self.sink = sink
        self.clock = clock
        self.control = Control()
        self.stop = threading.Event()
        self.rng = random.Random(derive_seed(seed, f"testbed|{app}"))
        self._rng_lock = threading.Lock()
        self.last_fresh: Dict[str, float] = {}
        self._threads: List[threading.Thread] = []
        self._msg_id = 0
        self._id_lock = threading.Lock()
        ins = {t: self.topics[t] for t in self.spec["subscribes"]}
        self.station = _Station(ins)
        self._writers = {
            t: transport.writer(t, topic_partitions(self.topics[t])[0], self.topics[t])
            for t in self.spec["publishes"]
        }
        for t in self.spec["subscribes"]:
            transport.subscribe(t, topic_partitions(self.topics[t])[1], self.topics[t],
                                lambda s, t=t: self._on_sample(t, s))

    # ── Policy ──────────────────────────────────────────────────────────────

    def emit_share(self, now: float) -> float:
        inputs = self.spec["inputs"]
        policy = str(self.spec["policy"])
        if not inputs or policy == "periodic":
            return 1.0
        fresh = sum(1 for t in inputs
                    if now - self.last_fresh.get(t, self._t0) <= self.spec["staleness_s"][t])
        if policy == "all_of":
            return 1.0 if fresh == len(inputs) else 0.0
        if policy == "any_of":
            return 1.0 if fresh else 0.0
        k = int(policy.split(":", 1)[1]) if ":" in policy else 1
        return min(1.0, fresh / min(k, len(inputs)))

    # ── Threads ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._t0 = self.clock()
        for t in self.spec["publishes"]:
            th = threading.Thread(target=self._publisher, args=(t,), daemon=True, name=f"{self.app}:{t}")
            self._threads.append(th)
        if self.spec["subscribes"]:
            self._threads.append(threading.Thread(target=self._worker, daemon=True, name=f"{self.app}:worker"))
        for th in self._threads:
            th.start()

    def shutdown(self) -> None:
        self.stop.set()
        for th in self._threads:
            th.join(timeout=2.0)

    def _rand(self) -> float:
        with self._rng_lock:
            return self.rng.random()

    def _publisher(self, topic: str) -> None:
        rate = float(self.spec["per_publisher_rate"][topic])
        if rate <= 0:
            return
        n = max(1, len(self.topics[topic]["publishers"]))
        nxt = self._t0 + (self.spec["phase_index"][topic] / n) / rate
        credit = 0.0
        while not self.stop.is_set():
            delay = nxt - self.clock()
            if delay > 0 and self.stop.wait(delay):
                return
            now = self.clock()
            nxt += 1.0 / (rate * max(self.control.babbling, 1e-9))
            c = self.control
            if c.crashed or c.frozen:
                continue
            if c.omission and self._rand() < c.omission:
                continue
            share = self.emit_share(now)
            if share < 1.0:
                credit += share
                if credit < 1.0:
                    continue
                credit -= 1.0
            with self._id_lock:
                self._msg_id += 1
                mid = self._msg_id
            sample = {"msg_id": mid, "publisher": self.app, "created": now,
                      "size": int(self.topics[topic].get("size", 64))}
            self._writers[topic](sample)
            self.sink.emit(make_event(now, "saag.publish", self.app, topic,
                                      **{"messaging.message.id": mid}))

    def _on_sample(self, topic: str, sample: Dict[str, Any]) -> None:
        if self.control.crashed:
            return
        dropped = self.station.put(topic, sample)
        if dropped is not None:
            self.sink.emit(make_event(self.clock(), "saag.drop", self.app, topic,
                                      publisher=dropped.get("publisher")))

    def _worker(self) -> None:
        while not self.stop.is_set():
            got = self.station.get(self.stop)
            if got is None:
                return
            topic, sample = got
            while self.control.frozen and not self.stop.is_set():
                time.sleep(0.01)
            if self.control.crashed:
                continue
            start = self.clock()
            mean = float(self.spec["service_mean_s"]) * self.control.slowdown
            if mean > 0:
                with self._rng_lock:
                    service = self.rng.expovariate(1.0 / mean)
                time.sleep(service)
            done = self.clock()
            latency_ms = (done - float(sample["created"])) * 1000.0
            deadline = self.topics[topic].get("deadline_ms")
            if deadline is not None and latency_ms > float(deadline):
                self.sink.emit(make_event(done, "saag.deadline_miss", self.app, topic,
                                          publisher=sample.get("publisher"), latency_ms=latency_ms))
                continue
            self.last_fresh[topic] = done
            self.sink.emit(make_event(
                done, "saag.deliver", self.app, topic, publisher=sample.get("publisher"),
                created_unix_s=sample["created"], latency_ms=latency_ms,
                service_ms=(done - start) * 1000.0,
                **{"messaging.message.id": sample.get("msg_id")}))


class BrokerRelay:
    """Forward a broker's routed topics ingress -> egress, with failover."""

    def __init__(self, broker: str, plan: Mapping[str, Any], transport: Transport,
                 sink: TelemetrySink, seed: int = 0, clock: Callable[[], float] = time.time) -> None:
        self.broker = broker
        self.plan = plan
        self.spec = plan["brokers"][broker]
        self.transport = transport
        self.sink = sink
        self.clock = clock
        self.control = Control()
        self.stop = threading.Event()
        self.rng = random.Random(derive_seed(seed, f"testbed|{broker}"))
        self.heard: Dict[str, float] = {}
        self._threads: List[threading.Thread] = []
        topics = plan["topics"]
        self.station = _Station({t: topics[t] for t in self.spec["routes"]})
        self._out = {t: transport.writer(t, EGRESS, topics[t]) for t in self.spec["routes"]}
        for t in self.spec["routes"]:
            transport.subscribe(t, INGRESS, topics[t], lambda s, t=t: self._on_sample(t, s))
        self._hb = transport.writer(HEARTBEAT_TOPIC, DIRECT, {"reliability": "BEST_EFFORT"})
        transport.subscribe(HEARTBEAT_TOPIC, DIRECT, {"reliability": "BEST_EFFORT"}, self._on_heartbeat)

    def active_for(self, topic: str, now: float) -> bool:
        """Lowest-id router whose heartbeat is within the lease routes the topic."""
        for r in self.plan["topics"][topic]["routers"]:
            if r == self.broker:
                return True
            if now - self.heard.get(r, self._t0) <= BROKER_LEASE_S:
                return False
        return False

    def start(self) -> None:
        self._t0 = self.clock()
        self._threads = [threading.Thread(target=self._worker, daemon=True),
                         threading.Thread(target=self._heartbeat, daemon=True)]
        for th in self._threads:
            th.start()

    def shutdown(self) -> None:
        self.stop.set()
        for th in self._threads:
            th.join(timeout=2.0)

    def _on_heartbeat(self, sample: Dict[str, Any]) -> None:
        self.heard[sample["publisher"]] = self.clock()

    def _heartbeat(self) -> None:
        while not self.stop.wait(HEARTBEAT_PERIOD_S):
            if not self.control.crashed and not self.control.frozen:
                self._hb({"msg_id": 0, "publisher": self.broker, "created": self.clock(), "size": 0})

    def _on_sample(self, topic: str, sample: Dict[str, Any]) -> None:
        if self.control.crashed or not self.active_for(topic, self.clock()):
            return
        dropped = self.station.put(topic, sample)
        if dropped is not None:
            self.sink.emit(make_event(self.clock(), "saag.drop", self.broker, topic,
                                      publisher=dropped.get("publisher")))

    def _worker(self) -> None:
        while not self.stop.is_set():
            got = self.station.get(self.stop)
            if got is None:
                return
            topic, sample = got
            while self.control.frozen and not self.stop.is_set():
                time.sleep(0.01)
            if self.control.crashed:
                continue
            mean = float(self.spec["service_mean_s"]) * self.control.slowdown
            if mean > 0:
                time.sleep(self.rng.expovariate(1.0 / mean))
            self._out[topic](sample)
