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
"""

from __future__ import annotations

import re
import threading
from typing import Any, Callable, Dict, Mapping

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
    return spec


class DdsTransport:
    """One DomainParticipant; one Topic per name; partitions via Publisher/Subscriber QoS."""

    def __init__(self, domain_id: int = 0) -> None:
        from dataclasses import dataclass

        from cyclonedds.domain import DomainParticipant
        from cyclonedds.idl import IdlStruct
        from cyclonedds.idl.types import float64, int64

        @dataclass
        class Sample(IdlStruct, typename="saag::Sample"):
            msg_id: int64
            publisher: str
            created: float64
            size: int64

        self.Sample = Sample
        self.dp = DomainParticipant(domain_id)
        self._topics: Dict[str, Any] = {}
        self._entities = []
        self._lock = threading.Lock()

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
        return Qos(*policies)

    def _topic(self, name: str):
        from cyclonedds.topic import Topic

        with self._lock:
            if name not in self._topics:
                self._topics[name] = Topic(self.dp, dds_name(name), self.Sample)
            return self._topics[name]

    def writer(self, topic: str, partition: str, qos: Mapping[str, Any]) -> Callable[[Dict[str, Any]], None]:
        from cyclonedds.core import Policy, Qos
        from cyclonedds.pub import DataWriter, Publisher

        pub = Publisher(self.dp, qos=Qos(Policy.Partition([partition])))
        w = DataWriter(pub, self._topic(topic), qos=self._qos(qos))
        self._entities += [pub, w]
        Sample = self.Sample

        def send(sample: Dict[str, Any]) -> None:
            w.write(Sample(msg_id=int(sample["msg_id"]), publisher=str(sample["publisher"]),
                           created=float(sample["created"]), size=int(sample.get("size", 0))))
        return send

    def subscribe(self, topic: str, partition: str, qos: Mapping[str, Any],
                  callback: Callable[[Dict[str, Any]], None]) -> None:
        from cyclonedds.core import Listener, Policy, Qos
        from cyclonedds.sub import DataReader, Subscriber

        class _L(Listener):
            def on_data_available(self, reader):
                for s in reader.take(N=256):
                    callback({"msg_id": s.msg_id, "publisher": s.publisher,
                              "created": s.created, "size": s.size})

        sub = Subscriber(self.dp, qos=Qos(Policy.Partition([partition])))
        r = DataReader(sub, self._topic(topic), qos=self._qos(qos), listener=_L())
        self._entities += [sub, r]

    def close(self) -> None:
        self._entities.clear()
        self._topics.clear()
