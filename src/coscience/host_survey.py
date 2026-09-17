"""An agent's survey of a server, on top of the standard probe (O11). The agent owns
the final word on what a server offers; the human decides what enters the pool."""
import json
import math
from pathlib import Path

from coscience.resources import _parse_gpus

# A survey's working directory is committed with the rest of the substrate
# (`git add -A`) — the agent may leave scratch files there (SURVEY_BRIEF allows it,
# inside its own working directory). Only the files coscience itself owns are
# tracked; anything the agent leaves behind stays untracked (review round 1, M1).
_GITIGNORE = "*\n!.gitignore\n!thread.md\n!proposal.json\n"

SURVEY_BRIEF = """You are surveying a compute server before it joins a research platform's pool.
A standard probe already ran; its facts, checks and proposed capacity are below.

Your job: find out what this server really offers and how it should be used, then write
your conclusions to proposal.json in your current working directory.

- Reach the server with `ssh <target>` (key login is already set up). Run whatever you
  need to be sure: hardware, GPUs and drivers, memory, disk under the run root, load and
  other users' processes, schedulers, container runtimes, CUDA.
- You may create and delete files only inside the run root on the server and inside your
  own working directory here. Do not install system packages, change the server's
  configuration, or stop anyone's processes. If you need a tool the server lacks, install
  it for your user inside the run root, and say so in the notes.
- Confirm or correct the proposed capacity and GPU cards. Offer less than the hardware
  when other people use the server.
- A failed check stays failed. If you judge it harmless for this platform's work, add an
  override with a concrete reason; otherwise leave it and say what must be fixed.
- Write proposal.json exactly in this shape (omit nothing; use [] for none):
  {"capacity": {"cpu": <threads to offer>, "memory_gb": <GB to offer>},
   "gpus": [{"model": "<name>", "vram_gb": <GB>}],
   "notes": "<usage rules and anything a sprint agent must know, a few sentences>",
   "overrides": [{"check": "<failed check name>", "reason": "<why it is harmless>"}]}
- End your reply with a short summary for the human: what you found, what you changed
  from the probe's proposal, and anything they must decide."""


def ensure_gitignore(tdir: Path) -> None:
    """Write (or rewrite) the survey directory's `.gitignore`. Idempotent, and
    cheap enough to call on every turn's launch."""
    path = Path(tdir) / ".gitignore"
    if not path.is_file() or path.read_text() != _GITIGNORE:
        path.write_text(_GITIGNORE)


def render_first_prompt(record: dict, entry: dict | None) -> str:
    declared = record.get("declared", {})
    parts = [SURVEY_BRIEF, "",
             f"SERVER: {record.get('name', '')}  ssh target: {declared.get('ssh', '')}  "
             f"run root: {declared.get('run_root', '')}",
             "CHECKS:"]
    parts += [f"- {c.get('name')}: {'ok' if c.get('ok') else 'FAILED'}"
              + (f" — {c.get('detail')}" if c.get('detail') else "")
              for c in record.get("checks", [])]
    parts += ["PROBE FACTS:", json.dumps(record.get("facts", {}), indent=2, sort_keys=True),
              "PROBE PROPOSAL:", json.dumps(record.get("proposal", {}), indent=2, sort_keys=True)]
    if entry:
        parts += ["CURRENT POOL ENTRY:", json.dumps(entry, indent=2, sort_keys=True)]
    return "\n".join(parts)


def render_reprobe_notice(record: dict) -> str:
    """Prepended to a follow-up turn's prompt when the server was probed again
    since the agent's last turn (review round 1, I1): the new ssh/run_root and
    CHECKS block, so overrides the agent writes next are judged against what is
    actually current, not what it last saw."""
    declared = record.get("declared", {})
    parts = ["[SYSTEM] This server was probed again since your last turn — the facts below "
             "may have changed. Any overrides you wrote earlier no longer apply automatically; "
             "review the checks and update proposal.json if anything changed.",
             f"SERVER: {record.get('name', '')}  ssh target: {declared.get('ssh', '')}  "
             f"run root: {declared.get('run_root', '')}",
             "CHECKS:"]
    parts += [f"- {c.get('name')}: {'ok' if c.get('ok') else 'FAILED'}"
              + (f" — {c.get('detail')}" if c.get('detail') else "")
              for c in record.get("checks", [])]
    return "\n".join(parts)


