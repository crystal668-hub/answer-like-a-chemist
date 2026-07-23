# Benchmark Inputs and Runner Convergence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the temp SUPERChem image path failure, repair SUPERChem image extraction at the source, and add benchmark runner-level convergence/recovery controls for all benchmark records.

**Architecture:** Treat visual input portability as a data contract: temp benchmark paths are rewritten to relative workspace paths, and future SUPERChem extraction records only the images actually referenced by each record. Treat convergence as a runner contract rather than a prompt preference: each runner receives a `ConvergencePolicy`, writes it into metadata, and enforces wall-clock/status recovery limits while preserving the model's tool and turn exploration space.

**Tech Stack:** Python 3.12, `uv`, `pytest`/`unittest`, JSONL, OpenClaw CLI, existing `benchmarking` package.

---

## Scope And Constraints

- Canonical project root is `/Users/xutao/.openclaw/workspace`.
- Before implementation, read `/Users/xutao/.openclaw/workspace/GLOBAL_DEV_SPEC.md`.
- Use current code as source of truth if this plan and code differ.
- This plan intentionally follows these required directions:
  - Rewrite temp benchmark SUPERChem absolute image paths to relative `../../../benchmarks/superchem/assets/...`.
  - Modify `benchmarks/superchem/extract_superchem_pool.py` so image locators come from current record text (`question`, `options`, and, when needed, `reference_reasoning`), not recursive extraction of full `question_images` or `options_images` blobs.
  - Add architecture-level convergence controls for all benchmark records, not only HLE and not only prompt text. At this stage, do not enforce hard `max_tool_calls` or `max_turns` limits; record tool/turn counts as diagnostics only.
- Do not add runtime fallback that silently remaps stale `/home/dministrator/...` paths. The data layer should be fixed; runtime should keep failing fast on invalid absolute SUPERChem paths.
- After code/test changes, run relevant tests first, then commit the changes.

## File Structure

- Modify: `/Users/xutao/.openclaw/workspace/temp-benchmarks/superchem/data/superchem_pool.jsonl`
  - One-time repair of selected temp records. Replace stale absolute `/home/dministrator/.openclaw/benchmarks/superchem/assets/...` paths with relative `../../../benchmarks/superchem/assets/...` paths.

- Create: `/Users/xutao/.openclaw/workspace/scripts/repair_temp_superchem_image_paths.py`
  - Idempotent utility for the one-time temp benchmark rewrite. It should be narrow and explicit so the temp benchmark can be repaired reproducibly.

- Modify: `/Users/xutao/.openclaw/workspace/benchmarks/superchem/extract_superchem_pool.py`
  - Add text-based image locator parsing.
  - Stop deriving question/option image paths by recursively traversing full source image maps.
  - Preserve source image maps only as diagnostics/source metadata.

- Modify: `/Users/xutao/.openclaw/workspace/benchmarks/superchem/tests/test_extract_superchem_pool.py`
  - Add tests that prove shared image maps with hundreds of unrelated images do not pollute record-local image paths.

- Create: `/Users/xutao/.openclaw/workspace/benchmarking/convergence.py`
  - Shared convergence policy dataclass, metadata helpers, transcript parsing helpers, and runner-level recovery outcome helpers.

- Modify: `/Users/xutao/.openclaw/workspace/benchmarking/runners/single_llm.py`
  - Accept `ConvergencePolicy`.
  - Pass timeout/finalization policy values to the OpenClaw wrapper.
  - Convert timeout transcript recovery outcomes into structured recovered answers without failing records solely because of high tool/turn counts.

- Modify: `/Users/xutao/.openclaw/workspace/benchmarking/single_llm_openclaw_wrapper.py`
  - Accept convergence CLI flags.
  - Add session transcript inspection.
  - If OpenClaw times out or reaches a blocked state, inspect the transcript for a final-answer-complete assistant message before returning an empty timeout.
  - Emit convergence metadata into `result.meta`.

- Modify: `/Users/xutao/.openclaw/workspace/benchmarking/runners/chemqa.py`
  - Accept the same `ConvergencePolicy`.
  - Enforce global wall-clock, max unchanged status polls, and max recovery attempts for ChemQA runs across all records.
  - Emit convergence metadata in `runner_meta`.

- Modify: `/Users/xutao/.openclaw/workspace/benchmark_test.py`
  - Add CLI flags for convergence policy.
  - Construct and pass policy to both single-agent and ChemQA runners.
  - Add policy metadata to run manifests/results.

- Modify: `/Users/xutao/.openclaw/workspace/benchmarking/prompts.py`
  - Keep existing time-budget wording, but make it a reflection of the runner policy. The prompt remains advisory; the new enforcement lives in runners/wrapper.

- Modify: `/Users/xutao/.openclaw/workspace/tests/test_benchmark_test.py`
  - Cover temp path repair helper if imported through script or direct subprocess.
  - Cover CLI parsing and policy propagation.

- Modify: `/Users/xutao/.openclaw/workspace/tests/test_single_llm_session_wrapper.py`
  - Cover convergence flags and transcript recovery metadata.

- Create: `/Users/xutao/.openclaw/workspace/tests/test_benchmark_convergence.py`
  - Unit tests for `ConvergencePolicy`, transcript answer extraction, and metadata serialization.

- Modify: `/Users/xutao/.openclaw/workspace/GLOBAL_DEV_SPEC.md`
  - Update after implementation because runner behavior, execution flow, result metadata, and SUPERChem extraction behavior will change.

---

## Design Detail 1: Temp SUPERChem Path Rewrite

### Data Contract

Current temp records contain paths such as:

```text
/home/dministrator/.openclaw/benchmarks/superchem/assets/_shared/c8/c8431ecf8f1f72412875714aad1de3e187d52513.png
```

They must become:

```text
../../../benchmarks/superchem/assets/_shared/c8/c8431ecf8f1f72412875714aad1de3e187d52513.png
```

Reasoning:

- The record JSONL lives at `/Users/xutao/.openclaw/workspace/temp-benchmarks/superchem/data/superchem_pool.jsonl`.
- `benchmark_test._resolve_record_relative_image_path()` resolves relative image paths from the JSONL parent directory.
- From `temp-benchmarks/superchem/data/`, the path to canonical assets is `../../../benchmarks/superchem/assets/...`.
- This preserves the runtime rule that SUPERChem paths must be relative and portable.

### Task 1: Add Reproducible Temp Path Repair Script

