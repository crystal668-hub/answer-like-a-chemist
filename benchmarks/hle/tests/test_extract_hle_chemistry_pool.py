from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


TEST_ROOT = Path(__file__).resolve().parent
FIXTURES_DIR = TEST_ROOT / "fixtures"
MODULE_PATH = TEST_ROOT.parent / "extract_hle_chemistry_pool.py"
MODULE_NAME = "extract_hle_chemistry_pool_under_test"

SPEC = importlib.util.spec_from_file_location(MODULE_NAME, MODULE_PATH)
assert SPEC and SPEC.loader
extract_module = importlib.util.module_from_spec(SPEC)
sys.modules[MODULE_NAME] = extract_module
SPEC.loader.exec_module(extract_module)


class HLEChemistryExtractionTests(unittest.TestCase):
    def test_extract_pool_filters_chemistry_and_keeps_hle_fields(self) -> None:
        records, counts = extract_module.extract_pool_from_jsonl(FIXTURES_DIR / "hle_rows.jsonl")

        self.assertEqual(
            {
                "scanned_source_rows": 3,
                "selected_records": 2,
                "excluded_non_chemistry": 1,
                "excluded_missing_question": 0,
                "excluded_missing_answer": 0,
            },
            counts,
        )
        self.assertEqual(["hle-chemistry-hle-chem-1", "hle-chemistry-hle-chem-2"], [record["id"] for record in records])
        self.assertEqual(["hle", "hle"], [record["eval_kind"] for record in records])
        self.assertEqual(["short-answer", "multiple-choice"], [record["answer_type"] for record in records])
        self.assertEqual("https://example.test/img.png", records[1]["image"])
        self.assertEqual("hle-chem-2", records[1]["source_id"])

    def test_cli_writes_jsonl_and_manifest_from_jsonl_input(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            output_jsonl = temp_dir / "hle_chemistry_pool.jsonl"

            completed = subprocess.run(
                [
                    sys.executable,
                    str(MODULE_PATH),
                    "--input-jsonl",
                    str(FIXTURES_DIR / "hle_rows.jsonl"),
                    "--output-jsonl",
                    str(output_jsonl),
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(0, completed.returncode, msg=completed.stderr)
            payload = json.loads(completed.stdout)
            manifest_path = output_jsonl.resolve().with_suffix(".manifest.json")

            self.assertEqual(output_jsonl.resolve().as_posix(), payload["output_jsonl"])
            self.assertEqual(manifest_path.as_posix(), payload["manifest"])
            records = [json.loads(line) for line in output_jsonl.read_text(encoding="utf-8").splitlines()]
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

            self.assertEqual(2, len(records))
            self.assertEqual("cais/hle", manifest["source_dataset"])
            self.assertEqual("hle-chemistry-pool-v1", manifest["field_contract_version"])
            self.assertEqual(2, manifest["counts"]["selected_records"])


if __name__ == "__main__":
    unittest.main()
