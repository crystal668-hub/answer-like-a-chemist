from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_ROOT / "scripts" / "bioactivity_query.py"


def run_script(request: dict) -> dict:
    with tempfile.TemporaryDirectory(prefix="chembl-skill-test-") as temp_dir_name:
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


class ChemblWrapperTests(unittest.TestCase):
    def test_activity_fixture_success(self) -> None:
        payload = run_script(
            {
                "target": "EGFR",
                "standard_type": "IC50",
                "max_standard_value": 100,
                "standard_units": "nM",
                "activity_fixture": [
                    {
                        "molecule_chembl_id": "CHEMBL553",
                        "target_chembl_id": "CHEMBL203",
                        "standard_type": "IC50",
                        "standard_value": "12",
                        "standard_units": "nM",
                    },
                    {
                        "molecule_chembl_id": "CHEMBL939",
                        "target_chembl_id": "CHEMBL203",
                        "standard_type": "IC50",
                        "standard_value": "78",
                        "standard_units": "nM",
                    },
                ],
            }
        )

        self.assertEqual(payload["status"], "success")
        result = payload["primary_result"]
        self.assertEqual(result["activity_count"], 2)
        self.assertEqual(result["best_activity"]["molecule_chembl_id"], "CHEMBL553")
        self.assertEqual(result["target_chembl_ids"], ["CHEMBL203"])

    def test_missing_dependency_is_structured(self) -> None:
        payload = run_script({"target": "", "standard_type": "IC50", "max_standard_value": 100})

        self.assertEqual(payload["status"], "error")
        if payload["provider_health"].get("chembl-database", {}).get("status") == "missing_dependency":
            self.assertEqual(payload["errors"][0]["code"], "missing_dependency")
        else:
            self.assertIn(payload["errors"][0]["code"], {"missing_target", "provider_error"})
