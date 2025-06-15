#!/usr/bin/env python3
"""
Failure Simulation Module for the Publish-Subscribe System Model

This module provides functions for simulating component failures
and assessing their impact on system functionality.
"""

import networkx as nx

def simulate_failure(G, failed_component, component_type):
    """
    Simulate the failure of a specific component and assess system impact
    
    Args:
        G: NetworkX graph object
        failed_component: The node ID of the component to simulate failure for
        component_type: The type of component (Broker, Node, Application, Topic)
        
    Returns:
        set: Set of impacted nodes
    """
    print(f"\n=== Simulating Failure of {failed_component} ({component_type}) ===")
    
    impacted_nodes = set()
    
    if component_type == "Broker":
        # Find topics routed by this broker
        affected_topics = [node for node in G.successors(failed_component) 
                          if G.nodes[node].get('type') == 'Topic' and G[failed_component][node].get('type') == 'ROUTES']
        
        print(f"Broker {failed_component} routes {len(affected_topics)} topics")
        
        # Find applications using these topics
        for topic in affected_topics:
            publishers = [node for node in G.predecessors(topic) 
                         if G.nodes[node].get('type') == 'Application' and G[node][topic].get('type') == 'PUBLISHES_TO']
            
            subscribers = [node for node in G.predecessors(topic) 
                          if G.nodes[node].get('type') == 'Application' and G[node][topic].get('type') == 'SUBSCRIBES_TO']
            
            impacted_nodes.update(publishers)
            impacted_nodes.update(subscribers)
        
        print(f"Impact: {len(impacted_nodes)} applications affected")
        if impacted_nodes:
            print("Affected applications:")
            # Limit list to first 10 applications if there are many
            app_list = sorted(impacted_nodes)
            display_apps = app_list[:10]
            for app in display_apps:
                print(f"  - {app}")
            if len(app_list) > 10:
                print(f"  ... and {len(app_list) - 10} more applications")

        # Calculate effect on remaining broker load
        remaining_brokers = [node for node, attrs in G.nodes(data=True) 
                            if attrs.get('type') == 'Broker' and node != failed_component]
        
        if remaining_brokers:
            # Find current load on remaining brokers
            current_topics_per_broker = {}
            for broker in remaining_brokers:
                topics = [node for node in G.successors(broker) 
                         if G.nodes[node].get('type') == 'Topic' and G[broker][node].get('type') == 'ROUTES']
                current_topics_per_broker[broker] = len(topics)
            
            # Calculate theoretical redistribution
            avg_additional_load = len(affected_topics) / len(remaining_brokers)
            print(f"\nLoad impact on remaining brokers:")
            print(f"  {len(affected_topics)} topics must be redistributed among {len(remaining_brokers)} brokers")
            print(f"  Average additional load per broker: {avg_additional_load:.1f} topics")
            
            # Identify brokers that might be overloaded
            potential_overloads = []
            for broker, current_topics in current_topics_per_broker.items():
                new_total = current_topics + avg_additional_load
                if new_total > 1.5 * current_topics:  # 50% increase is significant
                    potential_overloads.append(broker)
            
            if potential_overloads:
                print("  Warning: These brokers may become overloaded:")
                for broker in potential_overloads[:5]:  # Limit list
                    print(f"    - {broker}")
                if len(potential_overloads) > 5:
                    print(f"    ... and {len(potential_overloads) - 5} more brokers")
    
    elif component_type == "Node":
        # Find services running on this node
        affected_services = [node for node in G.predecessors(failed_component) 
                            if G[node][failed_component].get('type') == 'RUNS_ON']
        
        print(f"Node {failed_component} hosts {len(affected_services)} services")
        
        # Categorize affected services
        affected_apps = []
        affected_brokers = []
        
        for service in affected_services:
            if G.nodes[service].get('type') == 'Application':
                affected_apps.append(service)
            elif G.nodes[service].get('type') == 'Broker':
                affected_brokers.append(service)
        
        print(f"Directly affected: {len(affected_apps)} applications, {len(affected_brokers)} brokers")
        
        # Add directly affected services to impacted nodes
        impacted_nodes.update(affected_services)
        
        # For affected brokers, analyze cascade impact
        broker_impacted_nodes = set()
        for broker in affected_brokers:
            broker_impacted = simulate_failure(G, broker, "Broker")
            broker_impacted_nodes.update(broker_impacted)
        
        # Add indirectly affected nodes
        impacted_nodes.update(broker_impacted_nodes)
        
        # Calculate capacity impact on remaining nodes
        remaining_nodes = [node for node, attrs in G.nodes(data=True) 
                          if attrs.get('type') == 'Node' and node != failed_component]
        
        if remaining_nodes and affected_services:
            print(f"\nCapacity impact analysis:")
            print(f"  {len(affected_services)} services must be redistributed among {len(remaining_nodes)} nodes")
            
            # Get current load on remaining nodes
            current_load = {}
            for node in remaining_nodes:
                services = [n for n in G.predecessors(node) 
                           if G[n][node].get('type') == 'RUNS_ON']
                current_load[node] = len(services)
            
            avg_current_load = sum(current_load.values()) / len(current_load)
            avg_additional_load = len(affected_services) / len(remaining_nodes)
            new_avg_load = avg_current_load + avg_additional_load
            
            print(f"  Current average load: {avg_current_load:.1f} services per node")
            print(f"  Additional average load: {avg_additional_load:.1f} services per node")
            print(f"  New average load: {new_avg_load:.1f} services per node")
            
            # Show potential capacity concerns
            if new_avg_load > avg_current_load * 1.3:  # 30% increase
                print("  Warning: System may experience capacity issues after redistribution")
        
        # Count total affected applications
        affected_app_count = sum(1 for node in impacted_nodes if G.nodes[node].get('type') == 'Application')
        print(f"Total applications affected: {affected_app_count}")
    
    elif component_type == "Application":
        # Find applications that depend on this one
        dependent_apps = [node for node in G.predecessors(failed_component) 
                         if G.nodes[node].get('type') == 'Application' and G[node][failed_component].get('type') == 'DEPENDS_ON']
        
        impacted_nodes.update(dependent_apps)
        
        # Find topics exclusively published by this application
        exclusive_topics = []
        for topic, attrs in G.nodes(data=True):
            if attrs.get('type') == 'Topic':
                publishers = [n for n in G.predecessors(topic) 
                             if G.nodes[n].get('type') == 'Application' and G[n][topic].get('type') == 'PUBLISHES_TO']
                if publishers == [failed_component]:
                    exclusive_topics.append(topic)
        
        # Find applications that subscribe to exclusively published topics
        additional_impacted = set()
        for topic in exclusive_topics:
            subscribers = [n for n in G.predecessors(topic) 
                          if G.nodes[n].get('type') == 'Application' and G[n][topic].get('type') == 'SUBSCRIBES_TO']
            additional_impacted.update(subscribers)
        
        # Add to total impacted nodes
        impacted_nodes.update(additional_impacted)
        
        # Summarize impact
        direct_deps = len(dependent_apps)
        subscribers_to_exclusive = len(additional_impacted - set(dependent_apps))
        
        print(f"Application {failed_component} failure impacts:")
        print(f"  - {direct_deps} directly dependent applications")
        if exclusive_topics:
            print(f"  - Exclusively publishes to {len(exclusive_topics)} topics")
            print(f"  - {subscribers_to_exclusive} additional applications affected via topic subscriptions")
        
        # List impacted applications
        if impacted_nodes:
            print("\nImpacted applications:")
            app_list = sorted(impacted_nodes)
            display_apps = app_list[:10]
            for app in display_apps:
                print(f"  - {app}")
            if len(app_list) > 10:
                print(f"  ... and {len(app_list) - 10} more applications")
    
    elif component_type == "Topic":
        # Find applications publishing to or subscribing from this topic
        publishers = [node for node in G.predecessors(failed_component) 
                     if G.nodes[node].get('type') == 'Application' and G[node][failed_component].get('type') == 'PUBLISHES_TO']
        
        subscribers = [node for node in G.predecessors(failed_component) 
                      if G.nodes[node].get('type') == 'Application' and G[node][failed_component].get('type') == 'SUBSCRIBES_TO']
        
        # Add to impacted nodes
        impacted_nodes.update(publishers)
        impacted_nodes.update(subscribers)
        
        # Analyze dependency chain
        subscription_map = {}
        for app in subscribers:
            # Check if subscribers depend on publishers
            for pub in publishers:
                if app != pub:  # Skip self-dependencies
                    for u, v, attrs in G.edges(data=True):
                        if (u == app and v == pub and attrs.get('type') == 'DEPENDS_ON'):
                            if app not in subscription_map:
                                subscription_map[app] = []
                            subscription_map[app].append(pub)
        
        # Summarize impact
        print(f"Topic {failed_component} failure impacts:")
        print(f"  - {len(publishers)} publisher applications")
        print(f"  - {len(subscribers)} subscriber applications")
        
        if subscription_map:
            print(f"  - {len(subscription_map)} applications with direct dependencies on publishers")
        
        # List impacted applications
        if impacted_nodes:
            print("\nImpacted applications:")
            app_list = sorted(impacted_nodes)
            display_apps = app_list[:10]
            for app in display_apps:
                print(f"  - {app}")
            if len(app_list) > 10:
                print(f"  ... and {len(app_list) - 10} more applications")
    
    # Calculate the percentage of system impacted
    total_apps = sum(1 for node, attrs in G.nodes(data=True) if attrs.get('type') == 'Application')
    impacted_apps = sum(1 for node in impacted_nodes if G.nodes[node].get('type') == 'Application')
    
    if total_apps > 0:
        impact_percentage = (impacted_apps / total_apps) * 100
        print(f"\nSystem Impact: {impact_percentage:.1f}% of applications affected")
        
        # Provide an impact severity assessment
        if impact_percentage < 10:
            print("Impact Assessment: LOW - System can tolerate this failure with minimal disruption")
        elif impact_percentage < 30:
            print("Impact Assessment: MODERATE - Significant but manageable disruption")
        elif impact_percentage < 60:
            print("Impact Assessment: HIGH - Major system disruption")
        else:
            print("Impact Assessment: SEVERE - Critical system failure")
        
        # Provide resilience recommendations
        print("\nResilience Recommendations:")
        if component_type == "Broker":
            print("  - Implement broker redundancy and load balancing")
            print("  - Consider multi-broker topic replication")
        elif component_type == "Node":
            print("  - Distribute critical services across multiple nodes")
            print("  - Implement automated service migration capabilities")
        elif component_type == "Application":
            print("  - Reduce exclusive topic publishing")
            print("  - Implement application redundancy for critical publishers")
        elif component_type == "Topic":
            print("  - Consider topic partitioning to distribute message load")
            print("  - Implement message replay capabilities for recovery")
    
    return impacted_nodes

