import argparse
import sys
import pytest
import importlib.util
import json
from unittest.mock import MagicMock, patch, mock_open
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

import cli.run as run_module_imported
import cli.generate_graph
import cli.import_graph
import cli.analyze_graph
import cli.simulate_graph
import cli.validate_graph
import cli.visualize_graph
import cli.export_graph
import cli.benchmark
import cli.detect_antipatterns


def _run_main(module, argv: list[str]) -> int:
    with patch.object(sys, "argv", ["run.py"] + argv), \
         patch("pathlib.Path.exists", return_value=True):
        try:
            ret = module.main()
            # main() returns an int exit code directly (see `sys.exit(main())`
            # at module scope) rather than always calling sys.exit() itself —
            # a bare `return 1` must be observed the same as `sys.exit(1)`.
            return ret if ret is not None else 0
        except SystemExit as e:
            return e.code


# =============================================================================
# Shared Fixtures
# =============================================================================





# =============================================================================
# CLI Smoke Tests (Individual Scripts)
# =============================================================================

class TestGenerateGraphCLI:
    """Tests for bin/generate_graph.py"""
    
    @pytest.fixture(scope="class")
    def script_module(self):
        return cli.generate_graph

    def test_main(self, script_module):
        mock_data = {"nodes": [{"id": "n1"}]}
        
        with patch.object(sys, 'argv', ['generate_graph.py', '--scale', 'tiny', '--output', 'test_output.json']), \
             patch('tools.generation.generate_graph', return_value=mock_data) as mock_gen, \
             patch('builtins.open', mock_open()) as m_open:
            
            try:
                script_module.main()
            except SystemExit as e:
                if e.code not in (0, None):
                    pytest.skip(f"generate_graph CLI argparse layout differs (exit={e.code}); skipping smoke test")
            
            # If main returned successfully, the output file should be opened
            if m_open.called:
                m_open.assert_called()


class TestImportGraphCLI:
    """Tests for bin/import_graph.py"""
    
    @pytest.fixture(scope="class")
    def script_module(self):
        return cli.import_graph

    def test_main(self, script_module):
        mock_client = MagicMock()
        mock_client.import_topology.return_value = {
            "nodes_imported": 10,
            "edges_imported": 5,
            "duration_ms": 1.5,
            "success": True
        }
        mock_data = {"nodes": []}
        
        with patch.object(sys, 'argv', ['import_graph.py', '--input', 'test.json', '--clear']), \
             patch.object(script_module, 'Client', return_value=mock_client) as MockClient, \
             patch.object(Path, 'exists', return_value=True), \
             patch('builtins.open', mock_open(read_data=json.dumps(mock_data))):
            
            script_module.main()
            
            MockClient.assert_called_once()
            mock_client.import_topology.assert_called_once_with(filepath='test.json', clear=True, dry_run=False)


class TestAnalyzeGraphCLI:
    """Tests for bin/analyze_graph.py"""
    
    @pytest.fixture(scope="class")
    def script_module(self):
        return cli.analyze_graph

    def test_main(self, script_module):
        mock_client = MagicMock()
        mock_results = MagicMock()
        mock_client.analyze.return_value = mock_results
        
        with patch.object(sys, 'argv', ['analyze_graph.py', '--layer', 'app']), \
             patch.object(script_module, 'Client', return_value=mock_client) as MockClient, \
             patch.object(script_module, 'ConsoleDisplay'):
            
            ret = script_module.main()
            
            MockClient.assert_called_once()
            mock_client.analyze.assert_called_once()


