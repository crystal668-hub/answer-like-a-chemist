from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import venv
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from benchmarking.runtime import vgb_bridge as bridge
from benchmarking.runtime import vgb_worker as workers
from benchmarking.runtime.cancellation import (
    BenchmarkCancelledError,
    CancellationReason,
    CancellationToken,
    OwnedProcessRegistry,
)


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge.runtime_paths, "data_root", tmp_path / "data")
    monkeypatch.setattr(bridge.runtime_paths, "project_state_root", tmp_path / "state")
    content = Path(__file__).with_name("fixtures").joinpath("vgb_worker/verifier_grounded_benchmark.py").read_bytes()
    config = bridge.ReleaseConfig("fixture", "1", "commit", "tag", "fixture.whl",
        hashlib.sha256(content).hexdigest(), len(content),
        {name: {"task_ids": ["task-a", "task-b"], "timeout_seconds": 2} for name in ("rdkit", "property")})
    config.wheel_path.parent.mkdir(parents=True)
    config.wheel_path.write_bytes(content)
    venv.EnvBuilder(with_pip=False, symlinks=True).create(config.runtime_root / ".venv")
    site = config.runtime_root / f".venv/lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
    (site / "verifier_grounded_benchmark.py").write_bytes(content)
    config.runtime_manifest.write_text(json.dumps({**config.identity, "source_commit": config.source_commit,
        "source_tag": config.source_tag, "wheel_path": str(config.wheel_path)}))
    return config


@pytest.fixture
def worker(config, tmp_path):
    token = CancellationToken()
    worker = workers.VerifierWorker(config, evidence_root=tmp_path / "evidence", cancellation_token=token,
        process_registry=OwnedProcessRegistry(cancellation_token=token), validation_cache=bridge.InvocationValidationCache())
    yield worker
    worker.close()
    assert not worker.registry.active()


def evaluate(config, worker=None, answer="FINAL ANSWER: CCO", track="rdkit", task="task-a"):
    return bridge.evaluate_answer(track=track, task_id=task, answer_text=answer,
        release_identity=config.identity, release_config=config, worker=worker)


def test_shadow_complete_results_repeated_interleaved_and_recycle(config, worker):
    worker.max_requests = 3
    cases = [("rdkit", "FINAL ANSWER: CCO"), ("property", "FINAL ANSWER: 1.25"),
             ("rdkit", "invalid"), ("property", "infrastructure"), ("rdkit", "FINAL ANSWER: CCO")]
    for track, answer in cases * 2:
        assert evaluate(config, worker, answer, track) == evaluate(config, None, answer, track)
    assert worker.to_meta()["process_count"] == 4
    worker.close()
    worker.close()
    assert worker.to_meta()["closed"]
    with pytest.raises(workers.VerifierWorkerError, match="closed"):
        evaluate(config, worker)


@pytest.mark.parametrize("answer,code", [("__crash__", "crashed"), ("__sleep__", "timeout"),
                                         ("__exception__", "verifier_exception")])
def test_fault_has_evidence_and_next_request_restarts_without_replay(config, worker, answer, code):
    with pytest.raises(workers.VerifierWorkerError) as caught:
        worker.invoke(config, {"action": "evaluate_one", "track": "rdkit", "task_id": "task-a", "answer_text": answer}, timeout=0.15)
    assert caught.value.code == code
    assert evaluate(config, worker)["scores"]["score"] == 0.75
    assert worker.to_meta()["process_count"] == 2
    events = [json.loads(line) for line in (worker.evidence_root / "events.jsonl").read_text().splitlines()]
    assert len([e for e in events if e["event"] == "request"]) == 2
    assert any(e.get("code") == code for e in events)


def test_restart_budget_is_per_invocation(config, worker):
    for _ in range(3):
        with pytest.raises(workers.VerifierWorkerError, match="crashed"):
            evaluate(config, worker, "__crash__")
    with pytest.raises(workers.VerifierWorkerError, match="restart_limit"):
        evaluate(config, worker)
    assert worker.to_meta()["process_count"] == 3


def test_start_failure_does_not_leak_and_can_recover(config, worker, monkeypatch):
    import subprocess
    real_popen = subprocess.Popen
    def fail(*args, **kwargs):
        raise OSError("fixture spawn failure")
    monkeypatch.setattr(workers.subprocess, "Popen", fail)
    with pytest.raises(workers.VerifierWorkerError, match="transport_error"):
        evaluate(config, worker)
    assert not worker.registry.active()
    monkeypatch.setattr(workers.subprocess, "Popen", real_popen)
    assert evaluate(config, worker)["status"] == "scored"


