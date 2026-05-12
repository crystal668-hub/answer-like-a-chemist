from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from benchmarking.core.convergence import ConvergencePolicy
from benchmarking.workflow.runners.chemqa import ChemQARunner


class ChemQAConvergenceTests(unittest.TestCase):
    def test_wait_for_terminal_status_stops_after_policy_recovery_limit(self) -> None:
        policy = ConvergencePolicy(
            timeout_seconds=60,
            max_unchanged_status_polls=1,
            max_recovery_attempts=2,
        )
        runner = object.__new__(ChemQARunner)
        runner.chemqa_root = Path("/tmp/chemqa")
        runner.convergence_policy = policy
        runner._benchmark_error_factory = None
        runner._is_chemqa_terminal_status = lambda payload: False
        recovery_calls: list[dict[str, object]] = []

        status = {
            "status": "running",
            "phase": "review",
            "updated_at": "2026-05-09T00:00:00Z",
        }
        runner._read_run_status = lambda run_id: dict(status)
        runner._recover_stalled_run = lambda run_id, last_status: recovery_calls.append(dict(last_status)) or {
            "status": "ok"
        }

        times = iter(range(100))
        with mock.patch("time.time", side_effect=lambda: next(times)), \
            mock.patch("time.sleep", return_value=None):
            with self.assertRaises(RuntimeError) as ctx:
                runner._wait_for_terminal_status("run-1", timeout_seconds=60)

        self.assertIn("convergence", str(ctx.exception).lower())
        self.assertEqual(policy.max_recovery_attempts, len(recovery_calls))


if __name__ == "__main__":
    unittest.main()
