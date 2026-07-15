from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


PLUGIN_PATH = (
    Path(__file__).resolve().parents[1]
    / "benchmarking"
    / "runtime"
    / "openclaw_plugins"
    / "benchmark-workdir-guard"
    / "index.js"
)


@unittest.skipUnless(shutil.which("node"), "Node.js is required for the OpenClaw plugin test")
class BenchmarkWorkdirGuardTests(unittest.TestCase):
    def _run_hook(self, *, workspace: Path, workdir: str | None) -> dict[str, object] | None:
        script = """
import plugin from %s;
let handler;
plugin.register({
  pluginConfig: { agentWorkspaces: { "benchmark-agent": %s } },
  on(name, candidate) {
    if (name === "before_tool_call") handler = candidate;
  },
});
const params = %s === null ? {} : { workdir: %s };
const result = handler(
  { toolName: "exec", params },
  { agentId: "benchmark-agent" },
);
process.stdout.write(JSON.stringify(result ?? null));
""" % (
            json.dumps(PLUGIN_PATH.as_uri()),
            json.dumps(str(workspace)),
            json.dumps(workdir),
            json.dumps(workdir),
        )
        completed = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(completed.stdout)

    def test_missing_explicit_workdir_is_blocked_before_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            workspace.mkdir()

            result = self._run_hook(workspace=workspace, workdir=str(workspace / "missing"))

            self.assertIs(result["block"], True)
            self.assertIn("does not exist", str(result["blockReason"]))
            self.assertIn("The command was not executed", str(result["blockReason"]))

    def test_existing_workdir_inside_attempt_workspace_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            scratch = workspace / ".benchmark-scratch" / "record" / "session"
            scratch.mkdir(parents=True)

            self.assertIsNone(self._run_hook(workspace=workspace, workdir=str(scratch)))

    def test_workdir_outside_attempt_workspace_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            workspace = root / "workspace"
            outside = root / "outside"
            workspace.mkdir()
            outside.mkdir()

            result = self._run_hook(workspace=workspace, workdir=str(outside))

            self.assertIs(result["block"], True)
            self.assertIn("outside the current benchmark workspace", str(result["blockReason"]))

    def test_file_workdir_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            workspace.mkdir()
            file_path = workspace / "not-a-directory"
            file_path.write_text("data", encoding="utf-8")

            result = self._run_hook(workspace=workspace, workdir=str(file_path))

            self.assertIs(result["block"], True)
            self.assertIn("not a directory", str(result["blockReason"]))

    def test_symlink_escape_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            workspace = root / "workspace"
            outside = root / "outside"
            workspace.mkdir()
            outside.mkdir()
            symlink = workspace / "outside-link"
            symlink.symlink_to(outside, target_is_directory=True)

            result = self._run_hook(workspace=workspace, workdir=str(symlink))

            self.assertIs(result["block"], True)
            self.assertIn("outside the current benchmark workspace", str(result["blockReason"]))

    def test_omitted_workdir_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            workspace.mkdir()

            self.assertIsNone(self._run_hook(workspace=workspace, workdir=None))
