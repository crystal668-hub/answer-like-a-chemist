"""Retirement removes old execution and filter choices without removing VGB/history."""
from importlib.util import find_spec
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from benchmarking.core.convergence import is_complete_answer_for_eval, is_complete_rescue_answer
from benchmarking.core.datasets import BenchmarkRecord, is_retired_benchmark, load_records
from benchmarking.scoring.errors import EvaluationRegistryError
from benchmarking.scoring.registry import evaluate_record, legacy_evaluators
from benchmarking.service.chemdebate import execution as frozen
from benchmarking.service.chemdebate.prompts import build_chemqa_goal, resolve_chemqa_answer_kind
from benchmarking.service.single.runner import validate_candidate_answer_contract, verifier_grounded_answer_schema_from_record
from benchmarking.runtime.vgb_bridge import load_release_config
from benchmarking.workflow.errors import BenchmarkError

ROOT = Path(__file__).resolve().parents[1]
RETIRED = [('chembench', 'chembench_open_ended'), ('frontierscience', 'frontierscience_olympiad'),
           ('frontierscience', 'frontierscience_research'), ('hle', 'hle'),
           ('superchem', 'superchem_multiple_choice_rpf')]


def record(dataset='custom', kind='generic_semantic'):
    return BenchmarkRecord(record_id='r1', dataset=dataset, eval_kind=kind,
                           source_file='fixture', prompt='Question?', reference_answer='A')


@pytest.mark.parametrize('dataset,kind', RETIRED)
def test_retired_scoring_cannot_use_generic_or_override(dataset, kind):
    judge = Mock()
    override = Mock()
    for candidate in (record(dataset, kind), record('custom', kind), record(dataset, 'generic_semantic')):
        with pytest.raises(EvaluationRegistryError, match='retired'):
            evaluate_record(candidate, short_answer_text='A', full_response_text='A', judge=judge,
                            evaluators=legacy_evaluators(), evaluator_overrides={candidate.eval_kind: override})
    judge.evaluate_json.assert_not_called()
    override.assert_not_called()


@pytest.mark.parametrize('dataset,kind', RETIRED)
def test_retired_chemqa_prompt_and_selection_rejected(monkeypatch, dataset, kind):
    candidate = record(dataset, kind)
    candidate.payload['answer_kind'] = 'generic_semantic_answer'
    with pytest.raises(ValueError, match='retired'):
        build_chemqa_goal(candidate, websearch_enabled=False)
    from benchmarking.workflow import dataset_selection
    monkeypatch.setattr(dataset_selection, 'load_records', lambda files: [candidate])
    with pytest.raises(BenchmarkError, match='retired'):
        frozen.select_records([], SimpleNamespace(subsets=None, record_ids=None))


@pytest.mark.parametrize('name', ['chembench', 'frontierscience', 'hle', 'superchem'])
def test_retired_evaluator_modules_are_removed(name):
    assert find_spec('benchmarking.scoring.evaluators.' + name) is None
    assert not (ROOT / 'benchmarking/scoring/evaluators' / (name + '.py')).exists()


def test_remaining_frozen_scoring_and_explicit_answer_kinds():
    assert set(legacy_evaluators()) == {'generic_semantic', 'verifier_grounded'}
    candidate = record()
    candidate.payload['answer_kind'] = 'formula_short_answer'
    assert resolve_chemqa_answer_kind(candidate) == 'formula_short_answer'
    assert candidate.prompt in build_chemqa_goal(candidate, websearch_enabled=False)


@pytest.mark.parametrize('track', list(load_release_config().tracks))
def test_vgb_prompts_and_answers_remain_supported_without_hle_fallback(track):
    dataset = load_release_config().tracks[track]['dataset']
    candidate = load_records([ROOT / 'benchmarking/resources/verifier_grounded/datasets' / (dataset + '.jsonl')])[0]
    candidate.dataset = dataset
    assert not is_retired_benchmark(dataset=dataset, eval_kind=candidate.eval_kind)
    assert resolve_chemqa_answer_kind(candidate) == 'verifier_grounded_candidate'
    assert build_chemqa_goal(candidate, websearch_enabled=False).endswith(candidate.prompt)
    schema = verifier_grounded_answer_schema_from_record(candidate)
    old_answer = 'Explanation: checked.\nAnswer: CCO\nConfidence: 90%'
    for check in (is_complete_answer_for_eval, is_complete_rescue_answer):
        assert not check(old_answer, eval_kind='verifier_grounded', answer_schema=schema)
    assert not validate_candidate_answer_contract(record=candidate, short_answer_text='',
        full_response_text=old_answer, runner_meta={}).valid
    answer = ('FINAL ANSWER:\n```xyz\n3\nwater\nO 0 0 0\nH 0 0 1\nH 0 1 0\n```'
              if track == 'xtb' else 'FINAL ANSWER: {"answer": 1}')
    assert validate_candidate_answer_contract(record=candidate, short_answer_text='',
        full_response_text=answer, runner_meta={}).valid


def test_policy_is_exact_and_skill_keeps_general_chemistry_workflow():
    assert is_retired_benchmark(dataset=' HLE ')
    assert is_retired_benchmark(subset='frontierscience_Research')
    assert not is_retired_benchmark(dataset='custom_hle_study', subset='superchemistry')
    candidate = record('verifier_grounded_rdkit', 'verifier_grounded')
    candidate.prompt = 'Discuss chembench, frontierscience, hle and superchem as names.'
    assert build_chemqa_goal(candidate, websearch_enabled=False).endswith(candidate.prompt)
    skill = (ROOT / 'skills/act-like-a-chemist/SKILL.md').read_text()
    assert '### HLE Tasks' not in skill
    assert 'Atomic Coverage Checklist' in skill and 'Numerical Discipline' in skill


def test_chemqa_direct_runner_rejects_before_workspace_allocation():
    from benchmarking.core.datasets import GradingSpec
    from benchmarking.service.chemdebate.runner import ChemQARunner
    runner = object.__new__(ChemQARunner)
    subset_record = BenchmarkRecord(record_id='r1', dataset='custom', source_file='fixture',
        prompt='Q', grading=GradingSpec(kind='generic_semantic', subset='hle_chemistry', reference_answer='A'))
    for candidate in (record('hle', 'hle'), subset_record):
        with pytest.raises(ValueError, match='retired'):
            runner.run(candidate, object())
