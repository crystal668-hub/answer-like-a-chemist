from __future__ import annotations
from pathlib import Path
from benchmarking.runtime.workspace_policy import ProtectedRoot
from benchmarking.runtime.cancellation import CancellationOutcome, CancellationReason, CancellationToken, OwnedProcessRegistry

def _compatibility_protected_roots(*, runtime_root: Path, output_root: Path) -> tuple[ProtectedRoot, ...]:
    return (
        ProtectedRoot("benchmark_runtime_root", runtime_root, "compatibility.runtime_root"),
        ProtectedRoot("current_output_root", output_root, "compatibility.output_root"),
    )

class _CancellationRunnerMixin:
    _cancellation_token: CancellationToken
    _process_registry: OwnedProcessRegistry

    def cancel(self, reason: CancellationReason) -> None:
        self._cancellation_enabled = True
        self._cancellation_token.cancel(reason)

    def wait_cancelled(self, deadline: float) -> CancellationOutcome:
        return self._process_registry.wait_cancelled(deadline)
