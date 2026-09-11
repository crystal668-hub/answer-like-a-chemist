"""Bounded execution of record continuations at attempt and scoring boundaries."""

from __future__ import annotations

import heapq
import itertools
import time
from collections import deque
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass
from pathlib import Path
from functools import wraps
from typing import Any, Callable

from benchmarking.runtime.cancellation import BenchmarkCancelledError
from benchmarking.runtime.attempt_finalization import write_evidence, read_evidence
from benchmarking.core.contracts import (
    AnswerPayload,
    FailureInfo,
    RecoveryInfo,
    RunnerResult,
    RunStatus,
)


def persist_runner_result(path: Path, result: RunnerResult) -> Path:
    write_evidence(path, asdict(result))
    return path


def load_runner_result(path: Path) -> RunnerResult:
    payload = read_evidence(path)
    return RunnerResult(
        status=RunStatus(payload["status"]),
        answer=AnswerPayload(**payload["answer"]),
        raw=payload["raw"],
        runner_meta=payload["runner_meta"],
        failure=FailureInfo(**payload["failure"]) if payload.get("failure") else None,
        recovery=RecoveryInfo(**payload["recovery"])
        if payload.get("recovery")
        else None,
    )


@dataclass(frozen=True)
class WorkStep:
    operation: Callable[[], Any]
    kind: str = "attempt"


@dataclass(frozen=True)
class RetryDelay:
    seconds: float
    wait: Callable[[float], Any] = time.sleep


def staged(function):
    @wraps(function)
    def wrapper(*args, staged=False, **kwargs):
        steps = function(*args, **kwargs)
        if staged:
            return steps
        value = None
        error = None
        while True:
            try:
                step = steps.throw(error) if error is not None else steps.send(value)
            except StopIteration as done:
                return done.value
            value, error = None, None
            try:
                if isinstance(step, RetryDelay):
                    step.wait(step.seconds)
                else:
                    value = step.operation()
            except Exception as exc:
                error = exc

    wrapper.supports_steps = True
    return wrapper


class AttemptQueueExecutor:
    def __init__(self, max_workers, cancellation_token, *, clock=time.monotonic):
        self.limit = max_workers
        self.token = cancellation_token
        self.clock = clock
        self.groups = {}
        self.rotation = deque()
        self.scoring = deque()
        self.delayed = []
        self.sequence = itertools.count()
        self.completed = deque()
        self.active = {}
        self.record_keys = set()
        self.attempts = ThreadPoolExecutor(max_workers=max_workers)
        self.evaluations = ThreadPoolExecutor(max_workers=1)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.attempts.shutdown(wait=True, cancel_futures=True)
        self.evaluations.shutdown(wait=True, cancel_futures=True)

    def submit(self, function, **kwargs):
        future = Future()
        group = kwargs["group"].id
        for record in kwargs.get("records", []):
            key = (group, record.record_id)
            if key in self.record_keys:
                raise ValueError(f"duplicate queued record: {key}")
            self.record_keys.add(key)
        context = (future, function(staged=True, **kwargs), group)
        if group not in self.groups:
            self.groups[group] = deque()
            self.rotation.append(group)
        self.groups[group].append((context, None, None, None))
        return future

    def _advance(self, context, value=None, error=None):
        future, steps, group = context
        if future.done():
            return
        try:
            step = steps.throw(error) if error else steps.send(value)
        except StopIteration as done:
            future.set_result(done.value)
            self.completed.append(future)
            return
        except Exception as exc:
            future.set_exception(exc)
            self.completed.append(future)
            return
        if isinstance(step, RetryDelay):
            heapq.heappush(
                self.delayed,
                (self.clock() + step.seconds, next(self.sequence), context),
            )
        elif step.kind == "score":
            self.scoring.append((context, step))
        else:
            self.groups[group].append((context, step, None, None))

    def run(self):
        while (
            self.active
            or self.delayed
            or self.scoring
            or any(self.groups.values())
            or self.completed
        ):
            while self.completed:
                yield self.completed.popleft()
            while self.delayed and (
                self.delayed[0][0] <= self.clock() or self.token.is_cancelled
            ):
                _, _, context = heapq.heappop(self.delayed)
                self.groups[context[2]].append((context, None, None, None))
            if self.token.is_cancelled:
                pending = [item[0] for queue in self.groups.values() for item in queue]
                pending.extend(item[0] for item in self.scoring)
                for queue in self.groups.values():
                    queue.clear()
                self.scoring.clear()
                for context in pending:
                    self._advance(
                        context,
                        error=BenchmarkCancelledError("Benchmark run cancelled"),
                    )
            attempts = sum(kind == "attempt" for _, kind in self.active.values())
            while (
                attempts < self.limit
                and any(self.groups.values())
                and not self.token.is_cancelled
            ):
                group = self.rotation[0]
                self.rotation.rotate(-1)
                if not self.groups[group]:
                    continue
                context, step, value, error = self.groups[group].popleft()
                if step is None:
                    self._advance(context, value, error)
                    continue

                def execute(operation=step.operation):
                    self.token.raise_if_cancelled()
                    return operation()

                self.active[self.attempts.submit(execute)] = (context, "attempt")
                attempts += 1
            if (
                self.scoring
                and not any(kind == "score" for _, kind in self.active.values())
                and not self.token.is_cancelled
            ):
                context, step = self.scoring.popleft()

                def evaluate(operation=step.operation):
                    self.token.raise_if_cancelled()
                    return operation()

                self.active[self.evaluations.submit(evaluate)] = (context, "score")
            if self.active:
                done, _ = wait(self.active, timeout=0.05, return_when=FIRST_COMPLETED)
                for future in done:
                    context, _ = self.active.pop(future)
                    try:
                        value = future.result()
                    except Exception as exc:
                        self._advance(context, error=exc)
                    else:
                        self._advance(context, value)
            elif self.delayed and not self.completed:
                self.token.wait(min(0.05, max(0, self.delayed[0][0] - self.clock())))