**Files:**
- Create: `/Users/xutao/.openclaw/workspace/scripts/repair_temp_superchem_image_paths.py`
- Test: `/Users/xutao/.openclaw/workspace/tests/test_benchmark_test.py`

- [ ] **Step 1: Write the failing test**

Add this test to `tests/test_benchmark_test.py`:

```python
    def test_repair_temp_superchem_image_paths_rewrites_stale_absolute_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            jsonl_path = root / "temp-benchmarks" / "superchem" / "data" / "superchem_pool.jsonl"
            jsonl_path.parent.mkdir(parents=True)
            record = {
                "id": "superchem-demo-mm",
                "question_image_paths": [
                    "/home/dministrator/.openclaw/benchmarks/superchem/assets/_shared/aa/demo.png"
                ],
                "option_image_paths": {
                    "B": [
                        "/home/dministrator/.openclaw/benchmarks/superchem/assets/_shared/bb/option.png"
                    ]
                },
                "explanation_image_paths": [
                    "/home/dministrator/.openclaw/benchmarks/superchem/assets/_shared/cc/expl.png"
                ],
            }
            jsonl_path.write_text(json.dumps(record) + "\n", encoding="utf-8")

            script_path = MODULE_PATH.parent / "scripts" / "repair_temp_superchem_image_paths.py"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(script_path),
                    "--jsonl",
                    str(jsonl_path),
                    "--old-prefix",
                    "/home/dministrator/.openclaw/benchmarks/superchem/assets",
                    "--new-prefix",
                    "../../../benchmarks/superchem/assets",
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(0, completed.returncode, completed.stderr)
            repaired = json.loads(jsonl_path.read_text(encoding="utf-8"))
            self.assertEqual(
                ["../../../benchmarks/superchem/assets/_shared/aa/demo.png"],
                repaired["question_image_paths"],
            )
            self.assertEqual(
                {"B": ["../../../benchmarks/superchem/assets/_shared/bb/option.png"]},
                repaired["option_image_paths"],
            )
            self.assertEqual(
                ["../../../benchmarks/superchem/assets/_shared/cc/expl.png"],
                repaired["explanation_image_paths"],
            )
            summary = json.loads(completed.stdout)
            self.assertEqual(3, summary["rewritten_paths"])
            self.assertEqual(1, summary["records_seen"])
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
uv run pytest /Users/xutao/.openclaw/workspace/tests/test_benchmark_test.py::BenchmarkTestModuleTests::test_repair_temp_superchem_image_paths_rewrites_stale_absolute_paths -q
```

Expected: FAIL because `/Users/xutao/.openclaw/workspace/scripts/repair_temp_superchem_image_paths.py` does not exist.

- [ ] **Step 3: Implement the script**

Create `/Users/xutao/.openclaw/workspace/scripts/repair_temp_superchem_image_paths.py`:

```python
#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


IMAGE_PATH_FIELDS = ("question_image_paths", "explanation_image_paths")
OPTION_IMAGE_PATH_FIELD = "option_image_paths"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rewrite stale absolute SUPERChem temp benchmark image paths.")
    parser.add_argument(
        "--jsonl",
        type=Path,
        default=Path("/Users/xutao/.openclaw/workspace/temp-benchmarks/superchem/data/superchem_pool.jsonl"),
        help="SUPERChem temp benchmark JSONL to rewrite in place.",
    )
    parser.add_argument(
        "--old-prefix",
        default="/home/dministrator/.openclaw/benchmarks/superchem/assets",
        help="Stale absolute assets prefix to replace.",
    )
    parser.add_argument(
        "--new-prefix",
        default="../../../benchmarks/superchem/assets",
        help="Relative assets prefix to write into the JSONL.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Report changes without writing the JSONL.")
    return parser.parse_args()


def rewrite_path(value: Any, *, old_prefix: str, new_prefix: str) -> tuple[Any, int]:
    if not isinstance(value, str):
        return value, 0
    if value == old_prefix:
        return new_prefix, 1
    prefix = old_prefix.rstrip("/") + "/"
    if value.startswith(prefix):
        return new_prefix.rstrip("/") + "/" + value[len(prefix):], 1
    return value, 0


def rewrite_record(record: dict[str, Any], *, old_prefix: str, new_prefix: str) -> tuple[dict[str, Any], int]:
    updated = dict(record)
    changes = 0
    for field in IMAGE_PATH_FIELDS:
        values = updated.get(field)
        if not isinstance(values, list):
            continue
        rewritten = []
        for item in values:
            new_item, changed = rewrite_path(item, old_prefix=old_prefix, new_prefix=new_prefix)
            rewritten.append(new_item)
            changes += changed
        updated[field] = rewritten
    option_paths = updated.get(OPTION_IMAGE_PATH_FIELD)
    if isinstance(option_paths, dict):
        rewritten_options: dict[str, Any] = {}
        for option, values in option_paths.items():
            if not isinstance(values, list):
                rewritten_options[str(option)] = values
                continue
            rewritten_values = []
            for item in values:
                new_item, changed = rewrite_path(item, old_prefix=old_prefix, new_prefix=new_prefix)
                rewritten_values.append(new_item)
                changes += changed
            rewritten_options[str(option)] = rewritten_values
        updated[OPTION_IMAGE_PATH_FIELD] = rewritten_options
    return updated, changes


def repair_jsonl(path: Path, *, old_prefix: str, new_prefix: str, dry_run: bool = False) -> dict[str, int | str | bool]:
    records: list[dict[str, Any]] = []
    records_seen = 0
    records_changed = 0
    rewritten_paths = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        records_seen += 1
        record = json.loads(line)
        if not isinstance(record, dict):
            raise ValueError(f"Expected JSON object at record {records_seen}")
        updated, changes = rewrite_record(record, old_prefix=old_prefix, new_prefix=new_prefix)
        if changes:
            records_changed += 1
            rewritten_paths += changes
        records.append(updated)
    if not dry_run:
        path.write_text(
            "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
            encoding="utf-8",
        )
    return {
        "jsonl": str(path),
        "dry_run": dry_run,
        "records_seen": records_seen,
        "records_changed": records_changed,
        "rewritten_paths": rewritten_paths,
    }


def main() -> int:
    args = parse_args()
    summary = repair_jsonl(
        args.jsonl.expanduser().resolve(),
        old_prefix=str(args.old_prefix).rstrip("/"),
        new_prefix=str(args.new_prefix).rstrip("/"),
        dry_run=bool(args.dry_run),
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the focused test**

Run:

```bash
uv run pytest /Users/xutao/.openclaw/workspace/tests/test_benchmark_test.py::BenchmarkTestModuleTests::test_repair_temp_superchem_image_paths_rewrites_stale_absolute_paths -q
```

Expected: PASS.

- [ ] **Step 5: Rewrite the actual temp benchmark JSONL**

Run:

```bash
uv run python /Users/xutao/.openclaw/workspace/scripts/repair_temp_superchem_image_paths.py \
  --jsonl /Users/xutao/.openclaw/workspace/temp-benchmarks/superchem/data/superchem_pool.jsonl
