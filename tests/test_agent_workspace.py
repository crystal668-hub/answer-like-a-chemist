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
)


class AttemptWorkspaceManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.template_root = self.root / "templates" / "single"
        self.template_root.mkdir(parents=True)
        (self.template_root / "AGENTS.md").write_text("# clean template\n", encoding="utf-8")
        self.manager = self._manager(invocation_id="invocation-1")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _manager(self, *, invocation_id: str) -> AttemptWorkspaceManager:
        return AttemptWorkspaceManager(
            runtime_root=self.root / "runtime" / "runs",
            output_root=self.root / "output",
            run_id="run-1",
            invocation_id=invocation_id,
            templates={
                "single-v1": WorkspaceTemplate(template_id="single-v1", source_dir=self.template_root),
            },
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
        self.assertEqual({"AGENTS.md", SENTINEL_FILENAME, ".benchmark-scratch"}, {path.name for path in lease.active_workspace.iterdir()})
        self.manager.seal(lease, AttemptOutcome(runner_status="completed"))

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

    def test_workdir_fallback_tool_result_marks_attempt_contaminated(self) -> None:
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

        self.assertEqual("contaminated", audit.status)
        self.assertEqual("workdir_fallback", audit.findings[0]["rule_id"])
        self.assertEqual("/missing/attempt/output", audit.findings[0]["requested_workdir"])
        self.assertEqual(str(lease.active_workspace), audit.findings[0]["fallback_workdir"])
        self.manager.seal(lease, AttemptOutcome(runner_status="failed", contamination_audit=audit))

    def test_forbidden_path_audit_unavailable_fails_closed_contract(self) -> None:
        lease = self.manager.prepare(self._identity())
        audit = self.manager.audit_attempt(lease, {})
        self.assertEqual("unavailable", audit.status)
        self.assertEqual("transcript_unavailable", audit.findings[0]["rule_id"])
        self.manager.seal(lease, AttemptOutcome(runner_status="failed", contamination_audit=audit))

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
