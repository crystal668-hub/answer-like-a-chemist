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
    def _run_hook(
        self,
        *,
        workspace: Path,
        workdir: str | None = None,
        tool_name: str = "exec",
        params: dict[str, object] | None = None,
    ) -> dict[str, object] | None:
        policy = {
            "policy_digest": "test-policy",
            "read_scopes": [
                {"scope_id": "active_workspace", "path": str(workspace), "kind": "directory"}
            ],
            "write_scopes": [
                {"scope_id": "attempt_scratch", "path": str(workspace / "scratch"), "kind": "directory"}
            ],
            "exec_workdir_scopes": [
                {"scope_id": "active_workspace", "path": str(workspace), "kind": "directory"}
            ],
        }
        event_params = params if params is not None else ({} if workdir is None else {"workdir": workdir})
        script = """
import plugin from %s;
let handler;
plugin.register({
  pluginConfig: { agentPolicies: { "benchmark-agent": %s } },
  on(name, candidate) {
    if (name === "before_tool_call") handler = candidate;
  },
});
const result = handler(
  { toolName: %s, params: %s },
  { agentId: "benchmark-agent" },
);
process.stdout.write(JSON.stringify(result ?? null));
""" % (
            json.dumps(PLUGIN_PATH.as_uri()),
            json.dumps(policy),
            json.dumps(tool_name),
            json.dumps(event_params),
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
            self.assertIn("The operation was not executed", str(result["blockReason"]))

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
            self.assertIn("outside the policy scope", str(result["blockReason"]))

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
            self.assertIn("outside the policy scope", str(result["blockReason"]))

    def test_omitted_workdir_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            workspace.mkdir()

            self.assertIsNone(self._run_hook(workspace=workspace, workdir=None))

    def test_structured_write_inside_scratch_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir) / "workspace"
            (workspace / "scratch").mkdir(parents=True)

            self.assertIsNone(
                self._run_hook(
                    workspace=workspace,
                    tool_name="write",
                    params={"path": "scratch/notes.txt", "content": "ok"},
                )
            )

    def test_structured_write_outside_scratch_is_blocked_with_stable_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            workspace = root / "workspace"
            outside = root / "outside"
            (workspace / "scratch").mkdir(parents=True)
            outside.mkdir()

            result = self._run_hook(
                workspace=workspace,
                tool_name="write",
                params={"path": str(outside / "answer.txt"), "content": "no"},
            )

            self.assertIs(result["block"], True)
            self.assertIn("benchmark_workspace_guard_blocked", str(result["blockReason"]))
            self.assertIn("access=write", str(result["blockReason"]))

    def test_structured_read_symlink_escape_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            workspace = root / "workspace"
            outside = root / "outside"
            (workspace / "scratch").mkdir(parents=True)
            outside.mkdir()
            (outside / "secret.txt").write_text("secret", encoding="utf-8")
            (workspace / "scratch" / "link").symlink_to(outside, target_is_directory=True)

            result = self._run_hook(
                workspace=workspace,
                tool_name="read",
                params={"path": "scratch/link/secret.txt"},
            )

            self.assertIs(result["block"], True)
            self.assertIn("access=read", str(result["blockReason"]))
