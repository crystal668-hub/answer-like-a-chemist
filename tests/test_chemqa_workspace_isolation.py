import json
import tempfile
import types
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest import mock

from benchmarking.core.contracts import AnswerPayload, RunnerResult, RunStatus
from benchmarking.core.datasets import BenchmarkRecord
from benchmarking.runtime.agent_workspace import (
    AttemptWorkspaceManager,
    ProtectedRoot,
    ContaminationAudit,
    WorkspaceIsolationError,
    default_workspace_templates,
)
from benchmarking.runtime.session_isolation import inspect_postflight_session
from benchmarking.workflow.cli import ChemQARunner


@dataclass(frozen=True)
class Group:
    id: str = "chemqa_skills_on"
    skills_enabled: bool = True
    websearch: bool = False


class ChemQAWorkspaceIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        project_root = Path(__file__).resolve().parents[1]
        self.manager = AttemptWorkspaceManager(
            runtime_root=self.root / "runtime" / "runs",
            output_root=self.root / "output",
            run_id="run-1",
            invocation_id="invocation-1",
            templates=default_workspace_templates(project_root),
            protected_roots=(
                ProtectedRoot("benchmark_runtime_root", self.root / "runtime" / "runs", "test.runtime_root"),
                ProtectedRoot("current_output_root", self.root / "output", "test.output_root"),
            ),
        )
        self.runner = ChemQARunner(
            chemqa_root=self.root / "chemqa-root",
            timeout_seconds=30,
            config_path=self.root / "config.json",
            slot_set="A",
            review_rounds=None,
            rebuttal_rounds=None,
            model_profile="test-profile",
            runtime_bundle_root=self.root / "bundles",
            launch_workspace_root=self.root / "output" / "chemqa-launch",
            workspace_manager=self.manager,
            contamination_auditor=lambda **_kwargs: ContaminationAudit(status="clean"),
        )
        self.runner._ensure_runtime_bundle = lambda record, bundle_root: None

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _record(record_id: str) -> BenchmarkRecord:
        return BenchmarkRecord(
            record_id=record_id,
            dataset="chembench",
            source_file="/tmp/demo.jsonl",
            eval_kind="chembench_open_ended",
            prompt="Return water.",
            reference_answer="O",
            payload={},
        )

    def test_six_slots_seal_after_inner_cleanup_and_next_record_is_clean(self) -> None:
        events: list[str] = []
        attempt_workspaces: list[set[str]] = []

        def fake_run_prepared(runner, record, group, *, run_id, input_bundle):
            events.append(f"workflow:{record.record_id}")
            workspaces = dict(runner._active_slot_workspaces)
            self.assertEqual(6, len(workspaces))
            self.assertEqual(6, len(set(workspaces.values())))
            attempt_workspaces.append({str(path) for path in workspaces.values()})
            for agent_id, workspace in workspaces.items():
                self.assertTrue((workspace / ".benchmark-workspace.json").is_file())
                self.assertTrue((workspace / ".debateclaw-slot.json").is_file())
                self.assertFalse((workspace / "previous-role-output.txt").exists())
                self.assertFalse(any(path.name == ".git" for path in workspace.rglob(".git")))
                (workspace / "previous-role-output.txt").write_text(agent_id, encoding="utf-8")
            events.append(f"cleanup:{record.record_id}")
            return RunnerResult(
                status=RunStatus.COMPLETED,
                answer=AnswerPayload(short_answer_text="O", full_response_text="FINAL ANSWER: O"),
                raw={"run_id": run_id},
                runner_meta={"run_id": run_id},
            )

        self.runner._run_prepared = types.MethodType(fake_run_prepared, self.runner)
        original_seal = self.manager.seal

        def recording_seal(*args, **kwargs):
            events.append(f"seal:{args[0].identity.record_id}:{args[0].identity.agent_id}")
            return original_seal(*args, **kwargs)

        with mock.patch.object(self.manager, "seal", side_effect=recording_seal):
            first = self.runner.run(self._record("record-1"), Group())
            second = self.runner.run(self._record("record-2"), Group())

        self.assertEqual(RunStatus.COMPLETED, first.status)
        self.assertEqual(RunStatus.COMPLETED, second.status)
        self.assertEqual(attempt_workspaces[0], attempt_workspaces[1])
        first_cleanup_index = events.index("cleanup:record-1")
        first_seal_index = next(index for index, event in enumerate(events) if event.startswith("seal:record-1:"))
        self.assertLess(first_cleanup_index, first_seal_index)
        for result in (first, second):
            isolation = result.runner_meta["workspace_isolation"]
            self.assertTrue(isolation["preflight_ok"])
            self.assertTrue(isolation["archive_ok"])
            self.assertEqual(6, len(isolation["slots"]))
            for slot_meta in isolation["slots"].values():
                archive_workspace = Path(slot_meta["archive_workspace"])
                self.assertTrue((archive_workspace / "previous-role-output.txt").is_file())
                self.assertFalse(Path(slot_meta["active_workspace"]).exists())

    def test_run_local_out_of_scratch_writes_are_scoreable_degraded(self) -> None:
        config_path = self.root / "config.json"
        agent_ids = ["debateA-coordinator", *[f"debateA-{index}" for index in range(1, 6)]]
        config_path.write_text(
            json.dumps(
                {
                    "agents": {
                        "list": [
                            {"id": agent_id, "model": "openai/gpt-5.5"}
                            for agent_id in agent_ids
                        ]
                    }
                }
            ),
            encoding="utf-8",
        )
        self.runner.config_path = config_path
        self.runner._contamination_auditor = None
        self.runner._session_audit_resolver = (
            lambda agent_id, session_id, *, session_store_path=None: inspect_postflight_session(
                agent_id,
                session_id,
                config_path=config_path,
                session_store_path=session_store_path,
            )
        )

        def fake_run_prepared(runner, record, group, *, run_id, input_bundle):
            assert runner._current_lease_set is not None
            assert runner._current_launch_home is not None
            other_workspace = runner._current_lease_set.leases[-1].active_workspace
            for lease in runner._current_lease_set.leases:
                store_path = (
                    runner._current_launch_home
                    / ".openclaw"
                    / "agents"
                    / lease.identity.agent_id.lower()
                    / "sessions"
                    / "sessions.json"
                )
                store_path.parent.mkdir(parents=True, exist_ok=True)
                transcript_path = store_path.parent / f"{lease.identity.session_id}.jsonl"
                transcript_path.write_text(
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
                                            "path": str(lease.active_workspace / "role-output.yaml"),
                                            "content": f"source_path: {other_workspace}/review.yaml\n",
                                        },
                                    }
                                ],
                            },
                        }
                    )
                    + "\n",
                    encoding="utf-8",
                )
                store_path.write_text(
                    json.dumps(
                        {
                            f"agent:{lease.identity.agent_id.lower()}:explicit:{lease.identity.session_id}": {
                                "sessionId": lease.identity.session_id,
                                "sessionFile": str(transcript_path),
                                "modelProvider": "openai",
                                "model": "gpt-5.5",
                            }
                        }
                    ),
                    encoding="utf-8",
                )
            return RunnerResult(
                status=RunStatus.COMPLETED,
                answer=AnswerPayload(short_answer_text="O", full_response_text="FINAL ANSWER: O"),
                raw={"run_id": run_id},
                runner_meta={"run_id": run_id},
            )

        self.runner._run_prepared = types.MethodType(fake_run_prepared, self.runner)

        result = self.runner.run(self._record("record-1"), Group())

        self.assertEqual(RunStatus.COMPLETED, result.status)
        isolation = result.runner_meta["workspace_isolation"]
        self.assertEqual("complete", isolation["audit_execution_status"])
        self.assertEqual("violated", isolation["boundary_status"])
        self.assertEqual("clear", isolation["contamination_status"])
        self.assertEqual("scoreable_degraded", isolation["adjudication"])
        self.assertEqual(6, len(isolation["findings"]))
        self.assertTrue(all(item["access_mode"] == "write" for item in isolation["findings"]))
        for slot_meta in isolation["slots"].values():
            manifest = json.loads(Path(slot_meta["archive_manifest"]).read_text(encoding="utf-8"))
            self.assertEqual("scoreable_degraded", manifest["workspace_isolation"]["adjudication"])
            self.assertEqual("clear", manifest["workspace_isolation"]["contamination_status"])
            self.assertEqual(1, manifest["workspace_isolation"]["finding_count"])

    def test_one_slot_archive_failure_discards_completed_answer(self) -> None:
        def fake_run_prepared(runner, record, group, *, run_id, input_bundle):
            return RunnerResult(
                status=RunStatus.COMPLETED,
                answer=AnswerPayload(short_answer_text="O", full_response_text="FINAL ANSWER: O"),
                raw={},
                runner_meta={"run_id": run_id},
            )

        self.runner._run_prepared = types.MethodType(fake_run_prepared, self.runner)
        original_seal = self.manager.seal
        failed = {"done": False}

        def fail_one_slot(lease, outcome):
            if not failed["done"]:
                failed["done"] = True
                self.manager.quarantine(lease, reason="forced_archive_failure")
                raise WorkspaceIsolationError("workspace_archive_failed", "forced archive failure")
            return original_seal(lease, outcome)

        with mock.patch.object(self.manager, "seal", side_effect=fail_one_slot):
            result = self.runner.run(self._record("record-1"), Group())

        self.assertEqual(RunStatus.FAILED, result.status)
        self.assertFalse(result.should_score())
        self.assertEqual("workspace_archive_failed", result.failure.code)
        self.assertFalse(result.runner_meta["workspace_isolation"]["archive_ok"])
        self.assertEqual(1, len(list(self.manager.quarantine_root.iterdir())))

    def test_stalled_recovery_rotates_to_fresh_attempt_lease_set(self) -> None:
        self.runner._invoke_cleanroom_cleanup = lambda manifest_path: {
            "status": "cleaned",
            "manifest_path": str(manifest_path),
        }

        def fake_run_prepared(runner, record, group, *, run_id, input_bundle):
            first_set = runner._current_lease_set
            assert first_set is not None
            for lease in first_set.leases:
                (lease.active_workspace / "old-role-output.txt").write_text(
                    lease.identity.agent_id,
                    encoding="utf-8",
                )
            runner._rotate_workspaces_for_recovery(run_id)
            second_set = runner._current_lease_set
            assert second_set is not None
            self.assertEqual({1}, {lease.identity.attempt_index for lease in second_set.leases})
            for lease in second_set.leases:
                self.assertFalse((lease.active_workspace / "old-role-output.txt").exists())
                (lease.active_workspace / "new-role-output.txt").write_text(
                    lease.identity.agent_id,
                    encoding="utf-8",
                )
            return RunnerResult(
                status=RunStatus.COMPLETED,
                answer=AnswerPayload(short_answer_text="O", full_response_text="FINAL ANSWER: O"),
                raw={},
                runner_meta={"run_id": run_id},
            )

        self.runner._run_prepared = types.MethodType(fake_run_prepared, self.runner)

        result = self.runner.run(self._record("record-1"), Group())

        isolation = result.runner_meta["workspace_isolation"]
        self.assertTrue(isolation["archive_ok"])
        self.assertEqual(1, len(isolation["attempt_archives"]))
        previous_slots = isolation["attempt_archives"][0]["slots"]
        self.assertEqual(6, len(previous_slots))
        for slot_meta in previous_slots.values():
            self.assertTrue((Path(slot_meta["archive_workspace"]) / "old-role-output.txt").is_file())
        for slot_meta in isolation["slots"].values():
            workspace = Path(slot_meta["archive_workspace"])
            self.assertTrue((workspace / "new-role-output.txt").is_file())
            self.assertFalse((workspace / "old-role-output.txt").exists())


if __name__ == "__main__":
    unittest.main()
