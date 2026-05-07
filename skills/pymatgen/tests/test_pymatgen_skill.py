from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_ROOT / "scripts" / "structure_summary.py"


def run_script(request: dict) -> dict:
    with tempfile.TemporaryDirectory(prefix="pymatgen-skill-test-") as temp_dir_name:
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


class PymatgenWrapperTests(unittest.TestCase):
    def test_structure_fixture_success(self) -> None:
        payload = run_script(
            {
                "structure_fixture": {
                    "formula": "Si",
                    "space_group": "Fd-3m",
                    "lattice": {"a": 5.43, "b": 5.43, "c": 5.43, "alpha": 90, "beta": 90, "gamma": 90},
                    "sites": [{"species": "Si"}, {"species": "Si"}],
                    "coordination_environments": [{"site": 0, "environment": "tetrahedral"}],
                }
            }
        )

        self.assertEqual(payload["status"], "success")
        result = payload["primary_result"]
        self.assertEqual(result["formula"], "Si")
        self.assertEqual(result["site_count"], 2)
        self.assertEqual(result["species"], ["Si"])
        self.assertEqual(result["coordination_environments"][0]["environment"], "tetrahedral")

    def test_missing_dependency_is_structured(self) -> None:
        payload = run_script({"structure_path": "/tmp/nonexistent.cif"})

        self.assertEqual(payload["status"], "error")
        if payload["provider_health"].get("pymatgen", {}).get("status") == "missing_dependency":
            self.assertEqual(payload["errors"][0]["code"], "missing_dependency")
        else:
            self.assertIn(payload["errors"][0]["code"], {"unexpected_error"})
