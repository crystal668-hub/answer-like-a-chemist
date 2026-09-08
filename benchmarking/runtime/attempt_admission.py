"""Small resource admission controller for attempt-level scheduling."""

from __future__ import annotations

import threading
from dataclasses import dataclass


@dataclass(frozen=True)
class ResourceRequest:
    cpus: float = 1.0
    memory_bytes: int = 0
    pids: int = 0


@dataclass(frozen=True)
class CapacitySnapshot:
    total_cpus: float
    total_memory_bytes: int
    total_pids: int
    used_cpus: float
    used_memory_bytes: int
    used_pids: int


@dataclass(frozen=True)
class AdmissionLease:
    request: ResourceRequest
    token: int


class AttemptAdmissionController:
    def __init__(self, *, total_cpus: float, total_memory_bytes: int, total_pids: int = 0, max_attempts: int | None = None) -> None:
        if total_cpus <= 0 or total_memory_bytes < 0 or total_pids < 0:
            raise ValueError("resource capacities must be non-negative and CPUs must be positive")
        self._total = ResourceRequest(total_cpus, total_memory_bytes, total_pids)
        self._max_attempts = max_attempts if max_attempts is None else max(1, int(max_attempts))
        self._used = ResourceRequest(0.0, 0, 0)
        self._active = 0
        self._next_token = 0
        self._condition = threading.Condition()

    def _fits(self, request: ResourceRequest) -> bool:
        if self._max_attempts is not None and self._active >= self._max_attempts:
            return False
        return (
            self._used.cpus + request.cpus <= self._total.cpus
            and (self._total.memory_bytes == 0 or self._used.memory_bytes + request.memory_bytes <= self._total.memory_bytes)
            and (self._total.pids == 0 or self._used.pids + request.pids <= self._total.pids)
        )

    def acquire(self, request: ResourceRequest, *, timeout: float | None = None) -> AdmissionLease:
        with self._condition:
            if not self._fits(request) and timeout is None:
                raise RuntimeError("attempt admission capacity exhausted")
            if not self._condition.wait_for(lambda: self._fits(request), timeout=timeout):
                raise TimeoutError("timed out waiting for attempt admission capacity")
            self._next_token += 1
            lease = AdmissionLease(request, self._next_token)
            self._used = ResourceRequest(self._used.cpus + request.cpus, self._used.memory_bytes + request.memory_bytes, self._used.pids + request.pids)
            self._active += 1
            return lease

    def release(self, lease: AdmissionLease) -> None:
        with self._condition:
            self._used = ResourceRequest(max(0.0, self._used.cpus - lease.request.cpus), max(0, self._used.memory_bytes - lease.request.memory_bytes), max(0, self._used.pids - lease.request.pids))
            self._active = max(0, self._active - 1)
            self._condition.notify_all()

    def capacity_snapshot(self) -> CapacitySnapshot:
        with self._condition:
            return CapacitySnapshot(self._total.cpus, self._total.memory_bytes, self._total.pids, self._used.cpus, self._used.memory_bytes, self._used.pids)

    def cancel_pending(self, reason: str) -> int:
        with self._condition:
            self._condition.notify_all()
        return 0
