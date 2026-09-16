from scripts.benchmark_docker_wait import CHILD


def test_docker_wait_acceptance_child_has_finalization_signal_handler():
    assert "signal.SIGTERM" in CHILD
    assert "supervisor-finalized" in CHILD
