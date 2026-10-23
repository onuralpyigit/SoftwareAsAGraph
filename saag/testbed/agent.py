"""
Per-host testbed agent: ``python -m saag.testbed.agent --host H1 ...``.

Runs every application and broker relay the deployment plan puts on one host,
over Cyclone DDS (or the in-process transport, for a single-process run of a
whole scenario with ``--host all --transport local``). Telemetry goes to
``<telemetry>/<host>.jsonl``. Every ``CONTROL_POLL_S`` the agent re-reads
``<control>/<component>.json`` (written by `saag.testbed.chaos`) into that
component's `Control`, which is how application-level faults reach a process
inside a container.
"""

from __future__ import annotations

import argparse
import faulthandler
import json
import signal
import time
from pathlib import Path
from typing import Dict, List, Optional

from saag.simulation.calibration import make_event

from .runtime import AppRuntime, BrokerRelay, Control, LocalTransport, TelemetrySink

CONTROL_POLL_S = 0.1
#: How often agent-level DDS status counts are written to the trace.
STATUS_PERIOD_S = 1.0


def _emit_status(parts: Dict[str, object], sink: TelemetrySink) -> None:
    now = time.time()
    for part in parts.values():
        events = getattr(part, "status_events", None)
        if events is not None:
            for ev in events(now):
                sink.emit(ev)


def run_host(plan: Dict, host: str, transport, sink: TelemetrySink, duration_s: float,
             control_dir: Optional[Path] = None, seed: int = 0) -> None:
    hosts = plan["hosts"] if host == "all" else {host: plan["hosts"][host]}
    parts: Dict[str, object] = {}
    for h, residents in hosts.items():
        for b in residents["brokers"]:
            parts[b] = BrokerRelay(b, plan, transport, sink, seed=seed)
        for a in residents["apps"]:
            replicated = len(plan["apps"][a].get("hosts", [])) > 1
            parts[f"{a}@{h}" if replicated else a] = AppRuntime(
                a, plan, transport, sink, seed=seed, host=h)
    for p in parts.values():
        p.start()
    end = time.time() + duration_s
    stamps: Dict[str, tuple] = {}
    if getattr(transport, "status_callback", "absent") is None:
        # DDS transport: report the middleware's own status counts too.
        transport.status_callback = lambda kind, owner, topic, name, count: sink.emit(make_event(
            time.time(), "saag.status", owner.partition("@")[0], topic, status=name,
            count=count, entity=kind, source="cyclone", instance=owner))
    next_status = time.time() + STATUS_PERIOD_S
    try:
        while time.time() < end:
            if time.time() >= next_status:
                next_status += STATUS_PERIOD_S
                _emit_status(parts, sink)
            if control_dir is not None:
                for name, part in parts.items():
                    # Component-level control applies to every instance;
                    # instance-level ("A@H1", a host fault in local mode) on top.
                    files = [control_dir / f"{name.partition('@')[0]}.json", control_dir / f"{name}.json"]
                    stamp = tuple(f.stat().st_mtime if f.exists() else None for f in files)
                    if stamp != stamps.get(name, (None, None)):
                        stamps[name] = stamp
                        state: Dict = {}
                        for f in files:
                            if f.exists():
                                state.update(json.loads(f.read_text()))
                        part.control = Control.from_dict(state)
            time.sleep(CONTROL_POLL_S)
    finally:
        _emit_status(parts, sink)
        for p in parts.values():
            p.shutdown()
        transport.close()
        sink.close()


def main(argv: Optional[List[str]] = None) -> None:
    ap = argparse.ArgumentParser(prog="saag.testbed.agent")
    ap.add_argument("--deployment", required=True, type=Path)
    ap.add_argument("--host", required=True, help="host id from the plan, or 'all'")
    ap.add_argument("--telemetry", required=True, type=Path)
    ap.add_argument("--control", type=Path, default=None)
    ap.add_argument("--duration", type=float, required=True)
    ap.add_argument("--transport", choices=("dds", "local"), default="dds")
    ap.add_argument("--domain", type=int, default=0)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)
    # `docker kill -s USR1 <container>` dumps every thread's stack to the log.
    faulthandler.register(signal.SIGUSR1, all_threads=True)

    plan = json.loads(args.deployment.read_text())
    if args.transport == "dds":
        from .dds import DdsTransport
        transport = DdsTransport(args.domain)
    else:
        transport = LocalTransport()
    args.telemetry.mkdir(parents=True, exist_ok=True)
    sink = TelemetrySink(args.telemetry / f"{args.host}.jsonl")
    run_host(plan, args.host, transport, sink, args.duration, args.control, args.seed)


if __name__ == "__main__":
    main()
