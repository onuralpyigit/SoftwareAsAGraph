#!/usr/bin/env python3
"""
simulate_graph.py
─────────────────
CLI entry point for the SaG simulation pipeline.

Two simulation modes, each accessible as a subcommand:

  fault-inject   Systematic BFS cascade fault injection.
                 Produces ``impact_scores.json`` – the I(v) ground-truth
                 vector used to validate Q(v) predictions via Spearman ρ.

  message-flow   Discrete-event pub-sub message flow simulation (SimPy).
                 Models publishers, broker queues, subscribers, QoS
                 enforcement, and optional runtime fault injection.

  combined       Run both modes in sequence.

EXAMPLES
────────

  # Ground-truth generation for the ATM dataset (all Application + Broker nodes)
  python cli/simulate_graph.py fault-inject \
      --input data/atm_system.json \\
      --output output/simulation/ \\
      --seeds 42,123,456,789,2024 \\
      --export-json

  # Single-node fault injection for ConflictDetector only
  python cli/simulate_graph.py fault-inject \
      --input data/atm_system.json \\
      --nodes ConflictDetector \\
      --output output/simulation/ \\
      --export-json

  # Message-flow simulation, 300 s, fault ConflictDetector at t=150 s
  python cli/simulate_graph.py message-flow \
      --input data/atm_system.json \\
      --duration 300 \\
      --fault-node ConflictDetector \\
      --fault-time 150 \\
      --seed 42 \\
      --output output/simulation/ \\
      --export-json

  # Run both in one pass
  python cli/simulate_graph.py combined \
      --input data/atm_system.json \\
      --output output/simulation/ \\
      --seeds 42,123,456 \\
      --duration 200 \\
      --fault-node ASTERIX_Broker \\
      --export-json

OUTPUT FILES
────────────
  fault-inject  →  output/impact_scores.json
                   output/impact_scores_summary.txt
  message-flow  →  output/message_flow_results.json
                   output/message_flow_summary.txt
  combined      →  all of the above
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

# Add project root to sys.path to support direct execution (python cli/simulate_graph.py)
if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import time
from typing import Any, Dict, List, Optional

from cli.common.arguments import setup_logging
from cli.loso_evaluate import _project_topic_qos_onto_edges
from saag.simulation.fault_injector import FaultInjector
# Choice tuples only -- this module guards its own `import simpy` in a
# try/except, so importing it costs nothing where SimPy is absent. The engine
# itself is still constructed lazily inside _run_message_flow.
from saag.simulation.message_flow_simulator import (
    QOS_MODES,
    REPLAY_DEADLINE_POLICIES,
    SERVICE_DISTRIBUTIONS,
    UTILIZATION_MODES,
)

logger = logging.getLogger("simulate_graph")


# ─────────────────────────────────────────────────────────────────────────────
# Graph loader (mirrors analyze_graph.py's approach)
# ─────────────────────────────────────────────────────────────────────────────

# Moved to saag/core/graph_io.py; see the note there. Re-exported under the
# original private name so reproduce/ and this module keep working.
from saag.core.graph_io import load_graph as _load_graph  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# fault-inject subcommand
# ─────────────────────────────────────────────────────────────────────────────

def _run_fault_inject(args: argparse.Namespace) -> None:

    input_path = Path(args.input)
    
    is_direct_file = args.output.endswith(".json")
    if is_direct_file:
        out_json = Path(args.output)
        output_dir = out_json.parent
    else:
        output_dir = Path(args.output)
        out_json = output_dir / "impact_scores.json"
        
    output_dir.mkdir(parents=True, exist_ok=True)

    if not input_path.exists():
        logger.error("Input file not found: %s", input_path)
        sys.exit(1)

    seeds = _parse_seeds(args.seeds)
    node_types = [t.strip() for t in args.node_types.split(",")]
    node_ids: Optional[List[str]] = (
        [n.strip() for n in args.nodes.split(",")]
        if args.nodes
        else None
    )

    logger.info("═" * 60)
    logger.info("FAULT INJECTION")
    logger.info("  Input      : %s", input_path)
    logger.info("  Output     : %s", args.output)
    logger.info("  Node types : %s", node_types)
    logger.info("  Node IDs   : %s", node_ids or "all")
    logger.info("  Seeds      : %s", seeds)
    logger.info("  Cascade lim: %s", args.cascade_depth or "unlimited")
    logger.info("  Propagation: %s", args.propagation_threshold)
    logger.info("  QoS factor : %s", getattr(args, "qos_factor", "ladder"))
    logger.info("═" * 60)

    t0 = time.perf_counter()
    g = _load_graph(input_path)

    injector = FaultInjector(
        graph=g,
        seeds=seeds,
        cascade_depth_limit=args.cascade_depth,
        propagation_threshold=args.propagation_threshold,
        qos_factor_mode=getattr(args, "qos_factor", "ladder"),
        network_gating=getattr(args, "network_gating", False),
    )
    result = injector.run(node_types=node_types, node_ids=node_ids)
    elapsed = time.perf_counter() - t0

    # ── Print summary ────────────────────────────────────────────────────
    _print_fault_inject_summary(result, elapsed)

    # ── Export ────────────────────────────────────────────────────────────
    if args.export_json or is_direct_file:
        result.save(out_json)
        logger.info("Impact scores written → %s", out_json)

    _write_fault_inject_text_summary(result, elapsed, output_dir)


def _run_edge_criticality(args: argparse.Namespace) -> None:
    """Measure the cost of severing each candidate relationship.

    Produces the edge labels the GNN's edge head is trained against. Before
    this command existed, ``networkx_to_hetero_data`` synthesised them from
    ``I*(source) x {1.0 if bridge else 0.1}`` — a structural heuristic standing
    in for a measurement — so the relationship-criticality results were
    validated against a hand-chosen multiplier rather than against a severed
    edge. ``FailureSimulator.simulate_edge_removal`` actually removes the edge,
    with both endpoints left alive, and recomputes the composite.
    """
    from saag.infrastructure.memory_repo import MemoryRepository
    from saag.simulation.failure_simulator import FailureSimulator
    from saag.simulation.graph import SimulationGraph
    from saag.simulation.service import SimulationService

    input_path = Path(args.input)
    if not input_path.exists():
        logger.error("Input file not found: %s", input_path)
        sys.exit(1)

    is_direct_file = args.output.endswith(".json")
    if is_direct_file:
        out_json = Path(args.output)
        output_dir = out_json.parent
    else:
        output_dir = Path(args.output)
        out_json = output_dir / "edge_criticality.json"
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info("=" * 60)
    logger.info("EDGE CRITICALITY (relationship removal sweep)")
    logger.info("  Input  : %s", input_path)
    logger.info("  Output : %s", out_json)
    logger.info("  Layer  : %s", args.layer_scope)
    logger.info("  Top-q  : %s", args.top_q)
    logger.info("=" * 60)

    t0 = time.perf_counter()
    repo = MemoryRepository()
    repo.save_graph(json.loads(input_path.read_text()), clear=True)
    graph = SimulationGraph(repo.get_graph_data(include_raw=True))
    sim = FailureSimulator(graph, network_gating=args.network_gating)
    # Flow disruption carries 15% of the composite and reads 0.0 until the
    # discrete-event baseline exists. Primed through the service so this path
    # and the shipped API path cannot drift apart.
    SimulationService._prime_baseline_flows(graph, sim)

    edges = sim.simulate_edge_removal_sweep(layer=args.layer_scope, top_q=args.top_q)
    elapsed = time.perf_counter() - t0

    payload = {
        "labeler": "FailureSimulator.simulate_edge_removal",
        "layer": args.layer_scope,
        "top_q": args.top_q,
        "elapsed_s": round(elapsed, 4),
        "n_evaluated": sum(1 for e in edges if e.evaluated),
        "n_nonzero": sum(1 for e in edges if e.combined_impact > 1e-6),
        "edge_criticality": [e.to_dict() for e in edges],
    }
    out_json.write_text(json.dumps(payload, indent=2))

    print()
    print("=" * 70)
    print("EDGE CRITICALITY SUMMARY")
    print("=" * 70)
    print(f"  Candidates evaluated : {payload['n_evaluated']}")
    print(f"  Non-zero impact      : {payload['n_nonzero']}")
    print(f"  Elapsed              : {elapsed:.2f}s")
    for e in edges[:10]:
        print(f"    {e.source:>18s} -> {e.target:<18s} "
              f"{e.relationship:<14s} {e.combined_impact:.4f}  [{e.level}]")
    print("=" * 70)
    logger.info("Edge criticality written -> %s", out_json)


def _print_fault_inject_summary(result, elapsed: float) -> None:
    print()
    print("=" * 70)
    print("FAULT INJECTION SUMMARY")
    print("=" * 70)
    print(f"  Nodes injected          : {result.total_nodes_injected}")
    print(f"  Total subscribers       : {result.total_subscribers}")
    print(f"  Seeds used              : {result.seeds_used}")
    print(f"  Elapsed                 : {elapsed:.2f}s")
    print()
    print(f"  {'Rank':<5}  {'Node ID':<30}  {'Type':<14}  {'I(v)':>7}  "
          f"{'Depth':>5}  {'Orphaned':>8}  {'Impacted':>8}")
    print(f"  {'─'*5}  {'─'*30}  {'─'*14}  {'─'*7}  {'─'*5}  {'─'*8}  {'─'*8}")
    for row in result.top_k_by_impact[:20]:
        print(f"  {row['rank']:<5}  {row['node_id']:<30}  "
              f"{row['node_type']:<14}  {row['impact_score']:>7.4f}  "
              f"{row['cascade_depth']:>5}  {row['orphaned_topics']:>8}  "
              f"{row['impacted_subscribers']:>8}")
    print("=" * 70)
    print()


def _write_fault_inject_text_summary(result, elapsed: float, output_dir: Path) -> None:
    lines = ["FAULT INJECTION SUMMARY", "=" * 70, ""]
    lines.append(f"Nodes injected   : {result.total_nodes_injected}")
    lines.append(f"Total subscribers: {result.total_subscribers}")
    lines.append(f"Seeds used       : {result.seeds_used}")
    lines.append(f"Elapsed          : {elapsed:.2f}s")
    lines.append("")
    lines.append(f"{'Rank':<5}  {'Node ID':<30}  {'Type':<14}  "
                 f"{'I(v)':>7}  {'StdDev':>7}  {'Depth':>5}  "
                 f"{'Orphaned':>8}  {'Impacted':>8}")
    lines.append("-" * 90)
    for row in result.top_k_by_impact:
        lines.append(
            f"{row['rank']:<5}  {row['node_id']:<30}  {row['node_type']:<14}  "
            f"{row['impact_score']:>7.4f}  {row['impact_score_std']:>7.4f}  "
            f"{row['cascade_depth']:>5}  {row['orphaned_topics']:>8}  "
            f"{row['impacted_subscribers']:>8}"
        )
    summary_path = output_dir / "impact_scores_summary.txt"
    summary_path.write_text("\n".join(lines))
    logger.info("Text summary written → %s", summary_path)


# ─────────────────────────────────────────────────────────────────────────────
# message-flow subcommand
# ─────────────────────────────────────────────────────────────────────────────

def _run_message_flow(args: argparse.Namespace) -> None:
    try:
        from saag.simulation.message_flow_simulator import MessageFlowSimulator
    except ImportError as exc:
        logger.error("Cannot import MessageFlowSimulator: %s", exc)
        sys.exit(1)

    input_path = Path(args.input)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    if not input_path.exists():
        logger.error("Input file not found: %s", input_path)
        sys.exit(1)

    fault_time = getattr(args, "fault_time", None)
    fault_node = getattr(args, "fault_node", None)

    logger.info("═" * 60)
    logger.info("MESSAGE FLOW SIMULATION")
    logger.info("  Input      : %s", input_path)
    logger.info("  Output     : %s", output_dir)
    logger.info("  Duration   : %.1f s", args.duration)
    logger.info("  Seed       : %d", args.seed)
    qos_mode = getattr(args, "qos_mode", "full")
    rho = getattr(args, "target_utilization", 0.65)
    logger.info("  QoS mode   : %s", qos_mode)
    logger.info(
        "  Target rho : %s",
        # `legacy` ignores the target by definition; printing it there would
        # report a setting the run does not honour.
        rho if (rho and qos_mode != "legacy") else "uncalibrated",
    )
    logger.info("  Fault node : %s", fault_node or "none")
    if fault_node:
        logger.info("  Fault time : %.1f s", fault_time or (args.duration / 2))
    logger.info("═" * 60)

    t0 = time.perf_counter()
    g = _load_graph(input_path)

    # 0 means "leave service times uncalibrated"; argparse cannot express None
    # on a float flag without a sentinel, and the constructor rejects 0.0 as a
    # utilization because rho <= 0 is not a load.
    target_u = getattr(args, "target_utilization", 0.65)
    target_u = None if not target_u else float(target_u)

    sim = MessageFlowSimulator(
        graph=g,
        duration=float(args.duration),
        fault_node=fault_node,
        fault_time=float(fault_time) if fault_time else None,
        seed=args.seed,
        default_queue_size=getattr(args, "default_queue_size", 100),
        default_publish_rate_hz=getattr(args, "default_rate", 10.0),
        qos_mode=getattr(args, "qos_mode", "full"),
        target_utilization=target_u,
        utilization_mode=getattr(args, "utilization_mode", "per_subscriber"),
        service_distribution=getattr(args, "service_distribution", "exponential"),
        durability_replay_deadline=getattr(args, "replay_deadline", "original"),
        warmup_s=float(getattr(args, "warmup", 0.0)),
        guard_band_s=float(getattr(args, "guard_band", 0.0)),
    )
    result = sim.run()
    elapsed = time.perf_counter() - t0

    _print_message_flow_summary(result, elapsed)

    if args.export_json:
        out_json = output_dir / "message_flow_results.json"
        result.save(out_json)
        logger.info("Message flow results written → %s", out_json)

    _write_message_flow_text_summary(result, elapsed, output_dir)


def _operating_point(result) -> Optional[str]:
    """Requested vs realised utilization, or None for an uncalibrated run.

    A `target_utilization` unaccompanied by the measured busy fraction is a
    requested number, not a measured one; the two belong in the same line so a
    reader can check them against each other without reopening the JSON.
    """
    if result.target_utilization is None:
        return None
    measured = list(result.measured_utilization.values())
    realised = (
        f"{sum(measured) / len(measured):.3f} measured (n={len(measured)})"
        if measured else "not measured"
    )
    return f"{result.target_utilization:.2f} target / {realised}"


def _format_golden_signals(gs, indent: str = "  ") -> List[str]:
    lines = [f"{indent}FOUR GOLDEN SIGNALS (Google SRE):"]
    lat = gs.latency
    p50_s = f"{lat.p50_ms:.2f} ms" if lat.p50_ms is not None else "—"
    p95_s = f"{lat.p95_ms:.2f} ms" if lat.p95_ms is not None else "—"
    p99_s = f"{lat.p99_ms:.2f} ms" if lat.p99_ms is not None else "—"
    wait_s = f"{lat.mean_queue_wait_ms:.2f} ms" if lat.mean_queue_wait_ms is not None else "—"
    serv_s = f"{lat.mean_service_time_ms:.2f} ms" if lat.mean_service_time_ms is not None else "—"
    lines.append(f"{indent}  • Latency   : p50={p50_s} | p95={p95_s} | p99={p99_s}")
    lines.append(f"{indent}                Queue wait (mean)={wait_s} | Service (mean)={serv_s}")

    tr = gs.traffic
    lines.append(
        f"{indent}  • Traffic   : Publish={tr.published_rate_hz:.1f} msg/s | "
        f"Deliver={tr.delivered_rate_hz:.1f} msg/s | "
        f"Throughput={tr.throughput_bytes_per_s:.1f} B/s ({tr.throughput_kbps:.2f} Kbps)"
    )

    err = gs.errors
    lines.append(
        f"{indent}  • Errors    : Total={err.total_errors:,} (rate={err.error_rate:.4f}) | "
        f"Overflows={err.queue_overflows:,} | Deadline viol={err.deadline_violations:,}"
    )

    sat = gs.saturation
    lines.append(
        f"{indent}  • Saturation: CPU util={sat.cpu_utilization * 100:.1f}% "
        f"(peak={sat.peak_cpu_utilization * 100:.1f}%) | "
        f"Queue occupancy={sat.mean_queue_occupancy * 100:.1f}% "
        f"(peak={sat.peak_queue_occupancy * 100:.1f}%)"
    )
    return lines


def _print_message_flow_summary(result, elapsed: float) -> None:
    print()
    print("=" * 70)
    print("MESSAGE FLOW SUMMARY")
    print("=" * 70)
    print(f"  Duration              : {result.simulation_duration:.1f} s")
    print(f"  Seed                  : {result.seed}")
    print(f"  Total published       : {result.total_messages_published:,}")
    print(f"  Total delivered       : {result.total_messages_delivered:,}")
    print(f"  System delivery rate  : {result.system_delivery_rate:.4f}")
    print(f"  System drop rate      : {result.system_drop_rate:.4f}")
    print(f"  Deadline violations   : {result.total_deadline_violations:,}")
    print(f"  Queue overflows       : {result.total_queue_overflows:,}")
    print(f"  QoS mode              : {result.qos_mode}")
    _op = _operating_point(result)
    if _op:
        print(f"  Utilization           : {_op}")
    print(f"  Elapsed (wall)        : {elapsed:.2f}s")

    if getattr(result, "golden_signals", None):
        print()
        for l in _format_golden_signals(result.golden_signals, indent="  "):
            print(l)

    if result.fault_event:
        fe = result.fault_event
        print()
        print("  Fault event:")
        print(f"    Node       : {fe.faulted_node_id} ({fe.faulted_node_type})")
        print(f"    Time       : {fe.fault_time:.1f} s")
        print(f"    Orphaned   : {fe.cascade_orphaned_topics}")
        print(f"    Impacted   : {fe.cascade_impacted_subscribers}")
        print(f"    Lost topics: {fe.lost_topics_count} (critical: {fe.lost_critical_topics_count})")
        print(f"    Lost apps  : {fe.lost_applications_count} (critical: {fe.lost_critical_applications_count})")
        print(f"    Rate before: {fe.delivery_rate_before:.4f}")
        print(f"    Rate after : {fe.delivery_rate_after:.4f}")
        if getattr(fe, "signals_before", None) and getattr(fe, "signals_after", None):
            print()
            print("    Signals before fault:")
            for l in _format_golden_signals(fe.signals_before, indent="      "):
                print(l)
            print("    Signals after fault:")
            for l in _format_golden_signals(fe.signals_after, indent="      "):
                print(l)

    print()
    print(f"  {'Topic':<30}  {'Delivery':>9}  {'P50 ms':>8}  {'P95 ms':>8}  "
          f"{'Deadline viol':>13}")
    print(f"  {'─'*30}  {'─'*9}  {'─'*8}  {'─'*8}  {'─'*13}")
    for tid, ts in sorted(result.topic_stats.items(),
                          key=lambda x: x[1].delivery_rate):
        p50 = f"{ts.latency_p50:.2f}" if ts.latency_p50 is not None else "—"
        p95 = f"{ts.latency_p95:.2f}" if ts.latency_p95 is not None else "—"
        print(f"  {ts.topic_name:<30}  {ts.delivery_rate:>9.4f}  {p50:>8}  "
              f"{p95:>8}  {ts.total_dropped_deadline:>13,}")
    print("=" * 70)
    print()


def _write_message_flow_text_summary(result, elapsed: float, output_dir: Path) -> None:
    lines = ["MESSAGE FLOW SIMULATION SUMMARY", "=" * 70, ""]
    lines.append(f"Duration           : {result.simulation_duration:.1f} s")
    lines.append(f"Seed               : {result.seed}")
    lines.append(f"Published          : {result.total_messages_published:,}")
    lines.append(f"Delivered          : {result.total_messages_delivered:,}")
    lines.append(f"System delivery    : {result.system_delivery_rate:.4f}")
    lines.append(f"Deadline violations: {result.total_deadline_violations:,}")
    lines.append(f"Queue overflows    : {result.total_queue_overflows:,}")
    lines.append(f"QoS mode           : {result.qos_mode}")
    _op = _operating_point(result)
    if _op:
        lines.append(f"Utilization        : {_op}")
    lines.append(f"Elapsed (wall)     : {elapsed:.2f}s")

    if getattr(result, "golden_signals", None):
        lines.append("")
        lines.extend(_format_golden_signals(result.golden_signals, indent=""))

    if result.fault_event:
        fe = result.fault_event
        lines.extend([
            "",
            "FAULT EVENT",
            f"  Node    : {fe.faulted_node_id} ({fe.faulted_node_type})",
            f"  Time    : {fe.fault_time:.1f} s",
            f"  Orphaned: {', '.join(fe.cascade_orphaned_topics)}",
            f"  Impacted: {', '.join(fe.cascade_impacted_subscribers)}",
            f"  Lost topics: {fe.lost_topics_count} (critical: {fe.lost_critical_topics_count})",
            f"  Lost apps  : {fe.lost_applications_count} (critical: {fe.lost_critical_applications_count})",
            f"  Rate before fault : {fe.delivery_rate_before:.4f}",
            f"  Rate after fault  : {fe.delivery_rate_after:.4f}",
        ])
        if getattr(fe, "signals_before", None) and getattr(fe, "signals_after", None):
            lines.append("")
            lines.append("  Signals before fault:")
            lines.extend(_format_golden_signals(fe.signals_before, indent="    "))
            lines.append("  Signals after fault:")
            lines.extend(_format_golden_signals(fe.signals_after, indent="    "))

    lines.extend(["", "TOPIC BREAKDOWN", "-" * 70])
    for tid, ts in sorted(result.topic_stats.items(),
                          key=lambda x: x[1].delivery_rate):
        lines.append(
            f"  {ts.topic_name:<30}  delivery={ts.delivery_rate:.4f}  "
            f"published={ts.total_published}  dropped_deadline={ts.total_dropped_deadline}"
        )

    summary_path = output_dir / "message_flow_summary.txt"
    summary_path.write_text("\n".join(lines))
    logger.info("Text summary written → %s", summary_path)


# ─────────────────────────────────────────────────────────────────────────────
# combined subcommand
# ─────────────────────────────────────────────────────────────────────────────

def _run_combined(args: argparse.Namespace) -> None:
    """Run both simulations, deriving separate output paths when needed.

    _run_fault_inject treats a `.json`-suffixed --output as a single result
    file; _run_message_flow always treats --output as a directory it
    creates. Passing one unmodified `args.output` to both meant
    `combined --output results.json` had message-flow try to mkdir() a path
    fault-inject had just written as a file, raising FileExistsError.
    """
    import copy

    logger.info("Running COMBINED simulation (fault-inject + message-flow)")
    _run_fault_inject(args)

    flow_args = args
    if args.output.endswith(".json"):
        flow_args = copy.copy(args)
        flow_args.output = str(Path(args.output).parent / "message_flow")
    _run_message_flow(flow_args)




# ─────────────────────────────────────────────────────────────────────────────
# Argument parsing
# ─────────────────────────────────────────────────────────────────────────────

def _parse_seeds(seeds_str: str) -> List[int]:
    try:
        return [int(s.strip()) for s in seeds_str.split(",")]
    except ValueError:
        logger.error("Invalid seeds argument: %s  (expected comma-separated ints)", seeds_str)
        sys.exit(1)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="simulate_graph.py",
        description=(
            "SaG simulation pipeline: fault injection (ground-truth I(v)) "
            "and discrete-event message flow simulation."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("EXAMPLES")[1] if "EXAMPLES" in __doc__ else "",
    )

    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    # ── Shared arguments factory ──────────────────────────────────────────
    def _add_shared(p: argparse.ArgumentParser) -> None:
        p.add_argument("--input", default=None,
                       help="Path to graph JSON file.")
        p.add_argument("--layer", default=None,
                       help="System layer name (resolves to data/<layer>.json if --input is missing).")
        p.add_argument("--output", default="output/simulation/",
                       help="Output directory.  Created if absent.  "
                            "Default: output/simulation/")
        p.add_argument("--export-json", action="store_true",
                       help="Write JSON result file(s) to --output.")
        p.add_argument("--verbose", "-v", action="store_true",
                       help="Enable DEBUG logging.")

    def _add_network_gating(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--network-gating", action="store_true",
            help="Count a pub-sub delivery only while the CONNECTS_TO network "
                 "(hosts, switches, routers) still connects publisher, routing "
                 "broker and subscriber.  Off by default; a no-op without CONNECTS_TO.",
        )

    # ── fault-inject ──────────────────────────────────────────────────────
    fi = subparsers.add_parser(
        "fault-inject",
        help="BFS cascade fault injection → I(v) ground-truth impact scores.",
        description=(
            "Injects single-node faults and traces cascading pub-sub failures "
            "to produce per-node ground-truth impact scores I(v)."
        ),
    )
    _add_shared(fi)
    _add_network_gating(fi)
    fi.add_argument(
        "--nodes",
        default=None,
        metavar="ID1,ID2,...",
        help="Comma-separated node IDs to inject.  If omitted, injects all "
             "nodes matching --node-types.",
    )
    fi.add_argument(
        "--node-types",
        default="Application,Broker,Library",
        metavar="TYPE1,TYPE2",
        help="Comma-separated node types to inject.  "
             "Default: Application,Broker,Library.  Types omitted here get NO "
             "ground truth and are recorded in the artifact's unlabeled_node_ids.  "
             "'Topic' and 'Node' are excluded on purpose: the cascade derives "
             "DEPENDS_ON only from PUBLISHES_TO/SUBSCRIBES_TO/USES, so injecting "
             "either yields I(v)=0 for every one of them — spurious zeros, not labels.  "
             "With --network-gating, 'Node' and 'NetworkDevice' do get labels: "
             "losing them can cut subscribers off from their publishers.",
    )
    fi.add_argument(
        "--seeds",
        default="42,123,456,789,2024",
        metavar="42,123,...",
        help="Comma-separated seeds for multi-seed stability testing.  "
             "Default: the five recommended seeds; labels are the per-node mean "
             "and impact_score_std reports the spread.",
    )
    fi.add_argument(
        "--cascade-depth",
        type=int,
        default=0,
        metavar="N",
        help="Maximum cascade wave depth (0 = unlimited).  Default: 0",
    )
    fi.add_argument(
        "--propagation-threshold",
        type=float,
        default=0.2,
        metavar="F",
        help="Fraction of feed loss before a subscriber cascades further.  Default: 0.2",
    )
    fi.add_argument(
        "--qos-factor",
        choices=FaultInjector.QOS_FACTOR_MODES,
        default="ladder",
        help=(
            "How a topic's QoS scales its feed-loss fraction. 'ladder' (default) "
            "reproduces the published x1.2/x1.15/x1.05 constants; 'wt' uses the "
            "shared w(t) weight so durability participates too; 'none' disables "
            "QoS scaling and is the topology-only arm of the label ablation."
        ),
    )

    # ── edge-criticality ─────────────────────────────────────────────────
    ec = subparsers.add_parser(
        "edge-criticality",
        help="Relationship removal sweep -> measured per-edge criticality labels.",
        description=(
            "Severs each candidate relationship with both endpoints left alive "
            "and recomputes the composite impact, producing the measured edge "
            "labels the GNN edge head trains against."
        ),
    )
    _add_shared(ec)
    _add_network_gating(ec)
    ec.add_argument(
        "--layer-scope",
        default="system",
        metavar="LAYER",
        help="Layer whose edges are swept. Default: system",
    )
    ec.add_argument(
        "--top-q",
        type=int,
        default=50,
        metavar="N",
        help=(
            "Candidate budget beyond bridges: the N highest-betweenness edges. "
            "An exhaustive sweep is O(|E|) full impact recomputations, so edges "
            "outside the candidate set are emitted with evaluated=false rather "
            "than scored 0. Default: 50"
        ),
    )

    # ── message-flow ─────────────────────────────────────────────────────
    mf = subparsers.add_parser(
        "message-flow",
        help="Discrete-event pub-sub message flow simulation (SimPy).",
        description=(
            "Models publishers, broker queues, subscribers and QoS in "
            "simulated time.  Optionally injects a node fault at --fault-time."
        ),
    )
    _add_shared(mf)
    mf.add_argument(
        "--duration",
        type=float,
        default=100.0,
        metavar="SECONDS",
        help="Simulation duration in simulated seconds.  Default: 100.0",
    )
    mf.add_argument(
        "--fault-node",
        default=None,
        metavar="NODE_ID",
        help="Node ID to fault during simulation.  If omitted, no fault is injected.",
    )
    mf.add_argument(
        "--fault-time",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Simulated time at which to inject the fault.  "
             "Default: duration / 2.",
    )
    mf.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for jitter in intervals and processing times.  Default: 42",
    )
    mf.add_argument(
        "--default-rate",
        type=float,
        default=10.0,
        metavar="HZ",
        help="Fallback publish rate (Hz) when not in graph metadata.  Default: 10.0",
    )
    mf.add_argument(
        "--default-queue-size",
        type=int,
        default=100,
        metavar="N",
        help="Fallback broker queue capacity.  Default: 100",
    )

    # ── QoS enforcement ──────────────────────────────────────────────────
    # Mirrors --qos-factor on fault-inject: the two engines' QoS arms are
    # selected the same way so an ablation can name its arm consistently.
    mf.add_argument(
        "--qos-mode",
        choices=QOS_MODES,
        default="full",
        help="Which declared QoS policies to enforce.  'contracts' = deadlines "
             "and history-depth queue capacity; 'recovery' = durability replay; "
             "'full' = both plus priority-ordered service; 'none' = policies "
             "neutralised but load kept (the QoS-off ablation arm); 'legacy' = "
             "the pre-QoS engine, uncalibrated.  Default: full",
    )
    mf.add_argument(
        "--target-utilization",
        type=float,
        default=0.65,
        metavar="RHO",
        help="Baseline load each subscriber is calibrated to, in (0, 1).  Pass 0 "
             "to leave service times uncalibrated.  Below ~0.5 nothing is "
             "contended and no QoS contract can bind; above ~0.8 run-to-run "
             "variance grows faster than the signal.  Default: 0.65",
    )
    mf.add_argument(
        "--utilization-mode",
        choices=UTILIZATION_MODES,
        default="per_subscriber",
        help="'per_subscriber' sizes each subscriber's service rate to its own "
             "offered load, so RHO means the same thing on a 1 Hz and a 700 Hz "
             "scenario.  'global' uses one rate per scenario.  "
             "Default: per_subscriber",
    )
    mf.add_argument(
        "--service-distribution",
        choices=SERVICE_DISTRIBUTIONS,
        default="exponential",
        help="Service-time distribution at each subscriber.  Default: exponential",
    )
    mf.add_argument(
        "--replay-deadline",
        choices=REPLAY_DEADLINE_POLICIES,
        default="original",
        help="Whether a durability-replayed sample keeps its original timestamp "
             "('original': a declared deadline drops it, since durability "
             "recovers state rather than timeliness) or is treated as a fresh "
             "snapshot ('reset').  Default: original",
    )
    mf.add_argument(
        "--warmup",
        type=float,
        default=0.0,
        metavar="SECONDS",
        help="Messages created before this time are counted in neither fault "
             "window, letting queues reach steady state first.  Default: 0.0",
    )
    mf.add_argument(
        "--guard-band",
        type=float,
        default=0.0,
        metavar="SECONDS",
        help="Messages created within this distance of --fault-time are counted "
             "in neither window.  Default: 0.0",
    )

    # ── combined ──────────────────────────────────────────────────────────
    co = subparsers.add_parser(
        "combined",
        help="Run fault-inject followed by message-flow.",
        description="Runs both simulation modes in sequence.",
    )
    _add_shared(co)
    # Fault-inject flags
    co.add_argument("--nodes", default=None, metavar="ID1,ID2,...")
    co.add_argument("--node-types", default="Application,Broker", metavar="TYPE1,TYPE2")
    co.add_argument("--seeds", default="42", metavar="42,123,...")
    co.add_argument("--cascade-depth", type=int, default=0, metavar="N")
    co.add_argument("--propagation-threshold", type=float, default=0.2, metavar="F")
    co.add_argument("--qos-factor", choices=FaultInjector.QOS_FACTOR_MODES, default="ladder")
    # Message-flow flags
    co.add_argument("--duration", type=float, default=100.0, metavar="SECONDS")
    co.add_argument("--fault-node", default=None, metavar="NODE_ID")
    co.add_argument("--fault-time", type=float, default=None, metavar="SECONDS")
    co.add_argument("--seed", type=int, default=42)
    co.add_argument("--default-rate", type=float, default=10.0, metavar="HZ")
    co.add_argument("--default-queue-size", type=int, default=100, metavar="N")

    return parser


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    # Default to "fault-inject" when no subcommand is given. Only the first
    # positional token can be a subcommand, so checking it alone (rather than
    # scanning every non-flag token, as this used to) avoids mistaking an
    # option *value* (e.g. `--output combined`) for a command — see
    # cli/validate_graph.py's main(), which fixed the same bug.
    if not sys.argv[1:] or sys.argv[1].startswith("-"):
        sys.argv.insert(1, "fault-inject")

    parser = _build_parser()
    args = parser.parse_args()
    setup_logging(args)

    if not args.input and getattr(args, "layer", None):
        args.input = f"data/{args.layer}.json"

    if not args.input:
        logger.error("Either --input or --layer must be specified.")
        sys.exit(1)

    dispatch = {
        "fault-inject": _run_fault_inject,
        "edge-criticality": _run_edge_criticality,
        "message-flow": _run_message_flow,
        "combined": _run_combined,
    }

    handler = dispatch.get(args.subcommand)
    if handler is None:
        parser.print_help()
        sys.exit(1)

    handler(args)


if __name__ == "__main__":
    main()