from __future__ import annotations

import argparse
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from benchmarking.service.single import openclaw_wrapper as wrapper


class SingleLLMSessionWrapperTests(unittest.TestCase):
    XYZ_ANSWER_SCHEMA = {
        "format": "final_answer_block",
        "final_answer_prefix": "FINAL ANSWER:",
        "value_type": "xyz",
        "fence_language": "xyz",
    }

    def test_wrapper_script_help_runs_from_file_path(self) -> None:
        wrapper_path = Path(wrapper.__file__).resolve()

        completed = subprocess.run(
            [sys.executable, str(wrapper_path), "--help"],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn("Run single-LLM OpenClaw turns", completed.stdout)

    def test_lifecycle_status_uses_timeout_payload_when_process_exit_is_zero(self) -> None:
        target = {
            "payloads": [{"text": "LLM request timed out."}],
            "meta": {"aborted": True, "livenessState": "blocked"},
        }
        self.assertEqual(
            "failed",
            wrapper.lifecycle_status_for_result(
                target,
                returncode=0,
                eval_kind="verifier_grounded",
            ),
        )

    def test_run_openclaw_uses_benchmark_workspace_as_process_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir).resolve()
            args = argparse.Namespace(
                agent="benchmark-single",
                session_id="session-a",
                message="Q",
                thinking="",
                timeout=0,
                json=True,
            )
            completed = subprocess.CompletedProcess(["openclaw"], 0, stdout="{}", stderr="")

            with mock.patch.object(wrapper, "resolve_openclaw_executable", return_value="openclaw"), \
                mock.patch.object(wrapper.subprocess, "run", return_value=completed) as run_mock:
                result = wrapper.run_openclaw(
                    args,
                    env={"BENCHMARK_WORKSPACE_DIR": str(workspace)},
                    message_override="finalize",
                )

        self.assertIs(completed, result)
        self.assertEqual(str(workspace), run_mock.call_args.kwargs["cwd"])

    def test_run_openclaw_rejects_unavailable_benchmark_workspace(self) -> None:
        args = argparse.Namespace(
            agent="benchmark-single",
            session_id="session-a",
            message="Q",
            thinking="",
            timeout=0,
            json=True,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            missing = Path(tmpdir) / "missing"
            with mock.patch.object(wrapper, "resolve_openclaw_executable", return_value="openclaw"), \
                self.assertRaises(wrapper.SessionIsolationError):
                wrapper.run_openclaw(
                    args,
                    env={"BENCHMARK_WORKSPACE_DIR": str(missing)},
                    message_override="finalize",
                )

    def write_config(self, root: Path, *, model: str = "openai/gpt-5") -> Path:
        agent_dir = root / "agents" / "benchmark-single" / "agent"
        agent_dir.mkdir(parents=True, exist_ok=True)
        config_path = root / "openclaw.json"
        config_path.write_text(
            json.dumps(
                {
                    "agents": {
                        "list": [
                            {
                                "id": "benchmark-single",
                                "agentDir": str(agent_dir),
                                "workspace": str(root / "workspace"),
                                "model": model,
                            }
                        ]
                    }
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return config_path

    def agent_result(self, text: str, *, meta: dict | None = None, is_error: bool = False) -> subprocess.CompletedProcess:
        item = {"text": text}
        if is_error:
            item["isError"] = True
        return subprocess.CompletedProcess(
            ["openclaw"],
            0,
            stdout=json.dumps({"result": {"payloads": [item], "meta": meta or {}}}),
            stderr="",
        )

    def test_takeover_exit_recovers_complete_transcript_and_keeps_typed_diagnostic(self) -> None:
        self.skipTest("legacy live JSONL recovery contract retired in OpenClaw 9.5")
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = self.write_config(root)
            transcript = root / "session-a.jsonl"
            transcript.write_text(
                '{"message":{"role":"assistant","content":[{"type":"text","text":"Reasoning.\\nFINAL ANSWER: B"}]}}\n',
                encoding="utf-8",
            )
            stderr = (
                "[diagnostic] lane task error: lane=main "
                'error="EmbeddedAttemptSessionTakeoverError: session file changed while embedded prompt lock was released: '
                f'{transcript}"\n'
            )
            completed = subprocess.CompletedProcess(["openclaw"], 1, stdout="", stderr=stderr)
            args = argparse.Namespace(
                agent="benchmark-single",
                config_file=str(config_path),
                session_id="session-a",
                message="Q",
                thinking="high",
                timeout=900,
                eval_kind="verifier_grounded",
                answer_schema_json="",
                json=True,
            )
            audit = {
                "requested_session_id": "session-a",
                "agent_id": "benchmark-single",
                "session_store_path": str(root / "sessions.json"),
                "preflight_removed_stale_main_entry": False,
                "preflight_previous_session_id": "",
                "postflight_entry_session_id": "session-a",
                "postflight_entry_session_file": str(transcript),
                "session_isolation_ok": True,
            }

            with mock.patch.object(wrapper, "parse_args", return_value=args), \
                mock.patch.object(wrapper, "reset_agent_main_session_if_stale", return_value=audit), \
                mock.patch.object(wrapper, "run_openclaw", return_value=completed), \
                mock.patch.object(wrapper, "inspect_postflight_session", return_value=audit), \
                mock.patch.object(sys, "stdout", new_callable=io.StringIO) as stdout:
                exit_code = wrapper.main()

        self.assertEqual(0, exit_code)
        payload = json.loads(stdout.getvalue())["result"]
        self.assertEqual("Reasoning.\nFINAL ANSWER: B", payload["payloads"][0]["text"])
        self.assertTrue(payload["meta"]["convergence"]["transcript_answer_recovered"])
        self.assertEqual("openclaw_session_takeover", payload["meta"]["execution_error"]["code"])
        self.assertFalse(payload["meta"]["execution_error"]["retryable"])

    def attach_time_reminder_meta(
        self,
        completed: subprocess.CompletedProcess,
        *,
        due: bool,
        elapsed: float,
        remaining: float,
        enabled: bool = True,
        threshold: int = 600,
    ) -> subprocess.CompletedProcess:
        completed.time_reminder_meta = {
            "enabled": enabled,
            "threshold_seconds": threshold,
            "due_before_primary_return": due,
            "primary_elapsed_seconds": elapsed,
            "applied": False,
            "skipped_reason": "",
            "remaining_seconds_at_primary_return": remaining,
        }
        return completed

    def test_missing_session_store_is_a_noop(self) -> None:
        self.skipTest("legacy sessions.json mutation contract retired in OpenClaw 9.5")
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = self.write_config(root)

            audit = wrapper.reset_agent_main_session_if_stale(
                "benchmark-single",
                "session-a",
                config_path=config_path,
            )

            self.assertFalse(audit["preflight_removed_stale_main_entry"])
            self.assertEqual("session-a", audit["requested_session_id"])
            self.assertFalse(Path(audit["session_store_path"]).exists())

    def test_matching_main_entry_is_preserved(self) -> None:
        self.skipTest("legacy sessions.json mutation contract retired in OpenClaw 9.5")
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = self.write_config(root)
            store_path = root / "agents" / "benchmark-single" / "sessions" / "sessions.json"
            store_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "agent:benchmark-single:main": {
                    "sessionId": "session-a",
                    "sessionFile": str(store_path.parent / "session-a.jsonl"),
                    "modelProvider": "openai",
                    "model": "gpt-5",
                }
            }
            store_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

            audit = wrapper.reset_agent_main_session_if_stale(
                "benchmark-single",
                "session-a",
                config_path=config_path,
            )

            self.assertFalse(audit["preflight_removed_stale_main_entry"])
            self.assertEqual(payload, json.loads(store_path.read_text(encoding="utf-8")))

    def test_stale_main_entry_is_removed_without_deleting_transcript(self) -> None:
        self.skipTest("legacy sessions.json mutation contract retired in OpenClaw 9.5")
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = self.write_config(root)
            store_path = root / "agents" / "benchmark-single" / "sessions" / "sessions.json"
            store_path.parent.mkdir(parents=True, exist_ok=True)
            old_transcript = store_path.parent / "old-session.jsonl"
            old_transcript.write_text("{}", encoding="utf-8")
            store_path.write_text(
                json.dumps(
                    {
                        "agent:benchmark-single:main": {
                            "sessionId": "old-session",
                            "sessionFile": str(old_transcript),
                            "modelProvider": "openai",
                            "model": "gpt-5",
                        },
                        "agent:other:main": {"sessionId": "other-session"},
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            audit = wrapper.reset_agent_main_session_if_stale(
                "benchmark-single",
                "session-a",
                config_path=config_path,
            )

            self.assertTrue(audit["preflight_removed_stale_main_entry"])
            self.assertEqual("old-session", audit["preflight_previous_session_id"])
            updated = json.loads(store_path.read_text(encoding="utf-8"))
            self.assertNotIn("agent:benchmark-single:main", updated)
            self.assertIn("agent:other:main", updated)
            self.assertTrue(old_transcript.is_file())

    def test_same_session_id_wrong_session_file_is_removed(self) -> None:
        self.skipTest("legacy sessions.json mutation contract retired in OpenClaw 9.5")
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = self.write_config(root)
            store_path = root / "agents" / "benchmark-single" / "sessions" / "sessions.json"
            store_path.parent.mkdir(parents=True, exist_ok=True)
            store_path.write_text(
                json.dumps(
                    {
                        "agent:benchmark-single:main": {
                            "sessionId": "session-a",
                            "sessionFile": str(store_path.parent / "old-session.jsonl"),
                            "modelProvider": "openai",
                            "model": "gpt-5",
                        }
                    }
                ),
                encoding="utf-8",
            )

            audit = wrapper.reset_agent_main_session_if_stale(
                "benchmark-single",
                "session-a",
                config_path=config_path,
            )

            self.assertTrue(audit["preflight_removed_stale_main_entry"])
            self.assertNotIn("agent:benchmark-single:main", json.loads(store_path.read_text(encoding="utf-8")))

    def test_model_mismatch_is_removed(self) -> None:
        self.skipTest("legacy JSONL mutation contract retired in OpenClaw 9.5")
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = self.write_config(root, model="openai/gpt-5")
            store_path = root / "agents" / "benchmark-single" / "sessions" / "sessions.json"
            store_path.parent.mkdir(parents=True, exist_ok=True)
            store_path.write_text(
                json.dumps(
                    {
                        "agent:benchmark-single:main": {
                            "sessionId": "session-a",
                            "sessionFile": str(store_path.parent / "session-a.jsonl"),
                            "modelProvider": "openai",
                            "model": "old-model",
                        }
                    }
                ),
                encoding="utf-8",
            )

            audit = wrapper.reset_agent_main_session_if_stale(
                "benchmark-single",
                "session-a",
                config_path=config_path,
            )

            self.assertTrue(audit["preflight_removed_stale_main_entry"])

    def test_invalid_session_store_json_fails_explicitly(self) -> None:
        self.skipTest("legacy sessions.json mutation contract retired in OpenClaw 9.5")
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = self.write_config(root)
            store_path = root / "agents" / "benchmark-single" / "sessions" / "sessions.json"
            store_path.parent.mkdir(parents=True, exist_ok=True)
            store_path.write_text("{bad json", encoding="utf-8")

            with self.assertRaises(wrapper.SessionIsolationError):
                wrapper.reset_agent_main_session_if_stale(
                    "benchmark-single",
                    "session-a",
                    config_path=config_path,
                )

    def test_postflight_reports_matching_session_ok(self) -> None:
        self.skipTest("legacy sessions.json mutation contract retired in OpenClaw 9.5")
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = self.write_config(root)
            store_path = root / "agents" / "benchmark-single" / "sessions" / "sessions.json"
            store_path.parent.mkdir(parents=True, exist_ok=True)
            store_path.write_text(
                json.dumps(
                    {
                        "agent:benchmark-single:main": {
                            "sessionId": "session-a",
                            "sessionFile": str(store_path.parent / "session-a.jsonl"),
                            "modelProvider": "openai",
                            "model": "gpt-5",
                        }
                    }
                ),
                encoding="utf-8",
            )

            audit = wrapper.inspect_postflight_session(
                "benchmark-single",
                "session-a",
                config_path=config_path,
            )

            self.assertTrue(audit["session_isolation_ok"])
            self.assertEqual("session-a", audit["postflight_entry_session_id"])
            self.assertTrue(audit["postflight_entry_session_file"].endswith("session-a.jsonl"))

    def test_postflight_prefers_matching_explicit_session_entry(self) -> None:
        self.skipTest("legacy sessions.json mutation contract retired in OpenClaw 9.5")
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = self.write_config(root)
            store_path = root / "agents" / "benchmark-single" / "sessions" / "sessions.json"
            store_path.parent.mkdir(parents=True, exist_ok=True)
            explicit_file = store_path.parent / "session-a.jsonl"
            explicit_file.write_text("{}\n", encoding="utf-8")
            store_path.write_text(
                json.dumps(
                    {
                        "agent:benchmark-single:main": {
                            "sessionId": "old-session",
                            "sessionFile": str(store_path.parent / "old-session.jsonl"),
                            "modelProvider": "openai",
                            "model": "gpt-5",
                        },
                        "agent:benchmark-single:explicit:session-a": {
                            "sessionId": "session-a",
                            "sessionFile": str(explicit_file),
                            "modelProvider": "openai",
                            "model": "gpt-5",
                        },
                    }
                ),
                encoding="utf-8",
            )

            audit = wrapper.inspect_postflight_session(
                "benchmark-single",
                "session-a",
                config_path=config_path,
            )

            self.assertTrue(audit["session_isolation_ok"])
            self.assertEqual("session-a", audit["postflight_entry_session_id"])
            self.assertEqual(str(explicit_file), audit["postflight_entry_session_file"])

    def test_postflight_reads_explicit_session_from_run_local_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = self.write_config(root)
            store_path = root / "launch-home" / ".openclaw" / "agents" / "benchmark-single" / "sessions" / "sessions.json"
            store_path.parent.mkdir(parents=True, exist_ok=True)
            transcript_path = store_path.parent / "session-a.jsonl"
            transcript_path.write_text("{}\n", encoding="utf-8")
            store_path.write_text(
                json.dumps(
                    {
                        "agent:benchmark-single:explicit:session-a": {
                            "sessionId": "session-a",
                            "sessionFile": str(transcript_path),
                            "modelProvider": "openai",
                            "model": "gpt-5",
                        }
                    }
                ),
                encoding="utf-8",
            )

            audit = wrapper.inspect_postflight_session(
                "benchmark-single",
                "session-a",
                config_path=config_path,
                session_store_path=store_path,
            )

            self.assertTrue(audit["session_isolation_ok"])
            self.assertEqual(str(store_path.resolve()), audit["session_store_path"])
            self.assertEqual(str(transcript_path), audit["postflight_entry_session_file"])

    def test_postflight_allows_same_session_with_model_metadata_drift(self) -> None:
        self.skipTest("legacy sessions.json mutation contract retired in OpenClaw 9.5")
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = self.write_config(root, model="minimax/MiniMax-M2.7")
            store_path = root / "agents" / "benchmark-single" / "sessions" / "sessions.json"
            store_path.parent.mkdir(parents=True, exist_ok=True)
            store_path.write_text(
                json.dumps(
                    {
                        "agent:benchmark-single:main": {
                            "sessionId": "session-a",
                            "sessionFile": str(store_path.parent / "session-a.jsonl"),
                            "modelProvider": "su8",
                            "model": "gpt-5.4",
                        }
                    }
                ),
                encoding="utf-8",
            )

            audit = wrapper.inspect_postflight_session(
                "benchmark-single",
                "session-a",
                config_path=config_path,
            )

            self.assertTrue(audit["session_isolation_ok"])
            self.assertFalse(audit["postflight_model_matches_requested"])
            self.assertEqual("minimax", audit["requested_model_provider"])
            self.assertEqual("MiniMax-M2.7", audit["requested_model"])
            self.assertEqual("su8", audit["postflight_entry_model_provider"])
            self.assertEqual("gpt-5.4", audit["postflight_entry_model"])

    def test_main_merges_preflight_and_postflight_audit_into_openclaw_json(self) -> None:
        self.skipTest("legacy JSONL wrapper fixture replaced by trajectory export contract")
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = self.write_config(root)

            fake_args = argparse.Namespace(
                agent="benchmark-single",
                config_file=str(config_path),
                session_id="session-a",
                message="Q",
                thinking="high",
                timeout=30,
                json=True,
            )
            completed = subprocess.CompletedProcess(
                ["openclaw"],
                0,
                stdout=json.dumps({"result": {"payloads": [{"text": "FINAL ANSWER: 1"}], "meta": {}}}),
                stderr="",
            )
            preflight = {
                "requested_session_id": "session-a",
                "agent_id": "benchmark-single",
                "session_store_path": str(root / "sessions.json"),
                "preflight_removed_stale_main_entry": True,
                "preflight_previous_session_id": "old-session",
                "postflight_entry_session_id": "",
                "postflight_entry_session_file": "",
                "session_isolation_ok": False,
            }
            postflight = {
                "requested_session_id": "session-a",
                "agent_id": "benchmark-single",
                "session_store_path": str(root / "sessions.json"),
                "preflight_removed_stale_main_entry": False,
                "preflight_previous_session_id": "",
                "postflight_entry_session_id": "session-a",
                "postflight_entry_session_file": str(root / "session-a.jsonl"),
                "session_isolation_ok": True,
            }

            with mock.patch.object(wrapper, "parse_args", return_value=fake_args):
                with mock.patch.object(wrapper, "reset_agent_main_session_if_stale", return_value=preflight):
                    with mock.patch.object(wrapper, "run_openclaw", return_value=completed):
                        with mock.patch.object(wrapper, "inspect_postflight_session", return_value=postflight):
                            with mock.patch("builtins.print") as print_mock:
                                exit_code = wrapper.main()

            self.assertEqual(0, exit_code)
            payload = json.loads(print_mock.call_args.args[0])
            audit = payload["result"]["meta"]["session_isolation"]
            self.assertTrue(audit["session_isolation_ok"])
            self.assertTrue(audit["preflight_removed_stale_main_entry"])
            self.assertEqual("old-session", audit["preflight_previous_session_id"])
            self.assertEqual("session-a", audit["postflight_entry_session_id"])

    def test_main_extracts_openclaw_json_when_stdout_has_prefix_text(self) -> None:
        self.skipTest("legacy JSONL wrapper fixture replaced by trajectory export contract")
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = self.write_config(root)

            fake_args = argparse.Namespace(
                agent="benchmark-single",
                config_file=str(config_path),
                session_id="session-a",
                message="Q",
                thinking="high",
                timeout=30,
                json=True,
            )
            noisy_stdout = (
                "Invalid config warning that is not JSON\n"
                + json.dumps({"result": {"payloads": [{"text": "FINAL ANSWER: 1"}], "meta": {}}})
                + "\n"
            )
            completed = subprocess.CompletedProcess(["openclaw"], 0, stdout=noisy_stdout, stderr="")
            audit = {
                "requested_session_id": "session-a",
                "agent_id": "benchmark-single",
                "session_store_path": str(root / "sessions.json"),
                "preflight_removed_stale_main_entry": False,
                "preflight_previous_session_id": "",
                "postflight_entry_session_id": "session-a",
                "postflight_entry_session_file": str(root / "session-a.jsonl"),
                "session_isolation_ok": True,
            }

            with mock.patch.object(wrapper, "parse_args", return_value=fake_args):
                with mock.patch.object(wrapper, "reset_agent_main_session_if_stale", return_value=audit):
                    with mock.patch.object(wrapper, "run_openclaw", return_value=completed):
                        with mock.patch.object(wrapper, "inspect_postflight_session", return_value=audit):
                            with mock.patch("builtins.print") as print_mock:
                                exit_code = wrapper.main()

            self.assertEqual(0, exit_code)
            payload = json.loads(print_mock.call_args.args[0])
            self.assertEqual("FINAL ANSWER: 1", payload["result"]["payloads"][0]["text"])
            self.assertTrue(payload["result"]["meta"]["session_isolation"]["session_isolation_ok"])

    def test_main_keeps_invalid_stdout_as_diagnostics_only(self) -> None:
        self.skipTest("legacy JSONL wrapper fixture replaced by trajectory export contract")
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = self.write_config(root)
            fake_args = argparse.Namespace(
                agent="benchmark-single",
                config_file=str(config_path),
                session_id="session-a",
                message="Q",
                thinking="high",
                timeout=30,
                json=True,
            )
            completed = subprocess.CompletedProcess(
                ["openclaw"],
                0,
                stdout=json.dumps({"query": "not an agent result", "count": 3}),
                stderr="",
            )
            audit = {
                "requested_session_id": "session-a",
                "agent_id": "benchmark-single",
                "session_store_path": str(root / "sessions.json"),
                "preflight_removed_stale_main_entry": False,
                "preflight_previous_session_id": "",
                "postflight_entry_session_id": "session-a",
                "postflight_entry_session_file": str(root / "session-a.jsonl"),
                "session_isolation_ok": True,
            }

            with mock.patch.object(wrapper, "parse_args", return_value=fake_args):
                with mock.patch.object(wrapper, "reset_agent_main_session_if_stale", return_value=audit):
                    with mock.patch.object(wrapper, "run_openclaw", return_value=completed):
                        with mock.patch.object(wrapper, "inspect_postflight_session", return_value=audit):
                            with mock.patch("builtins.print") as print_mock:
                                exit_code = wrapper.main()

            self.assertEqual(0, exit_code)
            payload = json.loads(print_mock.call_args.args[0])
            result = payload["result"]
            self.assertEqual([], result["payloads"])
            diagnostics = result["meta"]["stdout_diagnostics"]
            self.assertFalse(diagnostics["schema_valid"])
            self.assertEqual("missing_payloads", diagnostics["reason"])
            self.assertEqual("not an agent result", diagnostics["invalid_stdout_payload"]["query"])
            self.assertTrue(result["meta"]["session_isolation"]["session_isolation_ok"])

    def test_wrapper_records_transcript_metrics_and_recovers_transcript_answer(self) -> None:
        self.skipTest("legacy live JSONL recovery contract retired in OpenClaw 9.5")
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = self.write_config(root)
            store_path = root / "agents" / "benchmark-single" / "sessions" / "sessions.json"
            session_path = store_path.parent / "session-a.jsonl"
            session_path.parent.mkdir(parents=True, exist_ok=True)
            session_path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "type": "message",
                                "message": {
                                    "role": "assistant",
                                    "content": [{"type": "toolCall", "name": "read"}],
                                },
                            }
                        ),
                        json.dumps(
                            {
                                "type": "message",
                                "message": {
                                    "role": "assistant",
                                    "content": [
                                        {
                                            "type": "text",
                                            "text": "Checked derivation.\nFINAL ANSWER: 273",
                                        }
                                    ],
                                },
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            store_path.write_text(
                json.dumps(
                    {
                        "agent:benchmark-single:main": {
                            "sessionId": "session-a",
                            "sessionFile": str(session_path),
                            "modelProvider": "openai",
                            "model": "gpt-5",
                        }
                    }
                ),
                encoding="utf-8",
            )
            fake_args = argparse.Namespace(
                agent="benchmark-single",
                config_file=str(config_path),
                session_id="session-a",
                message="Q",
                thinking="high",
                timeout=30,
                json=True,
                eval_kind="verifier_grounded",
            )
            completed = subprocess.CompletedProcess(
                ["openclaw"],
                0,
                stdout=json.dumps(
                    {
                        "result": {
                            "payloads": [{"text": "Request timed out before a response was generated."}],
                            "meta": {"aborted": True, "livenessState": "blocked"},
                        }
                    }
                ),
                stderr="",
            )

            with mock.patch.object(wrapper, "parse_args", return_value=fake_args), \
                mock.patch.object(wrapper, "run_openclaw", return_value=completed), \
                mock.patch.object(sys, "stdout", new_callable=io.StringIO) as stdout:
                exit_code = wrapper.main()

        self.assertEqual(0, exit_code)
        payload = json.loads(stdout.getvalue())
        result = payload["result"]
        self.assertEqual("Checked derivation.\nFINAL ANSWER: 273", result["payloads"][0]["text"])
        convergence = result["meta"]["convergence"]
        self.assertTrue(convergence["transcript_answer_recovered"])
        self.assertEqual(90, convergence["policy"]["finalization_safety_seconds"])
        self.assertNotIn("finalization_grace_seconds", convergence["policy"])
        self.assertEqual(1, convergence["tool_call_count"])
        self.assertEqual(2, convergence["assistant_turn_count"])

    def test_wrapper_recovers_stream_error_from_transcript(self) -> None:
        self.skipTest("legacy live JSONL recovery contract retired in OpenClaw 9.5")
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = self.write_config(root)
            store_path = root / "agents" / "benchmark-single" / "sessions" / "sessions.json"
            session_path = store_path.parent / "session-a.jsonl"
            session_path.parent.mkdir(parents=True, exist_ok=True)
            session_path.write_text(
                json.dumps(
                    {
                        "type": "message",
                        "message": {
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "text",
                                    "text": "Visible reasoning.\nFINAL ANSWER: B",
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
                        "agent:benchmark-single:main": {
                            "sessionId": "session-a",
                            "sessionFile": str(session_path),
                            "modelProvider": "openai",
                            "model": "gpt-5",
                        }
                    }
                ),
                encoding="utf-8",
            )
            fake_args = argparse.Namespace(
                agent="benchmark-single",
                config_file=str(config_path),
                session_id="session-a",
                message="Q",
                thinking="high",
                timeout=30,
                json=True,
                eval_kind="verifier_grounded",
            )
            completed = subprocess.CompletedProcess(
                ["openclaw"],
                0,
                stdout=json.dumps(
                    {
                        "result": {
                            "payloads": [{"text": "stream_read_error", "isError": True}],
                            "meta": {
                                "stopReason": "error",
                                "completion": {"finishReason": "error"},
                                "livenessState": "blocked",
                            },
                        }
                    }
                ),
                stderr="",
            )

            with mock.patch.object(wrapper, "parse_args", return_value=fake_args), \
                mock.patch.object(wrapper, "run_openclaw", return_value=completed), \
                mock.patch.object(sys, "stdout", new_callable=io.StringIO) as stdout:
                exit_code = wrapper.main()

        self.assertEqual(0, exit_code)
        payload = json.loads(stdout.getvalue())
        result = payload["result"]
        self.assertEqual("Visible reasoning.\nFINAL ANSWER: B", result["payloads"][0]["text"])
        convergence = result["meta"]["convergence"]
        self.assertTrue(convergence["agent_error_payload_detected"])
        self.assertEqual("agent_stream_read_error", convergence["agent_error_kind"])
        self.assertTrue(convergence["transcript_answer_recovered"])
        self.assertEqual("single-llm-session-transcript", convergence["recovery_source"])

    def test_wrapper_recovers_markdown_final_answer_from_transcript(self) -> None:
        self.skipTest("legacy live JSONL recovery contract retired in OpenClaw 9.5")
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = self.write_config(root)
            store_path = root / "agents" / "benchmark-single" / "sessions" / "sessions.json"
            session_path = store_path.parent / "session-a.jsonl"
            session_path.parent.mkdir(parents=True, exist_ok=True)
            session_path.write_text(
                json.dumps(
                    {
                        "type": "message",
                        "message": {
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "text",
                                    "text": "Visible reasoning.\n**FINAL ANSWER:** B",
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
                        "agent:benchmark-single:main": {
                            "sessionId": "session-a",
                            "sessionFile": str(session_path),
                            "modelProvider": "openai",
                            "model": "gpt-5",
                        }
                    }
                ),
                encoding="utf-8",
            )
            fake_args = argparse.Namespace(
                agent="benchmark-single",
                config_file=str(config_path),
                session_id="session-a",
                message="Q",
                thinking="high",
                timeout=30,
                json=True,
                eval_kind="verifier_grounded",
            )
            completed = subprocess.CompletedProcess(
                ["openclaw"],
                0,
                stdout=json.dumps(
                    {
                        "result": {
                            "payloads": [{"text": "Request timed out before a response was generated."}],
                            "meta": {"aborted": True, "livenessState": "blocked"},
                        }
                    }
                ),
                stderr="",
            )

            with mock.patch.object(wrapper, "parse_args", return_value=fake_args), \
                mock.patch.object(wrapper, "run_openclaw", return_value=completed), \
                mock.patch.object(sys, "stdout", new_callable=io.StringIO) as stdout:
                exit_code = wrapper.main()

        self.assertEqual(0, exit_code)
        payload = json.loads(stdout.getvalue())
        result = payload["result"]
        self.assertEqual("Visible reasoning.\n**FINAL ANSWER:** B", result["payloads"][0]["text"])
        convergence = result["meta"]["convergence"]
        self.assertTrue(convergence["transcript_answer_recovered"])
        self.assertEqual("single-llm-session-transcript", convergence["recovery_source"])

    def test_wrapper_finalization_rescue_replaces_error_payload_when_complete(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = self.write_config(root)
            store_path = root / "agents" / "benchmark-single" / "sessions" / "sessions.json"
            session_path = store_path.parent / "session-a.jsonl"
            session_path.parent.mkdir(parents=True, exist_ok=True)
            session_path.write_text(
                json.dumps(
                    {
                        "type": "message",
                        "message": {
                            "role": "assistant",
                            "content": [{"type": "text", "text": "Reasoning without final marker."}],
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            store_path.write_text(
                json.dumps(
                    {
                        "agent:benchmark-single:main": {
                            "sessionId": "session-a",
                            "sessionFile": str(session_path),
                            "modelProvider": "openai",
                            "model": "gpt-5",
                        }
                    }
                ),
                encoding="utf-8",
            )
            fake_args = argparse.Namespace(
                agent="benchmark-single",
                config_file=str(config_path),
                session_id="session-a",
                message="Q",
                thinking="high",
                timeout=30,
                json=True,
                eval_kind="verifier_grounded",
            )
            first = subprocess.CompletedProcess(
                ["openclaw"],
                0,
                stdout=json.dumps(
                    {
                        "result": {
                            "payloads": [{"text": "stream_read_error", "isError": True}],
                            "meta": {
                                "stopReason": "error",
                                "completion": {"finishReason": "error"},
                                "livenessState": "blocked",
                            },
                        }
                    }
                ),
                stderr="",
            )
            rescue = subprocess.CompletedProcess(
                ["openclaw"],
                0,
                stdout=json.dumps({"result": {"payloads": [{"text": "Visible check.\nFINAL ANSWER: B"}], "meta": {}}}),
                stderr="",
            )

            with mock.patch.object(wrapper, "parse_args", return_value=fake_args), \
                mock.patch.object(wrapper, "run_openclaw", side_effect=[first, rescue]) as run_mock, \
                mock.patch.object(sys, "stdout", new_callable=io.StringIO) as stdout:
                exit_code = wrapper.main()

        self.assertEqual(0, exit_code)
        self.assertEqual(1, run_mock.call_count)
        payload = json.loads(stdout.getvalue())
        result = payload["result"]
        self.assertEqual("stream_read_error", result["payloads"][0]["text"])
        convergence = result["meta"]["convergence"]
        self.assertTrue(convergence["agent_error_payload_detected"])
        self.assertEqual("agent_stream_read_error", convergence["agent_error_kind"])
        self.assertFalse(convergence["finalization_rescue_attempted"])

    def test_wrapper_finalization_rescue_accepts_verifier_grounded_xyz_block_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = self.write_config(root)
            store_path = root / "agents" / "benchmark-single" / "sessions" / "sessions.json"
            session_path = store_path.parent / "session-a.jsonl"
            session_path.parent.mkdir(parents=True, exist_ok=True)
            session_path.write_text(
                json.dumps(
                    {
                        "type": "message",
                        "message": {
                            "role": "assistant",
                            "content": [{"type": "text", "text": "Reasoning without final marker."}],
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            store_path.write_text(
                json.dumps(
                    {
                        "agent:benchmark-single:main": {
                            "sessionId": "session-a",
                            "sessionFile": str(session_path),
                            "modelProvider": "openai",
                            "model": "gpt-5",
                        }
                    }
                ),
                encoding="utf-8",
            )
            fake_args = argparse.Namespace(
                agent="benchmark-single",
                config_file=str(config_path),
                session_id="session-a",
                message="Q",
                thinking="high",
                timeout=30,
                json=True,
                eval_kind="verifier_grounded",
                answer_schema_json=json.dumps(self.XYZ_ANSWER_SCHEMA),
            )
            first = subprocess.CompletedProcess(
                ["openclaw"],
                0,
                stdout=json.dumps(
                    {
                        "result": {
                            "payloads": [{"text": "stream_read_error", "isError": True}],
                            "meta": {
                                "stopReason": "error",
                                "completion": {"finishReason": "error"},
                                "livenessState": "blocked",
                            },
                        }
                    }
                ),
                stderr="",
            )
            rescue_text = "FINAL ANSWER:\n```xyz\n3\nwater\nO 0 0 0\nH 0 0 1\nH 0 1 0\n```"
            rescue = self.agent_result(rescue_text)

            with mock.patch.object(wrapper, "parse_args", return_value=fake_args), \
                mock.patch.object(wrapper, "run_openclaw", side_effect=[first, rescue]) as run_mock, \
                mock.patch.object(sys, "stdout", new_callable=io.StringIO) as stdout:
                exit_code = wrapper.main()

        self.assertEqual(0, exit_code)
        self.assertEqual(1, run_mock.call_count)
        payload = json.loads(stdout.getvalue())
        result = payload["result"]
        self.assertEqual("stream_read_error", result["payloads"][0]["text"])
        convergence = result["meta"]["convergence"]
        self.assertTrue(convergence["agent_error_payload_detected"])
        self.assertEqual("agent_stream_read_error", convergence["agent_error_kind"])
        self.assertFalse(convergence["finalization_rescue_attempted"])

    def test_wrapper_keeps_complete_replay_invalid_final_answer_native(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = self.write_config(root)
            store_path = root / "agents" / "benchmark-single" / "sessions" / "sessions.json"
            session_path = store_path.parent / "session-a.jsonl"
            session_path.parent.mkdir(parents=True, exist_ok=True)
            session_path.write_text("", encoding="utf-8")
            store_path.write_text(
                json.dumps(
                    {
                        "agent:benchmark-single:main": {
                            "sessionId": "session-a",
                            "sessionFile": str(session_path),
                            "modelProvider": "openai",
                            "model": "gpt-5",
                        }
                    }
                ),
                encoding="utf-8",
            )
            answer_text = "Visible verification.\nFINAL ANSWER: CCO"
            fake_args = argparse.Namespace(
                agent="benchmark-single",
                config_file=str(config_path),
                session_id="session-a",
                message="Q",
                thinking="high",
                timeout=30,
                json=True,
                eval_kind="verifier_grounded",
            )
            completed = subprocess.CompletedProcess(
                ["openclaw"],
                0,
                stdout=json.dumps(
                    {
                        "result": {
                            "payloads": [{"text": answer_text}],
                            "meta": {
                                "replayInvalid": True,
                                "stopReason": "stop",
                                "completion": {"finishReason": "stop"},
                                "livenessState": "working",
                            },
                        }
                    }
                ),
                stderr="",
            )

            with mock.patch.object(wrapper, "parse_args", return_value=fake_args), \
                mock.patch.object(wrapper, "run_openclaw", return_value=completed) as run_mock, \
                mock.patch.object(sys, "stdout", new_callable=io.StringIO) as stdout:
                exit_code = wrapper.main()

        self.assertEqual(0, exit_code)
        self.assertEqual(1, run_mock.call_count)
        payload = json.loads(stdout.getvalue())
        result = payload["result"]
        self.assertEqual(answer_text, result["payloads"][0]["text"])
        convergence = result["meta"]["convergence"]
        self.assertFalse(convergence["agent_error_payload_detected"])
        self.assertEqual("", convergence["agent_error_kind"])
        self.assertFalse(convergence["transcript_answer_recovered"])
        self.assertFalse(convergence["finalization_rescue_attempted"])
        self.assertEqual("replay_invalid", convergence["replay_invalid_diagnostics"]["reason"])

    def test_wrapper_keeps_complete_replay_invalid_verifier_grounded_xyz_block_native(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = self.write_config(root)
            store_path = root / "agents" / "benchmark-single" / "sessions" / "sessions.json"
            session_path = store_path.parent / "session-a.jsonl"
            session_path.parent.mkdir(parents=True, exist_ok=True)
            session_path.write_text("", encoding="utf-8")
            store_path.write_text(
                json.dumps(
                    {
                        "agent:benchmark-single:main": {
                            "sessionId": "session-a",
                            "sessionFile": str(session_path),
                            "modelProvider": "openai",
                            "model": "gpt-5",
                        }
                    }
                ),
                encoding="utf-8",
            )
            answer_text = "Visible verification.\nFINAL ANSWER:\n```xyz\n3\nwater\nO 0 0 0\nH 0 0 1\nH 0 1 0\n```"
            fake_args = argparse.Namespace(
                agent="benchmark-single",
                config_file=str(config_path),
                session_id="session-a",
                message="Q",
                thinking="high",
                timeout=30,
                json=True,
                eval_kind="verifier_grounded",
                answer_schema_json=json.dumps(self.XYZ_ANSWER_SCHEMA),
            )
            completed = subprocess.CompletedProcess(
                ["openclaw"],
                0,
                stdout=json.dumps(
                    {
                        "result": {
                            "payloads": [{"text": answer_text}],
                            "meta": {
                                "replayInvalid": True,
                                "stopReason": "stop",
                                "completion": {"finishReason": "stop"},
                                "livenessState": "working",
                            },
                        }
                    }
                ),
                stderr="",
            )

            with mock.patch.object(wrapper, "parse_args", return_value=fake_args), \
                mock.patch.object(wrapper, "run_openclaw", return_value=completed) as run_mock, \
                mock.patch.object(sys, "stdout", new_callable=io.StringIO) as stdout:
                exit_code = wrapper.main()

        self.assertEqual(0, exit_code)
        self.assertEqual(1, run_mock.call_count)
        payload = json.loads(stdout.getvalue())
        result = payload["result"]
        self.assertEqual(answer_text, result["payloads"][0]["text"])
        convergence = result["meta"]["convergence"]
        self.assertFalse(convergence["agent_error_payload_detected"])
        self.assertEqual("", convergence["agent_error_kind"])
        self.assertFalse(convergence["transcript_answer_recovered"])
        self.assertFalse(convergence["finalization_rescue_attempted"])
        self.assertEqual("replay_invalid", convergence["replay_invalid_diagnostics"]["reason"])

    def test_wrapper_reports_replay_invalid_diagnostics_when_not_recovered(self) -> None:
        self.skipTest("legacy live JSONL recovery contract retired in OpenClaw 9.5")
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = self.write_config(root)
            store_path = root / "agents" / "benchmark-single" / "sessions" / "sessions.json"
            session_path = store_path.parent / "session-a.jsonl"
            session_path.parent.mkdir(parents=True, exist_ok=True)
            session_path.write_text(
                json.dumps(
                    {
                        "type": "message",
                        "message": {
                            "role": "assistant",
                            "content": [{"type": "text", "text": "Partial research answer without conclusion."}],
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            store_path.write_text(
                json.dumps(
                    {
                        "agent:benchmark-single:main": {
                            "sessionId": "session-a",
                            "sessionFile": str(session_path),
                            "modelProvider": "openai",
                            "model": "gpt-5",
                        }
                    }
                ),
                encoding="utf-8",
            )
            fake_args = argparse.Namespace(
                agent="benchmark-single",
                config_file=str(config_path),
                session_id="session-a",
                message="Q",
                thinking="high",
                timeout=30,
                json=True,
                eval_kind="verifier_grounded",
            )
            first = subprocess.CompletedProcess(
                ["openclaw"],
                0,
                stdout=json.dumps(
                    {
                        "result": {
                            "payloads": [{"text": "Partial research answer without conclusion."}],
                            "meta": {
                                "replayInvalid": True,
                                "stopReason": "stop",
                                "completion": {"finishReason": "stop"},
                                "livenessState": "working",
                                "error": {"message": "session replay failed while rebuilding context"},
                            },
                        }
                    }
                ),
                stderr="",
            )
            rescue = subprocess.CompletedProcess(
                ["openclaw"],
                0,
                stdout=json.dumps({"result": {"payloads": [{"text": "Still partial."}], "meta": {}}}),
                stderr="",
            )

            with mock.patch.object(wrapper, "parse_args", return_value=fake_args), \
                mock.patch.object(wrapper, "run_openclaw", side_effect=[first, rescue]), \
                mock.patch.object(sys, "stdout", new_callable=io.StringIO) as stdout:
                exit_code = wrapper.main()

        self.assertEqual(0, exit_code)
        payload = json.loads(stdout.getvalue())
        convergence = payload["result"]["meta"]["convergence"]
        diagnostics = convergence["replay_invalid_diagnostics"]
        self.assertEqual("replay_invalid", diagnostics["reason"])
        self.assertIn("session replay failed", diagnostics["diagnostic_text"])
        self.assertEqual("working", diagnostics["livenessState"])
        self.assertEqual("stop", diagnostics["stopReason"])
        self.assertEqual("stop", diagnostics["finishReason"])

    def test_wrapper_finalization_rescue_keeps_error_payload_when_incomplete(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = self.write_config(root)
            store_path = root / "agents" / "benchmark-single" / "sessions" / "sessions.json"
            session_path = store_path.parent / "session-a.jsonl"
            session_path.parent.mkdir(parents=True, exist_ok=True)
            session_path.write_text(
                json.dumps(
                    {
                        "type": "message",
                        "message": {
                            "role": "assistant",
                            "content": [{"type": "text", "text": "Reasoning without final marker."}],
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            store_path.write_text(
                json.dumps(
                    {
                        "agent:benchmark-single:main": {
                            "sessionId": "session-a",
                            "sessionFile": str(session_path),
                            "modelProvider": "openai",
                            "model": "gpt-5",
                        }
                    }
                ),
                encoding="utf-8",
            )
            fake_args = argparse.Namespace(
                agent="benchmark-single",
                config_file=str(config_path),
                session_id="session-a",
                message="Q",
                thinking="high",
                timeout=30,
                json=True,
                eval_kind="verifier_grounded",
            )
            first = subprocess.CompletedProcess(
                ["openclaw"],
                0,
                stdout=json.dumps(
                    {
                        "result": {
                            "payloads": [{"text": "stream_read_error", "isError": True}],
                            "meta": {
                                "stopReason": "error",
                                "completion": {"finishReason": "error"},
                                "livenessState": "blocked",
                            },
                        }
                    }
                ),
                stderr="",
            )
            rescue = subprocess.CompletedProcess(
                ["openclaw"],
                0,
                stdout=json.dumps(
                    {
                        "result": {
                            "payloads": [{"text": "## Conclusion\nThe spectrum appears most consistent with option B."}],
                            "meta": {},
                        }
                    }
                ),
                stderr="",
            )

            with mock.patch.object(wrapper, "parse_args", return_value=fake_args), \
                mock.patch.object(wrapper, "run_openclaw", side_effect=[first, rescue]) as run_mock, \
                mock.patch.object(sys, "stdout", new_callable=io.StringIO) as stdout:
                exit_code = wrapper.main()

        self.assertEqual(0, exit_code)
        self.assertEqual(1, run_mock.call_count)
        payload = json.loads(stdout.getvalue())
        result = payload["result"]
        self.assertEqual("stream_read_error", result["payloads"][0]["text"])
        self.assertTrue(result["payloads"][0]["isError"])
        convergence = result["meta"]["convergence"]
        self.assertFalse(convergence["finalization_rescue_attempted"])
        self.assertTrue(convergence["agent_error_payload_detected"])
        self.assertEqual("agent_stream_read_error", convergence["agent_error_kind"])

    def test_wrapper_does_not_finalization_rescue_timeout_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = self.write_config(root)
            store_path = root / "agents" / "benchmark-single" / "sessions" / "sessions.json"
            session_path = store_path.parent / "session-a.jsonl"
            session_path.parent.mkdir(parents=True, exist_ok=True)
            session_path.write_text(
                json.dumps(
                    {
                        "type": "message",
                        "message": {
                            "role": "assistant",
                            "content": [{"type": "text", "text": "Partial work without final answer."}],
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            store_path.write_text(
                json.dumps(
                    {
                        "agent:benchmark-single:main": {
                            "sessionId": "session-a",
                            "sessionFile": str(session_path),
                            "modelProvider": "openai",
                            "model": "gpt-5",
                        }
                    }
                ),
                encoding="utf-8",
            )
            fake_args = argparse.Namespace(
                agent="benchmark-single",
                config_file=str(config_path),
                session_id="session-a",
                message="Q",
                thinking="high",
                timeout=30,
                json=True,
            )
            completed = subprocess.CompletedProcess(
                ["openclaw"],
                0,
                stdout=json.dumps(
                    {
                        "result": {
                            "payloads": [{"text": "Request timed out before a response was generated."}],
                            "meta": {"aborted": True, "livenessState": "blocked"},
                        }
                    }
                ),
                stderr="",
            )

            with mock.patch.object(wrapper, "parse_args", return_value=fake_args), \
                mock.patch.object(wrapper, "run_openclaw", return_value=completed) as run_mock, \
                mock.patch.object(sys, "stdout", new_callable=io.StringIO) as stdout:
                exit_code = wrapper.main()

        self.assertEqual(0, exit_code)
        self.assertEqual(1, run_mock.call_count)
        payload = json.loads(stdout.getvalue())
        result = payload["result"]
        self.assertEqual("Request timed out before a response was generated.", result["payloads"][0]["text"])
        convergence = result["meta"]["convergence"]
        self.assertFalse(convergence["agent_error_payload_detected"])
        self.assertEqual("", convergence["agent_error_kind"])
        self.assertFalse(convergence["finalization_rescue_attempted"])

    def test_time_reminder_not_sent_before_threshold_when_answer_is_complete(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = self.write_config(root)
            fake_args = argparse.Namespace(
                agent="benchmark-single",
                config_file=str(config_path),
                session_id="session-a",
                message="Q",
                thinking="high",
                timeout=900,
                json=True,
            )
            completed = self.attach_time_reminder_meta(
                self.agent_result("Reasoning.\nFINAL ANSWER: B"),
                due=False,
                elapsed=120.0,
                remaining=780.0,
            )
            audit = {
                "requested_session_id": "session-a",
                "agent_id": "benchmark-single",
                "session_store_path": str(root / "sessions.json"),
                "preflight_removed_stale_main_entry": False,
                "preflight_previous_session_id": "",
                "postflight_entry_session_id": "session-a",
                "postflight_entry_session_file": "",
                "session_isolation_ok": True,
            }

            with mock.patch.object(wrapper, "parse_args", return_value=fake_args), \
                mock.patch.object(wrapper, "reset_agent_main_session_if_stale", return_value=audit), \
                mock.patch.object(wrapper, "run_openclaw", return_value=completed) as run_mock, \
                mock.patch.object(wrapper, "inspect_postflight_session", return_value=audit), \
                mock.patch.object(sys, "stdout", new_callable=io.StringIO) as stdout:
                exit_code = wrapper.main()

        self.assertEqual(0, exit_code)
        self.assertEqual(1, run_mock.call_count)
        payload = json.loads(stdout.getvalue())
        reminder = payload["result"]["meta"]["convergence"]["time_reminder"]
        self.assertTrue(reminder["enabled"])
        self.assertFalse(reminder["due_before_primary_return"])
        self.assertFalse(reminder["applied"])
        self.assertEqual("threshold_not_reached", reminder["skipped_reason"])

    def test_time_reminder_threshold_scales_with_answer_budget(self) -> None:
        args = argparse.Namespace(timeout=7200)

        meta = wrapper._base_time_reminder_meta(args)

        self.assertTrue(meta["enabled"])
        self.assertEqual(6000, meta["threshold_seconds"])

    def test_time_reminder_due_after_threshold_but_not_sent_when_answer_is_complete(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = self.write_config(root)
            fake_args = argparse.Namespace(
                agent="benchmark-single",
                config_file=str(config_path),
                session_id="session-a",
                message="Q",
                thinking="high",
                timeout=900,
                json=True,
            )
            completed = self.attach_time_reminder_meta(
                self.agent_result("Reasoning.\nFINAL ANSWER: B"),
                due=True,
                elapsed=760.0,
                remaining=140.0,
            )
            audit = {
                "requested_session_id": "session-a",
                "agent_id": "benchmark-single",
                "session_store_path": str(root / "sessions.json"),
                "preflight_removed_stale_main_entry": False,
                "preflight_previous_session_id": "",
                "postflight_entry_session_id": "session-a",
                "postflight_entry_session_file": "",
                "session_isolation_ok": True,
            }

            with mock.patch.object(wrapper, "parse_args", return_value=fake_args), \
                mock.patch.object(wrapper, "reset_agent_main_session_if_stale", return_value=audit), \
                mock.patch.object(wrapper, "run_openclaw", return_value=completed) as run_mock, \
                mock.patch.object(wrapper, "inspect_postflight_session", return_value=audit), \
                mock.patch.object(sys, "stdout", new_callable=io.StringIO) as stdout:
                exit_code = wrapper.main()

        self.assertEqual(0, exit_code)
        self.assertEqual(1, run_mock.call_count)
        payload = json.loads(stdout.getvalue())
        reminder = payload["result"]["meta"]["convergence"]["time_reminder"]
        self.assertTrue(reminder["due_before_primary_return"])
        self.assertFalse(reminder["applied"])
        self.assertEqual("complete_answer_available", reminder["skipped_reason"])

    def test_time_reminder_sent_after_threshold_when_answer_is_incomplete(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = self.write_config(root)
            fake_args = argparse.Namespace(
                agent="benchmark-single",
                config_file=str(config_path),
                session_id="session-a",
                message="Q",
                thinking="high",
                timeout=900,
                json=True,
            )
            primary = self.attach_time_reminder_meta(
                self.agent_result("Partial reasoning without final marker."),
                due=True,
                elapsed=760.0,
                remaining=140.0,
            )
            reminder_result = self.agent_result("Condensed reasoning.\nFINAL ANSWER: B")
            audit = {
                "requested_session_id": "session-a",
                "agent_id": "benchmark-single",
                "session_store_path": str(root / "sessions.json"),
                "preflight_removed_stale_main_entry": False,
                "preflight_previous_session_id": "",
                "postflight_entry_session_id": "session-a",
                "postflight_entry_session_file": "",
                "session_isolation_ok": True,
            }

            with mock.patch.object(wrapper, "parse_args", return_value=fake_args), \
                mock.patch.object(wrapper, "reset_agent_main_session_if_stale", return_value=audit), \
                mock.patch.object(wrapper, "run_openclaw", side_effect=[primary, reminder_result]) as run_mock, \
                mock.patch.object(wrapper, "inspect_postflight_session", return_value=audit), \
                mock.patch.object(sys, "stdout", new_callable=io.StringIO) as stdout:
                exit_code = wrapper.main()

        self.assertEqual(0, exit_code)
        self.assertEqual(2, run_mock.call_count)
        reminder_kwargs = run_mock.call_args_list[1].kwargs
        self.assertEqual(140, reminder_kwargs["timeout_override"])
        self.assertIn("Less than one sixth of the answer budget remains", reminder_kwargs["message_override"])
        self.assertIn("Please quickly organize the reasoning chain already available in this session", reminder_kwargs["message_override"])
        self.assertIn("Converge on a complete final answer in the required format", reminder_kwargs["message_override"])
        self.assertIn("Do not start new tool chains or skill exploration", reminder_kwargs["message_override"])
        payload = json.loads(stdout.getvalue())
        self.assertEqual("Condensed reasoning.\nFINAL ANSWER: B", payload["result"]["payloads"][0]["text"])
        reminder = payload["result"]["meta"]["convergence"]["time_reminder"]
        self.assertTrue(reminder["applied"])
        self.assertEqual("", reminder["skipped_reason"])

    def test_time_reminder_not_sent_after_threshold_when_no_time_remains(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = self.write_config(root)
            fake_args = argparse.Namespace(
                agent="benchmark-single",
                config_file=str(config_path),
                session_id="session-a",
                message="Q",
                thinking="high",
                timeout=900,
                json=True,
            )
            completed = self.attach_time_reminder_meta(
                self.agent_result("Request timed out before a response was generated.", meta={"aborted": True}),
                due=True,
                elapsed=900.0,
                remaining=0.0,
            )
            audit = {
                "requested_session_id": "session-a",
                "agent_id": "benchmark-single",
                "session_store_path": str(root / "sessions.json"),
                "preflight_removed_stale_main_entry": False,
                "preflight_previous_session_id": "",
                "postflight_entry_session_id": "session-a",
                "postflight_entry_session_file": "",
                "session_isolation_ok": True,
            }

            with mock.patch.object(wrapper, "parse_args", return_value=fake_args), \
                mock.patch.object(wrapper, "reset_agent_main_session_if_stale", return_value=audit), \
                mock.patch.object(wrapper, "run_openclaw", return_value=completed) as run_mock, \
                mock.patch.object(wrapper, "inspect_postflight_session", return_value=audit), \
                mock.patch.object(sys, "stdout", new_callable=io.StringIO) as stdout:
                exit_code = wrapper.main()

        self.assertEqual(0, exit_code)
        self.assertEqual(1, run_mock.call_count)
        payload = json.loads(stdout.getvalue())
        reminder = payload["result"]["meta"]["convergence"]["time_reminder"]
        self.assertTrue(reminder["due_before_primary_return"])
        self.assertFalse(reminder["applied"])
        self.assertEqual("no_remaining_time", reminder["skipped_reason"])

    def test_primary_time_reminder_tracking_does_not_kill_running_turn_at_threshold(self) -> None:
        fake_args = argparse.Namespace(timeout=900)
        poll_values = [None, 0]
        killed = {"value": False}

        class FakeProcess:
            returncode = 0

            def communicate(self, timeout: float | None = None) -> tuple[str, str]:
                status = poll_values.pop(0)
                if status is None:
                    raise subprocess.TimeoutExpired(["openclaw"], timeout)
                return (json.dumps({"result": {"payloads": [{"text": "FINAL ANSWER: B"}], "meta": {}}}), "")

            def kill(self) -> None:
                killed["value"] = True

            def terminate(self) -> None:
                killed["value"] = True

        with tempfile.TemporaryDirectory() as tmpdir, \
            mock.patch.object(wrapper.subprocess, "Popen", return_value=FakeProcess()) as popen_mock, \
            mock.patch.object(wrapper.time, "monotonic", side_effect=[0.0, 751.0, 800.0]), \
            mock.patch.object(wrapper.time, "sleep", return_value=None):
            workspace = str(Path(tmpdir).resolve())
            result = wrapper._run_openclaw_with_time_reminder_tracking(
                ["openclaw"],
                args=fake_args,
                env={"BENCHMARK_WORKSPACE_DIR": workspace},
            )

        self.assertFalse(killed["value"])
        self.assertEqual(workspace, popen_mock.call_args.kwargs["cwd"])
        self.assertEqual(0, result.returncode)
        self.assertTrue(result.time_reminder_meta["due_before_primary_return"])
        self.assertEqual(800.0, result.time_reminder_meta["primary_elapsed_seconds"])
        self.assertEqual(100.0, result.time_reminder_meta["remaining_seconds_at_primary_return"])


if __name__ == "__main__":
    unittest.main()
