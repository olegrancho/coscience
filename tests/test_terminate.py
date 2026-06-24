import time

from coscience.executor import is_running, launch_detached, terminate_detached


def test_terminate_kills_running_job():
    pid = launch_detached("sleep 30")
    assert is_running(pid) is True
    terminate_detached(pid)
    deadline = time.time() + 5
    while is_running(pid) and time.time() < deadline:
        time.sleep(0.05)
    assert is_running(pid) is False


def test_terminate_kills_child_processes_too(tmp_path):
    # The shell spawns a child `sleep`; terminating the group must kill both.
    marker = tmp_path / "still_running.txt"
    # child writes the marker only AFTER the sleep finishes; if we kill the
    # group the sleep dies and the marker is never written.
    pid = launch_detached(f"sleep 30; echo done > {marker}")
    assert is_running(pid) is True
    terminate_detached(pid)
    deadline = time.time() + 5
    while is_running(pid) and time.time() < deadline:
        time.sleep(0.05)
    time.sleep(0.3)
    assert not marker.exists()


def test_terminate_dead_pid_is_noop():
    terminate_detached(999999)  # no such process — must not raise
