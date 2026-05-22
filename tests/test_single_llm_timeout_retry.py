from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from benchmarking.core.datasets import BenchmarkRecord
from benchmarking.workflow.runners.single_llm import SingleLLMRunner
from benchmarking.workflow.prompts import build_single_llm_prompt


@dataclass(frozen=True)
class Group:
    id: str
    skills_enabled: bool
    websearch: bool = True


@dataclass
class CompletedProcess:
    returncode: int = 0
    stdout: str = "{}"
    stderr: str = ""


class SingleLLMTimeoutRetryTests(unittest.TestCase):
    def _record(self) -> BenchmarkRecord:
        return BenchmarkRecord(
            record_id="record-1",
            dataset="frontierscience",
            source_file="/tmp/frontierscience.jsonl",
            eval_kind="frontierscience_olympiad",
            prompt="Identify X.",
            reference_answer="X",
            payload={"track": "olympiad"},
        )

    def _runner(
        self,
        *,
        captured_commands: list[list[str]],
        captured_envs: list[dict[str, str]] | None = None,
        captured_timeouts: list[int | None] | None = None,
        timeout_once: bool = True,
        no_timeout: bool = False,
        build_prompt: Any | None = None,
        config_path: Path = Path("/tmp/openclaw.json"),
    ) -> SingleLLMRunner:
        calls = {"count": 0}
        prompt_builder = build_prompt or (lambda *args, **kwargs: "BASE PROMPT")

        def run_subprocess(command: list[str], *, env: dict[str, str], timeout: int) -> CompletedProcess:
            calls["count"] += 1
            captured_commands.append(command)
            if captured_envs is not None:
                captured_envs.append(dict(env))
            if captured_timeouts is not None:
                captured_timeouts.append(timeout)
            if timeout_once and calls["count"] == 1:
                raise subprocess.TimeoutExpired(command, timeout)
            return CompletedProcess()

        return SingleLLMRunner(
            agent_id="benchmark-single-skills-on",
            timeout_seconds=10,
            config_path=config_path,
            runtime_bundle_root=Path("/tmp/bundles"),
            run_subprocess=run_subprocess,
            parse_json_stdout=lambda result, command: {
                "result": {"payloads": [{"text": "Reasoning\nFINAL ANSWER: X"}], "meta": {}}
            },
            unwrap_agent_payload=lambda payload: payload["result"],
            summarize_payloads=lambda payloads: "\n\n".join(str(item.get("text") or "") for item in payloads),
            normalize_answer_tracks=lambda *, full_response_text: ("X", full_response_text),
            ensure_runtime_bundle=lambda record, *, bundle_root: None,
            build_single_llm_prompt=prompt_builder,
            slugify=lambda value, **kwargs: str(value),
            benchmark_agent_thinking="high",
            timeout_retries=1,
            timeout_retry_backoff_seconds=(0,),
            sleep_fn=lambda seconds: None,
            no_timeout=no_timeout,
        )

    def _message_from_command(self, command: list[str]) -> str:
        return command[command.index("--message") + 1]

    def test_skills_on_timeout_retry_adds_focus_prompt_after_first_attempt(self) -> None:
        captured_commands: list[list[str]] = []
        runner = self._runner(captured_commands=captured_commands)

        result = runner.run(self._record(), Group(id="single_llm_skills_on", skills_enabled=True))

        self.assertEqual(2, len(captured_commands))
        first_prompt = self._message_from_command(captured_commands[0])
        self.assertIn("BASE PROMPT", first_prompt)
        self.assertIn("BENCHMARK SCRATCH DIRECTORY", first_prompt)
        retry_prompt = self._message_from_command(captured_commands[1])
        self.assertIn("BASE PROMPT", retry_prompt)
        self.assertIn("previous skills-enabled benchmark attempt timed out", retry_prompt)
        self.assertIn("reduce tool use", retry_prompt)
        self.assertIn("finalize promptly", retry_prompt)
        self.assertFalse(result.runner_meta["timeout_retry"]["attempt_history"][0]["retry_focus_prompt_applied"])
        self.assertTrue(result.runner_meta["timeout_retry"]["attempt_history"][1]["retry_focus_prompt_applied"])

    def test_skills_off_timeout_retry_keeps_original_prompt(self) -> None:
        captured_commands: list[list[str]] = []
        runner = self._runner(captured_commands=captured_commands)

        result = runner.run(self._record(), Group(id="single_llm_skills_off", skills_enabled=False))

        self.assertEqual(2, len(captured_commands))
        self.assertEqual("BASE PROMPT", self._message_from_command(captured_commands[0]))
        self.assertEqual("BASE PROMPT", self._message_from_command(captured_commands[1]))
        self.assertFalse(result.runner_meta["timeout_retry"]["attempt_history"][0]["retry_focus_prompt_applied"])
        self.assertFalse(result.runner_meta["timeout_retry"]["attempt_history"][1]["retry_focus_prompt_applied"])

    def test_no_timeout_mode_omits_prompt_budget_and_wrapper_timeout(self) -> None:
        captured_commands: list[list[str]] = []
        captured_timeouts: list[int | None] = []
        captured_prompt_kwargs: dict[str, object] = {}

        def prompt_builder(*args, **kwargs):
            captured_prompt_kwargs.update(kwargs)
            return build_single_llm_prompt(*args, **kwargs)

        runner = self._runner(
            captured_commands=captured_commands,
            captured_timeouts=captured_timeouts,
            timeout_once=False,
            no_timeout=True,
            build_prompt=prompt_builder,
        )

        result = runner.run(self._record(), Group(id="single_llm_skills_on", skills_enabled=True))

        self.assertEqual(1, len(captured_commands))
        self.assertEqual([24 * 60 * 60], captured_timeouts)
        self.assertNotIn("--timeout", captured_commands[0])
        prompt = self._message_from_command(captured_commands[0])
        self.assertNotIn("Time budget:", prompt)
        self.assertIsNone(captured_prompt_kwargs.get("time_budget_seconds"))
        self.assertEqual("no_timeout", result.runner_meta["timeout_mode"])
        self.assertFalse(result.runner_meta["timeout_retry"]["triggered"])

    def test_skills_on_attempt_uses_record_session_scratch_directory(self) -> None:
        captured_commands: list[list[str]] = []
        captured_envs: list[dict[str, str]] = []
        runner = self._runner(captured_commands=captured_commands, captured_envs=captured_envs, timeout_once=False)

        result = runner.run(self._record(), Group(id="single_llm_skills_on", skills_enabled=True))

        self.assertEqual(1, len(captured_commands))
        session_id = captured_commands[0][captured_commands[0].index("--session-id") + 1]
        scratch_dir = Path(captured_envs[0]["BENCHMARK_SKILL_SCRATCH_DIR"])
        expected = (
            Path("/tmp/openclaw.json").parent / "benchmark-single-skills-on" / ".benchmark-scratch" / "record-1" / session_id
        ).resolve()
        self.assertEqual(expected, scratch_dir)
        self.assertTrue(scratch_dir.is_dir())
        self.assertEqual(str(scratch_dir / "requests"), captured_envs[0]["BENCHMARK_SKILL_REQUEST_DIR"])
        self.assertEqual(str(scratch_dir / "outputs"), captured_envs[0]["BENCHMARK_SKILL_OUTPUT_DIR"])
        self.assertIn(str(scratch_dir), self._message_from_command(captured_commands[0]))
        scratch_meta = result.runner_meta["skill_scratch"]
        self.assertEqual(str(scratch_dir), scratch_meta["scratch_dir"])
        self.assertEqual("record-1", scratch_meta["record_slug"])
        self.assertEqual(session_id, scratch_meta["session_id"])
        self.assertEqual("single_llm_skills_on", scratch_meta["group_id"])

    def test_skills_on_scratch_directory_uses_configured_agent_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            workspace = root / "benchmark" / "workspaces" / "benchmark-single-skills-on"
            config_path = root / "runtime-config" / "single-openclaw.json"
            config_path.parent.mkdir(parents=True)
            config_path.write_text(
                json.dumps(
                    {
                        "agents": {
                            "list": [
                                {
                                    "id": "benchmark-single-skills-on",
                                    "workspace": str(workspace),
                                    "agentDir": str(root / "agents" / "benchmark-single-skills-on" / "agent"),
                                }
                            ]
                        }
                    }
                ),
                encoding="utf-8",
            )
            captured_commands: list[list[str]] = []
            captured_envs: list[dict[str, str]] = []
            runner = self._runner(
                captured_commands=captured_commands,
                captured_envs=captured_envs,
                timeout_once=False,
                config_path=config_path,
            )

            runner.run(self._record(), Group(id="single_llm_skills_on", skills_enabled=True))

            session_id = captured_commands[0][captured_commands[0].index("--session-id") + 1]
            scratch_dir = Path(captured_envs[0]["BENCHMARK_SKILL_SCRATCH_DIR"])
            self.assertEqual((workspace / ".benchmark-scratch" / "record-1" / session_id).resolve(), scratch_dir)


if __name__ == "__main__":
    unittest.main()