def probe_ref(record: dict) -> dict:
    """The (probed_at, ssh, run_root) triple that ties an accepted override to the
    exact probe the agent was shown (review round 1, I1)."""
    declared = record.get("declared", {})
    return {"probed_at": record.get("probed_at"),
            "ssh": declared.get("ssh"), "run_root": declared.get("run_root")}


def read_probe_ref(tdir: Path) -> dict | None:
    path = Path(tdir) / "probe-ref.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def write_probe_ref(tdir: Path, record: dict) -> None:
    (Path(tdir) / "probe-ref.json").write_text(json.dumps(probe_ref(record)))


def probe_ref_matches(ref: dict | None, record: dict) -> bool:
    if ref is None:
        return False
    current = probe_ref(record)
    try:
        same_time = abs(float(ref.get("probed_at") or 0) - float(current["probed_at"] or 0)) <= 1e-6
    except (TypeError, ValueError):
        same_time = ref.get("probed_at") == current["probed_at"]
    return (same_time and ref.get("ssh") == current["ssh"]
            and ref.get("run_root") == current["run_root"])


def read_proposal(tdir: Path, record: dict) -> tuple[dict | None, str]:
    path = Path(tdir) / "proposal.json"
    if not path.is_file():
        return None, ""
    try:
        raw = json.loads(path.read_text())
    except (ValueError, OSError) as exc:
        return None, f"proposal.json is not valid JSON: {exc}"
    try:
        return _validate(raw, record), ""
    except ValueError as exc:
        return None, str(exc)


def _validate(raw, record) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("proposal.json must be an object")
    capacity_raw = raw.get("capacity")
    if capacity_raw is None:
        capacity_raw = {}
    if not isinstance(capacity_raw, dict):
        raise ValueError("proposal capacity: must be an object")
    capacity = {}
    for key, val in capacity_raw.items():
        if (isinstance(val, bool) or not isinstance(val, (int, float))
                or not math.isfinite(val) or val < 0):
            raise ValueError(f"proposal capacity.{key}: must be a non-negative number")
        capacity[str(key)] = float(val)
    gpus = [{"model": g.model, "vram_gb": g.vram_gb}
            for g in _parse_gpus("proposal ", raw.get("gpus") or [])]
    failed = {c.get("name") for c in record.get("checks", []) if not c.get("ok")}
    overrides_raw = raw.get("overrides")
    if overrides_raw is None:
        overrides_raw = []
    if not isinstance(overrides_raw, list):
        raise ValueError("proposal overrides: must be a list")
    overrides = []
    for o in overrides_raw:
        if not isinstance(o, dict):
            raise ValueError("proposal overrides: each override must be an object")
        check, reason = o.get("check"), o.get("reason")
        if not isinstance(check, str) or not isinstance(reason, str):
            raise ValueError("proposal overrides: check and reason must be strings")
        reason = reason.strip()
        if check not in failed:
            raise ValueError(f"proposal override {check!r}: that check did not fail")
        if not reason:
            raise ValueError(f"proposal override {check!r}: needs a reason")
        overrides.append({"check": check, "reason": reason})
    return {"capacity": capacity, "gpus": gpus, "notes": str(raw.get("notes") or ""),
            "overrides": overrides}


def override_notes(record: dict, proposal: dict | None) -> str:
    """The `Override <check>: <reason>` lines for every failed check, or raise
    ValueError naming the first failed check without a reason."""
    failed = [c.get("name") for c in record.get("checks", []) if not c.get("ok")]
    reasons = {o["check"]: o["reason"] for o in (proposal or {}).get("overrides", [])}
    missing = [c for c in failed if c not in reasons]
    if missing:
        raise ValueError(f"check {missing[0]} failed and the survey gives no reason to accept it")
    return "\n".join(f"Override {c}: {reasons[c]}" for c in failed)


def collect(substrate, name: str, thread):
    """Collect a survey thread's in-flight turn, if any. Shared by `Service.get_survey`
    and the dispatcher's per-cycle sweep, so both apply the exact same collection."""
    from coscience import chat_agent
    return chat_agent.collect_into(
        substrate.repo_root, thread, substrate.survey_thread_dir(name),
        save=lambda t: substrate.save_survey_thread(name, t),
        commit=substrate.commit,
        label=f"server {name}: survey",
        interrupted_text="_(The survey agent stopped before replying — send a message to continue.)_")
