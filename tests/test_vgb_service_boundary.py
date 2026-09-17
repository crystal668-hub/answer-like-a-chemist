"""Active VGB admission and composition must not inherit frozen capabilities."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from benchmarking.core.datasets import load_records
from benchmarking.runtime.vgb_bridge import load_release_config
from benchmarking.scoring.errors import EvaluationRegistryError
from benchmarking.scoring.registry import evaluate_record, legacy_evaluators
from benchmarking.service.single import execution
from benchmarking.workflow import cli, dataset_selection
from benchmarking.workflow.errors import BenchmarkError

ROOT = Path(__file__).resolve().parents[1]
RESOURCE = ROOT / 'benchmarking/resources/verifier_grounded'


def test_default_import_closure_excludes_legacy_evaluators_and_judge():
    code = '''
import json, sys
from benchmarking.workflow import cli
from benchmarking.service.single import execution, adapter
print(json.dumps([name for name in sys.modules if name.startswith((
    "benchmarking.service.chemdebate", "benchmarking.runtime.judge",
    "benchmarking.scoring.evaluators.chembench", "benchmarking.scoring.evaluators.frontierscience",
    "benchmarking.scoring.evaluators.hle", "benchmarking.scoring.evaluators.superchem",
    "benchmarking.scoring.evaluators.generic"))]))
'''
    result = subprocess.run([sys.executable, '-c', code], cwd=ROOT, text=True, capture_output=True, check=True)
    assert json.loads(result.stdout) == []


def test_release_inventory_matches_canonical_directories():
    release = load_release_config()
    assert {value['dataset'] for value in release.tracks.values()} == set(dataset_selection.CANONICAL_BENCHMARK_NAMES)
    assert len(release.tracks) == 4


@pytest.mark.parametrize('flag', ['--subsets', '--random-count-per-subset', '--random-seed',
                                  '--judge-agent', '--judge-model', '--judge-timeout', '--judge-agent-thinking'])
def test_default_cli_rejects_legacy_options(monkeypatch, flag):
    monkeypatch.setattr(sys, 'argv', ['benchmark', flag, '1'])
    with pytest.raises(SystemExit):
        cli.parse_args()


def test_frozen_entrypoint_paths_and_scoring_are_retained(monkeypatch):
    from benchmarking.service.chemdebate import cli as legacy_cli, execution as frozen
    assert callable(legacy_cli.main)
    assert set(frozen.experiments.EXPERIMENT_GROUPS) == {'chemqa_skills_on'}
    assert frozen.experiments.EXPERIMENT_GROUPS['chemqa_skills_on'].runner == 'chemqa'
    for bundle in ('chemqa-review', 'debateclaw-v1', 'benchmark-cleanroom'):
        assert (ROOT / 'skills' / bundle / 'SKILL.md').is_file()
    assert 'generic_semantic' in frozen.evaluator_registry()
    monkeypatch.setattr(sys, 'argv', ['legacy', '--subsets', 'hle_chemistry', '--judge-timeout', '123'])
    args = cli.parse_args(frozen)
    assert args.subsets == 'hle_chemistry' and args.judge_timeout == 123


def test_invocation_registry_does_not_fall_back_to_global_legacy_registry(monkeypatch):
    from benchmarking.scoring import registry
    monkeypatch.setattr(registry, 'EVALUATORS', legacy_evaluators())
    record = SimpleNamespace(grading=SimpleNamespace(kind='chembench_open_ended'))
    assert set(execution.evaluator_registry()) == {'verifier_grounded'}
    with pytest.raises(EvaluationRegistryError):
        evaluate_record(record, short_answer_text='x', full_response_text='x', judge=None,
                        evaluators=execution.evaluator_registry())


@pytest.fixture
def vgb_file(tmp_path):
    source = next(RESOURCE.rglob('verifier_grounded_rdkit.jsonl'))
    path = tmp_path / 'input/verifier_grounded_rdkit/data/verifier_grounded_rdkit.jsonl'
    path.parent.mkdir(parents=True)
    path.write_text(source.read_text())
    return path


def test_valid_records_and_explicit_files_are_accepted(vgb_file):
    records = execution.select_records([vgb_file], SimpleNamespace())
    assert len(records) == load_release_config().tracks['rdkit']['task_count']
    args = SimpleNamespace(benchmark_root='', files=str(vgb_file), datasets=None)
    assert execution.select_dataset_files(args) == [vgb_file]


@pytest.mark.parametrize('mutation', ['dataset', 'eval_kind', 'track', 'task_id', 'release'])
def test_record_validation_precedes_filtering(monkeypatch, vgb_file, mutation):
    record = deepcopy(load_records([vgb_file])[0])
    if mutation in ('dataset', 'eval_kind'):
        record = replace(record, **{mutation: 'chembench'})
    else:
        record.payload['verifier_grounded'][mutation] = 'invalid'
    monkeypatch.setattr(dataset_selection, 'load_records', lambda files: [record])
    with pytest.raises(BenchmarkError, match='Invalid VGB record'):
        execution.select_records([vgb_file], SimpleNamespace(record_ids='other', limit=0))


def test_discovery_filters_legacy_and_explicit_files_validate_content(tmp_path, vgb_file):
    dataset = tmp_path / 'verifier_grounded_rdkit/data/verifier_grounded_rdkit.jsonl'
    dataset.parent.mkdir(parents=True)
    dataset.write_text(vgb_file.read_text())
    legacy = tmp_path / 'chembench/data/chembench.jsonl'
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps({'id': 'old', 'prompt': 'Q', 'eval_kind': 'chembench_open_ended', 'answer': 'A'}) + '\n')
    args = SimpleNamespace(benchmark_root=str(tmp_path), files=None, datasets=None)
    assert execution.select_dataset_files(args) == [dataset]
    args.datasets = 'chembench'
    with pytest.raises(BenchmarkError, match='Unsupported VGB dataset'):
        execution.select_dataset_files(args)
    args.datasets = None
    args.files = str(legacy)
    with pytest.raises(BenchmarkError, match='Invalid VGB record'):
        execution.select_dataset_files(args)


def test_adapter_never_materializes_dataset_bundle(monkeypatch, tmp_path):
    from benchmarking.runtime import bundles
    from benchmarking.service.single.adapter import SingleLLMRunner
    monkeypatch.setattr(bundles, 'ensure_runtime_bundle', lambda *a, **k: pytest.fail('bundle materialized'))
    runner = SingleLLMRunner(agent_id='test', timeout_seconds=1, config_path=tmp_path / 'config.json',
                             runtime_bundle_root=tmp_path, execution_backend='host')
    assert runner._ensure_runtime_bundle(object(), bundle_root=tmp_path) is None
    with pytest.raises(ValueError, match='requires eval_kind=verifier_grounded'):
        runner.run(SimpleNamespace(eval_kind='chembench_open_ended'), object())


def test_frozen_record_selection_filters_ids_before_sampling(monkeypatch):
    from benchmarking.core.datasets import BenchmarkRecord
    from benchmarking.service.chemdebate import execution as frozen
    records = [BenchmarkRecord(record_id=str(i), dataset='chembench', source_file='fixture',
                               prompt='Q', eval_kind='chembench_open_ended', reference_answer='A')
               for i in range(4)]
    monkeypatch.setattr(dataset_selection, 'load_records', lambda files: records)
    args = SimpleNamespace(subsets='chembench', record_ids='3,1', random_count_per_subset=1, random_seed=0)
    selected = frozen.select_records([], args)
    assert len(selected) == 1 and selected[0].record_id in {'3', '1'}
