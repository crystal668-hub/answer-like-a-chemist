from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any, Callable

from benchmarking.core.convergence import ConvergencePolicy
from benchmarking.core.contracts import AnswerPayload, FailureInfo, RecoveryInfo, RunnerResult, RunStatus
from benchmarking.runtime.openclaw_env import build_openclaw_subprocess_env
from benchmarking.runtime.agent_workspace import (
    AttemptOutcome,
    AttemptWorkspaceManager,
    ContaminationAudit,
    WorkspaceIsolationError,
    WorkspaceLeaseSet,
    adjudicate_workspace_findings,
)
from benchmarking.workflow.runners.chemqa_artifacts import ChemQAArtifactSupport
from benchmarking.workflow.runners.chemqa_workspaces import ChemQAWorkspaceSupport


class ConvergenceLimitExceeded(RuntimeError):
    pass


class ChemQARunner(ChemQAArtifactSupport, ChemQAWorkspaceSupport):
    def __init__(
        self,
        *,
        chemqa_root: Path,
        timeout_seconds: int,
        config_path: Path,
        slot_set: str,
        review_rounds: int | None,
        rebuttal_rounds: int | None,
        model_profile: str,
        runtime_bundle_root: Path,
        launch_workspace_root: Path,
        launch_script: Path,
        collect_script: Path,
        runtime_dir: Path,
        current_python,
        run_subprocess,
        parse_json_stdout,
        deep_copy_jsonish,
        ensure_runtime_bundle,
        build_chemqa_goal,
        resolve_chemqa_answer_kind,
        cleanup_manifest_path,
        build_cleanup_manifest_payload,
        write_cleanup_manifest,
        register_pending_cleanup_manifest,
        update_cleanup_manifest,
        invoke_cleanroom_cleanup,
        unregister_pending_cleanup_manifest,
        now_stamp,
        slugify,
        default_chemqa_preset: str,
        default_openclaw_env_file: Path,
        actual_slot_ids,
        workspace_manager: AttemptWorkspaceManager,
        session_audit_resolver: Callable[..., dict[str, Any]] | None = None,
        contamination_auditor: Callable[..., ContaminationAudit] | None = None,
        allowed_workspace_roots: tuple[Path, ...] | list[Path] = (),
        unique_run_suffix: bool = True,
        normalize_chemqa_run_status,
        is_chemqa_terminal_status,
        is_chemqa_success_status,
        build_chemqa_full_response,
        build_chemqa_response_from_submission,
        load_yaml_mapping,
        normalize_space,
        benchmark_error_factory=None,
        cleanup_error_factory=None,
        benchmark_agent_thinking: str | None = None,
        convergence_policy: ConvergencePolicy | None = None,
    ) -> None:
        self.chemqa_root = chemqa_root
        self.timeout_seconds = timeout_seconds
        self.convergence_policy = convergence_policy or ConvergencePolicy(timeout_seconds=timeout_seconds)
        self.config_path = config_path
        self.slot_set = slot_set
        self.review_rounds = review_rounds
        self.rebuttal_rounds = rebuttal_rounds
        self.model_profile = model_profile
        self.runtime_bundle_root = runtime_bundle_root
        self.launch_workspace_root = launch_workspace_root
        self.launch_script = launch_script
        self.collect_script = collect_script
        self.runtime_dir = runtime_dir
        self._current_python = current_python
        self._run_subprocess = run_subprocess
        self._parse_json_stdout = parse_json_stdout
        self._deep_copy_jsonish = deep_copy_jsonish
        self._ensure_runtime_bundle = ensure_runtime_bundle
        self._build_chemqa_goal = build_chemqa_goal
        self._resolve_chemqa_answer_kind = resolve_chemqa_answer_kind
        self._cleanup_manifest_path = cleanup_manifest_path
        self._build_cleanup_manifest_payload = build_cleanup_manifest_payload
        self._write_cleanup_manifest = write_cleanup_manifest
        self._register_pending_cleanup_manifest = register_pending_cleanup_manifest
        self._update_cleanup_manifest = update_cleanup_manifest
        self._invoke_cleanroom_cleanup = invoke_cleanroom_cleanup
        self._unregister_pending_cleanup_manifest = unregister_pending_cleanup_manifest
        self._now_stamp = now_stamp
        self._slugify = slugify
        self._default_chemqa_preset = default_chemqa_preset
        self._default_openclaw_env_file = default_openclaw_env_file
        self._actual_slot_ids = actual_slot_ids
        self.workspace_manager = workspace_manager
        self._session_audit_resolver = session_audit_resolver
        self._contamination_auditor = contamination_auditor
        self.allowed_workspace_roots = tuple(Path(path).expanduser().resolve() for path in allowed_workspace_roots)
        self._active_slot_workspaces: dict[str, Path] = {}
        self._current_group_id = ""
        self._unique_run_suffix = bool(unique_run_suffix)
        self._current_lease_set: WorkspaceLeaseSet | None = None
        self._current_input_bundle: Any = None
        self._current_launch_home: Path | None = None
        self._workspace_attempt_archives: list[dict[str, Any]] = []
        self._normalize_chemqa_run_status = normalize_chemqa_run_status
        self._is_chemqa_terminal_status = is_chemqa_terminal_status
        self._is_chemqa_success_status = is_chemqa_success_status
        self._build_chemqa_full_response = build_chemqa_full_response
        self._build_chemqa_response_from_submission = build_chemqa_response_from_submission
        self._load_yaml_mapping = load_yaml_mapping
        self._normalize_space = normalize_space
        self._benchmark_error_factory = benchmark_error_factory
        self._cleanup_error_factory = cleanup_error_factory
        self._benchmark_agent_thinking = benchmark_agent_thinking

    def _status_path(self, run_id: str) -> Path:
        return self.chemqa_root / "control" / "run-status" / f"{run_id}.json"

    def _read_run_status(self, run_id: str) -> dict[str, Any]:
        status_path = self._status_path(run_id)
        if not status_path.is_file():
            return {}
        return self._normalize_chemqa_run_status(json.loads(status_path.read_text(encoding="utf-8")))

    def _run_status_progress_signature(self, payload: dict[str, Any]) -> str:
        progress_payload = {
            "status": payload.get("status"),
            "legacy_status": payload.get("legacy_status"),
            "terminal_state": payload.get("terminal_state"),
            "protocol_terminal_state": payload.get("protocol_terminal_state"),
            "artifact_flow_state": payload.get("artifact_flow_state"),
            "benchmark_terminal_state": payload.get("benchmark_terminal_state"),
            "phase": payload.get("phase"),
            "review_round": payload.get("review_round"),
            "rebuttal_round": payload.get("rebuttal_round"),
            "phase_progress": payload.get("phase_progress"),
            "updated_at": payload.get("updated_at"),
        }
        return json.dumps(progress_payload, sort_keys=True, ensure_ascii=False, default=str)

    def _recover_stalled_run(self, run_id: str, last_status: dict[str, Any]) -> dict[str, Any]:
        self._rotate_workspaces_for_recovery(run_id)
        recover_script = self.chemqa_root / "scripts" / "recover_run.py"
        if not recover_script.is_file():
            return {"status": "skipped", "reason": f"missing recover script: {recover_script}"}
        data_dir = self.chemqa_root / "generated" / "clawteam-data" / "runs" / run_id
        command = [
            self._current_python(),
            str(recover_script),
            "--skill-root",
            str(self.chemqa_root),
            "--team",
            run_id,
            "--runtime-dir",
            str(self.runtime_dir),
            "--config-file",
            str(self.config_path),
            "--workspace-root",
            str(self._managed_slot_workspace_root()),
            "--max-steps",
            "6",
            "--max-respawns-per-role-phase-signature",
            "2",
            "--json",
        ]
        env = os.environ.copy()
        env["CLAWTEAM_DATA_DIR"] = str(data_dir)
        result = self._run_subprocess(command, env=env, cwd=self.chemqa_root, timeout=120)
        if result.returncode != 0:
            return {
                "status": "error",
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "last_status": self._deep_copy_jsonish(last_status),
            }
        output = (result.stdout or "").strip() or (result.stderr or "").strip()
        if not output:
            return {"status": "ok", "empty_output": True}
        try:
            return self._deep_copy_jsonish(json.loads(output))
        except json.JSONDecodeError:
            return {"status": "ok", "raw_output": output}

    def _wait_for_terminal_status(self, run_id: str, *, timeout_seconds: int) -> dict[str, Any]:
        import time

        policy = getattr(self, "convergence_policy", ConvergencePolicy(timeout_seconds=timeout_seconds))
        deadline = time.time() + int(policy.timeout_seconds or timeout_seconds)
        last_status: dict[str, Any] = {}
        last_signature = ""
        unchanged_polls = 0
        recovery_attempts = 0
        while time.time() < deadline:
            last_status = self._read_run_status(run_id)
            if self._is_chemqa_terminal_status(last_status):
                return last_status
            signature = self._run_status_progress_signature(last_status)
            if signature and signature == last_signature:
                unchanged_polls += 1
            else:
                unchanged_polls = 0
                last_signature = signature
            if unchanged_polls >= max(1, int(policy.max_unchanged_status_polls)):
                if recovery_attempts >= max(0, int(policy.max_recovery_attempts)):
                    error_message = (
                        f"ChemQA run `{run_id}` exceeded convergence limits before terminal status. "
                        f"Last status: {last_status}"
                    )
                    if self._benchmark_error_factory is not None:
                        raise self._benchmark_error_factory(error_message)
                    raise ConvergenceLimitExceeded(error_message)
                recovery_attempts += 1
                self._recover_stalled_run(run_id, last_status)
                unchanged_polls = 0
            time.sleep(30)
        error_message = (
            f"ChemQA run `{run_id}` did not reach a terminal state within {timeout_seconds}s. Last status: {last_status}"
        )
        if self._benchmark_error_factory is not None:
            raise self._benchmark_error_factory(error_message)
        raise RuntimeError(error_message)


    def _run_prepared(self, record: Any, group: Any, *, run_id: str, input_bundle: Any) -> RunnerResult:
        payload: dict[str, Any] = {}
        answer_kind = self._resolve_chemqa_answer_kind(record)
        goal = self._build_chemqa_goal(record, websearch_enabled=group.websearch, input_bundle=input_bundle)
        launch_root = self.launch_workspace_root / group.id / self._slugify(record.record_id, limit=80)
        launch_home = launch_root / "home"
        self._current_launch_home = launch_home
        template_dir = launch_home / ".clawteam" / "templates"
        command_map_dir = launch_root / "command-maps"
        template_dir.mkdir(parents=True, exist_ok=True)
        command_map_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = self._cleanup_manifest_path(self.launch_workspace_root.parent, run_id)
        initial_manifest = self._build_cleanup_manifest_payload(
            run_id=run_id,
            benchmark_kind="chemqa",
            group_id=group.id,
            output_root=self.launch_workspace_root.parent,
            launch_home=launch_home,
            control_roots=[
                self.chemqa_root / "control" / "runplans" / f"{run_id}.json",
                self.chemqa_root / "control" / "run-status" / f"{run_id}.json",
            ],
            generated_roots=[
                self.chemqa_root / "generated" / "command-maps" / f"{run_id}-command-map.json",
                self.chemqa_root / "generated" / "prompt-bundles" / f"{run_id}-prompts.json",
                self.chemqa_root / "generated" / "runtime-context" / f"{run_id}-context.json",
            ],
            artifact_roots=[
                self.chemqa_root / "generated" / "artifacts" / run_id,
                self.chemqa_root / "generated" / "clawteam-data" / "runs" / run_id,
                launch_root,
            ],
            extra={
                "record_id": record.record_id,
                "template_dir": str(template_dir),
                "command_map_dir": str(command_map_dir),
            },
        )
        self._write_cleanup_manifest(manifest_path, initial_manifest)
        self._register_pending_cleanup_manifest(manifest_path)
        command = [
            self._current_python(),
            str(self.launch_script),
            "--root",
            str(self.chemqa_root),
            "--preset",
            self._default_chemqa_preset,
            "--goal",
            goal,
            "--run-id",
            run_id,
            "--answer-kind",
            answer_kind,
            "--model-profile",
            self.model_profile,
            "--slot-set",
            self.slot_set,
            "--openclaw-config",
            str(self.config_path),
            "--template-dir",
            str(template_dir),
            "--command-map-dir",
            str(command_map_dir),
            "--runtime-dir",
            str(self.runtime_dir),
            "--launch-mode",
            "run",
        ]
        if input_bundle is not None:
            command.extend(["--additional-file-workspace", str(input_bundle.bundle_dir)])
        if self.review_rounds is not None:
            command.extend(["--review-rounds", str(self.review_rounds)])
        if self.rebuttal_rounds is not None:
            command.extend(["--rebuttal-rounds", str(self.rebuttal_rounds)])

        env = build_openclaw_subprocess_env(base_env=os.environ.copy(), config_path=self.config_path)
        env["HOME"] = str(launch_home)
        env["OPENCLAW_ENV_FILE"] = str(self._default_openclaw_env_file)
        env["OPENCLAW_DEBATE_TRUSTED_PLUGINS"] = "duckduckgo" if group.websearch else "__none__"
        env["CHEMQA_ANSWER_KIND"] = answer_kind
        env["BENCHMARK_CLEANROOM_RUN_ID"] = run_id
        env["BENCHMARK_CLEANROOM_LEASE_DIR"] = str((self.launch_workspace_root.parent / "cleanroom" / "leases").resolve())

        try:
            result = self._run_subprocess(
                command,
                env=env,
                cwd=self.chemqa_root,
                timeout=self.convergence_policy.timeout_seconds,
            )
            payload = self._parse_json_stdout(result, command)
            materialize = self._deep_copy_jsonish((payload.get("materialize") or {}))
            self._update_cleanup_manifest(
                manifest_path,
                {
                    "launch_home": str(launch_home.resolve()),
                    "clawteam_data_dir": str(
                        Path(str(materialize.get("clawteam_data_dir") or (self.chemqa_root / "generated" / "clawteam-data" / "runs" / run_id)))
                        .expanduser()
                        .resolve()
                    ),
                    "session_assignments": self._deep_copy_jsonish(((payload.get("compile") or {}).get("session_assignments") or {})),
                    "control_roots": [
                        str(self.chemqa_root / "control" / "runplans" / f"{run_id}.json"),
                        str(self.chemqa_root / "control" / "run-status" / f"{run_id}.json"),
                    ],
                    "generated_roots": [
                        str((command_map_dir / f"{run_id}-command-map.json").resolve()),
                        str(self.chemqa_root / "generated" / "prompt-bundles" / f"{run_id}-prompts.json"),
                        str(self.chemqa_root / "generated" / "runtime-context" / f"{run_id}-context.json"),
                        str(template_dir),
                    ],
                    "artifact_roots": [
                        str(self.chemqa_root / "generated" / "artifacts" / run_id),
                        str(self.chemqa_root / "generated" / "clawteam-data" / "runs" / run_id),
                        str(launch_root.resolve()),
                    ],
                    "launch_payload": self._deep_copy_jsonish(payload),
                },
            )
            try:
                run_status = self._wait_for_terminal_status(run_id, timeout_seconds=self.convergence_policy.timeout_seconds)
            except Exception as exc:
                message = str(exc)
                is_convergence_stop = "convergence limit" in message.lower() or "exceeded convergence" in message.lower()
                if not is_convergence_stop:
                    raise
                run_status = self._read_run_status(run_id)
                archive_meta = self._archive_artifacts(
                    run_id=run_id,
                    group_id=group.id,
                    record_id=record.record_id,
                    run_status=run_status,
                    env=env,
                )
                runner_meta = {
                    "run_id": run_id,
                    "launch": payload,
                    "convergence_policy": self.convergence_policy.to_meta(),
                    "terminal_state": "failed",
                    "terminal_reason_code": "convergence_limit_exceeded",
                    "run_status": run_status,
                    "error": message,
                    **archive_meta,
                }
                if input_bundle is not None:
                    runner_meta["runtime_bundle"] = input_bundle.to_meta()
                return RunnerResult(
                    status=RunStatus.FAILED,
                    answer=AnswerPayload(),
                    raw={"run_status": run_status},
                    runner_meta=runner_meta,
                    failure=FailureInfo(
                        code="convergence_limit_exceeded",
                        message=message,
                        details={
                            "policy": self.convergence_policy.to_meta(),
                            "run_id": run_id,
                            "run_status": run_status,
                        },
                    ),
                )
            terminal_state = str(run_status.get("terminal_state") or "")
            terminal_reason_code = str(run_status.get("terminal_reason_code") or "")
            legacy_status = str(run_status.get("legacy_status") or "")
            artifact_collection = self._deep_copy_jsonish(run_status.get("artifact_collection") or {})

            if not self._is_chemqa_success_status(run_status):
                archive_meta = self._archive_artifacts(
                    run_id=run_id,
                    group_id=group.id,
                    record_id=record.record_id,
                    run_status=run_status,
                    env=env,
                )
                reconciled_qa_result = self._load_archived_completed_qa_result(archive_meta)
                if reconciled_qa_result is not None:
                    qa_result_path, qa_result = reconciled_qa_result
                    short_answer_text, full_response_text = self._build_chemqa_full_response(qa_result=qa_result)
                    runner_meta = {
                        "run_id": run_id,
                        "launch": payload,
                        "convergence_policy": self.convergence_policy.to_meta(),
                        "qa_result_path": str(qa_result_path),
                        "acceptance_status": qa_result.get("acceptance_status"),
                        "terminal_state": qa_result.get("terminal_state"),
                        "terminal_reason_code": terminal_reason_code or "",
                        "artifact_collection": artifact_collection,
                        "run_status": run_status,
                        "reconciled_from_archived_artifacts": True,
                        **archive_meta,
                    }
                    if legacy_status:
                        runner_meta["legacy_status"] = legacy_status
                    if input_bundle is not None:
                        runner_meta["runtime_bundle"] = input_bundle.to_meta()
                    return RunnerResult(
                        status=RunStatus.COMPLETED,
                        answer=AnswerPayload(
                            short_answer_text=short_answer_text,
                            full_response_text=full_response_text,
                        ),
                        raw=qa_result,
                        runner_meta=runner_meta,
                    )
                message = (
                    f"ChemQA run ended with non-success status: "
                    f"{terminal_state or legacy_status or 'unknown'}"
                )
                runner_meta = {
                    "run_id": run_id,
                    "launch": payload,
                    "convergence_policy": self.convergence_policy.to_meta(),
                    "acceptance_status": None,
                    "terminal_state": terminal_state or "unknown",
                    "terminal_reason_code": terminal_reason_code or "",
                    "artifact_collection": artifact_collection,
                    "run_status": run_status,
                    "non_success_terminal_status": legacy_status or terminal_state or "unknown",
                    "missing_reviewer_lanes": list(((run_status.get("phase_progress") or {}).get("missing_reviewer_lanes") or [])),
                    "error": message,
                    **archive_meta,
                }
                archived_qa_result_path = str(archive_meta.get("qa_result_path") or "").strip()
                if archived_qa_result_path:
                    runner_meta["qa_result_path"] = archived_qa_result_path
                if legacy_status:
                    runner_meta["legacy_status"] = legacy_status
                if input_bundle is not None:
                    runner_meta["runtime_bundle"] = input_bundle.to_meta()
                recovery_assessment = self._assess_recovered_answer(
                    run_id=run_id,
                    run_status=run_status,
                    archive_meta=archive_meta,
                )
                if recovery_assessment is not None:
                    recovery_details = self._deep_copy_jsonish(recovery_assessment.get("details") or {})
                    runner_meta.update(
                        {
                            "fallback_used": True,
                            **recovery_details,
                            "evaluable": bool(recovery_assessment.get("evaluable")),
                            "scored": bool(recovery_assessment.get("scored")),
                            "recovery_mode": str(recovery_assessment.get("recovery_mode") or "none"),
                            "answer_reliability": str(recovery_assessment.get("reliability") or "none"),
                            "degraded_execution": True,
                            "recovery_reason": str(recovery_assessment.get("reason") or ""),
                        }
                    )
                if recovery_assessment is not None and recovery_assessment.get("evaluable"):
                    return RunnerResult(
                        status=RunStatus.RECOVERED,
                        answer=AnswerPayload(
                            short_answer_text=str(recovery_assessment["short_answer_text"]),
                            full_response_text=str(recovery_assessment["full_response_text"]),
                        ),
                        raw={"run_status": run_status, "fallback": recovery_details},
                        runner_meta=runner_meta,
                        recovery=RecoveryInfo(
                            source="failure_artifact"
                            if str(recovery_details.get("fallback_source") or "") == "failure_artifact"
                            else "candidate_submission",
                            scored=True,
                            evaluable=True,
                            reliability=str(recovery_assessment["reliability"]),
                            recovery_mode=str(recovery_assessment["recovery_mode"]),
                            reason=str(recovery_assessment["reason"]),
                            details=recovery_details,
                        ),
                    )
                return RunnerResult(
                    status=RunStatus.FAILED,
                    answer=AnswerPayload(),
                    raw={"run_status": run_status},
                    runner_meta=runner_meta,
                    failure=FailureInfo(
                        code=terminal_reason_code or "chemqa_non_success_terminal_status",
                        message=message,
                        details={"run_status": run_status},
                    ),
                )

            qa_result_path = self._resolve_existing_qa_result(run_id, run_status)
            if qa_result_path is None:
                qa_result_path = self._ensure_artifacts(run_id, env=env, run_status=run_status)
            archive_meta = self._archive_artifacts(
                run_id=run_id,
                group_id=group.id,
                record_id=record.record_id,
                run_status=run_status,
                env=env,
                qa_result_path=qa_result_path,
            )
            archived_qa_result_path = Path(str(archive_meta.get("qa_result_path") or "").strip()).expanduser()
            if str(archive_meta.get("qa_result_path") or "").strip() and archived_qa_result_path.is_file():
                qa_result_path = archived_qa_result_path.resolve()
            qa_result = json.loads(qa_result_path.read_text(encoding="utf-8"))
            short_answer_text, full_response_text = self._build_chemqa_full_response(qa_result=qa_result)
            runner_meta = {
                "run_id": run_id,
                "launch": payload,
                "convergence_policy": self.convergence_policy.to_meta(),
                "qa_result_path": str(qa_result_path),
                "acceptance_status": qa_result.get("acceptance_status"),
                "terminal_state": terminal_state or qa_result.get("terminal_state"),
                "terminal_reason_code": terminal_reason_code or "",
                "artifact_collection": artifact_collection,
                "run_status": run_status,
                **archive_meta,
            }
            if legacy_status:
                runner_meta["legacy_status"] = legacy_status
            if input_bundle is not None:
                runner_meta["runtime_bundle"] = input_bundle.to_meta()
            return RunnerResult(
                status=RunStatus.COMPLETED,
                answer=AnswerPayload(
                    short_answer_text=short_answer_text,
                    full_response_text=full_response_text,
                ),
                raw=qa_result,
                runner_meta=runner_meta,
            )
        finally:
            try:
                cleanup_report = self._invoke_cleanroom_cleanup(manifest_path=manifest_path)
            except Exception as exc:
                self._unregister_pending_cleanup_manifest(manifest_path)
                if self._cleanup_error_factory is not None:
                    raise self._cleanup_error_factory(f"ChemQA cleanup failed for run `{run_id}`: {exc}") from exc
                raise
            else:
                self._unregister_pending_cleanup_manifest(manifest_path)
                if payload:
                    payload.setdefault("cleanup_report", cleanup_report)

    def run(self, record: Any, group: Any) -> RunnerResult:
        run_id = f"benchmark-{group.id}-{self._slugify(record.record_id, limit=40)}-{self._now_stamp()}"
        if self._unique_run_suffix:
            run_id = f"{run_id}-{uuid.uuid4().hex[:8]}"
        input_bundle = self._ensure_runtime_bundle(record, bundle_root=self.runtime_bundle_root)
        identities = self._slot_identities(record=record, group=group, run_id=run_id)
        try:
            lease_set = self.workspace_manager.prepare_set(identities)
        except WorkspaceIsolationError as error:
            return self._workspace_failure_result(
                error=error,
                isolation_meta={
                    "schema_version": 3,
                    "run_id": self.workspace_manager.run_id,
                    "invocation_id": self.workspace_manager.invocation_id,
                    "group_id": str(group.id),
                    "record_id": str(record.record_id),
                    "attempt_index": 0,
                    "preflight_ok": False,
                    "archive_ok": False,
                    "audit_execution_status": "unavailable",
                    "boundary_status": "unknown",
                    "contamination_status": "indeterminate",
                    "adjudication": "non_evaluable",
                    "findings": [],
                    "slots": {},
                },
                run_id=run_id,
            )

        self._install_lease_set(lease_set)
        self._current_group_id = str(group.id)
        self._current_input_bundle = input_bundle
        self._current_launch_home = (
            self.launch_workspace_root
            / str(group.id)
            / self._slugify(record.record_id, limit=80)
            / "home"
        )
        self._workspace_attempt_archives = []
        try:
            try:
                result = self._run_prepared(record, group, run_id=run_id, input_bundle=input_bundle)
            except WorkspaceIsolationError as error:
                result = self._workspace_failure_result(
                    error=error,
                    isolation_meta={
                        "schema_version": 3,
                        "preflight_ok": True,
                        "archive_ok": False,
                        "audit_execution_status": "unavailable",
                        "boundary_status": "unknown",
                        "contamination_status": "indeterminate",
                        "adjudication": "non_evaluable",
                        "findings": list(error.details.get("audits") or []),
                        "slots": {},
                        "attempt_archives": list(self._workspace_attempt_archives),
                    },
                    run_id=run_id,
                )
            except Exception as exc:
                result = self._chemqa_attempt_failure(exc=exc, run_id=run_id)

            final_lease_set = self._current_lease_set
            if final_lease_set is None:
                execution_error = result.runner_meta.get("execution_error")
                if isinstance(execution_error, dict) and execution_error.get("source") == "workspace_isolation":
                    result.runner_meta["workspace_isolation"]["attempt_archives"] = list(
                        self._workspace_attempt_archives
                    )
                    return result
                return self._workspace_failure_result(
                    error=WorkspaceIsolationError(
                        "workspace_recovery_failed",
                        "ChemQA attempt ended without an active workspace lease set to archive.",
                    ),
                    isolation_meta={
                        "schema_version": 3,
                        "preflight_ok": False,
                        "archive_ok": False,
                        "audit_execution_status": "unavailable",
                        "boundary_status": "unknown",
                        "contamination_status": "indeterminate",
                        "adjudication": "non_evaluable",
                        "findings": [],
                        "slots": {},
                        "attempt_archives": list(self._workspace_attempt_archives),
                    },
                    run_id=run_id,
                    original_result=result,
                )
            audits = {
                lease.identity.agent_id: self._audit_slot(
                    lease=lease,
                    result=result,
                    input_bundle=input_bundle,
                )
                for lease in final_lease_set.leases
            }
            isolation_meta = final_lease_set.to_meta()
            all_findings = [
                {"agent_id": agent_id, **dict(finding)}
                for agent_id, audit in audits.items()
                for finding in audit.findings
            ]
            audit_execution_status = (
                "unavailable"
                if any(audit.audit_execution_status == "unavailable" for audit in audits.values())
                else "complete"
            )
            combined_audit = adjudicate_workspace_findings(
                all_findings,
                audit_execution_status=audit_execution_status,
                recovery={
                    "slots": {
                        agent_id: dict(audit.recovery)
                        for agent_id, audit in audits.items()
                    },
                    "model_reinvoked": False,
                },
            )
            cleanup_by_agent = {
                agent_id: self.workspace_manager.cleanup_boundary_writes(audit)
                for agent_id, audit in audits.items()
            }
            combined_cleanup = {
                "attempted_count": sum(item["attempted_count"] for item in cleanup_by_agent.values()),
                "succeeded_count": sum(item["succeeded_count"] for item in cleanup_by_agent.values()),
                "failed_count": sum(item["failed_count"] for item in cleanup_by_agent.values()),
                "slots": cleanup_by_agent,
            }
            isolation_meta.update(
                {
                    "run_id": self.workspace_manager.run_id,
                    "invocation_id": self.workspace_manager.invocation_id,
                    "group_id": str(group.id),
                    "record_id": str(record.record_id),
                    "attempt_index": 0,
                    "attempt_archives": list(self._workspace_attempt_archives),
                    "cleanup": combined_cleanup,
                    **combined_audit.to_payload(),
                }
            )
            for lease in final_lease_set.leases:
                bundle_dir = getattr(input_bundle, "bundle_dir", None)
                policy = self.workspace_manager.policy_for_lease(
                    lease,
                    role="chemqa",
                    skills_enabled=bool(getattr(group, "skills_enabled", True)),
                    always_read_scopes=([Path(bundle_dir)] if bundle_dir is not None else []),
                    read_scopes=self.allowed_workspace_roots,
                )
                isolation_meta["slots"][lease.identity.agent_id].update(
                    {"policy_digest": policy.digest, "policy": policy.to_payload()}
                )
            if combined_audit.adjudication == "non_evaluable":
                result = self._workspace_failure_result(
                    error=WorkspaceIsolationError(
                        "benchmark_workspace_contamination",
                        "ChemQA workspace information contamination was detected or could not be excluded.",
                        details={
                            "audit_execution_status": combined_audit.audit_execution_status,
                            "contamination_status": combined_audit.contamination_status,
                            "adjudication": combined_audit.adjudication,
                            "findings": all_findings,
                        },
                    ),
                    isolation_meta=isolation_meta,
                    run_id=run_id,
                    original_result=result,
                )
            else:
                if combined_audit.adjudication == "scoreable_degraded":
                    result.runner_meta["degraded_execution"] = True
                result.runner_meta["workspace_isolation"] = isolation_meta

            seal_errors: list[WorkspaceIsolationError] = []
            for lease in final_lease_set.leases:
                slot_meta = isolation_meta["slots"][lease.identity.agent_id]
                try:
                    archive = self.workspace_manager.seal(
                        lease,
                        AttemptOutcome(
                            runner_status=result.status.value,
                            archive_reason="attempt_terminal",
                            contamination_audit=audits[lease.identity.agent_id],
                        ),
                    )
                    slot_meta.update(archive.to_meta())
                except WorkspaceIsolationError as error:
                    slot_meta["archive_ok"] = False
                    slot_meta["archive_error"] = dict(error.details)
                    seal_errors.append(error)
            isolation_meta["archive_ok"] = not seal_errors
            result.runner_meta["workspace_isolation"] = isolation_meta
            if seal_errors:
                return self._workspace_failure_result(
                    error=WorkspaceIsolationError(
                        "workspace_archive_failed",
                        "One or more ChemQA role workspaces could not be archived.",
                        details={"slot_errors": [dict(error.details) for error in seal_errors]},
                    ),
                    isolation_meta=isolation_meta,
                    run_id=run_id,
                    original_result=result,
                )
            return result
        finally:
            self._active_slot_workspaces = {}
            self._current_group_id = ""
            self._current_lease_set = None
            self._current_input_bundle = None
            self._current_launch_home = None
            self._workspace_attempt_archives = []
