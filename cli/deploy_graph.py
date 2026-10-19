#!/usr/bin/env python3
"""
Pre-deployment Planning CLI

Maps Applications and Brokers to Nodes under the resource, replica,
anti-affinity, pinning and topic-deadline constraints of a deployment spec,
trades cross-host traffic against expected blast radius, and verifies every
candidate on the replica-aware failure simulator. It plans a deployment; it
never deploys anything.

Exit codes: 0 success, 1 error, 2 the spec is invalid, cannot be satisfied, or no
feasible placement was found.
"""
import argparse
import json
import sys
from pathlib import Path

# Add project root to sys.path to support direct execution (python cli/deploy_graph.py)
if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from saag import Client
from saag.deployment.heuristics import BASELINES
from saag.deployment.service import SOLVERS
from saag.deployment.spec import DeploymentSpecError
from cli.common.arguments import add_neo4j_arguments, add_runtime_arguments, setup_logging
from cli.common.console import ConsoleDisplay

EXIT_INFEASIBLE = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Plan an application-to-node deployment from a deployment spec and verify it on the "
                    "replica-aware failure simulator. Plans only: nothing is deployed."
    )
    parser.add_argument("--spec", "-s", required=True, metavar="FILE",
                        help="Deployment spec (YAML or JSON): node capacity, failure domains, latency, demands.")
    parser.add_argument("--input", "-i", type=str, help="Topology JSON to run in-memory (default: Neo4j).")

    solve = parser.add_argument_group("Solver")
    solve.add_argument("--front-points", type=int, default=5,
                       help="Points on the traffic-vs-blast front (default 5).")
    solve.add_argument("--ties", type=int, default=5,
                       help="Extra placements tied at minimum blast, to measure solver-pick noise (default 5).")
    solve.add_argument("--balance", action="store_true", help="Break remaining ties toward lower peak utilisation.")
    solve.add_argument("--time-limit", type=float, default=60.0, help="Seconds per MILP solve (default 60).")
    solve.add_argument("--time-budget", type=float, default=600.0, help="Seconds for all MILP solves (default 600).")
    solve.add_argument("--probe-limit", type=float, default=30.0,
                       help="Seconds the first MILP stage gets to find any placement before --solver auto "
                            "falls back to the heuristic (default 30).")
    solve.add_argument("--heuristic-budget", type=float, default=600.0,
                       help="Seconds for the heuristic search, on its own clock (default 600).")
    solve.add_argument("--max-nonzeros", type=int, default=150_000,
                       help="Model size above which --solver auto uses the heuristic (default 150,000).")
    solve.add_argument("--solver", choices=SOLVERS, default="auto",
                       help="auto: MILP with heuristic fallback; milp: MILP only; heuristic: local search only.")

    verify = parser.add_argument_group("Held-out verification")
    verify.add_argument("--baselines", nargs="+", choices=BASELINES, default=list(BASELINES),
                        help="Baselines to score (indeg_spread is always included).")
    verify.add_argument("--seeds", nargs="+", type=int, default=[42, 123, 456],
                        help="Failure-simulator seeds (default 42 123 456).")
    verify.add_argument("--kappa", type=float, default=1.0,
                        help="Noise-bar multiple of the seed standard deviation (default 1.0).")
    verify.add_argument("--network-gating", choices=("auto", "on", "off"), default="auto",
                        help="Gate deliveries on the CONNECTS_TO network; auto = on when the topology has devices.")

    out = parser.add_argument_group("Output")
    out.add_argument("--output", "-o", metavar="FILE", help="Write the full result JSON here.")
    out.add_argument("--placed-topology", metavar="FILE",
                     help="Write the topology with the recommended placement applied (importable with saag-import).")

    add_neo4j_arguments(parser)
    add_runtime_arguments(parser)
    return parser


def build_client(args, console: ConsoleDisplay) -> Client:
    if args.input:
        from saag.infrastructure.memory_repo import MemoryRepository
        console.print_step(f"Loading graph JSON in-memory: {args.input}...")
        repo = MemoryRepository()
        with open(args.input) as f:
            repo.save_graph(json.load(f), clear=True)
        repo.derive_dependencies()
        return Client(repo=repo)
    console.print_step(f"Connecting to Neo4j at {args.uri}...")
    return Client(neo4j_uri=args.uri, user=args.user, password=args.password)


