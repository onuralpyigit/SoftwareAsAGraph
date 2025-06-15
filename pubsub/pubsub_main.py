#!/usr/bin/env python3
"""
Main Program File for the Publish-Subscribe System Model

This module orchestrates the full analysis process by creating the graph model,
identifying critical components, analyzing failures, and generating recommendations.
"""

import sys
import time
import argparse
import os

def run_complete_analysis(config, args):
    """
    Run complete analysis workflow
    
    Args:
        config: SystemConfig object
        args: Parsed command line arguments
    """
    from pubsub_graph import create_complete_graph
    from pubsub_analysis import analyze_graph
    from pubsub_critical import identify_critical_components, print_critical_summary, get_simulation_targets
    from pubsub_failure import run_failure_simulations
    from pubsub_recommendations import generate_improvement_recommendations
    from pubsub_viz import generate_visualizations
    from pubsub_io import export_graph_to_csv, export_component_metrics_to_csv
    from pubsub_io import export_critical_components_to_csv, export_recommendations_to_csv
    
    start_time = time.time()
    
    # Check if we should import from CSV
    if args.import_csv:
        from pubsub_io import import_graph_from_csv
        if not os.path.exists(args.nodes_csv) or not os.path.exists(args.edges_csv):
            print(f"Error: Specified CSV files not found")
            sys.exit(1)
            
        print(f"Importing graph from CSV files...")
        G = import_graph_from_csv(args.nodes_csv, args.edges_csv)
    else:
        # Create graph model
        print("=== Creating System Model ===")
        G, components = create_complete_graph(config, use_neo4j=not args.no_neo4j)
    
    # Run basic analysis
    analyze_graph(G)
    
    # Identify critical components
    print("\n=== Identifying Critical Components ===")
    critical_analysis = identify_critical_components(G, config)
    print_critical_summary(critical_analysis)
    
    # Prepare for failure simulations
    simulation_targets = get_simulation_targets(critical_analysis)
    
    # Run failure simulations
    print("\n=== Running Failure Simulations ===")
    simulation_results = run_failure_simulations(G, simulation_targets)
    
    # Generate improvement recommendations
    print("\n=== Generating Recommendations ===")
    recommendations = generate_improvement_recommendations(G, critical_analysis, config)
    
    # Create visualizations
    if not args.no_viz:
        print("\n=== Creating Visualizations ===")
        generate_visualizations(G, config, critical_analysis)
        
        # Generate web-based visualization if requested
        if args.web_viz:
            from pubsub_web_viz import generate_web_visualization_with_analysis
            web_dir = args.web_dir if args.web_dir else "web_viz"
            print(f"\n=== Creating Web-based Visualization ({web_dir}) ===")
            html_path = generate_web_visualization_with_analysis(
                G, 
                critical_analysis, 
                simulation_results, 
                recommendations, 
                web_dir
            )
    else:
        print("\nSkipping visualizations (--no-viz flag used)")

    
    # Export data if requested
    if args.export_csv:
        export_dir = args.export_dir
        print(f"\n=== Exporting Data to CSV ({export_dir}) ===")
        
        # Export graph structure
        node_file, edge_file = export_graph_to_csv(G, export_dir)
        
        # Export component metrics
        metrics_files = export_component_metrics_to_csv(critical_analysis['component_metrics'], export_dir)
        
        # Export critical components
        critical_file = export_critical_components_to_csv(critical_analysis['critical_components'], export_dir)
        
        # Export recommendations
        if recommendations:
            rec_file = export_recommendations_to_csv(recommendations, export_dir)
    
    end_time = time.time()
    elapsed_time = end_time - start_time
    
    print(f"\n=== Analysis Complete ({elapsed_time:.2f} seconds) ===")
    print(f"Created model with {G.number_of_nodes()} nodes and {G.number_of_edges()} edges")
    print(f"Identified {sum(len(components) for components in critical_analysis['critical_components'].values())} critical components")
    
    return G, critical_analysis, recommendations

def parse_extended_args():
    """
    Parse command line arguments with additional analysis options
    
    Returns:
        tuple: (config, args) - Configuration object and parsed arguments
    """
    from pubsub_config import SystemConfig
    
    parser = argparse.ArgumentParser(description='Publish-Subscribe System Graph Model Analysis')
    parser.add_argument('--brokers', type=int, default=2, help='Number of brokers (default: 2)')
    parser.add_argument('--nodes', type=int, default=4, help='Number of nodes/machines (default: 4)')
    parser.add_argument('--apps', type=int, default=10, help='Number of applications (default: 10)')
    parser.add_argument('--topics', type=int, default=25, help='Number of topics (default: 25)')
    parser.add_argument('--no-neo4j', action='store_true', help='Skip Neo4j database operations')
    parser.add_argument('--no-viz', action='store_true', help='Skip visualizations')
    
    # Web visualization options
    parser.add_argument('--web-viz', action='store_true', help='Generate web-based visualization')
    parser.add_argument('--web-dir', type=str, help='Directory for web visualization files (default: web_viz)')
    
    # CSV import/export options
    parser.add_argument('--export-csv', action='store_true', help='Export graph and analysis data to CSV files')
    parser.add_argument('--export-dir', type=str, default='graph_data', help='Directory for exported CSV files')
    parser.add_argument('--import-csv', action='store_true', help='Import graph from CSV files')
    parser.add_argument('--nodes-csv', type=str, default='graph_data/nodes.csv', help='CSV file containing node data')
    parser.add_argument('--edges-csv', type=str, default='graph_data/edges.csv', help='CSV file containing edge data')
    
    # Analysis modules selection
    parser.add_argument('--basic-only', action='store_true', help='Run only basic analysis')
    parser.add_argument('--critical-only', action='store_true', help='Run only critical component identification')
    parser.add_argument('--failure-only', action='store_true', help='Run only failure simulation')
    parser.add_argument('--recommendations-only', action='store_true', help='Run only recommendations generation')
    parser.add_argument('--viz-only', action='store_true', help='Run only visualizations')
    parser.add_argument('--web-viz-only', action='store_true', help='Generate only web-based visualization')
    
    args = parser.parse_args()
    
    config = SystemConfig(
        num_brokers=args.brokers,
        num_nodes=args.nodes,
        num_applications=args.apps,
        num_topics=args.topics
    )
    
    return config, args

