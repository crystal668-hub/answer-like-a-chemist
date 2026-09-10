"""Cancellation-aware, FIFO admission for individual attempts."""

from __future__ import annotations

import threading
from collections import deque
from contextlib import contextmanager

from benchmarking.runtime.cancellation import CancellationToken

DEFAULT_MAX_CONCURRENT_ATTEMPTS = 2


class AttemptAdmissionController:
    def __init__(self, *, max_attempts: int = DEFAULT_MAX_CONCURRENT_ATTEMPTS,
                 cancellation_token: CancellationToken | None = None) -> None:
        if max_attempts < 1:
            raise ValueError("max_concurrent_attempts must be positive")
        self.max_attempts = max_attempts
        self.cancellation_token = cancellation_token or CancellationToken()
        self._condition = threading.Condition()
        self._pending = deque()
        self._active: set[object] = set()

    def acquire(self) -> object:
        token = object()
        with self._condition:
            self._pending.append(token)
            try:
                while True:
                    self.cancellation_token.raise_if_cancelled()
                    if self._pending[0] is token and len(self._active) < self.max_attempts:
                        self._pending.popleft()
                        self._active.add(token)
                        self._condition.notify_all()
                        return token
                    self._condition.wait(0.05)
            except BaseException:
                self._pending.remove(token)
                self._condition.notify_all()
                raise

    def release(self, token: object) -> None:
        with self._condition:
            if token not in self._active:
                raise ValueError("unknown or already released admission lease")
            self._active.remove(token)
            self._condition.notify_all()

    @contextmanager
    def attempt(self):
        token = self.acquire()
        try:
            yield
        finally:
            self.release(token)
