from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_ROOT / "scripts" / "trajectory_summary.py"


def run_script(request: dict) -> dict:
    with tempfile.TemporaryDirectory(prefix="md-skill-test-") as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        request_path = temp_dir / "request.json"
        output_dir = temp_dir / "out"
        request_path.write_text(json.dumps(request), encoding="utf-8")
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), "--request-json", str(request_path), "--output-dir", str(output_dir), "--json"],
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(completed.stdout)
        assert payload == json.loads((output_dir / "result.json").read_text(encoding="utf-8"))
        return payload


class MolecularDynamicsWrapperTests(unittest.TestCase):
    def test_trajectory_fixture_success(self) -> None:
        payload = run_script(
            {
                "trajectory_fixture": {
                    "frames": [
                        [[0, 0, 0], [1, 0, 0]],
                        [[0, 0, 0], [2, 0, 0]],
                    ],
                    "rmsf": True,
                    "contact_map": True,
                }
            }
        )

        self.assertEqual(payload["status"], "success")
        result = payload["primary_result"]
        self.assertEqual(result["frame_count"], 2)
        self.assertEqual(result["atom_count"], 2)
        self.assertAlmostEqual(result["rmsd"]["final"], 2 ** -0.5, places=6)

    def test_missing_dependency_is_structured(self) -> None:
        payload = run_script({"topology_path": "/tmp/topology.pdb", "trajectory_path": "/tmp/traj.dcd"})

        self.assertEqual(payload["status"], "error")
        if payload["provider_health"].get("molecular-dynamics", {}).get("status") == "missing_dependency":
            self.assertEqual(payload["errors"][0]["code"], "missing_dependency")
        else:
            self.assertIn(payload["errors"][0]["code"], {"missing_input_file", "parse_error"})
