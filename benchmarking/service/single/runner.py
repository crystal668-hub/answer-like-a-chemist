from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import uuid
from contextlib import nullcontext
from datetime import UTC, datetime
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import Any
from dotenv import dotenv_values

from benchmarking.core.contracts import (
    AnswerPayload,
    FailureInfo,
    RecoveryInfo,
    RunnerResult,
    RunStatus,
)
from benchmarking.core.convergence import (
    ConvergencePolicy,
    has_final_answer_marker,
    has_research_final_marker,
    is_complete_answer_for_eval,
    is_timeout_family_text,
)
from benchmarking.runtime import paths as runtime_paths
from benchmarking.runtime.bundles import RuntimePathProjection
from benchmarking.runtime.attempt_finalization import cleanup_owned_environment, read_evidence, register_environment, write_evidence
from benchmarking.runtime.dependency_evidence import validate_dependency_evidence
from benchmarking.runtime.agent_workspace import (
    AttemptIdentity,
    AttemptOutcome,
    AttemptWorkspaceLease,
    AttemptWorkspaceManager,
    WorkspaceIsolationError,
)
from benchmarking.runtime.attempt_environment import (
    AttemptPythonEnvironment,
    collect_dependency_manifest,
    create_attempt_environment,
    dependency_install_events,
    remediate_forbidden_distributions,
)
from benchmarking.runtime.container_runtime import (
    ContainerRuntimeError,
    ContainerAttemptSpec,
    ContainerMount,
    DockerContainerRuntime,
    materialize_container_config,
)
from benchmarking.runtime.cancellation import CancellationReason
from benchmarking.runtime.error_capture import (
    ExecutionErrorClassification,
    capture_execution_error,
)
from benchmarking.runtime.openclaw_env import build_openclaw_subprocess_env
from benchmarking.runtime.session_isolation import (
    SessionIsolationError,
    inspect_postflight_session,
)
from benchmarking.runtime.workspace_policy import (
    ContaminationAudit,
    WorkspaceAccessPolicy,
    WorkspaceAudit,
    ensure_workspace_audit,
    build_workspace_access_policy,
)
from benchmarking.skills.audit import build_skill_use_audit
from benchmarking.workflow.attempt_queue import RetryDelay, WorkStep, staged

OPENCLAW_RESPONSE_TIMEOUT_TEXT = "Request timed out before a response was generated"
OPENCLAW_IDLE_TIMEOUT_TEXT = "The model did not produce a response before the LLM idle timeout"
OPENCLAW_SHORT_LLM_TIMEOUT_TEXT = "LLM request timed out."
OPENCLAW_SHORT_REQUEST_TIMEOUT_TEXT = "Request timed out."
OPENCLAW_STREAM_READ_ERROR_TEXT = "stream_read_error"
OPENCLAW_AGENT_NO_RESPONSE_FRAGMENT = "Agent couldn't generate a response"
OPENCLAW_TIMEOUT_SENTINELS = (
    OPENCLAW_SHORT_LLM_TIMEOUT_TEXT,
    OPENCLAW_SHORT_REQUEST_TIMEOUT_TEXT,
    OPENCLAW_RESPONSE_TIMEOUT_TEXT,
    OPENCLAW_IDLE_TIMEOUT_TEXT,
)
NO_TIMEOUT_SUBPROCESS_GUARD_SECONDS = 24 * 60 * 60
HLE_ANSWER_RE = re.compile(r"(?im)^\s*Answer\s*:\s*\S")
FINAL_MARKER_REQUIRED_EVAL_KINDS = {
    "superchem_multiple_choice_rpf",
    "chembench_open_ended",
    "frontierscience_olympiad",
    "verifier_grounded",
}


def verifier_grounded_answer_schema_from_record(record: Any) -> dict[str, Any]:
    if str(getattr(record, "eval_kind", "") or "").strip() != "verifier_grounded":
        return {}
    candidates: list[Any] = []
    payload = getattr(record, "payload", None)
    if isinstance(payload, dict):
        candidates.append(payload.get("verifier_grounded"))
    grading = getattr(record, "grading", None)
    config = getattr(grading, "config", None)
    if isinstance(config, dict):
        candidates.append(config.get("verifier_grounded"))
    for verifier_config in candidates:
        if not isinstance(verifier_config, dict):
            continue
        schema = verifier_config.get("answer_schema")
        if isinstance(schema, dict):
            return dict(schema)
        task = verifier_config.get("task")
        if not isinstance(task, dict):
            continue
        schema = task.get("answer_schema")
        if isinstance(schema, dict):
            return dict(schema)
    return {}


@dataclass(frozen=True)
class CandidateAnswerContract:
    valid: bool
    code: str = ""
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentErrorClassification:
    kind: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TimeoutRetryDecision:
    retryable: bool
    reason: str = ""


def _error_dict(runner_meta: dict[str, Any]) -> dict[str, Any]:
    error = runner_meta.get("error")
    return error if isinstance(error, dict) else {}


def is_runner_meta_timeout_family(runner_meta: dict[str, Any]) -> bool:
    error = _error_dict(runner_meta)
    execution_error = runner_meta.get("execution_error")
    if isinstance(execution_error, dict) and execution_error.get("retryable") is False:
        return False
    kind = str(error.get("kind") or runner_meta.get("error_kind") or runner_meta.get("kind") or "").strip().lower()
    if kind == "timeout":
        return True
    candidates = [
        runner_meta.get("error"),
        runner_meta.get("message"),
        runner_meta.get("stderr"),
        runner_meta.get("stdout"),
        error.get("message"),
        error.get("code"),
        error.get("type"),
    ]
    convergence = runner_meta.get("convergence")
    if isinstance(convergence, dict):
        if convergence.get("latest_prompt_error_is_timeout") is True:
            return True
        candidates.extend(
            [
                convergence.get("latest_prompt_error"),
                convergence.get("prompt_error"),
                convergence.get("finalization_rescue_error"),
            ]
        )
    return any(is_timeout_family_text(candidate) for candidate in candidates if candidate is not None)


def is_openclaw_timeout_result(
    *,
    runner_meta: dict[str, Any],
    full_response_text: str,
    eval_kind: str = "",
    answer_schema: dict[str, Any] | None = None,
) -> bool:
    text = str(full_response_text or "")
    stripped = text.strip()
    if is_complete_answer_for_eval(
        text,
        eval_kind=str(eval_kind or ""),
        answer_schema=answer_schema,
    ) or HLE_ANSWER_RE.search(text):
        return False
    if is_runner_meta_timeout_family(runner_meta):
        return True
    has_timeout_text = any(needle in text for needle in OPENCLAW_TIMEOUT_SENTINELS)
    if has_timeout_text and stripped in OPENCLAW_TIMEOUT_SENTINELS:
        return True
    if has_timeout_text and (
        runner_meta.get("aborted") is True or str(runner_meta.get("livenessState") or "") == "blocked"
    ):
        return True
    return is_timeout_family_text(text)


def classify_agent_error_payload(
    *,
    payloads: list[dict[str, Any]],
    runner_meta: dict[str, Any],
    full_response_text: str,
    eval_kind: str = "",
    answer_schema: dict[str, Any] | None = None,
) -> AgentErrorClassification | None:
    if is_openclaw_timeout_result(
        runner_meta=runner_meta,
        full_response_text=full_response_text,
        eval_kind=eval_kind,
        answer_schema=answer_schema,
    ):
        return None
    payload_texts = [str(item.get("text") or "").strip() for item in payloads if isinstance(item, dict)]
    error_payload = any(item.get("isError") is True for item in payloads if isinstance(item, dict))
    completion = runner_meta.get("completion") if isinstance(runner_meta.get("completion"), dict) else {}
    stop_reason = str(runner_meta.get("stopReason") or "").strip().lower()
    finish_reason = str(completion.get("finishReason") or completion.get("stopReason") or "").strip().lower()
    liveness_state = str(runner_meta.get("livenessState") or "").strip()
    has_complete_answer = bool(
        is_complete_answer_for_eval(
            full_response_text,
            eval_kind=str(eval_kind or ""),
            answer_schema=answer_schema,
        )
        or HLE_ANSWER_RE.search(full_response_text)
    )
    replay_invalid_diagnostics = _replay_invalid_diagnostics(
        runner_meta=runner_meta,
        payload_is_error=error_payload,
    )

    if (
        any(text == OPENCLAW_STREAM_READ_ERROR_TEXT for text in payload_texts)
        or stop_reason == "error"
        or finish_reason == "error"
    ):
        message = "Single-LLM OpenClaw agent stream failed before producing a benchmark answer."
        return AgentErrorClassification(
            kind="agent_stream_read_error",
            message=message,
            details={
                "kind": "agent_stream_read_error",
                "message": message,
                "payload_texts": payload_texts[:5],
                "payload_is_error": error_payload,
                "stopReason": runner_meta.get("stopReason"),
                "finishReason": completion.get("finishReason"),
                "livenessState": runner_meta.get("livenessState"),
                "replayInvalid": runner_meta.get("replayInvalid"),
                **({"replay_invalid_diagnostics": replay_invalid_diagnostics} if replay_invalid_diagnostics else {}),
            },
        )

    if (
        any(OPENCLAW_AGENT_NO_RESPONSE_FRAGMENT in text for text in payload_texts)
        or (runner_meta.get("replayInvalid") is True and not has_complete_answer)
        or (liveness_state in {"abandoned", "blocked"} and (error_payload or not has_complete_answer))
    ):
        message = "Single-LLM OpenClaw agent response was unavailable before producing a benchmark answer."
        return AgentErrorClassification(
            kind="agent_response_unavailable",
            message=message,
            details={
                "kind": "agent_response_unavailable",
                "message": message,
                "payload_texts": payload_texts[:5],
                "payload_is_error": error_payload,
                "stopReason": runner_meta.get("stopReason"),
                "finishReason": completion.get("finishReason"),
                "livenessState": runner_meta.get("livenessState"),
                "replayInvalid": runner_meta.get("replayInvalid"),
                **({"replay_invalid_diagnostics": replay_invalid_diagnostics} if replay_invalid_diagnostics else {}),
            },
        )

    return None