def test_cleanup_failure_cancels_and_records_evidence(config, worker, monkeypatch):
    evaluate(config, worker)
    real_killpg = os.killpg
    def fail(*args):
        raise PermissionError("fixture denied signal")
    try:
        monkeypatch.setattr(workers.os, "killpg", fail)
        worker.close()
        assert worker.token.is_cancelled
        assert worker.token.cleanup_errors[0]["stage"] == "verifier_worker_cleanup"
        assert worker.to_meta()["cleanup_failed"]
    finally:
        monkeypatch.setattr(workers.os, "killpg", real_killpg)
        # Resolve the injected OS failure before fixture teardown.
        worker._cleanup_failed = False
        worker.close()


def test_cancellation_during_request_and_lock_wait(config, worker):
    with ThreadPoolExecutor(max_workers=2) as executor:
        active = executor.submit(evaluate, config, worker, "__sleep__")
        waiting = executor.submit(evaluate, config, worker)
        time.sleep(0.1)
        started = time.monotonic()
        worker.token.cancel(CancellationReason(source="test", message="cancel fixture"))
        for future in (active, waiting):
            with pytest.raises(BenchmarkCancelledError, match="cancel fixture"):
                future.result(timeout=2)
        assert time.monotonic() - started < 2
    assert not worker.registry.active()


def test_close_interrupts_active_request(config, worker):
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(evaluate, config, worker, "__sleep__")
        time.sleep(0.1)
        worker.close()
        with pytest.raises(workers.VerifierWorkerError, match="closed"):
            future.result(timeout=2)


def test_native_and_python_output_cannot_corrupt_protocol(config, worker):
    assert evaluate(config, worker, "__noise__")["status"] == "scored"
    stderr = next(worker.evidence_root.glob("*.stderr.log")).read_text()
    assert all(text in stderr for text in ("python diagnostic", "native diagnostic", "stderr diagnostic"))


@pytest.mark.parametrize("script,code", [
    ('import sys; sys.stdin.readline(); print("not-json", flush=True)', "invalid_response"),
    ('import sys; sys.stdin.readline(); print("[]", flush=True)', "invalid_response"),
    ('import sys; sys.stdin.readline(); print(\'{"protocol":1,"id":999,"result":{}}\', flush=True)', "invalid_response"),
    ('import sys; sys.stdin.readline(); sys.stdout.write("{"); sys.stdout.flush()', "crashed"),
    ('import time; time.sleep(60)', "timeout"),
])
def test_malformed_or_nonresponsive_child(config, worker, monkeypatch, script, code):
    monkeypatch.setattr(workers, "WORKER_API_SCRIPT", script)
    # Larger than a pipe buffer: cancellation/deadline also cover blocked writes.
    with pytest.raises(workers.VerifierWorkerError) as caught:
        worker.invoke(config, {"action": "evaluate_one", "answer_text": "x" * 100000}, timeout=0.15)
    assert caught.value.code == code


def test_frame_bounds_fail_without_truncation(config, worker, monkeypatch):
    monkeypatch.setattr(workers, "MAX_FRAME_BYTES", 512)
    with pytest.raises(workers.VerifierWorkerError, match="request_too_large"):
        evaluate(config, worker, "x" * 1000)
    assert worker.to_meta()["process_count"] == 0
    monkeypatch.setattr(workers, "WORKER_API_SCRIPT", 'import sys; sys.stdin.readline(); print("x"*1000, flush=True)')
    with pytest.raises(workers.VerifierWorkerError, match="response_too_large"):
        evaluate(config, worker)


@pytest.mark.parametrize("file", ["wheel_path", "runtime_manifest", "runtime_python"])
def test_fingerprint_change_recycles_and_revalidates(config, worker, file):
    evaluate(config, worker)
    path = getattr(config, file)
    if file == "runtime_python":
        # Remove only the fixture venv link, never the shared interpreter.
        path.unlink()
    elif file == "wheel_path":
        path.write_bytes(b"broken wheel")
    else:
        path.write_text("{}")
    with pytest.raises(bridge.VerifierGroundedRuntimeError):
        evaluate(config, worker)
    assert not worker.registry.active()


def test_valid_fingerprint_change_restarts_loaded_modules(config, worker):
    evaluate(config, worker)
    config.runtime_manifest.write_text(config.runtime_manifest.read_text() + " ")
    evaluate(config, worker)
    assert worker.to_meta()["process_count"] == 2


def test_new_invocation_has_new_process(config, worker, tmp_path):
    evaluate(config, worker)
    old_pid = worker._process.pid
    worker.close()
    token = CancellationToken()
    other = workers.VerifierWorker(config, evidence_root=tmp_path / "other", cancellation_token=token,
                                  process_registry=OwnedProcessRegistry(cancellation_token=token))
    try:
        evaluate(config, other)
        assert other._process.pid != old_pid
        assert other.to_meta()["process_count"] == 1
    finally:
        other.close()