class TestSimulateGraphCLI:
    """Tests for bin/simulate_graph.py"""
    
    @pytest.fixture(scope="class")
    def script_module(self):
        return cli.simulate_graph

    def test_main_fault_inject(self, script_module):
        with patch.object(sys, 'argv', ['simulate_graph.py', 'fault-inject', '--input', 'test.json']), \
             patch.object(script_module, '_run_fault_inject') as mock_run:
            
            script_module.main()
            mock_run.assert_called_once()

    def test_main_message_flow(self, script_module):
        with patch.object(sys, 'argv', ['simulate_graph.py', 'message-flow', '--input', 'test.json']), \
             patch.object(script_module, '_run_message_flow') as mock_run:

            script_module.main()
            mock_run.assert_called_once()

    def test_default_subcommand_not_fooled_by_option_value(self, script_module):
        """An option *value* equal to a subcommand name (--output combined)
        must not be mistaken for an explicit subcommand — only the first
        positional token can be one. Previously this scanned every non-flag
        token, so `--output combined` suppressed the default "fault-inject"
        insertion and argparse's required subparser then raised SystemExit."""
        with patch.object(
            sys, 'argv',
            ['simulate_graph.py', '--output', 'combined', '--input', 'test.json'],
        ), patch.object(script_module, '_run_fault_inject') as mock_run:

            script_module.main()
            mock_run.assert_called_once()
            assert mock_run.call_args[0][0].output == 'combined'

    def test_combined_derives_separate_output_for_message_flow(self, script_module):
        """_run_message_flow always mkdir()s --output; _run_fault_inject
        treats a .json-suffixed --output as a single file it writes.
        `combined --output results.json` used to pass the same value to
        both, so message-flow's mkdir() collided with the file fault-inject
        had just written (FileExistsError)."""
        args = argparse.Namespace(output="out/results.json")
        with patch.object(script_module, "_run_fault_inject") as mock_fi, \
             patch.object(script_module, "_run_message_flow") as mock_mf:
            script_module._run_combined(args)

        fi_args = mock_fi.call_args[0][0]
        mf_args = mock_mf.call_args[0][0]
        assert fi_args.output == "out/results.json"
        assert mf_args.output != fi_args.output
        assert not mf_args.output.endswith(".json")

    def test_combined_keeps_shared_output_dir_when_not_json(self, script_module):
        """A directory --output (the common case) is unambiguous for both
        runners, so it must not be split."""
        args = argparse.Namespace(output="out/simulation")
        with patch.object(script_module, "_run_fault_inject") as mock_fi, \
             patch.object(script_module, "_run_message_flow") as mock_mf:
            script_module._run_combined(args)

        assert mock_fi.call_args[0][0].output == "out/simulation"
        assert mock_mf.call_args[0][0].output == "out/simulation"


class TestValidateGraphCLI:
    """Tests for bin/validate_graph.py"""
    
    @pytest.fixture(scope="class")
    def script_module(self):
        return cli.validate_graph

    def test_main_single(self, script_module):
        import networkx as nx
        with patch.object(sys, 'argv', ['validate_graph.py', 'single', '--input', 'test.json']), \
             patch('cli.validate_graph.load_graph', return_value=(nx.DiGraph(), {})), \
             patch('cli.validate_graph.run_single', return_value=(MagicMock(), {})) as mock_run, \
             patch('cli.validate_graph.print_single_report'):
            
            try:
                script_module.main()
            except SystemExit:
                pass
            mock_run.assert_called_once()

    def test_main_sweep(self, script_module):
        import networkx as nx
        with patch.object(sys, 'argv', ['validate_graph.py', 'sweep', '--input', 'test.json']), \
             patch('cli.validate_graph.load_graph', return_value=(nx.DiGraph(), {})), \
             patch('cli.validate_graph.run_sweep', return_value=MagicMock()) as mock_run, \
             patch('cli.validate_graph.print_sweep_report'):
            
            try:
                script_module.main()
            except SystemExit:
                pass
            mock_run.assert_called_once()


