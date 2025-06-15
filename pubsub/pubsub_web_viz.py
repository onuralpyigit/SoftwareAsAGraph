#!/usr/bin/env python3
"""
Web-based Visualization Module for the Publish-Subscribe System Model

This module provides functions for creating interactive, web-based
visualizations of the system model using D3.js, including failure
simulation, impact assessment, and recommendations.
"""

import os
import json
import webbrowser
from pathlib import Path

def generate_web_visualization(G, critical_components=None, simulation_results=None, recommendations=None, output_dir="web_viz"):
    """
    Generate an interactive web-based visualization of the graph
    
    Args:
        G: NetworkX graph object
        critical_components: Optional dictionary of critical components
        simulation_results: Optional dictionary of failure simulation results
        recommendations: Optional dictionary of improvement recommendations
        output_dir: Directory to store the visualization files
        
    Returns:
        str: Path to the generated HTML file
    """
    # Create output directory
    os.makedirs(output_dir, exist_ok=True)
    
    # Prepare graph data for D3.js
    nodes = []
    links = []
    
    # Create set of critical nodes for quick lookup
    critical_nodes = set()
    if critical_components:
        for component_type, components in critical_components.items():
            for component_info in components:
                critical_nodes.add(component_info['node'])
    
    # Create dictionary of critical components with their reasons
    critical_details = {}
    if critical_components:
        for component_type, components in critical_components.items():
            for component_info in components:
                critical_details[component_info['node']] = {
                    'reasons': component_info.get('reasons', []),
                    'metrics': component_info.get('metrics', {})
                }
    
    # Prepare impact data if available
    impact_data = {}
    if simulation_results:
        for component_type, impacted_nodes in simulation_results.items():
            if not isinstance(impacted_nodes, set):
                continue
            impact_node_ids = list(impacted_nodes)
            impact_data[component_type] = impact_node_ids
    
    # Prepare nodes
    for node_id, attrs in G.nodes(data=True):
        node_type = attrs.get('type', 'Unknown')
        is_critical = node_id in critical_nodes
        
        # Add additional properties for critical components
        critical_info = {}
        if is_critical:
            critical_info = critical_details.get(node_id, {})
        
        nodes.append({
            'id': node_id,
            'label': node_id,
            'group': node_type,
            'critical': is_critical,
            'critical_info': critical_info
        })
    
    # Prepare links
    for source, target, attrs in G.edges(data=True):
        edge_type = attrs.get('type', 'Unknown')
        
        links.append({
            'source': source,
            'target': target,
            'type': edge_type
        })
    
    # Create JSON data
    graph_data = {
        'nodes': nodes,
        'links': links,
        'impact': impact_data
    }
    
    # Prepare recommendations data if available
    recommendations_data = {}
    if recommendations:
        recommendations_data = recommendations
    
    # Write JSON data to file
    json_path = os.path.join(output_dir, "graph_data.js")
    with open(json_path, 'w') as f:
        f.write("var graphData = " + json.dumps(graph_data, indent=2) + ";\n")
        f.write("var recommendationsData = " + json.dumps(recommendations_data, indent=2) + ";")
    
    # Create HTML file
    html_content = generate_html_template()
    html_path = os.path.join(output_dir, "index.html")
    
    with open(html_path, 'w') as f:
        f.write(html_content)
    
    # Create CSS file
    css_content = generate_css_template()
    css_path = os.path.join(output_dir, "style.css")
    
    with open(css_path, 'w') as f:
        f.write(css_content)
    
    # Create D3.js visualization script
    js_content = generate_d3_script()
    js_path = os.path.join(output_dir, "visualization.js")
    
    with open(js_path, 'w') as f:
        f.write(js_content)
    
    print(f"Web visualization generated at {html_path}")
    print(f"Open this file in a web browser to view the interactive visualization")
    
    # Try to open in browser
    try:
        webbrowser.open('file://' + os.path.abspath(html_path))
    except:
        print("Could not automatically open browser. Please open the HTML file manually.")
    
    return html_path

