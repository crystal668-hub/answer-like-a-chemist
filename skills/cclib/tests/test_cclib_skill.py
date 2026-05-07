from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_ROOT / "scripts" / "parse_output.py"
EXPECTED_TOP_LEVEL_KEYS = {
    "status",
    "request",
    "primary_result",
    "candidates",
    "diagnostics",
    "warnings",
    "errors",
    "tool_trace",
    "source_trace",
    "provider_health",
}


def run_script(request: dict) -> dict:
    with tempfile.TemporaryDirectory(prefix="cclib-skill-test-") as temp_dir_name:
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


class CclibWrapperTests(unittest.TestCase):
    def test_fixture_summary_success(self) -> None:
        payload = run_script(
            {
                "parsed_fixture": {
                    "package": "Gaussian",
                    "natom": 3,
                    "scfenergies": [-2050.1, -2060.25],
                    "homos": [4],
                    "moenergies": [[-20.0, -10.0, 1.0]],
                    "vibfreqs": [3657.0, 1595.0, -42.0],
                    "metadata": {"success": True},
                }
            }
        )

        self.assertTrue(EXPECTED_TOP_LEVEL_KEYS.issubset(payload))
        self.assertEqual(payload["status"], "success")
        result = payload["primary_result"]
        self.assertEqual(result["package"], "Gaussian")
        self.assertEqual(result["final_scf_energy_ev"], -2060.25)
        self.assertEqual(result["vibrational_frequency_count"], 3)
        self.assertEqual(result["imaginary_frequencies"], [-42.0])

    def test_missing_dependency_is_structured(self) -> None:
        payload = run_script({"output_path": "/tmp/nonexistent-gaussian.log"})

        self.assertEqual(payload["status"], "error")
        if payload["provider_health"].get("cclib", {}).get("status") == "missing_dependency":
            self.assertEqual(payload["errors"][0]["code"], "missing_dependency")
        else:
            self.assertIn(payload["errors"][0]["code"], {"missing_input_file", "parse_failed", "parse_error"})
