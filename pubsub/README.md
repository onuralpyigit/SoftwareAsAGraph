# Publish-Subscribe Graph Model

A graph-based modeling and analysis framework for distributed publish-subscribe systems.

## Overview

This project provides a comprehensive set of tools for modeling, analyzing, and visualizing distributed software systems based on the publish-subscribe architecture. It uses graph theory to represent system components and their relationships, enabling the identification of critical components, impact analysis, and targeted improvement recommendations.

## Features

- **Graph-based Modeling**: Represent system components (applications, brokers, topics, nodes) and their relationships as a directed graph
- **Adaptive Threshold Analysis**: Identify critical components using system size-aware thresholds
- **Failure Impact Simulation**: Assess the impact of component failures on system functionality
- **Targeted Recommendations**: Generate specific recommendations for improving system resilience
- **Visualization**: Create layered visualizations of different aspects of the system architecture
- **Web-based Visualization**: Generate interactive D3.js visualizations for exploring the graph model
- **Import/Export**: Save and load graph models and analysis results using CSV files

## Modules

The framework is divided into several modules, each focusing on a specific aspect of the analysis:

1. **pubsub_config.py**: Configuration settings and command-line argument parsing
2. **pubsub_graph.py**: Graph creation and manipulation functions
3. **pubsub_analysis.py**: Basic graph analysis functions
4. **pubsub_threshold.py**: Adaptive threshold calculation for critical component identification
5. **pubsub_critical.py**: Critical component identification based on multiple rules
6. **pubsub_failure.py**: Failure simulation and impact assessment
7. **pubsub_recommendations.py**: System improvement recommendation generation
8. **pubsub_viz.py**: Graph visualization functions (static visualizations using matplotlib)
9. **pubsub_web_viz.py**: Web-based visualization functions (interactive visualizations using D3.js)
10. **pubsub_io.py**: Import/export functions for graph data and analysis results
11. **pubsub_main.py**: Main program orchestrating the complete analysis workflow

## Installation

### Requirements

- Python 3.7+
- NetworkX
- Matplotlib
- NumPy
- py2neo (for Neo4j integration, optional)
- Web browser (for interactive visualizations)

```bash
pip install networkx matplotlib numpy py2neo
```

### Neo4j Setup (Optional)

If you want to use Neo4j for graph storage and querying:

1. Download and install Neo4j from [https://neo4j.com/download/](https://neo4j.com/download/)
2. Create a new database
3. Set password to "password" or update the connection string in the code

## Usage

### Running the Complete Analysis

```bash
python pubsub_main.py --brokers 3 --nodes 5 --apps 15 --topics 30
```

### Web-based Visualization

```bash
# Generate a web-based visualization along with the analysis
python pubsub_main.py --web-viz --web-dir my_visualization

# Generate only the web-based visualization
python pubsub_main.py --web-viz-only --web-dir my_visualization

# Import from CSV and generate web visualization
python pubsub_main.py --import-csv --nodes-csv my_data/nodes.csv --edges-csv my_data/edges.csv --web-viz-only
```

The web-based visualization provides:
- Interactive force-directed graph layout
- Zooming and panning capabilities
- Node highlighting and selection
- Different view options (complete system, application layer, etc.)
- Component details on click
- Highlighting of critical components

### Importing/Exporting Data

```bash
# Export graph and analysis results to CSV files
python pubsub_main.py --export-csv --export-dir my_data

# Import graph from CSV files
python pubsub_main.py --import-csv --nodes-csv my_data/nodes.csv --edges-csv my_data/edges.csv

# Import and run analysis
python pubsub_main.py --import-csv --nodes-csv my_data/nodes.csv --edges-csv my_data/edges.csv --export-csv
```

### Running Specific Modules

```bash
# Run only basic analysis
python pubsub_main.py --basic-only --brokers 3 --nodes 5 --apps 15 --topics 30

# Run only critical component identification
python pubsub_main.py --critical-only --brokers 3 --nodes 5 --apps 15 --topics 30

# Run only failure simulation
python pubsub_main.py --failure-only --brokers 3 --nodes 5 --apps 15 --topics 30

# Run only recommendations generation
python pubsub_main.py --recommendations-only --brokers 3 --nodes 5 --apps 15 --topics 30

# Run only visualizations
python pubsub_main.py --viz-only --brokers 3 --nodes 5 --apps 15 --topics 30
```

### Command-Line Options

- `--brokers N`: Set number of brokers (default: 2)
- `--nodes N`: Set number of nodes/machines (default: 4)
- `--apps N`: Set number of applications (default: 10)
- `--topics N`: Set number of topics (default: 25)
- `--no-neo4j`: Skip Neo4j database operations
- `--no-viz`: Skip visualizations
- `--web-viz`: Generate web-based visualization
- `--web-dir DIR`: Set directory for web visualization files
- `--web-viz-only`: Generate only web-based visualization
- `--export-csv`: Export graph and analysis results to CSV files
- `--export-dir DIR`: Set directory for exported CSV files (default: graph_data)
- `--import-csv`: Import graph from CSV files
- `--nodes-csv FILE`: Set path to nodes CSV file (default: graph_data/nodes.csv)
- `--edges-csv FILE`: Set path to edges CSV file (default: graph_data/edges.csv)

## CSV File Format

### Nodes CSV
```
id,type,name,properties
App-1,Application,App-1,{'type': 'Service'}
Broker-1,Broker,Broker-1,{}
Topic-1,Topic,Topic-1,{'message_type': 'Command'}
Node-1,Node,Node-1,{'capacity': '8'}
```

### Edges CSV
```
source,target,type,properties
App-1,Topic-1,PUBLISHES_TO,{}
App-2,Topic-1,SUBSCRIBES_TO,{}
Broker-1,Topic-1,ROUTES,{}
App-1,Node-1,RUNS_ON,{}
```

## Graph Model Structure

### Node Types

1. **Application (A)**: Entities that produce or consume messages (publishers/subscribers)
2. **Broker (B)**: Components that route messages between topics and applications
3. **Topic (T)**: Logical channels for message transport
4. **Node (N)**: Physical or virtual machines hosting applications and broker services

### Edge Types

1. **PUBLISHES_TO**: Application → Topic relationship
2. **SUBSCRIBES_TO**: Application → Topic relationship
3. **ROUTES**: Broker → Topic relationship
4. **RUNS_ON**: Application/Broker → Node relationship
5. **DEPENDS_ON**: Derived relationship showing dependencies between applications
6. **CONNECTS_TO**: Infrastructure-level connections between nodes or brokers

## Analysis Workflow

1. **Graph Creation**: Generate a graph model with the specified number of components
2. **Basic Analysis**: Analyze general graph properties and identify potential bottlenecks
3. **Critical Component Identification**: Apply adaptive thresholds to identify critical components
4. **Failure Simulation**: Simulate failure of critical components and assess impact
5. **Recommendation Generation**: Generate targeted recommendations for system improvement
6. **Visualization**: Create visual representations of different system layers
7. **Data Export**: Save analysis results and graph structure to CSV files

## Visualization Options

### Static Visualizations (Matplotlib)
1. **Application Level Layer**: Shows dependencies between applications
2. **Infrastructure Level Layer**: Shows connections between nodes
3. **Application-Infrastructure Layer**: Shows which applications and brokers run on which nodes
4. **Messaging Layer**: Shows publish/subscribe relationships between applications and topics
5. **Complete System View**: Shows all components and relationships
6. **Critical Components View**: Highlights identified critical components

### Interactive Web Visualization (D3.js)
- **Force-directed graph layout**: Represents the system structure dynamically
- **Multiple view options**: Switch between different system aspects
- **Component details**: Click on nodes to see detailed information
- **Critical component highlighting**: Visually emphasize critical components
- **Failure simulation**: Visualize the impact of component failures on the system
- **Impact assessment**: See statistics and severity assessments for failures
- **Recommendations**: Browse categorized improvement recommendations
- **Zoom and pan**: Explore complex graphs more easily
- **Different node shapes**: Distinguish component types by shapes (circles, diamonds, squares, triangles)

## Example Usage in Research

This framework can support research in distributed systems by:

1. **Evaluating Architecture Designs**: Compare different architectural patterns and their resilience
2. **Identifying Vulnerabilities**: Locate single points of failure and dependency bottlenecks
3. **Quantifying Resilience**: Measure the impact of component failures on system functionality
4. **Validating Improvements**: Assess the effectiveness of architectural changes
5. **Sharing and Reproducing Results**: Export models and analysis results for reuse or validation
6. **Presenting Findings**: Use interactive visualizations in presentations and publications

## Working with Real-World Systems

To analyze a real-world publish-subscribe system:

1. **Data Collection**: Gather information about your system's components and their relationships
2. **CSV Creation**: Format the data according to the CSV schema (nodes.csv and edges.csv)
3. **Import and Analysis**:
   ```bash
   python pubsub_main.py --import-csv --nodes-csv real_system/nodes.csv --edges-csv real_system/edges.csv
   ```
4. **Visualization and Exploration**:
   ```bash
   python pubsub_main.py --import-csv --nodes-csv real_system/nodes.csv --edges-csv real_system/edges.csv --web-viz
   ```
5. **Report Generation**:
   ```bash
   python pubsub_main.py --import-csv --nodes-csv real_system/nodes.csv --edges-csv real_system/edges.csv --export-csv --export-dir analysis_results
   ```

## License

MIT License

## Acknowledgements

This project was developed as part of PhD research on distributed system quality modeling.