def generate_html_template():
    """Generate HTML template for the visualization"""
    return """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Publish-Subscribe System Visualization</title>
    <link rel="stylesheet" href="style.css">
    <script src="https://d3js.org/d3.v7.min.js"></script>
</head>
<body>
    <div class="container">
        <header>
            <h1>Publish-Subscribe System Visualization</h1>
            <div class="controls">
                <div class="control-group">
                    <label for="view-select">View:</label>
                    <select id="view-select">
                        <option value="complete">Complete System</option>
                        <option value="application">Application Layer</option>
                        <option value="infrastructure">Infrastructure Layer</option>
                        <option value="messaging">Messaging Layer</option>
                    </select>
                </div>
                <div class="control-group">
                    <label for="highlight-critical">Highlight Critical Components:</label>
                    <input type="checkbox" id="highlight-critical" checked>
                </div>
                <div class="control-group">
                    <label for="display-labels">Show Labels:</label>
                    <input type="checkbox" id="display-labels" checked>
                </div>
                <div class="control-group">
                    <label for="use-shapes">Use Shapes:</label>
                    <input type="checkbox" id="use-shapes" checked>
                </div>
                <div class="control-group">
                    <label for="simulate-failure">Simulate Failure:</label>
                    <select id="simulate-failure">
                        <option value="none">None</option>
                        <option value="broker">Broker Failure</option>
                        <option value="node">Node Failure</option>
                        <option value="application">Application Failure</option>
                        <option value="topic">Topic Failure</option>
                    </select>
                </div>
            </div>
        </header>
        <div class="main-content">
            <div class="visualization-container">
                <svg id="visualization"></svg>
            </div>
            <div class="sidebar">
                <div class="panel">
                    <div class="legend">
                        <h3>Legend</h3>
                        <div class="legend-item">
                            <div class="legend-shape application"></div>
                            <div>Application (Circle)</div>
                        </div>
                        <div class="legend-item">
                            <div class="legend-shape broker"></div>
                            <div>Broker (Diamond)</div>
                        </div>
                        <div class="legend-item">
                            <div class="legend-shape topic"></div>
                            <div>Topic (Square)</div>
                        </div>
                        <div class="legend-item">
                            <div class="legend-shape node"></div>
                            <div>Node (Triangle)</div>
                        </div>
                        <div class="legend-item">
                            <div class="legend-color critical"></div>
                            <div>Critical Component</div>
                        </div>
                        <div class="legend-item">
                            <div class="legend-color impacted"></div>
                            <div>Impacted Component</div>
                        </div>
                    </div>
                    <div class="node-info">
                        <h3>Component Information</h3>
                        <div id="node-details">
                            <p>Click on a component to see details</p>
                        </div>
                    </div>
                </div>
                <div class="recommendations-panel">
                    <h3>Improvement Recommendations</h3>
                    <div class="tab-container">
                        <div class="tabs">
                            <button class="tab-btn active" data-tab="redundancy">Redundancy</button>
                            <button class="tab-btn" data-tab="load-balancing">Load Balancing</button>
                            <button class="tab-btn" data-tab="decoupling">Decoupling</button>
                            <button class="tab-btn" data-tab="monitoring">Monitoring</button>
                        </div>
                        <div id="redundancy" class="tab-content active">
                            <ul id="redundancy-recs"></ul>
                        </div>
                        <div id="load-balancing" class="tab-content">
                            <ul id="load-balancing-recs"></ul>
                        </div>
                        <div id="decoupling" class="tab-content">
                            <ul id="decoupling-recs"></ul>
                        </div>
                        <div id="monitoring" class="tab-content">
                            <ul id="monitoring-recs"></ul>
                        </div>
                    </div>
                </div>
                <div class="failure-impact-panel">
                    <h3>Failure Impact Assessment</h3>
                    <div id="impact-details">
                        <p>Select a failure scenario from the dropdown menu</p>
                        <div id="impact-stats"></div>
                    </div>
                </div>
            </div>
        </div>
    </div>
    <script src="graph_data.js"></script>
    <script src="visualization.js"></script>
</body>
</html>
"""

