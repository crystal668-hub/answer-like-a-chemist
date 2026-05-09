from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path
from typing import Any

from ..contracts import AnswerPayload, FailureInfo, RecoveryInfo, RunnerResult, RunStatus
from ..convergence import ConvergencePolicy
from ..skill_audit import build_skill_use_audit


OPENCLAW_RESPONSE_TIMEOUT_TEXT = "Request timed out before a response was generated"
OPENCLAW_IDLE_TIMEOUT_TEXT = "The model did not produce a response before the LLM idle timeout"


def is_openclaw_timeout_result(*, runner_meta: dict[str, Any], full_response_text: str) -> bool:
    text = str(full_response_text or "")
    has_timeout_text = OPENCLAW_RESPONSE_TIMEOUT_TEXT in text or OPENCLAW_IDLE_TIMEOUT_TEXT in text
    if not has_timeout_text:
        return False
    return runner_meta.get("aborted") is True or str(runner_meta.get("livenessState") or "") == "blocked"


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
        configured_skills: tuple[str, ...] | list[str] = (),
        skill_health_summary: dict[str, Any] | None = None,
        convergence_policy: ConvergencePolicy | None = None,
    ) -> None:
        self.agent_id = agent_id
        self.timeout_seconds = timeout_seconds
        self.convergence_policy = convergence_policy or ConvergencePolicy(timeout_seconds=timeout_seconds)
        self.config_path = config_path
        self.runtime_bundle_root = runtime_bundle_root
        self.configured_skills = tuple(str(skill) for skill in configured_skills)
        self._run_subprocess = run_subprocess
        self._parse_json_stdout = parse_json_stdout
        self._unwrap_agent_payload = unwrap_agent_payload
        self._summarize_payloads = summarize_payloads
        self._normalize_answer_tracks = normalize_answer_tracks
        self._ensure_runtime_bundle = ensure_runtime_bundle
        self._build_single_llm_prompt = build_single_llm_prompt
        self._slugify = slugify
        self._benchmark_agent_thinking = benchmark_agent_thinking
        self.skill_health_summary = dict(skill_health_summary or {})

    def run(self, record: Any, group: Any) -> RunnerResult:
        input_bundle = self._ensure_runtime_bundle(record, bundle_root=self.runtime_bundle_root)
        prompt = self._build_single_llm_prompt(
            record,
            websearch_enabled=group.websearch,
            skills_enabled=bool(getattr(group, "skills_enabled", True)),
            input_bundle=input_bundle,
            available_skills=set(self.configured_skills),
            time_budget_seconds=self.convergence_policy.timeout_seconds,
        )
        session_id = f"benchmark-{group.id}-{self._slugify(record.record_id, limit=40)}-{uuid.uuid4().hex[:8]}"
        wrapper_path = Path(__file__).resolve().parents[1] / "single_llm_openclaw_wrapper.py"
        command = [
            sys.executable,
            str(wrapper_path),
            "--agent",
            self.agent_id,
            "--config-file",
            str(self.config_path),
            "--session-id",
            session_id,
            "--message",
            prompt,
            "--thinking",
            self._benchmark_agent_thinking,
            "--timeout",
            str(self.convergence_policy.timeout_seconds),
            "--finalization-grace-seconds",
            str(self.convergence_policy.finalization_grace_seconds),
            "--json",
        ]
        env = os.environ.copy()
        env["OPENCLAW_CONFIG_PATH"] = str(self.config_path)
        result = self._run_subprocess(command, env=env, timeout=self.convergence_policy.timeout_seconds + 30)
        payload = self._parse_json_stdout(result, command)
        result_payload = self._unwrap_agent_payload(payload)
        runner_meta = dict(result_payload.get("meta") or {})
        runner_meta["convergence_policy"] = self.convergence_policy.to_meta()
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
                skill_health_summary=self.skill_health_summary,
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
        payloads = list((result_payload.get("payloads") or []))
        full_response_text = self._summarize_payloads(payloads)
        short_answer_text, full_response_text = self._normalize_answer_tracks(full_response_text=full_response_text)
        runner_meta["skill_use_audit"] = build_skill_use_audit(
            skills_enabled=bool(getattr(group, "skills_enabled", True)),
            configured_skills=self.configured_skills,
            runner_meta=runner_meta,
            final_response_text=full_response_text,
            skill_health_summary=self.skill_health_summary,
        )
        if input_bundle is not None:
            runner_meta["runtime_bundle"] = input_bundle.to_meta()
        convergence_meta = runner_meta.get("convergence")
        transcript_answer_recovered = (
            isinstance(convergence_meta, dict) and convergence_meta.get("transcript_answer_recovered") is True
        )
        if is_openclaw_timeout_result(runner_meta=runner_meta, full_response_text=full_response_text) and not transcript_answer_recovered:
            message = "Single-LLM OpenClaw agent response timed out before producing a benchmark answer."
            runner_meta["error"] = message
            runner_meta["agent_timeout_detected"] = True
            runner_meta["agent_timeout_payload_text"] = full_response_text
            return RunnerResult(
                status=RunStatus.FAILED,
                answer=AnswerPayload(),
                raw=payload,
                runner_meta=runner_meta,
                failure=FailureInfo(
                    code="agent_response_timeout",
                    message=message,
                    details={
                        "aborted": runner_meta.get("aborted"),
                        "livenessState": runner_meta.get("livenessState"),
                        "durationMs": runner_meta.get("durationMs"),
                        "payload_text": full_response_text,
                    },
                ),
            )
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
        if transcript_answer_recovered:
            assert isinstance(convergence_meta, dict)
            runner_meta["degraded_execution"] = True
            runner_meta["recovery_mode"] = "single-llm-session-transcript"
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
                    source="single-llm-session-transcript",
                    scored=True,
                    evaluable=True,
                    reliability="high_confidence_recovered",
                    recovery_mode="single-llm-session-transcript",
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
