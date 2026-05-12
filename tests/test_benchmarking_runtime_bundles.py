from __future__ import annotations

import base64
import os
import tempfile
import unittest
from pathlib import Path

from benchmarking.core.datasets import BenchmarkRecord
from benchmarking.runtime.bundles import (
    RuntimeBundleError,
    _superchem_asset_cache_relative_path,
    ensure_runtime_bundle,
)


class RuntimeBundleTests(unittest.TestCase):
    def test_superchem_bundle_uses_only_visible_image_locators(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            data_dir = temp_dir / "data"
            assets_dir = temp_dir / "assets"
            locator = "/media/uploads/question-visible.png"
            visible_image = assets_dir / _superchem_asset_cache_relative_path(locator)
            visible_image.parent.mkdir(parents=True)
            visible_image.write_bytes(b"visible")
            noisy_image = assets_dir / "_shared" / "noise" / "unused.png"
            noisy_image.parent.mkdir(parents=True)
            noisy_image.write_bytes(b"noise")

            record = BenchmarkRecord(
                record_id="superchem-visible-question-mm",
                dataset="superchem",
                source_file=str(data_dir / "superchem.jsonl"),
                eval_kind="superchem_multiple_choice_rpf",
                prompt=f"Question ![q]({locator})",
                reference_answer="A",
                payload={
                    "source_uuid": "uuid-visible-question",
                    "modality": "multimodal",
                    "question": f"Question ![q]({locator})",
                    "options": {"A": "answer"},
                    "question_image_paths": [
                        os.path.relpath(noisy_image, start=data_dir).replace(os.sep, "/"),
                        os.path.relpath(visible_image, start=data_dir).replace(os.sep, "/"),
                    ],
                    "option_image_paths": {},
                },
            )

            bundle = ensure_runtime_bundle(record, bundle_root=temp_dir / "bundles")

            assert bundle is not None
            markdown = bundle.question_markdown.read_text(encoding="utf-8")
            self.assertEqual(1, len(bundle.image_files))
            self.assertEqual(b"visible", bundle.image_files[0].read_bytes())
            self.assertNotIn(locator, markdown)
            self.assertIn("](images/img01.png)", markdown)

    def test_hle_bundle_materializes_base64_image(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            data_uri = "data:image/png;base64," + base64.b64encode(b"png-bytes").decode("ascii")
            record = BenchmarkRecord(
                record_id="hle-chemistry-demo",
                dataset="hle",
                source_file="/tmp/hle.jsonl",
                eval_kind="hle",
                prompt="Using the provided information, identify the step.",
                reference_answer="Final step",
                payload={
                    "question": "Using the provided information, identify the step.",
                    "answer": "Final step",
                    "image": data_uri,
                },
            )

            bundle = ensure_runtime_bundle(record, bundle_root=temp_dir / "bundles")

            assert bundle is not None
            self.assertEqual(1, len(bundle.image_files))
            self.assertEqual(b"png-bytes", bundle.image_files[0].read_bytes())
            self.assertIn("images/hle-image-01.png", bundle.question_markdown.read_text(encoding="utf-8"))

    def test_superchem_bundle_rejects_missing_visible_locator(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_name:
            locator = "/media/uploads/missing-visible.png"
            record = BenchmarkRecord(
                record_id="superchem-missing-visible-mm",
                dataset="superchem",
                source_file=str(Path(temp_dir_name) / "data" / "superchem.jsonl"),
                eval_kind="superchem_multiple_choice_rpf",
                prompt=f"Question ![q]({locator})",
                reference_answer="A",
                payload={
                    "source_uuid": "uuid-missing-visible",
                    "modality": "multimodal",
                    "question": f"Question ![q]({locator})",
                    "options": {"A": "answer"},
                    "question_image_paths": [],
                    "option_image_paths": {},
                },
            )

            with self.assertRaisesRegex(RuntimeBundleError, "missing-visible"):
                ensure_runtime_bundle(record, bundle_root=Path(temp_dir_name) / "bundles")


if __name__ == "__main__":
    unittest.main()