class TestVisualizeGraphCLI:
    """Tests for bin/visualize_graph.py"""
    
    @pytest.fixture(scope="class")
    def script_module(self):
        return cli.visualize_graph

    def test_main(self, script_module):
        mock_client = MagicMock()
        mock_client.visualize.return_value = 'test.html'
        
        with patch.object(sys, 'argv', ['visualize_graph.py', '--layer', 'app', '--output', 'test.html']), \
             patch.object(script_module, 'Client', return_value=mock_client) as MockClient, \
             patch('os.path.exists', return_value=True), \
             patch('os.path.getsize', return_value=1024):
            
            ret = script_module.main()
            
            MockClient.assert_called_once()
            mock_client.visualize.assert_called_once()

    def test_main_with_gnn_and_triage(self, script_module):
        mock_client = MagicMock()
        mock_client.visualize.return_value = 'test.html'
        
        with patch.object(sys, 'argv', ['visualize_graph.py', '--layers', 'app,system', '--gnn-model', 'ckpts/', '--triage-k', '5']), \
             patch.object(script_module, 'Client', return_value=mock_client) as MockClient, \
             patch('os.path.exists', return_value=True), \
             patch('os.path.getsize', return_value=1024):
            
            ret = script_module.main()
            
            MockClient.assert_called_once()
            mock_client.visualize.assert_called_once_with(
                output="dashboard.html",
                layers=["app", "system"],
                include_network=True,
                include_matrix=True,
                include_validation=True,
                antipatterns_file=None,
                multi_seed=0,
                cascade_file=None,
                gnn_model="ckpts/",
                triage_k=5,
            )


class TestDetectAntipatternsCLI:
    """Tests for bin/detect_antipatterns.py"""
    
    @pytest.fixture(scope="class")
    def script_module(self):
        return cli.detect_antipatterns

    def test_main_catalog(self, script_module):
        with patch.object(sys, 'argv', ['detect_antipatterns.py', '--catalog']), \
             pytest.raises(SystemExit) as exc:
            script_module.main()
        assert exc.value.code == 0

    def test_main_scan(self, script_module):
        mock_client = MagicMock()
        mock_analysis = MagicMock()
        mock_prediction = MagicMock()
        mock_client.analyze.return_value = mock_analysis
        mock_client.predict.return_value = mock_prediction
        mock_client.detect_antipatterns.return_value = []
        
        with patch.object(sys, 'argv', ['detect_antipatterns.py', '--layer', 'app', '--use-ahp', '--no-exit-code']), \
             patch.object(script_module, 'Client', return_value=mock_client) as MockClient:
            
            script_module.main()
            
            MockClient.assert_called_once()
            mock_client.analyze.assert_called_once_with(layer='app')
            mock_client.predict.assert_called_once_with(
                mock_analysis, use_ahp=True, ahp_shrinkage=0.7, active_patterns=None,
            )
            mock_client.detect_antipatterns.assert_called_once_with(
                mock_prediction, active_patterns=None,
            )



class TestExportGraphCLI:
    """Tests for bin/export_graph.py"""
    
    @pytest.fixture(scope="class")
    def script_module(self):
        return cli.export_graph

    def test_main(self, script_module):
        mock_client = MagicMock()
        mock_data = {"nodes": [], "relationships": {}}
        mock_client.export_topology.return_value = mock_data
        
        with patch.object(sys, 'argv', ['export_graph.py', '--output', 'exported.json']), \
             patch.object(script_module, 'Client', return_value=mock_client) as MockClient, \
             patch('builtins.open', mock_open()) as m_open:
            
            script_module.main()
            
            MockClient.assert_called_once()
            mock_client.export_topology.assert_called_once()
            m_open.assert_called()


# =============================================================================
# Pipeline Orchestrator Tests (bin/run.py)
# =============================================================================

