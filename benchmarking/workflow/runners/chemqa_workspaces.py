from __future__ import annotations

import json
import os
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

from benchmarking.core.contracts import AnswerPayload, FailureInfo, RunnerResult, RunStatus
from benchmarking.runtime.agent_workspace import (
    AttemptIdentity,
    AttemptOutcome,
    AttemptWorkspaceLease,
    WorkspaceAudit,
    WorkspaceIsolationError,
    WorkspaceLeaseSet,
    ensure_workspace_audit,
)
from benchmarking.runtime.session_isolation import sanitize_agent_id


class ChemQAWorkspaceSupport:
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

