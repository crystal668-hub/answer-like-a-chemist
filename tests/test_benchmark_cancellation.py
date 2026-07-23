from __future__ import annotations

import json
import os
import signal
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmarking.core.answer_processing import normalize_answer_tracks
from benchmarking.core.contracts import AnswerPayload, RunnerResult, RunStatus
from benchmarking.core.datasets import BenchmarkRecord
from benchmarking.core.reporting import build_error_group_record_result
from benchmarking.runtime import subprocess_utils
from benchmarking.runtime.cancellation import (
    BenchmarkCancelledError,
    CancellationReason,
    CancellationToken,
    OwnedProcessRegistry,
)
from benchmarking.scoring.results import build_execution_error_evaluation
from benchmarking.workflow.orchestration import run_group
from benchmarking.workflow.cli import install_cancellation_signal_handlers, restore_signal_handlers
from benchmarking.workflow import runner_adapters
from benchmarking.runtime.judge import JudgeClient


def _record(record_id: str) -> BenchmarkRecord:
    return BenchmarkRecord(
        record_id=record_id,
        dataset="chembench",
        source_file="/tmp/demo.jsonl",
        eval_kind="chembench_open_ended",
        prompt="Q",
        reference_answer="A",
        payload={},
    )


def _error_result(**kwargs):
    return build_error_group_record_result(
        **kwargs,
        classify_subset_fn=lambda _record: "chembench",
        normalize_answer_tracks_fn=normalize_answer_tracks,
        build_execution_error_evaluation_fn=build_execution_error_evaluation,
        deep_copy_jsonish_fn=lambda value: json.loads(json.dumps(value)),
    )


def test_cancellation_reason_is_first_writer_wins_and_idempotent() -> None:
    token = CancellationToken()
    first = CancellationReason(source="signal", signal_name="SIGINT", message="first")
    second = CancellationReason(source="signal", signal_name="SIGTERM", message="second")

    assert token.cancel(first) is True
    assert token.cancel(second) is False
    assert token.reason == first
    assert token.request_count == 2
    with pytest.raises(BenchmarkCancelledError, match="first"):
        token.raise_if_cancelled()


def test_signal_handlers_record_first_signal_and_restore(monkeypatch) -> None:
    token = CancellationToken()
    installed: dict[signal.Signals, object] = {}
    restored: list[tuple[signal.Signals, object]] = []

    monkeypatch.setattr(signal, "getsignal", lambda sig: f"previous-{sig.name}")

    def capture(sig, handler):
        if callable(handler):
            installed[sig] = handler
        else:
            restored.append((sig, handler))

    monkeypatch.setattr(signal, "signal", capture)
    previous = install_cancellation_signal_handlers(token)
    installed[signal.SIGINT](signal.SIGINT, None)
    installed[signal.SIGTERM](signal.SIGTERM, None)
    restore_signal_handlers(previous)

    assert token.reason is not None
    assert token.reason.signal_name == "SIGINT"
    assert token.request_count == 2
    assert restored == [
        (signal.SIGINT, "previous-SIGINT"),
        (signal.SIGTERM, "previous-SIGTERM"),
    ]


def test_runners_and_judge_expose_uniform_cancellation_interface() -> None:
    for runner_type in (runner_adapters.SingleLLMRunner, runner_adapters.ChemQARunner, JudgeClient):
        assert callable(getattr(runner_type, "cancel"))
        assert callable(getattr(runner_type, "wait_cancelled"))


def test_owned_process_registry_terminates_parent_and_child_process_group(tmp_path: Path) -> None:
    token = CancellationToken()
    registry = OwnedProcessRegistry(cancellation_token=token, grace_seconds=0.2)
    child_pid_path = tmp_path / "child.pid"
    script = (
        "import pathlib, subprocess, sys, time; "
        "child=subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
        f"pathlib.Path({str(child_pid_path)!r}).write_text(str(child.pid)); "
        "time.sleep(60)"
    )
    caught: list[Exception] = []

    def invoke() -> None:
        try:
            subprocess_utils.run_subprocess(
                [sys.executable, "-c", script],
                timeout=60,
                cancellation_token=token,
                process_registry=registry,
            )
        except Exception as exc:
            caught.append(exc)

    worker = threading.Thread(target=invoke)
    worker.start()
    deadline = time.monotonic() + 5
    while not child_pid_path.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert child_pid_path.exists()
    child_pid = int(child_pid_path.read_text())

    token.cancel(CancellationReason(source="test"))
    outcome = registry.terminate_all()
    worker.join(timeout=5)

    assert outcome.completed is True
    assert worker.is_alive() is False
    assert any(isinstance(exc, BenchmarkCancelledError) for exc in caught)
    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, 0)


def test_run_group_stops_scheduling_and_materializes_cancelled_records(tmp_path: Path) -> None:
    token = CancellationToken()
    calls: list[str] = []
    group = SimpleNamespace(
        id="single_llm_skills_off",
        label="Single",
        runner="single_llm",
        websearch=False,
        skills_enabled=False,
    )

    class Runner:
        def run(self, record, actual_group):
            calls.append(record.record_id)
            token.cancel(CancellationReason(source="test"))
            return RunnerResult(
                status=RunStatus.COMPLETED,
                answer=AnswerPayload(short_answer_text="A", full_response_text="FINAL ANSWER: A"),
                runner_meta={},
                raw={},
            )

        def cancel(self, reason):
            token.cancel(reason)

        def wait_cancelled(self, deadline):
            return None

    results = run_group(
        group=group,
        records=[_record("r1"), _record("r2")],
        output_root=tmp_path,
        single_timeout=10,
        chemqa_timeout=10,
        judge=object(),
        config_path=tmp_path / "config.json",
        single_agent="agent",
        chemqa_root=tmp_path,
        chemqa_model_profile="unused",
        review_rounds=None,
        rebuttal_rounds=None,
        chemqa_slot_sets={},
        experiment_specs={group.id: SimpleNamespace(skill_allowlist=())},
        build_runner_fn=lambda **_kwargs: Runner(),
        evaluate_answer_fn=lambda *_args, **_kwargs: pytest.fail("cancelled record reached evaluator"),
        build_error_group_record_result_fn=_error_result,
        classify_subset_fn=lambda _record: "chembench",
        save_json_fn=lambda path, payload: (
            path.parent.mkdir(parents=True, exist_ok=True),
            path.write_text(json.dumps(payload), encoding="utf-8"),
        ),
        slugify_fn=str,
        single_agent_thinking="minimal",
        cancellation_token=token,
    )

    assert calls == ["r1"]
    assert [result.run_lifecycle_status for result in results] == ["cancelled", "cancelled"]
    assert all(result.execution_error_kind == "cancelled" for result in results)
    assert all(result.scored is False and result.evaluable is False for result in results)
    assert all(result.evaluation["score"] is None for result in results)