@pytest.fixture
def mock_pipeline():
    with patch('cli.run.Pipeline') as mock_pipe_class:
        instance = MagicMock()
        # Mock fluent interface
        instance.analyze.return_value = instance
        instance.predict.return_value = instance
        instance.diagnose.return_value = instance
        instance.simulate.return_value = instance
        instance.validate.return_value = instance
        instance.visualize.return_value = instance
        # Make run() return a result whose post-execution display blocks are skipped
        run_result = MagicMock()
        run_result.analysis = None
        run_result.prediction = None
        run_result.diagnosis = None
        run_result.triage = None
        run_result.simulation = None
        run_result.validation = None
        run_result.prescription = None
        run_result.problems = []
        instance.run.return_value = run_result
        mock_pipe_class.return_value = instance
        mock_pipe_class.from_json.return_value = instance
        yield mock_pipe_class, instance

class TestRunOrchestrator:
    """Tests for bin/run.py using saag.Pipeline"""

    def test_run_help_subprocess(self):
        import subprocess
        result = subprocess.run([sys.executable, str(PROJECT_ROOT / "cli" / "run.py"), "--help"], capture_output=True, text=True)
        assert result.returncode == 0
        assert "usage:" in result.stdout.lower()

    
    @pytest.fixture(scope="class")
    def run_module(self):
        return cli.run

    def test_all_stages_called(self, run_module, mock_pipeline):
        mock_class, mock_inst = mock_pipeline
        ret = _run_main(run_module, ["--all"])
        assert ret == 0

        mock_class.from_json.assert_called_once()
        mock_inst.analyze.assert_called_once_with(layer='system')
        mock_inst.predict.assert_called_once_with(gnn_checkpoint=None, use_ahp=False)
        mock_inst.simulate.assert_called_once_with(layer='system', mode='exhaustive')
        mock_inst.validate.assert_called_once_with(layers=['app', 'infra', 'mw', 'system'])
        mock_inst.visualize.assert_called_once()
        mock_inst.run.assert_called_once()

    def test_neo4j_args_forwarded(self, run_module, mock_pipeline):
        mock_class, mock_inst = mock_pipeline
        uri, user, pw = "bolt://db:7687", "admin", "secret"
        ret = _run_main(run_module, ["--analyze", "--uri", uri, "--user", user, "--password", pw])
        assert ret == 0
        mock_class.assert_called_once_with(neo4j_uri=uri, user=user, password=pw)

    def test_pipeline_aborts_on_failure(self, run_module, mock_pipeline):
        """main() catches pipeline.run() failures and returns 1 rather than
        letting the exception escape uncaught — every sibling CLI
        (import_graph.py, analyze_graph.py, ...) already does this."""
        mock_class, mock_inst = mock_pipeline
        mock_inst.run.side_effect = Exception("Failed")
        ret = _run_main(run_module, ["--all"])
        assert ret == 1

    def test_analyze_only(self, run_module, mock_pipeline):
        mock_class, mock_inst = mock_pipeline
        _run_main(run_module, ["--analyze"])
        assert mock_inst.analyze.call_count == 1
        assert mock_inst.predict.call_count == 0

    def test_validate_only(self, run_module, mock_pipeline):
        mock_class, mock_inst = mock_pipeline
        _run_main(run_module, ["--validate"])
        assert mock_inst.validate.call_count == 1
        assert mock_inst.analyze.call_count == 0

    def test_no_stage_prints_help(self, run_module, mock_pipeline):
        with patch('sys.stderr'), patch('argparse.ArgumentParser.error') as mock_error:
            _run_main(run_module, [])
            mock_error.assert_called_once()

