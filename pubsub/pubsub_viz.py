#!/usr/bin/env python3
"""
Visualization Module for the Publish-Subscribe System Model

This module provides functions for visualizing different aspects of
the publish-subscribe system model, including layers and critical components.
"""

import matplotlib.pyplot as plt
import networkx as nx

def visualize_layer(G, node_types, edge_types, title, layout=None, scale_factor=1.0):
    """
    Visualize a specific layer of the system graph
    
    Args:
        G: NetworkX graph object
        node_types: List of node types to include
        edge_types: List of edge types to include
        title: Title for the visualization
        layout: Optional pre-computed layout
        scale_factor: Factor to scale visualization elements (for larger graphs)
    """
    # Create a subgraph with selected node and edge types
    SG = nx.DiGraph()
    
    # Add nodes of specified types
    for node, attrs in G.nodes(data=True):
        if attrs.get('type') in node_types:
            SG.add_node(node, **attrs)
    
    # Add edges of specified types
    for u, v, attrs in G.edges(data=True):
        if attrs.get('type') in edge_types and u in SG.nodes and v in SG.nodes:
            SG.add_edge(u, v, **attrs)
    
    # Skip empty graphs
    if len(SG) == 0:
        print(f"No nodes in {title} layer")
        return
    
    # Set figure size based on graph size
    node_count = len(SG)
    fig_size_base = 10
    fig_size = max(8, min(fig_size_base, fig_size_base * scale_factor * (node_count / 20)))
    plt.figure(figsize=(fig_size, fig_size))
    
    # Use specified layout or determine best layout based on graph properties
    if layout is None:
        # For small graphs, spring layout works well
        if node_count < 50:
            pos = nx.spring_layout(SG, seed=42)
        # For larger graphs, use faster algorithms
        else:
            try:
                pos = nx.kamada_kawai_layout(SG)
            except:
                # Fallback to spring layout with reduced iterations
                pos = nx.spring_layout(SG, seed=42, iterations=50)
    else:
        pos = layout
    
    # Scale node size inversely with graph size
    node_size_base = 800
    node_size = max(100, node_size_base * (scale_factor * (50 / max(node_count, 1))))
    
    # Color nodes by type
    color_map = {
        'Application': 'lightblue',
        'Broker': 'orange',
        'Topic': 'lightgreen', 
        'Node': 'pink'
    }
    
    node_colors = [color_map[SG.nodes[node]['type']] for node in SG.nodes]
    
    # Draw nodes
    nx.draw_networkx_nodes(SG, pos, node_size=node_size, node_color=node_colors, alpha=0.8)
    
    # Draw edges with different colors by type
    edge_colors = {
        'RUNS_ON': 'gray',
        'PUBLISHES_TO': 'green',
        'SUBSCRIBES_TO': 'blue',
        'ROUTES': 'orange',
        'DEPENDS_ON': 'red',
        'CONNECTS_TO': 'black'
    }
    
    # Scale edge properties based on graph size
    edge_width = max(0.5, 1.5 * scale_factor)
    arrow_size = max(5, 15 * scale_factor)
    
    for edge_type in edge_types:
        edges = [(u, v) for u, v, attrs in SG.edges(data=True) if attrs.get('type') == edge_type]
        if edges:
            nx.draw_networkx_edges(SG, pos, edgelist=edges, 
                                  edge_color=edge_colors[edge_type], 
                                  width=edge_width, 
                                  arrowsize=arrow_size,
                                  connectionstyle='arc3,rad=0.1')
    
    # Adjust label size based on graph size
    font_size_base = 10
    font_size = max(6, font_size_base * scale_factor)
    
    # For large graphs, limit labels or use a different approach
    if node_count > 100:
        # Only label important nodes
        important_nodes = {}
        for node, attrs in SG.nodes(data=True):
            if attrs.get('type') in ['Broker', 'Node']:
                important_nodes[node] = node
            elif len(list(SG.predecessors(node))) > 3 or len(list(SG.successors(node))) > 3:
                important_nodes[node] = node
        
        nx.draw_networkx_labels(SG, pos, labels=important_nodes, font_size=font_size, font_weight='bold')
    else:
        # Label all nodes
        nx.draw_networkx_labels(SG, pos, font_size=font_size, font_weight='bold')
    
    # Create legend for node types
    node_patches = [plt.Line2D([0], [0], marker='o', color='w', markerfacecolor=color, markersize=10, label=node_type) 
                   for node_type, color in color_map.items() if node_type in node_types]
    
    # Create legend for edge types
    edge_patches = [plt.Line2D([0], [0], color=color, lw=2, label=edge_type) 
                   for edge_type, color in edge_colors.items() if edge_type in edge_types]
    
    # Add legends
    plt.legend(handles=node_patches + edge_patches, loc='upper right')
    
    plt.title(f"{title} ({node_count} nodes)")
    plt.axis('off')
    plt.tight_layout()
    
    # Save figure with size info
    filename = f"{title.replace(' ', '_').lower()}_nodes.png"
    plt.savefig(filename, dpi=300)
    #plt.show()

