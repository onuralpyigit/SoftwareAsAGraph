#!/usr/bin/env python3
"""
Import/Export Module for the Publish-Subscribe System Model

This module provides functions for importing and exporting graph data
to/from CSV files, enabling data persistence and exchange.
"""

import os
import csv
import networkx as nx

def export_graph_to_csv(G, export_dir="graph_data"):
    """
    Export graph data to CSV files
    
    Args:
        G: NetworkX graph object
        export_dir: Directory to store CSV files
        
    Returns:
        tuple: (node_file, edge_file) - Paths to created CSV files
    """
    # Create directory if it doesn't exist
    if not os.path.exists(export_dir):
        os.makedirs(export_dir)
    
    # Define file paths
    node_file = os.path.join(export_dir, "nodes.csv")
    edge_file = os.path.join(export_dir, "edges.csv")
    
    # Export nodes
    with open(node_file, 'w', newline='') as f:
        writer = csv.writer(f)
        # Write header
        writer.writerow(['id', 'type', 'name', 'properties'])
        
        # Write node data
        for node, attrs in G.nodes(data=True):
            # Extract node type and name
            node_type = attrs.get('type', '')
            node_name = node
            
            # Collect other properties
            properties = {}
            for key, value in attrs.items():
                if key not in ['type', 'name']:
                    properties[key] = value
            
            writer.writerow([node, node_type, node_name, str(properties)])
    
    # Export edges
    with open(edge_file, 'w', newline='') as f:
        writer = csv.writer(f)
        # Write header
        writer.writerow(['source', 'target', 'type', 'properties'])
        
        # Write edge data
        for source, target, attrs in G.edges(data=True):
            # Extract edge type
            edge_type = attrs.get('type', '')
            
            # Collect other properties
            properties = {}
            for key, value in attrs.items():
                if key != 'type':
                    properties[key] = value
            
            writer.writerow([source, target, edge_type, str(properties)])
    
    print(f"Graph exported to CSV files:")
    print(f"  Nodes: {node_file}")
    print(f"  Edges: {edge_file}")
    
    return node_file, edge_file

def import_graph_from_csv(node_file, edge_file):
    """
    Import graph data from CSV files
    
    Args:
        node_file: Path to nodes CSV file
        edge_file: Path to edges CSV file
        
    Returns:
        DiGraph: NetworkX directed graph object
    """
    # Create a new directed graph
    G = nx.DiGraph()
    
    # Import nodes
    with open(node_file, 'r', newline='') as f:
        reader = csv.reader(f)
        # Skip header
        next(reader)
        
        # Read node data
        for row in reader:
            if len(row) >= 3:
                node_id = row[0]
                node_type = row[1]
                node_name = row[2]
                
                # Create node with type attribute
                G.add_node(node_id, type=node_type, name=node_name)
                
                # Add other properties if available
                if len(row) >= 4:
                    # Parse properties string if present
                    try:
                        props_str = row[3].replace("'", '"')  # Replace single quotes with double quotes
                        # Simple parsing - this can be enhanced for more complex properties
                        if props_str.startswith('{') and props_str.endswith('}'):
                            pairs = props_str[1:-1].split(',')
                            for pair in pairs:
                                if ':' in pair:
                                    key, value = pair.split(':', 1)
                                    key = key.strip()
                                    value = value.strip()
                                    if key != 'type' and key != 'name':
                                        G.nodes[node_id][key] = value
                    except:
                        # If parsing fails, continue without additional properties
                        pass
    
    # Import edges
    with open(edge_file, 'r', newline='') as f:
        reader = csv.reader(f)
        # Skip header
        next(reader)
        
        # Read edge data
        for row in reader:
            if len(row) >= 3:
                source = row[0]
                target = row[1]
                edge_type = row[2]
                
                # Add edge with type attribute
                G.add_edge(source, target, type=edge_type)
                
                # Add other properties if available
                if len(row) >= 4:
                    # Parse properties string if present
                    try:
                        props_str = row[3].replace("'", '"')  # Replace single quotes with double quotes
                        # Simple parsing - this can be enhanced for more complex properties
                        if props_str.startswith('{') and props_str.endswith('}'):
                            pairs = props_str[1:-1].split(',')
                            for pair in pairs:
                                if ':' in pair:
                                    key, value = pair.split(':', 1)
                                    key = key.strip()
                                    value = value.strip()
                                    if key != 'type':
                                        G[source][target][key] = value
                    except:
                        # If parsing fails, continue without additional properties
                        pass
    
    print(f"Graph imported from CSV files:")
    print(f"  Nodes: {G.number_of_nodes()} (from {node_file})")
    print(f"  Edges: {G.number_of_edges()} (from {edge_file})")
    
    return G

