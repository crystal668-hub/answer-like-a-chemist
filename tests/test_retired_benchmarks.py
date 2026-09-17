"""Only pinned verifier-grounded tracks remain executable."""
from importlib.util import find_spec
from pathlib import Path

import pytest

from benchmarking.core.records import BenchmarkRecord, load_records
from benchmarking.runtime.vgb_bridge import load_release_config
from benchmarking.scoring.errors import EvaluationRegistryError
from benchmarking.scoring.registry import DEFAULT_EVALUATORS, evaluate_record
from benchmarking.service.chemdebate.prompts import build_chemqa_goal
from benchmarking.service.chemdebate.runner import ChemQARunner
from benchmarking.service.single.runner import validate_candidate_answer_contract

ROOT = Path(__file__).resolve().parents[1]


def test_non_vgb_scoring_and_chemqa_prompt_are_rejected() -> None:
    record = BenchmarkRecord(
        record_id="legacy",
        track="legacy:chembench",
        source_file="fixture",
        eval_kind="generic_semantic",
        prompt="Question?",
        reference_answer="A",
    )
    with pytest.raises(EvaluationRegistryError, match="No evaluator"):
        evaluate_record(
            record,
            short_answer_text="A",
            full_response_text="A",
            judge=None,
            evaluators=DEFAULT_EVALUATORS,
        )
    with pytest.raises(ValueError, match="only verifier-grounded"):
        build_chemqa_goal(record, websearch_enabled=False)


@pytest.mark.parametrize("name", ["chembench", "frontierscience", "generic", "hle", "superchem"])
def test_non_vgb_evaluator_modules_are_removed(name: str) -> None:
    assert find_spec(f"benchmarking.scoring.evaluators.{name}") is None


@pytest.mark.parametrize("track", list(load_release_config().tracks))
def test_vgb_tracks_remain_supported(track: str) -> None:
    path = ROOT / "benchmarking/resources/verifier_grounded/tracks" / f"{track}.jsonl"
    candidate = load_records([path])[0]
    assert candidate.track == track
    assert build_chemqa_goal(candidate, websearch_enabled=False).endswith(candidate.prompt)
    old_answer = "Explanation: checked.\nAnswer: CCO\nConfidence: 90%"
    assert not validate_candidate_answer_contract(
        record=candidate,
        short_answer_text="",
        full_response_text=old_answer,
        runner_meta={},
    ).valid


def test_chemqa_runner_rejects_non_vgb_before_workspace_allocation() -> None:
    record = BenchmarkRecord(
        record_id="legacy",
        track="legacy:hle",
        source_file="fixture",
        eval_kind="generic_semantic",
        prompt="Question?",
        reference_answer="A",
    )
    runner = object.__new__(ChemQARunner)
    with pytest.raises(ValueError, match="only verifier-grounded"):
        runner.run(record, object())
