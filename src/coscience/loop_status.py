"""A compact, self-refreshing status block for the long-running loop clients.

Renders four lines — a header, what happened last iteration, a rolling one-hour
summary, and (when the client uses Claude) current Claude usage as small colored
bars. On a TTY the block refreshes in place and a background heartbeat keeps the
clock/uptime ticking between beats; when output is redirected to a log file it
falls back to one compact line per iteration so logs stay readable."""
from __future__ import annotations

import datetime as _dt
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
from collections import deque

_USAGE_RE = re.compile(r"(\w+):\s*(\d+)%\s*\(resets ([^)]+)\)")
_GREEN, _YELLOW, _RED, _RESET = "\x1b[32m", "\x1b[33m", "\x1b[31m", "\x1b[0m"
_WEEKDAYS = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}


def _fmt_reset(reset: str, mode: str) -> str:
    """`mode='time'` -> 'HH:MM'; `mode='date'` -> 'D Mon' for the next occurrence
    of the named weekday. Falls back to the raw string if it can't be parsed."""
    reset = reset.strip()
    if mode == "time":
        m = re.search(r"(\d{1,2}):(\d{2})", reset)
        return f"{int(m.group(1)):02d}:{m.group(2)}" if m else reset
    wm = re.search(r"\b(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\b", reset)
    if not wm:
        return reset
    today = _dt.date.today()
    days = (_WEEKDAYS[wm.group(1)] - today.weekday()) % 7
    return (today + _dt.timedelta(days=days)).strftime("%-d %b")


def _fmt_dur(seconds: float) -> str:
    s = int(max(0, seconds))
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    if h:
        return f"{h}h{m:02d}m"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"


def _bar(pct: int, width: int = 6, color: bool = True) -> str:
    filled = max(0, min(width, round(pct / 100 * width)))
    bar = "█" * filled + "░" * (width - filled)
    if not color:
        return bar
    c = _GREEN if pct < 60 else _YELLOW if pct < 85 else _RED
    return f"{c}{bar}{_RESET}"


def format_usage(raw: str, color: bool = True) -> str:
    """Turn '5h: 22% (resets Sun 2:10) | week: 83% (resets Sun 23:00) [live]' into
    compact bars: '5h ▓▓░░░░ 22% 02:10   wk █████░ 83% 28 Jun'. The 5h window keeps
    just the reset time; the week is relabelled 'wk' and shows the reset date. Falls
    back to the raw string if it doesn't match."""
    found = _USAGE_RE.findall(raw)
    if not found:
        return raw
    chunks = []
    for label, pct, reset in found:
        pct = int(pct)
        if label.lower().startswith("week"):
            name, when = "wk", _fmt_reset(reset, "date")
        else:
            name, when = label, _fmt_reset(reset, "time")
        chunks.append(f"{name} {_bar(pct, 6, color)} {pct}% {when}")
    return "   ".join(chunks)


def default_usage_cmd():
    """Where to read live Claude usage. Override with COSCIENCE_USAGE_CMD;
    otherwise auto-detect the Claude Code usage skill if it's installed."""
    env = os.environ.get("COSCIENCE_USAGE_CMD")
    if env:
        return shlex.split(env)
    script = os.path.expanduser("~/.claude/skills/usage/usage.py")
    if os.path.isfile(script):
        return ["python3", script]
    return None


class LoopStatus:
    def __init__(self, name, uses_claude=False, usage_cmd=None, clock=time.time):
        self.name = name
        self.uses_claude = uses_claude
        self.usage_cmd = usage_cmd if usage_cmd is not None else default_usage_cmd()
        self._clock = clock
        self.start = clock()
        self.iters = 0
        self.last_line = "starting…"
        self._events: deque = deque()            # (t, counters) within the last hour
        self._usage_text = ""
        self._usage_at = 0.0
        self._drawn = False
        self._tty = sys.stdout.isatty()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._hb = None

    # --- state ---------------------------------------------------------------
    def record(self, last_line: str, counters: dict | None = None) -> None:
        with self._lock:
            now = self._clock()
            self.iters += 1
            self.last_line = last_line
            self._events.append((now, dict(counters or {})))
            cutoff = now - 3600
            while self._events and self._events[0][0] < cutoff:
                self._events.popleft()
            self._render_locked()

    def _hour_summary(self) -> str:
        agg: dict = {}
        for _, c in self._events:
            for k, v in c.items():
                agg[k] = agg.get(k, 0) + v
        runs = len(self._events)
        parts = [f"{v} {k}" for k, v in agg.items() if v]
        tail = (" · " + " · ".join(parts)) if parts else ""
        return f"{runs} run{'' if runs == 1 else 's'}{tail}"

    def _claude_line(self) -> str:
        if not self.uses_claude:
            return "claude: not used (shell executor)"
        if self.usage_cmd:
            now = self._clock()
            if not self._usage_text or now - self._usage_at > 60:   # refresh usage ~60s
                try:
                    out = subprocess.run(self.usage_cmd, capture_output=True,
                                         text=True, timeout=15)
                    lines = (out.stdout or "").strip().splitlines()
                    self._usage_text = lines[0] if lines else ""
                except Exception:
                    self._usage_text = ""
                self._usage_at = now
            if self._usage_text:
                return f"claude: {format_usage(self._usage_text, color=self._tty)}"
        return f"claude: in use ({self.iters} call{'' if self.iters == 1 else 's'} this run)"

    def lines(self) -> list[str]:
        clock = time.strftime("%H:%M:%S")
        up = _fmt_dur(self._clock() - self.start)
        return [
            f"{self.name} · iter {self.iters} · up {up} · {clock}",
            f"last:   {self.last_line}",
            f"1h:     {self._hour_summary()}",
            self._claude_line(),
        ]

    # --- rendering -----------------------------------------------------------
    def render(self) -> None:
        with self._lock:
            self._render_locked()

    def _render_locked(self) -> None:
        lines = self.lines()
        if self._tty:
            width = shutil.get_terminal_size((100, 24)).columns
            out = sys.stdout
            if self._drawn:
                out.write("\x1b[4A")                       # cursor up 4 lines
            for ln in lines:
                body = ln if "\x1b" in ln else ln[: width - 1]  # don't slice colour codes
                out.write("\x1b[2K" + body + "\n")          # clear line, write
            out.flush()
            self._drawn = True
        else:
            clock = time.strftime("%H:%M:%S")
            print(f"[{clock}] {self.name} #{self.iters}: {self.last_line}", flush=True)

    # --- heartbeat (TTY only) ------------------------------------------------
    def start_heartbeat(self, every: float = 5.0) -> None:
        """Re-render every `every` seconds so uptime/clock tick between beats."""
        if not self._tty or self._hb is not None:
            return

        def _tick():
            while not self._stop.wait(every):
                self.render()
        self._hb = threading.Thread(target=_tick, daemon=True)
        self._hb.start()

    def stop(self) -> None:
        self._stop.set()
