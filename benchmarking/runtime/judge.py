from __future__ import annotations

import hashlib
import os
import threading
import uuid
from pathlib import Path
from typing import Any, Callable

from benchmarking.runtime import paths as runtime_paths
from benchmarking.runtime import subprocess_utils
from benchmarking.runtime.agent_workspace import (
    AttemptIdentity,
    AttemptOutcome,
    AttemptWorkspaceManager,
    WorkspaceIsolationError,
    default_workspace_templates,
)
from benchmarking.runtime.openclaw_env import build_openclaw_subprocess_env
from benchmarking.runtime.session_isolation import (
    SessionIsolationError,
    inspect_postflight_session,
    merge_preflight_postflight_audit,
    reset_agent_main_session_if_stale,
)
from benchmarking.runtime.workspace_policy import (
    ContaminationAudit,
    ProtectedRoot,
    ensure_workspace_audit,
)
from benchmarking.scoring.evaluators import safe_json_extract


DEFAULT_JUDGE_THINKING = "high"


class JudgeError(RuntimeError):
    pass


def _compatibility_protected_roots(*, runtime_root: Path, output_root: Path) -> tuple[ProtectedRoot, ...]:
    return (
        ProtectedRoot("benchmark_runtime_root", runtime_root, "compatibility.runtime_root"),
        ProtectedRoot("current_output_root", output_root, "compatibility.output_root"),
    )