def print_candidates(res) -> None:
    head = (f"  {'candidate':<14}{'kind':<10}{'traffic':>12}{'x-host':>8}{'blast E':>9}{'blast max':>10}"
            f"{'peak util':>10}{'hosts':>6}{'I_comp E':>11}{'±σ':>9}{'worst':>7}  verdict")
    print(head)
    print("  " + "-" * (len(head) - 2))
    for c in res.candidates:
        h = c.held_out
        util = max(c.load["max_cpu_util"], c.load["max_mem_util"])
        flags = (" *" if c.name == res.recommended else "") + ("" if c.feasible else " (violates constraints)")
        print(f"  {c.name:<14}{c.kind:<10}{c.traffic:>12.4g}{c.cross_host_share:>8.2f}{c.blast_exp:>9.3f}"
              f"{c.blast_max:>10.0f}{util:>10.2f}{c.load['hosts_used']:>6.0f}{h.expected_impact:>11.5f}"
              f"{h.sigma_seed:>9.1e}{h.worst_impact:>7.3f}  {c.verdict or '-'}{flags}")


def print_diagnosis(console: ConsoleDisplay, diagnosis) -> None:
    for line in diagnosis.prechecks:
        console.print_error(line)
    for family, n in diagnosis.families.items():
        console.print_error(f"{family}: {n} constraint(s) cannot be met together")
    for c in diagnosis.culprits:
        print("    " + ", ".join(f"{k}={v}" for k, v in c.items()))


def main():
    args = build_parser().parse_args()
    setup_logging(args)
    console = ConsoleDisplay()
    console.print_header("Pre-deployment Planning")

    try:
        client = build_client(args, console)
        console.print_step(f"Solving placement for spec {args.spec} and verifying on the failure simulator...")
        res = client.deploy(
            args.spec,
            front_points=args.front_points, ties=args.ties, balance=args.balance,
            time_limit=args.time_limit, time_budget=args.time_budget, probe_limit=args.probe_limit,
            heuristic_budget=args.heuristic_budget, max_nonzeros=args.max_nonzeros,
            solver=args.solver, baseline_names=args.baselines, seeds=args.seeds, kappa=args.kappa,
            network_gating={"auto": None, "on": True, "off": False}[args.network_gating],
        )
    except DeploymentSpecError as e:
        console.print_error(str(e))
        sys.exit(EXIT_INFEASIBLE)
    except Exception as e:
        console.print_error(f"Deployment planning failed: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(res.to_dict(), indent=2))
        console.print_success(f"Result saved to {args.output}")

    if res.status == "infeasible":
        console.print_header("No feasible placement")
        print_diagnosis(console, res.diagnosis)
        sys.exit(EXIT_INFEASIBLE)
    if res.status == "no_feasible":
        console.print_header("No feasible placement found")
        console.print_warning("Not proven infeasible: the search found no placement meeting every constraint. "
                              "The closest candidate violates:")
        print_diagnosis(console, res.diagnosis)
        print(f"\n  {res.rationale}")
        sys.exit(EXIT_INFEASIBLE)

    console.print_subheader(f"Candidates (solver: {res.solver}, status: {res.status}, "
                            f"latency: {res.spec_summary.get('latency_source')})")
    print_candidates(res)
    if res.skipped:
        console.print_warning(f"Skipped (time budget or no solution): {', '.join(res.skipped)}")
    rho = "n/a" if res.proxy_oracle_rho is None else f"{res.proxy_oracle_rho:+.2f}"
    print(f"\n  Best baseline: {res.best_baseline}   tie spread: {res.noise_floor:.3g}   "
          f"rho(blast proxy, held-out): {rho}")

    console.print_subheader(f"Recommendation: {res.recommended}")
    print(f"  {res.rationale}")
    if res.reliability_claim:
        console.print_success("Held-out reliability gain confirmed.")
    else:
        console.print_warning("No reliability gain claimed; the recommendation rests on traffic and feasibility.")

    if args.placed_topology and res.recommended_candidate is not None:
        from saag.deployment import apply_placement
        out_path = Path(args.placed_topology)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        topology = client.repo.export_json()
        out_path.write_text(json.dumps(apply_placement(topology, res.recommended_candidate.placement), indent=2))
        console.print_success(f"Placed topology saved to {args.placed_topology}")


if __name__ == "__main__":
    main()
