#!/usr/bin/env python3
"""
Configuration module for the Publish-Subscribe Graph Model

This module defines the SystemConfig class and parsing utilities
for configuring the pub-sub system model.
"""

import argparse

class SystemConfig:
    """
    Configuration class for the publish-subscribe system model

    Attributes:
        num_brokers (int): Number of broker nodes
        num_nodes (int): Number of physical/virtual machine nodes
        num_applications (int): Number of application nodes
        num_topics (int): Number of topic nodes
    """
    def __init__(self, num_brokers=2, num_nodes=4, num_applications=10, num_topics=25):
        """
        Initialize system configuration with default or specified values

        Args:
            num_brokers (int, optional): Number of brokers. Defaults to 2.
            num_nodes (int, optional): Number of nodes. Defaults to 4.
            num_applications (int, optional): Number of applications. Defaults to 10.
            num_topics (int, optional): Number of topics. Defaults to 25.
        """
        self.num_brokers = num_brokers
        self.num_nodes = num_nodes
        self.num_applications = num_applications
        self.num_topics = num_topics
        
        # Validate configuration
        if num_brokers < 1:
            raise ValueError("System must have at least 1 broker")
        if num_nodes < 1:
            raise ValueError("System must have at least 1 node")
        if num_applications < 1:
            raise ValueError("System must have at least 1 application")
        if num_topics < 1:
            raise ValueError("System must have at least 1 topic")
            
        # Print configuration summary
        print(f"System Configuration:")
        print(f"  Brokers: {num_brokers}")
        print(f"  Nodes: {num_nodes}")
        print(f"  Applications: {num_applications}")
        print(f"  Topics: {num_topics}")

def parse_args():
    """
    Parse command line arguments for system configuration

    Returns:
        SystemConfig: Configuration object initialized with parsed values
    """
    parser = argparse.ArgumentParser(description='Publish-Subscribe System Graph Model')
    parser.add_argument('--brokers', type=int, default=2, help='Number of brokers (default: 2)')
    parser.add_argument('--nodes', type=int, default=4, help='Number of nodes/machines (default: 4)')
    parser.add_argument('--apps', type=int, default=10, help='Number of applications (default: 10)')
    parser.add_argument('--topics', type=int, default=25, help='Number of topics (default: 25)')
    parser.add_argument('--no-neo4j', action='store_true', help='Skip Neo4j database operations')
    parser.add_argument('--no-viz', action='store_true', help='Skip visualizations')
    args = parser.parse_args()
    
    return SystemConfig(
        num_brokers=args.brokers,
        num_nodes=args.nodes,
        num_applications=args.apps,
        num_topics=args.topics
    ), args

# If run directly, show configuration help
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Publish-Subscribe System Graph Model Configuration')
    parser.add_argument('--brokers', type=int, default=2, help='Number of brokers (default: 2)')
    parser.add_argument('--nodes', type=int, default=4, help='Number of nodes/machines (default: 4)')
    parser.add_argument('--apps', type=int, default=10, help='Number of applications (default: 10)')
    parser.add_argument('--topics', type=int, default=25, help='Number of topics (default: 25)')
    
    args = parser.parse_args()
    config = SystemConfig(
        num_brokers=args.brokers,
        num_nodes=args.nodes,
        num_applications=args.apps,
        num_topics=args.topics
    )
    
    print("\nConfiguration module can be imported in other scripts.")
    print("Run the main analysis script with: python pubsub_main.py [options]")
