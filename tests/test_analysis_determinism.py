"""Q(v) must not depend on PYTHONHASHSEED.

The CDI baseline used to pick its 16 highest-degree sample sources from a set
with a degree-only sort key, so equal-degree nodes kept set-iteration order --
salted per process -- and Q(v) moved by up to 4.4e-4 between runs of the same
code on the same input (atm_system: 17-18 of 74 components).
"""

import subprocess
import sys

#: Hash seeds that produced three different Q vectors on atm_system before the fix.
_HASH_SEEDS = (0, 2, 3)

_SNIPPET = """
import copy, json
from saag.analysis.service import AnalysisService
from saag.infrastructure.memory_repo import MemoryRepository
from saag.prediction.service import PredictionService
repo = MemoryRepository()
repo.save_graph(copy.deepcopy(json.load(open('data/scenarios/atm_system.json'))), clear=True)
quality = PredictionService().predict_quality(AnalysisService(repo).analyze_layer('system').structural)
print(sorted((c.id, repr(c.scores.overall), repr(c.structural.cdi)) for c in quality.components))
"""


def test_q_is_identical_across_hash_seeds():
    runs = {
        subprocess.run(
            [sys.executable, "-c", _SNIPPET],
            capture_output=True, text=True, check=True,
            env={"PYTHONHASHSEED": str(h), "PYTHONPATH": "."},
        ).stdout
        for h in _HASH_SEEDS
    }
    assert len(runs) == 1, "Q(v) / CDI vary with PYTHONHASHSEED"
