from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_TERMS = ("conformabench",)
ALLOWLIST = {
    "tests/test_conformabench_removed.py",
    "GLOBAL_DEV_SPEC.md",
}


class ConformaBenchRemovalTests(unittest.TestCase):
    def test_tracked_project_files_do_not_reference_conformabench(self) -> None:
        tracked_files = subprocess.check_output(
            ["git", "ls-files"],
            cwd=PROJECT_ROOT,
            text=True,
        ).splitlines()
        offenders: list[str] = []
        for relative_path in tracked_files:
            if relative_path in ALLOWLIST:
                continue
            path = PROJECT_ROOT / relative_path
            if not path.is_file():
                continue
            try:
                content = path.read_text(encoding="utf-8").lower()
            except UnicodeDecodeError:
                continue
            if any(term in content or term in relative_path.lower() for term in FORBIDDEN_TERMS):
                offenders.append(relative_path)

        self.assertEqual([], offenders)


if __name__ == "__main__":
    unittest.main()
