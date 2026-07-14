"""Harvest worker replies to feedback threads. The worker appends JSONL lines
{thread_id, text} to <sprint_dir>/feedback.out; we consume new bytes past a stored
offset and append them as 'worker' messages. Best-effort; never raises into a beat."""
from __future__ import annotations

import json
import time

from coscience import threads


def harvest_feedback(substrate, sprint_id: str) -> int:
    d = substrate.sprint_dir(sprint_id)
    out = d / "feedback.out"
    if not out.is_file():
        return 0
    off_path = d / "feedback.offset"
    try:
        offset = int(off_path.read_text().strip()) if off_path.is_file() else 0
    except (OSError, ValueError):
        offset = 0
    data = out.read_bytes()
    if offset >= len(data):
        return 0
    chunk = data[offset:].decode("utf-8", "replace")
    sprint = substrate.load_sprint(sprint_id)
    by_id = {t["id"]: t for t in sprint.threads}
    n = 0
    for line in chunk.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        t = by_id.get(str(ev.get("thread_id", "")))
        if t is not None and t.get("target") == "worker" and t.get("status") == "open":
            threads.append(t, "worker", str(ev.get("text", "")), "", now=time.time())
            n += 1
    if n:
        substrate.save_sprint(sprint)
    try:
        off_path.write_text(str(len(data)))
    except OSError:
        pass
    return n