def run_module(module_name, config, args):
    """
    Run a specific analysis module
    
    Args:
        module_name: Name of the module to run
        config: SystemConfig object
        args: Parsed command line arguments
    """
    from pubsub_graph import create_complete_graph
    from pubsub_io import import_graph_from_csv
    
    # Check if we should import from CSV
    if args.import_csv:
        if not os.path.exists(args.nodes_csv) or not os.path.exists(args.edges_csv):
            print(f"Error: Specified CSV files not found")
            sys.exit(1)
            
        print(f"Importing graph from CSV files...")
        G = import_graph_from_csv(args.nodes_csv, args.edges_csv)
    else:
        # Create graph model (required for all modules)
        print("=== Creating System Model ===")
        G, components = create_complete_graph(config, use_neo4j=not args.no_neo4j)
    
    if module_name == 'basic':
        from pubsub.pubsub_analysis import analyze_graph
        analyze_graph(G)
        
    elif module_name == 'critical':
        from pubsub_critical import identify_critical_components, print_critical_summary
        critical_analysis = identify_critical_components(G, config)
        print_critical_summary(critical_analysis)
        
        # Export if requested
        if args.export_csv:
            from pubsub_io import export_component_metrics_to_csv, export_critical_components_to_csv
            export_component_metrics_to_csv(critical_analysis['component_metrics'], args.export_dir)
            export_critical_components_to_csv(critical_analysis['critical_components'], args.export_dir)
        
    elif module_name == 'failure':
        from pubsub_critical import identify_critical_components, get_simulation_targets
        from pubsub_failure import run_failure_simulations
        critical_analysis = identify_critical_components(G, config)
        simulation_targets = get_simulation_targets(critical_analysis)
        run_failure_simulations(G, simulation_targets)
        
    elif module_name == 'recommendations':
        from pubsub_critical import identify_critical_components
        from pubsub_recommendations import generate_improvement_recommendations
        critical_analysis = identify_critical_components(G, config)
        recommendations = generate_improvement_recommendations(G, critical_analysis, config)
        
        # Export if requested
        if args.export_csv and recommendations:
            from pubsub_io import export_recommendations_to_csv
            export_recommendations_to_csv(recommendations, args.export_dir)
        
    elif module_name == 'viz':
        from pubsub_critical import identify_critical_components
        from pubsub_viz import generate_visualizations
        critical_analysis = identify_critical_components(G, config)
        generate_visualizations(G, config, critical_analysis)
    
    elif module_name == 'web_viz':
        from pubsub_critical import identify_critical_components
        from pubsub_web_viz import generate_web_visualization_with_analysis, prepare_simulation_data
        
        # Identify critical components
        critical_analysis = identify_critical_components(G, config)
        
        # Prepare simulation data
        simulation_results = prepare_simulation_data(G, critical_analysis['critical_components'])
        
        # Generate recommendations
        recommendations = generate_improvement_recommendations(G, critical_analysis, config)
        
        # Generate web visualization
        web_dir = args.web_dir if args.web_dir else "web_viz"
        html_path = generate_web_visualization_with_analysis(
            G, 
            critical_analysis, 
            simulation_results, 
            recommendations, 
            web_dir
        )

    
    # Export graph if requested
    if args.export_csv:
        from pubsub_io import export_graph_to_csv
        export_graph_to_csv(G, args.export_dir)
    
    print(f"\n=== {module_name.capitalize()} Analysis Complete ===")

def main():
    """Main entry point for the program"""
    try:
        # Parse arguments
        config, args = parse_extended_args()
        
        # Check if a specific module was requested
        if args.basic_only:
            run_module('basic', config, args)
        elif args.critical_only:
            run_module('critical', config, args)
        elif args.failure_only:
            run_module('failure', config, args)
        elif args.recommendations_only:
            run_module('recommendations', config, args)
        elif args.viz_only:
            run_module('viz', config, args)
        elif args.web_viz_only:
            run_module('web_viz', config, args)
        else:
            # Run complete analysis
            run_complete_analysis(config, args)
        
    except KeyboardInterrupt:
        print("\nAnalysis interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()