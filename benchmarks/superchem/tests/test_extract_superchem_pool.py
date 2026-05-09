from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


TEST_ROOT = Path(__file__).resolve().parent
MODULE_PATH = TEST_ROOT.parent / "extract_superchem_pool.py"
MODULE_NAME = "extract_superchem_pool_under_test"

SPEC = importlib.util.spec_from_file_location(MODULE_NAME, MODULE_PATH)
assert SPEC and SPEC.loader
extract_module = importlib.util.module_from_spec(SPEC)
sys.modules[MODULE_NAME] = extract_module
SPEC.loader.exec_module(extract_module)


class SuperChemExtractionTests(unittest.TestCase):
    def fake_fetch_json(self, url: str, *, timeout_seconds: int) -> dict[str, object]:
        self.assertGreater(timeout_seconds, 0)
        if "/splits?" in url:
            return {
                "splits": [
                    {"dataset": "ZehuaZhao/SUPERChem", "config": "default", "split": "train"},
                ]
            }
        if "/rows?" in url and "offset=0" in url:
            return {
                "num_rows_total": 4,
                "rows": [
                    {
                        "row_idx": 0,
                        "row": {
                            "uuid": "mcq-with-images",
                            "question_type": "multiple_choice",
                            "question_en": "Which reagent gives the shown product? ![question](/media/uploads/question.png)",
                            "options_en": {
                                "A": "NaBH4",
                                "B": "LiAlH4 ![option-b](/media/uploads/option-b.png)",
                                "C": "PCC",
                            },
                            "answer_en": ["B"],
                            "explanation_en": (
                                "<Checkpoint id='1'>Identify the reduction pattern.</Checkpoint> "
                                "![expl](/media/uploads/expl.png)"
                            ),
                            "question_images": {"/media/uploads/question.png": None},
                            "options_images": {"B": {"/media/uploads/option-b.png": None}},
                            "explanation_images": {"/media/uploads/expl.png": None},
                            "canary": "superchem-canary",
                        },
                    },
                    {
                        "row_idx": 1,
                        "row": {
                            "uuid": "mcq-text-only",
                            "question_type": "multiple_choice",
                            "question_en": "Which atom has the highest electronegativity?",
                            "options_en": {"A": "F", "B": "O", "C": "N"},
                            "answer_en": ["A"],
                            "explanation_en": "<Checkpoint id='1'>Compare periodic trends.</Checkpoint>",
                            "question_images": None,
                            "options_images": None,
                            "explanation_images": None,
                        },
                    },
                    {
                        "row_idx": 2,
                        "row": {
                            "uuid": "missing-checkpoints",
                            "question_type": "multiple_choice",
                            "question_en": "Should be excluded",
                            "options_en": {"A": "1", "B": "2"},
                            "answer_en": ["A"],
                            "explanation_en": "No checkpoint tags here",
                        },
                    },
                    {
                        "row_idx": 3,
                        "row": {
                            "uuid": "not-mcq",
                            "question_type": "open_ended",
                            "question_en": "Should be excluded",
                            "options_en": {"A": "1", "B": "2"},
                            "answer_en": ["A"],
                            "explanation_en": "<Checkpoint id='1'>Ignore.</Checkpoint>",
                        },
                    },
                ],
            }
        if "/rows?" in url and "offset=4" in url:
            return {
                "num_rows_total": 4,
                "rows": [],
            }
        raise AssertionError(f"Unexpected URL: {url}")

    def fake_download_binary(self, url: str, *, timeout_seconds: int) -> bytes:
        self.assertGreater(timeout_seconds, 0)
        return f"binary-for::{url}".encode("utf-8")

    def test_extract_image_urls_supports_dict_keys_and_superchem_host_resolution(self) -> None:
        urls = extract_module.extract_image_urls({"/media/uploads/example.png": None})
        self.assertEqual(["/media/uploads/example.png"], urls)
        self.assertEqual(
            "https://superchem.pku.edu.cn/media/uploads/example.png",
            extract_module.absolutize_url("/media/uploads/example.png"),
        )

    def test_normalize_option_images_supports_shared_image_maps(self) -> None:
        payload = {
            "/media/uploads/option-a.png": None,
            "/media/uploads/option-b.png": None,
        }
        self.assertEqual(
            {
                "_shared": [
                    "/media/uploads/option-a.png",
                    "/media/uploads/option-b.png",
                ]
            },
            extract_module.normalize_option_images(payload, ["A", "B", "C"]),
        )

    def test_extract_image_urls_from_record_text_uses_only_inline_references(self) -> None:
        text = (
            "Question <MultiModal>![reactant](/media/uploads/q.png)</MultiModal> "
            "again ![duplicate](/media/uploads/q.png) "
            "and absolute ![product](https://superchem.pku.edu.cn/media/uploads/product.jpg)."
        )

        self.assertEqual(
            [
                "/media/uploads/q.png",
                "https://superchem.pku.edu.cn/media/uploads/product.jpg",
            ],
            extract_module.extract_image_urls_from_record_text(text),
        )

    def test_transform_row_ignores_unreferenced_shared_image_maps(self) -> None:
        row = {
            "uuid": "shared-map-noise",
            "question_type": "multiple_choice",
            "question_en": "Pick the product. ![q](/media/uploads/q.png)",
            "options_en": {
                "A": "No image",
                "B": "<MultiModal>![b](/media/uploads/b.png)</MultiModal>",
                "C": "No image",
            },
            "answer_en": ["B"],
            "explanation_en": (
                "<Checkpoint id='1'>Choose the cyclized product.</Checkpoint> "
                "![reason](/media/uploads/reason.png)"
            ),
            "question_images": {
                "/media/uploads/q.png": None,
                "/media/uploads/unrelated-1.png": None,
                "/media/uploads/unrelated-2.png": None,
            },
            "options_images": {
                "/media/uploads/b.png": None,
                "/media/uploads/unrelated-option.png": None,
            },
            "explanation_images": {
                "/media/uploads/reason.png": None,
                "/media/uploads/unrelated-expl.png": None,
            },
            "canary": "superchem-canary",
        }

        with tempfile.TemporaryDirectory() as temp_dir_name:
            assets_dir = Path(temp_dir_name) / "assets"
            output_base_dir = Path(temp_dir_name) / "data"
            with mock.patch.object(extract_module, "download_binary", side_effect=self.fake_download_binary):
                records, stats = extract_module.transform_row(
                    dataset="ZehuaZhao/SUPERChem",
                    config="default",
                    split="train",
                    row_idx=0,
                    row=row,
                    assets_dir=assets_dir,
                    output_base_dir=output_base_dir,
                    timeout_seconds=30,
                )

        self.assertEqual(1, len(records))
        record = records[0]
        expected_q = str(Path("..") / "assets" / extract_module.cache_relative_path_for_url("/media/uploads/q.png"))
        expected_b = str(Path("..") / "assets" / extract_module.cache_relative_path_for_url("/media/uploads/b.png"))
        expected_reason = str(Path("..") / "assets" / extract_module.cache_relative_path_for_url("/media/uploads/reason.png"))
        self.assertEqual([expected_q], record["question_image_paths"])
        self.assertEqual({"B": [expected_b]}, record["option_image_paths"])
        self.assertEqual([expected_reason], record["explanation_image_paths"])
        self.assertEqual(1, stats["question_image_references"])
        self.assertEqual(1, stats["option_image_references"])
        self.assertEqual(1, stats["explanation_image_references"])

    def test_extract_pool_generates_multimodal_records_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_name:
            assets_dir = Path(temp_dir_name) / "assets"
            output_base_dir = Path(temp_dir_name) / "data"
            with mock.patch.object(extract_module, "fetch_json", side_effect=self.fake_fetch_json):
                with mock.patch.object(extract_module, "download_binary", side_effect=self.fake_download_binary):
                    records, stats = extract_module.extract_pool(
                        dataset="ZehuaZhao/SUPERChem",
                        assets_dir=assets_dir,
                        page_size=100,
                        timeout_seconds=30,
                        output_base_dir=output_base_dir,
                    )

        self.assertEqual(4, stats["scanned_source_rows"])
        self.assertEqual(1, stats["selected_source_rows"])
        self.assertEqual(1, stats["selected_records"])
        self.assertEqual(1, stats["multimodal_records"])
        self.assertEqual(1, stats["excluded_missing_checkpoint_tags"])
        self.assertEqual(1, stats["excluded_non_multiple_choice"])
        self.assertEqual(1, stats["excluded_missing_images_for_multimodal"])
        self.assertEqual(1, stats["source_rows_with_images"])
        self.assertEqual(
            ["superchem-mcq-with-images-mm"],
            [record["id"] for record in records],
        )

        multimodal = next(record for record in records if record["id"] == "superchem-mcq-with-images-mm")
        self.assertEqual("multimodal", multimodal["modality"])
        self.assertTrue(multimodal["has_images"])
        self.assertEqual("B", multimodal["answer"])
        self.assertEqual(
            "Which reagent gives the shown product? ![question](/media/uploads/question.png)\n\n"
            "Options:\nA. NaBH4\nB. LiAlH4 ![option-b](/media/uploads/option-b.png)\nC. PCC",
            multimodal["prompt"],
        )
        expected_question = str(Path("..") / "assets" / extract_module.cache_relative_path_for_url("/media/uploads/question.png"))
        expected_option = str(Path("..") / "assets" / extract_module.cache_relative_path_for_url("/media/uploads/option-b.png"))
        expected_explanation = str(Path("..") / "assets" / extract_module.cache_relative_path_for_url("/media/uploads/expl.png"))
        self.assertEqual([expected_question], multimodal["question_image_paths"])
        self.assertEqual({"B": [expected_option]}, multimodal["option_image_paths"])
        self.assertEqual([expected_explanation], multimodal["explanation_image_paths"])

    def test_main_writes_jsonl_manifest_and_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            output_jsonl = temp_dir / "data" / "superchem_pool.jsonl"
            assets_dir = temp_dir / "assets"

            with mock.patch.object(extract_module, "fetch_json", side_effect=self.fake_fetch_json):
                with mock.patch.object(extract_module, "download_binary", side_effect=self.fake_download_binary):
                    with mock.patch.object(
                        sys,
                        "argv",
                        [
                            str(MODULE_PATH),
                            "--output-jsonl",
                            str(output_jsonl),
                            "--assets-dir",
                            str(assets_dir),
                        ],
                    ):
                        completed = extract_module.main()

            self.assertEqual(0, completed)
            manifest_path = output_jsonl.with_suffix(".manifest.json")
            self.assertTrue(output_jsonl.is_file())
            self.assertTrue(manifest_path.is_file())
            self.assertTrue(
                (assets_dir / extract_module.cache_relative_path_for_url("/media/uploads/question.png")).is_file()
            )
            self.assertTrue(
                (assets_dir / extract_module.cache_relative_path_for_url("/media/uploads/option-b.png")).is_file()
            )
            self.assertTrue(
                (assets_dir / extract_module.cache_relative_path_for_url("/media/uploads/expl.png")).is_file()
            )

            records = [json.loads(line) for line in output_jsonl.read_text(encoding="utf-8").splitlines() if line.strip()]
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

            self.assertEqual(1, len(records))
            self.assertEqual("ZehuaZhao/SUPERChem", manifest["source_dataset"])
            self.assertEqual("superchem-pool-v1", manifest["field_contract_version"])
            self.assertEqual("superchem_pool.jsonl", manifest["output_path"])
            self.assertEqual("../assets", manifest["assets_dir"])
            self.assertEqual(1, manifest["counts"]["selected_source_rows"])
            self.assertEqual(0, manifest["counts"]["text_only_records"])
            self.assertEqual(1, manifest["counts"]["multimodal_records"])
            self.assertEqual(1, manifest["counts"]["excluded_missing_images_for_multimodal"])
            self.assertEqual(1, manifest["counts"]["downloaded_question_images"])
            self.assertEqual(1, manifest["counts"]["downloaded_option_images"])
            self.assertEqual(1, manifest["counts"]["downloaded_explanation_images"])
            self.assertTrue(any("text_only (-txt) SUPERChem benchmark records were removed" in note for note in manifest["notes"]))


if __name__ == "__main__":
    unittest.main()
