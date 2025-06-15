#!/usr/bin/env python3
"""
Basic Analysis Module for the Publish-Subscribe System Model

This module provides functions for analyzing the basic structure
and properties of the publish-subscribe system model.
"""

import networkx as nx

def analyze_graph(G):
    """
    Perform basic analysis of the graph structure
    
    Args:
        G: NetworkX graph object
    """
    print("\n=== Graph Analysis ===")
    
    print(f"\nGraph Summary:")
    print(f"Number of nodes: {G.number_of_nodes()}")
    print(f"Number of edges: {G.number_of_edges()}")
    
    # Count node types
    node_types = {}
    for node, attrs in G.nodes(data=True):
        node_type = attrs.get('type')
        node_types[node_type] = node_types.get(node_type, 0) + 1
    
    print("\nNode Distribution:")
    for node_type, count in node_types.items():
        print(f"  {node_type}: {count}")
    
    # Count edge types
    edge_types = {}
    for u, v, attrs in G.edges(data=True):
        edge_type = attrs.get('type')
        edge_types[edge_type] = edge_types.get(edge_type, 0) + 1
    
    print("\nEdge Distribution:")
    for edge_type, count in edge_types.items():
        print(f"  {edge_type}: {count}")
    
    # Extract application subgraph
    app_nodes = [node for node, attrs in G.nodes(data=True) if attrs.get('type') == 'Application']
    app_graph = G.subgraph(app_nodes)
    
    # Analyze application dependencies
    print("\nApplication Dependency Analysis:")
    app_deps = {}
    for app in app_nodes:
        in_deps = list(G.predecessors(app))
        out_deps = list(G.successors(app))
        in_apps = [dep for dep in in_deps if G.nodes[dep].get('type') == 'Application']
        out_apps = [dep for dep in out_deps if G.nodes[dep].get('type') == 'Application']
        
        app_deps[app] = {
            'dependents': len(in_apps),
            'dependencies': len(out_apps)
        }
    
    # Find most depended-upon applications
    deps_sorted = sorted(app_deps.items(), key=lambda x: x[1]['dependents'], reverse=True)
    print("\nMost Central Applications (by number of dependents):")
    for app, stats in deps_sorted[:3]:
        print(f"  {app}: {stats['dependents']} dependents, {stats['dependencies']} dependencies")
    
    # Analyze broker workload
    print("\nBroker Workload Analysis:")
    broker_load = {}
    broker_nodes = [node for node, attrs in G.nodes(data=True) if attrs.get('type') == 'Broker']
    
    for broker in broker_nodes:
        # Count routed topics
        routed_topics = [node for node in G.successors(broker) 
                         if G.nodes[node].get('type') == 'Topic' and G[broker][node].get('type') == 'ROUTES']
        
        # Get dependent applications
        dependent_apps = [node for node in G.predecessors(broker) 
                         if G.nodes[node].get('type') == 'Application' and G[node][broker].get('type') == 'DEPENDS_ON']
        
        broker_load[broker] = {
            'topics': len(routed_topics),
            'applications': len(dependent_apps)
        }
    
    for broker, stats in broker_load.items():
        print(f"  {broker}: Routes {stats['topics']} topics, Serves {stats['applications']} applications")
    
    # Analyze node utilization
    print("\nNode Utilization Analysis:")
    node_util = {}
    infra_nodes = [node for node, attrs in G.nodes(data=True) if attrs.get('type') == 'Node']
    
    for node in infra_nodes:
        # Count hosted applications
        hosted_apps = [n for n in G.predecessors(node) 
                      if G.nodes[n].get('type') == 'Application' and G[n][node].get('type') == 'RUNS_ON']
        
        # Count hosted brokers
        hosted_brokers = [n for n in G.predecessors(node) 
                         if G.nodes[n].get('type') == 'Broker' and G[n][node].get('type') == 'RUNS_ON']
        
        node_util[node] = {
            'applications': len(hosted_apps),
            'brokers': len(hosted_brokers),
            'total': len(hosted_apps) + len(hosted_brokers)
        }
    
    for node, stats in node_util.items():
        print(f"  {node}: Hosts {stats['applications']} applications, {stats['brokers']} brokers (Total: {stats['total']})")
    
    # Find most central nodes in the infrastructure
    infra_graph = G.subgraph(infra_nodes)
    if infra_graph.number_of_nodes() > 0:
        try:
            betweenness = nx.betweenness_centrality(infra_graph)
            sorted_betweenness = sorted(betweenness.items(), key=lambda x: x[1], reverse=True)
            
            print("\nInfrastructure Centrality (Most Critical Nodes):")
            for node, score in sorted_betweenness:
                print(f"  {node}: {score:.4f}")
        except:
            print("\nCould not compute infrastructure centrality (graph may be disconnected)")
    
    # Identify potential bottlenecks
    print("\nPotential System Bottlenecks:")
    
    # Topics with many publishers and subscribers
    topic_load = {}
    for node, attrs in G.nodes(data=True):
        if attrs.get('type') == 'Topic':
            publishers = [n for n in G.predecessors(node) 
                         if G.nodes[n].get('type') == 'Application' and G[n][node].get('type') == 'PUBLISHES_TO']
            
            subscribers = [n for n in G.predecessors(node) 
                          if G.nodes[n].get('type') == 'Application' and G[n][node].get('type') == 'SUBSCRIBES_TO']
            
            topic_load[node] = {
                'publishers': len(publishers),
                'subscribers': len(subscribers),
                'total': len(publishers) + len(subscribers)
            }
    
    # Sort topics by total connections
    sorted_topics = sorted(topic_load.items(), key=lambda x: x[1]['total'], reverse=True)
    for topic, stats in sorted_topics[:3]:
        print(f"  Topic: {topic} - {stats['publishers']} publishers, {stats['subscribers']} subscribers")
    
    # Brokers with high load
    sorted_brokers = sorted(broker_load.items(), key=lambda x: x[1]['topics'], reverse=True)
    for broker, stats in sorted_brokers:
        topic_threshold = max(2, node_types.get('Topic', 0) // 5)  # 20% of topics
        if stats['topics'] > topic_threshold:
            print(f"  Broker: {broker} - High routing load with {stats['topics']} topics")
    
    # Overloaded nodes
    sorted_nodes = sorted(node_util.items(), key=lambda x: x[1]['total'], reverse=True)
    for node, stats in sorted_nodes:
        service_threshold = max(2, (node_types.get('Application', 0) + node_types.get('Broker', 0)) // 4)  # 25% of services
        if stats['total'] > service_threshold:
            print(f"  Node: {node} - Potential overload with {stats['total']} total services")

if __name__ == "__main__":
    import sys
    
    from pubsub_config import SystemConfig, parse_args
    from pubsub_graph import create_complete_graph
    
    try:
        # Parse arguments
        config, args = parse_args()
        
        # Create graph
        print("Creating graph model...")
        G, components = create_complete_graph(config, use_neo4j=not args.no_neo4j)
        
        # Run analysis
        analyze_graph(G)
        
        print("\nBasic analysis complete.")
        
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)