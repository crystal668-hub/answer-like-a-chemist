from __future__ import annotations

from pathlib import Path
import unittest

from benchmarking.runtime import paths as runtime_paths


ROOT = Path(__file__).resolve().parents[1]
OPENCLAW_HOME = ROOT.parent


class WorkspaceLayoutTests(unittest.TestCase):
    def test_benchmark_data_roots_live_under_openclaw_data(self) -> None:
        self.assertEqual(OPENCLAW_HOME / "data" / "formal-benchmarks", runtime_paths.benchmarks_root)
        self.assertEqual(OPENCLAW_HOME / "data" / "temp-benchmarks", runtime_paths.temp_benchmarks_root)
        self.assertFalse((ROOT / "benchmarks").exists())
        self.assertFalse((ROOT / "temp-benchmarks").exists())

    def test_obsolete_root_entrypoints_are_removed(self) -> None:
        self.assertFalse((ROOT / "benchmark_test.py").exists())
        self.assertFalse((ROOT / "benchmark_rl.py").exists())
        self.assertFalse((ROOT / "runtime_paths.py").exists())

    def test_benchmarking_code_is_clustered_under_layered_packages(self) -> None:
        benchmarking_root = ROOT / "benchmarking"
        clustered_files = {
            path.relative_to(benchmarking_root).as_posix()
            for package in ("core", "scoring", "runtime", "skills", "analysis", "workflow")
            for path in (benchmarking_root / package).glob("*.py")
        }

        self.assertIn("core/contracts.py", clustered_files)
        self.assertIn("scoring/registry.py", clustered_files)
        self.assertTrue((benchmarking_root / "scoring" / "evaluators").is_dir())
        self.assertIn("runtime/config.py", clustered_files)
        self.assertIn("runtime/paths.py", clustered_files)
        self.assertIn("skills/health.py", clustered_files)
        self.assertIn("analysis/automated.py", clustered_files)
        self.assertIn("workflow/cli.py", clustered_files)

    def test_benchmarking_flat_compatibility_modules_are_removed(self) -> None:
        benchmarking_root = ROOT / "benchmarking"
        flat_modules = {
            path.name
            for path in benchmarking_root.glob("*.py")
            if path.name != "__init__.py"
        }

        self.assertEqual(set(), flat_modules)
        self.assertFalse((benchmarking_root / "runners").exists())


if __name__ == "__main__":
    unittest.main()
