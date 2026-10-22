"""
Testbed workflow: build -> run (with chaos) -> collect -> calibrate -> fidelity.

``run`` has two modes. ``local`` runs the whole scenario in this process over
`LocalTransport` -- no containers, no DDS, application-level faults only -- and
is the quick path for checking the loop end to end. ``docker`` brings the
containers up with ``docker compose``, applies the chaos plan on schedule and
tears them down; it needs Docker and builds an image that downloads
``cyclonedds`` from PyPI.
"""

from __future__ import annotations

import dataclasses
import json
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from saag.core.graph_io import load_graph
from saag.simulation.calibration import TelemetryCalibrator, TelemetryTrace
from saag.simulation.runtime.faults import FaultSpec

from .chaos import execute, plan_actions
from .collect import load_run
from .compose import write_testbed
from .deployment import build_deployment



def build(scenario: Path, out_dir: Path, duration_s: float, seed: int = 0,
          target_utilization: float = 0.65) -> Path:
    plan = build_deployment(scenario, target_utilization=target_utilization)
    plan["source_scenario"] = str(Path(scenario))
    return write_testbed(plan, out_dir, duration_s, seed)


def _load(out_dir: Path) -> Dict[str, Any]:
    return json.loads((Path(out_dir) / "deployment.json").read_text())


def _reset_run_dir(out_dir: Path) -> None:
    for sub in ("telemetry", "control"):
        d = Path(out_dir) / sub
        d.mkdir(parents=True, exist_ok=True)
        for f in d.iterdir():
            if f.is_file():
                f.unlink()


def run(out_dir: Path, duration_s: float, faults: Optional[List[FaultSpec]] = None,
        mode: str = "local", seed: int = 0) -> Dict[str, Any]:
    from .agent import run_host
    from .runtime import LocalTransport, TelemetrySink

    out_dir = Path(out_dir)
    plan = _load(out_dir)
    _reset_run_dir(out_dir)
    chaos = plan_actions(faults or [], plan, duration_s, seed=seed, local=(mode == "local"))
    start = time.time()
    (out_dir / "run.json").write_text(json.dumps({
        "start_unix": start, "duration_s": duration_s, "mode": mode, "seed": seed,
        "faults": [f.to_dict() for f in faults or []], "unsupported": chaos.unsupported}))
    if mode == "local":
        sink = TelemetrySink(out_dir / "telemetry" / "all.jsonl")
        th = threading.Thread(target=run_host, args=(plan, "all", LocalTransport(), sink,
                                                     duration_s, out_dir / "control", seed))
        th.start()
        log = execute(chaos, out_dir / "control", start)
        th.join()
    elif mode == "docker":
        compose = ["docker", "compose", "-f", str(out_dir / "docker-compose.yml")]
        subprocess.run(compose + ["up", "--build", "-d"], check=True)
        try:
            log = execute(chaos, out_dir / "control", start)
            remaining = start + duration_s + 2.0 - time.time()
            if remaining > 0:
                time.sleep(remaining)
        finally:
            subprocess.run(compose + ["down"])
    else:
        raise ValueError(f"mode must be 'local' or 'docker', got {mode!r}")
    meta = json.loads((out_dir / "run.json").read_text())
    meta["chaos_log"] = log
    (out_dir / "run.json").write_text(json.dumps(meta, indent=2))
    return meta


def calibrate(out_dir: Path, warmup_s: float = 2.0) -> Dict[str, Any]:
    """Calibrate on the steady state: after warm-up, before the first fault.

    Rates and service times measured across a fault describe the faulted
    system, not the one the simulator should be parameterised with. Trigger
    policies are the exception -- they are identifiable only from input
    outages -- so they are inferred over the whole run.
    """
    out_dir = Path(out_dir)
    trace = load_run(out_dir)
    meta = json.loads((out_dir / "run.json").read_text())
    first = min((f["start_s"] for f in meta["faults"]), default=trace.end_s)
    steady = trace.window(trace.start_s, first)
    profile = TelemetryCalibrator(warmup_s=warmup_s, infer_policies=False).calibrate(
        steady, source=str(out_dir))
    whole = TelemetryCalibrator(warmup_s=warmup_s).calibrate(trace, source=str(out_dir))
    profile.trigger_policy, profile.trigger_evidence = whole.trigger_policy, whole.trigger_evidence
    profile.provenance["policy_window_s"] = [trace.start_s, trace.end_s]
    d = dataclasses.asdict(profile)
    d["edge_failure_correlation"] = {"|".join(k): v for k, v in profile.edge_failure_correlation.items()}
    (Path(out_dir) / "profile.json").write_text(json.dumps(d, indent=2, sort_keys=True))
    return d


def profile_from_json(d: Dict[str, Any]):
    from saag.simulation.models import RuntimeTelemetryProfile

    d = dict(d)
    d["edge_failure_correlation"] = {tuple(k.split("|", 1)): v
                                     for k, v in d.get("edge_failure_correlation", {}).items()}
    return RuntimeTelemetryProfile(**d)


def simulate_like(out_dir: Path, settings: Optional[Dict[str, Any]] = None,
                  calibrated: bool = False) -> TelemetryTrace:
    """Run `MessageFlowSimulator` on the same scenario, faults and duration as the testbed run."""
    from saag.simulation.message_flow_simulator import MessageFlowSimulator

    out_dir = Path(out_dir)
    plan, meta = _load(out_dir), json.loads((out_dir / "run.json").read_text())
    g = load_graph(Path(plan["source_scenario"]))
    profile = None
    if calibrated:
        profile = profile_from_json(json.loads((out_dir / "profile.json").read_text()))
    faults = [FaultSpec.from_dict(f) for f in meta["faults"]] or None
    # replica_aware: the testbed runs an instance of a multi-hosted component on
    # every host, so a host outage moves it rather than killing it.
    kw = {"behavior_model": "reactive", "deployment_model": "stations",
          "rng_streams": "per_process", "replica_aware": True,
          "target_utilization": plan["target_utilization"],
          "staleness_periods": plan["staleness_periods"], **(settings or {})}
    res = MessageFlowSimulator(g, duration=meta["duration_s"], seed=meta["seed"], faults=faults,
                               record_events=True, telemetry_profile=profile, **kw).run()
    return TelemetryTrace.from_records(res.events, start_s=0.0, end_s=meta["duration_s"])


def fidelity(out_dir: Path, calibrated: bool = False, warmup_s: float = 2.0) -> Dict[str, Any]:
    from saag.validation.fidelity import compare_fault_response, compare_steady_state, stalled_in_trace

    out_dir = Path(out_dir)
    meta = json.loads((out_dir / "run.json").read_text())
    observed = load_run(out_dir)
    simulated = simulate_like(out_dir, calibrated=calibrated)
    first = min((f["start_s"] for f in meta["faults"]), default=meta["duration_s"])
    report = {
        "run": {k: meta[k] for k in ("duration_s", "mode", "seed", "faults", "unsupported")},
        "calibrated": calibrated,
        "steady_state": compare_steady_state(simulated.window(warmup_s, first),
                                             observed.window(warmup_s, first)),
    }
    if meta["faults"]:
        targets = {f["target"] for f in meta["faults"]}
        sim_st = stalled_in_trace(simulated, first) - targets
        obs_st = stalled_in_trace(observed, first) - targets
        report["fault_response"] = compare_fault_response({}, {}, sim_st, obs_st)
    name = "fidelity_calibrated.json" if calibrated else "fidelity.json"
    (out_dir / name).write_text(json.dumps(report, indent=2, sort_keys=True, default=str))
    return report
