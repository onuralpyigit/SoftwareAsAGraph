#!/usr/bin/env python3
"""
Graph Creation Module for the Publish-Subscribe System Model

This module contains functions for creating and manipulating the graph model
of a publish-subscribe system, including node and relationship creation.
"""

import random
import networkx as nx
from py2neo import Graph, Node, Relationship

def connect_to_neo4j(use_neo4j=True, uri="bolt://localhost:7687", user="neo4j", password="password"):
    """
    Connect to Neo4j database
    
    Args:
        use_neo4j (bool): Whether to use Neo4j database
        uri (str): Neo4j connection URI
        user (str): Neo4j username
        password (str): Neo4j password
        
    Returns:
        Graph or None: Neo4j graph connection or None if not using Neo4j
    """
    if not use_neo4j:
        print("Skipping Neo4j connection")
        return None
    
    try:
        # Connect to Neo4j database
        graph = Graph(uri, auth=(user, password))
        # Clear existing database to avoid conflicts
        graph.run("MATCH (n) DETACH DELETE n")
        print("Connected to Neo4j and cleared existing data")
        return graph
    except Exception as e:
        print(f"Error connecting to Neo4j: {e}")
        print("Continuing without Neo4j database")
        return None

def create_node(graph, label, name, properties=None):
    """
    Create a node either in Neo4j or as a dictionary
    
    Args:
        graph: Neo4j graph connection or None
        label (str): Node label
        name (str): Node name
        properties (dict, optional): Additional node properties
        
    Returns:
        Node: Neo4j node or dictionary representation
    """
    if properties is None:
        properties = {}
    properties["name"] = name
    
    if graph is not None:
        # Create in Neo4j
        node = Node(label, **properties)
        graph.create(node)
        return node
    else:
        # Create as dictionary
        node = {"label": label, "name": name, "properties": properties}
        return node

def create_relationship(graph, source_node, rel_type, target_node, properties=None):
    """
    Create a relationship either in Neo4j or as a dictionary
    
    Args:
        graph: Neo4j graph connection or None
        source_node: Source node
        rel_type (str): Relationship type
        target_node: Target node
        properties (dict, optional): Additional relationship properties
        
    Returns:
        Relationship: Neo4j relationship or dictionary representation
    """
    if properties is None:
        properties = {}
    
    if graph is not None:
        # Create in Neo4j
        rel = Relationship(source_node, rel_type, target_node, **properties)
        graph.create(rel)
        return rel
    else:
        # Create as dictionary
        rel = {
            "source": source_node["name"] if isinstance(source_node, dict) else source_node["name"],
            "target": target_node["name"] if isinstance(target_node, dict) else target_node["name"],
            "type": rel_type,
            "properties": properties
        }
        return rel

def create_system(graph, config):
    """
    Create system components based on configuration
    
    Args:
        graph: Neo4j graph connection or None
        config: System configuration object
        
    Returns:
        tuple: Lists of created components (brokers, nodes, applications, topics)
    """
    # Create nodes for the system based on config
    # Brokers
    brokers = [create_node(graph, "Broker", f"Broker-{i+1}") for i in range(config.num_brokers)]

    # Nodes (machines)
    nodes = [create_node(graph, "Node", f"Node-{i+1}", {"capacity": random.randint(4, 8)}) 
             for i in range(config.num_nodes)]

    # Applications
    applications = [create_node(graph, "Application", f"App-{i+1}", 
                               {"type": random.choice(["Service", "Client", "Admin"])}) 
                    for i in range(config.num_applications)]

    # Topics
    topics = [create_node(graph, "Topic", f"Topic-{i+1}", 
                         {"message_type": random.choice(["Command", "Event", "Query", "Response"])}) 
              for i in range(config.num_topics)]
    
    return brokers, nodes, applications, topics

def create_infrastructure_connections(graph, nodes, brokers, connection_density=0.7):
    """
    Create infrastructure connections between nodes and brokers
    
    Args:
        graph: Neo4j graph connection or None
        nodes: List of node objects
        brokers: List of broker objects
        connection_density (float): Probability of connection between nodes
        
    Returns:
        list or None: List of created relationships if not using Neo4j, None otherwise
    """
    relationships = []
    
    # Create node-to-node connections
    for i in range(len(nodes)):
        for j in range(i+1, len(nodes)):
            if random.random() < connection_density:
                rel = create_relationship(graph, nodes[i], "CONNECTS_TO", nodes[j], 
                                   {"bandwidth": random.randint(100, 1000)})
                if graph is None:
                    relationships.append(rel)
    
    # Create broker-to-broker connections if multiple brokers
    if len(brokers) > 1:
        # Ensure all brokers are connected in a network
        # First create a minimum spanning tree to ensure connectivity
        for i in range(len(brokers) - 1):
            rel = create_relationship(graph, brokers[i], "CONNECTS_TO", brokers[i+1], 
                               {"protocol": "MQTT"})
            if graph is None:
                relationships.append(rel)
        
        # Then add some additional connections for redundancy (if more than 2 brokers)
        if len(brokers) > 2:
            for i in range(len(brokers)):
                for j in range(i+2, len(brokers)):
                    if random.random() < 0.5:  # 50% chance of additional connections
                        rel = create_relationship(graph, brokers[i], "CONNECTS_TO", brokers[j], 
                                           {"protocol": "MQTT"})
                        if graph is None:
                            relationships.append(rel)
    
    return relationships if graph is None else None

