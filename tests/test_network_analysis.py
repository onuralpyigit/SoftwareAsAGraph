"""Step-2 analysis of the physical network: switches/routers scored through Rule 7."""

import copy
import json
from pathlib import Path

import pytest

from saag.analysis.antipattern_detector import AntiPatternDetector
from saag.analysis.service import AnalysisService
from saag.infrastructure.memory_repo import MemoryRepository
from saag.prediction.service import PredictionService
from tools.generation import GenerationService, load_config
from tools.generation.models import NetworkConfig

MESH_YAML = Path("data/scenarios/scenario_10_atm_system.yaml")


def _infra(topology):
    repo = MemoryRepository()
    repo.save_graph(copy.deepcopy(topology), clear=True)
    result = AnalysisService(repo).analyze_layer("infra")
    quality = PredictionService().predict_quality(result.structural)
    return result, {c.id: c.scores.overall for c in quality.components}


def _fabric(cores):
    config = load_config(MESH_YAML)
    config.network = NetworkConfig(core_routers=cores)
    return GenerationService(config=config).generate()


def test_a_single_core_ranks_with_the_switches_and_above_redundant_cores():
    """One core sits on every cross-rack path, like each ToR does; two cores share the load."""
    _r, single = _infra(_fabric(cores=1))
    _r, double = _infra(json.loads(Path("data/network/atm_two_tier_system.json").read_text()))
    assert single["RT0"] == pytest.approx(max(v for k, v in single.items() if k.startswith("SW")))
    assert single["RT0"] > double["RT0"]
    assert max(double["RT0"], double["RT1"]) < min(double["SW0"], double["SW1"])


def test_redundant_core_is_not_flagged_isolated():
    """No host is forced through a redundant core, so it has no DEPENDS_ON edges at all."""
    result, _q = _infra(json.loads(Path("data/network/atm_two_tier_system.json").read_text()))
    quality = PredictionService().predict_quality(result.structural)
    by_id = {c.id: c for c in quality.components}
    assert by_id["RT0"].structural.is_isolated           # what the exemption is for
    flagged = {p.entity_id for p in AntiPatternDetector().detect(quality, "infra")
               if p.evidence.get("isolated")}
    assert not flagged & {"RT0", "RT1"}