```

Expected stdout shape:

```json
{"dry_run": false, "jsonl": "/Users/xutao/.openclaw/workspace/temp-benchmarks/superchem/data/superchem_pool.jsonl", "records_changed": 3, "records_seen": 3, "rewritten_paths": 1000}
```

The exact `rewritten_paths` count may differ if the extractor fix is applied first, but `records_changed` should be `3` before extractor repair.

- [ ] **Step 6: Verify paths resolve through current runtime logic**

Run:

```bash
uv run python - <<'PY'
import json
from pathlib import Path
jsonl = Path("/Users/xutao/.openclaw/workspace/temp-benchmarks/superchem/data/superchem_pool.jsonl")
for line in jsonl.read_text(encoding="utf-8").splitlines():
    record = json.loads(line)
    paths = list(record.get("question_image_paths") or [])
    for values in (record.get("option_image_paths") or {}).values():
        paths.extend(values or [])
    missing = []
    absolute = []
    for path in paths:
        if Path(path).is_absolute():
            absolute.append(path)
        elif not (jsonl.parent / path).resolve().is_file():
            missing.append(path)
    print(record["id"], "paths=", len(paths), "absolute=", len(absolute), "missing=", len(missing))
    if absolute or missing:
        raise SystemExit(1)
PY
```

Expected: three records print `absolute= 0 missing= 0`.

- [ ] **Step 7: Commit**

```bash
git add /Users/xutao/.openclaw/workspace/scripts/repair_temp_superchem_image_paths.py \
  /Users/xutao/.openclaw/workspace/tests/test_benchmark_test.py \
  /Users/xutao/.openclaw/workspace/temp-benchmarks/superchem/data/superchem_pool.jsonl
git commit -m "fix: repair temp superchem image paths"
```

---

## Design Detail 2: SUPERChem Extractor Uses Only Current Record References

### New Extraction Rule

The extractor must localize only image locators actually present in the current record text:

- `question_image_paths`: image locators found in `question`.
- `option_image_paths`: image locators found inside each normalized option value.
- `explanation_image_paths`: image locators found in `reference_reasoning` / explanation.

Source fields such as `question_images`, `options_images`, and `explanation_images` remain useful diagnostics. They should be retained in:

- `source_question_image_paths`
- `source_option_image_paths`
- `source_explanation_image_paths`

But they must not be recursively traversed to decide which images are benchmark inputs.

### Parsing Contract

Add a parser that extracts Markdown-style and raw SUPERChem locators from text:

```text
![alt](/media/uploads/example.png)
<MultiModal>![alt](/media/uploads/example.png)</MultiModal>
https://superchem.pku.edu.cn/media/uploads/example.png
/media/uploads/example.png
```

The parser should preserve first-seen order and de-duplicate.

### Task 2: Add Text-Based Locator Tests

**Files:**
- Modify: `/Users/xutao/.openclaw/workspace/benchmarks/superchem/tests/test_extract_superchem_pool.py`
- Modify: `/Users/xutao/.openclaw/workspace/benchmarks/superchem/extract_superchem_pool.py`

- [ ] **Step 1: Write failing tests**

Add these tests to `SuperChemExtractionTests`:

```python
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
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
uv run pytest /Users/xutao/.openclaw/workspace/benchmarks/superchem/tests/test_extract_superchem_pool.py::SuperChemExtractionTests::test_extract_image_urls_from_record_text_uses_only_inline_references \
  /Users/xutao/.openclaw/workspace/benchmarks/superchem/tests/test_extract_superchem_pool.py::SuperChemExtractionTests::test_transform_row_ignores_unreferenced_shared_image_maps -q
```

Expected: FAIL because `extract_image_urls_from_record_text` does not exist and `transform_row()` still uses recursive source map extraction.

- [ ] **Step 3: Implement text locator parser**

Add near existing image helpers in `extract_superchem_pool.py`:

```python
MARKDOWN_IMAGE_URL_RE = re.compile(r"!\[[^\]]*]\((?P<url>[^)\s]+)(?:\s+\"[^\"]*\")?\)")
MEDIA_UPLOAD_URL_RE = re.compile(r"https?://superchem\.pku\.edu\.cn/media/uploads/[^\s)>'\"]+|/media/uploads/[^\s)>'\"]+")


