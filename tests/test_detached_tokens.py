import re
import time

from coscience.executor import is_running, launch_detached, process_token, terminate_detached


def test_launch_returns_identity_token():
    token = launch_detached("sleep 30")
    assert re.fullmatch(r"\d+:\d+", token)   # "<pid>:<starttime>", not a bare int
    assert is_running(token) is True
    terminate_detached(token)


def test_reused_pid_is_not_mistaken_for_the_original(tmp_path):
    token = launch_detached("true")          # exits almost immediately
    deadline = time.time() + 5
    while is_running(token) and time.time() < deadline:
        time.sleep(0.02)
    # craft a token with the same pid but a different start time -> must read as dead
    pid = int(token.split(":")[0])
    assert is_running(f"{pid}:999999999") is False


def test_legacy_bare_pid_string_degrades_to_liveness():
    # an implausible bare PID (no identity) is treated as plain liveness -> dead
    assert is_running("999999999") is False


def test_process_token_shape():
    token = process_token(1)                 # pid 1 exists; token is "1:<starttime>"
    assert token.startswith("1:")
