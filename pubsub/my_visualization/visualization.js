// Set up visualization parameters
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
