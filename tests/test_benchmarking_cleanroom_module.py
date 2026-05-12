from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from benchmarking.runtime import cleanroom


class CleanroomModuleTests(unittest.TestCase):
    def test_cleanup_manifest_path_delegates_to_runtime_lease(self) -> None:
        class RuntimeLease:
            @staticmethod
            def manifest_path(output_root: Path, run_id: str) -> Path:
                return output_root / "cleanroom" / "manifests" / f"{run_id}.manifest.json"

        with tempfile.TemporaryDirectory() as tmpdir:
            output_root = Path(tmpdir)
            path = cleanroom.cleanup_manifest_path(
                output_root,
                "demo-run",
                cleanroom_runtime_lease=RuntimeLease,
                cleanroom_root=Path(tmpdir) / "skill",
            )

            self.assertEqual(output_root / "cleanroom" / "manifests" / "demo-run.manifest.json", path)

    def test_pending_manifest_registry_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / "demo.manifest.json"
            cleanroom.register_pending_cleanup_manifest(manifest_path, cleanup_callback=lambda: [])
            try:
                self.assertIn(manifest_path, cleanroom.iter_pending_cleanup_manifests())
            finally:
                cleanroom.unregister_pending_cleanup_manifest(manifest_path)
            self.assertNotIn(manifest_path, cleanroom.iter_pending_cleanup_manifests())

    def test_invoke_cleanroom_cleanup_uses_injected_python_and_runner(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            captured: dict[str, object] = {}
            manifest_path = Path(tmpdir) / "demo.manifest.json"
            manifest_path.write_text("{}", encoding="utf-8")

            def fake_run_subprocess(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
                captured["command"] = list(command)
                captured["kwargs"] = dict(kwargs)
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout=json.dumps({"success": True}),
                    stderr="",
                )

            payload = cleanroom.invoke_cleanroom_cleanup(
                manifest_path=manifest_path,
                cleanroom_root=Path(tmpdir) / "benchmark-cleanroom",
                current_python=lambda: "/tmp/fake-venv/bin/python",
                run_subprocess=fake_run_subprocess,
            )

            self.assertTrue(payload["success"])
            command = captured["command"]
            assert isinstance(command, list)
            self.assertEqual("/tmp/fake-venv/bin/python", command[0])
            self.assertIn("cleanup_benchmark_run.py", command[1])


if __name__ == "__main__":
    unittest.main()
