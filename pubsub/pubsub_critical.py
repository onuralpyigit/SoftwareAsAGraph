#!/usr/bin/env python3
"""
Critical Component Identification Module for the Publish-Subscribe System Model

This module provides functions for identifying critical components in the system
using adaptive thresholds and multiple identification rules.
"""

import networkx as nx
from pubsub_threshold import CriticalityThresholds

def identify_critical_components(G, config):
    """
    Identify the critical components in the system based on adaptive thresholds
    
    Args:
        G: NetworkX graph object
        config: SystemConfig object
        
    Returns:
        dict: Dictionary with critical component information and component metrics
    """
    # Calculate adaptive thresholds based on system configuration
    thresholds = CriticalityThresholds(config)
    
    # Dictionary to store critical components by type
    critical_components = {
        'broker': [],
        'node': [],
        'application': [],
        'topic': []
    }
    
    # Dictionary to store all component metrics
    component_metrics = {
        'broker_connections': {},
        'node_loads': {},
        'app_dependencies': {},
        'app_exclusive_topics': {},
        'topic_subscribers': {}
    }
    
    # Count total components by type
    total_components = {
        'brokers': sum(1 for _, attrs in G.nodes(data=True) if attrs.get('type') == 'Broker'),
        'nodes': sum(1 for _, attrs in G.nodes(data=True) if attrs.get('type') == 'Node'),
        'applications': sum(1 for _, attrs in G.nodes(data=True) if attrs.get('type') == 'Application'),
        'topics': sum(1 for _, attrs in G.nodes(data=True) if attrs.get('type') == 'Topic')
    }
    
    # ===== BROKER ANALYSIS =====
    # Calculate broker metrics and identify critical brokers
    broker_connections = {}
    broker_impacted_apps = {}
    
    for node, attrs in G.nodes(data=True):
        if attrs.get('type') == 'Broker':
            # Count routed topics
            routed_topics = [n for n in G.successors(node) 
                            if G.nodes[n].get('type') == 'Topic' and G[node][n].get('type') == 'ROUTES']
            broker_connections[node] = len(routed_topics)
            
            # Count impacted applications
            impacted_apps = set()
            for topic in routed_topics:
                # Find apps that publish to this topic
                publishers = [n for n in G.predecessors(topic) 
                             if G.nodes[n].get('type') == 'Application' and G[n][topic].get('type') == 'PUBLISHES_TO']
                # Find apps that subscribe to this topic
                subscribers = [n for n in G.predecessors(topic) 
                              if G.nodes[n].get('type') == 'Application' and G[n][topic].get('type') == 'SUBSCRIBES_TO']
                impacted_apps.update(publishers)
                impacted_apps.update(subscribers)
            
            broker_impacted_apps[node] = len(impacted_apps)
    
    # Store broker metrics
    component_metrics['broker_connections'] = broker_connections
    component_metrics['broker_impacted_apps'] = broker_impacted_apps
    
    # Identify critical brokers using thresholds
    for broker, topic_count in broker_connections.items():
        is_critical = False
        criticality_reasons = []
        
        # Rule 1: High Topic Coverage
        topic_coverage = topic_count / max(1, total_components['topics'])
        if topic_coverage > thresholds.broker_topic_coverage:
            is_critical = True
            criticality_reasons.append(f"Routes {topic_count} topics ({topic_coverage:.0%} of all topics)")
        
        # Rule 2: High Application Impact
        app_impact = broker_impacted_apps.get(broker, 0) / max(1, total_components['applications'])
        if app_impact > thresholds.broker_application_impact:
            is_critical = True
            criticality_reasons.append(f"Impacts {broker_impacted_apps.get(broker, 0)} applications " +
                                      f"({app_impact:.0%} of all applications)")
        
        # Rule 3: Network Articulation Point (if there are multiple brokers)
        if total_components['brokers'] > 1:
            # Create broker-only subgraph
            broker_subgraph = nx.Graph()
            broker_nodes = [n for n, attrs in G.nodes(data=True) if attrs.get('type') == 'Broker']
            for n in broker_nodes:
                broker_subgraph.add_node(n)
            
            for u, v, attrs in G.edges(data=True):
                if (u in broker_nodes and v in broker_nodes and 
                    attrs.get('type') == 'CONNECTS_TO'):
                    broker_subgraph.add_edge(u, v)
            
            # Check if broker is an articulation point
            if len(broker_nodes) > 2:  # Need at least 3 brokers for articulation points
                try:
                    articulation_points = list(nx.articulation_points(broker_subgraph))
                    if broker in articulation_points:
                        is_critical = True
                        criticality_reasons.append("Acts as a network bridge between broker groups")
                except nx.NetworkXError:
                    # Graph may not be connected, which is itself a problem
                    pass
        
        if is_critical:
            critical_components['broker'].append({
                'node': broker,
                'metrics': {
                    'topic_count': topic_count,
                    'topic_coverage': topic_coverage,
                    'impacted_apps': broker_impacted_apps.get(broker, 0),
                    'app_impact': app_impact
                },
                'reasons': criticality_reasons
            })
    
    # ===== NODE ANALYSIS =====
    # Calculate node metrics and identify critical nodes
    node_loads = {}
    node_broker_hosts = {}
    
    for node, attrs in G.nodes(data=True):
        if attrs.get('type') == 'Node':
            # Count hosted services
            hosted_services = [n for n in G.predecessors(node) 
                              if G[n][node].get('type') == 'RUNS_ON']
            node_loads[node] = len(hosted_services)
            
            # Count hosted brokers
            hosted_brokers = [n for n in hosted_services 
                             if G.nodes[n].get('type') == 'Broker']
            node_broker_hosts[node] = len(hosted_brokers)
    
    # Store node metrics
    component_metrics['node_loads'] = node_loads
    component_metrics['node_broker_hosts'] = node_broker_hosts
    
    # Identify critical nodes using thresholds
    for node, service_count in node_loads.items():
        is_critical = False
        criticality_reasons = []
        
        # Rule 1: High Service Density
        total_services = total_components['applications'] + total_components['brokers']
        service_density = service_count / max(1, total_services)
        if service_density > thresholds.node_service_density:
            is_critical = True
            criticality_reasons.append(f"Hosts {service_count} services ({service_density:.0%} of all services)")
        
        # Rule 2: Hosts Many Brokers
        broker_hosting_ratio = node_broker_hosts.get(node, 0) / max(1, total_components['brokers'])
        if broker_hosting_ratio > thresholds.node_broker_hosting:
            is_critical = True
            criticality_reasons.append(f"Hosts {node_broker_hosts.get(node, 0)} brokers " +
                                      f"({broker_hosting_ratio:.0%} of all brokers)")
        
        # Rule 3: Hosts Critical Brokers
        hosts_critical_broker = False
        for broker_info in critical_components['broker']:
            critical_broker = broker_info['node']
            # Check if this broker runs on this node
            for u, v, attrs in G.edges(data=True):
                if (u == critical_broker and v == node and 
                    attrs.get('type') == 'RUNS_ON'):
                    hosts_critical_broker = True
                    break
        
        if hosts_critical_broker:
            is_critical = True
            criticality_reasons.append("Hosts one or more critical brokers")
        
        # Rule 4: Infrastructure Bridge
        # Create node-only subgraph
        if total_components['nodes'] > 2:  # Need at least 3 nodes for articulation points
            node_subgraph = nx.Graph()
            node_nodes = [n for n, attrs in G.nodes(data=True) if attrs.get('type') == 'Node']
            for n in node_nodes:
                node_subgraph.add_node(n)
            
            for u, v, attrs in G.edges(data=True):
                if (u in node_nodes and v in node_nodes and 
                    attrs.get('type') == 'CONNECTS_TO'):
                    node_subgraph.add_edge(u, v)
            
            try:
                articulation_points = list(nx.articulation_points(node_subgraph))
                if node in articulation_points:
                    is_critical = True
                    criticality_reasons.append("Acts as a network bridge between infrastructure segments")
            except nx.NetworkXError:
                # Graph may not be connected
                pass
        
        if is_critical:
            critical_components['node'].append({
                'node': node,
                'metrics': {
                    'service_count': service_count,
                    'service_density': service_density,
                    'broker_hosts': node_broker_hosts.get(node, 0),
                    'broker_hosting_ratio': broker_hosting_ratio
                },
                'reasons': criticality_reasons
            })
    
    # ===== APPLICATION ANALYSIS =====
    # Calculate application metrics and identify critical applications
    app_dependencies = {}
    app_exclusive_topics = {}
    
    # First calculate dependencies
    for node, attrs in G.nodes(data=True):
        if attrs.get('type') == 'Application':
            # Count applications that depend on this one
            dependents = [n for n in G.predecessors(node) 
                         if G.nodes[n].get('type') == 'Application' and G[n][node].get('type') == 'DEPENDS_ON']
            app_dependencies[node] = len(dependents)
    
    # Then calculate exclusive topic publishing
    for node, attrs in G.nodes(data=True):
        if attrs.get('type') == 'Topic':
            # Get all publishers to this topic
            publishers = [n for n in G.predecessors(node) 
                         if G.nodes[n].get('type') == 'Application' and G[n][node].get('type') == 'PUBLISHES_TO']
            
            # If there's exactly one publisher, it's exclusive
            if len(publishers) == 1:
                app = publishers[0]
                if app not in app_exclusive_topics:
                    app_exclusive_topics[app] = []
                app_exclusive_topics[app].append(node)
    
    # Store application metrics
    component_metrics['app_dependencies'] = app_dependencies
    component_metrics['app_exclusive_topics'] = app_exclusive_topics
    
    # Identify critical applications using thresholds
    for app in set(app_dependencies.keys()).union(app_exclusive_topics.keys()):
        is_critical = False
        criticality_reasons = []
        
        # Rule 1: Many Dependent Applications
        dependent_count = app_dependencies.get(app, 0)
        dependency_ratio = dependent_count / max(1, total_components['applications'] - 1)  # Exclude self
        
        if dependency_ratio > thresholds.app_dependency_ratio:
            is_critical = True
            criticality_reasons.append(f"Has {dependent_count} dependent applications " +
                                      f"({dependency_ratio:.0%} of other applications)")
        
        # Rule 2: Exclusive Publisher for Many Topics
        exclusive_topics = app_exclusive_topics.get(app, [])
        if len(exclusive_topics) >= thresholds.app_publisher_uniqueness:
            is_critical = True
            criticality_reasons.append(f"Sole publisher for {len(exclusive_topics)} topics")
        
        if is_critical:
            critical_components['application'].append({
                'node': app,
                'metrics': {
                    'dependent_count': dependent_count,
                    'dependency_ratio': dependency_ratio,
                    'exclusive_topics': len(exclusive_topics),
                    'exclusive_topic_names': [t for t in exclusive_topics]
                },
                'reasons': criticality_reasons
            })
    
    # ===== TOPIC ANALYSIS =====
    # Calculate topic metrics and identify critical topics
    topic_subscribers = {}
    
    for node, attrs in G.nodes(data=True):
        if attrs.get('type') == 'Topic':
            # Count subscribers
            subscribers = [n for n in G.predecessors(node) 
                          if G.nodes[n].get('type') == 'Application' and G[n][node].get('type') == 'SUBSCRIBES_TO']
            topic_subscribers[node] = len(subscribers)
    
    # Store topic metrics
    component_metrics['topic_subscribers'] = topic_subscribers
    
    # Identify critical topics using thresholds
    for topic, subscriber_count in topic_subscribers.items():
        is_critical = False
        criticality_reasons = []
        
        # Rule 1: High Subscriber Breadth
        subscriber_breadth = subscriber_count / max(1, total_components['applications'])
        
        if (subscriber_count >= thresholds.topic_criticality_minimum_subs and 
            subscriber_breadth > thresholds.topic_subscriber_breadth):
            is_critical = True
            criticality_reasons.append(f"Has {subscriber_count} subscribers " +
                                     f"({subscriber_breadth:.0%} of all applications)")
        
        # Rule 2: Cross-System Communication (this would require more complex analysis)
        # This would need community detection or other advanced algorithms
        
        if is_critical:
            critical_components['topic'].append({
                'node': topic,
                'metrics': {
                    'subscriber_count': subscriber_count,
                    'subscriber_breadth': subscriber_breadth
                },
                'reasons': criticality_reasons
            })
    
    # Organize results for return
    result = {
        'critical_components': critical_components,
        'component_metrics': component_metrics,
        'thresholds': thresholds
    }
    
    return result