def create_service_distribution(graph, brokers, nodes, applications, topics, config):
    """
    Distribute services across infrastructure and create messaging relationships
    
    Args:
        graph: Neo4j graph connection or None
        brokers: List of broker objects
        nodes: List of node objects
        applications: List of application objects
        topics: List of topic objects
        config: System configuration object
        
    Returns:
        list or None: List of created relationships if not using Neo4j, None otherwise
    """
    relationships = []
    
    # Place brokers on nodes - with load balancing for larger systems
    if config.num_nodes >= config.num_brokers:
        # If we have enough nodes, distribute evenly
        nodes_for_brokers = random.sample(nodes, config.num_brokers)
        for i, broker in enumerate(brokers):
            rel = create_relationship(graph, broker, "RUNS_ON", nodes_for_brokers[i])
            if graph is None:
                relationships.append(rel)
    else:
        # Otherwise distribute randomly but try to avoid overloading
        node_load = {node["name"] if isinstance(node, dict) else node["name"]: 0 for node in nodes}
        for broker in brokers:
            # Find least loaded node
            target_node_name = min(node_load.items(), key=lambda x: x[1])[0]
            target_node = next(node for node in nodes if (node["name"] if isinstance(node, dict) else node["name"]) == target_node_name)
            
            rel = create_relationship(graph, broker, "RUNS_ON", target_node)
            if graph is None:
                relationships.append(rel)
            
            node_load[target_node_name] += 2  # Brokers count as heavier load
    
    # Place applications on nodes - with load balancing
    node_load = {node["name"] if isinstance(node, dict) else node["name"]: 0 for node in nodes}
    for app in applications:
        # Find least loaded node
        target_node_name = min(node_load.items(), key=lambda x: x[1])[0]
        target_node = next(node for node in nodes if (node["name"] if isinstance(node, dict) else node["name"]) == target_node_name)
        
        rel = create_relationship(graph, app, "RUNS_ON", target_node)
        if graph is None:
            relationships.append(rel)
        
        node_load[target_node_name] += 1
    
    # Assign topics to brokers for routing - with load balancing
    # Calculate how many topics each broker should handle
    topics_per_broker = max(1, config.num_topics // config.num_brokers)
    remaining_topics = config.num_topics
    
    for i, broker in enumerate(brokers):
        # Last broker gets any remaining topics
        if i == len(brokers) - 1:
            topics_to_assign = remaining_topics
        else:
            topics_to_assign = min(topics_per_broker, remaining_topics)
        
        # Assign topics to this broker
        for j in range(topics_to_assign):
            topic_index = i * topics_per_broker + j
            if topic_index < len(topics):
                rel = create_relationship(graph, broker, "ROUTES", topics[topic_index])
                if graph is None:
                    relationships.append(rel)
                remaining_topics -= 1
    
    # Calculate reasonable publishing range based on system size
    min_pub = max(1, min(2, config.num_topics // 5))
    max_pub = max(2, min(5, config.num_topics // 2))
    
    # Calculate reasonable subscription range based on system size
    min_sub = max(1, min(3, config.num_topics // 4))
    max_sub = max(3, min(8, config.num_topics // 2))
    
    # Each application publishes to a reasonable number of topics
    for app in applications:
        # Adjust range if topics are fewer than max_pub
        adjusted_max_pub = min(max_pub, len(topics))
        num_pub_topics = random.randint(min_pub, adjusted_max_pub)
        pub_topics = random.sample(topics, num_pub_topics)
        
        for topic in pub_topics:
            rel = create_relationship(graph, app, "PUBLISHES_TO", topic)
            if graph is None:
                relationships.append(rel)
    
    # Each application subscribes to a reasonable number of topics
    for app in applications:
        # Adjust range if topics are fewer than max_sub
        adjusted_max_sub = min(max_sub, len(topics))
        num_sub_topics = random.randint(min_sub, adjusted_max_sub)
        sub_topics = random.sample(topics, num_sub_topics)
        
        for topic in sub_topics:
            rel = create_relationship(graph, app, "SUBSCRIBES_TO", topic)
            if graph is None:
                relationships.append(rel)
    
    return relationships if graph is None else None

def create_derived_relationships(graph, config):
    """
    Create derived relationships between components
    
    Args:
        graph: Neo4j graph connection or None
        config: System configuration object
        
    Returns:
        list or None: List of derived relationships if not using Neo4j, None otherwise
    """
    if graph is not None:
        # Use Neo4j for creating derived relationships
        
        # Create derived DEPENDS_ON relationships
        # An application depends on another if it subscribes to a topic that the other publishes to
        query = """
        MATCH (pub:Application)-[:PUBLISHES_TO]->(t:Topic)<-[:SUBSCRIBES_TO]-(sub:Application)
        WHERE pub <> sub
        MERGE (sub)-[r:DEPENDS_ON]->(pub)
        RETURN pub.name, sub.name, count(t) as topic_count
        """
        graph.run(query)
        
        # Create application-broker dependencies
        # An application depends on a broker if the broker routes topics that the application publishes to or subscribes to
        query = """
        MATCH (app:Application)-[:PUBLISHES_TO|SUBSCRIBES_TO]->(t:Topic)<-[:ROUTES]-(b:Broker)
        MERGE (app)-[r:DEPENDS_ON]->(b)
        RETURN app.name, b.name, count(t) as topic_count
        """
        graph.run(query)
        return None
    else:
        # For in-memory mode, we'd need to implement this logic in Python
        # This would require knowledge of all relationships created so far
        print("Skipping derived relationships (Neo4j not available)")
        return []

def neo4j_to_networkx(graph, all_relationships=None):
    """
    Extract graph data from Neo4j or create from dictionaries and convert to NetworkX
    
    Args:
        graph: Neo4j graph connection or None
        all_relationships: List of relationships if not using Neo4j
        
    Returns:
        DiGraph: NetworkX directed graph
    """
    G = nx.DiGraph()
    
    if graph is not None:
        # Neo4j mode - extract from database
        # Add nodes from Neo4j
        for label in ["Application", "Broker", "Topic", "Node"]:
            query = f"MATCH (n:{label}) RETURN n.name as name, labels(n) as labels"
            result = graph.run(query).data()
            for record in result:
                G.add_node(record["name"], type=label)
        
        # Add edges from Neo4j
        for rel_type in ["RUNS_ON", "PUBLISHES_TO", "SUBSCRIBES_TO", "ROUTES", "DEPENDS_ON", "CONNECTS_TO"]:
            query = f"MATCH (a)-[r:{rel_type}]->(b) RETURN a.name as source, b.name as target, type(r) as type"
            result = graph.run(query).data()
            for record in result:
                G.add_edge(record["source"], record["target"], type=record["type"])
    else:
        # In-memory mode - create from dictionaries
        # This is a simplified version and would need to be expanded for a full implementation
        # For demonstration purposes only
        print("Creating NetworkX graph from in-memory data")
        
        # Add some sample nodes
        G.add_node("App-1", type="Application")
        G.add_node("Broker-1", type="Broker")
        G.add_node("Topic-1", type="Topic")
        G.add_node("Node-1", type="Node")
        
        # Add some sample edges
        G.add_edge("App-1", "Topic-1", type="PUBLISHES_TO")
        G.add_edge("Broker-1", "Topic-1", type="ROUTES")
        G.add_edge("App-1", "Node-1", type="RUNS_ON")
    
    return G

def create_complete_graph(config, use_neo4j=True):
    """
    Create a complete graph model of the pub-sub system
    
    Args:
        config: System configuration object
        use_neo4j (bool): Whether to use Neo4j
        
    Returns:
        tuple: (NetworkX graph, components dictionary)
    """
    # Connect to Neo4j if needed
    graph_db = connect_to_neo4j(use_neo4j)
    
    print("\n=== Creating System Model ===")
    
    # Create system components
    brokers, nodes, applications, topics = create_system(graph_db, config)
    
    # Create infrastructure connections
    infra_relationships = create_infrastructure_connections(graph_db, nodes, brokers)
    
    # Create service distribution and messaging relationships
    service_relationships = create_service_distribution(graph_db, brokers, nodes, applications, topics, config)
    
    # Create derived relationships
    derived_relationships = create_derived_relationships(graph_db, config)
    
    # Gather all relationships for in-memory mode
    all_relationships = []
    if graph_db is None:
        if infra_relationships:
            all_relationships.extend(infra_relationships)
        if service_relationships:
            all_relationships.extend(service_relationships)
        if derived_relationships:
            all_relationships.extend(derived_relationships)
    
    # Convert to NetworkX graph
    G = neo4j_to_networkx(graph_db, all_relationships)
    
    print(f"Created system model with {G.number_of_nodes()} nodes and {G.number_of_edges()} edges")
    
    components = {
        "brokers": brokers,
        "nodes": nodes,
        "applications": applications,
        "topics": topics
    }
    
    return G, components

if __name__ == "__main__":
    from pubsub_config import SystemConfig, parse_args
    
    # Default configuration if run directly
    config, args = parse_args()
    
    # Create graph with in-memory or Neo4j storage
    G, components = create_complete_graph(config, use_neo4j=not args.no_neo4j)
    
    print("\nGraph creation complete.")
    print(f"Created {len(components['brokers'])} brokers, {len(components['nodes'])} nodes, " +
          f"{len(components['applications'])} applications, and {len(components['topics'])} topics")
    print("\nRun the analysis module to analyze the graph: python pubsub_analysis.py [options]")