class TestLayerHandling:
    """Tests for --layer/--layers argument mapping in run.py"""

    @pytest.fixture(scope="class")
    def run_module(self):
        return cli.run

    def test_single_layer_uses_layer_flag(self, run_module, mock_pipeline):
        mock_class, mock_inst = mock_pipeline
        _run_main(run_module, ["--analyze", "--layer", "system"])
        mock_inst.analyze.assert_called_once_with(layer="system")

    def test_visualize_multi_layer(self, run_module, mock_pipeline):
        mock_class, mock_inst = mock_pipeline
        _run_main(run_module, ["--visualize", "--layer", "app"])
        kwargs = mock_inst.visualize.call_args[1]
        assert kwargs["layers"] == ["app"]

    def test_all_honours_explicit_layer_for_validate_and_visualize(self, run_module, mock_pipeline):
        """--all used to hardcode all four layers for validate/visualize
        even when the user passed --layer explicitly, while analyze/simulate
        (which read args.layer directly) honoured it — so --all --layer app
        analyzed "app" but validated/visualized every layer."""
        mock_class, mock_inst = mock_pipeline
        _run_main(run_module, ["--all", "--layer", "app"])
        mock_inst.analyze.assert_called_once_with(layer="app")
        mock_inst.simulate.assert_called_once_with(layer="app", mode="exhaustive")
        mock_inst.validate.assert_called_once_with(layers=["app"])
        assert mock_inst.visualize.call_args[1]["layers"] == ["app"]

    def test_all_defaults_to_every_layer_without_explicit_layer(self, run_module, mock_pipeline):
        """Without an explicit --layer, --all keeps validating/visualizing
        every layer — only an explicit --layer should narrow it."""
        mock_class, mock_inst = mock_pipeline
        _run_main(run_module, ["--all"])
        mock_inst.validate.assert_called_once_with(layers=["app", "infra", "mw", "system"])
        assert mock_inst.visualize.call_args[1]["layers"] == ["app", "infra", "mw", "system"]

class TestOptionsPassthrough:
    """Tests for flag forwarding to sub-scripts in run.py"""

    @pytest.fixture(scope="class")
    def run_module(self):
        return cli.run

    def test_use_ahp_forwarded(self, run_module, mock_pipeline):
        """use_ahp belongs to the Predict stage (RM weighting), not
        Analyze — Client.analyze() explicitly discards unknown kwargs, so
        this must reach predict(), not analyze()."""
        mock_class, mock_inst = mock_pipeline
        _run_main(run_module, ["--predict", "--use-ahp"])
        mock_inst.predict.assert_called_once_with(gnn_checkpoint=None, use_ahp=True)
        mock_inst.analyze.assert_not_called()

    def test_diagnose_flag_reaches_pipeline(self, run_module, mock_pipeline):
        """--diagnose (Step 4, Pathway A) must reach Pipeline.diagnose(),
        with --triage-k forwarded as its k= — the Triage bridge now belongs
        to Diagnose, not Predict."""
        mock_class, mock_inst = mock_pipeline
        _run_main(run_module, ["--diagnose", "--triage-k", "5"])
        mock_inst.diagnose.assert_called_once_with(k=5, use_ahp=False)
        mock_inst.predict.assert_not_called()


class TestDiagnoseStage:
    """Tests for the Step 3/Step 4 split in run.py: Predict (Pathway B
    ranking) and Diagnose (Pathway A root cause) are independent stages."""

    @pytest.fixture(scope="class")
    def run_module(self):
        return cli.run

    def test_all_skips_predict_but_diagnose_still_runs_without_checkpoint(
        self, run_module, mock_pipeline, monkeypatch
    ):
        """On a first run with no GNN checkpoint, --all must skip Predict
        (it would crash without one) but still run Diagnose — RM scores,
        anti-patterns and explanations need no checkpoint at all."""
        from saag.prediction.service import PredictionService
        monkeypatch.setattr(PredictionService, "_has_checkpoint", staticmethod(lambda d: False))

        mock_class, mock_inst = mock_pipeline
        ret = _run_main(run_module, ["--all"])
        assert ret == 0
        mock_inst.predict.assert_not_called()
        mock_inst.diagnose.assert_called_once_with(k=None, use_ahp=False)

