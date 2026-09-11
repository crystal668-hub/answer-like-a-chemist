import threading
from types import SimpleNamespace

from benchmarking.runtime.cancellation import CancellationToken
from benchmarking.workflow.attempt_queue import (
    AttemptQueueExecutor,
    RetryDelay,
    WorkStep,
    staged,
)


def test_slow_score_does_not_block_attempt_and_retry_does_not_hold_slot():
    events = []
    next_attempt = threading.Event()

    @staged
    def record(*, group, name):
        yield WorkStep(lambda: events.append(name + "-attempt"))
        if name == "retry":
            yield RetryDelay(0.05)
            yield WorkStep(lambda: events.append("retry-second"))
        if name == "slow":
            yield WorkStep(lambda: next_attempt.wait(2), "score")
        else:
            next_attempt.set()
        return name

    with AttemptQueueExecutor(1, CancellationToken()) as queue:
        futures = [
            queue.submit(record, group=SimpleNamespace(id="a"), name=name)
            for name in ("slow", "retry", "next")
        ]
        assert set(queue.run()) == set(futures)
    assert events.index("next-attempt") < events.index("retry-second")
    assert [f.result() for f in futures] == ["slow", "retry", "next"]


def test_group_round_robin_and_fifo():
    starts = []

    @staged
    def record(*, group, name):
        yield WorkStep(lambda: starts.append(name))
        return name

    with AttemptQueueExecutor(1, CancellationToken()) as queue:
        for group, name in (("a", "a1"), ("a", "a2"), ("b", "b1"), ("b", "b2")):
            queue.submit(record, group=SimpleNamespace(id=group), name=name)
        list(queue.run())
    assert starts == ["a1", "b1", "a2", "b2"]


def test_cancelled_queue_starts_no_new_work():
    from benchmarking.runtime.cancellation import (
        CancellationReason,
        BenchmarkCancelledError,
    )

    token = CancellationToken()
    starts = []

    @staged
    def record(*, group, name):
        try:

            def attempt():
                starts.append(name)
                token.cancel(CancellationReason(source="test"))

            yield WorkStep(attempt)
            yield RetryDelay(30)
            yield WorkStep(lambda: starts.append("unexpected"))
        except BenchmarkCancelledError:
            return "cancelled"

    with AttemptQueueExecutor(1, token) as queue:
        for name in ("one", "two", "three"):
            queue.submit(record, group=SimpleNamespace(id="group"), name=name)
        list(queue.run())
    assert starts == ["one"]


def test_scoring_wait_does_not_occupy_attempt_worker():
    score_started = threading.Event()
    second_attempt = threading.Event()

    @staged
    def record(*, group, name):
        if name == "second":
            yield WorkStep(lambda: (score_started.wait(2), second_attempt.set()))
        else:
            yield WorkStep(lambda: None)

            def judge():
                score_started.set()
                assert second_attempt.wait(2), "attempt was blocked by judge"

            yield WorkStep(judge, "score")
        return name

    with AttemptQueueExecutor(1, CancellationToken()) as queue:
        first = queue.submit(record, group=SimpleNamespace(id="g"), name="first")
        second = queue.submit(record, group=SimpleNamespace(id="g"), name="second")
        list(queue.run())
        assert first.result() == "first"
        assert second.result() == "second"