def print_critical_summary(critical_components_analysis):
    """
    Print a summary of identified critical components
    
    Args:
        critical_components_analysis: Results of critical component identification
    """
    critical_components = critical_components_analysis['critical_components']
    
    print("\nCritical Component Summary:")
    
    if not any(critical_components.values()):
        print("  No critical components identified based on current thresholds.")
    else:
        # Broker summary
        if critical_components['broker']:
            print(f"\n  Critical Brokers ({len(critical_components['broker'])} identified):")
            for idx, broker_info in enumerate(critical_components['broker']):
                broker = broker_info['node']
                reasons = broker_info['reasons']
                print(f"    {idx+1}. {broker} - Critical because:")
                for reason in reasons:
                    print(f"       - {reason}")
        
        # Node summary
        if critical_components['node']:
            print(f"\n  Critical Nodes ({len(critical_components['node'])} identified):")
            for idx, node_info in enumerate(critical_components['node']):
                node = node_info['node']
                reasons = node_info['reasons']
                print(f"    {idx+1}. {node} - Critical because:")
                for reason in reasons:
                    print(f"       - {reason}")
        
        # Application summary
        if critical_components['application']:
            print(f"\n  Critical Applications ({len(critical_components['application'])} identified):")
            for idx, app_info in enumerate(critical_components['application']):
                app = app_info['node']
                reasons = app_info['reasons']
                print(f"    {idx+1}. {app} - Critical because:")
                for reason in reasons:
                    print(f"       - {reason}")
        
        # Topic summary
        if critical_components['topic']:
            print(f"\n  Critical Topics ({len(critical_components['topic'])} identified):")
            for idx, topic_info in enumerate(critical_components['topic']):
                topic = topic_info['node']
                reasons = topic_info['reasons']
                print(f"    {idx+1}. {topic} - Critical because:")
                for reason in reasons:
                    print(f"       - {reason}")

