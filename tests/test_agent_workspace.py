import json
import os
import socket
import tempfile
import unittest
from pathlib import Path

from benchmarking.runtime.agent_workspace import (
    SENTINEL_FILENAME,
    AttemptIdentity,
    AttemptOutcome,
    AttemptWorkspaceManager,
    WorkspaceIsolationError,
    WorkspaceTemplate,
    default_workspace_templates,
)
from benchmarking.runtime.workspace_policy import (
    AccessScope,
    ProtectedRoot,
    WorkspaceAccessPolicy,
    WorkspaceAudit,
    adjudicate_workspace_findings,
)


class AttemptWorkspaceManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.template_root = self.root / "templates" / "single"
        self.template_root.mkdir(parents=True)
        (self.template_root / "AGENTS.md").write_text("# clean template\n", encoding="utf-8")
        self.manager = self._manager(invocation_id="invocation-1")
        self.audit_index = 0

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _manager(self, *, invocation_id: str) -> AttemptWorkspaceManager:
        runtime_root = self.root / "runtime" / "runs"
        output_root = self.root / "output"
        return AttemptWorkspaceManager(
            runtime_root=runtime_root,
            output_root=output_root,
            run_id="run-1",
            invocation_id=invocation_id,
            templates={
                "single-v1": WorkspaceTemplate(template_id="single-v1", source_dir=self.template_root),
            },
            protected_roots=(
                ProtectedRoot("benchmark_runtime_root", runtime_root, "test.runtime_root"),
                ProtectedRoot("current_output_root", output_root, "test.output_root"),
                ProtectedRoot("benchmark_dataset_root", self.root / "datasets", "test.datasets"),
                ProtectedRoot("temp_benchmark_dataset_root", self.root / "temp-datasets", "test.temp_datasets"),
                ProtectedRoot("verifier_release_root", self.root / "verifier-releases", "test.verifier_releases"),
                ProtectedRoot("verifier_runtime_root", self.root / "verifier-runtimes", "test.verifier_runtimes"),
                ProtectedRoot("verifier_resource_root", self.root / "verifier-resources", "test.verifier_resources"),
                ProtectedRoot("benchmark_results_root", self.root / "benchmark-results", "test.results"),
                ProtectedRoot("agents_root", self.root / "agents", "test.agents"),
                *(
                    ProtectedRoot("legacy_benchmark_workspace", self.root / "legacy" / name, f"test.legacy.{name}")
                    for name in (
                        "benchmark-single-skills-on",
                        "benchmark-single-skills-off",
                        "benchmark-judge",
                        "custom-single-agent",
                    )
                ),
            ),
        )

    @staticmethod
    def _identity(*, invocation_id: str = "invocation-1", attempt_index: int = 0, session_id: str = "session-1") -> AttemptIdentity:
        return AttemptIdentity(
            run_id="run-1",
            invocation_id=invocation_id,
            group_id="single_llm_skills_on",
            runner_kind="single_llm",
            agent_id="benchmark-agent",
            record_id="record/one",
            attempt_index=attempt_index,
            session_id=session_id,
            template_id="single-v1",
        )

    def _audit_tool_call(
        self,
        *,
        tool_name: str,
        arguments: object,
        allowed_roots: tuple[Path, ...] | list[Path] = (),
        environment: dict[str, str] | None = None,
    ):
        index = self.audit_index
        self.audit_index += 1
        identity = self._identity(attempt_index=index, session_id=f"audit-session-{index}")
        lease = self.manager.prepare(identity)
        resolved_arguments = arguments(lease) if callable(arguments) else arguments
        transcript = self.root / f"audit-transcript-{index}.jsonl"
        transcript.write_text(
            json.dumps(
                {
                    "type": "message",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "toolCall", "name": tool_name, "arguments": resolved_arguments}],
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        audit = self.manager.audit_attempt(
            lease,
            {"session_isolation": {"postflight_entry_session_file": str(transcript)}},
            allowed_roots=allowed_roots,
            environment=environment,
        )
        self.manager.seal(
            lease,
            AttemptOutcome(
                runner_status="completed" if audit.status == "clean" else "failed",
                contamination_audit=audit,
            ),
        )
        return audit

    def _audit_tool_event(
        self,
        *,
        tool_name: str,
        arguments: object,
        result: dict[str, object] | None,
        allowed_roots: tuple[Path, ...] | list[Path] = (),
        environment: dict[str, str] | None = None,
    ) -> WorkspaceAudit:
        index = self.audit_index
        self.audit_index += 1
        identity = self._identity(attempt_index=index, session_id=f"event-session-{index}")
        lease = self.manager.prepare(identity)
        resolved_arguments = arguments(lease) if callable(arguments) else arguments
        call_id = f"call-{index}"
        lines = [
            {
                "type": "message",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "toolCall",
                            "id": call_id,
                            "name": tool_name,
                            "arguments": resolved_arguments,
                        }
                    ],
                },
            }
        ]
        if result is not None:
            lines.append(
                {
                    "type": "message",
                    "message": {
                        "role": "toolResult",
                        "toolCallId": call_id,
                        "toolName": tool_name,
                        "content": [{"type": "text", "text": str(result.get("text") or "")}],
                        "isError": bool(result.get("isError", False)),
                        "details": result.get("details") or {},
                    },
                }
            )
        transcript = self.root / f"event-transcript-{index}.jsonl"
        transcript.write_text(
            "".join(json.dumps(line) + "\n" for line in lines),
            encoding="utf-8",
        )
        audit = self.manager.audit_attempt(
            lease,
            {"session_isolation": {"postflight_entry_session_file": str(transcript)}},
            allowed_roots=allowed_roots,
            environment=environment,
        )
        self.manager.seal(
            lease,
            AttemptOutcome(
                runner_status="completed" if audit.adjudication != "non_evaluable" else "failed",
                contamination_audit=audit,
            ),
        )
        return audit

    def test_prepare_creates_exact_sentinel_and_current_scratch_atomically(self) -> None:
        identity = self._identity()
        lease = self.manager.prepare(identity)

        sentinel = json.loads(lease.sentinel_path.read_text(encoding="utf-8"))
        for key, value in identity.sentinel_fields().items():
            self.assertEqual(value, sentinel[key])
        self.assertEqual(str(lease.active_workspace.resolve()), sentinel["workspace_path"])
        self.assertRegex(sentinel["template_sha256"], r"^[0-9a-f]{64}$")
        self.assertTrue(lease.request_dir.is_dir())
        self.assertTrue(lease.output_dir.is_dir())
        self.assertTrue(lease.notes_dir.is_dir())
        self.assertEqual({"AGENTS.md", SENTINEL_FILENAME, "scratch"}, {path.name for path in lease.active_workspace.iterdir()})
        self.assertEqual(lease.active_workspace / "scratch", lease.scratch_dir)
        self.assertTrue((lease.scratch_dir / "tmp").is_dir())
        self.manager.seal(lease, AttemptOutcome(runner_status="completed"))

    def test_workspace_access_policy_digest_is_order_independent_and_exact_files_stay_exact(self) -> None:
        workspace = (self.root / "policy-workspace").resolve()
        scratch = workspace / "scratch"
        skill_root = (self.root / "skills").resolve()
        wrapper = (self.root / "scripts" / "run_skill.py").resolve()
        scopes = (
            AccessScope("skill-root", skill_root, "directory", "test.skills"),
            AccessScope("skill-wrapper", wrapper, "file", "test.wrapper"),
        )
        first = WorkspaceAccessPolicy(
            schema_version=1,
            role="single_llm",
            skills_enabled=True,
            read_scopes=(AccessScope("workspace", workspace, "directory", "test.workspace"), *scopes),
            write_scopes=(AccessScope("scratch", scratch, "directory", "test.scratch"),),
            exec_workdir_scopes=(AccessScope("workspace", workspace, "directory", "test.workspace"),),
            protected_roots=self.manager.protected_roots,
        )
        second = WorkspaceAccessPolicy(
            schema_version=1,
            role="single_llm",
            skills_enabled=True,
            read_scopes=tuple(reversed(first.read_scopes)),
            write_scopes=first.write_scopes,
            exec_workdir_scopes=first.exec_workdir_scopes,
            protected_roots=tuple(reversed(first.protected_roots)),
        )

        self.assertEqual(first.digest, second.digest)
        self.assertTrue(first.allows("read", wrapper))
        self.assertFalse(first.allows("read", wrapper / "child"))

    def test_skills_off_policy_excludes_skill_root_and_wrapper(self) -> None:
        lease = self.manager.prepare(self._identity())
        skill_root = self.root / "skills"
        wrapper = self.root / "scripts" / "run_skill.py"
        policy = self.manager.policy_for_lease(
            lease,
            role="single_llm",
            skills_enabled=False,
            read_scopes=(skill_root, wrapper),
        )

        self.assertFalse(policy.allows("read", skill_root / "rdkit" / "SKILL.md"))
        self.assertFalse(policy.allows("read", wrapper))
        self.assertTrue(policy.allows("write", lease.scratch_dir / "notes" / "answer.txt"))
        self.manager.seal(lease, AttemptOutcome(runner_status="completed"))

    def test_default_role_templates_share_canonical_base_contract(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        templates = default_workspace_templates(project_root)
        manager = AttemptWorkspaceManager(
            runtime_root=self.root / "template-runtime",
            output_root=self.root / "template-output",
            run_id="template-run",
            invocation_id="template-invocation",
            templates=templates,
            protected_roots=(
                ProtectedRoot("benchmark_runtime_root", self.root / "template-runtime", "test.runtime"),
                ProtectedRoot("current_output_root", self.root / "template-output", "test.output"),
            ),
        )
        contracts: dict[str, str] = {}
        for index, template_id in enumerate(templates):
            identity = AttemptIdentity(
                run_id="template-run",
                invocation_id="template-invocation",
                group_id=f"group-{index}",
                runner_kind="judge" if template_id == "judge-v1" else "single_llm",
                agent_id=f"agent-{index}",
                record_id=f"record-{index}",
                attempt_index=0,
                session_id=f"session-{index}",
                template_id=template_id,
            )
            lease = manager.prepare(identity)
            contracts[template_id] = (lease.active_workspace / "AGENTS.md").read_text(encoding="utf-8")
            manager.seal(lease, AttemptOutcome(runner_status="completed"))

        base_rule = "structured file tools, use only workspace-relative `scratch/...` paths."
        self.assertTrue(all(base_rule in contract for contract in contracts.values()))
        self.assertIn("Skill use is optional", contracts["single-llm-skills-on-v1"])
        self.assertIn("Skills and local skill scripts are unavailable", contracts["single-llm-skills-off-v1"])
        self.assertIn("Return only the requested JSON verdict", contracts["judge-v1"])

    def test_tool_result_outcomes_and_access_modes_are_preserved(self) -> None:
        protected = self.root / "datasets" / "track" / "tasks.jsonl"
        cases = (
            ({"text": "blocked: benchmark_workspace_guard_blocked", "isError": True}, "blocked"),
            ({"text": "ENOENT: no such file", "isError": True}, "failed"),
            ({"text": "contents", "isError": False}, "succeeded"),
            (None, "unknown"),
        )
        for result, expected_outcome in cases:
            with self.subTest(expected_outcome=expected_outcome):
                audit = self._audit_tool_event(
                    tool_name="read",
                    arguments={"path": str(protected)},
                    result=result,
                )
                finding = audit.findings[0]
                self.assertTrue(finding["tool_call_id"])
                self.assertEqual("read", finding["access_mode"])
                self.assertEqual(expected_outcome, finding["operation_outcome"])
                self.assertIn("call_line", finding["evidence"])
                self.assertIn("result_line", finding["evidence"])

    def test_tool_semantics_registry_covers_all_access_modes(self) -> None:
        protected = self.root / "datasets" / "track" / "resource"
        cases = (
            ("list", {"path": str(protected)}, "list"),
            ("search", {"directory": str(protected)}, "search"),
            ("execute_file", {"path": str(protected)}, "execute"),
            ("edit", {"path": str(protected), "old_text": "a", "new_text": "b"}, "mutate"),
            ("unregistered_file_tool", {"path": str(protected)}, "unknown"),
        )
        for tool_name, arguments, expected_mode in cases:
            with self.subTest(tool_name=tool_name):
                audit = self._audit_tool_event(
                    tool_name=tool_name,
                    arguments=arguments,
                    result={"text": "benchmark_workspace_guard_blocked", "isError": True},
                )
                self.assertEqual(expected_mode, audit.findings[0]["access_mode"])
                self.assertEqual("blocked", audit.findings[0]["operation_outcome"])
                self.assertEqual("scoreable_degraded", audit.adjudication)

    def test_failed_protected_read_with_returned_content_is_still_contaminated(self) -> None:
        audit = self._audit_tool_event(
            tool_name="read",
            arguments={"path": str(self.root / "datasets" / "secret.txt")},
            result={"text": "protected answer contents", "isError": True},
        )

        self.assertEqual("failed", audit.findings[0]["operation_outcome"])
        self.assertEqual("confirmed", audit.findings[0]["information_exposure"])
        self.assertEqual("non_evaluable", audit.adjudication)

    def test_outside_workdir_fallback_depends_on_operation_outcome(self) -> None:
        policy = WorkspaceAccessPolicy(
            schema_version=1,
            role="single_llm",
            skills_enabled=False,
            read_scopes=(AccessScope("workspace", self.root / "workspace", "directory", "test"),),
            write_scopes=(AccessScope("scratch", self.root / "workspace" / "scratch", "directory", "test"),),
            exec_workdir_scopes=(AccessScope("workspace", self.root / "workspace", "directory", "test"),),
            protected_roots=self.manager.protected_roots,
        )
        for text, expected_contamination, expected_adjudication in (
            (
                'Warning: workdir "/missing" is unavailable; using "/outside".\n(Command exited with code 1)',
                "clear",
                "scoreable_degraded",
            ),
            (
                'Warning: workdir "/missing" is unavailable; using "/outside".',
                "indeterminate",
                "non_evaluable",
            ),
        ):
            with self.subTest(expected_adjudication=expected_adjudication):
                result_message = {
                    "role": "toolResult",
                    "toolName": "exec",
                    "content": [{"type": "text", "text": text}],
                    "isError": expected_adjudication == "scoreable_degraded",
                }
                from benchmarking.runtime.workspace_audit import _workdir_fallback_finding

                parsed = _workdir_fallback_finding(result_message, line_number=1, policy=policy)
                audit = adjudicate_workspace_findings((parsed,))
                self.assertEqual(expected_contamination, audit.contamination_status)
                self.assertEqual(expected_adjudication, audit.adjudication)

    def test_write_only_boundary_violation_is_scoreable_degraded(self) -> None:
        protected_target = self.root / "output" / "sibling-run" / "generated.py"
        audit = self._audit_tool_event(
            tool_name="write",
            arguments={"path": str(protected_target), "content": "print('current attempt')\n"},
            result={"text": "Wrote file", "isError": False},
        )

        self.assertEqual("violated", audit.boundary_status)
        self.assertEqual("clear", audit.contamination_status)
        self.assertEqual("scoreable_degraded", audit.adjudication)
        self.assertEqual("write", audit.findings[0]["access_mode"])
        self.assertEqual("none", audit.findings[0]["information_exposure"])

    def test_successful_protected_read_is_non_evaluable(self) -> None:
        audit = self._audit_tool_event(
            tool_name="read",
            arguments={"path": str(self.root / "datasets" / "track" / "tasks.jsonl")},
            result={"text": "protected record contents", "isError": False},
        )

        self.assertEqual("violated", audit.boundary_status)
        self.assertEqual("confirmed", audit.contamination_status)
        self.assertEqual("non_evaluable", audit.adjudication)

    def test_allowed_workdir_fallback_is_warning_and_scoreable(self) -> None:
        finding = {
            "rule_id": "workdir_fallback",
            "tool_call_id": "call-1",
            "tool_name": "exec",
            "candidate_source": "tool_result.warning",
            "access_mode": "workdir",
            "operation_outcome": "failed",
            "requested_workdir": "/missing/attempt/output",
            "fallback_workdir": str(self.root / "workspace"),
            "fallback_allowed": True,
            "resource_provenance": "unknown",
            "information_exposure": "none",
            "boundary_effect": "warning",
            "evidence": {},
        }
        audit = adjudicate_workspace_findings((finding,))

        self.assertEqual("warning", audit.boundary_status)
        self.assertEqual("clear", audit.contamination_status)
        self.assertEqual("scoreable", audit.adjudication)

    def test_cleanup_deletes_only_proven_current_attempt_owned_targets(self) -> None:
        owned = self.root / "output" / "owned" / "generated.txt"
        unknown = self.root / "output" / "unknown" / "keep.txt"
        owned.parent.mkdir(parents=True)
        unknown.parent.mkdir(parents=True)
        owned.write_text("owned", encoding="utf-8")
        unknown.write_text("keep", encoding="utf-8")
        base = {
            "rule_id": "protected_path_access",
            "tool_call_id": "call-write",
            "policy_id": "current_output_root",
            "tool_name": "write",
            "candidate_source": "write.path",
            "access_mode": "write",
            "operation_outcome": "succeeded",
            "matched_root": str((self.root / "output").resolve()),
            "information_exposure": "none",
            "boundary_effect": "violated",
            "evidence": {},
        }
        audit = adjudicate_workspace_findings(
            (
                {**base, "resolved_path": str(owned), "resource_provenance": "current_attempt_owned"},
                {**base, "resolved_path": str(unknown), "resource_provenance": "unknown"},
            )
        )

        cleanup = self.manager.cleanup_boundary_writes(audit)

        self.assertEqual(1, cleanup["attempted_count"])
        self.assertEqual(1, cleanup["succeeded_count"])
        self.assertFalse(owned.exists())
        self.assertEqual("keep", unknown.read_text(encoding="utf-8"))

    def test_identity_scope_and_outside_runtime_paths_fail_closed(self) -> None:
        with self.assertRaisesRegex(WorkspaceIsolationError, "scope") as raised:
            self.manager.prepare(self._identity(invocation_id="other"))
        self.assertEqual("workspace_path_unsafe", raised.exception.code)

        with self.assertRaises(WorkspaceIsolationError) as contained:
            self.manager._ensure_contained(self.root / "outside", self.root / "runtime")
        self.assertEqual("workspace_path_unsafe", contained.exception.code)

    def test_symlink_broken_symlink_and_special_file_are_rejected(self) -> None:
        identity = self._identity()
        active = self.manager.active_workspace_path(group_id=identity.group_id, agent_id=identity.agent_id)
        active.parent.mkdir(parents=True)
        active.symlink_to(self.root / "missing")
        with self.assertRaises(WorkspaceIsolationError) as broken:
            self.manager.prepare(identity)
        self.assertEqual("workspace_path_unsafe", broken.exception.code)
        active.unlink()

        lease = self.manager.prepare(identity)
        fifo = lease.active_workspace / "special.fifo"
        os.mkfifo(fifo)
        with self.assertRaises(WorkspaceIsolationError) as special:
            self.manager._validate_runtime_tree(lease.active_workspace)
        self.assertEqual("workspace_path_unsafe", special.exception.code)
        fifo.unlink()
        self.manager.seal(lease, AttemptOutcome(runner_status="failed"))

    def test_template_with_git_or_symlink_is_rejected(self) -> None:
        git_dir = self.template_root / ".git"
        git_dir.mkdir()
        (git_dir / "config").write_text("answer=old\n", encoding="utf-8")
        with self.assertRaises(WorkspaceIsolationError) as git_error:
            self.manager.prepare(self._identity())
        self.assertEqual("workspace_template_invalid", git_error.exception.code)
        for path in git_dir.iterdir():
            path.unlink()
        git_dir.rmdir()

        (self.template_root / "linked").symlink_to(self.template_root / "AGENTS.md")
        with self.assertRaises(WorkspaceIsolationError) as symlink_error:
            self.manager.prepare(self._identity())
        self.assertEqual("workspace_template_invalid", symlink_error.exception.code)

    def test_seal_archives_complete_workspace_and_is_idempotent(self) -> None:
        lease = self.manager.prepare(self._identity())
        (lease.active_workspace / "unknown-root.bin").write_bytes(b"root history")
        (lease.output_dir / "result.xyz").write_text("1\nH\nH 0 0 0\n", encoding="utf-8")

        archive = self.manager.seal(lease, AttemptOutcome(runner_status="completed"))
        second = self.manager.seal(lease, AttemptOutcome(runner_status="completed"))

        self.assertEqual(archive, second)
        self.assertFalse(lease.active_workspace.exists())
        self.assertEqual(b"root history", (archive.workspace / "unknown-root.bin").read_bytes())
        self.assertTrue((archive.workspace / lease.output_dir.relative_to(lease.active_workspace) / "result.xyz").is_file())
        manifest = json.loads(archive.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual("openclaw-benchmark-workspace-archive", manifest["kind"])
        self.assertEqual("completed", manifest["runner_status"])
        self.assertGreaterEqual(manifest["file_count"], 4)

    def test_archive_collision_does_not_overwrite_and_quarantines_managed_workspace(self) -> None:
        identity = self._identity()
        lease = self.manager.prepare(identity)
        archive_path = self.manager._archive_path(identity)
        archive_path.mkdir(parents=True)
        marker = archive_path / "keep.txt"
        marker.write_text("keep", encoding="utf-8")

        with self.assertRaises(WorkspaceIsolationError) as raised:
            self.manager.seal(lease, AttemptOutcome(runner_status="failed"))

        self.assertEqual("workspace_archive_failed", raised.exception.code)
        self.assertEqual("keep", marker.read_text(encoding="utf-8"))
        self.assertFalse(lease.active_workspace.exists())
        self.assertEqual(1, len(list(self.manager.quarantine_root.iterdir())))

    def test_lock_conflict_fails_closed(self) -> None:
        identity = self._identity()
        lease = self.manager.prepare(identity)
        competing_manager = self._manager(invocation_id="invocation-1")

        with self.assertRaises(WorkspaceIsolationError) as raised:
            competing_manager.prepare(identity)

        self.assertEqual("workspace_lock_conflict", raised.exception.code)
        self.manager.seal(lease, AttemptOutcome(runner_status="aborted"))

    def test_damaged_sentinel_is_left_unchanged(self) -> None:
        identity = self._identity()
        active = self.manager.active_workspace_path(group_id=identity.group_id, agent_id=identity.agent_id)
        active.mkdir(parents=True)
        sentinel = active / SENTINEL_FILENAME
        sentinel.write_text("not-json\n", encoding="utf-8")

        with self.assertRaises(WorkspaceIsolationError) as raised:
            self.manager.prepare(identity)

        self.assertEqual("workspace_sentinel_invalid", raised.exception.code)
        self.assertTrue(active.is_dir())
        self.assertEqual("not-json\n", sentinel.read_text(encoding="utf-8"))

    def test_valid_conflicting_attempt_is_quarantined_and_current_attempt_fails(self) -> None:
        first_identity = self._identity(session_id="old-session")
        first = self.manager.prepare(first_identity)
        self.manager._release_lease(first)

        with self.assertRaises(WorkspaceIsolationError) as raised:
            self.manager.prepare(self._identity(session_id="new-session"))

        self.assertEqual("workspace_sentinel_invalid", raised.exception.code)
        self.assertFalse(first.active_workspace.exists())
        self.assertEqual(1, len(list(self.manager.quarantine_root.iterdir())))

    def test_new_attempt_rebuilds_clean_workspace_after_archive(self) -> None:
        first = self.manager.prepare(self._identity())
        (first.active_workspace / "old-answer.xyz").write_text("old", encoding="utf-8")
        first_archive = self.manager.seal(first, AttemptOutcome(runner_status="failed", archive_reason="timeout_retry"))

        second_identity = self._identity(attempt_index=1, session_id="session-2")
        second = self.manager.prepare(second_identity)

        self.assertFalse((second.active_workspace / "old-answer.xyz").exists())
        self.assertFalse((second.scratch_dir.parent / "session-1").exists())
        self.assertTrue((first_archive.workspace / "old-answer.xyz").is_file())
        self.manager.seal(second, AttemptOutcome(runner_status="completed"))

    def test_recover_incomplete_uses_previous_invocation_identity(self) -> None:
        old_manager = self._manager(invocation_id="old-invocation")
        old_identity = self._identity(invocation_id="old-invocation")
        old_lease = old_manager.prepare(old_identity)
        old_manager._release_lease(old_lease)

        current = self._manager(invocation_id="new-invocation")
        archives = current.recover_incomplete("old-invocation")

        self.assertEqual(1, len(archives))
        manifest = json.loads(archives[0].manifest_path.read_text(encoding="utf-8"))
        self.assertEqual("old-invocation", manifest["invocation_id"])
        self.assertEqual("shutdown_recovery", manifest["archive_reason"])

    def test_recovery_leaves_unknown_directory_unchanged(self) -> None:
        old_manager = self._manager(invocation_id="old-invocation")
        unknown = old_manager.active_workspace_path(group_id="group", agent_id="agent")
        unknown.mkdir(parents=True)
        (unknown / "answer.txt").write_text("unknown", encoding="utf-8")

        with self.assertRaises(WorkspaceIsolationError) as raised:
            self.manager.recover_incomplete("old-invocation")

        self.assertEqual("workspace_recovery_failed", raised.exception.code)
        self.assertTrue(unknown.is_dir())

    def test_socket_is_rejected_by_runtime_tree_audit(self) -> None:
        lease = self.manager.prepare(self._identity())
        socket_path = lease.active_workspace / "agent.sock"
        short_socket_path = Path(tempfile.gettempdir()) / f"bawi-{os.getpid()}-{id(self)}.sock"
        short_socket_path.unlink(missing_ok=True)
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            server.bind(str(short_socket_path))
            os.replace(short_socket_path, socket_path)
            with self.assertRaises(WorkspaceIsolationError) as raised:
                self.manager._validate_runtime_tree(lease.active_workspace)
            self.assertEqual("workspace_path_unsafe", raised.exception.code)
        finally:
            server.close()
            short_socket_path.unlink(missing_ok=True)
            socket_path.unlink(missing_ok=True)
            self.manager.seal(lease, AttemptOutcome(runner_status="failed"))

    def test_forbidden_path_audit_detects_absolute_relative_and_shell_expansion(self) -> None:
        for index, command in enumerate(
            (
                str(self.manager.archive_root / "old" / "answer.xyz"),
                "../../../../benchmark-single-skills-on/final_answer.xyz",
                "$HOME/benchmark/workspaces/benchmark-judge/verdict.json",
            )
        ):
            with self.subTest(command=command):
                identity = self._identity(attempt_index=index, session_id=f"session-{index}")
                lease = self.manager.prepare(identity)
                transcript = self.root / f"transcript-{index}.jsonl"
                transcript.write_text(
                    json.dumps(
                        {
                            "type": "message",
                            "message": {
                                "role": "assistant",
                                "content": [
                                    {
                                        "type": "toolCall",
                                        "name": "exec",
                                        "arguments": {"command": f"cat {command}"},
                                    }
                                ],
                            },
                        }
                    )
                    + "\n",
                    encoding="utf-8",
                )
                if index == 2:
                    environment = {"HOME": str(self.root / "managed")}
                    manager = AttemptWorkspaceManager(
                        runtime_root=Path(environment["HOME"]) / "benchmark" / "workspaces" / "runs",
                        output_root=self.root / "other-output",
                        run_id="run-1",
                        invocation_id="invocation-1",
                        templates=self.manager.templates,
                        protected_roots=(
                            ProtectedRoot(
                                "legacy_benchmark_workspace",
                                Path(environment["HOME"]) / "benchmark" / "workspaces" / "benchmark-judge",
                                "test.legacy",
                            ),
                        ),
                    )
                    manager.runtime_root.parent.mkdir(parents=True, exist_ok=True)
                    audit = manager.audit_attempt(
                        lease,
                        {"session_isolation": {"postflight_entry_session_file": str(transcript)}},
                        environment=environment,
                    )
                else:
                    audit = self.manager.audit_attempt(
                        lease,
                        {"session_isolation": {"postflight_entry_session_file": str(transcript)}},
                        environment={"HOME": str(self.root)},
                    )
                self.assertEqual("contaminated", audit.status)
                self.assertEqual(1, len(audit.findings))
                self.manager.seal(
                    lease,
                    AttemptOutcome(runner_status="failed", contamination_audit=audit),
                )

    def test_forbidden_path_audit_allows_verifier_grounded_run_directory(self) -> None:
        run_id = "verifier-grounded-rdkit-qwen3.7-max-20260716-010815"
        manager = AttemptWorkspaceManager(
            runtime_root=self.root / "runtime" / "runs",
            output_root=self.root / "output",
            run_id=run_id,
            invocation_id="invocation-1",
            templates=self.manager.templates,
            protected_roots=(
                ProtectedRoot("benchmark_runtime_root", self.root / "runtime" / "runs", "test.runtime_root"),
                ProtectedRoot("current_output_root", self.root / "output", "test.output_root"),
            ),
        )
        identity = AttemptIdentity(
            run_id=run_id,
            invocation_id="invocation-1",
            group_id="single_llm_skills_on",
            runner_kind="single_llm",
            agent_id="benchmark-agent",
            record_id="record/one",
            attempt_index=0,
            session_id="session-verifier-grounded",
            template_id="single-v1",
        )
        lease = manager.prepare(identity)
        transcript = self.root / "verifier-grounded-transcript.jsonl"
        transcript.write_text(
            json.dumps(
                {
                    "type": "message",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "toolCall",
                                "name": "exec",
                                "arguments": {
                                    "command": (
                                        f'cd "{lease.scratch_dir}" && '
                                        "mkdir -p requests outputs && python -c 'print(1)'"
                                    )
                                },
                            }
                        ],
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )

        audit = manager.audit_attempt(
            lease,
            {"session_isolation": {"postflight_entry_session_file": str(transcript)}},
            environment={"BENCHMARK_SKILL_SCRATCH_DIR": str(lease.scratch_dir)},
        )

        self.assertEqual("clean", audit.status)
        manager.seal(lease, AttemptOutcome(runner_status="completed", contamination_audit=audit))

    def test_forbidden_path_audit_does_not_assign_policy_by_resource_name(self) -> None:
        commands = (
            "cat /tmp/formal-benchmarks/verifier_grounded_rdkit/data/tasks.jsonl",
            "cat /tmp/verifier-grounded-benchmark/tasks/task.yaml",
            "cat /tmp/data/verifier-grounded-releases/0.1.1/manifest.json",
            "cat /tmp/sample_answers.jsonl",
        )
        for index, command in enumerate(commands):
            with self.subTest(command=command):
                identity = self._identity(attempt_index=index, session_id=f"verifier-session-{index}")
                lease = self.manager.prepare(identity)
                transcript = self.root / f"verifier-resource-transcript-{index}.jsonl"
                transcript.write_text(
                    json.dumps(
                        {
                            "type": "message",
                            "message": {
                                "role": "assistant",
                                "content": [
                                    {
                                        "type": "toolCall",
                                        "name": "exec",
                                        "arguments": {"command": command},
                                    }
                                ],
                            },
                        }
                    )
                    + "\n",
                    encoding="utf-8",
                )

                audit = self.manager.audit_attempt(
                    lease,
                    {"session_isolation": {"postflight_entry_session_file": str(transcript)}},
                )

                self.assertEqual("clean", audit.status)
                self.assertEqual((), audit.findings)
                self.manager.seal(
                    lease,
                    AttemptOutcome(runner_status="completed", contamination_audit=audit),
                )

    def test_forbidden_path_audit_reports_configured_root_evidence(self) -> None:
        cases = (
            ("benchmark_dataset_root", self.root / "datasets" / "track" / "tasks.jsonl"),
            ("temp_benchmark_dataset_root", self.root / "temp-datasets" / "old.jsonl"),
            ("verifier_release_root", self.root / "verifier-releases" / "wheel.whl"),
            ("verifier_runtime_root", self.root / "verifier-runtimes" / "manifest.json"),
            ("verifier_resource_root", self.root / "verifier-resources" / "release.json"),
            ("benchmark_results_root", self.root / "benchmark-results" / "other-run" / "results.json"),
            ("agents_root", self.root / "agents" / "agent" / "sessions.json"),
        )
        for expected_policy, path in cases:
            with self.subTest(policy=expected_policy):
                audit = self._audit_tool_call(
                    tool_name="exec",
                    arguments={"command": f"cat {path}"},
                )
                self.assertEqual("contaminated", audit.status)
                finding = audit.findings[0]
                self.assertEqual("protected_path_access", finding["rule_id"])
                self.assertEqual(expected_policy, finding["policy_id"])
                self.assertEqual(str(path.resolve(strict=False)), finding["resolved_path"])
                matched_root = next(
                    root.path for root in self.manager.protected_roots if root.policy_id == expected_policy
                )
                self.assertEqual(str(matched_root), finding["matched_root"])
                Path(finding["resolved_path"]).relative_to(Path(finding["matched_root"]))

    def test_forbidden_path_audit_supports_relative_env_home_and_flag_paths(self) -> None:
        dataset_file = self.root / "datasets" / "track" / "tasks.jsonl"
        commands_and_env = (
            (lambda lease: f"cat {os.path.relpath(dataset_file, lease.active_workspace)}", {}),
            (lambda _lease: "cat $DATASET_ROOT/track/tasks.jsonl", {"DATASET_ROOT": str(self.root / "datasets")}),
            (lambda _lease: "cat ${DATASET_ROOT}/track/tasks.jsonl", {"DATASET_ROOT": str(self.root / "datasets")}),
            (lambda _lease: "cat ~/track/tasks.jsonl", {"HOME": str(self.root / "datasets")}),
            (lambda _lease: f"tool --input={dataset_file}", {}),
        )
        for command_factory, environment in commands_and_env:
            with self.subTest(environment=environment):
                audit = self._audit_tool_call(
                    tool_name="exec",
                    arguments=lambda lease: {"command": command_factory(lease)},
                    environment=environment,
                )
                self.assertEqual("contaminated", audit.status)
                self.assertEqual("benchmark_dataset_root", audit.findings[0]["policy_id"])

        workdir = self._audit_tool_call(
            tool_name="exec",
            arguments={"command": "pwd", "workdir": str(self.root / "datasets" / "track")},
        )
        self.assertEqual("contaminated", workdir.status)
        self.assertEqual("exec.workdir", workdir.findings[0]["candidate_source"])

    def test_all_explicit_legacy_workspaces_are_protected(self) -> None:
        for name in (
            "benchmark-single-skills-on",
            "benchmark-single-skills-off",
            "benchmark-judge",
            "custom-single-agent",
        ):
            with self.subTest(name=name):
                audit = self._audit_tool_call(
                    tool_name="read",
                    arguments={"path": str(self.root / "legacy" / name / "history.json")},
                )
                self.assertEqual("contaminated", audit.status)
                self.assertEqual("legacy_benchmark_workspace", audit.findings[0]["policy_id"])

    def test_environment_expansion_uses_exact_identifiers_and_ignores_unknown_dynamic_values(self) -> None:
        exact = self._audit_tool_call(
            tool_name="exec",
            arguments={"command": "cat $HOME2/track/tasks.jsonl"},
            environment={"HOME": str(self.root / "safe"), "HOME2": str(self.root / "datasets")},
        )
        self.assertEqual("contaminated", exact.status)
        self.assertEqual("benchmark_dataset_root", exact.findings[0]["policy_id"])

        for command in (
            "cat $UNKNOWN/track/tasks.jsonl",
            f"cat ${{UNKNOWN:-{self.root / 'datasets'}}}/tasks.jsonl",
            f"cat $(printf {self.root / 'datasets'})/tasks.jsonl",
            f"cat `printf {self.root / 'datasets'}`/tasks.jsonl",
        ):
            with self.subTest(command=command):
                audit = self._audit_tool_call(tool_name="exec", arguments={"command": command})
                self.assertEqual("clean", audit.status)

    def test_existing_symlink_alias_resolves_into_protected_root(self) -> None:
        dataset_root = self.root / "datasets"
        dataset_root.mkdir()
        alias = self.root / "dataset-alias"
        alias.symlink_to(dataset_root, target_is_directory=True)

        audit = self._audit_tool_call(
            tool_name="read",
            arguments={"path": str(alias / "track" / "tasks.jsonl")},
        )

        self.assertEqual("contaminated", audit.status)
        self.assertEqual("benchmark_dataset_root", audit.findings[0]["policy_id"])
        self.assertEqual(
            str((dataset_root / "track" / "tasks.jsonl").resolve(strict=False)),
            audit.findings[0]["resolved_path"],
        )

    def test_allowed_child_scope_wins_over_protected_parent_without_allowing_siblings(self) -> None:
        public_bundle = self.manager.output_root / "input-bundles" / "record-1"
        allowed = self._audit_tool_call(
            tool_name="read",
            arguments={"path": str(public_bundle / "record.json")},
            allowed_roots=[public_bundle],
        )
        sibling = self._audit_tool_call(
            tool_name="read",
            arguments={"path": str(public_bundle.parent / "record-2" / "record.json")},
            allowed_roots=[public_bundle],
        )

        self.assertEqual("clean", allowed.status)
        self.assertEqual("contaminated", sibling.status)
        self.assertEqual("current_output_root", sibling.findings[0]["policy_id"])

    def test_structured_payload_text_is_not_a_path_source(self) -> None:
        audit = self._audit_tool_call(
            tool_name="edit",
            arguments={
                "path": "notes.txt",
                "new_text": f"refer to {self.root / 'datasets' / 'track' / 'tasks.jsonl'}",
            },
        )

        self.assertEqual("clean", audit.status)

    def test_unconfigured_named_paths_and_exact_output_sibling_are_clean(self) -> None:
        for path in (
            "/tmp/formal-benchmarks/track/tasks.jsonl",
            "/tmp/verifier-grounded-benchmark/tasks/task.yaml",
            "/tmp/sample_answers.jsonl",
            self.manager.output_root.parent / "unrelated" / "results.json",
        ):
            with self.subTest(path=path):
                audit = self._audit_tool_call(
                    tool_name="exec",
                    arguments={"command": f"cat {path}"},
                )
                self.assertEqual("clean", audit.status)

    def test_most_specific_protected_root_is_independent_of_policy_order(self) -> None:
        parent = self.root / "specific-results"
        child = parent / "run-a"
        policies = (
            ProtectedRoot("benchmark_results_root", parent, "test.parent"),
            ProtectedRoot("current_output_root", child, "test.child"),
            ProtectedRoot("z_same_depth_policy", child, "test.tie"),
        )
        observed = []
        for index, ordered in enumerate((policies, tuple(reversed(policies)))):
            manager = AttemptWorkspaceManager(
                runtime_root=self.root / f"specific-runtime-{index}",
                output_root=child,
                run_id="specific-run",
                invocation_id=f"specific-invocation-{index}",
                templates=self.manager.templates,
                protected_roots=ordered,
            )
            identity = AttemptIdentity(
                run_id="specific-run",
                invocation_id=f"specific-invocation-{index}",
                group_id="group",
                runner_kind="single_llm",
                agent_id="agent",
                record_id="record",
                attempt_index=0,
                session_id=f"specific-session-{index}",
                template_id="single-v1",
            )
            lease = manager.prepare(identity)
            transcript = self.root / f"specific-{index}.jsonl"
            transcript.write_text(
                json.dumps(
                    {
                        "message": {
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "toolCall",
                                    "name": "read",
                                    "arguments": {"path": str(child / "secret.json")},
                                }
                            ],
                        }
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            audit = manager.audit_attempt(
                lease,
                {"session_isolation": {"postflight_entry_session_file": str(transcript)}},
            )
            observed.append(audit.findings[0]["policy_id"])
            manager.seal(lease, AttemptOutcome(runner_status="failed", contamination_audit=audit))

        self.assertEqual(["current_output_root", "current_output_root"], observed)

    def test_invalid_protected_root_policy_fails_before_workspace_prepare(self) -> None:
        invalid_file = self.root / "not-a-directory"
        invalid_file.write_text("x", encoding="utf-8")
        for root in (
            ProtectedRoot("", self.root / "somewhere", "test"),
            ProtectedRoot("root", Path("/"), "test"),
            ProtectedRoot("file", invalid_file, "test"),
        ):
            with self.subTest(root=root):
                with self.assertRaises(WorkspaceIsolationError) as raised:
                    AttemptWorkspaceManager(
                        runtime_root=self.root / "invalid-runtime",
                        output_root=self.root / "invalid-output",
                        run_id="run",
                        invocation_id="invocation",
                        templates=self.manager.templates,
                        protected_roots=(root,),
                    )
                self.assertEqual("workspace_policy_invalid", raised.exception.code)

    def test_protected_root_manifest_is_normalized_deduplicated_and_stable(self) -> None:
        first = self.root / "policy-a"
        second = self.root / "policy-b"
        manager = AttemptWorkspaceManager(
            runtime_root=self.root / "policy-runtime",
            output_root=self.root / "policy-output",
            run_id="run",
            invocation_id="invocation",
            templates=self.manager.templates,
            protected_roots=(
                ProtectedRoot("shared", second, "source-b"),
                ProtectedRoot("shared", first, "source-z"),
                ProtectedRoot("shared", first, "source-a"),
            ),
        )

        manifest = manager.forbidden_path_policy_manifest()
        self.assertEqual(2, len(manifest["protected_roots"]))
        self.assertEqual(
            [str(first.resolve(strict=False)), str(second.resolve(strict=False))],
            [item["path"] for item in manifest["protected_roots"]],
        )
        self.assertEqual("source-a", manifest["protected_roots"][0]["source"])

    def test_rdkit_false_positive_trajectory_replays_clean_and_real_dataset_root_is_blocked(self) -> None:
        lease = self.manager.prepare(
            self._identity(attempt_index=900, session_id="rdkit-trajectory-replay")
        )
        fixture_path = Path(__file__).parent / "fixtures" / "rdkit_forbidden_path_false_positive.json"
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))

        def write_transcript(path: Path, replacement: Path) -> None:
            content = []
            for call in fixture:
                arguments = json.loads(
                    json.dumps(call["arguments"]).replace("{scratch_dir}", str(replacement))
                )
                content.append(
                    {
                        "type": "toolCall",
                        "name": call["tool_name"],
                        "arguments": arguments,
                    }
                )
            path.write_text(
                json.dumps({"message": {"role": "assistant", "content": content}}) + "\n",
                encoding="utf-8",
            )

        clean_transcript = self.root / "rdkit-clean-trajectory.jsonl"
        write_transcript(clean_transcript, lease.scratch_dir)
        environment = {"BENCHMARK_SKILL_SCRATCH_DIR": str(lease.scratch_dir)}
        clean = self.manager.audit_attempt(
            lease,
            {"session_isolation": {"postflight_entry_session_file": str(clean_transcript)}},
            environment=environment,
        )

        protected_transcript = self.root / "rdkit-protected-trajectory.jsonl"
        write_transcript(protected_transcript, self.root / "datasets")
        contaminated = self.manager.audit_attempt(
            lease,
            {"session_isolation": {"postflight_entry_session_file": str(protected_transcript)}},
            environment=environment,
        )

        self.assertEqual("clean", clean.status)
        self.assertEqual("violated", contaminated.boundary_status)
        self.assertEqual("clear", contaminated.contamination_status)
        self.assertEqual("scoreable_degraded", contaminated.adjudication)
        finding = contaminated.findings[0]
        self.assertEqual("protected_path_access", finding["rule_id"])
        self.assertEqual("benchmark_dataset_root", finding["policy_id"])
        self.assertIn("resolved_path", finding)
        self.assertIn("matched_root", finding)
        self.assertEqual({"protected_path_access"}, {item["rule_id"] for item in contaminated.findings})
        self.manager.seal(
            lease,
            AttemptOutcome(runner_status="failed", contamination_audit=contaminated),
        )

    def test_malformed_exec_command_makes_audit_unavailable_without_false_path_finding(self) -> None:
        audit = self._audit_tool_call(
            tool_name="exec",
            arguments={"command": "cat '/unterminated"},
        )

        self.assertEqual("unavailable", audit.status)
        finding = audit.findings[0]
        self.assertEqual("transcript_audit_failed", finding["rule_id"])
        self.assertEqual("exec", finding["tool_name"])
        self.assertEqual(1, finding["transcript_line"])
        self.assertEqual("No closing quotation", finding["exception_message"])
        self.assertIn("/unterminated", finding["command_excerpt"])
        self.assertNotIn("resolved_path", finding)

    def test_heredoc_bodies_with_quotes_and_multiple_delimiters_audit_clean(self) -> None:
        audit = self._audit_tool_call(
            tool_name="exec",
            arguments=lambda lease: {
                "command": (
                    f'cd "{lease.scratch_dir}" && python3 << \'PYEOF\'\n'
                    "def calculate():\n"
                    '    \"\"\"Docstring with \"quotes\" and shell-like << text.\"\"\"\n'
                    '    open("outputs/result.json", "w")\n'
                    "PYEOF\n"
                    "cat > notes/first.txt <<FIRST <<-SECOND\n"
                    "'quoted body'\n"
                    "FIRST\n"
                    "\t\"second body\"\n"
                    "\tSECOND\n"
                )
            },
        )

        self.assertEqual("clean", audit.status)

    def test_here_string_is_not_misclassified_as_heredoc(self) -> None:
        audit = self._audit_tool_call(
            tool_name="exec",
            arguments={"command": "read value <<< \"quoted text\" && echo \"$value\""},
        )

        self.assertEqual("clean", audit.status)

    def test_heredoc_body_still_reports_determinate_protected_path(self) -> None:
        protected = self.root / "datasets" / "track" / "tasks.jsonl"
        audit = self._audit_tool_call(
            tool_name="exec",
            arguments=lambda lease: {
                "command": (
                    f'cd "{lease.scratch_dir}" && python3 <<\'PYEOF\'\n'
                    "from pathlib import Path\n"
                    f'Path("{protected}").read_text()\n'
                    "PYEOF\n"
                )
            },
        )

        self.assertEqual("contaminated", audit.status)
        finding = audit.findings[0]
        self.assertEqual("benchmark_dataset_root", finding["policy_id"])
        self.assertEqual("exec.heredoc", finding["candidate_source"])
        self.assertEqual(str(protected.resolve(strict=False)), finding["resolved_path"])

    def test_cd_chain_resolves_relative_paths_from_effective_directory(self) -> None:
        clean = self._audit_tool_call(
            tool_name="exec",
            arguments=lambda lease: {
                "command": (
                    f'cd "{lease.scratch_dir}" && mkdir -p outputs/cand1 && '
                    "cd outputs/cand1 && xtb ../../requests/candidate.xyz"
                )
            },
        )
        protected = self.root / "datasets" / "track" / "tasks.jsonl"
        contaminated = self._audit_tool_call(
            tool_name="exec",
            arguments=lambda lease: {
                "command": (
                    f'cd "{lease.scratch_dir}" && mkdir -p outputs/cand1 && '
                    f'cd outputs/cand1 && cat "{os.path.relpath(protected, lease.scratch_dir / "outputs" / "cand1")}"'
                )
            },
        )

        self.assertEqual("clean", clean.status)
        self.assertEqual("contaminated", contaminated.status)
        finding = contaminated.findings[0]
        self.assertEqual("benchmark_dataset_root", finding["policy_id"])
        self.assertEqual(str(protected.resolve(strict=False)), finding["resolved_path"])

    def test_forbidden_path_audit_allows_current_workspace_and_skill_root(self) -> None:
        lease = self.manager.prepare(self._identity())
        skill_root = self.root / "workspace" / "skills"
        skill_root.mkdir(parents=True)
        transcript = self.root / "clean-transcript.jsonl"
        transcript.write_text(
            json.dumps(
                {
                    "type": "message",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "toolCall",
                                "name": "exec",
                                "arguments": {
                                    "command": f"python {skill_root}/rdkit/run.py --out {lease.output_dir}/result.json"
                                },
                            }
                        ],
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )

        audit = self.manager.audit_attempt(
            lease,
            {"session_isolation": {"postflight_entry_session_file": str(transcript)}},
            allowed_roots=[skill_root],
        )

        self.assertEqual("clean", audit.status)
        self.manager.seal(lease, AttemptOutcome(runner_status="completed", contamination_audit=audit))

    def test_forbidden_path_audit_ignores_paths_embedded_in_write_content(self) -> None:
        lease = self.manager.prepare(self._identity())
        transcript = self.root / "write-content-transcript.jsonl"
        transcript.write_text(
            json.dumps(
                {
                    "type": "message",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "toolCall",
                                "name": "write",
                                "arguments": {
                                    "path": str(lease.active_workspace / "protocol.yaml"),
                                    "content": f"source_path: {self.manager.archive_root}/old/result.json\n",
                                },
                            }
                        ],
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )

        audit = self.manager.audit_attempt(
            lease,
            {"session_isolation": {"postflight_entry_session_file": str(transcript)}},
        )

        self.assertEqual("clean", audit.status)
        self.manager.seal(lease, AttemptOutcome(runner_status="completed", contamination_audit=audit))

    def test_workdir_fallback_to_allowed_workspace_is_warning_only(self) -> None:
        lease = self.manager.prepare(self._identity())
        transcript = self.root / "workdir-fallback-transcript.jsonl"
        transcript.write_text(
            json.dumps(
                {
                    "type": "message",
                    "message": {
                        "role": "toolResult",
                        "toolName": "exec",
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    'Warning: workdir "/missing/attempt/output" is unavailable; '
                                    f'using "{lease.active_workspace}".\n(Command exited with code 1)'
                                ),
                            }
                        ],
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )

        audit = self.manager.audit_attempt(
            lease,
            {"session_isolation": {"postflight_entry_session_file": str(transcript)}},
        )

        self.assertEqual("clean", audit.status)
        self.assertEqual("warning", audit.boundary_status)
        self.assertEqual("clear", audit.contamination_status)
        self.assertEqual("scoreable", audit.adjudication)
        self.assertEqual("workdir_fallback", audit.findings[0]["rule_id"])
        self.assertEqual("/missing/attempt/output", audit.findings[0]["requested_workdir"])
        self.assertEqual(str(lease.active_workspace), audit.findings[0]["fallback_workdir"])
        self.manager.seal(lease, AttemptOutcome(runner_status="completed", contamination_audit=audit))

    def test_forbidden_path_audit_unavailable_fails_closed_contract(self) -> None:
        lease = self.manager.prepare(self._identity())
        audit = self.manager.audit_attempt(lease, {})
        self.assertEqual("unavailable", audit.status)
        self.assertEqual("transcript_unavailable", audit.findings[0]["rule_id"])
        self.manager.seal(lease, AttemptOutcome(runner_status="failed", contamination_audit=audit))

    def test_missing_active_transcript_recovers_from_exact_archive_reference(self) -> None:
        lease = self.manager.prepare(self._identity())
        archived = self.root / "archived-session.jsonl"
        archived.write_text(
            json.dumps({"message": {"role": "assistant", "content": [{"type": "text", "text": "done"}]}})
            + "\n",
            encoding="utf-8",
        )

        audit = self.manager.audit_attempt(
            lease,
            {
                "session_isolation": {
                    "postflight_entry_session_file": str(self.root / "missing.jsonl"),
                    "archived_session_file": str(archived),
                }
            },
        )

        self.assertEqual("complete", audit.audit_execution_status)
        self.assertEqual("scoreable", audit.adjudication)
        self.assertTrue(audit.recovery["attempted"])
        self.assertTrue(audit.recovery["succeeded"])
        self.assertFalse(audit.recovery["model_reinvoked"])
        self.manager.seal(lease, AttemptOutcome(runner_status="completed", contamination_audit=audit))

    def test_parser_failure_recovers_from_archive_transcript(self) -> None:
        lease = self.manager.prepare(self._identity())
        malformed = self.root / "malformed-session.jsonl"
        archived = self.root / "parser-recovery-archive.jsonl"
        malformed.write_text("{not-json}\n", encoding="utf-8")
        archived.write_text(
            json.dumps({"message": {"role": "assistant", "content": [{"type": "text", "text": "done"}]}})
            + "\n",
            encoding="utf-8",
        )

        audit = self.manager.audit_attempt(
            lease,
            {
                "session_isolation": {
                    "postflight_entry_session_file": str(malformed),
                    "archived_session_file": str(archived),
                }
            },
        )

        self.assertEqual("complete", audit.audit_execution_status)
        self.assertEqual("scoreable", audit.adjudication)
        self.assertTrue(audit.recovery["succeeded"])
        self.assertEqual("archive", audit.recovery["source"])
        self.manager.seal(lease, AttemptOutcome(runner_status="completed", contamination_audit=audit))

    def test_prepare_set_is_all_or_quarantined(self) -> None:
        first = self._identity(session_id="session-a")
        second = AttemptIdentity(
            **{
                **self._identity(session_id="session-b").sentinel_fields(),
                "agent_id": first.agent_id,
            }
        )

        with self.assertRaises(WorkspaceIsolationError) as raised:
            self.manager.prepare_set([first, second])

        self.assertEqual("workspace_lock_conflict", raised.exception.code)
        self.assertFalse(
            self.manager.active_workspace_path(group_id=first.group_id, agent_id=first.agent_id).exists()
        )
        self.assertEqual(1, len(list(self.manager.quarantine_root.iterdir())))

    def test_startup_recovery_quarantines_preparing_crash_temp(self) -> None:
        old_manager = self._manager(invocation_id="old-invocation")
        identity = self._identity(invocation_id="old-invocation")
        lease = old_manager.prepare(identity)
        preparing_temp = lease.active_workspace.parent / f".{lease.active_workspace.name}.prepare-crash"
        lease.active_workspace.rename(preparing_temp)
        old_manager._release_lease(lease)

        current = self._manager(invocation_id="new-invocation")
        report = current.recover_all_incomplete()

        self.assertEqual("recovered", report["status"])
        self.assertFalse(preparing_temp.exists())
        self.assertEqual(1, len(list(current.quarantine_root.iterdir())))

    def test_startup_recovery_quarantines_sealing_crash_temp(self) -> None:
        old_manager = self._manager(invocation_id="old-invocation")
        identity = self._identity(invocation_id="old-invocation")
        lease = old_manager.prepare(identity)
        final_archive = old_manager._archive_path(identity)
        sealing_temp = final_archive.parent / f".{final_archive.name}.seal-crash"
        sealing_temp.mkdir(parents=True)
        lease.active_workspace.rename(sealing_temp / "workspace")
        old_manager._release_lease(lease)

        current = self._manager(invocation_id="new-invocation")
        report = current.recover_all_incomplete()

        self.assertEqual("recovered", report["status"])
        self.assertEqual(1, len(report["quarantines"]))
        self.assertFalse(sealing_temp.exists())


if __name__ == "__main__":
    unittest.main()
