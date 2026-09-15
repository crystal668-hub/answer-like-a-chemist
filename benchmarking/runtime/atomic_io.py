from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def atomic_write_text(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    """Replace a file atomically, keeping the temporary file beside its target."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Check the target and its writable directory boundary.  System temporary
    # roots can themselves be symlinks (notably macOS /var), so rejecting every
    # ancestor would make ordinary temporary output unusable.
    if path.is_symlink() or path.parent.is_symlink():
        raise OSError(f"refusing atomic write through symlink: {path}")
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding=encoding) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write_json(path: Path, payload: Any) -> None:
    atomic_write_text(path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