def generate_css_template():
    """Generate CSS for the visualization"""
    return """body {
    font-family: Arial, sans-serif;
    margin: 0;
    padding: 0;
    background-color: #f5f5f5;
}

.container {
    display: flex;
    flex-direction: column;
    height: 100vh;
    max-width: 1800px;
    margin: 0 auto;
    background-color: white;
    box-shadow: 0 0 10px rgba(0, 0, 0, 0.1);
}

header {
    padding: 15px;
    border-bottom: 1px solid #ddd;
    background-color: #f8f8f8;
}

h1 {
    margin: 0 0 15px 0;
    font-size: 1.8em;
    color: #333;
}

.controls {
    display: flex;
    flex-wrap: wrap;
    gap: 20px;
    align-items: center;
}

.control-group {
    display: flex;
    align-items: center;
    gap: 8px;
}

select, input {
    padding: 5px;
    border: 1px solid #ddd;
    border-radius: 4px;
}

.main-content {
    display: flex;
    flex: 1;
    overflow: hidden;
}

.visualization-container {
    flex: 3;
    height: 100%;
    border-right: 1px solid #ddd;
    overflow: hidden;
}

.sidebar {
    flex: 2;
    display: flex;
    flex-direction: column;
    max-width: 500px;
    overflow-y: auto;
}

svg {
    width: 100%;
    height: 100%;
}

.panel, .recommendations-panel, .failure-impact-panel {
    padding: 15px;
    border-bottom: 1px solid #ddd;
}

.legend, .node-info {
    margin-bottom: 20px;
}

h3 {
    margin-top: 0;
    margin-bottom: 10px;
    font-size: 1.2em;
    color: #333;
}

.legend-item {
    display: flex;
    align-items: center;
    margin-bottom: 8px;
    gap: 10px;
}

.legend-color, .legend-shape {
    width: 20px;
    height: 20px;
    border-radius: 50%;
}

.legend-shape.application {
    background-color: #6baed6;
    border-radius: 50%;
}

.legend-shape.broker {
    background-color: #fd8d3c;
    border-radius: 0;
    transform: rotate(45deg);
}

.legend-shape.topic {
    background-color: #74c476;
    border-radius: 0;
}

.legend-shape.node {
    background-color: #fb9a99;
    border-radius: 0;
    clip-path: polygon(50% 0%, 100% 100%, 0% 100%);
    height: 17px;
}

.critical {
    background-color: #fb6a6a;
    border: 2px solid #b10026;
}

.impacted {
    background-color: #ffeda0;
    border: 2px solid #feb24c;
}

/* D3.js styles */
.node circle, .node rect, .node polygon {
    stroke-width: 2px;
}

.node.application circle {
    fill: #6baed6;
    stroke: #3182bd;
}

.node.broker polygon.diamond {
    fill: #fd8d3c;
    stroke: #e6550d;
}

.node.topic rect {
    fill: #74c476;
    stroke: #31a354;
}

.node.node polygon.triangle {
    fill: #fb9a99;
    stroke: #e31a1c;
}

.node.critical circle, 
.node.critical rect, 
.node.critical polygon {
    stroke: #b10026;
    stroke-width: 3px;
}

.node.impacted circle, 
.node.impacted rect, 
.node.impacted polygon {
    fill: #ffeda0;
    stroke: #feb24c;
    stroke-width: 2.5px;
}

.link {
    stroke-opacity: 0.6;
}

.link.PUBLISHES_TO {
    stroke: #41ab5d;
}

.link.SUBSCRIBES_TO {
    stroke: #3182bd;
}

.link.ROUTES {
    stroke: #e6550d;
}

.link.RUNS_ON {
    stroke: #999;
}

.link.DEPENDS_ON {
    stroke: #e31a1c;
}

.link.CONNECTS_TO {
    stroke: #000;
}

.tab-container {
    width: 100%;
}

.tabs {
    display: flex;
    overflow-x: auto;
    border-bottom: 1px solid #ddd;
}

.tab-btn {
    padding: 8px 16px;
    border: none;
    background: none;
    cursor: pointer;
    font-size: 14px;
    transition: background-color 0.3s;
}

.tab-btn:hover {
    background-color: #f0f0f0;
}

.tab-btn.active {
    background-color: #e6e6e6;
    border-bottom: 2px solid #4a90e2;
}

.tab-content {
    display: none;
    padding: 15px 0;
}

.tab-content.active {
    display: block;
}

#impact-stats {
    margin-top: 10px;
}

.impact-stat {
    margin-bottom: 10px;
}

.impact-stat .bar-container {
    height: 20px;
    width: 100%;
    background-color: #f0f0f0;
    border-radius: 4px;
    overflow: hidden;
    margin-top: 5px;
}

.impact-stat .bar {
    height: 100%;
    background-color: #feb24c;
}

.impact-stat .severity {
    font-weight: bold;
    margin-top: 5px;
}

.severity.low {
    color: green;
}

.severity.moderate {
    color: orange;
}

.severity.high {
    color: #e31a1c;
}

.severity.severe {
    color: #b10026;
}

#node-details h4 {
    margin-top: 0;
    margin-bottom: 10px;
}

#node-details p {
    margin: 5px 0;
}

#node-details ul {
    margin: 5px 0;
    padding-left: 20px;
}
"""