def get_simulation_targets(critical_components_analysis):
    """
    Prepare targets for failure simulation from critical component analysis
    
    Args:
        critical_components_analysis: Results of critical component identification
        
    Returns:
        dict: Dictionary of critical components to simulate failures for
    """
    critical_components = critical_components_analysis['critical_components']
    
    # Prepare for failure simulations
    simulation_targets = {}
    
    # Get one component of each type for simulation (if available)
    for component_type, components in critical_components.items():
        if components:
            # Take the first critical component of each type
            simulation_targets[component_type] = {
                'node': components[0]['node'],
                'type': component_type.capitalize()  # Convert 'broker' to 'Broker', etc.
            }
    
    return simulation_targets

if __name__ == "__main__":
    import sys
    import matplotlib.pyplot as plt
    
    from pubsub_config import SystemConfig, parse_args
    from pubsub_graph import create_complete_graph
    
    try:
        # Parse arguments
        config, args = parse_args()
        
        # Create graph
        print("Creating graph model...")
        G, components = create_complete_graph(config, use_neo4j=not args.no_neo4j)
        
        # Identify critical components
        print("\nIdentifying critical components...")
        critical_analysis = identify_critical_components(G, config)
        
        # Print summary
        print_critical_summary(critical_analysis)
        
        # Get simulation targets
        targets = get_simulation_targets(critical_analysis)
        
        print(f"\nIdentified {sum(len(components) for components in critical_analysis['critical_components'].values())} " +
              f"critical components across {len([t for t in targets if targets])} component types.")
        
        print("\nRun the failure simulation module to analyze failure impact:")
        print("python pubsub_failure.py [options]")
        
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)