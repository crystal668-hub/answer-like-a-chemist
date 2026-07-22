from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from benchmarking.core.convergence import ConvergencePolicy
from benchmarking.core.contracts import AnswerPayload, FailureInfo, RecoveryInfo, RunnerResult, RunStatus
from benchmarking.runtime.openclaw_env import build_openclaw_subprocess_env
from benchmarking.runtime.session_isolation import sanitize_agent_id
from benchmarking.runtime.agent_workspace import (
    AttemptIdentity,
    AttemptOutcome,
    AttemptWorkspaceLease,
    AttemptWorkspaceManager,
    ContaminationAudit,
    WorkspaceAudit,
    WorkspaceIsolationError,
    WorkspaceLeaseSet,
    adjudicate_workspace_findings,
    ensure_workspace_audit,
)


class ConvergenceLimitExceeded(RuntimeError):
    pass


class ChemQARunner:
    _ARCHIVABLE_ARTIFACT_FILENAMES = (
        "candidate_submission.json",
        "acceptance_decision.json",
        "submission_trace.json",
        "submission_cycles.json",
        "proposer_trajectory.json",
        "reviewer_trajectories.json",
        "review_statuses.json",
        "final_review_items.json",
        "final_answer.md",
        "final_submission.json",
        "qa_result.json",
    )

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

    def _candidate_protocol_dirs(self, run_id: str, run_status: dict[str, Any]) -> list[Path]:
        candidates: list[Path] = []
        explicit_protocol = str(run_status.get("protocol_path") or "").strip()
        explicit_workspace_protocol = str(run_status.get("workspace_protocol_path") or "").strip()
        if explicit_protocol:
            explicit_parent = Path(explicit_protocol).expanduser().resolve().parent
            if self._is_allowed_protocol_source(explicit_parent, run_id=run_id):
                candidates.append(explicit_parent)
        if explicit_workspace_protocol:
            workspace_parent = Path(explicit_workspace_protocol).expanduser().resolve().parent
            if self._is_allowed_protocol_source(workspace_parent, run_id=run_id):
                candidates.append(workspace_parent)

        protocol_dir = self.chemqa_root / "generated" / "clawteam-data" / "runs" / run_id / "teams" / run_id
        candidates.append(protocol_dir)
        coordinator_slot = self._actual_slot_ids(self.slot_set)["debate-coordinator"]
        coordinator_workspace = getattr(self, "_active_slot_workspaces", {}).get(coordinator_slot)
        if coordinator_workspace is not None:
            candidates.append(coordinator_workspace)

        deduped: list[Path] = []
        seen: set[str] = set()
        for candidate in candidates:
            key = str(candidate.resolve()) if candidate.exists() else str(candidate)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(candidate)
        return deduped

    def _is_allowed_protocol_source(self, path: Path, *, run_id: str) -> bool:
        candidate = path.expanduser().resolve(strict=False)
        allowed_roots = [
            self.chemqa_root / "generated" / "clawteam-data" / "runs" / run_id,
            *getattr(self, "_active_slot_workspaces", {}).values(),
        ]
        for root in allowed_roots:
            try:
                candidate.relative_to(Path(root).expanduser().resolve(strict=False))
                return True
            except ValueError:
                continue
        return False

    def _resolve_existing_qa_result(self, run_id: str, run_status: dict[str, Any]) -> Path | None:
        explicit_qa_result = str(run_status.get("qa_result_path") or "").strip()
        if explicit_qa_result:
            path = Path(explicit_qa_result).expanduser().resolve()
            if path.is_file():
                return path
        explicit_output_dir = str(run_status.get("artifacts_output_dir") or "").strip()
        candidate_dirs = []
        if explicit_output_dir:
            candidate_dirs.append(Path(explicit_output_dir).expanduser().resolve())
        candidate_dirs.append(self.chemqa_root / "generated" / "artifacts" / run_id)
        for directory in candidate_dirs:
            path = directory / "qa_result.json"
            if path.is_file():
                return path
        return None

    def _archive_dir(self, *, group_id: str, record_id: str, run_id: str) -> Path:
        return self.launch_workspace_root.parent / "artifacts" / group_id / self._slugify(record_id, limit=80) / run_id

    def _protocol_candidates_in_dir(self, source_dir: Path) -> tuple[Path, ...]:
        return (
            source_dir / "chemqa_review_protocol.yaml",
            source_dir / "chemqa_review_protocol.yml",
            source_dir / "chemqa_review_protocol.json",
            source_dir / "debate-coordinator" / "chemqa_review_protocol.yaml",
            source_dir / "debate-coordinator" / "chemqa_review_protocol.json",
            source_dir / "coordinator" / "chemqa_review_protocol.yaml",
            source_dir / "coordinator" / "chemqa_review_protocol.json",
        )

    def _resolve_protocol_file(self, run_id: str, run_status: dict[str, Any]) -> Path | None:
        explicit_candidates = []
        explicit_protocol = str(run_status.get("protocol_path") or "").strip()
        explicit_workspace_protocol = str(run_status.get("workspace_protocol_path") or "").strip()
        if explicit_protocol:
            candidate = Path(explicit_protocol).expanduser().resolve()
            if self._is_allowed_protocol_source(candidate.parent, run_id=run_id):
                explicit_candidates.append(candidate)
        if explicit_workspace_protocol:
            candidate = Path(explicit_workspace_protocol).expanduser().resolve()
            if self._is_allowed_protocol_source(candidate.parent, run_id=run_id):
                explicit_candidates.append(candidate)

        seen: set[str] = set()
        for candidate in explicit_candidates:
            key = str(candidate)
            if key in seen:
                continue
            seen.add(key)
            if candidate.is_file():
                return candidate

        for source_dir in self._candidate_protocol_dirs(run_id, run_status):
            for candidate in self._protocol_candidates_in_dir(source_dir):
                key = str(candidate)
                if key in seen:
                    continue
                seen.add(key)
                if candidate.is_file():
                    return candidate
        return None

    def _candidate_artifact_dirs(
        self,
        run_id: str,
        run_status: dict[str, Any],
        *,
        qa_result_path: Path | None = None,
    ) -> list[Path]:
        candidates: list[Path] = []
        if qa_result_path is not None:
            candidates.append(qa_result_path.expanduser().resolve().parent)
        explicit_output_dir = str(run_status.get("artifacts_output_dir") or "").strip()
        if explicit_output_dir:
            candidates.append(Path(explicit_output_dir).expanduser().resolve())
        candidates.append(self.chemqa_root / "generated" / "artifacts" / run_id)

        deduped: list[Path] = []
        seen: set[str] = set()
        for candidate in candidates:
            key = str(candidate)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(candidate)
        return deduped

    def _copy_existing_artifacts(self, *, source_dir: Path, archive_dir: Path) -> None:
        if not source_dir.is_dir():
            return
        for filename in self._ARCHIVABLE_ARTIFACT_FILENAMES:
            source_path = source_dir / filename
            if not source_path.is_file():
                continue
            shutil.copy2(source_path, archive_dir / filename)
        for filename in (
            "final_answer_artifact.json",
            "failure_artifact.json",
            "artifact_manifest.json",
            "candidate_view.json",
            "validation_summary.json",
        ):
            source_path = source_dir / filename
            if source_path.is_file():
                shutil.copy2(source_path, archive_dir / filename)

    def _normalize_archived_qa_result(self, archive_dir: Path) -> dict[str, Any] | None:
        qa_result_path = archive_dir / "qa_result.json"
        if not qa_result_path.is_file():
            return None
        payload = json.loads(qa_result_path.read_text(encoding="utf-8"))
        artifact_paths = payload.get("artifact_paths")
        if not isinstance(artifact_paths, dict):
            artifact_paths = {}
        normalized_paths: dict[str, str] = {}
        key_to_filename = {
            "candidate_submission": "candidate_submission.json",
            "acceptance_decision": "acceptance_decision.json",
            "submission_trace": "submission_trace.json",
            "submission_cycles": "submission_cycles.json",
            "proposer_trajectory": "proposer_trajectory.json",
            "reviewer_trajectories": "reviewer_trajectories.json",
            "review_statuses": "review_statuses.json",
            "final_review_items": "final_review_items.json",
            "final_answer": "final_answer.md",
            "final_submission": "final_submission.json",
            "qa_result": "qa_result.json",
            "final_answer_artifact": "final_answer_artifact.json",
            "failure_artifact": "failure_artifact.json",
            "artifact_manifest": "artifact_manifest.json",
            "candidate_view": "candidate_view.json",
            "validation_summary": "validation_summary.json",
        }
        for key, filename in key_to_filename.items():
            candidate = archive_dir / filename
            if candidate.is_file():
                normalized_paths[key] = str(candidate)
            elif isinstance(artifact_paths.get(key), str) and str(artifact_paths.get(key)).strip():
                normalized_paths[key] = str(artifact_paths[key]).strip()
        payload["artifact_paths"] = normalized_paths
        qa_result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return payload

    def _archive_artifacts(
        self,
        *,
        run_id: str,
        group_id: str,
        record_id: str,
        run_status: dict[str, Any],
        env: dict[str, str],
        qa_result_path: Path | None = None,
    ) -> dict[str, Any]:
        archive_dir = self._archive_dir(group_id=group_id, record_id=record_id, run_id=run_id)
        archive_dir.mkdir(parents=True, exist_ok=True)

        errors: list[str] = []
        protocol_path = self._resolve_protocol_file(run_id, run_status)
        archived_protocol_path = ""
        if protocol_path is not None:
            archived_protocol = archive_dir / "chemqa_review_protocol.yaml"
            try:
                shutil.copy2(protocol_path, archived_protocol)
                archived_protocol_path = str(archived_protocol)
            except Exception as exc:
                errors.append(f"protocol_copy_failed: {exc}")

        for source_dir in self._candidate_artifact_dirs(run_id, run_status, qa_result_path=qa_result_path):
            try:
                self._copy_existing_artifacts(source_dir=source_dir, archive_dir=archive_dir)
            except Exception as exc:
                errors.append(f"artifact_copy_failed[{source_dir}]: {exc}")

        archived_qa_result_path = archive_dir / "qa_result.json"
        if not archived_qa_result_path.is_file() and protocol_path is not None:
            try:
                self._collect_artifacts_from_source(source_dir=protocol_path.parent, output_dir=archive_dir, env=env)
            except Exception as exc:
                errors.append(f"artifact_rebuild_failed: {exc}")

        normalized_qa_result = None
        if archived_qa_result_path.is_file():
            try:
                normalized_qa_result = self._normalize_archived_qa_result(archive_dir)
            except Exception as exc:
                errors.append(f"qa_result_normalization_failed: {exc}")

        archived_artifact_paths: dict[str, str] = {}
        for filename in self._ARCHIVABLE_ARTIFACT_FILENAMES:
            archived_path = archive_dir / filename
            if archived_path.is_file():
                archived_artifact_paths[archived_path.stem] = str(archived_path)
        for filename in (
            "final_answer_artifact.json",
            "failure_artifact.json",
            "artifact_manifest.json",
            "candidate_view.json",
            "validation_summary.json",
        ):
            archived_path = archive_dir / filename
            if archived_path.is_file():
                archived_artifact_paths[archived_path.stem] = str(archived_path)
        if normalized_qa_result is not None and isinstance(normalized_qa_result.get("artifact_paths"), dict):
            archived_artifact_paths.update(
                {str(key): str(value) for key, value in normalized_qa_result["artifact_paths"].items() if str(value).strip()}
            )

        has_protocol = bool(archived_protocol_path)
        has_qa_result = archived_qa_result_path.is_file()
        if has_protocol and has_qa_result:
            archive_status = "ok"
        elif has_protocol:
            archive_status = "protocol_only"
        elif archived_artifact_paths:
            archive_status = "artifacts_only"
        elif errors:
            archive_status = "error"
        else:
            archive_status = "missing"

        return {
            "archive_dir": str(archive_dir),
            "archived_protocol_path": archived_protocol_path,
            "archived_artifact_paths": archived_artifact_paths,
            "artifact_archive_status": archive_status,
            "artifact_archive_error": "\n".join(errors).strip(),
            "qa_result_path": str(archived_qa_result_path) if archived_qa_result_path.is_file() else "",
        }

    def _candidate_submission_paths(self, run_id: str, run_status: dict[str, Any]) -> list[Path]:
        import re

        candidates: list[Path] = []
        for root in self._candidate_protocol_dirs(run_id, run_status):
            if not root.exists():
                continue
            for path in root.rglob("proposer-1.md"):
                if "proposals" not in path.parts:
                    continue
                candidates.append(path.resolve())

        deduped: list[Path] = []
        seen: set[str] = set()
        for candidate in candidates:
            key = str(candidate)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(candidate)

        def sort_key(path: Path) -> tuple[int, float, str]:
            match = re.search(r"epoch-(\d+)", str(path))
            epoch = int(match.group(1)) if match else -1
            try:
                mtime = path.stat().st_mtime
            except OSError:
                mtime = 0.0
            return (epoch, mtime, str(path))

        return sorted(deduped, key=sort_key, reverse=True)

    def _build_candidate_submission_fallback(self, run_id: str, run_status: dict[str, Any]) -> tuple[str, str, dict[str, Any]] | None:
        for proposal_path in self._candidate_submission_paths(run_id, run_status):
            proposal_payload = self._load_yaml_mapping(proposal_path)
            if not proposal_payload:
                continue
            short_answer_text, full_response_text = self._build_chemqa_response_from_submission(final_submission=proposal_payload)
            if short_answer_text:
                return short_answer_text, full_response_text, {
                    "fallback_source": "proposer-1-proposal",
                    "proposal_path": str(proposal_path),
                    "proposal_payload": proposal_payload,
                }

        preview = self._normalize_space(str(run_status.get("final_answer_preview") or ""))
        if preview:
            return preview, f"FINAL ANSWER: {preview}", {
                "fallback_source": "run-status-final-answer-preview",
            }
        return None

    def _assess_recovered_answer(
        self,
        *,
        run_id: str,
        run_status: dict[str, Any],
        archive_meta: dict[str, Any],
    ) -> dict[str, Any] | None:
        projection = self._failure_artifact_answer_projection(run_status=run_status, archive_meta=archive_meta)
        if projection is not None:
            return projection
        fallback_payload = self._build_candidate_submission_fallback(run_id, run_status)
        if fallback_payload is None:
            return None
        short_answer_text, full_response_text, fallback_meta = fallback_payload
        short_text = self._normalize_space(short_answer_text)
        if not short_text:
            return {
                "evaluable": False,
                "scored": False,
                "reliability": "none",
                "recovery_mode": str(fallback_meta.get("fallback_source") or "none"),
                "reason": "empty_short_answer",
                "short_answer_text": "",
                "full_response_text": full_response_text,
                "details": fallback_meta,
            }
        recovery_mode = str(fallback_meta.get("fallback_source") or "candidate_submission")
        if recovery_mode == "run-status-final-answer-preview":
            return {
                "evaluable": False,
                "scored": False,
                "reliability": "low_confidence_recovered",
                "recovery_mode": recovery_mode,
                "reason": "preview_requires_strict_validation",
                "short_answer_text": short_text,
                "full_response_text": full_response_text,
                "details": fallback_meta,
            }
        return {
            "evaluable": True,
            "scored": True,
            "reliability": "high_confidence_recovered",
            "recovery_mode": recovery_mode,
            "reason": "",
            "short_answer_text": short_text,
            "full_response_text": full_response_text,
            "details": fallback_meta,
        }

    def _failure_artifact_answer_projection(
        self,
        *,
        run_status: dict[str, Any],
        archive_meta: dict[str, Any],
    ) -> dict[str, Any] | None:
        candidates: list[Path] = []
        for key in ("failure_artifact_path",):
            value = str(run_status.get(key) or "").strip()
            if value:
                candidates.append(Path(value).expanduser())
        archived_paths = archive_meta.get("archived_artifact_paths")
        if isinstance(archived_paths, dict):
            value = str(archived_paths.get("failure_artifact") or "").strip()
            if value:
                candidates.append(Path(value).expanduser())
        qa_result_path = str(archive_meta.get("qa_result_path") or run_status.get("qa_result_path") or "").strip()
        payloads: list[dict[str, Any]] = []
        for candidate in candidates:
            if not candidate.is_file():
                continue
            try:
                payload = json.loads(candidate.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(payload, dict):
                payloads.append(payload)
        if qa_result_path:
            path = Path(qa_result_path).expanduser()
            if path.is_file():
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    payload = {}
                if isinstance(payload, dict):
                    payloads.append(payload)

        for payload in payloads:
            projection = payload.get("answer_projection")
            recovery = payload.get("recovery_eligibility")
            if not isinstance(projection, dict) or not isinstance(recovery, dict):
                continue
            short_text = self._normalize_space(
                str(
                    projection.get("evaluator_answer")
                    or projection.get("direct_answer")
                    or projection.get("answer")
                    or projection.get("value")
                    or ""
                )
            )
            full_text = str(projection.get("full_answer") or projection.get("display_answer") or short_text).strip()
            if short_text and full_text == short_text:
                full_text = f"FINAL ANSWER: {short_text}"
            return {
                "evaluable": bool(recovery.get("evaluable")),
                "scored": bool(recovery.get("scored")),
                "reliability": str(recovery.get("reliability") or "none"),
                "recovery_mode": str(recovery.get("recovery_mode") or "failure_artifact_answer_projection"),
                "reason": str(recovery.get("reason") or ""),
                "short_answer_text": short_text,
                "full_response_text": full_text,
                "details": {
                    "fallback_source": "failure_artifact",
                    "answer_projection": self._deep_copy_jsonish(projection),
                    "recovery_eligibility": self._deep_copy_jsonish(recovery),
                },
            }
        return None

    def _collect_artifacts_from_source(self, *, source_dir: Path, output_dir: Path, env: dict[str, str]) -> None:
        command = [
            self._current_python(),
            str(self.collect_script),
            "--skill-root",
            str(self.chemqa_root),
            "--source-dir",
            str(source_dir),
            "--output-dir",
            str(output_dir),
        ]
        answer_kind = str(env.get("CHEMQA_ANSWER_KIND") or "").strip()
        if answer_kind:
            command.extend(["--answer-kind", answer_kind])
        result = self._run_subprocess(command, env=env, cwd=self.chemqa_root, timeout=120)
        self._parse_json_stdout(result, command)

    def _load_archived_completed_qa_result(self, archive_meta: dict[str, Any]) -> tuple[Path, dict[str, Any]] | None:
        archived_qa_result = str(archive_meta.get("qa_result_path") or "").strip()
        if not archived_qa_result:
            return None
        qa_result_path = Path(archived_qa_result).expanduser()
        if not qa_result_path.is_file():
            return None
        payload = json.loads(qa_result_path.read_text(encoding="utf-8"))
        if str(payload.get("terminal_state") or "").strip() != "completed":
            return None
        return qa_result_path.resolve(), payload

    def _ensure_artifacts(
        self,
        run_id: str,
        *,
        env: dict[str, str],
        run_status: dict[str, Any],
        wait_seconds: int = 120,
        poll_seconds: int = 5,
    ) -> Path:
        import time

        deadline = time.time() + wait_seconds
        last_seen_status = run_status
        checked_sources: list[str] = []
        while time.time() < deadline:
            last_seen_status = self._read_run_status(run_id) or last_seen_status
            qa_result_path = self._resolve_existing_qa_result(run_id, last_seen_status)
            if qa_result_path is not None:
                return qa_result_path

            output_dir = Path(
                str(last_seen_status.get("artifacts_output_dir") or (self.chemqa_root / "generated" / "artifacts" / run_id))
            ).expanduser().resolve()
            output_dir.mkdir(parents=True, exist_ok=True)

            for source_dir in self._candidate_protocol_dirs(run_id, last_seen_status):
                checked_sources.append(str(source_dir))
                if (source_dir / "chemqa_review_protocol.yaml").is_file() or (source_dir / "chemqa_review_protocol.yml").is_file():
                    self._collect_artifacts_from_source(source_dir=source_dir, output_dir=output_dir, env=env)
                    qa_result_path = output_dir / "qa_result.json"
                    if qa_result_path.is_file():
                        return qa_result_path
            time.sleep(poll_seconds)

        error_message = (
            f"ChemQA run `{run_id}` reached terminal state but artifacts were not resolved within {wait_seconds}s. "
            f"Last run status: {last_seen_status}. Checked sources: {checked_sources}"
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

    @staticmethod
    def _safe_session_id(*parts: str) -> str:
        raw = "-".join(part for part in parts if part)
        normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", raw)
        return re.sub(r"-{2,}", "-", normalized).strip("-")

    def _slot_identities(self, *, record: Any, group: Any, run_id: str) -> list[AttemptIdentity]:
        slots = self._actual_slot_ids(self.slot_set)
        suffixes = {
            "debate-coordinator": "coordinator",
            "debate-1": "proposer-1",
            "debate-2": "proposer-2",
            "debate-3": "proposer-3",
            "debate-4": "proposer-4",
            "debate-5": "proposer-5",
        }
        identities: list[AttemptIdentity] = []
        for logical_slot, agent_id in slots.items():
            identities.append(
                AttemptIdentity(
                    run_id=self.workspace_manager.run_id,
                    invocation_id=self.workspace_manager.invocation_id,
                    group_id=str(group.id),
                    runner_kind="chemqa",
                    agent_id=agent_id,
                    record_id=str(record.record_id),
                    attempt_index=0,
                    session_id=self._safe_session_id("chemqa-review", run_id, suffixes[logical_slot]),
                    template_id="chemqa-role-v1",
                )
            )
        return identities

    def _managed_slot_workspace_root(self) -> Path:
        if not self._active_slot_workspaces:
            raise RuntimeError("ChemQA managed slot workspace set is not active.")
        roots = {workspace.parent for workspace in self._active_slot_workspaces.values()}
        if len(roots) != 1:
            raise RuntimeError("ChemQA managed slot workspaces do not share one group root.")
        return next(iter(roots))

    def _install_lease_set(self, lease_set: WorkspaceLeaseSet) -> None:
        self._current_lease_set = lease_set
        self._active_slot_workspaces = {
            lease.identity.agent_id: lease.active_workspace for lease in lease_set.leases
        }
        workspace_root = self._managed_slot_workspace_root()
        for lease in lease_set.leases:
            debate_sentinel = {
                "kind": "debateclaw-slot-workspace",
                "version": 1,
                "slot": lease.identity.agent_id,
                "workspace": str(lease.active_workspace),
                "workspace_root": str(workspace_root),
                "last_session_id": lease.identity.session_id,
                "managed_by": "debateclaw",
            }
            (lease.active_workspace / ".debateclaw-slot.json").write_text(
                json.dumps(debate_sentinel, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

    def _rotate_workspaces_for_recovery(self, run_id: str) -> None:
        lease_set = self._current_lease_set
        if lease_set is None:
            raise WorkspaceIsolationError(
                "workspace_recovery_failed",
                "ChemQA recovery requested without an active workspace lease set.",
            )
        manifest_path = self._cleanup_manifest_path(self.launch_workspace_root.parent, run_id)
        try:
            cleanup_report = self._invoke_cleanroom_cleanup(manifest_path=manifest_path)
        except Exception as exc:
            raise WorkspaceIsolationError(
                "workspace_recovery_failed",
                f"ChemQA recovery could not stop the previous attempt processes: {exc}",
                details={"exception_type": type(exc).__name__},
            ) from exc

        diagnostic_result = RunnerResult(
            status=RunStatus.FAILED,
            answer=AnswerPayload(),
            raw={"run_id": run_id},
            runner_meta={"run_id": run_id, "recovery_rotation": True},
            failure=FailureInfo(
                code="chemqa_recovery_rotation",
                message="ChemQA stalled attempt is being rotated before recovery.",
            ),
        )
        audits = {
            lease.identity.agent_id: self._audit_slot(
                lease=lease,
                result=diagnostic_result,
                input_bundle=self._current_input_bundle,
            )
            for lease in lease_set.leases
        }
        archive_meta: dict[str, Any] = {
            "attempt_index": lease_set.leases[0].identity.attempt_index,
            "cleanup_report": cleanup_report,
            "slots": {},
        }
        errors: list[WorkspaceIsolationError] = []
        for lease in lease_set.leases:
            audit = audits[lease.identity.agent_id]
            try:
                archive = self.workspace_manager.seal(
                    lease,
                    AttemptOutcome(
                        runner_status="failed",
                        archive_reason="timeout_retry",
                        contamination_audit=audit,
                    ),
                )
                archive_meta["slots"][lease.identity.agent_id] = archive.to_meta()
            except WorkspaceIsolationError as error:
                errors.append(error)
                archive_meta["slots"][lease.identity.agent_id] = {"archive_ok": False, "error": dict(error.details)}
        self._workspace_attempt_archives.append(archive_meta)
        self._current_lease_set = None
        self._active_slot_workspaces = {}
        non_evaluable = any(audit.adjudication == "non_evaluable" for audit in audits.values())
        if non_evaluable:
            raise WorkspaceIsolationError(
                "benchmark_workspace_contamination",
                "ChemQA recovery workspace audit was contaminated or unavailable.",
                details={
                    "audits": {
                        agent_id: audit.to_payload() for agent_id, audit in audits.items()
                    }
                },
            )
        if errors:
            raise WorkspaceIsolationError(
                "workspace_recovery_failed",
                "ChemQA recovery could not archive all previous attempt workspaces.",
                details={"archive_errors": [dict(error.details) for error in errors]},
            )

        next_identities = [
            replace(identity, attempt_index=identity.attempt_index + 1)
            for identity in (lease.identity for lease in lease_set.leases)
        ]
        try:
            next_lease_set = self.workspace_manager.prepare_set(next_identities)
        except WorkspaceIsolationError as exc:
            raise WorkspaceIsolationError(
                "workspace_recovery_failed",
                f"ChemQA recovery could not prepare a fresh workspace lease set: {exc.message}",
                details={"prepare_error": dict(exc.details)},
            ) from exc
        self._install_lease_set(next_lease_set)

    def _chemqa_attempt_failure(self, *, exc: Exception, run_id: str) -> RunnerResult:
        message = f"ChemQA attempt failed before returning a terminal result: {exc}"
        return RunnerResult(
            status=RunStatus.FAILED,
            answer=AnswerPayload(),
            raw={"exception_type": type(exc).__name__, "exception_message": str(exc)},
            runner_meta={
                "run_id": run_id,
                "error": message,
                "execution_error": {
                    "code": "chemqa_attempt_exception",
                    "layer": "runner",
                    "source": "exception",
                    "retryable": False,
                    "exception_type": type(exc).__name__,
                },
            },
            failure=FailureInfo(
                code="chemqa_attempt_exception",
                message=message,
                details={"exception_type": type(exc).__name__, "exception_message": str(exc)},
            ),
        )

    def _workspace_failure_result(
        self,
        *,
        error: WorkspaceIsolationError,
        isolation_meta: dict[str, Any],
        run_id: str,
        original_result: RunnerResult | None = None,
    ) -> RunnerResult:
        runner_meta = dict(original_result.runner_meta if original_result is not None else {})
        runner_meta.update(
            {
                "run_id": run_id,
                "error": error.message,
                "execution_error": dict(error.details),
                "workspace_isolation": isolation_meta,
            }
        )
        raw: dict[str, Any] = {"workspace_isolation_error": dict(error.details)}
        if original_result is not None:
            raw["discarded_runner_raw"] = original_result.raw
            raw["discarded_status"] = original_result.status.value
        return RunnerResult(
            status=RunStatus.FAILED,
            answer=AnswerPayload(),
            raw=raw,
            runner_meta=runner_meta,
            failure=error.to_failure_info(),
        )

    def _audit_slot(
        self,
        *,
        lease: AttemptWorkspaceLease,
        result: RunnerResult,
        input_bundle: Any,
    ) -> WorkspaceAudit:
        if self._session_audit_resolver is None:
            session_audit: dict[str, Any] = {}
        else:
            try:
                session_store_path = None
                if self._current_launch_home is not None:
                    session_store_path = (
                        self._current_launch_home
                        / ".openclaw"
                        / "agents"
                        / sanitize_agent_id(lease.identity.agent_id)
                        / "sessions"
                        / "sessions.json"
                    )
                session_audit = self._session_audit_resolver(
                    lease.identity.agent_id,
                    lease.identity.session_id,
                    session_store_path=session_store_path,
                )
            except Exception as exc:
                session_audit = {
                    "session_isolation_ok": False,
                    "audit_error": type(exc).__name__,
                }
        runner_meta = {**result.runner_meta, "session_isolation": session_audit}
        bundle_dir = getattr(input_bundle, "bundle_dir", None)
        policy = self.workspace_manager.policy_for_lease(
            lease,
            role="chemqa",
            skills_enabled=True,
            always_read_scopes=([Path(bundle_dir)] if bundle_dir is not None else []),
            read_scopes=self.allowed_workspace_roots,
        )
        if self._contamination_auditor is not None:
            return ensure_workspace_audit(self._contamination_auditor(
                lease=lease,
                runner_meta=runner_meta,
                allowed_roots=[scope.path for scope in policy.read_scopes],
                environment=os.environ,
                policy=policy,
            ))
        return self.workspace_manager.audit_attempt(
            lease,
            runner_meta,
            allowed_roots=[scope.path for scope in policy.read_scopes],
            environment=os.environ,
            policy=policy,
        )

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
