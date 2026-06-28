import time

from coscience.executor import is_running, launch_detached, terminate_detached


def test_launch_detached_runs_and_reports_liveness(tmp_path):
    token = launch_detached(f"sleep 0.5; echo done > {tmp_path/'d.txt'}")
    assert is_running(token) is True
    deadline = time.time() + 5
    while is_running(token) and time.time() < deadline:
        time.sleep(0.05)
    assert is_running(token) is False
    assert (tmp_path / "d.txt").read_text().strip() == "done"


def test_launch_detached_runs_in_given_cwd(tmp_path):
    workdir = tmp_path / "work"
    workdir.mkdir()
    token = launch_detached("echo hi > here.txt", cwd=workdir)
    deadline = time.time() + 5
    while is_running(token) and time.time() < deadline:
        time.sleep(0.05)
    assert (workdir / "here.txt").read_text().strip() == "hi"


def test_terminate_detached_stops_a_long_job():
    token = launch_detached("sleep 30")
    assert is_running(token) is True
    terminate_detached(token)
    deadline = time.time() + 5
    while is_running(token) and time.time() < deadline:
        time.sleep(0.05)
    assert is_running(token) is False