def generate_d3_script():
    """Generate the D3.js visualization script"""
    return """// Set up visualization parameters
const width = document.querySelector('.visualization-container').clientWidth;
const height = document.querySelector('.visualization-container').clientHeight;

// Create SVG element
const svg = d3.select('#visualization')
    .attr('width', width)
    .attr('height', height);

// Create the force simulation
let simulation = d3.forceSimulation()
    .force('link', d3.forceLink().id(d => d.id).distance(100))
    .force('charge', d3.forceManyBody().strength(-300))
    .force('center', d3.forceCenter(width / 2, height / 2))
    .force('collision', d3.forceCollide().radius(30));

// Set up zoom behavior
const zoom = d3.zoom()
    .scaleExtent([0.1, 4])
    .on('zoom', (event) => {
        container.attr('transform', event.transform);
    });

svg.call(zoom);

// Create a container for all visualization elements
const container = svg.append('g');

// Create arrowhead marker definitions for links
svg.append('defs').selectAll('marker')
    .data(['PUBLISHES_TO', 'SUBSCRIBES_TO', 'ROUTES', 'RUNS_ON', 'DEPENDS_ON', 'CONNECTS_TO'])
    .enter().append('marker')
    .attr('id', d => `arrowhead-${d}`)
    .attr('viewBox', '0 -5 10 10')
    .attr('refX', 25)
    .attr('refY', 0)
    .attr('markerWidth', 6)
    .attr('markerHeight', 6)
    .attr('orient', 'auto')
    .append('path')
    .attr('d', 'M0,-5L10,0L0,5')
    .attr('class', d => d);

// Initialize visualization with data
let nodes = graphData.nodes;
let links = graphData.links;
let nodeElements, linkElements, textElements;

// Filter functions for different views
function filterForView(viewType) {
    if (viewType === 'complete') {
        return {
            nodes: graphData.nodes,
            links: graphData.links
        };
    } else if (viewType === 'application') {
        const filteredNodes = graphData.nodes.filter(node => node.group === 'Application');
        const nodeIds = new Set(filteredNodes.map(n => n.id));
        const filteredLinks = graphData.links.filter(link => 
            nodeIds.has(link.source.id || link.source) && 
            nodeIds.has(link.target.id || link.target) && 
            link.type === 'DEPENDS_ON'
        );
        return { nodes: filteredNodes, links: filteredLinks };
    } else if (viewType === 'infrastructure') {
        const filteredNodes = graphData.nodes.filter(node => node.group === 'Node');
        const nodeIds = new Set(filteredNodes.map(n => n.id));
        const filteredLinks = graphData.links.filter(link => 
            nodeIds.has(link.source.id || link.source) && 
            nodeIds.has(link.target.id || link.target) && 
            link.type === 'CONNECTS_TO'
        );
        return { nodes: filteredNodes, links: filteredLinks };
    } else if (viewType === 'messaging') {
        const filteredNodes = graphData.nodes.filter(node => 
            node.group === 'Application' || node.group === 'Topic' || node.group === 'Broker'
        );
        const nodeIds = new Set(filteredNodes.map(n => n.id));
        const filteredLinks = graphData.links.filter(link => 
            nodeIds.has(link.source.id || link.source) && 
            nodeIds.has(link.target.id || link.target) && 
            (link.type === 'PUBLISHES_TO' || link.type === 'SUBSCRIBES_TO' || link.type === 'ROUTES')
        );
        return { nodes: filteredNodes, links: filteredLinks };
    }
    return { nodes: graphData.nodes, links: graphData.links };
}

// Function to create a shaped node
function createNode(selection, useShapes) {
    // Remove any existing shape elements
    selection.selectAll('*').remove();
    
    if (useShapes) {
        // Create shape nodes
        selection.each(function(d) {
            const group = d.group.toLowerCase();
            if (group === 'application') {
                d3.select(this).append('circle')
                    .attr('r', d.critical ? 12 : 8);
            } else if (group === 'broker') {
                d3.select(this).append('polygon')
                    .attr('class', 'diamond')
                    .attr('points', d.critical ? '0,-16 16,0 0,16 -16,0' : '0,-10 10,0 0,10 -10,0');
            } else if (group === 'topic') {
                const size = d.critical ? 10 : 7;
                d3.select(this).append('rect')
                    .attr('x', -size)
                    .attr('y', -size)
                    .attr('width', size * 2)
                    .attr('height', size * 2);
            } else if (group === 'node') {
                const size = d.critical ? 12 : 8;
                d3.select(this).append('polygon')
                    .attr('class', 'triangle')
                    .attr('points', `0,${-size*1.2} ${size},${size} ${-size},${size}`);
            } else {
                // Default to circle for unknown types
                d3.select(this).append('circle')
                    .attr('r', d.critical ? 12 : 8);
            }
        });
    } else {
        // Create simple circle nodes
        selection.append('circle')
            .attr('r', d => d.critical ? 12 : 8);
    }
}

// Function to update the visualization
function updateVisualization(viewType, failureType = 'none') {
    // Filter data based on selected view
    const filteredData = filterForView(viewType);
    nodes = filteredData.nodes;
    links = filteredData.links;
    
    // Get current shape preference
    const useShapes = document.getElementById('use-shapes').checked;
    
    // Remove existing elements
    container.selectAll('*').remove();
    
    // Create links
    linkElements = container.append('g')
        .selectAll('line')
        .data(links)
        .enter().append('line')
        .attr('class', d => `link ${d.type}`)
        .attr('stroke-width', 1.5)
        .attr('marker-end', d => `url(#arrowhead-${d.type})`);
    
    // Get impacted nodes for failure simulation
    let impactedNodeIds = [];
    if (failureType !== 'none' && graphData.impact && graphData.impact[failureType]) {
        impactedNodeIds = graphData.impact[failureType];
        updateImpactDetails(failureType, impactedNodeIds);
    } else {
        clearImpactDetails();
    }
    
    // Create nodes
    nodeElements = container.append('g')
        .selectAll('.node')
        .data(nodes)
        .enter().append('g')
        .attr('class', d => {
            let classes = `node ${d.group.toLowerCase()} ${d.critical ? 'critical' : ''}`;
            if (impactedNodeIds.includes(d.id)) {
                classes += ' impacted';
            }
            return classes;
        })
        .call(d3.drag()
            .on('start', dragStarted)
            .on('drag', dragged)
            .on('end', dragEnded))
        .on('click', showNodeDetails);
    
    // Add shapes to nodes
    createNode(nodeElements, useShapes);
    
    // Add text labels to nodes
    textElements = container.append('g')
        .selectAll('text')
        .data(nodes)
        .enter().append('text')
        .text(d => d.label)
        .attr('class', 'node-label')
        .attr('font-size', 10)
        .attr('dx', 15)
        .attr('dy', 4)
        .style('opacity', document.getElementById('display-labels').checked ? 1 : 0);
    
    // Update simulation with new data
    simulation.nodes(nodes).on('tick', ticked);
    simulation.force('link').links(links);
    simulation.alpha(1).restart();
}

// Function to handle node drag events
function dragStarted(event, d) {
    if (!event.active) simulation.alphaTarget(0.3).restart();
    d.fx = d.x;
    d.fy = d.y;
}

function dragged(event, d) {
    d.fx = event.x;
    d.fy = event.y;
}

function dragEnded(event, d) {
    if (!event.active) simulation.alphaTarget(0);
    d.fx = null;
    d.fy = null;
}

// Function to update positions on each tick
function ticked() {
    linkElements
        .attr('x1', d => d.source.x)
        .attr('y1', d => d.source.y)
        .attr('x2', d => d.target.x)
        .attr('y2', d => d.target.y);
    
    nodeElements.attr('transform', d => `translate(${d.x},${d.y})`);
    
    textElements
        .attr('x', d => d.x)
        .attr('y', d => d.y);
}

// Function to show node details when clicked
function showNodeDetails(event, d) {
    const nodeDetails = document.getElementById('node-details');
    
    // Basic information
    let html = `
        <h4>${d.label}</h4>
        <p><strong>Type:</strong> ${d.group}</p>
        <p><strong>Critical:</strong> ${d.critical ? 'Yes' : 'No'}</p>
    `;
    
    // Show reasons if it's a critical component
    if (d.critical && d.critical_info && d.critical_info.reasons && d.critical_info.reasons.length > 0) {
        html += '<h5>Critical Because:</h5><ul>';
        d.critical_info.reasons.forEach(reason => {
            html += `<li>${reason}</li>`;
        });
        html += '</ul>';
    }
    
    // Show metrics if available
    if (d.critical && d.critical_info && d.critical_info.metrics && Object.keys(d.critical_info.metrics).length > 0) {
        html += '<h5>Metrics:</h5><ul>';
        for (const [key, value] of Object.entries(d.critical_info.metrics)) {
            if (typeof value === 'number' && key.includes('ratio') || key.includes('percentage') || key.includes('coverage')) {
                html += `<li>${key.replace(/_/g, ' ')}: ${(value * 100).toFixed(1)}%</li>`;
            } else {
                html += `<li>${key.replace(/_/g, ' ')}: ${value}</li>`;
            }
        }
        html += '</ul>';
    }
    
    // Get connected nodes
    const incomingLinks = links.filter(l => (l.target.id || l.target) === d.id);
    const outgoingLinks = links.filter(l => (l.source.id || l.source) === d.id);
    
    if (incomingLinks.length > 0) {
        html += '<h5>Incoming Connections:</h5><ul>';
        incomingLinks.forEach(link => {
            const sourceNode = nodes.find(n => n.id === (link.source.id || link.source));
            if (sourceNode) {
                html += `<li>${sourceNode.label} (${link.type})</li>`;
            }
        });
        html += '</ul>';
    }
    
    if (outgoingLinks.length > 0) {
        html += '<h5>Outgoing Connections:</h5><ul>';
        outgoingLinks.forEach(link => {
            const targetNode = nodes.find(n => n.id === (link.target.id || link.target));
            if (targetNode) {
                html += `<li>${targetNode.label} (${link.type})</li>`;
            }
        });
        html += '</ul>';
    }
    
    nodeDetails.innerHTML = html;
}

// Function to update impact details
function updateImpactDetails(failureType, impactedNodeIds) {
    const impactDetails = document.getElementById('impact-details');
    const impactStats = document.getElementById('impact-stats');
    
    // Count impacted applications
    const allApplications = graphData.nodes.filter(n => n.group === 'Application');
    const impactedApplications = allApplications.filter(n => impactedNodeIds.includes(n.id));
    
    const percentageImpacted = (impactedApplications.length / allApplications.length) * 100;
    
    // Determine severity level
    let severityLevel = 'low';
    let severityText = 'LOW - System can tolerate this failure with minimal disruption';
    
    if (percentageImpacted > 60) {
        severityLevel = 'severe';
        severityText = 'SEVERE - Critical system failure';
    } else if (percentageImpacted > 30) {
        severityLevel = 'high';
        severityText = 'HIGH - Major system disruption';
    } else if (percentageImpacted > 10) {
        severityLevel = 'moderate';
        severityText = 'MODERATE - Significant but manageable disruption';
    }
    
    // Update impact stats
    impactDetails.innerHTML = `
        <h4>${capitalizeFirstLetter(failureType)} Failure Impact</h4>
        <p>This scenario simulates the failure of a critical ${failureType.toLowerCase()}</p>
        
        <div class="impact-stat">
            <strong>Impacted Applications:</strong> ${impactedApplications.length} of ${allApplications.length} (${percentageImpacted.toFixed(1)}%)
            <div class="bar-container">
                <div class="bar" style="width: ${percentageImpacted}%"></div>
            </div>
            <div class="severity ${severityLevel}">Impact Assessment: ${severityText}</div>
        </div>
        
        <h5>Resilience Recommendations:</h5>
        <ul>
    `;
    
    // Add specific recommendations based on failure type
    if (failureType === 'broker') {
        impactDetails.innerHTML += `
            <li>Implement broker redundancy and load balancing</li>
            <li>Consider multi-broker topic replication</li>
            <li>Set up automatic failover mechanisms</li>
        `;
    } else if (failureType === 'node') {
        impactDetails.innerHTML += `
            <li>Distribute critical services across multiple nodes</li>
            <li>Implement automated service migration capabilities</li>
            <li>Use container orchestration for automatic rescheduling</li>
        `;
    } else if (failureType === 'application') {
        impactDetails.innerHTML += `
            <li>Reduce exclusive topic publishing</li>
            <li>Implement application redundancy for critical publishers</li>
            <li>Consider service mesh patterns for resilience</li>
        `;
    } else if (failureType === 'topic') {
        impactDetails.innerHTML += `
            <li>Consider topic partitioning to distribute message load</li>
            <li>Implement message replay capabilities for recovery</li>
            <li>Set up cross-topic redundancy patterns</li>
        `;
    }
    
    impactDetails.innerHTML += `</ul>`;
    
    // Show list of impacted applications
    if (impactedApplications.length > 0) {
        impactDetails.innerHTML += `<h5>Impacted Applications:</h5><ul>`;
        // Show first 10 and then a count if there are more
        const displayApps = impactedApplications.slice(0, 10);
        for (const app of displayApps) {
            impactDetails.innerHTML += `<li>${app.id}</li>`;
        }
        if (impactedApplications.length > 10) {
            impactDetails.innerHTML += `<li>... and ${impactedApplications.length - 10} more</li>`;
        }
        impactDetails.innerHTML += `</ul>`;
    }
}

// Function to clear impact details
function clearImpactDetails() {
    const impactDetails = document.getElementById('impact-details');
    impactDetails.innerHTML = `<p>Select a failure scenario from the dropdown menu</p>`;
}

// Function to load recommendations from data
function loadRecommendations() {
    // If no recommendations data, show placeholder
    if (!recommendationsData || Object.keys(recommendationsData).length === 0) {
        document.querySelectorAll('.tab-content ul').forEach(ul => {
            ul.innerHTML = `<li>No specific recommendations available</li>`;
        });
        return;
    }
    
    // Load recommendations for each category
    for (const [category, recommendations] of Object.entries(recommendationsData)) {
        const ul = document.getElementById(`${category}-recs`);
        if (!ul) continue;
        
        // Clear existing
        ul.innerHTML = '';
        
        // Add recommendations
        if (recommendations.length === 0) {
            ul.innerHTML = `<li>No ${category} recommendations identified</li>`;
        } else {
            recommendations.forEach(rec => {
                ul.innerHTML += `<li>${rec}</li>`;
            });
        }
    }
}

// Helper function to capitalize first letter
function capitalizeFirstLetter(string) {
    return string.charAt(0).toUpperCase() + string.slice(1);
}

// Initialize the visualization with complete view
updateVisualization('complete');
loadRecommendations();

// Set up tab switching
document.querySelectorAll('.tab-btn').forEach(button => {
    button.addEventListener('click', function() {
        // Remove active class from all buttons and contents
        document.querySelectorAll('.tab-btn').forEach(btn => {
            btn.classList.remove('active');
        });
        document.querySelectorAll('.tab-content').forEach(content => {
            content.classList.remove('active');
        });
        
        // Add active class to current button and content
        this.classList.add('active');
        document.getElementById(this.dataset.tab).classList.add('active');
    });
});

// Set up event listeners for controls
document.getElementById('view-select').addEventListener('change', function() {
    updateVisualization(this.value, document.getElementById('simulate-failure').value);
});

document.getElementById('highlight-critical').addEventListener('change', function() {
    const criticalNodes = container.selectAll('.node.critical');
    if (this.checked) {
        criticalNodes.selectAll('circle').attr('r', 12);
        criticalNodes.selectAll('rect').attr('x', -10).attr('y', -10).attr('width', 20).attr('height', 20);
        criticalNodes.selectAll('polygon.diamond').attr('points', '0,-16 16,0 0,16 -16,0');
        criticalNodes.selectAll('polygon.triangle').attr('points', '0,-14.4 12,9.6 -12,9.6');
    } else {
        criticalNodes.selectAll('circle').attr('r', 8);
        criticalNodes.selectAll('rect').attr('x', -7).attr('y', -7).attr('width', 14).attr('height', 14);
        criticalNodes.selectAll('polygon.diamond').attr('points', '0,-10 10,0 0,10 -10,0');
        criticalNodes.selectAll('polygon.triangle').attr('points', '0,-9.6 8,6.4 -8,6.4');
    }
});

document.getElementById('display-labels').addEventListener('change', function() {
    textElements.style('opacity', this.checked ? 1 : 0);
});

document.getElementById('use-shapes').addEventListener('change', function() {
    // Re-apply the current view with shapes toggle
    updateVisualization(
        document.getElementById('view-select').value,
        document.getElementById('simulate-failure').value
    );
});

document.getElementById('simulate-failure').addEventListener('change', function() {
    updateVisualization(document.getElementById('view-select').value, this.value);
});

// Handle window resize
window.addEventListener('resize', function() {
    const width = document.querySelector('.visualization-container').clientWidth;
    const height = document.querySelector('.visualization-container').clientHeight;
    
    svg.attr('width', width).attr('height', height);
    simulation.force('center', d3.forceCenter(width / 2, height / 2));
    simulation.alpha(0.3).restart();
});
"""

