from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any


class BenchmarkCancelledError(RuntimeError):
    pass


@dataclass(frozen=True)
class CancellationReason:
    source: str
    signal_name: str = ""
    message: str = "Benchmark run cancelled"
    requested_at: float = field(default_factory=time.time)

    def to_payload(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "signal": self.signal_name,
            "message": self.message,
            "requested_at": self.requested_at,
        }


@dataclass(frozen=True)
class CancellationOutcome:
    completed: bool
    active_process_count: int
    errors: tuple[dict[str, Any], ...] = ()

    def to_payload(self) -> dict[str, Any]:
        return {
            "completed": self.completed,
            "active_process_count": self.active_process_count,
            "errors": [dict(error) for error in self.errors],
        }


class CancellationToken:
    def __init__(self) -> None:
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._reason: CancellationReason | None = None
        self._request_count = 0

    def cancel(self, reason: CancellationReason) -> bool:
        with self._lock:
            self._request_count += 1
            if self._reason is not None:
                return False
            self._reason = reason
            self._event.set()
            return True

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def reason(self) -> CancellationReason | None:
        with self._lock:
            return self._reason

    @property
    def request_count(self) -> int:
        with self._lock:
            return self._request_count

    def wait(self, timeout: float | None = None) -> bool:
        return self._event.wait(timeout)

    def raise_if_cancelled(self) -> None:
        if self.is_cancelled:
            reason = self.reason
            raise BenchmarkCancelledError(reason.message if reason is not None else "Benchmark run cancelled")


class OwnedProcessRegistry:
    def __init__(self, *, cancellation_token: CancellationToken, grace_seconds: float = 5.0) -> None:
        self.cancellation_token = cancellation_token
        self.grace_seconds = max(0.0, float(grace_seconds))
        self._lock = threading.Lock()
        self._processes: dict[int, subprocess.Popen[str]] = {}

    def register(self, process: subprocess.Popen[str]) -> None:
        with self._lock:
            self._processes[int(process.pid)] = process

    def unregister(self, process: subprocess.Popen[str]) -> None:
        with self._lock:
            self._processes.pop(int(process.pid), None)

    def active(self) -> list[subprocess.Popen[str]]:
        with self._lock:
            return [process for process in self._processes.values() if process.poll() is None]

    @staticmethod
    def signal_process_group(process: subprocess.Popen[str], sig: signal.Signals) -> None:
        if process.poll() is None:
            os.killpg(int(process.pid), sig)

    def terminate_all(self, *, force: bool = False) -> CancellationOutcome:
        errors: list[dict[str, Any]] = []
        initial_signal = signal.SIGKILL if force else signal.SIGTERM
        for process in self.active():
            try:
                self.signal_process_group(process, initial_signal)
            except ProcessLookupError:
                continue
            except Exception as exc:
                errors.append({"pid": process.pid, "stage": "signal", "error": f"{type(exc).__name__}: {exc}"})
        deadline = time.monotonic() + (0.0 if force else self.grace_seconds)
        while self.active() and time.monotonic() < deadline:
            time.sleep(0.05)
        for process in self.active():
            try:
                self.signal_process_group(process, signal.SIGKILL)
            except ProcessLookupError:
                continue
            except Exception as exc:
                errors.append({"pid": process.pid, "stage": "force_kill", "error": f"{type(exc).__name__}: {exc}"})
        force_deadline = time.monotonic() + max(0.5, self.grace_seconds)
        while self.active() and time.monotonic() < force_deadline:
            time.sleep(0.05)
        active = self.active()
        return CancellationOutcome(completed=not active, active_process_count=len(active), errors=tuple(errors))

    def wait_cancelled(self, deadline: float) -> CancellationOutcome:
        while self.active() and time.monotonic() < deadline:
            time.sleep(0.05)
        active = self.active()
        return CancellationOutcome(completed=not active, active_process_count=len(active))
