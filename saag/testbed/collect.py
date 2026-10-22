"""Load a testbed run's telemetry as one `TelemetryTrace` on run-relative time."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from saag.simulation.calibration import TelemetryTrace


def load_run(run_dir: Path, start_unix: Optional[float] = None) -> TelemetryTrace:
    """Merge ``telemetry/*.jsonl``; times become seconds since the run started.

    The start comes from ``run.json`` (written when the run is launched) unless
    given; failing both, the first event is t = 0.
    """
    run_dir = Path(run_dir)
    files = sorted((run_dir / "telemetry").glob("*.jsonl"))
    duration = None
    if (run_dir / "run.json").exists():
        meta = json.loads((run_dir / "run.json").read_text())
        duration = meta.get("duration_s")
        if start_unix is None:
            start_unix = meta.get("start_unix")
    trace = TelemetryTrace.load(files)
    origin = start_unix if start_unix is not None else trace.start_s
    for e in trace.events:
        e["t"] -= origin
    # The run's own bounds, not the first/last event: silence at the end of a
    # run is exactly what a stalled cascade looks like.
    trace.start_s = 0.0 if start_unix is not None else trace.start_s - origin
    trace.end_s = float(duration) if duration is not None else trace.end_s - origin
    return trace