def generate_web_visualization_with_analysis(G, critical_analysis, simulation_results, recommendations, output_dir="web_viz"):
    """
    Generate an interactive web-based visualization with comprehensive analysis
    
    Args:
        G: NetworkX graph object
        critical_analysis: Results from critical component identification
        simulation_results: Results from failure simulation
        recommendations: System improvement recommendations
        output_dir: Directory to store the visualization files
        
    Returns:
        str: Path to the generated HTML file
    """
    # Extract the components needed for visualization
    critical_components = critical_analysis.get('critical_components', {})
    
    # Convert simulation results to expected format
    processed_simulation_results = {}
    if simulation_results:
        for component_type, result in simulation_results.items():
            if isinstance(result, set):
                processed_simulation_results[component_type] = list(result)
    
    # Generate the visualization
    return generate_web_visualization(G, critical_components, processed_simulation_results, recommendations, output_dir)

def run_failure_simulation_for_web(G, component, component_type):
    """
    Run a simplified failure simulation for one component for the web visualization
    
    Args:
        G: NetworkX graph object
        component: The component to simulate failure for
        component_type: The type of the component (broker, node, application, topic)
        
    Returns:
        set: Set of impacted components
    """
    impacted_nodes = set()
    
    if component_type.lower() == "broker":
        # Find topics routed by this broker
        affected_topics = [node for node in G.successors(component) 
                          if G.nodes[node].get('type') == 'Topic' and G[component][node].get('type') == 'ROUTES']
        
        # Find applications using these topics
        for topic in affected_topics:
            publishers = [node for node in G.predecessors(topic) 
                         if G.nodes[node].get('type') == 'Application' and G[node][topic].get('type') == 'PUBLISHES_TO']
            
            subscribers = [node for node in G.predecessors(topic) 
                          if G.nodes[node].get('type') == 'Application' and G[node][topic].get('type') == 'SUBSCRIBES_TO']
            
            impacted_nodes.update(publishers)
            impacted_nodes.update(subscribers)
    
    elif component_type.lower() == "node":
        # Find services running on this node
        affected_services = [node for node in G.predecessors(component) 
                            if G[node][component].get('type') == 'RUNS_ON']
        
        # Add directly affected services to impacted nodes
        impacted_nodes.update(affected_services)
        
        # For affected brokers, analyze cascade impact
        affected_brokers = [service for service in affected_services if G.nodes[service].get('type') == 'Broker']
        for broker in affected_brokers:
            broker_impacted = run_failure_simulation_for_web(G, broker, "Broker")
            impacted_nodes.update(broker_impacted)
    
    elif component_type.lower() == "application":
        # Find applications that depend on this one
        dependent_apps = [node for node in G.predecessors(component) 
                         if G.nodes[node].get('type') == 'Application' and G[node][component].get('type') == 'DEPENDS_ON']
        
        impacted_nodes.update(dependent_apps)
        
        # Find topics exclusively published by this application
        for topic, attrs in G.nodes(data=True):
            if attrs.get('type') == 'Topic':
                publishers = [n for n in G.predecessors(topic) 
                             if G.nodes[n].get('type') == 'Application' and G[n][topic].get('type') == 'PUBLISHES_TO']
                if publishers == [component]:
                    # Find subscribers to this topic
                    subscribers = [n for n in G.predecessors(topic) 
                                  if G.nodes[n].get('type') == 'Application' and G[n][topic].get('type') == 'SUBSCRIBES_TO']
                    impacted_nodes.update(subscribers)
    
    elif component_type.lower() == "topic":
        # Find applications publishing to or subscribing from this topic
        publishers = [node for node in G.predecessors(component) 
                     if G.nodes[node].get('type') == 'Application' and G[node][component].get('type') == 'PUBLISHES_TO']
        
        subscribers = [node for node in G.predecessors(component) 
                      if G.nodes[node].get('type') == 'Application' and G[node][component].get('type') == 'SUBSCRIBES_TO']
        
        # Add to impacted nodes
        impacted_nodes.update(publishers)
        impacted_nodes.update(subscribers)
    
    return impacted_nodes

