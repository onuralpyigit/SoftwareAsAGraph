"""
Faults on the testbed: `FaultSpec` -> timed actions on containers and processes.

The same `FaultSpec`s that drive `MessageFlowSimulator` drive the testbed, so a
fidelity comparison injects the identical fault in both.

========================  =====================================================
fault                     action
========================  =====================================================
Node CRASH                ``docker kill`` the host's container
Node TRANSIENT_CRASH      ``docker kill``, then ``docker start`` (agents restart
                          their residents fresh; telemetry is appended)
Node FREEZE               ``docker pause`` / ``docker unpause``
Node SLOWDOWN             ``docker update --cpus`` to 1/k of a core, then back
Application / Broker      control file ``<control>/<id>.json`` read by the
CRASH, TRANSIENT_CRASH,   agent (crashed / frozen / slowdown / omission /
FREEZE, SLOWDOWN,         babbling), reset when the fault ends
OMISSION, BABBLING
LINK_FLAP a|b             ``tc netem loss 100%`` on a's interface during each
                          bad period, from the same Gilbert-Elliott schedule the
                          simulator draws (an approximation: it cuts all of a's
                          traffic, not only the a-b link)
========================  =====================================================

BUFFER_LEAK and REDELIVERY_STORM are not emulated; `plan_actions` reports them
in ``unsupported`` instead of silently skipping them.

With ``local=True`` (the whole scenario in one process over `LocalTransport`,
no containers) a host fault becomes the same control-file fault on every
component resident on that host, and LINK_FLAP is unsupported.
"""

from __future__ import annotations

import json
import random
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from saag.simulation._stats import derive_seed
from saag.simulation.runtime.faults import FaultSpec, RuntimeFaultMode

from .compose import container_name

M = RuntimeFaultMode


@dataclass
class Action:
    t: float                                   # seconds after run start
    kind: str                                  # "docker" | "control"
    argv: Optional[List[str]] = None
    target: Optional[str] = None
    control: Optional[Dict[str, Any]] = None


@dataclass
class ChaosPlan:
    actions: List[Action] = field(default_factory=list)
    unsupported: List[Dict[str, Any]] = field(default_factory=list)


def _control(t: float, target: str, **state) -> Action:
    return Action(t=t, kind="control", target=target, control=state)


def plan_actions(faults: List[FaultSpec], plan: Dict[str, Any], duration_s: float,
                 seed: int = 0, local: bool = False) -> ChaosPlan:
    hosts = set(plan["hosts"])
    out = ChaosPlan()
    if local:
        expanded = []
        for f in faults:
            if f.target in hosts:
                h = plan["hosts"][f.target]
                expanded += [FaultSpec(c, f.mode, f.start_s, f.duration_s, f.params)
                             for c in h["apps"] + h["brokers"]]
            elif f.mode == M.LINK_FLAP:
                out.unsupported.append({**f.to_dict(), "reason": "no network in local mode"})
            else:
                expanded.append(f)
        faults, hosts = expanded, set()
    for i, f in enumerate(faults):
        end = f.end_s if f.end_s is not None else None
        p = f.params
        if f.mode == M.LINK_FLAP:
            a, _ = f.target.split("|", 1)
            rng = random.Random(derive_seed(seed, f"chaos|{i}|{f.target}"))
            good, bad = float(p.get("mean_good_s", 2.0)), float(p.get("mean_bad_s", 0.5))
            t, stop = f.start_s, end if end is not None else duration_s
            c = container_name(a)
            while True:
                t += rng.expovariate(1.0 / good)
                if t >= stop:
                    break
                out.actions.append(Action(t, "docker", ["docker", "exec", c, "tc", "qdisc", "replace",
                                                        "dev", "eth0", "root", "netem", "loss", "100%"]))
                t += rng.expovariate(1.0 / bad)
                out.actions.append(Action(min(t, stop), "docker",
                                          ["docker", "exec", c, "tc", "qdisc", "del", "dev", "eth0", "root"]))
                if t >= stop:
                    break
            continue
        if f.target in hosts:
            c = container_name(f.target)
            if f.mode in (M.CRASH, M.TRANSIENT_CRASH):
                out.actions.append(Action(f.start_s, "docker", ["docker", "kill", c]))
                if f.mode == M.TRANSIENT_CRASH:
                    out.actions.append(Action(end, "docker", ["docker", "start", c]))
            elif f.mode == M.FREEZE:
                out.actions.append(Action(f.start_s, "docker", ["docker", "pause", c]))
                out.actions.append(Action(end, "docker", ["docker", "unpause", c]))
            elif f.mode == M.SLOWDOWN:
                k = float(p.get("k", 4.0))
                out.actions.append(Action(f.start_s, "docker", ["docker", "update", "--cpus", f"{1.0 / k:.3f}", c]))
                if end is not None:
                    out.actions.append(Action(end, "docker", ["docker", "update", "--cpus", "0", c]))
            else:
                out.unsupported.append({**f.to_dict(), "reason": "host-level mode not emulated"})
            continue
        state = {
            M.CRASH: {"crashed": True}, M.TRANSIENT_CRASH: {"crashed": True},
            M.FREEZE: {"frozen": True},
            M.SLOWDOWN: {"slowdown": float(p.get("k", 4.0))},
            M.OMISSION: {"omission": float(p.get("p", 0.5))},
            M.BABBLING: {"babbling": float(p.get("k", 5.0))},
        }.get(f.mode)
        if state is None:
            out.unsupported.append({**f.to_dict(), "reason": "not emulated on the testbed"})
            continue
        out.actions.append(_control(f.start_s, f.target, **state))
        if end is not None:
            out.actions.append(_control(end, f.target))
    out.actions.sort(key=lambda a: a.t)
    return out


def execute(plan: ChaosPlan, control_dir: Path, t0: float,
            runner=subprocess.run, sleep=time.sleep, clock=time.time) -> List[Dict[str, Any]]:
    """Apply the actions at their times (relative to *t0*); returns an action log."""
    log = []
    for a in plan.actions:
        delay = t0 + a.t - clock()
        if delay > 0:
            sleep(delay)
        if a.kind == "control":
            (Path(control_dir) / f"{a.target}.json").write_text(json.dumps(a.control))
            log.append({"t": clock() - t0, "control": a.target, "state": a.control})
        else:
            res = runner(a.argv, capture_output=True, text=True)
            log.append({"t": clock() - t0, "argv": a.argv, "rc": getattr(res, "returncode", None)})
    return log
