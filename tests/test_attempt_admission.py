from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from benchmarking.runtime.attempt_admission import AttemptAdmissionController
from benchmarking.runtime.cancellation import BenchmarkCancelledError, CancellationReason, CancellationToken


def test_capacity_waits_until_release_and_rejects_duplicate_release():
    controller = AttemptAdmissionController(max_attempts=1)
    lease = controller.acquire()
    waiting = threading.Event()

    def acquire():
        waiting.set()
        return controller.acquire()

    with ThreadPoolExecutor() as executor:
        future = executor.submit(acquire)
        assert waiting.wait(1)
        assert not future.done()
        controller.release(lease)
        second = future.result(timeout=1)
        with pytest.raises(ValueError):
            controller.release(lease)
        controller.release(second)


def test_cancellation_wakes_waiter_without_starting_attempt():
    token = CancellationToken()
    controller = AttemptAdmissionController(max_attempts=1, cancellation_token=token)
    lease = controller.acquire()
    with ThreadPoolExecutor() as executor:
        future = executor.submit(controller.acquire)
        token.cancel(CancellationReason(source="test"))
        with pytest.raises(BenchmarkCancelledError):
            future.result(timeout=1)
    controller.release(lease)
    with pytest.raises(BenchmarkCancelledError):
        controller.acquire()


def test_exception_releases_attempt_and_invalid_limits_fail():
    controller = AttemptAdmissionController(max_attempts=1)
    with pytest.raises(RuntimeError):
        with controller.attempt():
            raise RuntimeError("attempt failed")
    with controller.attempt():
        pass
    with pytest.raises(ValueError):
        AttemptAdmissionController(max_attempts=0)
