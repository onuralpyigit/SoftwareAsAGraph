#!/usr/bin/env python3
"""
Adaptive Threshold Module for the Publish-Subscribe System Model

This module defines the CriticalityThresholds class for calculating
adaptive thresholds based on system configuration.
"""

import numpy as np

class CriticalityThresholds:
    """
    Defines and calculates adaptive thresholds for identifying critical components
    based on system size and statistical properties
    
    Attributes:
        config: SystemConfig object
        broker_topic_coverage (float): Threshold for broker topic coverage
        broker_application_impact (float): Threshold for broker app impact
        node_service_density (float): Threshold for node service density
        node_broker_hosting (float): Threshold for node broker hosting
        app_dependency_ratio (float): Threshold for application dependency
        app_publisher_uniqueness (int): Threshold for exclusive topic publishing
        topic_subscriber_breadth (float): Threshold for topic subscriber breadth
        topic_criticality_minimum_subs (int): Minimum subscribers for topic criticality
    """
    def __init__(self, config):
        """
        Initialize thresholds based on system configuration
        
        Args:
            config: SystemConfig object containing system dimensions
        """
        self.config = config
        
        # Calculate base thresholds using system size adaptation formulas
        # For smaller systems, we use higher percentage thresholds
        # For larger systems, we scale down thresholds appropriately
        
        # Broker thresholds
        self.broker_topic_coverage = self._calculate_broker_topic_coverage()
        self.broker_application_impact = self._calculate_broker_application_impact()
        
        # Node thresholds
        self.node_service_density = self._calculate_node_service_density()
        self.node_broker_hosting = self._calculate_node_broker_hosting()
        
        # Application thresholds
        self.app_dependency_ratio = self._calculate_app_dependency_ratio()
        self.app_publisher_uniqueness = self._calculate_app_publisher_uniqueness()
        
        # Topic thresholds
        self.topic_subscriber_breadth = self._calculate_topic_subscriber_breadth()
        self.topic_criticality_minimum_subs = max(2, min(5, config.num_applications // 10))
        
        # Print calculated thresholds
        self._print_thresholds()
    
    def _calculate_broker_topic_coverage(self):
        """
        Calculate adaptive threshold for broker topic coverage
        
        Returns:
            float: Threshold value
        """
        # For small systems (≤5 brokers), a broker handling >40% of topics is critical
        # For large systems (≥20 brokers), a broker handling >15% of topics is critical
        # Scale linearly between these points
        base = 0.40
        min_threshold = 0.15
        scaling_factor = 0.8  # How quickly threshold decreases with size
        
        if self.config.num_brokers <= 1:
            return 1.0  # If there's only one broker, it's automatically critical
        
        # Apply inverse scaling with system size
        threshold = base * pow(5 / max(5, self.config.num_brokers), scaling_factor)
        # Ensure threshold doesn't go below minimum
        return max(min_threshold, threshold)
    
    def _calculate_broker_application_impact(self):
        """
        Calculate threshold for broker impact on applications
        
        Returns:
            float: Threshold value
        """
        # How many applications can a broker impact before it's considered critical
        # This scales with system size
        base = 0.35
        min_threshold = 0.10
        
        if self.config.num_applications <= 5:
            # For very small systems, affecting >50% of apps is critical
            return 0.5
        
        # Apply scaling based on application count
        threshold = base * pow(10 / max(10, self.config.num_applications), 0.5)
        return max(min_threshold, threshold)
    
    def _calculate_node_service_density(self):
        """
        Calculate threshold for node service hosting density
        
        Returns:
            float: Threshold value
        """
        # For small systems (≤5 nodes), a node hosting >35% of services is critical
        # For large systems (≥20 nodes), a node hosting >10% of services is critical
        base = 0.35
        min_threshold = 0.10
        
        if self.config.num_nodes <= 1:
            return 1.0  # If there's only one node, it's automatically critical
        
        # Calculate total services
        total_services = self.config.num_applications + self.config.num_brokers
        
        # Apply scaling based on node count and total services
        threshold = base * pow(5 / max(5, self.config.num_nodes), 0.7)
        # Further adjust based on service density
        service_density_factor = min(1.0, max(0.5, 20 / max(20, total_services)))
        adjusted_threshold = threshold * service_density_factor
        
        return max(min_threshold, adjusted_threshold)
    
    def _calculate_node_broker_hosting(self):
        """
        Calculate threshold for criticality based on broker hosting
        
        Returns:
            float: Threshold value
        """
        # Node is critical if it hosts >X% of brokers
        base = 0.5
        min_threshold = 0.25
        
        if self.config.num_nodes <= 1 or self.config.num_brokers <= 1:
            return 1.0  # Edge cases
        
        # Apply scaling based on broker and node counts
        broker_node_ratio = self.config.num_brokers / self.config.num_nodes
        if broker_node_ratio >= 1:
            # More brokers than nodes - node is critical if it hosts multiple brokers
            return max(min_threshold, 1.0 / self.config.num_nodes * 1.5)
        else:
            # More nodes than brokers - use standard scaling
            return max(min_threshold, base * pow(broker_node_ratio, 0.3))
    
    def _calculate_app_dependency_ratio(self):
        """
        Calculate threshold for application dependency ratio
        
        Returns:
            float: Threshold value
        """
        # Application is critical if >X% of other applications depend on it
        base = 0.30
        min_threshold = 0.10
        
        if self.config.num_applications <= 3:
            return 0.5  # Small system edge case
        
        # Apply scaling based on application count
        threshold = base * pow(10 / max(10, self.config.num_applications), 0.6)
        return max(min_threshold, threshold)
    
    def _calculate_app_publisher_uniqueness(self):
        """
        Calculate threshold for application publisher uniqueness
        
        Returns:
            int: Threshold value (absolute number, not percentage)
        """
        # How many topics can an app be the sole publisher for before it's critical
        # Returns an absolute number, not a percentage
        
        if self.config.num_topics <= 5:
            return 1  # In a small system, being the sole publisher for even one topic is significant
        
        # For larger systems, scale based on topic count
        return max(1, min(5, self.config.num_topics // 10))
    
    def _calculate_topic_subscriber_breadth(self):
        """
        Calculate threshold for topic subscriber breadth
        
        Returns:
            float: Threshold value
        """
        # Topic is critical if >X% of applications subscribe to it
        base = 0.40
        min_threshold = 0.15
        
        if self.config.num_applications <= 3:
            return 0.5  # Small system edge case
        
        # Apply scaling based on application count
        threshold = base * pow(10 / max(10, self.config.num_applications), 0.4)
        return max(min_threshold, threshold)
    
    def _print_thresholds(self):
        """Print the calculated thresholds for reference"""
        print("\n=== Critical Component Thresholds ===")
        print(f"Broker topic coverage: >{self.broker_topic_coverage:.0%} of all topics")
        print(f"Broker application impact: >{self.broker_application_impact:.0%} of all applications")
        print(f"Node service density: >{self.node_service_density:.0%} of all services")
        print(f"Node broker hosting: >{self.node_broker_hosting:.0%} of all brokers")
        print(f"Application dependency: >{self.app_dependency_ratio:.0%} of other applications")
        print(f"Application publisher uniqueness: >{self.app_publisher_uniqueness} topics as sole publisher")
        print(f"Topic subscriber breadth: >{self.topic_subscriber_breadth:.0%} of all applications")
        print(f"Topic minimum subscribers: >{self.topic_criticality_minimum_subs} subscribers")

if __name__ == "__main__":
    from pubsub_config import SystemConfig, parse_args
    
    # Test the thresholds with different configurations
    print("Testing adaptive thresholds with different system sizes:")
    
    # Small system
    print("\n=== Small System ===")
    small_config = SystemConfig(num_brokers=2, num_nodes=3, num_applications=5, num_topics=10)
    small_thresholds = CriticalityThresholds(small_config)
    
    # Medium system
    print("\n=== Medium System ===")
    medium_config = SystemConfig(num_brokers=5, num_nodes=10, num_applications=20, num_topics=50)
    medium_thresholds = CriticalityThresholds(medium_config)
    
    # Large system
    print("\n=== Large System ===")
    large_config = SystemConfig(num_brokers=15, num_nodes=30, num_applications=100, num_topics=200)
    large_thresholds = CriticalityThresholds(large_config)
    
    print("\nAs you can see, thresholds adapt based on system size to maintain appropriate sensitivity.")
