"""Frozen polling and recovery policy for the legacy fixed-lane protocol."""
from dataclasses import dataclass
from benchmarking.core.convergence import ConvergencePolicy


@dataclass(frozen=True)
class ChemQAConvergencePolicy(ConvergencePolicy):
    max_unchanged_status_polls: int = 2
    max_recovery_attempts: int = 2