def prepare_simulation_data(G, critical_components):
    """
    Prepare simulation data for all critical components
    
    Args:
        G: NetworkX graph object
        critical_components: Dictionary of critical components
        
    Returns:
        dict: Dictionary of simulation results by component type
    """
    simulation_results = {}
    
    # Run simulation for one component of each type
    for component_type, components in critical_components.items():
        if not components:
            continue
            
        # Take the first component of this type
        critical_component = components[0]['node']
        
        # Run simulation
        impacted_nodes = run_failure_simulation_for_web(G, critical_component, component_type)
        
        # Store results
        simulation_results[component_type] = impacted_nodes
    
    return simulation_results

def export_graph_for_d3(G, critical_components=None, output_dir="graph_data"):
    """
    Export graph data in D3.js compatible JSON format
    
    Args:
        G: NetworkX graph object
        critical_components: Optional dictionary of critical components
        output_dir: Directory to store the JSON file
        
    Returns:
        str: Path to the created JSON file
    """
    # Create directory if it doesn't exist
    os.makedirs(output_dir, exist_ok=True)
    
    # Prepare graph data
    nodes = []
    links = []
    
    # Create set of critical nodes for quick lookup
    critical_nodes = set()
    if critical_components:
        for component_type, components in critical_components.items():
            for component_info in components:
                critical_nodes.add(component_info['node'])
    
    # Prepare nodes
    for node_id, attrs in G.nodes(data=True):
        node_type = attrs.get('type', 'Unknown')
        is_critical = node_id in critical_nodes
        
        nodes.append({
            'id': node_id,
            'label': node_id,
            'group': node_type,
            'critical': is_critical,
            'properties': {k: v for k, v in attrs.items() if k not in ['type']}
        })
    
    # Prepare links
    for source, target, attrs in G.edges(data=True):
        edge_type = attrs.get('type', 'Unknown')
        
        links.append({
            'source': source,
            'target': target,
            'type': edge_type,
            'properties': {k: v for k, v in attrs.items() if k not in ['type']}
        })
    
    # Create graph data
    graph_data = {
        'nodes': nodes,
        'links': links
    }
    
    # Write to JSON file
    json_path = os.path.join(output_dir, "graph_data.json")
    with open(json_path, 'w') as f:
        json.dump(graph_data, f, indent=2)
    
    print(f"Graph data exported to {json_path} in D3.js compatible format")
    return json_path

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
        
        # Prepare simulation data
        print("\nPreparing failure simulation data...")
        simulation_results = prepare_simulation_data(G, critical_analysis['critical_components'])
        
        # Generate web visualization
        output_dir = "web_viz"
        if len(sys.argv) > 1:
            output_dir = sys.argv[1]
            
        html_path = generate_web_visualization(
            G, 
            critical_analysis['critical_components'],
            simulation_results,
            None,  # No recommendations in this mode
            output_dir
        )
        print(f"Web visualization generated at {html_path}")
        
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)