@pytest.mark.parametrize("answer", ["FINAL ANSWER: CCO", "invalid", "infrastructure"])
def test_evaluator_scores_and_failure_messages_are_equal(config, worker, answer):
    from functools import partial

    from benchmarking.core.records import BenchmarkRecord
    from benchmarking.scoring.errors import EvaluationError
    from benchmarking.scoring.evaluators.verifier_grounded import (
        evaluate_verifier_grounded,
        run_verifier_grounded_evaluation,
    )
    record = BenchmarkRecord(record_id="task-a", track="rdkit", source_file="fixture", prompt="fixture",
        eval_kind="verifier_grounded", payload={"verifier_grounded": {
            "release": config.identity, "track": "rdkit", "task_id": "task-a"}})
    outcomes = []
    for selected in (None, worker):
        try:
            outcomes.append(evaluate_verifier_grounded(record, short_answer_text=answer, full_response_text=answer,
                judge=None, verifier_runner=partial(run_verifier_grounded_evaluation, release_config=config, worker=selected)))
        except EvaluationError as exc:
            outcomes.append((type(exc).__name__, str(exc)))
    assert outcomes[0] == outcomes[1]


@pytest.mark.parametrize("mode", ["isolated", "worker"])
@pytest.mark.parametrize("cancel", [False, True])
def test_cli_transport_wiring_and_cleanup(config, tmp_path, monkeypatch, mode, cancel):
    from benchmarking.core.contracts import AnswerPayload, RunnerResult, RunStatus
    from benchmarking.core.records import BenchmarkRecord
    from benchmarking.service.single import execution
    from benchmarking.workflow import cli, experiments, runner_adapters
    record = BenchmarkRecord(record_id="task-a", track="rdkit", source_file="fixture",
        prompt="fixture", eval_kind="verifier_grounded", payload={"verifier_grounded": {
            "release": config.identity, "track": "rdkit", "task_id": "task-a"}})
    monkeypatch.setattr(sys, "argv", ["benchmark", "--execution-backend", "host", "--no-analysis",
        "--groups", "single_llm_skills_off", "--exact-output-dir", str(tmp_path / "out"),
        *(["--verifier-mode", mode] if mode == "worker" else [])])
    monkeypatch.setattr(cli, "load_release_config", lambda: config)
    monkeypatch.setattr(execution, "select_track_files", lambda args: [tmp_path / "fixture.jsonl"])
    monkeypatch.setattr(execution, "select_records", lambda paths, args: [record])
    monkeypatch.setattr(cli.runtime_paths, "benchmark_runtime_root", tmp_path / "benchmark")

    class Pool:
        def __init__(self, **kwargs):
            self.context = type("Context", (), {"experiment_specs": experiments.EXPERIMENT_SPECS})()
        def config_for_group(self, group):
            (tmp_path / "config.json").write_text("{}")
            return tmp_path / "config.json"
        def judge_config_path(self):
            pytest.fail("VGB must not provision a judge")
    monkeypatch.setattr(cli.runtime_config_pool, "ConfigPool", Pool)
    monkeypatch.setattr(execution, "cleanup", lambda: None)

    def build_runner(**kwargs):
        class Runner:
            def run(self, record, group):
                if cancel:
                    kwargs["cancellation_token"].cancel(CancellationReason(source="fixture"))
                return RunnerResult(status=RunStatus.COMPLETED,
                    answer=AnswerPayload(short_answer_text="FINAL ANSWER: CCO", full_response_text="FINAL ANSWER: CCO"),
                    runner_meta={}, raw={})
        return Runner()
    monkeypatch.setattr(runner_adapters, "build_runner", build_runner)
    assert cli.main() == (130 if cancel else 0)
    manifest = json.loads((tmp_path / "out/runtime-manifest.json").read_text())
    assert manifest["judge"] is None
    assert manifest["verifier_transport"]["mode"] == mode
    if mode == "worker":
        assert manifest["verifier_transport"]["closed"]
        assert manifest["verifier_transport"]["process_count"] == (0 if cancel else 1)
    result = json.loads((tmp_path / "out/results.json").read_text())["results"][0]
    assert result["run_lifecycle_status"] == ("cancelled" if cancel else "completed")
    if not cancel:
        assert result["evaluation"]["score"] == 0.75


def test_request_identity_is_rejected_before_process_start(config, worker):
    with pytest.raises(bridge.VerifierGroundedRuntimeError, match="not part"):
        evaluate(config, worker, task="unlisted")
    with pytest.raises(workers.VerifierWorkerError, match="identity_mismatch"):
        worker.invoke(replace(config, source_tag="other"), {"action": "evaluate_one"}, timeout=1)
    assert worker.to_meta()["process_count"] == 0


def test_crashed_leader_descendant_is_terminated(config, worker):
    with pytest.raises(workers.VerifierWorkerError, match="crashed"):
        evaluate(config, worker, "__child_crash__")
    stderr = next(worker.evidence_root.glob("*.stderr.log")).read_text()
    pid = int(stderr.split("child_pid=")[1].splitlines()[0])
    # An orphan may briefly be a zombie; ps state distinguishes it from a live
    # descendant and works on both macOS and Linux.
    import subprocess
    state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    assert not state or state.startswith("Z")
