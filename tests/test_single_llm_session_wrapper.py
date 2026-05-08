from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from benchmarking import single_llm_openclaw_wrapper as wrapper


class SingleLLMSessionWrapperTests(unittest.TestCase):
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

    def test_missing_session_store_is_a_noop(self) -> None:
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

    def test_main_merges_preflight_and_postflight_audit_into_openclaw_json(self) -> None:
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


if __name__ == "__main__":
    unittest.main()