def export_component_metrics_to_csv(metrics, export_dir="graph_data"):
    """
    Export component metrics to CSV files
    
    Args:
        metrics: Dictionary of component metrics
        export_dir: Directory to store CSV files
        
    Returns:
        list: Paths to created CSV files
    """
    # Create directory if it doesn't exist
    if not os.path.exists(export_dir):
        os.makedirs(export_dir)
    
    files = []
    
    # Export each metric type to its own CSV file
    for metric_type, metric_data in metrics.items():
        if not metric_data:
            continue
            
        file_path = os.path.join(export_dir, f"{metric_type}.csv")
        files.append(file_path)
        
        with open(file_path, 'w', newline='') as f:
            writer = csv.writer(f)
            
            # Get all possible metrics from the first entry
            first_item = next(iter(metric_data.values()))
            if isinstance(first_item, dict):
                headers = ['component_id'] + list(first_item.keys())
            else:
                headers = ['component_id', 'value']
            
            # Write header
            writer.writerow(headers)
            
            # Write data
            for component_id, data in metric_data.items():
                if isinstance(data, dict):
                    row = [component_id] + [data.get(h, '') for h in headers[1:]]
                else:
                    row = [component_id, data]
                writer.writerow(row)
    
    print(f"Component metrics exported to CSV files in {export_dir}")
    return files

def export_critical_components_to_csv(critical_components, export_dir="graph_data"):
    """
    Export critical components to CSV file
    
    Args:
        critical_components: Dictionary of critical components by type
        export_dir: Directory to store CSV file
        
    Returns:
        str: Path to created CSV file
    """
    # Create directory if it doesn't exist
    if not os.path.exists(export_dir):
        os.makedirs(export_dir)
    
    file_path = os.path.join(export_dir, "critical_components.csv")
    
    with open(file_path, 'w', newline='') as f:
        writer = csv.writer(f)
        
        # Write header
        writer.writerow(['component_type', 'component_id', 'reasons', 'metrics'])
        
        # Write critical component data
        for component_type, components in critical_components.items():
            for component_info in components:
                component_id = component_info['node']
                reasons = '; '.join(component_info.get('reasons', []))
                metrics = str(component_info.get('metrics', {}))
                
                writer.writerow([component_type, component_id, reasons, metrics])
    
    print(f"Critical components exported to {file_path}")
    return file_path

def export_recommendations_to_csv(recommendations, export_dir="graph_data"):
    """
    Export recommendations to CSV file
    
    Args:
        recommendations: Dictionary of recommendations by category
        export_dir: Directory to store CSV file
        
    Returns:
        str: Path to created CSV file
    """
    # Create directory if it doesn't exist
    if not os.path.exists(export_dir):
        os.makedirs(export_dir)
    
    file_path = os.path.join(export_dir, "recommendations.csv")
    
    with open(file_path, 'w', newline='') as f:
        writer = csv.writer(f)
        
        # Write header
        writer.writerow(['category', 'recommendation'])
        
        # Write recommendation data
        for category, recs in recommendations.items():
            for recommendation in recs:
                writer.writerow([category, recommendation])
    
    print(f"Recommendations exported to {file_path}")
    return file_path

if __name__ == "__main__":
    import sys
    
    from pubsub_config import SystemConfig, parse_args
    from pubsub_graph import create_complete_graph
    
    try:
        # Parse arguments
        config, args = parse_args()
        
        # Check for import mode
        if len(sys.argv) > 1 and sys.argv[1] == "import" and len(sys.argv) >= 4:
            # Import mode with node and edge files specified
            node_file = sys.argv[2]
            edge_file = sys.argv[3]
            
            print(f"Importing graph from {node_file} and {edge_file}...")
            G = import_graph_from_csv(node_file, edge_file)
            print(f"Successfully imported graph with {G.number_of_nodes()} nodes and {G.number_of_edges()} edges")
            
        else:
            # Export mode
            print("Creating graph model for export...")
            G, components = create_complete_graph(config, use_neo4j=False)
            
            # Export to CSV
            export_dir = "graph_data"
            if len(sys.argv) > 1:
                export_dir = sys.argv[1]
                
            node_file, edge_file = export_graph_to_csv(G, export_dir)
            print(f"Successfully exported graph with {G.number_of_nodes()} nodes and {G.number_of_edges()} edges")
        
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
