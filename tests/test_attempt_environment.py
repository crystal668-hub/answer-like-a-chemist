from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from benchmarking.runtime.attempt_environment import (
    _materialize_native_tools,
    cleanup_attempt_environment,
    create_attempt_environment,
    dependency_install_events,
)


def test_create_attempt_environment_uses_uv_seed_no_project(tmp_path: Path) -> None:
    commands: list[list[str]] = []

    def fake_run(command, **kwargs):
        commands.append(list(command))
        if command[1] == "venv":
            python = tmp_path / "scratch" / "venv" / "bin" / "python"
            pip = python.parent / "pip"
            python.parent.mkdir(parents=True, exist_ok=True)
            python.write_text("", encoding="utf-8")
            pip.write_text("", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    env = create_attempt_environment(
        tmp_path / "scratch",
        bootstrap_python="/bootstrap/python",
        pypi_cutoff="2026-09-03T00:00:00Z",
        uv_executable="/usr/bin/uv",
        run_subprocess=fake_run,
    )
    assert commands[0] == [
        "/usr/bin/uv",
        "venv",
        "--seed",
        "--no-project",
        "--no-python-downloads",
        "--python",
        "/bootstrap/python",
        str(tmp_path / "scratch" / "venv"),
    ]
    assert env.to_env()["UV_EXCLUDE_NEWER"] == "2026-09-03T00:00:00Z"
    assert env.to_env()["UV_PYTHON"] == str(env.python)


def test_cleanup_attempt_environment_removes_venv_and_cache(tmp_path: Path) -> None:
    commands: list[list[str]] = []

    def fake_run(command, **kwargs):
        commands.append(list(command))
        python = tmp_path / "scratch" / "venv" / "bin" / "python"
        pip = python.parent / "pip"
        python.parent.mkdir(parents=True, exist_ok=True)
        python.write_text("", encoding="utf-8")
        pip.write_text("", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    env = create_attempt_environment(tmp_path / "scratch", uv_executable="/usr/bin/uv", run_subprocess=fake_run)
    marker = env.cache_dir / "marker"
    marker.write_text("x", encoding="utf-8")
    report = cleanup_attempt_environment(env)
    assert report == {"venv_removed": True, "cache_removed": True, "tool_bin_removed": True}
    assert not env.venv_dir.exists()
    assert not env.cache_dir.exists()


def test_uv_wrapper_rebinds_filtered_environment_and_preserves_arguments(tmp_path: Path) -> None:
    target = tmp_path / "bin with spaces" / "uv"
    target.parent.mkdir()
    target.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$VIRTUAL_ENV\" \"$UV_PYTHON\" \"$UV_DEFAULT_INDEX\" \"$UV_EXCLUDE_NEWER\" \"$UV_CACHE_DIR\" \"$1\"\n"
    )
    target.chmod(0o700)
    wrapper_dir = tmp_path / "scratch with spaces" / ".runtime-bin"
    wrapper_dir.mkdir(parents=True)
    _materialize_native_tools(
        wrapper_dir,
        uv=str(target),
        environment={
            "VIRTUAL_ENV": str(tmp_path / "scratch with spaces" / "venv"),
            "UV_PYTHON": str(tmp_path / "scratch with spaces" / "venv" / "bin" / "python"),
            "UV_DEFAULT_INDEX": "https://pypi.org/simple",
            "UV_EXCLUDE_NEWER": "2026-09-11T00:00:00Z",
            "UV_CACHE_DIR": str(tmp_path / "scratch with spaces" / "cache"),
        },
    )
    filtered = dict(os.environ)
    for key in ("VIRTUAL_ENV", "UV_PYTHON", "UV_DEFAULT_INDEX", "UV_EXCLUDE_NEWER", "UV_CACHE_DIR"):
        filtered.pop(key, None)
    result = subprocess.run(
        [str(wrapper_dir / "uv"), "pip", "install", "packaging==24.2"],
        env=filtered,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.splitlines() == [
        str(tmp_path / "scratch with spaces" / "venv"),
        str(tmp_path / "scratch with spaces" / "venv" / "bin" / "python"),
        "https://pypi.org/simple",
        "2026-09-11T00:00:00Z",
        str(tmp_path / "scratch with spaces" / "cache"),
        "pip",
    ]


def test_dependency_install_events_follow_background_process_result(tmp_path: Path) -> None:
    transcript = tmp_path / "session.jsonl"
    rows = [
        {
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "toolCall",
                        "id": "install",
                        "name": "exec",
                        "arguments": {"command": "uv pip install rdkit"},
                    }
                ],
            }
        },
        {
            "message": {
                "role": "toolResult",
                "toolCallId": "install",
                "toolName": "exec",
                "content": [
                    {
                        "type": "text",
                        "text": "Command still running (session tidy-canyon, pid 52).",
                    }
                ],
            }
        },
        {
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "toolCall",
                        "id": "poll",
                        "name": "process",
                        "arguments": {"action": "poll", "sessionId": "tidy-canyon"},
                    }
                ],
            }
        },
        {
            "message": {
                "role": "toolResult",
                "toolCallId": "poll",
                "toolName": "process",
                "content": [{"type": "text", "text": "Process exited with code 2."}],
            }
        },
    ]
    transcript.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )

    assert dependency_install_events(transcript) == [
        {
            "tool_call_id": "install",
            "command": "uv pip install rdkit",
            "call_line": 1,
            "result_line": 4,
            "outcome": "failed",
        }
    ]


def test_dependency_install_events_leave_unresolved_background_command_pending(
    tmp_path: Path,
) -> None:
    transcript = tmp_path / "session.jsonl"
    rows = [
        {
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "toolCall",
                        "id": "install",
                        "name": "exec",
                        "arguments": {"command": "uv pip install rdkit"},
                    }
                ],
            }
        },
        {
            "message": {
                "role": "toolResult",
                "toolCallId": "install",
                "toolName": "exec",
                "content": [
                    {
                        "type": "text",
                        "text": "Command still running (session tidy-canyon, pid 52).",
                    }
                ],
            }
        },
    ]
    transcript.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )

    assert dependency_install_events(transcript)[0]["outcome"] == "pending"