def _extract_diagnostic_text(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("message", "error", "code", "type", "reason"):
            text = str(value.get(key) or "").strip()
            if text:
                return text
    return str(value or "").strip()


def _replay_invalid_diagnostics(*, runner_meta: dict[str, Any], payload_is_error: bool) -> dict[str, Any]:
    if runner_meta.get("replayInvalid") is not True:
        return {}
    completion = runner_meta.get("completion") if isinstance(runner_meta.get("completion"), dict) else {}
    convergence = runner_meta.get("convergence") if isinstance(runner_meta.get("convergence"), dict) else {}
    existing = convergence.get("replay_invalid_diagnostics")
    if isinstance(existing, dict):
        diagnostics = dict(existing)
        diagnostics.setdefault("reason", "replay_invalid")
        diagnostics.setdefault("payload_is_error", payload_is_error)
        return diagnostics
    diagnostic_candidates = [
        runner_meta.get("replayInvalidReason"),
        runner_meta.get("replayError"),
        runner_meta.get("error"),
        runner_meta.get("message"),
        convergence.get("latest_prompt_error"),
        convergence.get("finalization_rescue_error"),
    ]
    diagnostic_text = ""
    for candidate in diagnostic_candidates:
        diagnostic_text = _extract_diagnostic_text(candidate)
        if diagnostic_text:
            break
    return {
        "reason": "replay_invalid",
        "diagnostic_text": diagnostic_text,
        "stopReason": runner_meta.get("stopReason"),
        "finishReason": completion.get("finishReason") or completion.get("stopReason"),
        "livenessState": runner_meta.get("livenessState"),
        "payload_is_error": payload_is_error,
        "latest_prompt_error": convergence.get("latest_prompt_error"),
        "latest_prompt_error_is_timeout": convergence.get("latest_prompt_error_is_timeout"),
    }


def _candidate_contract_meta(
    *,
    valid: bool,
    record: Any,
    short_answer_text: str,
    full_response_text: str,
    code: str = "",
    message: str = "",
    missing_fields: list[str] | None = None,
) -> dict[str, Any]:
    answer_schema = verifier_grounded_answer_schema_from_record(record)
    schema_format = str(answer_schema.get("format") or "")
    schema_value_type = str(answer_schema.get("value_type") or "")
    schema_fence_language = str(answer_schema.get("fence_language") or schema_value_type or "")
    meta: dict[str, Any] = {
        "valid": valid,
        "eval_kind": str(getattr(record, "eval_kind", "") or ""),
        "dataset": str(getattr(record, "dataset", "") or ""),
        "short_answer_text_present": bool(str(short_answer_text or "").strip()),
        "full_response_text_present": bool(str(full_response_text or "").strip()),
        "has_final_answer_marker": has_final_answer_marker(str(full_response_text or "")),
        "has_complete_answer_for_eval": is_complete_answer_for_eval(
            str(full_response_text or ""),
            eval_kind=str(getattr(record, "eval_kind", "") or ""),
            answer_schema=answer_schema,
        ),
        "answer_schema_format": schema_format,
        "answer_schema_value_type": schema_value_type,
        "answer_schema_fence_language": schema_fence_language,
        "has_hle_answer_field": bool(HLE_ANSWER_RE.search(str(full_response_text or ""))),
    }
    if code:
        meta["code"] = code
    if message:
        meta["message"] = message
    if missing_fields:
        meta["missing_fields"] = list(missing_fields)
    raw_text = str(full_response_text or "")
    if meta["eval_kind"] == "frontierscience_research":
        meta["has_research_final_marker"] = has_research_final_marker(raw_text)
    if raw_text:
        meta["raw_text"] = raw_text[:4000]
        meta["raw_text_truncated"] = len(raw_text) > 4000
    return meta


def validate_candidate_answer_contract(
    *,
    record: Any,
    short_answer_text: str,
    full_response_text: str,
    runner_meta: dict[str, Any],
) -> CandidateAnswerContract:
    full_text = str(full_response_text or "").strip()
    short_text = str(short_answer_text or "").strip()
    eval_kind = str(getattr(record, "eval_kind", "") or "").strip()
    answer_schema = verifier_grounded_answer_schema_from_record(record)
    has_complete_answer = is_complete_answer_for_eval(
        full_text,
        eval_kind=eval_kind,
        answer_schema=answer_schema,
    )
    if is_openclaw_timeout_result(
        runner_meta=runner_meta,
        full_response_text=full_text,
        eval_kind=eval_kind,
        answer_schema=answer_schema,
    ):
        message = "Single-LLM OpenClaw agent response timed out before producing a benchmark answer."
        return CandidateAnswerContract(
            valid=False,
            code="agent_response_timeout",
            message=message,
            details=_candidate_contract_meta(
                valid=False,
                record=record,
                short_answer_text=short_text,
                full_response_text=full_text,
                code="agent_response_timeout",
                message=message,
            ),
        )
    if not full_text:
        message = "Single-LLM candidate answer contract invalid: full_response_text is empty."
        return CandidateAnswerContract(
            valid=False,
            code="candidate_answer_contract_invalid",
            message=message,
            details=_candidate_contract_meta(
                valid=False,
                record=record,
                short_answer_text=short_text,
                full_response_text=full_text,
                code="candidate_answer_contract_invalid",
                message=message,
                missing_fields=["full_response_text"],
            ),
        )
    if eval_kind in FINAL_MARKER_REQUIRED_EVAL_KINDS and not has_complete_answer:
        message = "Single-LLM candidate answer contract invalid: required `FINAL ANSWER:` marker is missing."
        return CandidateAnswerContract(
            valid=False,
            code="candidate_answer_contract_invalid",
            message=message,
            details=_candidate_contract_meta(
                valid=False,
                record=record,
                short_answer_text=short_text,
                full_response_text=full_text,
                code="candidate_answer_contract_invalid",
                message=message,
                missing_fields=["short_answer_text", "FINAL ANSWER:"],
            ),
        )
    if eval_kind == "hle" and not HLE_ANSWER_RE.search(full_text):
        message = "Single-LLM candidate answer contract invalid: HLE response must include an `Answer:` field."
        return CandidateAnswerContract(
            valid=False,
            code="candidate_answer_contract_invalid",
            message=message,
            details=_candidate_contract_meta(
                valid=False,
                record=record,
                short_answer_text=short_text,
                full_response_text=full_text,
                code="candidate_answer_contract_invalid",
                message=message,
                missing_fields=["Answer:"],
            ),
        )
    return CandidateAnswerContract(
        valid=True,
        details=_candidate_contract_meta(
            valid=True,
            record=record,
            short_answer_text=short_text,
            full_response_text=full_text,
        ),
    )


class SingleLLMRunner:
    def __init__(
        self,
        *,
        agent_id: str,
        timeout_seconds: int,
        config_path: Path,
        runtime_bundle_root: Path,
        run_subprocess,
        parse_json_stdout,
        unwrap_agent_payload,
        summarize_payloads,
        normalize_answer_tracks,
        ensure_runtime_bundle,
        build_single_llm_prompt,
        slugify,
        benchmark_agent_thinking: str,
        workspace_manager: AttemptWorkspaceManager,
        allowed_workspace_roots: tuple[Path, ...] | list[Path] = (),
        contamination_auditor: Callable[..., ContaminationAudit] | None = None,
        configured_skills: tuple[str, ...] | list[str] = (),
        vgb_configured_skills: tuple[str, ...] | list[str] = (),
        convergence_policy: ConvergencePolicy | None = None,
        timeout_retries: int = 3,
        timeout_retry_backoff_seconds: tuple[int | float, ...] | list[int | float] = (5, 15, 45),
        sleep_fn: Callable[[float], None] = time.sleep,
        no_timeout: bool = False,
        pypi_cutoff: str | None = None,
        admission_controller=None,
        execution_backend: str = "docker",
        container_runtime: DockerContainerRuntime | None = None,
        container_image: str = "openclaw-benchmark-single-llm:latest",
        container_cpus: float | None = None,
        container_memory_bytes: int | None = None,
        container_pids_limit: int | None = None,
    ) -> None:
        self.agent_id = agent_id
        self.timeout_seconds = timeout_seconds
        self.convergence_policy = convergence_policy or ConvergencePolicy(timeout_seconds=timeout_seconds)
        self.config_path = config_path
        self.runtime_bundle_root = runtime_bundle_root
        self.default_configured_skills = tuple(str(skill) for skill in configured_skills)
        self.vgb_configured_skills = tuple(str(skill) for skill in vgb_configured_skills)
        self.configured_skills = self.default_configured_skills
        self._run_subprocess = run_subprocess
        self._parse_json_stdout = parse_json_stdout
        self._unwrap_agent_payload = unwrap_agent_payload
        self._summarize_payloads = summarize_payloads
        self._normalize_answer_tracks = normalize_answer_tracks
        self._ensure_runtime_bundle = ensure_runtime_bundle
        self._build_single_llm_prompt = build_single_llm_prompt
        self._slugify = slugify
        self._benchmark_agent_thinking = benchmark_agent_thinking
        self.workspace_manager = workspace_manager
        self.allowed_workspace_roots = tuple(Path(path).expanduser().resolve() for path in allowed_workspace_roots)
        self._contamination_auditor = contamination_auditor
        self.timeout_retries = max(0, int(timeout_retries))
        self.no_timeout = bool(no_timeout)
        self.pypi_cutoff = str(pypi_cutoff or os.environ.get("BENCHMARK_PYPI_CUTOFF") or datetime.now(UTC).isoformat()).strip()
        self.admission_controller = admission_controller
        self.execution_backend = str(execution_backend or "docker").strip().lower()
        if self.execution_backend not in {"host", "docker"}:
            raise ValueError(f"Unsupported single-LLM execution backend: {execution_backend}")
        self.container_runtime = container_runtime or DockerContainerRuntime()
        self.container_image = container_image
        self.container_cpus = container_cpus
        self.container_memory_bytes = container_memory_bytes
        self.container_pids_limit = container_pids_limit
        self.timeout_retry_backoff_seconds = self._normalize_backoff_seconds(
            timeout_retry_backoff_seconds,
            max_retries=self.timeout_retries,
        )
        self._sleep = sleep_fn

    @staticmethod
    def _normalize_backoff_seconds(
        raw: tuple[int | float, ...] | list[int | float],
        *,
        max_retries: int,
    ) -> tuple[float, ...]:
        if max_retries <= 0:
            return ()
        values = [max(0.0, float(value)) for value in raw]
        if not values:
            values = [0.0]
        while len(values) < max_retries:
            values.append(values[-1])
        return tuple(values[:max_retries])

    def _wrapper_subprocess_timeout_seconds(self) -> int:
        if self.no_timeout:
            return NO_TIMEOUT_SUBPROCESS_GUARD_SECONDS
        return (
            int(self.convergence_policy.timeout_seconds)
            + int(self.convergence_policy.finalization_safety_seconds)
            + 30
        )

    def _timeout_mode(self) -> str:
        return "no_timeout" if self.no_timeout else "bounded"

    def _build_command(
        self,
        *,
        record: Any,
        session_id: str,
        prompt: str,
        wrapper_path: Path,
        config_path: Path | None = None,
        python_executable: str | None = None,
    ) -> list[str]:
        command = [
            python_executable or sys.executable,
            str(wrapper_path),
            "--agent",
            self.agent_id,
            "--config-file",
            str(config_path or self.config_path),
            "--session-id",
            session_id,
            "--message",
            prompt,
            "--thinking",
            self._benchmark_agent_thinking,
            "--eval-kind",
            str(getattr(record, "eval_kind", "") or ""),
            "--json",
        ]
        if not self.no_timeout:
            command.extend(["--timeout", str(self.convergence_policy.timeout_seconds)])
        answer_schema = verifier_grounded_answer_schema_from_record(record)
        if answer_schema:
            command.extend(["--answer-schema-json", json.dumps(answer_schema, sort_keys=True)])
        return command

    @staticmethod
    def _attach_scratch_prompt(
        prompt: str,
        *,
        scratch_dir: Path,
        request_dir: Path,
        output_dir: Path,
        attempt_python_enabled: bool = False,
    ) -> str:
        lines = [
                prompt.rstrip(),
                "",
                "BENCHMARK WORKSPACE FILE CONTRACT:",
                "- Structured file tools must use workspace-relative `scratch/...` paths.",
                '- For exec, omit workdir and begin with: cd "$BENCHMARK_SKILL_SCRATCH_DIR" &&',
                "- Create child output directories in the same shell command before entering them.",
                "- Do not reconstruct or modify benchmark runtime absolute paths.",
        ]
        if attempt_python_enabled:
            lines.extend(
                [
                    "- This attempt has a fresh Python environment; use `$BENCHMARK_ATTEMPT_PYTHON` for scratch scripts.",
                    "- Install needed registry packages with `uv pip install PACKAGE`; installation time is part of the answer budget.",
                    "- Do not create another venv or use pip, requirements/constraints files, editable installs, URLs, local wheels, alternate indexes, or verifier packages.",
                ]
            )
        return "\n".join(lines)

    def _timeout_failure_result(
        self,
        *,
        record: Any,
        group: Any,
        input_bundle: Any,
        payload: dict[str, Any],
        runner_meta: dict[str, Any],
        message: str,
        details: dict[str, Any],
    ) -> RunnerResult:
        runner_meta["error"] = message
        runner_meta["agent_timeout_detected"] = True
        runner_meta["skill_use_audit"] = build_skill_use_audit(
            skills_enabled=bool(getattr(group, "skills_enabled", True)),
            configured_skills=self.configured_skills,
            runner_meta=runner_meta,
            final_response_text="",
        )
        if input_bundle is not None:
            runner_meta["runtime_bundle"] = input_bundle.to_meta()
        return RunnerResult(
            status=RunStatus.FAILED,
            answer=AnswerPayload(),
            raw=payload,
            runner_meta=runner_meta,
            failure=FailureInfo(
                code="agent_response_timeout",
                message=message,
                details=details,
            ),
        )

    def _execution_error_result(
        self,
        *,
        classification: ExecutionErrorClassification,
        record: Any,
        group: Any,
        input_bundle: Any,
        session_id: str,
    ) -> RunnerResult:
        details = classification.to_details()
        runner_meta: dict[str, Any] = {
            "convergence_policy": self.convergence_policy.to_meta(),
            "execution_error": dict(details),
            "error": classification.message,
            "session_id": session_id,
            "timeout_mode": self._timeout_mode(),
        }
        runner_meta["session_isolation"] = self._inspect_failed_attempt_session(session_id)
        runner_meta["skill_use_audit"] = build_skill_use_audit(
            skills_enabled=bool(getattr(group, "skills_enabled", True)),
            configured_skills=self.configured_skills,
            runner_meta=runner_meta,
            final_response_text="",
        )
        if input_bundle is not None:
            runner_meta["runtime_bundle"] = input_bundle.to_meta()
        return RunnerResult(
            status=RunStatus.FAILED,
            answer=AnswerPayload(),
            raw={"execution_error": dict(details)},
            runner_meta=runner_meta,
            failure=FailureInfo(
                code=classification.code,
                message=classification.message,
                details=dict(details),
            ),
        )

    def _inspect_failed_attempt_session(self, session_id: str) -> dict[str, Any]:
        try:
            audit = inspect_postflight_session(
                self.agent_id,
                session_id,
                config_path=self.config_path,
            )
        except (OSError, SessionIsolationError) as exc:
            return {
                "requested_session_id": session_id,
                "agent_id": self.agent_id,
                "postflight_entry_session_file": "",
                "session_isolation_ok": False,
                "postflight_inspection_error": f"{type(exc).__name__}: {exc}",
            }

        if not str(audit.get("postflight_entry_session_file") or "").strip():
            store_path_text = str(audit.get("session_store_path") or "").strip()
            if store_path_text:
                transcript_path = Path(store_path_text).expanduser().parent / f"{session_id}.jsonl"
                if transcript_path.is_file() and not transcript_path.is_symlink():
                    audit["postflight_entry_session_id"] = session_id
                    audit["postflight_entry_session_file"] = str(transcript_path.resolve())
                    audit["transcript_path_recovered"] = True
        return audit

    def _subprocess_timeout_result(
        self,
        *,
        exc: subprocess.TimeoutExpired,
        record: Any,
        group: Any,
        input_bundle: Any,
        session_id: str,
    ) -> RunnerResult:
        stdout = str(getattr(exc, "stdout", None) or getattr(exc, "output", None) or "")
        stderr = str(getattr(exc, "stderr", "") or "")
        classification = ExecutionErrorClassification(
            code="subprocess_timeout_expired",
            message="Single-LLM runner subprocess exceeded its wall-clock timeout.",
            layer="runner_subprocess",
            retryable=True,
            source="exception",
            details={
                "exception_type": type(exc).__name__,
                "exception_message": str(exc),
                "timeout": getattr(exc, "timeout", None),
                "stdout_excerpt": stdout[:1000],
                "stderr_excerpt": stderr[:1000],
                "session_id": session_id,
            },
        )
        return self._execution_error_result(
            classification=classification,
            record=record,
            group=group,
            input_bundle=input_bundle,
            session_id=session_id,
        )

    def _attempt_history_entry(
        self,
        *,
        attempt_number: int,
        session_id: str,
        result: RunnerResult,
        retryable: bool,
        retry_reason: str,
    ) -> dict[str, Any]:
        failure = result.failure
        timeout_exception = result.runner_meta.get("timeout_exception")
        execution_error = result.runner_meta.get("execution_error")
        entry: dict[str, Any] = {
            "attempt": attempt_number,
            "session_id": session_id,
            "status": str(result.status.value),
            "failure_code": str(getattr(failure, "code", "") or ""),
            "retryable": retryable,
            "retry_reason": retry_reason,
        }
        if isinstance(timeout_exception, dict):
            entry["exception_type"] = str(timeout_exception.get("exception_type") or "")
            entry["exception_message"] = str(timeout_exception.get("message") or "")[:1000]
        if isinstance(execution_error, dict):
            entry["execution_error"] = dict(execution_error)
            entry["error_layer"] = str(execution_error.get("layer") or "")
            entry["error_source"] = str(execution_error.get("source") or "")
            if "exception_type" in execution_error:
                entry["exception_type"] = str(execution_error.get("exception_type") or "")
            if "exception_message" in execution_error:
                entry["exception_message"] = str(execution_error.get("exception_message") or "")[:1000]
        isolation = result.runner_meta.get("workspace_isolation")
        if isinstance(isolation, dict):
            entry["workspace_isolation"] = dict(isolation)
        return entry

    def _workspace_failure_result(
        self,
        *,
        error: WorkspaceIsolationError,
        record: Any,
        group: Any,
        input_bundle: Any,
        session_id: str,
        isolation_meta: dict[str, Any],
        original_result: RunnerResult | None = None,
    ) -> RunnerResult:
        runner_meta = dict(original_result.runner_meta if original_result is not None else {})
        runner_meta.update(
            {
                "error": error.message,
                "execution_error": dict(error.details),
                "session_id": session_id,
                "workspace_isolation": isolation_meta,
                "convergence_policy": self.convergence_policy.to_meta(),
                "timeout_mode": self._timeout_mode(),
            }
        )
        runner_meta["skill_use_audit"] = build_skill_use_audit(
            skills_enabled=bool(getattr(group, "skills_enabled", True)),
            configured_skills=self.configured_skills,
            runner_meta=runner_meta,
            final_response_text="",
        )
        if input_bundle is not None:
            runner_meta["runtime_bundle"] = input_bundle.to_meta()
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

    def _unexpected_attempt_failure_result(
        self,
        *,
        exc: Exception,
        record: Any,
        group: Any,
        input_bundle: Any,
        session_id: str,
    ) -> RunnerResult:
        classification = ExecutionErrorClassification(
            code="runner_attempt_exception",
            message=f"Single-LLM attempt failed before producing a terminal runner result: {exc}",
            layer="runner",
            retryable=False,
            source="exception",
            details={
                "exception_type": type(exc).__name__,
                "exception_message": str(exc)[:1000],
                "session_id": session_id,
            },
        )
        return self._execution_error_result(
            classification=classification,
            record=record,
            group=group,
            input_bundle=input_bundle,
            session_id=session_id,
        )

    def _audit_attempt(
        self,
        *,
        lease: AttemptWorkspaceLease,
        result: RunnerResult,
        input_bundle: Any,
        environment: dict[str, str],
        policy: WorkspaceAccessPolicy,
    ) -> WorkspaceAudit:
        if self._contamination_auditor is not None:
            return ensure_workspace_audit(self._contamination_auditor(
                lease=lease,
                runner_meta=result.runner_meta,
                allowed_roots=[scope.path for scope in policy.read_scopes],
                environment=environment,
                policy=policy,
            ))
        mappings = None
        if self.execution_backend == "docker":
            mappings = RuntimePathProjection(lease.active_workspace, runtime_paths.skills_root, input_bundle).audit_mappings()
        return self.workspace_manager.audit_attempt(
            lease,
            result.runner_meta,
            allowed_roots=[scope.path for scope in policy.read_scopes],
            environment=environment,
            policy=policy,
            transcript_path_mappings=mappings,
        )

    def _run_isolated_attempt(
        self,
        *,
        record: Any,
        group: Any,
        input_bundle: Any,
        prompt: str,
        session_id: str,
        attempt_index: int,
        wrapper_path: Path,
        environment: dict[str, str],
    ) -> RunnerResult:
        skills_enabled = bool(getattr(group, "skills_enabled", True))
        is_vgb = str(getattr(record, "eval_kind", "") or "").strip() == "verifier_grounded"
        identity = AttemptIdentity(
            run_id=self.workspace_manager.run_id,
            invocation_id=self.workspace_manager.invocation_id,
            group_id=str(group.id),
            runner_kind="single_llm",
            agent_id=self.agent_id,
            record_id=str(record.record_id),
            attempt_index=attempt_index,
            session_id=session_id,
            template_id="single-llm-skills-on-v1" if skills_enabled else "single-llm-skills-off-v1",
        )
        try:
            lease = self.workspace_manager.prepare(identity)
        except WorkspaceIsolationError as error:
            return self._workspace_failure_result(
                error=error,
                record=record,
                group=group,
                input_bundle=input_bundle,
                session_id=session_id,
                isolation_meta={
                    "schema_version": 3,
                    **identity.sentinel_fields(),
                    "active_workspace": str(
                        self.workspace_manager.active_workspace_path(group_id=identity.group_id, agent_id=identity.agent_id)
                    ),
                    "preflight_ok": False,
                    "archive_ok": False,
                    "audit_execution_status": "unavailable",
                    "boundary_status": "unknown",
                    "contamination_status": "indeterminate",
                    "adjudication": "non_evaluable",
                    "findings": [],
                },
            )

        attempt_environment: AttemptPythonEnvironment | None = None
        result: RunnerResult | None = None
        attempt_env = dict(environment)
        if is_vgb and self.execution_backend == "host":
            try:
                register_environment(lease.scratch_dir, identity.sentinel_fields(), "host")
                attempt_environment = create_attempt_environment(
                    lease.scratch_dir,
                    bootstrap_python=sys.executable,
                    pypi_cutoff=self.pypi_cutoff,
                )
                attempt_env = build_openclaw_subprocess_env(
                    base_env=attempt_env,
                    config_path=self.config_path,
                    attempt_python=attempt_environment.python,
                )
                attempt_env.update(attempt_environment.to_env())
            except Exception as exc:
                cleanup_report = cleanup_owned_environment(lease.scratch_dir)
                result = self._unexpected_attempt_failure_result(
                    exc=exc,
                    record=record,
                    group=group,
                    input_bundle=input_bundle,
                    session_id=session_id,
                )
                result.runner_meta["attempt_environment"] = {"status": "failed", "error": str(exc)}
                result.runner_meta["attempt_environment_cleanup"] = cleanup_report

        attempt_env["BENCHMARK_WORKSPACE_DIR"] = str(lease.active_workspace)
        attempt_env["BENCHMARK_ATTEMPT_INDEX"] = str(attempt_index)
        attempt_env["BENCHMARK_SKILL_SCRATCH_DIR"] = str(lease.scratch_dir)
        attempt_env["BENCHMARK_SKILL_REQUEST_DIR"] = str(lease.request_dir)
        attempt_env["BENCHMARK_SKILL_OUTPUT_DIR"] = str(lease.output_dir)
        attempt_env["BENCHMARK_SKILL_NOTES_DIR"] = str(lease.notes_dir)
        attempt_env["BENCHMARK_PROJECT_ROOT"] = str(Path(__file__).resolve().parents[3])
        attempt_env["BENCHMARK_SKILL_RUNNER"] = str(Path(__file__).resolve().parents[3] / "scripts" / "run_skill.py")
        if result is None and self.execution_backend == "host" and self.config_path.is_file():
            try:
                config = read_evidence(self.config_path)
                policy = self.workspace_manager.policy_for_lease(
                    lease, role="single_llm", skills_enabled=skills_enabled,
                    always_read_scopes=[Path(input_bundle.bundle_dir)] if input_bundle is not None else [],
                    read_scopes=self.allowed_workspace_roots if skills_enabled else ())
                config.setdefault("plugins", {}).setdefault("entries", {}).setdefault(
                    "benchmark-workdir-guard", {}).setdefault("config", {}).setdefault("agentPolicies", {})[self.agent_id] = policy.to_payload()
                write_evidence(self.config_path, config)
            except Exception as exc:
                result = self._unexpected_attempt_failure_result(exc=exc, record=record, group=group,
                                                                 input_bundle=input_bundle, session_id=session_id)
        attempt_prompt = self._attach_scratch_prompt(
            prompt,
            scratch_dir=lease.scratch_dir,
            request_dir=lease.request_dir,
            output_dir=lease.output_dir,
            attempt_python_enabled=attempt_environment is not None or self.execution_backend == "docker",
        )
        scratch_meta = {
            "workspace_dir": str(lease.active_workspace),
            "scratch_dir": str(lease.scratch_dir),
            "request_dir": str(lease.request_dir),
            "output_dir": str(lease.output_dir),
            "notes_dir": str(lease.notes_dir),
            "scratch_contract_version": 2,
            "record_id": lease.identity.record_id,
            "session_id": session_id,
            "group_id": str(group.id),
        }
        if result is None:
            try:
                result = self._run_attempt(
                    record,
                    group,
                    input_bundle=input_bundle,
                    prompt=attempt_prompt,
                    session_id=session_id,
                    wrapper_path=wrapper_path,
                    env=attempt_env,
                )
            except Exception as exc:
                result = self._unexpected_attempt_failure_result(
                    exc=exc,
                    record=record,
                    group=group,
                    input_bundle=input_bundle,
                    session_id=session_id,
                )
        assert result is not None
        if result.runner_meta.get("container_cleanup", {}).get("removed") is False:
            result.runner_meta["workspace_isolation"] = {**lease.to_meta(), "archive_ok": False,
                "recovery_required": True, "reason": "container_execution_state_unconfirmed"}
            self.workspace_manager._release_lease(lease)
            return result
        if self.execution_backend == "docker":
            if result.failure is not None:
                session_root = lease.scratch_dir / "session"
                try:
                    session_audit = inspect_postflight_session(
                        self.agent_id, session_id, config_path=self.config_path,
                        session_store_path=session_root / "agents" / self.agent_id / "sessions/sessions.json",
                    )
                    result.runner_meta["session_isolation"] = self._translate_container_paths(session_audit, session_root=session_root)
                except (OSError, SessionIsolationError):
                    transcript = session_root / "agents" / self.agent_id / "sessions" / f"{session_id}.jsonl"
                    if transcript.is_file() and not transcript.is_symlink():
                        result.runner_meta["session_isolation"]["postflight_entry_session_file"] = str(transcript)
            manifest_path = lease.notes_dir / "dependency-manifest.json"
            result.runner_meta["attempt_environment"] = read_evidence(manifest_path)
            result.runner_meta["dependency_audit"] = result.runner_meta["attempt_environment"].get("dependency_audit", {})
            cleanup_path = lease.notes_dir / "dependency-cleanup.json"
            result.runner_meta["attempt_environment_cleanup"] = read_evidence(cleanup_path)
            result.runner_meta["host_environment_cleanup"] = cleanup_owned_environment(lease.scratch_dir)
        if attempt_environment is not None:
            manifest = {}
            try:
                session_isolation = result.runner_meta.get("session_isolation")
                session_isolation = session_isolation if isinstance(session_isolation, dict) else {}
                manifest = collect_dependency_manifest(
                    attempt_environment,
                    identity=identity.sentinel_fields(),
                    base_env=attempt_env,
                    install_events=dependency_install_events(
                        session_isolation.get("postflight_entry_session_file")
                    ),
                )
                dependency_audit = remediate_forbidden_distributions(
                    attempt_environment,
                    manifest,
                )
                manifest["dependency_audit"] = dependency_audit
                manifest_path = lease.notes_dir / "dependency-manifest.json"
                manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
                result.runner_meta["attempt_environment"] = manifest
                result.runner_meta["dependency_audit"] = dependency_audit
            except Exception as exc:
                manifest.update(status="manifest_failed", error=f"{type(exc).__name__}: {exc}")
                result.runner_meta["attempt_environment"] = manifest
            result.runner_meta["attempt_environment_cleanup"] = cleanup_owned_environment(lease.scratch_dir)
        if self.execution_backend == "docker" or is_vgb:
            manifest = result.runner_meta.get("attempt_environment") or {}
            try:
                validation = validate_dependency_evidence(manifest, identity=identity.sentinel_fields(), scratch=lease.scratch_dir,
                    expected_venv="/benchmark/workspace/scratch/venv" if self.execution_backend == "docker" else str(lease.scratch_dir / "venv"),
                    pypi_cutoff=self.pypi_cutoff)
            except (TypeError, ValueError, AttributeError) as exc:
                validation = {"status": "invalid", "scoreable": False, "errors": [str(exc)]}
            manifest.update(status=validation["status"], validation=validation)
            try:
                write_evidence(lease.notes_dir / "dependency-manifest.json", manifest)
            except OSError as exc:
                validation.update(status="invalid", scoreable=False, persistence_error=str(exc))
            result.runner_meta["attempt_environment"] = manifest
            result.runner_meta["dependency_evidence"] = validation
            if validation["status"] == "partial":
                result.runner_meta["degraded_execution"] = True
            if not validation["scoreable"] and result.failure is None:
                result = replace(result, status=RunStatus.FAILED, recovery=None, failure=FailureInfo(
                    code="dependency_evidence_invalid", message="Attempt dependency evidence is invalid", details=validation))
        result.runner_meta["workspace_scratch"] = scratch_meta
        if skills_enabled:
            result.runner_meta["skill_scratch"] = scratch_meta
        archive_reason = "timeout_retry" if self._timeout_retry_decision(result).retryable else "attempt_terminal"
        bundle_dir = getattr(input_bundle, "bundle_dir", None)
        policy = self.workspace_manager.policy_for_lease(
            lease,
            role="single_llm",
            skills_enabled=skills_enabled,
            always_read_scopes=([Path(bundle_dir)] if bundle_dir is not None else []),
            read_scopes=(self.allowed_workspace_roots if skills_enabled else ()),
        )
        audit = self._audit_attempt(
            lease=lease,
            result=result,
            input_bundle=input_bundle,
            environment=attempt_env,
            policy=policy,
        )
        cleanup = self.workspace_manager.cleanup_boundary_writes(audit)
        isolation_meta = lease.to_meta()
        isolation_meta.update(audit.to_payload())
        isolation_meta.update(
            {"policy_digest": policy.digest, "policy": policy.to_payload(), "cleanup": cleanup}
        )
        if audit.adjudication == "non_evaluable" and not (
            result.failure is not None
            and audit.audit_execution_status == "unavailable"
            and audit.contamination_status == "indeterminate"
        ):
            message = (
                "Benchmark workspace information contamination was detected."
                if audit.contamination_status == "confirmed"
                else "Benchmark workspace audit could not exclude information contamination."
            )
            result = self._workspace_failure_result(
                error=WorkspaceIsolationError(
                    "benchmark_workspace_contamination",
                    message,
                    details={
                        "audit_execution_status": audit.audit_execution_status,
                        "contamination_status": audit.contamination_status,
                        "adjudication": audit.adjudication,
                        "findings": isolation_meta["findings"],
                    },
                ),
                record=record,
                group=group,
                input_bundle=input_bundle,
                session_id=session_id,
                isolation_meta=isolation_meta,
                original_result=result,
            )
        else:
            if audit.adjudication == "scoreable_degraded":
                result.runner_meta["degraded_execution"] = True
            result.runner_meta["workspace_isolation"] = isolation_meta
        try:
            archive = self.workspace_manager.seal(
                lease,
                AttemptOutcome(
                    runner_status=result.status.value,
                    archive_reason=archive_reason,
                    contamination_audit=audit,
                ),
            )
        except WorkspaceIsolationError as error:
            isolation_meta["archive_ok"] = False
            isolation_meta["archive_error"] = dict(error.details)
            return self._workspace_failure_result(
                error=error,
                record=record,
                group=group,
                input_bundle=input_bundle,
                session_id=session_id,
                isolation_meta=isolation_meta,
                original_result=result,
            )
        isolation_meta.update(archive.to_meta())
        if attempt_environment is not None:
            archived_environment_manifest = archive.workspace / "scratch" / "notes" / "dependency-manifest.json"
            if archived_environment_manifest.is_file():
                result.runner_meta["attempt_environment_manifest"] = str(archived_environment_manifest)
        result.runner_meta["workspace_isolation"] = isolation_meta
        return result

    def _timeout_retry_decision(self, result: RunnerResult) -> TimeoutRetryDecision:
        failure = result.failure
        runner_meta = result.runner_meta or {}
        details = getattr(failure, "details", {}) if failure is not None else {}
        execution_error = runner_meta.get("execution_error")
        if isinstance(execution_error, dict):
            if execution_error.get("retryable") is True:
                return TimeoutRetryDecision(True, str(execution_error.get("code") or getattr(failure, "code", "")))
            if execution_error.get("retryable") is False:
                return TimeoutRetryDecision(False, "")
        if isinstance(details, dict):
            if details.get("retryable") is True:
                return TimeoutRetryDecision(True, str(details.get("code") or getattr(failure, "code", "")))
            if details.get("retryable") is False:
                return TimeoutRetryDecision(False, "")
        if getattr(failure, "code", "") == "agent_response_timeout":
            return TimeoutRetryDecision(True, "agent_response_timeout")
        if is_runner_meta_timeout_family(runner_meta):
            return TimeoutRetryDecision(True, "runner_meta_timeout_family")
        message = str(getattr(failure, "message", "") or "")
        if is_timeout_family_text(message):
            return TimeoutRetryDecision(True, "failure_timeout_family")
        return TimeoutRetryDecision(False, "")

    def _attach_timeout_retry_meta(
        self,
        result: RunnerResult,
        *,
        triggered: bool,
        attempts: int,
        retries_used: int,
        exhausted: bool,
        retry_reason: str,
        attempt_history: list[dict[str, Any]],
    ) -> RunnerResult:
        result.runner_meta["timeout_retry"] = {
            "triggered": triggered,
            "max_retries": self.timeout_retries,
            "backoff_seconds": list(self.timeout_retry_backoff_seconds),
            "attempts": attempts,
            "retries_used": retries_used,
            "exhausted": exhausted,
            "retry_reason": retry_reason,
            "attempt_history": attempt_history,
        }
        return result

    def _execute_attempt(self, **kwargs):
        with self.admission_controller.attempt() if self.admission_controller is not None else nullcontext():
            return self._run_isolated_attempt(**kwargs)

    @staged
    def run(self, record: Any, group: Any) -> RunnerResult:
        self._configure_record_skills(record, group)
        input_bundle = self._ensure_runtime_bundle(record, bundle_root=self.runtime_bundle_root)
        prompt = self._build_single_llm_prompt(
            record,
            websearch_enabled=group.websearch,
            skills_enabled=bool(getattr(group, "skills_enabled", True)),
            input_bundle=(RuntimePathProjection(self.workspace_manager.active_workspace_path(group_id=group.id, agent_id=self.agent_id), runtime_paths.skills_root, input_bundle).visible_bundle()
                          if self.execution_backend == "docker" else input_bundle),
            configured_skills=set(self.configured_skills),
            time_budget_seconds=None if self.no_timeout else self.convergence_policy.timeout_seconds,
        )
        initial_session_id = f"benchmark-{group.id}-{self._slugify(record.record_id, limit=40)}-{uuid.uuid4().hex[:8]}"
        wrapper_path = Path(__file__).with_name("openclaw_wrapper.py")
        env = os.environ.copy()
        env["OPENCLAW_CONFIG_PATH"] = str(self.config_path)
        attempt_history: list[dict[str, Any]] = []
        triggered = False
        retry_reason = ""
        total_attempts = self.timeout_retries + 1
        last_result: RunnerResult | None = None
        for attempt_index in range(total_attempts):
            cancellation_token = (
                getattr(self, "_cancellation_token", None)
                if getattr(self, "_cancellation_enabled", False)
                else None
            )
            if cancellation_token is not None:
                cancellation_token.raise_if_cancelled()
            session_id = initial_session_id if attempt_index == 0 else f"{initial_session_id}-retry{attempt_index}"
            result = yield WorkStep(lambda: self._execute_attempt(
                    record=record,
                    group=group,
                    input_bundle=input_bundle,
                    prompt=prompt,
                    session_id=session_id,
                    attempt_index=attempt_index,
                    wrapper_path=wrapper_path,
                    environment=env,
                ))
            last_result = result
            if self.workspace_manager is not None:
                write_evidence(self.workspace_manager.output_root / "attempt-results" / str(group.id) /
                               self._slugify(record.record_id) / f"{session_id}.json", {
                                   "status": result.status.value, "answer": result.answer.__dict__,
                                   "runner_meta": result.runner_meta, "raw": result.raw})
            decision = self._timeout_retry_decision(result)
            can_retry = decision.retryable and attempt_index < self.timeout_retries
            if cancellation_token is not None and cancellation_token.is_cancelled:
                can_retry = False
            if decision.retryable:
                triggered = True
                retry_reason = decision.reason
            attempt_history.append(
                self._attempt_history_entry(
                    attempt_number=attempt_index + 1,
                    session_id=session_id,
                    result=result,
                    retryable=can_retry,
                    retry_reason=decision.reason,
                )
            )
            if not can_retry:
                return self._attach_timeout_retry_meta(
                    result,
                    triggered=triggered,
                    attempts=attempt_index + 1,
                    retries_used=attempt_index,
                    exhausted=decision.retryable and triggered and attempt_index >= self.timeout_retries,
                    retry_reason=retry_reason,
                    attempt_history=attempt_history,
                )
            backoff = self.timeout_retry_backoff_seconds[attempt_index]
            yield RetryDelay(backoff, cancellation_token.wait if cancellation_token is not None else self._sleep)
            if cancellation_token is not None:
                cancellation_token.raise_if_cancelled()
        assert last_result is not None
        return self._attach_timeout_retry_meta(
            last_result,
            triggered=triggered,
            attempts=total_attempts,
            retries_used=max(0, total_attempts - 1),
            exhausted=triggered,
            retry_reason=retry_reason,
            attempt_history=attempt_history,
        )

    def _configure_record_skills(self, record: Any, group: Any) -> None:
        is_vgb = str(getattr(record, "eval_kind", "") or "").strip() == "verifier_grounded"
        skills_enabled = bool(getattr(group, "skills_enabled", True))
        selected = self.vgb_configured_skills if is_vgb and skills_enabled else self.default_configured_skills
        self.configured_skills = selected
        if not self.config_path.is_file():
            return
        payload = json.loads(self.config_path.read_text(encoding="utf-8"))
        entries = ((payload.get("agents") or {}).get("list") or [])
        changed = False
        for entry in entries:
            if isinstance(entry, dict) and str(entry.get("id") or "") == self.agent_id:
                if entry.get("skills") != list(selected):
                    entry["skills"] = list(selected)
                    changed = True
                break
        if not changed:
            return
        temporary = self.config_path.with_name(f".{self.config_path.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(temporary, self.config_path)

    def _run_attempt_in_docker(
        self,
        *,
        record: Any,
        group: Any,
        input_bundle: Any,
        prompt: str,
        session_id: str,
        wrapper_path: Path,
        env: dict[str, str],
    ) -> RunnerResult:
        workspace = Path(str(env.get("BENCHMARK_WORKSPACE_DIR") or self.workspace_manager.active_workspace_path(group_id=str(group.id), agent_id=self.agent_id))).resolve()
        spool = workspace / "scratch" / "outputs" / "container-spool"
        spool.mkdir(parents=True, exist_ok=True)
        config_path = spool / "openclaw.json"
        projection = RuntimePathProjection(workspace, runtime_paths.skills_root, input_bundle)
        policy = build_workspace_access_policy(
            active_workspace=workspace, role="single_llm",
            skills_enabled=bool(getattr(group, "skills_enabled", True)),
            protected_roots=self.workspace_manager.protected_roots,
            always_read_scopes=([Path(input_bundle.bundle_dir)] if input_bundle is not None else []),
            skill_read_scopes=self.allowed_workspace_roots if bool(getattr(group, "skills_enabled", True)) else (),
        )
        materialize_container_config(
            self.config_path,
            config_path,
            agent_id=self.agent_id,
            host_workspace=workspace,
            host_skills_root=runtime_paths.skills_root,
            skills_enabled=bool(getattr(group, "skills_enabled", True)),
            path_projection=projection,
            workspace_policy=policy.to_payload(),
        )
        session_root = workspace / "scratch" / "session"
        (session_root / "agents" / self.agent_id / "agent").mkdir(parents=True, exist_ok=True)
        for name in ("logs", "state"):
            (session_root / name).mkdir(parents=True, exist_ok=True)
        input_dir = Path(str(getattr(input_bundle, "bundle_dir", workspace / "scratch"))).resolve()
        container_command = self._build_command(
            record=record,
            session_id=session_id,
            prompt=prompt,
            wrapper_path=Path("/opt/benchmark/benchmarking/service/single/openclaw_wrapper.py"),
            config_path=Path("/benchmark/config/openclaw.json"),
            python_executable="/opt/benchmark/.venv/bin/python",
        )
        container_command = ["/opt/benchmark/.venv/bin/python", "-m", "benchmarking.runtime.container_attempt", *container_command[1:]]
        container_env = {
            key: value
            for key, value in {**dotenv_values(Path(os.environ.get("OPENCLAW_ENV_FILE", str(runtime_paths.openclaw_home / ".env")))), **env}.items()
            if isinstance(value, str)
            if key in {"LANG", "LC_ALL", "LC_CTYPE", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "NODE_USE_ENV_PROXY", "BENCHMARK_PYPI_CUTOFF"}
            or key.endswith("_API_KEY")
            or key.endswith("_TOKEN")
            or key.endswith("_BASE_URL")
        }
        container_env.update(
            {
                "HOME": "/home/benchmark",
                "OPENCLAW_HOME": "/home/benchmark",
                "OPENCLAW_STATE_DIR": "/benchmark/session",
                "OPENCLAW_CONFIG_PATH": "/benchmark/config/openclaw.json",
                "BENCHMARK_WORKSPACE_DIR": "/benchmark/workspace",
                "BENCHMARK_SKILL_SCRATCH_DIR": "/benchmark/workspace/scratch",
                "BENCHMARK_SKILL_REQUEST_DIR": "/benchmark/workspace/scratch/requests",
                "BENCHMARK_SKILL_OUTPUT_DIR": "/benchmark/workspace/scratch/outputs",
                "BENCHMARK_SKILL_NOTES_DIR": "/benchmark/workspace/scratch/notes",
                "BENCHMARK_PROJECT_ROOT": "/opt/benchmark",
                "BENCHMARK_SKILL_RUNNER": "/opt/benchmark/scripts/run_skill.py",
                "BENCHMARK_ATTEMPT_PYTHON": "/benchmark/workspace/scratch/venv/bin/python",
                "BENCHMARK_PYPI_CUTOFF": self.pypi_cutoff,
                "UV_CACHE_DIR": "/benchmark/workspace/scratch/tmp/cache/uv",
                "BENCHMARK_ATTEMPT_UV_CACHE": "/benchmark/workspace/scratch/tmp/cache/uv",
            }
        )
        mounts = [
            ContainerMount(workspace, PurePosixPath("/benchmark/workspace"), "rw", "workspace"),
            ContainerMount(config_path, PurePosixPath("/benchmark/config/openclaw.json"), "ro", "config"),
            ContainerMount(session_root, PurePosixPath("/benchmark/session"), "rw", "session"),
            ContainerMount(spool, PurePosixPath("/benchmark/result-spool"), "rw", "spool"),
        ]
        if input_bundle is not None:
            mounts.append(ContainerMount(input_dir, PurePosixPath("/benchmark/input"), "ro", "input"))
        if bool(getattr(group, "skills_enabled", True)):
            mounts.append(ContainerMount(runtime_paths.skills_root, PurePosixPath("/opt/benchmark/skills"), "ro", "skills"))
            mounts.append(ContainerMount(runtime_paths.project_root / "scripts" / "run_skill.py", PurePosixPath("/opt/benchmark/scripts/run_skill.py"), "ro", "skill_runner"))
        identity = AttemptIdentity(
            run_id=self.workspace_manager.run_id,
            invocation_id=self.workspace_manager.invocation_id,
            group_id=str(group.id),
            runner_kind="single_llm",
            agent_id=self.agent_id,
            record_id=str(record.record_id),
            attempt_index=int(env.get("BENCHMARK_ATTEMPT_INDEX") or 0),
            session_id=session_id,
            template_id="single-llm-skills-on-v1" if bool(getattr(group, "skills_enabled", True)) else "single-llm-skills-off-v1",
        )
        spec = ContainerAttemptSpec(
            identity=identity,
            image=self.container_image,
            command=tuple(container_command),
            environment=container_env,
            mounts=tuple(mounts),
            cpu_limit=self.container_cpus,
            memory_limit_bytes=self.container_memory_bytes,
            pids_limit=self.container_pids_limit,
            timeout_seconds=self._wrapper_subprocess_timeout_seconds(),
            allowed_source_roots=(workspace, input_dir, config_path, session_root, runtime_paths.skills_root, runtime_paths.project_root / "scripts/run_skill.py"),
        )
        container_env["BENCHMARK_ATTEMPT_IDENTITY"] = json.dumps(identity.sentinel_fields())
        (spool / "container-manifest.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "identity": identity.sentinel_fields(),
                    "path_projection": projection.to_meta(),
                    "image": spec.image,
                    "network_mode": spec.network_mode,
                    "mounts": [
                        {"source": str(mount.source), "target": str(mount.target), "mode": mount.mode, "kind": mount.kind}
                        for mount in spec.mounts
                    ],
                    "resource_limits": {"cpus": spec.cpu_limit, "memory_bytes": spec.memory_limit_bytes, "pids": spec.pids_limit},
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        try:
            handle = self.container_runtime.create(spec)
        except ContainerRuntimeError as exc:
            if exc.code != "container_cleanup_failed":
                raise
            token = self.admission_controller.cancellation_token if self.admission_controller is not None else getattr(self, "_cancellation_token", None)
            if token is not None:
                token.record_cleanup_error({"stage": "container_create", **exc.details})
                token.cancel(CancellationReason(source="container_cleanup", message="Container creation cleanup unconfirmed"))
            result = self._unexpected_attempt_failure_result(exc=exc, record=record, group=group, input_bundle=input_bundle, session_id=session_id)
            result.runner_meta["container_cleanup"] = {"removed": False, **exc.details}
            return result
        result = None
        outcome = None
        cleanup_errors = []
        try:
            self.container_runtime.start(handle)
            outcome = self.container_runtime.collect(handle, timeout_seconds=spec.timeout_seconds, cancellation_token=getattr(self, "_cancellation_token", None))
            (spool / "stdout.log").write_text(outcome.stdout, encoding="utf-8")
            (spool / "stderr.log").write_text(outcome.stderr, encoding="utf-8")
            if outcome.timed_out:
                result = self._subprocess_timeout_result(exc=subprocess.TimeoutExpired(container_command, spec.timeout_seconds, output=outcome.stdout, stderr=outcome.stderr), record=record, group=group, input_bundle=input_bundle, session_id=session_id)
            elif outcome.return_code != 0:
                classification = capture_execution_error(returncode=outcome.return_code or 1, stdout=outcome.stdout, stderr=outcome.stderr, session_id=session_id)
                result = self._execution_error_result(classification=classification, record=record, group=group, input_bundle=input_bundle, session_id=session_id)
            else:
                completed = subprocess.CompletedProcess(container_command, 0, outcome.stdout, outcome.stderr)
                payload = self._translate_container_paths(self._parse_json_stdout(completed, container_command), session_root=session_root)
                result_payload = self._unwrap_agent_payload(payload)
                runner_meta = dict(result_payload.get("meta") or {})
                runner_meta["container"] = {"container_id": handle.container_id, "container_name": handle.container_name, "image_digest": handle.image_digest, "inspect": dict(outcome.inspect), "stats": dict(outcome.stats)}
                result = self._build_container_runner_result(payload=payload, result_payload=result_payload,
                    runner_meta=runner_meta, record=record, group=group, input_bundle=input_bundle, session_id=session_id)
        except Exception as exc:
            result = self._unexpected_attempt_failure_result(exc=exc, record=record, group=group,
                                                             input_bundle=input_bundle, session_id=session_id)
        finally:
            try:
                self.container_runtime.stop(handle, grace_seconds=10)
            except Exception as exc:
                cleanup_errors.append({"stage": "stop", "error": str(exc)})
                try:
                    self.container_runtime.kill(handle)
                except Exception as exc:
                    cleanup_errors.append({"stage": "kill", "error": str(exc)})
            cleanup = self.container_runtime.remove(handle, force=True)
            report = {**cleanup.__dict__, "identity": identity.sentinel_fields(), "operations": cleanup_errors}
            if outcome is not None:
                report.update(outcome.cleanup)
                report.update(cancelled=outcome.cancelled, timed_out=outcome.timed_out)
            token = self.admission_controller.cancellation_token if self.admission_controller is not None else getattr(self, "_cancellation_token", None)
            if token is not None:
                token.record_cleanup_outcome(report)
            try:
                write_evidence(spool / "cleanup.json", report)
            except OSError as exc:
                cleanup_errors.append({"stage": "cleanup_evidence", "error": str(exc)})
            if not cleanup.removed:
                if token is not None:
                    token.record_cleanup_error({"stage": "container_remove", "container_id": handle.container_id,
                                                "identity": identity.sentinel_fields(), "error": cleanup.error})
                    token.cancel(CancellationReason(source="container_cleanup", message="Container removal failed; scheduling stopped"))
        assert result is not None
        result.runner_meta["container_cleanup"] = report
        result.runner_meta["path_projection"] = projection.to_meta()
        if not cleanup.removed and result.failure is None:
            result = replace(result, status=RunStatus.FAILED, recovery=None, failure=FailureInfo(
                "container_cleanup_failed", "Container cleanup failed", report))
        return result

    @staticmethod
    def _translate_container_paths(value: Any, *, session_root: Path) -> Any:
        if isinstance(value, str):
            return value.replace("/benchmark/session", str(session_root))
        if isinstance(value, list):
            return [SingleLLMRunner._translate_container_paths(item, session_root=session_root) for item in value]
        if isinstance(value, dict):
            return {
                key: SingleLLMRunner._translate_container_paths(item, session_root=session_root)
                for key, item in value.items()
            }
        return value

    def _build_container_runner_result(
        self,
        *,
        payload: dict[str, Any],
        result_payload: dict[str, Any],
        runner_meta: dict[str, Any],
        record: Any,
        group: Any,
        input_bundle: Any,
        session_id: str,
    ) -> RunnerResult:
        runner_meta["convergence_policy"] = self.convergence_policy.to_meta()
        runner_meta["timeout_mode"] = self._timeout_mode()
        payloads = list(result_payload.get("payloads") or [])
        full_response_text = self._summarize_payloads(payloads)
        short_answer_text, full_response_text = self._normalize_answer_tracks(full_response_text=full_response_text)
        runner_meta["skill_use_audit"] = build_skill_use_audit(
            skills_enabled=bool(getattr(group, "skills_enabled", True)),
            configured_skills=self.configured_skills,
            runner_meta=runner_meta,
            final_response_text=full_response_text,
        )
        if input_bundle is not None:
            runner_meta["runtime_bundle"] = input_bundle.to_meta()
        diagnostics = runner_meta.get("stdout_diagnostics")
        if isinstance(diagnostics, dict) and diagnostics.get("schema_valid") is False:
            message = "Single-LLM OpenClaw stdout did not contain a schema-valid agent result payload."
            return RunnerResult(status=RunStatus.FAILED, answer=AnswerPayload(), raw=payload, runner_meta={**runner_meta, "error": message}, failure=FailureInfo(code="agent_result_contract_invalid", message=message, details=dict(diagnostics)))
        session_isolation = runner_meta.get("session_isolation")
        if isinstance(session_isolation, dict) and session_isolation.get("session_isolation_ok") is False:
            message = f"Single-LLM OpenClaw session isolation failed for `{session_id}`."
            return RunnerResult(status=RunStatus.FAILED, answer=AnswerPayload(short_answer_text=short_answer_text, full_response_text=full_response_text), raw=payload, runner_meta={**runner_meta, "error": message}, failure=FailureInfo(code="session_isolation_failed", message=message, details=dict(session_isolation)))
        convergence = runner_meta.get("convergence")
        recovered = isinstance(convergence, dict) and bool(convergence.get("transcript_answer_recovered") or convergence.get("finalization_rescue_succeeded"))
        agent_error = classify_agent_error_payload(payloads=payloads, runner_meta=runner_meta, full_response_text=full_response_text, eval_kind=str(getattr(record, "eval_kind", "") or ""), answer_schema=verifier_grounded_answer_schema_from_record(record))
        if agent_error is not None and not recovered:
            return RunnerResult(status=RunStatus.FAILED, answer=AnswerPayload(), raw=payload, runner_meta={**runner_meta, "error": agent_error.message}, failure=FailureInfo(code=agent_error.kind, message=agent_error.message, details=dict(agent_error.details)))
        contract = validate_candidate_answer_contract(record=record, short_answer_text=short_answer_text, full_response_text=full_response_text, runner_meta=runner_meta)
        runner_meta["candidate_answer_contract"] = dict(contract.details)
        if not contract.valid and not recovered:
            return RunnerResult(status=RunStatus.FAILED, answer=AnswerPayload(), raw=payload, runner_meta={**runner_meta, "error": contract.message}, failure=FailureInfo(code=contract.code, message=contract.message, details=dict(contract.details)))
        answer = AnswerPayload(short_answer_text=short_answer_text, full_response_text=full_response_text)
        if recovered:
            runner_meta["degraded_execution"] = True
            return RunnerResult(status=RunStatus.RECOVERED, answer=answer, raw=payload, runner_meta=runner_meta, recovery=RecoveryInfo(source="single-llm-container-transcript", scored=True, evaluable=True, reliability="high_confidence_recovered", recovery_mode="single-llm-container-transcript", details=dict(convergence or {})))
        return RunnerResult(status=RunStatus.COMPLETED, answer=answer, raw=payload, runner_meta=runner_meta)

    def _run_attempt(
        self,
        record: Any,
        group: Any,
        *,
        input_bundle: Any,
        prompt: str,
        session_id: str,
        wrapper_path: Path,
        env: dict[str, str],
    ) -> RunnerResult:
        if self.execution_backend == "docker":
            return self._run_attempt_in_docker(
                record=record,
                group=group,
                input_bundle=input_bundle,
                prompt=prompt,
                session_id=session_id,
                wrapper_path=wrapper_path,
                env=env,
            )
        command = self._build_command(record=record, session_id=session_id, prompt=prompt, wrapper_path=wrapper_path)
        try:
            result = self._run_subprocess(command, env=env, timeout=self._wrapper_subprocess_timeout_seconds())
            if result.returncode != 0:
                classification = capture_execution_error(
                    returncode=result.returncode,
                    stdout=str(result.stdout or ""),
                    stderr=str(result.stderr or ""),
                    session_id=session_id,
                )
                return self._execution_error_result(
                    classification=classification,
                    record=record,
                    group=group,
                    input_bundle=input_bundle,
                    session_id=session_id,
                )
            payload = self._parse_json_stdout(result, command)
        except subprocess.TimeoutExpired as exc:
            return self._subprocess_timeout_result(
                exc=exc,
                record=record,
                group=group,
                input_bundle=input_bundle,
                session_id=session_id,
            )
        result_payload = self._unwrap_agent_payload(payload)
        runner_meta = dict(result_payload.get("meta") or {})
        runner_meta["convergence_policy"] = self.convergence_policy.to_meta()
        runner_meta["timeout_mode"] = self._timeout_mode()
        stdout_diagnostics = runner_meta.get("stdout_diagnostics")
        if isinstance(stdout_diagnostics, dict) and stdout_diagnostics.get("schema_valid") is False:
            message = (
                "Single-LLM OpenClaw stdout did not contain a schema-valid agent result payload: "
                + str(stdout_diagnostics.get("reason") or "invalid_stdout")
            )
            runner_meta["error"] = message
            runner_meta["stdout_diagnostics"] = dict(stdout_diagnostics)
            runner_meta["skill_use_audit"] = build_skill_use_audit(
                skills_enabled=bool(getattr(group, "skills_enabled", True)),
                configured_skills=self.configured_skills,
                runner_meta=runner_meta,
                final_response_text="",
            )
            if input_bundle is not None:
                runner_meta["runtime_bundle"] = input_bundle.to_meta()
            return RunnerResult(
                status=RunStatus.FAILED,
                answer=AnswerPayload(),
                raw=payload,
                runner_meta=runner_meta,
                failure=FailureInfo(
                    code="agent_result_contract_invalid",
                    message=message,
                    details=dict(stdout_diagnostics),
                ),
            )
        payloads = list(result_payload.get("payloads") or [])
        full_response_text = self._summarize_payloads(payloads)
        short_answer_text, full_response_text = self._normalize_answer_tracks(full_response_text=full_response_text)
        runner_meta["skill_use_audit"] = build_skill_use_audit(
            skills_enabled=bool(getattr(group, "skills_enabled", True)),
            configured_skills=self.configured_skills,
            runner_meta=runner_meta,
            final_response_text=full_response_text,
        )
        if input_bundle is not None:
            runner_meta["runtime_bundle"] = input_bundle.to_meta()
        convergence_meta = runner_meta.get("convergence")
        transcript_answer_recovered = (
            isinstance(convergence_meta, dict) and convergence_meta.get("transcript_answer_recovered") is True
        )
        finalization_rescue_recovered = (
            isinstance(convergence_meta, dict) and convergence_meta.get("finalization_rescue_succeeded") is True
        )
        recovery_source = ""
        if isinstance(convergence_meta, dict):
            recovery_source = str(convergence_meta.get("recovery_source") or "")
        session_isolation = runner_meta.get("session_isolation")
        if isinstance(session_isolation, dict) and session_isolation.get("session_isolation_ok") is False:
            actual_session = str(session_isolation.get("postflight_entry_session_id") or "")
            requested_session = str(session_isolation.get("requested_session_id") or session_id)
            message = (
                "Single-LLM OpenClaw session isolation failed: "
                f"requested `{requested_session}` but postflight entry pointed to `{actual_session}`."
            )
            runner_meta["error"] = message
            return RunnerResult(
                status=RunStatus.FAILED,
                answer=AnswerPayload(
                    short_answer_text=short_answer_text,
                    full_response_text=full_response_text,
                ),
                raw=payload,
                runner_meta=runner_meta,
                failure=FailureInfo(
                    code="session_isolation_failed",
                    message=message,
                    details=dict(session_isolation),
                ),
            )
        agent_error = classify_agent_error_payload(
            payloads=payloads,
            runner_meta=runner_meta,
            full_response_text=full_response_text,
            eval_kind=str(getattr(record, "eval_kind", "") or ""),
            answer_schema=verifier_grounded_answer_schema_from_record(record),
        )
        if agent_error is not None and not transcript_answer_recovered and not finalization_rescue_recovered:
            runner_meta["agent_error"] = dict(agent_error.details)
            runner_meta["error"] = agent_error.message
            return RunnerResult(
                status=RunStatus.FAILED,
                answer=AnswerPayload(),
                raw=payload,
                runner_meta=runner_meta,
                failure=FailureInfo(
                    code=agent_error.kind,
                    message=agent_error.message,
                    details=dict(agent_error.details),
                ),
            )
        contract = validate_candidate_answer_contract(
            record=record,
            short_answer_text=short_answer_text,
            full_response_text=full_response_text,
            runner_meta=runner_meta,
        )
        runner_meta["candidate_answer_contract"] = dict(contract.details)
        if not contract.valid and not transcript_answer_recovered:
            assert contract.code
            message = contract.message
            runner_meta["error"] = message
            if contract.code == "agent_response_timeout":
                runner_meta["agent_timeout_detected"] = True
                runner_meta["agent_timeout_payload_text"] = full_response_text
            return RunnerResult(
                status=RunStatus.FAILED,
                answer=AnswerPayload(),
                raw=payload,
                runner_meta=runner_meta,
                failure=FailureInfo(
                    code=contract.code,
                    message=message,
                    details={
                        **dict(contract.details),
                        "aborted": runner_meta.get("aborted"),
                        "livenessState": runner_meta.get("livenessState"),
                        "durationMs": runner_meta.get("durationMs"),
                    },
                ),
            )
        if transcript_answer_recovered or finalization_rescue_recovered:
            assert isinstance(convergence_meta, dict)
            source = recovery_source or "single-llm-session-transcript"
            runner_meta["degraded_execution"] = True
            runner_meta["recovery_mode"] = source
            runner_meta["answer_reliability"] = "high_confidence_recovered"
            return RunnerResult(
                status=RunStatus.RECOVERED,
                answer=AnswerPayload(
                    short_answer_text=short_answer_text,
                    full_response_text=full_response_text,
                ),
                raw=payload,
                runner_meta=runner_meta,
                recovery=RecoveryInfo(
                    source=source,
                    scored=True,
                    evaluable=True,
                    reliability="high_confidence_recovered",
                    recovery_mode=source,
                    details=dict(convergence_meta),
                ),
            )
        return RunnerResult(
            status=RunStatus.COMPLETED,
            answer=AnswerPayload(
                short_answer_text=short_answer_text,
                full_response_text=full_response_text,
            ),
            raw=payload,
            runner_meta=runner_meta,
        )
