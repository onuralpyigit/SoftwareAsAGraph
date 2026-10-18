#!/usr/bin/env python3
"""
cli/run.py — Software-as-a-Graph Pipeline Orchestrator
======================================================
Executes the analytical pipeline using the saag SDK.
"""

import argparse
import sys
from pathlib import Path

# Add project root to sys.path to support direct execution (python cli/run.py)
if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from saag import Pipeline
from cli.common.arguments import add_neo4j_arguments, add_common_arguments, setup_logging
from cli.common.console import ConsoleDisplay, Colors

_ALL_LAYERS = ["app", "infra", "mw", "system"]


def _resolve_layers(args, layer_explicit: bool) -> list:
    """Layers for the validate/visualize stages under --all.

    An explicitly-passed --layer must be honoured even under --all — it used
    to be silently discarded there in favour of all four layers, while
    analyze/simulate (which read args.layer directly) kept honouring it, so
    --all --layer app analyzed "app" but validated/visualized all four.
    """
    if layer_explicit:
        return [l.strip() for l in args.layer.split(",")]
    if args.all:
        return list(_ALL_LAYERS)
    return [l.strip() for l in args.layer.split(",")] if args.layer else ["system"]


def main():
    display = ConsoleDisplay()
    parser = argparse.ArgumentParser(
        description="Run the Software-as-a-Graph pipeline.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    
    # Stage flags
    group = parser.add_argument_group("Pipeline Stages")
    group.add_argument("--all", action="store_true",
                       help=("Run all stages sequentially: generate → import → analyze → "
                             "simulate → predict → diagnose → validate → prescribe → visualize. "
                             "NOTE: the predict stage requires a pre-trained GNN checkpoint "
                             "(--gnn-model); without one it is skipped, but the diagnose stage "
                             "still runs (RM scores, anti-patterns) since it needs no checkpoint. "
                             "To enable GNN ranking, label and train first with: "
                             "python cli/simulate_graph.py fault-inject --input <system.json> --export-json, then "
                             "python cli/train_graph.py --layer system "
                             "--simulated output/simulation/impact_scores.json --output <checkpoint_dir>"))
    group.add_argument("--generate", action="store_true", help="Run graph generation stage")
    group.add_argument("--input", "-i", metavar="FILE", help="System topology JSON file (input for import, output for generate)")
    group.add_argument("--analyze", action="store_true", help="Run analysis stage (structural metrics only)")
    group.add_argument("--predict", action="store_true", help="Run the Predict stage (RM always + GNN ranking when available)")
    group.add_argument("--diagnose", action="store_true", help="Run the Diagnose stage (RM scores + anti-patterns + explanation; no checkpoint required)")
    group.add_argument("--simulate", action="store_true", help="Run failure simulation stage")
    group.add_argument("--validate", action="store_true", help="Run validation stage (compare prediction vs simulation)")
    group.add_argument("--prescribe", action="store_true", help="Run prescriptive remediation stage")
    group.add_argument("--deploy", action="store_true",
                       help="Run pre-deployment planning (application-to-node mapping; needs --deployment-spec). "
                            "Plans only; nothing is deployed.")
    group.add_argument("--visualize", action="store_true", help="Run visualization stage (generates HTML dashboard)")
    
    # Stage-specific options
    opts = parser.add_argument_group("Stage Options")
    opts.add_argument("--config", type=Path, help="Path to graph configuration YAML file (for generation)")
    opts.add_argument("--scale", choices=["tiny", "small", "medium", "large", "jumbo", "xlarge"], help="Preset graph scale (for generation)")
    opts.add_argument("--output-dir", metavar="DIR", help="Directory for all intermediate and final outputs")
    opts.add_argument("--clear", "--clean", action="store_true", dest="clear", help="Clear Neo4j DB before import")
    opts.add_argument("--use-ahp", action="store_true", help="Use AHP-derived weights instead of default fixed weights")
    opts.add_argument("--gnn-model", metavar="PATH", help="Path to GNN model checkpoint directory")
    opts.add_argument("--triage-k", type=int, default=None, metavar="K",
                       help="Run the Triage bridge as part of diagnose: shortlist the "
                            "Top-K critical components (GNN-ranked when --gnn-model was "
                            "set and predict ran, RM-ranked otherwise) and print each "
                            "one's RM root-cause diagnosis. Requires diagnose to run "
                            "this same invocation (--diagnose or --all); --all alone "
                            "does not imply a --triage-k value.")
    opts.add_argument("--deployment-spec", metavar="FILE",
                      help="Deployment spec for --deploy (node capacity, failure domains, latency, demands). "
                           "With --all, the deploy stage runs only when this is given.")
    opts.add_argument("--sim-mode", default="exhaustive", help="Simulation mode (e.g., exhaustive, monte_carlo)")
    opts.add_argument("--no-network", action="store_true", help="Skip interactive network in visualization")
    opts.add_argument("--no-matrix", action="store_true", help="Skip dependency matrix in visualization")
    opts.add_argument("--no-validation", action="store_true", help="Skip validation metrics in visualization")

    add_neo4j_arguments(parser)
    add_common_arguments(parser)
    
    # Compatibility shim: old scripts might pass --import-file or --import
    parser.add_argument("--import-file", dest="input", help=argparse.SUPPRESS)
    
    args = parser.parse_args()
    setup_logging(args)

    # Whether the user gave --layer/-l explicitly, as opposed to relying on
    # add_common_arguments' "system" default — distinguishes "run --all with
    # the default" from "run --all --layer app", which --all used to ignore
    # for validate/visualize (see _resolve_layers below).
    layer_explicit = any(
        a in ("--layer", "-l") or a.startswith("--layer=") for a in sys.argv[1:]
    )

    # Ensure at least one stage is selected
    stages = [args.all, args.generate, bool(args.input), args.analyze, args.predict, args.diagnose, args.simulate, args.validate, args.prescribe, args.deploy, args.visualize]
    if not any(stages):
        parser.error("No stages selected. Use --all or specific stage flags.")
    if args.deploy and not args.deployment_spec:
        parser.error("--deploy needs --deployment-spec FILE.")

    # First-run guard: --all (or explicit --predict) requires a trained GNN checkpoint.
    # Without it the predict stage will fail with a cryptic FileNotFoundError.
    skip_predict = False
    if (args.all or args.predict) and not args.gnn_model:
        from saag.prediction.service import PredictionService
        _default_ckpt = "output/gnn_checkpoints"
        if not PredictionService._has_checkpoint(_default_ckpt):
            print(
                "\n[WARNING] --all / --predict requires a pre-trained GNN checkpoint.\n"
                f"  No checkpoint found at default path '{_default_ckpt}' and\n"
                "  no --gnn-model path was supplied.\n\n"
                "  First-run sequence:\n"
                "    1. python cli/run.py --input system.json --analyze\n"
                "    2. PYTHONPATH=. python cli/simulate_graph.py fault-inject \\\n"
                "           --input system.json --export-json\n"
                "    3. PYTHONPATH=. python cli/train_graph.py --layer system \\\n"
                "           --simulated output/simulation/impact_scores.json \\\n"
                f"           --output {_default_ckpt}\n"
                f"    4. python cli/run.py --all --gnn-model {_default_ckpt}\n\n"
                "  The predict stage (GNN ranking) will be skipped this run to avoid a\n"
                "  crash — the diagnose stage (RM scores, anti-patterns, explanation)\n"
                "  needs no checkpoint and still runs.\n",
                file=sys.stderr,
            )
            skip_predict = True   # neutralise predict in --all mode for this run
            # Note: args.all stays True so all other stages, including
            # diagnose, still execute.

    # 0. Generation Stage (Pre-Pipeline)
    # If using --all, we assume generation is desired if --config or --scale provided
    do_generate = args.generate or (args.all and (args.config or args.scale))
    if do_generate:
        from cli.common.dispatcher import dispatch_generate
        if not args.input and not args.all:
            parser.error("--generate requires --input to specify where to save the graph.")
        
        # Default output if not provided
        gen_output = args.input or (Path(args.output_dir) / "graph.json" if args.output_dir else "output/graph.json")
        gen_output = str(gen_output)

        # Use a throw-away namespace so args.output (the user's final result path) is not clobbered.
        import argparse as _ap
        gen_args = _ap.Namespace(**vars(args))
        gen_args.output = gen_output

        display.print_header("Stage 0: Graph Generation (optional)")
        display.print_step("Generating synthetic topology...")
        data = dispatch_generate(gen_args)
        display.display_graph_data_summary(data)

        # Update input path for subsequent stages
        args.input = gen_output

    # 1. Initialize Pipeline
    # If no input provided but analyze/simulate/etc was requested, we operate on existing DB
    if args.input or args.all:
        import_path = args.input if args.input else "output/graph.json"
        
        # Check if the file exists before passing to from_json
        if not Path(import_path).exists():
            if not do_generate:
                print(f"Error: Input file '{import_path}' not found.", file=sys.stderr)
                return 1
            
        pipeline = Pipeline.from_json(import_path, clear=args.clear, neo4j_uri=args.uri, user=args.user, password=args.password)
    else:
        pipeline = Pipeline(neo4j_uri=args.uri, user=args.user, password=args.password)
        
    # 2. Configure Pipeline Stages
    if args.analyze or args.all:
        pipeline.analyze(layer=args.layer)

    if (args.predict or args.all) and not skip_predict:
        pipeline.predict(gnn_checkpoint=args.gnn_model, use_ahp=args.use_ahp)

    if args.diagnose or args.all:
        # use_ahp only takes effect when predict() didn't already run (or was
        # skipped above) — otherwise diagnose() reuses predict()'s RM pass.
        pipeline.diagnose(k=args.triage_k, use_ahp=args.use_ahp)

    if args.simulate or args.all:
        pipeline.simulate(layer=args.layer, mode=args.sim_mode)

    if args.validate or args.all:
        pipeline.validate(layers=_resolve_layers(args, layer_explicit))

    if args.prescribe or args.all:
        pipeline.prescribe()

    if args.deploy or (args.all and args.deployment_spec):
        pipeline.deploy(args.deployment_spec)

    if args.visualize or args.all:
        layers = _resolve_layers(args, layer_explicit)

        out_path = args.output if args.output else "dashboard.html"
        if args.output_dir:
            out_path = str(Path(args.output_dir) / (args.output or "dashboard.html"))
            
        pipeline.visualize(
            output=out_path,
            layers=layers,
            include_network=not args.no_network,
            include_matrix=not args.no_matrix,
            include_validation=not args.no_validation
        )
        
    # 3. Execute pipeline
    display.print_header("Analytical Pipeline Execution")
    display.print_step("Running configured stages sequentially...")
    try:
        result = pipeline.run()
    except Exception as e:
        display.print_error(f"Pipeline execution failed: {e}")
        if getattr(args, "verbose", False):
            import traceback
            traceback.print_exc()
        return 1
    
    # 4. Post-Execution reporting
    if result.analysis:
        # Use the summary method we added
        display.display_structural_summary(result.analysis.raw.to_dict())
    
    if result.prediction:
        display.display_prediction_summary(result.prediction)

    if result.diagnosis:
        display.display_prediction_summary(result.diagnosis)

    if result.triage:
        display.print_subheader(f"Triage Shortlist (Top-{result.triage.k}, ranking source: {result.triage.ranking_source})")
        for entry in result.triage.entries:
            print(f"  {entry.rank}. {entry.component_id} — {entry.pattern} [{entry.level}] "
                  f"→ {', '.join(entry.roles)}")

    if result.simulation:
        display.display_simulation_summary(result.simulation)
        
    if result.validation:
        display.display_validation_summary(result.validation)
        
    if result.prescription:
        display.print_subheader("Prescriptive Remediation Summary (Stage 7)")
        print(f"  Baseline SRI : {result.prescription.original_sri:.4f}")
        print(f"  Mutated SRI  : {result.prescription.mutated_sri:.4f}")
        improvement = result.prescription.sri_improvement
        if improvement > 0:
            print(f"  Improvement  : {display.colored(f'+{improvement:.4f}', Colors.GREEN)}")
        else:
            print(f"  Improvement  : {improvement:.4f}")
        print(f"  Accepted     : {result.prescription.accepted}")
        print(f"  Applied changes: {len(result.prescription.applied_changes)}")
        for change in result.prescription.applied_changes[:5]:
            print(f"    - {change}")
        if len(result.prescription.applied_changes) > 5:
            print(f"    ... and {len(result.prescription.applied_changes) - 5} more changes.")
    
    if result.deployment:
        dep = result.deployment
        display.print_subheader("Pre-deployment Plan")
        print(f"  Status       : {dep.status} ({dep.solver})")
        if dep.status == "infeasible":
            for line in dep.diagnosis.prechecks:
                print(f"    - {line}")
            for family, n in dep.diagnosis.families.items():
                print(f"    - {family}: {n} constraint(s) cannot be met together")
        else:
            print(f"  Recommended  : {dep.recommended}   (best baseline: {dep.best_baseline})")
            print(f"  Reliability claim: {dep.reliability_claim}")
            print(f"  {dep.rationale}")

    # Anti-patterns (produced by the Diagnose stage; fall back to the
    # Predict stage's own problems for callers who bundled diagnose=True
    # into predict() instead of running diagnose() separately)
    if result.diagnosis:
        problems = result.diagnosis.problems
    elif result.prediction:
        problems = result.prediction.problems
    else:
        problems = []
    if problems and not getattr(args, "quiet", False):
        display.print_subheader(f"Architectural Anti-Patterns ({len(problems)})")
        for p in problems:
            severity = getattr(p, "severity", "medium").upper()
            category = getattr(p, "category", "risk")
            color = display.severity_color(severity)
            print(f"  {display.colored(f'[{severity}]', color)} {category}: {p.name}")
            print(f"    {display.colored('↳', Colors.GRAY)} {p.description}")

    # Save generic result if output path provided and not just visualizing
    if args.output and not (args.visualize or args.all):
        result.save(args.output)
        display.print_success(f"Pipeline executed successfully. Result saved to {args.output}")
    else:
        display.print_success("Pipeline execution completed.")

    return 0

if __name__ == "__main__":
    sys.exit(main())