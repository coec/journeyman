import sys
from pathlib import Path
from runpy import run_path


def _runner():
    runner_path = (
        Path(__file__).resolve().parents[1]
        / "bin"
        / "journeyman-runner"
    )
    bin_dir = str(runner_path.parent)

    sys.path.insert(0, bin_dir)
    try:
        return run_path(str(runner_path))
    finally:
        sys.path.remove(bin_dir)


def test_local_runner_execution_transitions_publish_capacity_immediately():
    runner = _runner()
    publish = runner["publish_local_runner_execution_state"]
    calls = []

    publish.__globals__["update_local_runner_heartbeat"] = (
        lambda **kwargs: calls.append(kwargs)
    )

    publish(True)
    publish(False)

    assert calls == [
        {
            "version": "local",
            "running_jobs": 1,
            "status_message": "Executing a local Job",
        },
        {
            "version": "local",
            "running_jobs": 0,
            "status_message": "Ready",
        },
    ]


def test_local_runner_execution_state_and_periodic_heartbeat_share_lock():
    source = (
        Path(__file__).resolve().parents[1]
        / "bin"
        / "journeyman-runner"
    ).read_text(encoding="utf-8")

    assert "local_runner_state_lock = threading.Lock()" in source
    assert "with local_runner_state_lock:" in source
    assert "publish_local_runner_execution_state(True)" in source
    assert "publish_local_runner_execution_state(False)" in source
