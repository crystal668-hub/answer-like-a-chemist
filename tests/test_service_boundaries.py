"""Business isolation and retained historical result contracts."""
from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmarking.workflow import cli, experiments, run_state
from benchmarking.workflow.errors import BenchmarkError

ROOT = Path(__file__).resolve().parents[1]


def test_single_import_and_cli_do_not_load_legacy():
    code = '''
import sys, json
from benchmarking.workflow import cli
from benchmarking.service.single.adapter import SingleLLMRunner
sys.argv = ["benchmark"]
args = cli.parse_args()
print(json.dumps({"groups": args.groups, "legacy": [m for m in sys.modules if "chemdebate" in m], "legacy_args": hasattr(args, "chemqa_root")}))
'''
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True)
    payload = json.loads(result.stdout)
    assert payload == {"groups": "single_llm_skills_on,single_llm_skills_off", "legacy": [], "legacy_args": False}


def test_active_catalog_rejects_legacy_and_legacy_entrypoint_is_explicit(monkeypatch):
    with pytest.raises(BenchmarkError, match="legacy"):
        experiments.select_group_ids("chemqa_skills_on")
    from benchmarking.service.chemdebate import execution
    monkeypatch.setattr(sys, "argv", ["legacy"])
    args = cli.parse_args(execution)
    assert args.groups == "chemqa_skills_on"
    assert execution.STATUS == "legacy-frozen"
    assert "chemqa-role-v1" in execution.workspace_templates(ROOT)
    assert execution.convergence_metadata(args)["chemqa"]["max_recovery_attempts"] == 2


def test_historical_group_description_does_not_need_runtime_registration():
    record = SimpleNamespace(group_id="chemqa_skills_on", group_label="Historical ChemQA",
                             runner="chemqa", websearch=False, skills_enabled=True)
    assert run_state.describe_result_group(record.group_id, [record], experiments.EXPERIMENT_GROUPS) == {
        "id": record.group_id, "label": record.group_label, "runner": "chemqa",
        "websearch": False, "skills_enabled": True,
    }


def test_business_import_direction():
    for folder, forbidden in [
        (ROOT / "benchmarking/runtime", "benchmarking.service"),
        (ROOT / "benchmarking/core", "benchmarking.service"),
        (ROOT / "benchmarking/service/single", "benchmarking.service.chemdebate"),
        (ROOT / "benchmarking/service/chemdebate", "benchmarking.service.single"),
    ]:
        for path in folder.rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.ImportFrom):
                    assert not (node.module or "").startswith(forbidden), path
                elif isinstance(node, ast.Import):
                    assert not any(alias.name.startswith(forbidden) for alias in node.names), path


def test_relocated_single_wrapper_runs_directly():
    path = ROOT / "benchmarking/service/single/openclaw_wrapper.py"
    result = subprocess.run([sys.executable, str(path), "--help"], cwd=ROOT,
                            capture_output=True, text=True, check=True)
    assert "--session-id" in result.stdout


def test_resume_keeps_retired_groups_discovered_on_disk(tmp_path):
    legacy = tmp_path / "per-record" / "chemqa_skills_on"
    legacy.mkdir(parents=True)
    (legacy / "record.json").write_text("{}")
    assert run_state.resolve_aggregate_group_ids(
        ["single_llm_skills_off"], output_root=tmp_path, merge_existing_per_record=True
    ) == ["single_llm_skills_off", "chemqa_skills_on"]
