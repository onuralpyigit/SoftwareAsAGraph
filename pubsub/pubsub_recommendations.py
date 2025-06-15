#!/usr/bin/env python3
"""
Improvement Recommendations Module for the Publish-Subscribe System Model

This module provides functions for generating targeted recommendations
for improving system resilience based on critical component analysis.
"""

import networkx as nx
import numpy as np

def generate_improvement_recommendations(G, critical_components_analysis, config):
    """
    Generate targeted recommendations for improving system resilience
    based on critical component analysis
    
    Args:
        G: NetworkX graph object
        critical_components_analysis: Results from critical component identification
        config: SystemConfig object
        
    Returns:
        dict: Dictionary of categorized recommendations
    """
    critical_components = critical_components_analysis['critical_components']
    component_metrics = critical_components_analysis['component_metrics']
    thresholds = critical_components_analysis['thresholds']
    
    print("\n=== System Improvement Recommendations ===")
    
    if not any(critical_components.values()):
        print("No critical components were identified based on current thresholds.")
        print("This suggests your system may already have good resilience characteristics.")
        print("Consider the following general recommendations for distributed pub-sub systems:")
        print("  1. Implement broker clustering for high availability")
        print("  2. Distribute application deployments across multiple nodes")
        print("  3. Consider message persistence for critical topics")
        return {}
    
    # Organize recommendations by type
    recommendations = {
        'redundancy': [],
        'load_balancing': [],
        'decoupling': [],
        'monitoring': []
    }
    
    # Count total components by type for reference
    total_components = {
        'brokers': sum(1 for _, attrs in G.nodes(data=True) if attrs.get('type') == 'Broker'),
        'nodes': sum(1 for _, attrs in G.nodes(data=True) if attrs.get('type') == 'Node'),
        'applications': sum(1 for _, attrs in G.nodes(data=True) if attrs.get('type') == 'Application'),
        'topics': sum(1 for _, attrs in G.nodes(data=True) if attrs.get('type') == 'Topic')
    }
    
    # === BROKER RECOMMENDATIONS ===
    if critical_components['broker']:
        broker_issue_count = len(critical_components['broker'])
        
        # Check for broker concentration issues
        if broker_issue_count >= max(1, config.num_brokers // 2):
            recommendations['load_balancing'].append(
                "Significant broker imbalance detected. Consider redistributing topics across brokers more evenly."
            )
        
        # Check for router redundancy issues
        for broker_info in critical_components['broker']:
            broker = broker_info['node']
            metrics = broker_info['metrics']
            
            # If broker routes many topics, suggest redundancy
            if metrics['topic_coverage'] > 0.3:  # 30% of topics
                recommendations['redundancy'].append(
                    f"Implement redundant routing for topics managed by {broker} " +
                    f"({metrics['topic_count']} topics, {metrics['topic_coverage']:.0%} of system)"
                )
            
            # If broker impacts many applications, suggest clustering
            if metrics['app_impact'] > 0.3:  # 30% of applications
                recommendations['redundancy'].append(
                    f"Implement broker clustering for {broker} to reduce single point of failure " +
                    f"(impacts {metrics['impacted_apps']} applications)"
                )
    
    # === NODE RECOMMENDATIONS ===
    if critical_components['node']:
        node_issue_count = len(critical_components['node'])
        
        # Check for node concentration issues
        if node_issue_count >= max(1, config.num_nodes // 3):
            recommendations['load_balancing'].append(
                "Multiple critical infrastructure nodes detected. Consider redistributing services more evenly."
            )
        
        # Generate specific recommendations
        for node_info in critical_components['node']:
            node = node_info['node']
            metrics = node_info['metrics']
            
            # If node hosts many services, suggest distribution
            if metrics['service_density'] > 0.25:  # 25% of services
                recommendations['load_balancing'].append(
                    f"Redistribute services from overloaded node {node} " +
                    f"({metrics['service_count']} services, {metrics['service_density']:.0%} of system)"
                )
            
            # If node hosts critical brokers, suggest separation
            if 'broker_hosts' in metrics and metrics['broker_hosts'] > 0:
                recommendations['redundancy'].append(
                    f"Separate critical brokers from node {node} onto dedicated infrastructure " +
                    f"(currently hosts {metrics['broker_hosts']} brokers)"
                )
    
    # === APPLICATION RECOMMENDATIONS ===
    if critical_components['application']:
        # Check application dependency patterns
        for app_info in critical_components['application']:
            app = app_info['node']
            metrics = app_info['metrics']
            
            # If application has many dependents, suggest decoupling
            if metrics['dependency_ratio'] > 0.2:  # 20% of applications
                recommendations['decoupling'].append(
                    f"Reduce dependencies on {app} by implementing mediator topics " +
                    f"({metrics['dependent_count']} dependents)"
                )
            
            # If application is sole publisher for many topics, suggest redundancy
            if 'exclusive_topics' in metrics and metrics['exclusive_topics'] > 2:
                recommendations['redundancy'].append(
                    f"Implement redundant publishers for topics exclusively published by {app} " +
                    f"({metrics['exclusive_topics']} exclusive topics)"
                )
    
    # === TOPIC RECOMMENDATIONS ===
    if critical_components['topic']:
        # Check topic subscription patterns
        for topic_info in critical_components['topic']:
            topic = topic_info['node']
            metrics = topic_info['metrics']
            
            # If topic has many subscribers, suggest partitioning
            if metrics['subscriber_breadth'] > 0.3:  # 30% of applications
                recommendations['decoupling'].append(
                    f"Consider partitioning high-subscription topic {topic} " +
                    f"({metrics['subscriber_count']} subscribers)"
                )
                
                # Also suggest monitoring
                recommendations['monitoring'].append(
                    f"Implement specialized monitoring for high-impact topic {topic}"
                )
    
    # Add general recommendations based on system size
    if config.num_brokers < 3 and total_components['brokers'] < 3:
        recommendations['redundancy'].append(
            "Consider increasing broker count for improved resilience (minimum 3 recommended)"
        )
    
    # Analyze infrastructure connectivity
    if config.num_nodes > 2:
        # Create node-only subgraph
        node_subgraph = nx.Graph()
        node_nodes = [n for n, attrs in G.nodes(data=True) if attrs.get('type') == 'Node']
        for n in node_nodes:
            node_subgraph.add_node(n)
        
        for u, v, attrs in G.edges(data=True):
            if (u in node_nodes and v in node_nodes and 
                attrs.get('type') == 'CONNECTS_TO'):
                node_subgraph.add_edge(u, v)
        
        # Check connectivity
        try:
            # If network has articulation points, suggest redundant connections
            articulation_points = list(nx.articulation_points(node_subgraph))
            if articulation_points:
                recommendations['redundancy'].append(
                    f"Add redundant network connections to improve resilience against infrastructure failures " +
                    f"({len(articulation_points)} critical connection points identified)"
                )
        except nx.NetworkXError:
            # Graph may not be connected
            recommendations['redundancy'].append(
                "Network topology analysis indicates potential disconnection points. " +
                "Review infrastructure connectivity."
            )
    
    # Print recommendations by category
    # Redundancy recommendations
    if recommendations['redundancy']:
        print("\n1. Redundancy Recommendations:")
        for i, rec in enumerate(recommendations['redundancy']):
            print(f"  {i+1}. {rec}")
    
    # Load balancing recommendations
    if recommendations['load_balancing']:
        print("\n2. Load Balancing Recommendations:")
        for i, rec in enumerate(recommendations['load_balancing']):
            print(f"  {i+1}. {rec}")
    
    # Decoupling recommendations
    if recommendations['decoupling']:
        print("\n3. Decoupling Recommendations:")
        for i, rec in enumerate(recommendations['decoupling']):
            print(f"  {i+1}. {rec}")
    
    # Monitoring recommendations
    if recommendations['monitoring']:
        print("\n4. Monitoring Recommendations:")
        for i, rec in enumerate(recommendations['monitoring']):
            print(f"  {i+1}. {rec}")
    
    # If no specific recommendations were generated
    if not any(recommendations.values()):
        print("\nNo specific improvement recommendations were generated based on the analysis.")
        print("Consider implementing general resilience best practices for distributed systems:")
        print("  1. Redundancy for critical services")
        print("  2. Load balancing across brokers and nodes")
        print("  3. Decoupling of tightly-coupled application dependencies")
        print("  4. Advanced monitoring and alerting for critical components")
    
    # Return recommendations for potential further use
    return recommendations

def analyze_load_balance(broker_connections, node_loads, config):
    """
    Analyze load balancing across system components with adaptive thresholds
    
    Args:
        broker_connections: Dictionary mapping brokers to their topic counts
        node_loads: Dictionary mapping nodes to their service counts
        config: SystemConfig object
        
    Returns:
        tuple: (broker_cv, node_cv) - coefficients of variation
    """
    broker_cv = 0
    node_cv = 0
    
    # Analyze broker load balance if we have more than one broker
    if len(broker_connections) > 1:
        broker_loads = list(broker_connections.values())
        broker_load_std = np.std(broker_loads)
        broker_load_mean = np.mean(broker_loads)
        broker_cv = broker_load_std / broker_load_mean if broker_load_mean > 0 else 0
        
        # Adjust threshold based on system size
        # Smaller systems may naturally have more variation
        imbalance_threshold = 0.3
        if config.num_brokers < 5:
            imbalance_threshold = 0.4
        
        if broker_cv > imbalance_threshold:
            print("  - Broker load is imbalanced. Consider redistributing topics among brokers:")
            for broker, count in broker_connections.items():
                print(f"    * {broker}: {count} topics")
    
    # Check node utilization balance if we have more than one node
    if len(node_loads) > 1:
        node_load_values = list(node_loads.values())
        node_load_std = np.std(node_load_values)
        node_load_mean = np.mean(node_load_values)
        node_cv = node_load_std / node_load_mean if node_load_mean > 0 else 0
        
        # Adjust threshold based on system size
        imbalance_threshold = 0.3
        if config.num_nodes < 5:
            imbalance_threshold = 0.4
        
        if node_cv > imbalance_threshold:
            print("  - Node utilization is imbalanced. Consider redistributing services:")
            for node, count in node_loads.items():
                print(f"    * {node}: {count} services")
    
    return broker_cv, node_cv

if __name__ == "__main__":
    import sys
    
    from pubsub_config import SystemConfig, parse_args
    from pubsub_graph import create_complete_graph
    from pubsub_critical import identify_critical_components
    
    try:
        # Parse arguments
        config, args = parse_args()
        
        # Create graph
        print("Creating graph model...")
        G, components = create_complete_graph(config, use_neo4j=not args.no_neo4j)
        
        # Identify critical components
        print("\nIdentifying critical components...")
        critical_analysis = identify_critical_components(G, config)
        
        # Generate recommendations
        recommendations = generate_improvement_recommendations(G, critical_analysis, config)
        
        print("\nRecommendation generation complete.")
        
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