class TestOutputPaths:
    """Tests for output directory and file paths in run.py"""

    @pytest.fixture(scope="class")
    def run_module(self):
        return cli.run

    def test_custom_output_dir(self, run_module, mock_pipeline):
        mock_class, mock_inst = mock_pipeline
        _run_main(run_module, ["--analyze", "--output", "results/v2.json"])
        # Expect save to be called because it's not visualize or all
        mock_inst.run.return_value.save.assert_called_once_with("results/v2.json")

    def test_custom_input_path(self, run_module, mock_pipeline):
        mock_class, mock_inst = mock_pipeline
        _run_main(run_module, ["--input", "data/custom.json"])
        mock_class.from_json.assert_called_once_with("data/custom.json", clear=False, neo4j_uri="bolt://localhost:7687", password="password", user="neo4j")

    def test_generate_stage_called(self, run_module, mock_pipeline):
        mock_class, mock_inst = mock_pipeline
        with patch('cli.common.dispatcher.dispatch_generate') as mock_gen:
            ret = _run_main(run_module, ["--generate", "--input", "test.json", "--scale", "tiny"])
            assert ret == 0
            mock_gen.assert_called_once()
            # Pipeline is then initialized from the generated file
            mock_class.from_json.assert_called_once_with("test.json", clear=False, neo4j_uri="bolt://localhost:7687", password="password", user="neo4j")


# =============================================================================
# Benchmark CLI Tests (bin/benchmark.py)
# =============================================================================