def run_failure_simulations(G, critical_components):
    """
    Run failure simulations on the identified critical components
    
    Args:
        G: NetworkX graph object
        critical_components: Dictionary with critical component information
        
    Returns:
        dict: Dictionary with simulation results
    """
    simulation_results = {}
    
    if not critical_components:
        print("\nNo critical components identified for failure simulation.")
        return simulation_results
    
    print("\n=== Failure Impact Simulation ===")
    print("Analyzing the potential impact of critical component failures")
    
    # Simulate broker failure
    if 'broker' in critical_components:
        critical_broker = critical_components['broker']['node']
        print(f"\nSimulating failure of critical broker: {critical_broker}")
        simulation_results['broker'] = simulate_failure(G, critical_broker, "Broker")
    
    # Simulate node failure
    if 'node' in critical_components:
        critical_node = critical_components['node']['node']
        print(f"\nSimulating failure of critical node: {critical_node}")
        simulation_results['node'] = simulate_failure(G, critical_node, "Node")
    
    # Simulate application failure
    if 'application' in critical_components:
        critical_app = critical_components['application']['node']
        print(f"\nSimulating failure of critical application: {critical_app}")
        simulation_results['application'] = simulate_failure(G, critical_app, "Application")
    
    # Simulate topic failure (optional)
    if 'topic' in critical_components:
        critical_topic = critical_components['topic']['node']
        print(f"\nSimulating failure of critical topic: {critical_topic}")
        simulation_results['topic'] = simulate_failure(G, critical_topic, "Topic")
    
    # Print overall system resilience summary
    print("\n=== System Resilience Assessment ===")
    resilience_scores = {}
    
    if simulation_results:
        # Calculate application impact percentages
        total_apps = sum(1 for node, attrs in G.nodes(data=True) if attrs.get('type') == 'Application')
        
        for component_type, impact in simulation_results.items():
            impacted_apps = sum(1 for node in impact if G.nodes.get(node, {}).get('type') == 'Application')
            impact_percentage = (impacted_apps / total_apps) * 100 if total_apps > 0 else 0
            resilience_score = max(0, 10 - (impact_percentage / 10))  # 0-10 scale
            
            resilience_scores[component_type] = {
                'impact_percentage': impact_percentage,
                'resilience_score': resilience_score
            }
            
            print(f"Resilience against {component_type} failure: {resilience_score:.1f}/10")
            print(f"  - Impact: {impact_percentage:.1f}% of applications affected")
    
    if resilience_scores:
        # Calculate overall resilience score (weighted average)
        overall_score = sum(score['resilience_score'] for score in resilience_scores.values()) / len(resilience_scores)
        print(f"\nOverall system resilience score: {overall_score:.1f}/10")
        
        # Interpret the score
        if overall_score >= 8.0:
            print("System exhibits high resilience against critical component failures")
        elif overall_score >= 6.0:
            print("System shows moderate resilience but has some vulnerability to failures")
        else:
            print("System has significant vulnerability to critical component failures")
            print("Consider implementing redundancy and decoupling strategies")
    
    return simulation_results

if __name__ == "__main__":
    import sys
    
    from pubsub_config import SystemConfig, parse_args
    from pubsub_graph import create_complete_graph
    from pubsub_critical import identify_critical_components, get_simulation_targets
    
    try:
        # Parse arguments
        config, args = parse_args()
        
        # Create graph
        print("Creating graph model...")
        G, components = create_complete_graph(config, use_neo4j=not args.no_neo4j)
        
        # Identify critical components
        print("\nIdentifying critical components for failure simulation...")
        critical_analysis = identify_critical_components(G, config)
        
        # Get simulation targets
        simulation_targets = get_simulation_targets(critical_analysis)
        
        if not simulation_targets:
            print("No critical components identified for failure simulation.")
            sys.exit(0)
        
        # Run failure simulations
        simulation_results = run_failure_simulations(G, simulation_targets)
        
        print("\nFailure simulation complete.")
        
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
