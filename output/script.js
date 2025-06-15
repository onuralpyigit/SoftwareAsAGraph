// Set up SVG canvas dimensions
var svg = d3.select("svg"),
    width = +svg.attr("width"),
    height = +svg.attr("height");

// Set up color scale
var color = d3.scaleOrdinal(d3.schemeCategory10);

// Create a simulation with forces
var simulation = d3.forceSimulation()
    .force("link", d3.forceLink().id(function(d) { return d.index; }).distance(50))
    .force("charge", d3.forceManyBody().strength(-100))  // Adjust strength as needed
    .force("center", d3.forceCenter(width / 2, height / 2));

// Load the data
d3.json("graph_data.json").then(function(graph) {

    // Add links (edges)
    var link = svg.append("g")
        .attr("class", "links")
      .selectAll("line")
      .data(graph.links)
      .enter().append("line")
        .attr("stroke-width", function(d) { return 1; });

    // Add nodes
    var node = svg.append("g")
        .attr("class", "nodes")
      .selectAll("g")
      .data(graph.nodes)
      .enter().append("g");

    var circles = node.append("circle")
        .attr("r", 5)
        .attr("fill", function(d) { return color(d.group); })
        .call(d3.drag()
            .on("start", dragstarted)
            .on("drag", dragged)
            .on("end", dragended));

    // Add labels
    var labels = node.append("text")
        .text(function(d) { return d.id; })
        .attr('x', 6)
        .attr('y', 3);

    // Set up simulation nodes and links
    simulation
        .nodes(graph.nodes)
        .on("tick", ticked);

    simulation.force("link")
        .links(graph.links);

    // Update positions on each tick
    function ticked() {
        link
            .attr("x1", function(d) { return d.source.x; })
            .attr("y1", function(d) { return d.source.y; })
            .attr("x2", function(d) { return d.target.x; })
            .attr("y2", function(d) { return d.target.y; });

        node
            .attr("transform", function(d) { return "translate(" + d.x + "," + d.y + ")"; });
    }

    // Drag event handlers
    function dragstarted(d) {
        if (!d3.event.active) simulation.alphaTarget(0.3).restart();
        d.fx = d.x;
        d.fy = d.y;
    }

    function dragged(d) {
        d.fx = d3.event.x;
        d.fy = d3.event.y;
    }

    function dragended(d) {
        if (!d3.event.active) simulation.alphaTarget(0);
        d.fx = null;
        d.fy = null;
    }

}).catch(function(error){
    console.log(error);
});
