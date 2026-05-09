from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from benchmarking import cli as benchmarking_cli
from benchmarking.reporting import GroupRecordResult as ReportingGroupRecordResult


def test_benchmark_test_is_thin_compatibility_facade() -> None:
    module_path = Path(__file__).resolve().parents[1] / "benchmark_test.py"
    spec = importlib.util.spec_from_file_location("benchmark_test_facade_probe", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    assert module.main is benchmarking_cli.main
    assert module.GroupRecordResult is ReportingGroupRecordResult
    assert module.GroupRecordResult.__module__ == "benchmarking.reporting"
    assert module.BenchmarkRecord is benchmarking_cli.BenchmarkRecord


def test_benchmarking_cli_owns_benchmark_entrypoint_behavior() -> None:
    assert callable(benchmarking_cli.main)
    assert callable(benchmarking_cli.parse_args)
    assert benchmarking_cli.EXPERIMENT_GROUPS["single_llm_skills_on"].runner == "single_llm"
    assert benchmarking_cli.EXPERIMENT_GROUPS["chemqa_skills_on"].runner == "chemqa"