class JudgeClient:
    def __init__(
        self,
        *,
        judge_agent: str,
        timeout_seconds: int,
        config_path: Path,
        thinking: str = DEFAULT_JUDGE_THINKING,
        workspace_manager: AttemptWorkspaceManager | None = None,
        contamination_auditor: Callable[..., Any] | None = None,
    ) -> None:
        self.judge_agent = judge_agent
        self.timeout_seconds = timeout_seconds
        self.config_path = config_path
        self.thinking = thinking
        self._lock = threading.Lock()
        compatibility_manager = workspace_manager is None
        if workspace_manager is None:
            runtime_root = config_path.expanduser().resolve().parent / ".benchmark-test-workspaces" / "runs"
            output_root = config_path.expanduser().resolve().parent / ".benchmark-test-output"
            workspace_manager = AttemptWorkspaceManager(
                runtime_root=runtime_root,
                output_root=output_root,
                run_id="judge-test",
                invocation_id=uuid.uuid4().hex,
                templates=default_workspace_templates(runtime_paths.project_root),
                protected_roots=_compatibility_protected_roots(
                    runtime_root=runtime_root,
                    output_root=output_root,
                ),
            )
        if compatibility_manager and contamination_auditor is None:
            contamination_auditor = lambda **_kwargs: ContaminationAudit(status="clean")
        self.workspace_manager = workspace_manager
        self._contamination_auditor = contamination_auditor
        self.last_workspace_isolation: dict[str, Any] = {}

    def evaluate_json(self, prompt: str) -> dict[str, Any]:
        session_id = f"benchmark-judge-{uuid.uuid4().hex[:12]}"
        identity = AttemptIdentity(
            run_id=self.workspace_manager.run_id,
            invocation_id=self.workspace_manager.invocation_id,
            group_id="benchmark-judge-runtime",
            runner_kind="judge",
            agent_id=self.judge_agent,
            record_id=f"judge-call-{hashlib.sha256(prompt.encode('utf-8')).hexdigest()[:12]}",
            attempt_index=0,
            session_id=session_id,
            template_id="judge-v1",
        )
        command = [
            "openclaw",
            "agent",
            "--local",
            "--agent",
            self.judge_agent,
            "--session-id",
            session_id,
            "--message",
            prompt,
            "--thinking",
            self.thinking,
            "--timeout",
            str(self.timeout_seconds),
            "--json",
        ]
        with self._lock:
            try:
                lease = self.workspace_manager.prepare(identity)
            except WorkspaceIsolationError as exc:
                raise JudgeError(f"Judge workspace isolation failed: {exc.message}") from exc
            env = build_openclaw_subprocess_env(base_env=os.environ.copy(), config_path=self.config_path)
            env.update(
                {
                    "BENCHMARK_WORKSPACE_DIR": str(lease.active_workspace),
                    "BENCHMARK_SKILL_SCRATCH_DIR": str(lease.scratch_dir),
                    "BENCHMARK_SKILL_REQUEST_DIR": str(lease.request_dir),
                    "BENCHMARK_SKILL_OUTPUT_DIR": str(lease.output_dir),
                    "BENCHMARK_SKILL_NOTES_DIR": str(lease.notes_dir),
                    "BENCHMARK_PROJECT_ROOT": str(runtime_paths.project_root),
                    "BENCHMARK_SKILL_RUNNER": str(runtime_paths.project_root / "scripts" / "run_skill.py"),
                }
            )
            outcome_status = "failed"
            contamination_audit = ContaminationAudit(
                status="unavailable",
                findings=({"rule_id": "judge_call_incomplete", "tool_name": "", "command_excerpt": ""},),
            )
            call_error: Exception | None = None
            parsed: dict[str, Any] = {}
            try:
                preflight_audit = reset_agent_main_session_if_stale(
                    self.judge_agent,
                    session_id,
                    config_path=self.config_path,
                )
                result = subprocess_utils.run_subprocess(
                    command,
                    env=env,
                    timeout=self.timeout_seconds + 30,
                )
                postflight_audit = inspect_postflight_session(
                    self.judge_agent,
                    session_id,
                    config_path=self.config_path,
                )
                audit = merge_preflight_postflight_audit(preflight_audit, postflight_audit)
                if audit.get("session_isolation_ok") is not True:
                    requested_session = str(audit.get("requested_session_id") or session_id)
                    actual_session = str(audit.get("postflight_entry_session_id") or "")
                    raise JudgeError(
                        "Judge OpenClaw session isolation failed: "
                        f"requested `{requested_session}` but postflight entry pointed to `{actual_session}`."
                    )
                payload = subprocess_utils.parse_json_stdout(result, command)
                result_payload = subprocess_utils.unwrap_agent_payload(payload)
                reply = subprocess_utils.summarize_payloads(list((result_payload.get("payloads") or [])))
                candidate = safe_json_extract(reply)
                if not isinstance(candidate, dict):
                    raise JudgeError(f"Judge must return a JSON object, got: {reply}")
                parsed = candidate
                policy = self.workspace_manager.policy_for_lease(
                    lease,
                    role="judge",
                    skills_enabled=False,
                )
                if self._contamination_auditor is not None:
                    contamination_audit = ensure_workspace_audit(
                        self._contamination_auditor(
                            lease=lease,
                            runner_meta={"session_isolation": audit},
                            allowed_roots=[],
                            environment=env,
                            policy=policy,
                        )
                    )
                else:
                    contamination_audit = self.workspace_manager.audit_attempt(
                        lease,
                        {"session_isolation": audit},
                        environment=env,
                        policy=policy,
                    )
                if ensure_workspace_audit(contamination_audit).adjudication == "non_evaluable":
                    raise JudgeError(
                        "Judge workspace information contamination was detected or could not be excluded."
                    )
                outcome_status = "completed"
            except SessionIsolationError as exc:
                call_error = JudgeError(f"Judge OpenClaw session isolation failed: {exc}")
            except Exception as exc:
                call_error = exc
            isolation_meta = lease.to_meta()
            normalized_audit = ensure_workspace_audit(contamination_audit)
            isolation_meta.update(normalized_audit.to_payload())
            policy = self.workspace_manager.policy_for_lease(
                lease,
                role="judge",
                skills_enabled=False,
            )
            isolation_meta.update({"policy_digest": policy.digest, "policy": policy.to_payload()})
            try:
                archive = self.workspace_manager.seal(
                    lease,
                    AttemptOutcome(
                        runner_status=outcome_status,
                        archive_reason="attempt_terminal",
                        contamination_audit=contamination_audit,
                    ),
                )
            except WorkspaceIsolationError as exc:
                isolation_meta["archive_ok"] = False
                isolation_meta["archive_error"] = dict(exc.details)
                self.last_workspace_isolation = isolation_meta
                raise JudgeError(f"Judge workspace archive failed: {exc.message}") from exc
            isolation_meta.update(archive.to_meta())
            self.last_workspace_isolation = isolation_meta
            if call_error is not None:
                raise call_error
            return parsed
