"""A chat agent kept running between replies (B3).

Every chat turn used to be a fresh `claude -p --resume`: process start, the session
hooks, and re-reading the whole conversation before the first token, so a quick
back-and-forth felt slow. Instead one `claude -p --input-format stream-json` process
per thread now stays up after replying and takes the next message on its input; after
`idle` seconds with nothing to say it closes, and the next message starts it again with
`--resume`, exactly as before.

This module is that process's host, run detached:

    python -m coscience.chat_session <thread_dir> --prompt-file F --workdir W --scope S
        --session-id ID [--resume] [--model M] [--idle 600]

It speaks the same files a one-shot turn does — the turn's stream into `turn.out`, its
exit code into `turn.exit` — so collecting a reply is unchanged. Its own control files
live outside the substrate (which is committed with `git add -A` and synced to other
machines) in a per-thread directory under the system temp dir: the poster hands it a
later message as `inbox.json`, under `session.lock`, and only while `session.json`
says the host is idle and was started for the same scope, model, workdir and session;
otherwise (scope changed, host gone or closing) the poster stops it and starts a fresh
one. The lock is what makes "about to close" and "a message just arrived" exclusive:
the host decides to close only while holding it, after checking the inbox is empty."""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

IDLE_DEFAULT = 600.0
POLL = 0.3

META, INBOX, LOCK = "session.json", "inbox.json", "session.lock"
READ_TOOLS = ["Read", "Glob", "Grep", "LS", "WebSearch", "WebFetch"]


def control_dir(thread_dir: Path) -> Path:
    """This machine's control files for a thread's host, never inside the substrate."""
    key = hashlib.sha1(str(Path(thread_dir).resolve()).encode()).hexdigest()[:16]
    root = os.environ.get("COSCIENCE_CHAT_CONTROL_DIR") or str(Path(tempfile.gettempdir()) / "coscience-chat")
    return Path(root) / key