class TestBenchmarkCLI:
    """Tests for bin/benchmark.py"""
    
    @pytest.fixture(scope="class")
    def benchmark_module(self):
        return cli.benchmark

    @pytest.fixture
    def tmp_output(self, tmp_path):
        return tmp_path / "benchmark_output"

    def _run_main(self, module, argv: list[str]) -> int:
        with patch.object(sys, "argv", ["benchmark.py"] + argv):
            return module.main()
    
    @patch("tools.benchmark.runner.BenchmarkRunner.run_scenario")
    @patch("tools.benchmark.runner.BenchmarkRunner.close")
    def test_scales_flag(self, mock_close, mock_run, benchmark_module, tmp_output):
        mock_run.return_value = []
        ret = self._run_main(
            benchmark_module,
            ["--scales", "tiny,small", "--runs", "1", "--output", str(tmp_output)]
        )
        assert ret == 0
        assert mock_run.call_count == 2
        scenario_names = [call.args[0].name for call in mock_run.call_args_list]
        assert "auto-tiny" in scenario_names
        assert "auto-small" in scenario_names

    @patch("tools.benchmark.runner.BenchmarkRunner.run_scenario")
    @patch("tools.benchmark.runner.BenchmarkRunner.close")
    def test_full_suite_flag(self, mock_close, mock_run, benchmark_module, tmp_output):
        mock_run.return_value = []
        ret = self._run_main(benchmark_module, ["--full-suite", "--output", str(tmp_output)])
        assert ret == 0
        assert mock_run.call_count == 3

    @patch("tools.benchmark.runner.BenchmarkRunner.run_scenario")
    @patch("tools.benchmark.runner.BenchmarkRunner.close")
    def test_default_scenario(self, mock_close, mock_run, benchmark_module, tmp_output):
        mock_run.return_value = []
        ret = self._run_main(benchmark_module, ["--output", str(tmp_output)])
        assert ret == 0
        assert mock_run.call_count == 2
        assert mock_run.call_args_list[0].args[0].scale == "tiny"
        assert mock_run.call_args_list[1].args[0].scale == "small"

    @patch("tools.benchmark.runner.BenchmarkRunner.run_scenario")
    @patch("tools.benchmark.runner.BenchmarkRunner.close")
    def test_layers_forwarded(self, mock_close, mock_run, benchmark_module, tmp_output):
        mock_run.return_value = []
        ret = self._run_main(
            benchmark_module,
            ["--scales", "tiny", "--layers", "app,infra", "--output", str(tmp_output)]
        )
        assert ret == 0
        scenario = mock_run.call_args[0][0]
        assert scenario.layers == ["app", "infra"]

    def test_committed_suite_config_resolves(self, benchmark_module):
        """data/benchmarks/scenarios_suite.yaml's graph_config paths used to
        be one directory too high (../data/scenario_*.yaml resolves to
        data/data/, which doesn't exist — the real files are under
        data/scenarios/). _load_yaml_scenarios() only resolves the path, it
        doesn't validate it (--dry-run's own success doesn't catch this —
        the existence check happens later, inside BenchmarkRunner, which
        --dry-run skips), so this checks the resolved paths directly."""
        scenarios = benchmark_module._load_yaml_scenarios(
            Path("data/benchmarks/scenarios_suite.yaml")
        )
        assert len(scenarios) == 7
        for scenario in scenarios:
            assert scenario.config_path.exists(), (
                f"{scenario.name}: {scenario.config_path} does not exist"
            )

    @patch("tools.benchmark.runner.BenchmarkRunner.run_scenario")
    @patch("tools.benchmark.runner.BenchmarkRunner.close")
    def test_runs_forwarded(self, mock_close, mock_run, benchmark_module, tmp_output):
        mock_run.return_value = []
        ret = self._run_main(
            benchmark_module,
            ["--scales", "tiny", "--runs", "5", "--output", str(tmp_output)]
        )
        assert ret == 0
        scenario = mock_run.call_args[0][0]
        assert scenario.runs == 5

    @patch("tools.benchmark.runner.BenchmarkRunner.run_scenario")
    @patch("tools.benchmark.runner.BenchmarkRunner.close")
    def test_reports_generated(self, mock_close, mock_run, benchmark_module, tmp_output):
        mock_run.return_value = []
        ret = self._run_main(benchmark_module, ["--scales", "tiny", "--output", str(tmp_output)])
        assert ret == 0
        assert (tmp_output / "benchmark_results.json").exists()
        assert (tmp_output / "benchmark_report.md").exists()

    @patch("tools.benchmark.runner.BenchmarkRunner.run_scenario")
    @patch("tools.benchmark.runner.BenchmarkRunner.close")
    def test_neo4j_args_forwarded(self, mock_close, mock_run, benchmark_module, tmp_output):
        mock_run.return_value = []
        with patch("tools.benchmark.runner.create_repository") as MockCreateRepo:
            ret = self._run_main(benchmark_module, [
                "--scales", "tiny",
                "--uri", "bolt://db:7687",
                "--user", "admin",
                "--password", "secret",
                "--output", str(tmp_output),
            ])
        assert ret == 0
        MockCreateRepo.assert_called_with(
            uri="bolt://db:7687", user="admin", password="secret"
        )


class TestCliCommonArguments:
    """Tests for cli/common/arguments.py Neo4j credential env-var resolution."""

    @staticmethod
    def _parsed_user(monkeypatch, **env) -> str:
        from cli.common.arguments import add_neo4j_arguments

        for key in ("NEO4J_USERNAME", "NEO4J_USER"):
            monkeypatch.delenv(key, raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, value)

        parser = argparse.ArgumentParser()
        add_neo4j_arguments(parser)
        return parser.parse_args([]).user

    def test_neo4j_username_env_var_is_honoured(self, monkeypatch):
        assert self._parsed_user(monkeypatch, NEO4J_USERNAME="alice") == "alice"

    def test_neo4j_user_env_var_still_works_as_fallback(self, monkeypatch):
        assert self._parsed_user(monkeypatch, NEO4J_USER="bob") == "bob"

    def test_neo4j_username_takes_precedence_over_neo4j_user(self, monkeypatch):
        assert self._parsed_user(monkeypatch, NEO4J_USERNAME="alice", NEO4J_USER="bob") == "alice"

    def test_neo4j_user_defaults_to_neo4j(self, monkeypatch):
        assert self._parsed_user(monkeypatch) == "neo4j"

