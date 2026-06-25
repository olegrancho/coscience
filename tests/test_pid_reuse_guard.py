import subprocess
import time

import pytest

from coscience.executor import (is_running, process_token, terminate_detached)


def _spawn(cmd="sleep 30"):
    return subprocess.Popen(cmd, shell=True, start_new_session=True,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def test_process_token_has_pid_and_nonempty_starttime():
    p = _spawn()
    try:
        tok = process_token(p.pid)
        assert tok.startswith(f"{p.pid}:")
        assert tok.split(":", 1)[1]  # start-time present
    finally:
        p.kill(); p.wait()


def test_is_running_true_for_matching_token():
    p = _spawn()
    try:
        assert is_running(process_token(p.pid)) is True
    finally:
        p.kill(); p.wait()


def test_is_running_false_for_reused_pid_token():
    # Same live PID, but a stale start-time => simulated PID reuse.
    p = _spawn()
    try:
        real = process_token(p.pid)
        pid, st = real.split(":")
        stale = f"{pid}:{int(st) + 1}"
        assert is_running(stale) is False   # identity mismatch -> treated as gone
        assert is_running(real) is True     # control: the real token still matches
    finally:
        p.kill(); p.wait()


def test_terminate_is_noop_for_reused_pid_token():
    p = _spawn()
    try:
        real = process_token(p.pid)
        pid, st = real.split(":")
        stale = f"{pid}:{int(st) + 1}"
        terminate_detached(stale, grace=0.3)
        assert is_running(real) is True      # real process must NOT have been killed
    finally:
        p.kill(); p.wait()


def test_terminate_kills_matching_token():
    p = _spawn()
    terminate_detached(process_token(p.pid), grace=1.0)
    assert p.wait(timeout=3) is not None     # process actually exited


def test_is_running_false_after_process_dies():
    p = _spawn()
    tok = process_token(p.pid)
    p.kill(); p.wait()
    time.sleep(0.05)
    assert is_running(tok) is False


def test_legacy_int_pid_liveness_preserved():
    p = _spawn()
    try:
        assert is_running(p.pid) is True     # bare int => liveness-only (old behavior)
    finally:
        p.kill(); p.wait()
    time.sleep(0.05)
    assert is_running(p.pid) is False        # dead pid