@contextlib.contextmanager
def locked(thread_dir: Path):
    ctl = control_dir(thread_dir)
    ctl.mkdir(parents=True, exist_ok=True)
    with open(ctl / LOCK, "a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def read_meta(thread_dir: Path) -> dict:
    try:
        return json.loads((control_dir(thread_dir) / META).read_text())
    except (OSError, ValueError):
        return {}


def _write_json(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(path)


def claude_argv(claude_bin: str, scope: str, session_id: str, resume: bool, model: str) -> list[str]:
    argv = [claude_bin, "-p", "--input-format", "stream-json", "--output-format", "stream-json",
            "--verbose"]
    argv += ["--resume", session_id] if resume else ["--session-id", session_id]
    if model:
        argv += ["--model", model]
    if scope == "full":
        argv += ["--dangerously-skip-permissions"]
    else:
        argv += ["--allowedTools", *READ_TOOLS]
    return argv


def _user_line(text: str) -> str:
    return json.dumps({"type": "user", "message": {"role": "user", "content": text}}) + "\n"


def _run_turn(proc, thread_dir: Path, prompt: str) -> int | None:
    """Send one message and stream its events into turn.out until its result. Returns
    the turn's exit code, or None when claude ended without one (the host then ends).
    The caller writes turn.exit, after marking the host idle: the exit file is what
    lets the next message be sent, and it must find the host ready for it."""
    out = thread_dir / "turn.out"
    try:
        proc.stdin.write(_user_line(prompt))
        proc.stdin.flush()
    except (BrokenPipeError, OSError):
        return None
    with open(out, "a", encoding="utf-8") as fh:
        for line in proc.stdout:
            fh.write(line)
            fh.flush()
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("type") == "result":
                return 1 if event.get("is_error") else 0
    return None


def _set_state(thread_dir: Path, state: str) -> None:
    meta = read_meta(thread_dir)
    if meta:
        meta["state"] = state
        meta["at"] = time.time()
        _write_json(control_dir(thread_dir) / META, meta)


def _next_message(thread_dir: Path, idle: float) -> str | None:
    """Wait for the next message; None once `idle` seconds pass with none. Closing is
    decided under the lock, after one last look at the inbox."""
    deadline = time.monotonic() + idle
    inbox = control_dir(thread_dir) / INBOX
    while True:
        if inbox.exists() or time.monotonic() >= deadline:
            with locked(thread_dir):
                if inbox.exists():
                    try:
                        prompt = json.loads(inbox.read_text()).get("prompt", "")
                    except (OSError, ValueError):
                        prompt = ""
                    inbox.unlink(missing_ok=True)
                    _set_state(thread_dir, "busy")
                    return prompt
                if time.monotonic() >= deadline:
                    _set_state(thread_dir, "closing")
                    return None
        time.sleep(POLL)


def host(thread_dir: Path, prompt: str, *, workdir: str, scope: str, session_id: str,
         resume: bool, model: str = "", idle: float = IDLE_DEFAULT, claude_bin: str = "claude") -> int:
    from coscience.executor import process_token
    proc = subprocess.Popen(claude_argv(claude_bin, scope, session_id, resume, model), cwd=workdir,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, bufsize=1)
    with locked(thread_dir):
        _write_json(control_dir(thread_dir) / META, {
            "token": process_token(os.getpid()), "scope": scope, "model": model,
            "workdir": workdir, "session_id": session_id, "state": "busy", "at": time.time(),
            "thread_dir": str(thread_dir), "idle_limit": idle})
    code = 1
    try:
        while prompt is not None:
            code = _run_turn(proc, thread_dir, prompt)
            if code is None:          # claude went away mid-turn: say so, as a turn exit
                proc.wait(timeout=30)
                (thread_dir / "turn.exit").write_text(f"{proc.returncode or 1}\n")
                return proc.returncode or 1
            with locked(thread_dir):
                _set_state(thread_dir, "idle")
                (thread_dir / "turn.exit").write_text(f"{code}\n")
            prompt = _next_message(thread_dir, idle)
        return code
    finally:
        with contextlib.suppress(Exception):
            proc.stdin.close()
        with contextlib.suppress(Exception):
            proc.wait(timeout=30)
        if proc.poll() is None:
            proc.kill()
        with locked(thread_dir):
            meta = read_meta(thread_dir)
            if meta.get("token") == process_token(os.getpid()):
                (control_dir(thread_dir) / META).unlink(missing_ok=True)


def deliver(thread_dir: Path, prompt: str, *, workdir: str, scope: str, session_id: str,
            model: str) -> str | None:
    """Hand a message to this thread's running host, if one is idle and was started for
    the same session, scope, model and workdir. Returns the host's token, or None when
    a fresh one must be started. A host that does not match is stopped first, so two
    processes never hold one session."""
    from coscience.executor import is_running, terminate_detached
    with locked(thread_dir):
        meta = read_meta(thread_dir)
        token = meta.get("token", "")
        alive = bool(token) and is_running(token)
        same = (meta.get("scope") == scope and meta.get("model", "") == (model or "")
                and meta.get("workdir") == workdir and meta.get("session_id") == session_id)
        if alive and same and meta.get("state") == "idle":
            for f in ("turn.out", "turn.exit"):
                (thread_dir / f).unlink(missing_ok=True)
            _write_json(control_dir(thread_dir) / INBOX, {"prompt": prompt, "at": time.time()})
            return token
    if alive:
        terminate_detached(token)
    with locked(thread_dir):
        (control_dir(thread_dir) / META).unlink(missing_ok=True)
        (control_dir(thread_dir) / INBOX).unlink(missing_ok=True)
    return None


def live_sessions() -> list[dict]:
    """Every chat agent this machine is keeping up (for Compute): its thread folder,
    whether it is answering or waiting, and since when."""
    from coscience.executor import is_running
    root = Path(os.environ.get("COSCIENCE_CHAT_CONTROL_DIR")
                or Path(tempfile.gettempdir()) / "coscience-chat")
    out = []
    for meta_file in sorted(root.glob(f"*/{META}")):
        try:
            meta = json.loads(meta_file.read_text())
        except (OSError, ValueError):
            continue
        if meta.get("token") and is_running(meta["token"]):
            out.append({"thread_dir": meta.get("thread_dir", ""), "state": meta.get("state", ""),
                        "since": meta.get("at"), "idle_limit": meta.get("idle_limit")})
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="host a chat agent between replies")
    ap.add_argument("thread_dir", type=Path)
    ap.add_argument("--prompt-file", required=True)
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--scope", default="read")
    ap.add_argument("--session-id", required=True)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--model", default="")
    ap.add_argument("--idle", type=float, default=IDLE_DEFAULT)
    ap.add_argument("--claude-bin", default="claude")
    a = ap.parse_args(argv)
    prompt = Path(a.prompt_file).read_text(encoding="utf-8")
    with contextlib.suppress(OSError):
        os.unlink(a.prompt_file)
    return host(a.thread_dir, prompt, workdir=a.workdir, scope=a.scope, session_id=a.session_id,
                resume=a.resume, model=a.model, idle=a.idle, claude_bin=a.claude_bin)


if __name__ == "__main__":
    sys.exit(main())
