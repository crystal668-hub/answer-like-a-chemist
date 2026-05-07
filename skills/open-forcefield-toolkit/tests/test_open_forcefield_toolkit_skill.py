from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_ROOT / "scripts" / "parameterize_molecule.py"


def run_script(request: dict) -> dict:
    with tempfile.TemporaryDirectory(prefix="openff-skill-test-") as temp_dir_name:
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


class OpenForcefieldWrapperTests(unittest.TestCase):
    def test_dry_run_plan_success(self) -> None:
        payload = run_script(
            {
                "smiles": "CCO",
                "force_field": "openff-2.0.0.offxml",
                "partial_charge_method": "am1bcc",
                "output_formats": ["openmm"],
                "execute": False,
            }
        )

        self.assertEqual(payload["status"], "success")
        result = payload["primary_result"]
        self.assertEqual(result["mode"], "dry_run")
        self.assertEqual(result["parameterization_plan"]["partial_charge_method"], "am1bcc")
        self.assertTrue(result["requires_openff_toolkit"])

    def test_missing_dependency_is_structured_for_execution(self) -> None:
        payload = run_script({"smiles": "CCO", "execute": True})

        self.assertEqual(payload["status"], "error")
        if payload["provider_health"].get("open-forcefield-toolkit", {}).get("status") == "missing_dependency":
            self.assertEqual(payload["errors"][0]["code"], "missing_dependency")
        else:
            self.assertIn(payload["errors"][0]["code"], {"unexpected_error"})
