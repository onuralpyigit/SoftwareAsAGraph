"""
Cyclone DDS transport for the testbed (optional extra ``testbed``).

`qos_spec` is the corpus -> DDS QoS mapping as plain data, so it is testable
without the middleware installed; `DdsTransport` builds real Cyclone DDS
entities from it. ``cyclonedds`` is imported only when a transport is
constructed.

=====================  =======================================================
corpus field           DDS policy
=====================  =======================================================
reliability            Reliability (RELIABLE, max_blocking_time 100 ms) /
                       BestEffort
durability             Durability (Volatile / TransientLocal / Transient /
                       Persistent)
history_depth          History KeepLast(depth) (KeepLast(100) when absent)
deadline_ms            Deadline (only when declared)
transport_priority     TransportPriority (LOW 0, MEDIUM 1, HIGH 2, CRITICAL 3)
partition              Partition (ingress / egress / direct, see runtime)
=====================  =======================================================

Samples are taken by one Python poller thread blocked on a WaitSet, never by
Cyclone listener callbacks. A listener runs Python on Cyclone's receive thread
while that thread holds the reader's lock and waits for the GIL; a Python thread
holding the GIL and writing to a topic with a local reader then needs the same
lock. With every component writing and reading the heartbeat topic in one
participant, that deadlock froze whole hosts on the first Docker runs.

The same poller reads the DDS standard communication statuses of every data
reader and writer that has an owner (`STATUS_GETTERS`) about once a second and
reports changed counts to ``status_callback(kind, owner, topic, status, count)``
-- polling the getters, again never status listeners. Writers and readers get
``Liveliness(AUTOMATIC, lease)`` when the topic carries ``liveliness_lease_s``,
so a killed or paused host shows up as not-alive writers at its readers.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from typing import Any, Callable, Dict, Mapping, Optional

logger = logging.getLogger(__name__)

#: status name (saag.simulation.runtime.dds_status.STATUSES) -> (entity kind,
#: Cyclone getter, field of the returned status struct).
STATUS_GETTERS = (
    ("requested_deadline_missed", "reader", "get_requested_deadline_missed_status", "total_count"),
    ("sample_lost", "reader", "get_sample_lost_status", "total_count"),
    ("sample_rejected", "reader", "get_sample_rejected_status", "total_count"),
    ("liveliness_alive_count", "reader", "get_liveliness_changed_status", "alive_count"),
    ("liveliness_not_alive_count", "reader", "get_liveliness_changed_status", "not_alive_count"),
    ("requested_incompatible_qos", "reader", "get_requested_incompatible_qos_status", "total_count"),
    ("offered_deadline_missed", "writer", "get_offered_deadline_missed_status", "total_count"),
)
STATUS_PERIOD_S = 1.0

PRIORITY_VALUE = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3, "URGENT": 3, "HIGHEST": 3}


def dds_name(topic: str) -> str:
    """Topic ids as DDS topic names: alphanumerics and underscores only."""
    return "saag_" + re.sub(r"[^A-Za-z0-9_]", "_", topic)


def qos_spec(topic: Mapping[str, Any]) -> Dict[str, Any]:
    reliable = str(topic.get("reliability", "RELIABLE")).upper() != "BEST_EFFORT"
    spec: Dict[str, Any] = {
        "reliability": "RELIABLE" if reliable else "BEST_EFFORT",
        "durability": str(topic.get("durability", "VOLATILE")).upper(),
        "history_keep_last": int(topic.get("history_depth") or 100),
        "transport_priority": PRIORITY_VALUE.get(str(topic.get("priority", "MEDIUM")).upper(), 1),
    }
    if topic.get("deadline_ms") is not None:
        spec["deadline_ms"] = float(topic["deadline_ms"])
    if topic.get("liveliness_lease_s") is not None:
        spec["liveliness_lease_s"] = float(topic["liveliness_lease_s"])
    return spec


class DdsTransport:
    """One DomainParticipant; one Topic per name; partitions via Publisher/Subscriber QoS."""

    def __init__(self, domain_id: int = 0) -> None:
        from cyclonedds.domain import DomainParticipant

        from ._dds_types import Sample

        from cyclonedds.core import WaitSet

        self.Sample = Sample
        self.dp = DomainParticipant(domain_id)
        self._topics: Dict[str, Any] = {}
        self._entities = []
        self._lock = threading.Lock()
        self._readers: list = []
        #: (kind, owner, topic, entity) for status polling.
        self._status_handles: list = []
        self._last_status: Dict[Any, int] = {}
        self.status_callback: Optional[Callable[[str, str, str, str, int], None]] = None
        self._waitset = WaitSet(self.dp)
        self._stop = threading.Event()
        self._poller = threading.Thread(target=self._poll, daemon=True, name="dds-poller")
        self._poller.start()

    def _qos(self, topic: Mapping[str, Any]):
        from cyclonedds.core import Policy, Qos
        from cyclonedds.util import duration

        spec = qos_spec(topic)
        policies = [
            Policy.Reliability.Reliable(duration(milliseconds=100))
            if spec["reliability"] == "RELIABLE" else Policy.Reliability.BestEffort,
            {"VOLATILE": Policy.Durability.Volatile,
             "TRANSIENT_LOCAL": Policy.Durability.TransientLocal,
             "TRANSIENT": Policy.Durability.Transient,
             "PERSISTENT": Policy.Durability.Persistent}.get(spec["durability"], Policy.Durability.Volatile),
            Policy.History.KeepLast(spec["history_keep_last"]),
            Policy.TransportPriority(spec["transport_priority"]),
        ]
        if "deadline_ms" in spec:
            policies.append(Policy.Deadline(duration(milliseconds=spec["deadline_ms"])))
        if "liveliness_lease_s" in spec:
            policies.append(Policy.Liveliness.Automatic(
                duration(milliseconds=1000.0 * spec["liveliness_lease_s"])))
        return Qos(*policies)

    def _topic(self, name: str):
        from cyclonedds.topic import Topic

        with self._lock:
            if name not in self._topics:
                self._topics[name] = Topic(self.dp, dds_name(name), self.Sample)
            return self._topics[name]

    def writer(self, topic: str, partition: str, qos: Mapping[str, Any],
               owner: Optional[str] = None) -> Callable[[Dict[str, Any]], None]:
        from cyclonedds.core import Policy, Qos
        from cyclonedds.pub import DataWriter, Publisher

        pub = Publisher(self.dp, qos=Qos(Policy.Partition([partition])))
        w = DataWriter(pub, self._topic(topic), qos=self._qos(qos))
        with self._lock:
            self._entities += [pub, w]
            if owner is not None:
                self._status_handles.append(("writer", owner, topic, w))
        Sample = self.Sample

        def send(sample: Dict[str, Any]) -> None:
            w.write(Sample(msg_id=int(sample["msg_id"]), publisher=str(sample["publisher"]),
                           created=float(sample["created"]), size=int(sample.get("size", 0))))
        return send

    def subscribe(self, topic: str, partition: str, qos: Mapping[str, Any],
                  callback: Callable[[Dict[str, Any]], None], owner: Optional[str] = None) -> None:
        from cyclonedds.core import InstanceState, Policy, Qos, ReadCondition, SampleState, ViewState
        from cyclonedds.sub import DataReader, Subscriber

        sub = Subscriber(self.dp, qos=Qos(Policy.Partition([partition])))
        r = DataReader(sub, self._topic(topic), qos=self._qos(qos))
        cond = ReadCondition(r, SampleState.Any | ViewState.Any | InstanceState.Any)
        with self._lock:
            self._readers.append((r, cond, callback))
            self._entities += [sub, r, cond]
            if owner is not None:
                self._status_handles.append(("reader", owner, topic, r))
        self._waitset.attach(cond)

    def _poll(self) -> None:
        from cyclonedds.sub import InvalidSample
        from cyclonedds.util import duration

        next_status = time.time() + STATUS_PERIOD_S
        while not self._stop.is_set():
            self._waitset.wait(duration(milliseconds=50))
            if time.time() >= next_status:
                next_status += STATUS_PERIOD_S
                self.poll_status()
            with self._lock:
                readers = list(self._readers)
            for r, cond, cb in readers:
                for s in r.take(N=256, condition=cond):
                    # Instance-state notifications (a remote writer went away --
                    # a host crash, or a peer shutting down) arrive as
                    # InvalidSample with no data fields.
                    if isinstance(s, InvalidSample):
                        continue
                    try:
                        cb({"msg_id": s.msg_id, "publisher": s.publisher,
                            "created": s.created, "size": s.size})
                    except Exception:  # one bad callback must not deafen the host
                        logger.exception("testbed callback failed")

    def poll_status(self) -> None:
        """Report every owned entity's status counts that changed since the last poll."""
        cb = self.status_callback
        if cb is None:
            return
        with self._lock:
            handles = list(self._status_handles)
        for kind, owner, topic, entity in handles:
            for name, k, getter, fld in STATUS_GETTERS:
                if k != kind:
                    continue
                try:
                    value = int(getattr(getattr(entity, getter)(), fld))
                except Exception:  # an entity torn down mid-poll
                    continue
                key = (kind, owner, topic, name)
                if self._last_status.get(key) != value:
                    self._last_status[key] = value
                    cb(kind, owner, topic, name, value)

    def close(self) -> None:
        self._stop.set()
        self._poller.join(timeout=2.0)
        self.poll_status()   # final counts
        with self._lock:
            self._readers.clear()
        self._entities.clear()
        self._topics.clear()
