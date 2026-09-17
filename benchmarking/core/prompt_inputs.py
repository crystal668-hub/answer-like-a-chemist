from __future__ import annotations
from typing import Any, Protocol

class RuntimeBundleLike(Protocol):
    bundle_dir: Any
    question_markdown: Any
    image_files: list[Any]