def visualize_critical_components(G, critical_components_analysis, config):
    """
    Create a special visualization highlighting critical components
    
    Args:
        G: NetworkX graph object
        critical_components_analysis: Results from critical component identification
        config: SystemConfig object
    """
    critical_components = critical_components_analysis['critical_components']
    
    # Skip if no critical components identified
    if not any(critical_components.values()):
        print("No critical components to visualize.")
        return
    
    # Extract all critical component nodes
    all_critical_nodes = []
    for component_type, components in critical_components.items():
        for component_info in components:
            all_critical_nodes.append(component_info['node'])
    
    # Create a subgraph with all nodes but highlight critical ones
    plt.figure(figsize=(14, 12))
    
    # Use spring layout with critical nodes at center for better visibility
    pos = nx.spring_layout(G, seed=42)
    
    # Adjust positions to put critical nodes more centrally
    center_x = sum(pos[node][0] for node in pos) / len(pos)
    center_y = sum(pos[node][1] for node in pos) / len(pos)
    
    for node in all_critical_nodes:
        if node in pos:
            # Move critical nodes 30% closer to center
            pos[node] = (
                pos[node][0] * 0.7 + center_x * 0.3,
                pos[node][1] * 0.7 + center_y * 0.3
            )
    
    # Draw non-critical nodes with less prominence
    non_critical_nodes = [node for node in G.nodes() if node not in all_critical_nodes]
    
    node_colors = []
    node_sizes = []
    
    # Prepare node colors and sizes
    for node in G.nodes():
        node_type = G.nodes[node].get('type')
        is_critical = node in all_critical_nodes
        
        # Assign color based on node type
        if node_type == 'Application':
            color = 'royalblue' if is_critical else 'lightblue'
        elif node_type == 'Broker':
            color = 'darkorange' if is_critical else 'orange'
        elif node_type == 'Topic':
            color = 'darkgreen' if is_critical else 'lightgreen'
        elif node_type == 'Node':
            color = 'darkred' if is_critical else 'pink'
        else:
            color = 'gray'
        
        # Assign size based on criticality
        size = 800 if is_critical else 300
        
        node_colors.append(color)
        node_sizes.append(size)
    
    # Draw all nodes
    nx.draw_networkx_nodes(G, pos, 
                          node_size=node_sizes,
                          node_color=node_colors,
                          alpha=0.8)
    
    # Draw edges
    edge_colors = {
        'RUNS_ON': 'gray',
        'PUBLISHES_TO': 'green',
        'SUBSCRIBES_TO': 'blue',
        'ROUTES': 'orange',
        'DEPENDS_ON': 'red',
        'CONNECTS_TO': 'black'
    }
    
    # Draw edges with reduced alpha for clarity
    for edge_type, color in edge_colors.items():
        edges = [(u, v) for u, v, attrs in G.edges(data=True) if attrs.get('type') == edge_type]
        
        # Highlight edges connected to critical components
        critical_edges = [(u, v) for u, v in edges if u in all_critical_nodes or v in all_critical_nodes]
        normal_edges = [(u, v) for u, v in edges if u not in all_critical_nodes and v not in all_critical_nodes]
        
        # Draw normal edges with less prominence
        if normal_edges:
            nx.draw_networkx_edges(G, pos, edgelist=normal_edges, 
                                  edge_color=color, width=0.7, alpha=0.3,
                                  connectionstyle='arc3,rad=0.1')
        
        # Draw critical edges with more prominence
        if critical_edges:
            nx.draw_networkx_edges(G, pos, edgelist=critical_edges, 
                                  edge_color=color, width=1.5, alpha=0.8,
                                  connectionstyle='arc3,rad=0.1')
    
    # Label critical nodes and limit others
    labels = {}
    for node in G.nodes():
        if node in all_critical_nodes:
            labels[node] = node  # Always label critical nodes
        elif len(G) <= 50:
            labels[node] = node  # Label all nodes for small graphs
        # For larger graphs, don't label non-critical nodes
    
    nx.draw_networkx_labels(G, pos, labels=labels, font_size=10, font_weight='bold')
    
    # Create legend
    legend_elements = [
        plt.Line2D([0], [0], marker='o', color='w', markerfacecolor='royalblue', markersize=15, label='Critical Application'),
        plt.Line2D([0], [0], marker='o', color='w', markerfacecolor='lightblue', markersize=10, label='Application'),
        plt.Line2D([0], [0], marker='o', color='w', markerfacecolor='darkorange', markersize=15, label='Critical Broker'),
        plt.Line2D([0], [0], marker='o', color='w', markerfacecolor='orange', markersize=10, label='Broker'),
        plt.Line2D([0], [0], marker='o', color='w', markerfacecolor='darkgreen', markersize=15, label='Critical Topic'),
        plt.Line2D([0], [0], marker='o', color='w', markerfacecolor='lightgreen', markersize=10, label='Topic'),
        plt.Line2D([0], [0], marker='o', color='w', markerfacecolor='darkred', markersize=15, label='Critical Node'),
        plt.Line2D([0], [0], marker='o', color='w', markerfacecolor='pink', markersize=10, label='Node')
    ]
    
    # Add edge type legend
    for edge_type, color in edge_colors.items():
        legend_elements.append(plt.Line2D([0], [0], color=color, lw=2, label=edge_type))
    
    plt.legend(handles=legend_elements, loc='upper right', fontsize='small')
    
    plt.title('Critical Component Analysis')
    plt.axis('off')
    plt.tight_layout()
    plt.savefig("critical_components_visualization.png", dpi=300)
    print("\nCritical components visualization saved as 'critical_components_visualization.png'")
    #plt.show()

