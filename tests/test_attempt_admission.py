import pytest

from benchmarking.runtime.attempt_admission import (
    AttemptAdmissionController,
    ResourceRequest,
)


def test_admission_tracks_and_releases_resources() -> None:
    controller = AttemptAdmissionController(total_cpus=2, total_memory_bytes=100, max_attempts=2)
    lease = controller.acquire(ResourceRequest(cpus=1, memory_bytes=40))
    snapshot = controller.capacity_snapshot()
    assert snapshot.used_cpus == 1
    assert snapshot.used_memory_bytes == 40
    controller.release(lease)
    assert controller.capacity_snapshot().used_cpus == 0


def test_admission_rejects_when_capacity_is_exhausted() -> None:
    controller = AttemptAdmissionController(total_cpus=1, total_memory_bytes=100)
    controller.acquire(ResourceRequest(cpus=1))
    with pytest.raises(RuntimeError, match="capacity exhausted"):
        controller.acquire(ResourceRequest(cpus=1))
