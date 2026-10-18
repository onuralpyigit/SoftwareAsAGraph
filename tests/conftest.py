import pytest
from saag.core import GraphData, ComponentData, EdgeData


def pytest_addoption(parser):
    parser.addoption(
        "--quick",
        action="store_true",
        default=False,
        help="Skip slow tests (e.g. long message-flow simulations)",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--quick"):
        skip_slow = pytest.mark.skip(reason="skipped due to --quick flag")
        for item in items:
            if "slow" in item.keywords:
                item.add_marker(skip_slow)

@pytest.fixture
def linear_graph():
    """A simple A -> B -> C linear graph."""
    return GraphData(
        components=[
            ComponentData(id="A", component_type="Application", weight=1.0),
            ComponentData(id="B", component_type="Application", weight=1.0),
            ComponentData(id="C", component_type="Application", weight=1.0),
        ],
        edges=[
            EdgeData("A", "B", "Application", "Application", "app_to_app", "dependency", 1.0),
            EdgeData("B", "C", "Application", "Application", "app_to_app", "dependency", 1.0),
        ],
    )

@pytest.fixture
def ap_graph():
    """Graph where B is an articulation point: A--B--C, B--D."""
    return GraphData(
        components=[
            ComponentData(id="A", component_type="Application", weight=1.0),
            ComponentData(id="B", component_type="Application", weight=1.0),
            ComponentData(id="C", component_type="Application", weight=1.0),
            ComponentData(id="D", component_type="Application", weight=1.0),
        ],
        edges=[
            EdgeData("A", "B", "Application", "Application", "app_to_app", "dependency", 1.0),
            EdgeData("B", "C", "Application", "Application", "app_to_app", "dependency", 1.0),
            EdgeData("B", "D", "Application", "Application", "app_to_app", "dependency", 1.0),
        ],
    )

@pytest.fixture
def star_graph():
    """Central hub S with 4 peripheral nodes L1-L4 depending on it."""
    components = [ComponentData(id="S", component_type="Application", weight=1.0)]
    edges = []
    for i in range(1, 5):
        id_ = f"L{i}"
        components.append(ComponentData(id=id_, component_type="Application", weight=1.0))
        edges.append(EdgeData(id_, "S", "Application", "Application", "app_to_app", "dependency", 1.0))
    return GraphData(components=components, edges=edges)

@pytest.fixture
def cycle_graph():
    """A closed loop of dependencies: A -> B -> C -> A."""
    return GraphData(
        components=[
            ComponentData(id="A", component_type="Application", weight=1.0),
            ComponentData(id="B", component_type="Application", weight=1.0),
            ComponentData(id="C", component_type="Application", weight=1.0),
        ],
        edges=[
            EdgeData("A", "B", "Application", "Application", "app_to_app", "dependency", 1.0),
            EdgeData("B", "C", "Application", "Application", "app_to_app", "dependency", 1.0),
            EdgeData("C", "A", "Application", "Application", "app_to_app", "dependency", 1.0),
        ],
    )

@pytest.fixture
def absorber_graph():
    """A node X with many incoming but zero outgoing dependencies (high CDPot)."""
    components = [ComponentData(id="X", component_type="Application", weight=1.0)]
    edges = []
    for i in range(1, 11):
        id_ = f"In{i}"
        components.append(ComponentData(id=id_, component_type="Application", weight=1.0))
        edges.append(EdgeData(id_, "X", "Application", "Application", "app_to_app", "dependency", 1.0))
    return GraphData(components=components, edges=edges)
