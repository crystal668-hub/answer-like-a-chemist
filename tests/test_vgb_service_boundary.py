"""Active and frozen services share the pinned Track-only admission boundary."""
import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmarking.core.records import load_records
from benchmarking.runtime.vgb_bridge import load_release_config
from benchmarking.scoring.errors import EvaluationRegistryError
from benchmarking.scoring.registry import evaluate_record
from benchmarking.service.single import execution
from benchmarking.workflow import cli, track_selection
from benchmarking.workflow.errors import BenchmarkError

ROOT = Path(__file__).resolve().parents[1]
RESOURCE = ROOT / "benchmarking/resources/verifier_grounded/tracks"


def test_default_import_closure_excludes_non_vgb_evaluators_and_judge() -> None:
    code = """
import json, sys
from benchmarking.workflow import cli
from benchmarking.service.single import execution, adapter
print(json.dumps([name for name in sys.modules if name.startswith((
    "benchmarking.service.chemdebate", "benchmarking.runtime.judge",
    "benchmarking.scoring.evaluators.generic"))]))
"""
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, text=True, capture_output=True, check=True
    )
    assert json.loads(result.stdout) == []


def test_release_inventory_is_the_canonical_track_table() -> None:
    release = load_release_config()
    assert list(release.tracks) == [
        "rdkit",
        "xtb",
        "property_calculation_advanced",
        "property_calculation_basic",
    ]
    assert set(release.tracks) == set(track_selection.CANONICAL_BENCHMARK_NAMES)
    assert all("dataset" not in value for value in release.tracks.values())


@pytest.mark.parametrize(
    "flag",
    [
        "--datasets",
        "--subsets",
        "--list-datasets",
        "--judge-agent",
        "--judge-model",
        "--judge-timeout",
        "--judge-agent-thinking",
    ],
)
def test_default_cli_rejects_removed_options(monkeypatch: pytest.MonkeyPatch, flag: str) -> None:
    monkeypatch.setattr(sys, "argv", ["benchmark", flag, "value"])
    with pytest.raises(SystemExit):
        cli.parse_args()


def test_frozen_entrypoint_is_vgb_only(monkeypatch: pytest.MonkeyPatch) -> None:
    from benchmarking.service.chemdebate import cli as legacy_cli
    from benchmarking.service.chemdebate import execution as frozen

    assert callable(legacy_cli.main)
    assert set(frozen.evaluator_registry()) == {"verifier_grounded"}
    monkeypatch.setattr(sys, "argv", ["legacy", "--tracks", "rdkit"])
    assert cli.parse_args(frozen).tracks == "rdkit"


def test_invocation_registry_has_no_generic_fallback() -> None:
    record = SimpleNamespace(grading=SimpleNamespace(kind="generic_semantic"))
    with pytest.raises(EvaluationRegistryError, match="No evaluator"):
        evaluate_record(
            record,
            short_answer_text="x",
            full_response_text="x",
            judge=None,
            evaluators=execution.evaluator_registry(),
        )


@pytest.fixture
def vgb_file(tmp_path: Path) -> Path:
    source = RESOURCE / "rdkit.jsonl"
    path = tmp_path / "rdkit/data/rdkit.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    return path


def test_valid_records_and_explicit_files_are_accepted(vgb_file: Path) -> None:
    records = execution.select_records([vgb_file], SimpleNamespace(record_ids=None))
    assert len(records) == load_release_config().tracks["rdkit"]["task_count"]
    args = SimpleNamespace(benchmark_root="", files=str(vgb_file), tracks=None)
    assert execution.select_track_files(args) == [vgb_file]


@pytest.mark.parametrize("mutation", ["record_track", "eval_kind", "track", "task_id", "release"])
def test_record_validation_precedes_filtering(
    monkeypatch: pytest.MonkeyPatch,
    vgb_file: Path,
    mutation: str,
) -> None:
    record = deepcopy(load_records([vgb_file])[0])
    if mutation == "record_track":
        record.track = "xtb"
    elif mutation == "eval_kind":
        record.eval_kind = "generic_semantic"
    else:
        record.payload["verifier_grounded"][mutation] = "invalid"
        record.grading.config["verifier_grounded"][mutation] = "invalid"
    monkeypatch.setattr(track_selection, "load_records", lambda _files: [record])
    with pytest.raises(BenchmarkError, match="Invalid VGB record"):
        execution.select_records([vgb_file], SimpleNamespace(record_ids="other"))


def test_discovery_requires_canonical_track_layout(tmp_path: Path, vgb_file: Path) -> None:
    canonical = tmp_path / "rdkit/data/rdkit.jsonl"
    canonical.parent.mkdir(parents=True, exist_ok=True)
    canonical.write_text(vgb_file.read_text(encoding="utf-8"), encoding="utf-8")
    args = SimpleNamespace(benchmark_root=str(tmp_path), files=None, tracks="rdkit")
    assert execution.select_track_files(args) == [canonical.resolve()]

    args.tracks = "chembench"
    with pytest.raises(BenchmarkError, match="Unsupported VGB track"):
        execution.select_track_files(args)

    legacy = tmp_path / "verifier_grounded_rdkit/data/verifier_grounded_rdkit.jsonl"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(vgb_file.read_text(encoding="utf-8"), encoding="utf-8")
    args.tracks = None
    args.files = str(legacy)
    with pytest.raises(BenchmarkError, match="Unsupported VGB track file"):
        execution.select_track_files(args)


def test_adapter_never_materializes_track_bundle(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from benchmarking.runtime import bundles
    from benchmarking.service.single.adapter import SingleLLMRunner

    monkeypatch.setattr(
        bundles,
        "ensure_runtime_bundle",
        lambda *args, **kwargs: pytest.fail("bundle materialized"),
    )
    runner = SingleLLMRunner(
        agent_id="test",
        timeout_seconds=1,
        config_path=tmp_path / "config.json",
        runtime_bundle_root=tmp_path,
        execution_backend="host",
    )
    assert runner._ensure_runtime_bundle(object(), bundle_root=tmp_path) is None