def generate_visualizations(G, config, critical_components_analysis=None):
    """
    Generate standard visualizations for the graph model
    
    Args:
        G: NetworkX graph object
        config: SystemConfig object
        critical_components_analysis: Optional results of critical component identification
    """
    # Calculate appropriate scale factor for visualizations based on system size
    total_elements = config.num_brokers + config.num_nodes + config.num_applications + config.num_topics
    scale_factor = 1.0
    if total_elements > 50:
        scale_factor = 0.8
    if total_elements > 100:
        scale_factor = 0.6
    if total_elements > 200:
        scale_factor = 0.4
    
    # Generate standard layer visualizations
    print("\n=== Generating Visualizations ===")
    
    # Application Layer (Applications and their dependencies)
    visualize_layer(G, 
                   node_types=['Application'], 
                   edge_types=['DEPENDS_ON'],
                   title='Application Level Layer',
                   scale_factor=scale_factor)

    # Infrastructure Layer (Nodes and their connections)
    visualize_layer(G, 
                   node_types=['Node'], 
                   edge_types=['CONNECTS_TO'],
                   title='Infrastructure Level Layer',
                   scale_factor=scale_factor)

    # Application-Infrastructure Layer (Applications, Brokers, and their hosting)
    visualize_layer(G, 
                   node_types=['Application', 'Broker', 'Node'], 
                   edge_types=['RUNS_ON', 'CONNECTS_TO'],
                   title='Application-Infrastructure Layer',
                   scale_factor=scale_factor)

    # Messaging Layer (Applications, Topics, and Brokers)
    visualize_layer(G, 
                   node_types=['Application', 'Topic', 'Broker'], 
                   edge_types=['PUBLISHES_TO', 'SUBSCRIBES_TO', 'ROUTES'],
                   title='Messaging Layer',
                   scale_factor=scale_factor)

    # For smaller systems, also show complete view
    if total_elements < 100:
        # Complete System View (All elements)
        visualize_layer(G, 
                       node_types=['Application', 'Topic', 'Broker', 'Node'], 
                       edge_types=['PUBLISHES_TO', 'SUBSCRIBES_TO', 'ROUTES', 'RUNS_ON', 'DEPENDS_ON', 'CONNECTS_TO'],
                       title='Complete System View',
                       scale_factor=scale_factor * 0.8)  # Reduce scale further for complete view
    else:
        print("System too large for complete visualization - skipping complete view")
    
    # If critical component analysis is provided, visualize critical components
    if critical_components_analysis:
        visualize_critical_components(G, critical_components_analysis, config)
    
    print("Visualizations complete. All visualization files saved as PNG.")

if __name__ == "__main__":
    import sys
    
    from pubsub_config import SystemConfig, parse_args
    from pubsub_graph import create_complete_graph
    from pubsub_critical import identify_critical_components
    
    try:
        # Parse arguments
        config, args = parse_args()
        
        if args.no_viz:
            print("Visualization disabled (--no-viz flag used)")
            sys.exit(0)
            
        # Create graph
        print("Creating graph model...")
        G, components = create_complete_graph(config, use_neo4j=not args.no_neo4j)
        
        # Optional: Identify critical components
        critical_analysis = None
        try:
            print("\nIdentifying critical components for visualization...")
            critical_analysis = identify_critical_components(G, config)
        except Exception as e:
            print(f"Warning: Could not identify critical components: {e}")
            print("Proceeding with standard visualizations only")
        
        # Generate visualizations
        generate_visualizations(G, config, critical_analysis)
        
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
