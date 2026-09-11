from __future__ import annotations
from typing import Any, Protocol
from benchmarking.core.datasets import BenchmarkRecord

class RuntimeBundleLike(Protocol):
    bundle_dir: Any
    question_markdown: Any
    image_files: list[Any]

def hle_answer_type(record: BenchmarkRecord) -> str:
    payload = dict(getattr(record, "payload", {}) or {})
    config = dict(getattr(getattr(record, "grading", None), "config", {}) or {})
    raw_answer_type = str(payload.get("answer_type") or config.get("answer_type") or "").strip().lower()
    if "multiple" in raw_answer_type or "choice" in raw_answer_type:
        return "multiple_choice"
    if "exact" in raw_answer_type:
        return "exact_match"
    return "generic"