def dedupe_preserve_order(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = normalize_text(value)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def extract_image_urls_from_record_text(text: Any) -> list[str]:
    raw = normalize_text(text)
    if not raw:
        return []
    candidates: list[str] = []
    for match in MARKDOWN_IMAGE_URL_RE.finditer(raw):
        candidates.append(match.group("url"))
    for match in MEDIA_UPLOAD_URL_RE.finditer(raw):
        candidates.append(match.group(0))
    return dedupe_preserve_order(candidates)


def extract_option_image_urls_from_record_text(options: dict[str, str]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for letter, text in options.items():
        urls = extract_image_urls_from_record_text(text)
        if urls:
            result[letter] = urls
    return result
```

- [ ] **Step 4: Change `transform_row()` to use text references for benchmark inputs**

Replace the current lines that derive `question_image_locators`, `option_images_raw`, and `explanation_image_locators` with:

```python
    source_question_image_locators = extract_image_urls(row.get("question_images"))
    source_option_images_raw = normalize_option_images(row.get("options_images"), options.keys())
    source_explanation_image_locators = extract_image_urls(row.get("explanation_images"))

    question_image_locators = extract_image_urls_from_record_text(question)
    question_images, question_downloaded, question_refs = localize_images(
        image_urls=question_image_locators,
        assets_dir=assets_dir,
        path_base_dir=output_base_dir,
        timeout_seconds=timeout_seconds,
    )
    option_images_raw = extract_option_image_urls_from_record_text(options)
    option_images: dict[str, list[str]] = {}
    option_downloaded = 0
    option_refs = 0
    for letter, urls in option_images_raw.items():
        localized, downloaded, refs = localize_images(
            image_urls=urls,
            assets_dir=assets_dir,
            path_base_dir=output_base_dir,
            timeout_seconds=timeout_seconds,
        )
        if localized:
            option_images[letter] = localized
        option_downloaded += downloaded
        option_refs += refs
    explanation_image_locators = extract_image_urls_from_record_text(explanation)
    explanation_images, explanation_downloaded, explanation_refs = localize_images(
        image_urls=explanation_image_locators,
        assets_dir=assets_dir,
        path_base_dir=output_base_dir,
        timeout_seconds=timeout_seconds,
    )
```

Then update `base_payload` source fields:

```python
        "source_question_image_paths": source_question_image_locators,
        "source_option_image_paths": source_option_images_raw,
        "source_explanation_image_paths": source_explanation_image_locators,
```

- [ ] **Step 5: Update existing tests that expected shared-map behavior**

Change `test_normalize_option_images_supports_shared_image_maps` name or assertion so it explicitly documents source metadata behavior rather than benchmark input behavior. Keep `normalize_option_images()` for `source_option_image_paths`, but do not use it for record-local `option_image_paths`.

- [ ] **Step 6: Run SUPERChem extractor tests**

Run:

```bash
uv run pytest /Users/xutao/.openclaw/workspace/benchmarks/superchem/tests/test_extract_superchem_pool.py -q
```

Expected: PASS.

- [ ] **Step 7: Regenerate canonical SUPERChem pool if desired**

Run only after the tests pass:

```bash
uv run python /Users/xutao/.openclaw/workspace/benchmarks/superchem/extract_superchem_pool.py \
  --output-jsonl /Users/xutao/.openclaw/workspace/benchmarks/superchem/data/superchem_pool.jsonl \
  --assets-dir /Users/xutao/.openclaw/workspace/benchmarks/superchem/assets
```

Expected:

- `question_image_paths` counts drop from 475 per record to the actual inline count.
- Existing assets remain under `/Users/xutao/.openclaw/workspace/benchmarks/superchem/assets/_shared/...`.
- Manifest counts reflect actual referenced images, not full source maps.

- [ ] **Step 8: Re-run temp path repair if temp records were copied from canonical**

If canonical regeneration is copied into temp benchmark, run:

```bash
uv run python /Users/xutao/.openclaw/workspace/scripts/repair_temp_superchem_image_paths.py \
  --jsonl /Users/xutao/.openclaw/workspace/temp-benchmarks/superchem/data/superchem_pool.jsonl
```

Expected: no absolute SUPERChem asset paths remain.

- [ ] **Step 9: Commit**

```bash
git add /Users/xutao/.openclaw/workspace/benchmarks/superchem/extract_superchem_pool.py \
  /Users/xutao/.openclaw/workspace/benchmarks/superchem/tests/test_extract_superchem_pool.py \
  /Users/xutao/.openclaw/workspace/benchmarks/superchem/data/superchem_pool.jsonl \
  /Users/xutao/.openclaw/workspace/benchmarks/superchem/data/superchem_pool.manifest.json \
  /Users/xutao/.openclaw/workspace/temp-benchmarks/superchem/data/superchem_pool.jsonl
git commit -m "fix: localize only referenced superchem images"
```

If canonical regeneration is intentionally deferred, omit generated data files from this commit and note that in the commit body.

---

## Design Detail 3: Architecture-Level Runner Convergence And Recovery

### Problem

Current single-agent prompt says:

```text
When roughly 20% or less of the budget remains, stop starting new tool or skill exploration.
```

That is advisory only. The runner cannot currently preserve a complete answer that appears in the session transcript before an OpenClaw timeout, and convergence metadata is not explicit enough to distinguish "model explored for a long time" from "runner failed to recover a produced answer."

ChemQA has stronger status polling, but the limits are embedded in `_wait_for_terminal_status()` and recovery cadence rather than a common benchmark policy. The result metadata does not make the convergence policy explicit across runner types.

### New Policy Contract

Create a shared `ConvergencePolicy`:

```python
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class ConvergencePolicy:
    timeout_seconds: int
    stop_fraction: float = 0.2
    finalization_grace_seconds: int = 90
    max_unchanged_status_polls: int = 2
    max_recovery_attempts: int = 2

    def to_meta(self) -> dict[str, Any]:
        return asdict(self)
```

Default values must apply to all records and all datasets. CLI overrides are global for a run. Tool calls and assistant turns are recorded in transcript summaries for analysis, but they are not part of `ConvergencePolicy` and must not fail or interrupt a record.

### Enforcement Semantics

Single LLM:

- The subprocess timeout remains `timeout_seconds + 30`.
- The OpenClaw CLI timeout remains `timeout_seconds`.
- Wrapper receives `--finalization-grace-seconds`.
- Wrapper inspects the session transcript after OpenClaw returns.
- If the run completed normally, metadata records observed tool/turn counts for diagnostics only.
- If the run timed out/aborted/blocked, wrapper extracts the latest complete final answer from the transcript if available.
- If a complete answer is recovered, runner returns `RunStatus.RECOVERED` with `RecoveryInfo(scored=True, evaluable=True, source="single-llm-session-transcript")`.
- If no answer is recoverable after an OpenClaw timeout sentinel, runner returns failed with `FailureInfo.code = "agent_response_timeout"`.
- High `tool_call_count` or high `assistant_turn_count` is never a failure condition in this design.

ChemQA:

- `ConvergencePolicy.timeout_seconds` replaces the bare timeout for launch and terminal wait.
- `_wait_for_terminal_status()` stops after `max_unchanged_status_polls` unchanged signatures and `max_recovery_attempts` recovery attempts.
- If an archived completed candidate/final artifact exists after a convergence stop, return recovered evaluable output as current recovery logic already supports.
- If no artifact exists, return failed with `FailureInfo.code = "convergence_limit_exceeded"` and include policy/status metadata.

Important limitation:

- This design intentionally avoids hard tool-count or turn-count limits because those would constrain the model's exploration space and confound the current skills-on/off benchmark. If a future experiment needs exploration-budget controls, add them as a separate opt-in experiment variable rather than default runner behavior.

### Task 3: Add Shared Convergence Module

**Files:**
- Create: `/Users/xutao/.openclaw/workspace/benchmarking/convergence.py`
- Create: `/Users/xutao/.openclaw/workspace/tests/test_benchmark_convergence.py`

- [ ] **Step 1: Write failing tests**

Create `/Users/xutao/.openclaw/workspace/tests/test_benchmark_convergence.py`:

```python
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from benchmarking.convergence import (
    ConvergencePolicy,
    extract_latest_complete_answer_from_transcript,
    summarize_transcript_convergence,
)


class BenchmarkConvergenceTests(unittest.TestCase):
    def test_policy_serializes_to_metadata(self) -> None:
        policy = ConvergencePolicy(timeout_seconds=900, finalization_grace_seconds=60)

        self.assertEqual(
            {
                "timeout_seconds": 900,
                "stop_fraction": 0.2,
                "finalization_grace_seconds": 60,
                "max_unchanged_status_polls": 2,
                "max_recovery_attempts": 2,
            },
            policy.to_meta(),
        )

    def test_transcript_summary_counts_tool_calls_and_turns(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            transcript = Path(tmpdir) / "session.jsonl"
            transcript.write_text(
                "\n".join(
                    [
                        json.dumps({"type": "session"}),
                        json.dumps({"type": "message", "message": {"role": "user", "content": [{"type": "text", "text": "Q"}]}}),
                        json.dumps({"type": "message", "message": {"role": "assistant", "content": [{"type": "toolCall", "name": "read"}]}}),
                        json.dumps({"type": "message", "message": {"role": "toolResult", "toolName": "read", "content": [{"type": "text", "text": "ok"}]}}),
                        json.dumps({"type": "message", "message": {"role": "assistant", "content": [{"type": "text", "text": "FINAL ANSWER: 42"}]}}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            summary = summarize_transcript_convergence(transcript)

        self.assertEqual(1, summary["tool_call_count"])
        self.assertEqual(2, summary["assistant_turn_count"])
        self.assertEqual(["read"], summary["tool_names"])

    def test_extract_latest_complete_answer_from_transcript(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            transcript = Path(tmpdir) / "session.jsonl"
            transcript.write_text(
                "\n".join(
                    [
                        json.dumps({"type": "message", "message": {"role": "assistant", "content": [{"type": "text", "text": "draft"}]}}),
                        json.dumps({"type": "message", "message": {"role": "assistant", "content": [{"type": "text", "text": "Explanation: short\\nAnswer: 273\\nConfidence: 55%"}]}}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            answer = extract_latest_complete_answer_from_transcript(transcript)

        self.assertEqual("Explanation: short\nAnswer: 273\nConfidence: 55%", answer)
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
uv run pytest /Users/xutao/.openclaw/workspace/tests/test_benchmark_convergence.py -q
```

Expected: FAIL because `benchmarking.convergence` does not exist.

- [ ] **Step 3: Implement module**

Create `/Users/xutao/.openclaw/workspace/benchmarking/convergence.py`:

```python
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


FINAL_ANSWER_RE = re.compile(r"(?im)^\s*FINAL ANSWER\s*:\s*\S+")
HLE_ANSWER_RE = re.compile(r"(?ims)^\s*Explanation\s*:.+^\s*Answer\s*:.+^\s*Confidence\s*:\s*\S+")


@dataclass(frozen=True)
class ConvergencePolicy:
    timeout_seconds: int
    stop_fraction: float = 0.2
    finalization_grace_seconds: int = 90
    max_unchanged_status_polls: int = 2
    max_recovery_attempts: int = 2

    def to_meta(self) -> dict[str, Any]:
        return asdict(self)


def _iter_transcript_messages(transcript_path: Path) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    if not transcript_path.is_file():
        return messages
    for line in transcript_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        message = event.get("message")
        if isinstance(message, dict):
            messages.append(message)
    return messages


def _text_from_content(content: Any) -> str:
    parts: list[str] = []
    if not isinstance(content, list):
        return ""
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "text":
            parts.append(str(item.get("text") or ""))
    return "\n".join(part for part in parts if part).strip()


def summarize_transcript_convergence(transcript_path: Path) -> dict[str, Any]:
    assistant_turn_count = 0
    tool_call_count = 0
    tool_names: list[str] = []
    for message in _iter_transcript_messages(transcript_path):
        if message.get("role") == "assistant":
            assistant_turn_count += 1
            for item in message.get("content") or []:
                if isinstance(item, dict) and item.get("type") == "toolCall":
                    tool_call_count += 1
                    name = str(item.get("name") or "")
                    if name:
                        tool_names.append(name)
    return {
        "transcript_path": str(transcript_path),
        "assistant_turn_count": assistant_turn_count,
        "tool_call_count": tool_call_count,
        "tool_names": tool_names,
    }


def is_complete_benchmark_answer(text: str) -> bool:
    candidate = str(text or "").strip()
    return bool(FINAL_ANSWER_RE.search(candidate) or HLE_ANSWER_RE.search(candidate))


def extract_latest_complete_answer_from_transcript(transcript_path: Path) -> str:
    for message in reversed(_iter_transcript_messages(transcript_path)):
        if message.get("role") != "assistant":
            continue
        text = _text_from_content(message.get("content"))
        if is_complete_benchmark_answer(text):
            return text
    return ""
```

- [ ] **Step 4: Run convergence tests**

Run:

```bash
uv run pytest /Users/xutao/.openclaw/workspace/tests/test_benchmark_convergence.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add /Users/xutao/.openclaw/workspace/benchmarking/convergence.py \
  /Users/xutao/.openclaw/workspace/tests/test_benchmark_convergence.py
git commit -m "feat: add benchmark convergence policy"
```

### Task 4: Wire Convergence Recovery Into Single-LLM Wrapper And Runner

**Files:**
- Modify: `/Users/xutao/.openclaw/workspace/benchmarking/single_llm_openclaw_wrapper.py`
- Modify: `/Users/xutao/.openclaw/workspace/benchmarking/runners/single_llm.py`
- Modify: `/Users/xutao/.openclaw/workspace/tests/test_single_llm_session_wrapper.py`

- [ ] **Step 1: Add wrapper tests for finalization policy and transcript metadata**

Add this test to `tests/test_single_llm_session_wrapper.py`:

```python
    def test_wrapper_records_transcript_metrics_and_recovers_transcript_answer(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = self.write_config(root)
            store_path = root / "agents" / "benchmark-single" / "sessions" / "sessions.json"
            session_path = store_path.parent / "session-a.jsonl"
            session_path.parent.mkdir(parents=True, exist_ok=True)
            session_path.write_text(
                json.dumps(
                    {
                        "type": "message",
                        "message": {
                            "role": "assistant",
                            "content": [{"type": "text", "text": "Explanation: ok\nAnswer: 273\nConfidence: 60%"}],
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            store_path.write_text(
                json.dumps(
                    {
                        "agent:benchmark-single:main": {
                            "sessionId": "session-a",
                            "sessionFile": str(session_path),
                            "modelProvider": "openai",
                            "model": "gpt-5",
                        }
                    }
                ),
                encoding="utf-8",
            )
            fake_args = argparse.Namespace(
                agent="benchmark-single",
                config_file=str(config_path),
                session_id="session-a",
                message="Q",
                thinking="high",
                timeout=30,
                json=True,
                finalization_grace_seconds=10,
            )
            completed = subprocess.CompletedProcess(
                ["openclaw"],
                0,
                stdout=json.dumps(
                    {
                        "result": {
                            "payloads": [{"text": "Request timed out before a response was generated."}],
                            "meta": {"aborted": True, "livenessState": "blocked"},
                        }
                    }
                ),
                stderr="",
            )

            with mock.patch.object(wrapper, "parse_args", return_value=fake_args), \
                mock.patch.object(wrapper, "run_openclaw", return_value=completed), \
                mock.patch.object(sys, "stdout", new_callable=io.StringIO) as stdout:
                exit_code = wrapper.main()

        self.assertEqual(0, exit_code)
        payload = json.loads(stdout.getvalue())
        result = payload["result"]
        self.assertEqual("Explanation: ok\nAnswer: 273\nConfidence: 60%", result["payloads"][0]["text"])
        convergence = result["meta"]["convergence"]
        self.assertTrue(convergence["transcript_answer_recovered"])
        self.assertEqual(10, convergence["policy"]["finalization_grace_seconds"])
        self.assertIn("tool_call_count", convergence)
        self.assertIn("assistant_turn_count", convergence)
```

If `io` is not imported in the file, add `import io`.

- [ ] **Step 2: Run wrapper test to verify failure**

Run:

```bash
uv run pytest /Users/xutao/.openclaw/workspace/tests/test_single_llm_session_wrapper.py::SingleLLMSessionWrapperTests::test_wrapper_records_transcript_metrics_and_recovers_transcript_answer -q
```

Expected: FAIL because wrapper does not parse finalization convergence flags and does not recover transcript answers.

- [ ] **Step 3: Extend wrapper args**

In `single_llm_openclaw_wrapper.py`, import convergence helpers:

```python
from benchmarking.convergence import (
    ConvergencePolicy,
    extract_latest_complete_answer_from_transcript,
    summarize_transcript_convergence,
)
```

Add parser flags:

```python
    parser.add_argument("--finalization-grace-seconds", type=int, default=90)
```

- [ ] **Step 4: Add helper to find transcript path**

Add:

```python
def transcript_path_from_audit(audit: dict[str, Any]) -> Path | None:
    raw = str(audit.get("postflight_entry_session_file") or "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    return path if path.is_file() else None
```

- [ ] **Step 5: Merge convergence metadata and recovered answer**

After postflight audit and before printing JSON, add:

```python
            policy = ConvergencePolicy(
                timeout_seconds=int(args.timeout or 0),
                finalization_grace_seconds=int(args.finalization_grace_seconds),
            )
            transcript_path = transcript_path_from_audit(audit)
            convergence_meta = {"policy": policy.to_meta(), "transcript_answer_recovered": False}
            if transcript_path is not None:
                convergence_meta.update(summarize_transcript_convergence(transcript_path))
            output = result.stdout.strip() or result.stderr.strip()
            payload = parse_openclaw_json_output(output)
            target = payload.get("result") if isinstance(payload, dict) and isinstance(payload.get("result"), dict) else payload
            if isinstance(target, dict):
                meta = target.setdefault("meta", {})
                if isinstance(meta, dict):
                    existing = meta.get("convergence")
                    merged = dict(existing) if isinstance(existing, dict) else {}
                    merged.update(convergence_meta)
                    meta["convergence"] = merged
                payloads = target.get("payloads")
                timeout_like = (
                    isinstance(payloads, list)
                    and any("Request timed out before a response was generated" in str(item.get("text") or "") for item in payloads if isinstance(item, dict))
                )
                if timeout_like and transcript_path is not None:
                    recovered = extract_latest_complete_answer_from_transcript(transcript_path)
                    if recovered:
                        target["payloads"] = [{"text": recovered}]
                        target.setdefault("meta", {}).setdefault("convergence", {})["transcript_answer_recovered"] = True
                        target["meta"]["convergence"]["recovery_source"] = "single-llm-session-transcript"
            payload = merge_isolation_audit(payload, audit)
            print(json.dumps(payload, ensure_ascii=False))
```

Keep the existing non-JSON branch unchanged.

- [ ] **Step 6: Pass convergence flags from `SingleLLMRunner`**

In `benchmarking/runners/single_llm.py`:

- Accept `convergence_policy: ConvergencePolicy | None = None`.
- Set `self.convergence_policy = convergence_policy or ConvergencePolicy(timeout_seconds=timeout_seconds)`.
- Use `self.convergence_policy.timeout_seconds` in prompt time budget and wrapper `--timeout`.
- Add wrapper flags:

```python
            "--finalization-grace-seconds",
            str(self.convergence_policy.finalization_grace_seconds),
```

- Add `runner_meta["convergence_policy"] = self.convergence_policy.to_meta()` after parsing payload.
- Before treating timeout sentinel as failure, check `runner_meta.get("convergence", {}).get("transcript_answer_recovered") is True`. If true, return:

```python
return RunnerResult(
    status=RunStatus.RECOVERED,
    answer=AnswerPayload(short_answer_text=short_answer_text, full_response_text=full_response_text),
    raw=payload,
    runner_meta=runner_meta,
    recovery=RecoveryInfo(
        source="single-llm-session-transcript",
        scored=True,
        evaluable=True,
        details=dict(runner_meta.get("convergence") or {}),
    ),
)
```

- Do not fail a record based on `tool_call_count` or `assistant_turn_count`; these remain diagnostics for reasoning-trace comparison.

- [ ] **Step 7: Run focused tests**

Run:

```bash
uv run pytest /Users/xutao/.openclaw/workspace/tests/test_single_llm_session_wrapper.py /Users/xutao/.openclaw/workspace/tests/test_benchmark_convergence.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add /Users/xutao/.openclaw/workspace/benchmarking/single_llm_openclaw_wrapper.py \
  /Users/xutao/.openclaw/workspace/benchmarking/runners/single_llm.py \
  /Users/xutao/.openclaw/workspace/tests/test_single_llm_session_wrapper.py
git commit -m "feat: enforce convergence metadata for single llm runs"
```

### Task 5: Wire Convergence Policy Into ChemQA Runner

**Files:**
- Modify: `/Users/xutao/.openclaw/workspace/benchmarking/runners/chemqa.py`
- Test: existing ChemQA tests under `/Users/xutao/.openclaw/workspace/tests/test_chemqa_artifact_flow.py`, `/Users/xutao/.openclaw/workspace/tests/test_chemqa_epoch_flow.py`, and targeted new unit test if a ChemQA runner test fixture exists.

- [ ] **Step 1: Add a focused ChemQA wait-policy test**

If no direct ChemQA runner unit test exists, add a minimal test file `/Users/xutao/.openclaw/workspace/tests/test_chemqa_convergence.py` that constructs a `ChemQARunner` with stub functions and verifies `_wait_for_terminal_status()` stops after policy limits.

Core assertion:

```python
with self.assertRaises(RuntimeError) as ctx:
    runner._wait_for_terminal_status("run-1", timeout_seconds=60)
self.assertIn("convergence", str(ctx.exception).lower())
```

Use a fake `_read_run_status()` returning the same non-terminal status and a fake `_recover_stalled_run()` that records calls. Expected recovery calls: `policy.max_recovery_attempts`.

- [ ] **Step 2: Implement policy fields**

In `ChemQARunner.__init__`, accept:

```python
        convergence_policy=None,
```

Set:

```python
        from ..convergence import ConvergencePolicy
        self.convergence_policy = convergence_policy or ConvergencePolicy(timeout_seconds=timeout_seconds)
```

Move the import to module top if no circular import appears.

- [ ] **Step 3: Enforce status convergence in `_wait_for_terminal_status()`**

Replace hardcoded unchanged/recovery logic with policy-based counters:

```python
        recovery_attempts = 0
        while time.time() < deadline:
            ...
            if unchanged_polls >= self.convergence_policy.max_unchanged_status_polls:
                if recovery_attempts >= self.convergence_policy.max_recovery_attempts:
                    error_message = (
                        f"ChemQA run `{run_id}` exceeded convergence limits before terminal status. "
                        f"Last status: {last_status}"
                    )
                    if self._benchmark_error_factory is not None:
                        raise self._benchmark_error_factory(error_message)
                    raise RuntimeError(error_message)
                recovery_attempts += 1
                last_recovery_attempt_at = now
                self._recover_stalled_run(run_id, last_status)
                unchanged_polls = 0
```

- [ ] **Step 4: Emit metadata**

Every ChemQA `runner_meta` return path should include:

```python
"convergence_policy": self.convergence_policy.to_meta(),
```

For failures caused by `_wait_for_terminal_status()` or convergence stop, include:

```python
FailureInfo(
    code="convergence_limit_exceeded",
    message=str(exc),
    details={"policy": self.convergence_policy.to_meta(), "run_id": run_id},
)
```

- [ ] **Step 5: Run ChemQA and convergence tests**

Run:

```bash
uv run pytest /Users/xutao/.openclaw/workspace/tests/test_chemqa_convergence.py \
  /Users/xutao/.openclaw/workspace/tests/test_chemqa_artifact_flow.py \
  /Users/xutao/.openclaw/workspace/tests/test_chemqa_epoch_flow.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add /Users/xutao/.openclaw/workspace/benchmarking/runners/chemqa.py \
  /Users/xutao/.openclaw/workspace/tests/test_chemqa_convergence.py
git commit -m "feat: apply convergence policy to chemqa runner"
```

### Task 6: Add Benchmark CLI Flags And Propagation

**Files:**
- Modify: `/Users/xutao/.openclaw/workspace/benchmark_test.py`
- Modify: `/Users/xutao/.openclaw/workspace/tests/test_benchmark_test.py`
- Modify: `/Users/xutao/.openclaw/workspace/tests/test_benchmark_prompts.py`

- [ ] **Step 1: Add CLI parsing test**

Add to `tests/test_benchmark_test.py`:

```python
    def test_parse_args_accepts_convergence_policy_flags(self) -> None:
        with mock.patch.object(
            sys,
            "argv",
            [
                "benchmark_test.py",
                "--single-timeout",
                "900",
                "--finalization-grace-seconds",
                "60",
                "--max-unchanged-status-polls",
                "1",
                "--max-recovery-attempts",
                "1",
            ],
        ):
            args = benchmark_test.parse_args()

        self.assertEqual(60, args.finalization_grace_seconds)
        self.assertEqual(1, args.max_unchanged_status_polls)
        self.assertEqual(1, args.max_recovery_attempts)
```

- [ ] **Step 2: Add parser flags**

In `benchmark_test.parse_args()`:

```python
    parser.add_argument("--finalization-grace-seconds", type=int, default=90, help="Reserved grace window for final answer recovery/finalization")
    parser.add_argument("--max-unchanged-status-polls", type=int, default=2, help="ChemQA convergence limit for unchanged status polls")
    parser.add_argument("--max-recovery-attempts", type=int, default=2, help="ChemQA convergence limit for recovery attempts")
```

- [ ] **Step 3: Build policy object once**

Import:

```python
from benchmarking.convergence import ConvergencePolicy
```

After args parse:

```python
    convergence_policy = ConvergencePolicy(
        timeout_seconds=args.single_timeout,
        finalization_grace_seconds=args.finalization_grace_seconds,
        max_unchanged_status_polls=args.max_unchanged_status_polls,
        max_recovery_attempts=args.max_recovery_attempts,
    )
```

For ChemQA, construct a policy with `timeout_seconds=args.chemqa_timeout` but same other fields.

- [ ] **Step 4: Pass policy into runner constructors**

In `SingleLLMRunner` wrapper class inside `benchmark_test.py`, pass:

```python
convergence_policy=single_convergence_policy
```

In ChemQA runner construction, pass:

```python
convergence_policy=chemqa_convergence_policy
```

If the current helper signature does not expose runner-specific kwargs cleanly, extend `run_group()` to accept `single_convergence_policy` and `chemqa_convergence_policy`.

- [ ] **Step 5: Add policy metadata to manifest/results**

When writing `runtime-manifest.json` or top-level `results.json`, include:

```python
"convergence_policy": {
    "single_llm": single_convergence_policy.to_meta(),
    "chemqa": chemqa_convergence_policy.to_meta(),
}
```

- [ ] **Step 6: Run benchmark CLI tests**

Run:

```bash
uv run pytest /Users/xutao/.openclaw/workspace/tests/test_benchmark_test.py /Users/xutao/.openclaw/workspace/tests/test_benchmark_prompts.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add /Users/xutao/.openclaw/workspace/benchmark_test.py \
  /Users/xutao/.openclaw/workspace/tests/test_benchmark_test.py \
  /Users/xutao/.openclaw/workspace/tests/test_benchmark_prompts.py
git commit -m "feat: expose benchmark convergence policy"
```

---

## Task 7: Update GLOBAL_DEV_SPEC

**Files:**
- Modify: `/Users/xutao/.openclaw/workspace/GLOBAL_DEV_SPEC.md`

- [ ] **Step 1: Update current capabilities**

Change the single-agent runner bullet to mention runner-level convergence policy, transcript answer recovery, and hard convergence metadata.

Suggested replacement sentence:

```markdown
- `DONE`: Run a single-agent OpenClaw baseline through a benchmark wrapper that gives each record a run-scoped `sessionId`, clears only stale `agent:<id>:main` session-store pointers before the turn, injects time-budget-aware answer instructions, validates OpenClaw stdout against a strict agent result schema before answer extraction, applies runner-level convergence policy metadata and transcript recovery for complete benchmark answers, treats unrecovered OpenClaw response-timeout sentinel payloads as failed/non-scoreable runs, and preserves historical transcript files via `workspace/benchmarking/runners/single_llm.py`, `workspace/benchmarking/single_llm_openclaw_wrapper.py`, `workspace/benchmarking/convergence.py`, and `workspace/benchmarking/result_contract.py`.
```

- [ ] **Step 2: Update visual input capability**

Suggested replacement sentence:

```markdown
- `DONE`: Creates per-record local input bundles for benchmark records with visual inputs. SuperChem image paths are expected to be relative to the record JSONL directory, temp benchmark path repair rewrites stale machine-local absolute paths into portable relative references, and the SuperChem extractor localizes only images directly referenced by the current question/options/reference reasoning text. HLE image fields are materialized from base64 data URIs or local files into the bundle, and remote-only HLE images fail fast instead of silently dropping visual context.
```

- [ ] **Step 3: Add source module entry**

Under `workspace/benchmarking/`, add:

```markdown
    - `convergence.py`
      - Defines benchmark runner convergence policy, transcript summary helpers, and complete-answer recovery from session transcripts.
```

- [ ] **Step 4: Run doc-neutral smoke tests**

Run:

```bash
uv run pytest /Users/xutao/.openclaw/workspace/tests/test_workspace_layout.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add /Users/xutao/.openclaw/workspace/GLOBAL_DEV_SPEC.md
git commit -m "docs: document benchmark convergence and superchem input contracts"
```

---

## Final Verification

Run the focused suite:

```bash
uv run pytest \
  /Users/xutao/.openclaw/workspace/benchmarks/superchem/tests/test_extract_superchem_pool.py \
  /Users/xutao/.openclaw/workspace/tests/test_benchmark_convergence.py \
  /Users/xutao/.openclaw/workspace/tests/test_single_llm_session_wrapper.py \
  /Users/xutao/.openclaw/workspace/tests/test_benchmark_test.py \
  /Users/xutao/.openclaw/workspace/tests/test_benchmark_prompts.py \
  /Users/xutao/.openclaw/workspace/tests/test_workspace_layout.py -q
```

Expected: PASS.

Verify temp SUPERChem can materialize bundles without starting model runs:

```bash
uv run python - <<'PY'
from pathlib import Path
from benchmarking.datasets import load_benchmark_records
import benchmark_test

records = load_benchmark_records(Path("/Users/xutao/.openclaw/workspace/temp-benchmarks/superchem/data/superchem_pool.jsonl"))
bundle_root = Path("/tmp/superchem-bundle-check")
for record in records:
    bundle = benchmark_test.ensure_runtime_bundle(record, bundle_root=bundle_root)
    print(record.record_id, len(bundle.image_files) if bundle else 0)
PY
```

Expected:

- No `SUPERChem multimodal record ... has unavailable image inputs` error.
- Each record prints at least one localized image.

Run the temp benchmark after fixes:

```bash
cd /Users/xutao/.openclaw/workspace
uv run python benchmark_test.py \
  --benchmark-root /Users/xutao/.openclaw/workspace/temp-benchmarks \
  --groups single_llm_skills_on,single_llm_skills_off \
  --single-agent-model openai/gpt-5.4 \
  --judge-model openai/gpt-5.4 \
  --max-concurrent-groups 2 \
  --finalization-grace-seconds 90 \
  --max-unchanged-status-polls 2 \
  --max-recovery-attempts 2 \
  --exact-output-dir /Users/xutao/.openclaw/workspace/state/benchmark-runs/temp-benchmark-$(date +%Y%m%d-%H%M%S)
```

Expected:

- `superchem_multimodal` records no longer fail before model execution due to unavailable image paths.
- Per-record `runner_meta` includes `convergence_policy` and/or `convergence`.
- HLE-like long-running records either complete, recover a complete transcript answer, or fail with structured convergence metadata rather than opaque missing-answer timeout.

---

## Self-Review

- Spec coverage:
  - Direction 1 is covered by Task 1 and final bundle verification.
  - Direction 2 is covered by Task 2 and SUPERChem extractor tests.
  - Direction 3 is covered by Tasks 3 through 6 and applies to single LLM plus ChemQA runners across all datasets.
- Placeholder scan:
  - No `TBD`, no open-ended "add tests" without test code, and each implementation step has concrete files and commands.
- Type consistency:
  - `ConvergencePolicy`, `to_meta()`, `summarize_transcript_convergence()`, and `extract_latest_complete_answer_from_transcript()` are consistently referenced across module, wrapper, runner, and tests.
