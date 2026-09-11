"""Explicit, lazy runner selection for entrypoint composition."""
from typing import Any


def build_runner(*, runner_kind: str, **kwargs: Any) -> Any:
    if runner_kind == "single_llm":
        from benchmarking.service.single.adapter import SingleLLMRunner
        return SingleLLMRunner(**kwargs)
    if runner_kind == "chemqa":
        from benchmarking.service.chemdebate.adapter import ChemQARunner
        return ChemQARunner(**kwargs)
    raise ValueError(f"Unsupported runner kind: {runner_kind}")
