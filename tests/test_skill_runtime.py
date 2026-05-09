from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from benchmarking.skill_runtime import WorkspaceUvSkillRunner, classify_skill_process_failure


def test_builds_workspace_uv_python_command() -> None:
    runner = WorkspaceUvSkillRunner(workspace_root=Path("/repo"), uv_executable="/usr/bin/uv")

    command = runner.build_command(Path("/repo/skills/chem-calculator/scripts/ksp_solver.py"), ["--json"])

    assert command == ["/usr/bin/uv", "run", "python", "/repo/skills/chem-calculator/scripts/ksp_solver.py", "--json"]


def test_missing_module_stderr_is_structured_missing_dependency() -> None:
    payload = classify_skill_process_failure(
        returncode=1,
        stdout="",
        stderr="ModuleNotFoundError: No module named 'bs4'",
        command=["uv", "run", "python", "script.py"],
    )

    assert payload["available"] is False
    assert payload["error_kind"] == "missing_dependency"
    assert payload["reason"] == "missing Python module: bs4"


def test_http_provider_error_is_structured_provider_failure() -> None:
    payload = classify_skill_process_failure(
        returncode=1,
        stdout="",
        stderr="requests.exceptions.HTTPError: 403 Client Error: Forbidden",
        command=["uv", "run", "python", "script.py"],
    )

    assert payload["available"] is False
    assert payload["error_kind"] == "provider_failure"
    assert "403" in payload["reason"]


def test_runner_returns_structured_invalid_output_payload() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        script = root / "script.py"
        script.write_text("print('not json')\n", encoding="utf-8")

        def fake_run(command, *, cwd=None, env=None, text=None, capture_output=None, check=None, timeout=None):
            return subprocess.CompletedProcess(command, 0, stdout="not json\n", stderr="")

        runner = WorkspaceUvSkillRunner(workspace_root=root, uv_executable="uv", run_subprocess=fake_run)
        payload = runner.run_script(script, [])

    assert payload["available"] is False
    assert payload["error_kind"] == "invalid_output"
    assert payload["command"][:3] == ["uv", "run", "python"]
