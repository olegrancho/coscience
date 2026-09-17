# O11 Let an Agent Own Server Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After the standard probe of a server, a human can ask an agent to survey it. The agent researches the server over SSH, confirms or corrects the proposed capacity and GPUs, writes the server's notes, and may explain away a failed check with a written reason. The human sees its proposal beside the probe's facts, can keep talking to the same session, and decides what enters the pool.

**Architecture:** A survey is a chat thread scoped to a server rather than a program. It reuses the program chat's machinery without copying it:
- the `ChatThread` model;
- the `thread.md` store, refactored to take a directory;
- `chat_agent.launch_turn` with `scope="full"`, resumable by session id;
- `chat_agent.collect_turn`, whose program-thread collector becomes a directory-and-save-callback helper that program chat calls through;
- the pause and usage gates;
- the call log, with `kind="survey"`.

The thread lives in `.coscience/host-surveys/<name>/`, and its first prompt carries a survey brief plus the probe record. The agent writes `proposal.json` in its working directory. The backend validates it and serves it with the thread. The dispatcher collects finished survey turns each cycle, as it does for chats. `confirm_host` and `update_host` accept failed checks only when the human asks to accept the agent's overrides and every failed check has a written reason; the reasons are kept in the server's notes. Everything stays behind `COSCIENCE_ALLOW_ONBOARDING`.

**Tech Stack:** Python 3 (FastAPI, PyYAML, pytest), React + Mantine + TanStack Query (vitest).

**Spec:** `docs/superpowers/specs/2026-09-14-multi-host-execution-design.md` (§5 Onboarding); todo item O11. Oleg's rulings, verbatim:
- "run standard tests but also use agent to do research and confirm. The ultimate responsibility will be on agent, and if it decides in needs better tools it must have access to ability to run commands"
- "I'd reuse existing framework as much as possible" (the full-scope PM chat)

## Global Constraints

- No commits or pushes without explicit approval; there are no commit steps in this run.
- Python: `~/venvs/coscience/bin/python -m pytest`, prefixed `PYTHONPATH=src` in the worktree (addopts already has `-q`; never add another). Frontend: from `frontend/`, `npx vitest run …` and `npx tsc -b`.
- Tests never launch Claude, ssh or rsync: inject `launch` the way program chat tests do (`Service.send_chat_message(..., launch=fake)` or whatever the chat send method's injectable parameter is named), and write `turn.out`/`turn.exit` by hand to simulate a finished turn.
- Every survey route calls `_require_onboarding()`. With `COSCIENCE_ALLOW_ONBOARDING` unset, they answer 403 and launch nothing.
- A survey turn launches only through the same gates program chat uses: paused → refused with `Paused — Resume in Compute to start a survey.`; `claude_usage_ok` false → refused with `Claude usage is exhausted — try again after the reset.` Both are `ValueError`, so the route answers 422. No thread message is written for a refused launch.
- A proposal is used only after validation. An invalid `proposal.json` is shown as `proposal_error` text and never applied, partly or silently.
- The agent's overrides never let a server in by themselves. `confirm_host` and `update_host` accept failed checks only with `accept_overrides=True`, and only when every failed check has an override with a non-empty reason. The accepted reasons are appended to the written entry's `notes` as `Override <check>: <reason>` lines.
- Program chat behaves exactly as before; the refactor changes no chat behaviour and no stored format.
- No change to this machine's Detect flow: surveys are for remote servers.

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `src/coscience/substrate.py` | modify | `_load_thread_at(dir, id)`, `_save_thread_at(dir, thread)`; program chat methods call through; `survey_thread_dir(name)`, `load_survey_thread(name)`, `save_survey_thread(name, thread)`, `list_survey_threads()` |
| `src/coscience/chat_agent.py` | modify | `collect_into(repo_root, thread, tdir, save, label)` holds `collect_thread`'s body; `collect_thread` calls it |
| `src/coscience/host_survey.py` | create | `SURVEY_BRIEF`, `render_first_prompt(record, entry)`, `read_proposal(tdir, record) -> (proposal, error)`, `override_notes(record, proposal)` |
| `src/coscience/service.py` | modify | `survey_host`, `get_survey`; `confirm_host`/`update_host` take `accept_overrides` |
| `src/coscience/http_api.py` | modify | `POST /api/hosts/{name}/survey`, `GET /api/hosts/{name}/survey`; `accept_overrides` on confirm and update bodies |
| `src/coscience/dispatcher.py` | modify | `_collect_surveys()` each cycle, beside `_collect_chats` |
| `tests/test_host_survey.py` | create | store, prompt, proposal validation, gates, collection, overrides, routes |
| `frontend/src/api.ts` | modify | `HostSurvey` types, `surveyHost`, `getSurvey`, `accept_overrides` |
| `frontend/src/components/SurveyPanel.tsx` | create | the survey conversation and proposal inside the server dialog |
| `frontend/src/components/AddHostModal.tsx` | modify | shows `SurveyPanel` after a probe; "Use proposal"; add/update with overrides |
| tests beside each frontend file | create/modify | the behaviour below |
| the spec | modify | "O11 as built" paragraph |

---

### Task 1: The backend runs, collects and applies a server survey

**Files:** all Python files in the table, and the spec.

**Interfaces:**
- Produces: `Service.survey_host(name: str, message: str = "", launch=None) -> dict`.
  - With no survey thread yet, it starts one (requires a probe record for `name`; the probe need not have passed every check, but `record["ok"]` must be true, i.e. SSH login worked).
  - With a thread, it sends `message` as a follow-up; an empty `message` on an existing thread is `ValueError("message is required")`, and a pending thread is `ValueError("the survey is still working on the previous message")`.
  - Returns `get_survey(name)`.
- Produces: `Service.get_survey(name: str) -> dict`, shaped `{"name", "pending": bool, "messages": [{role, text, at}], "proposal": dict | None, "proposal_error": str, "started": bool}`. It collects a finished turn first, as chat reads do. A server with no thread returns `started: False`, empty messages, `proposal: None`.
- Produces: `confirm_host(..., accept_overrides: bool = False)`, and `update_host(..., accept_overrides: bool = False)` for its new-SSH-target / new-run-root probe gate.
- Produces routes, each calling `_require_onboarding()`:
  - `POST /api/hosts/{name}/survey`, body `{"message": ""}` → `survey_host`; 404 when there is no probe record, 422 for `ValueError`;
  - `GET /api/hosts/{name}/survey` → `get_survey`.
  - `HostConfirmIn` and `HostUpdateIn` gain `accept_overrides: bool = False`.
- Produces the proposal schema that the agent writes to `proposal.json`, and that the dashboard reads after validation:

```json
{"capacity": {"cpu": 12, "memory_gb": 56},
 "gpus": [{"model": "NVIDIA GeForce RTX 2080 Ti", "vram_gb": 11}],
 "notes": "Shared lab box; long jobs only overnight.",
 "overrides": [{"check": "run_root_writable", "reason": "..."}]}
```

- [ ] **Step 1: Refactor the thread store and collector, with no behaviour change.**
  - In `substrate.py`, move the bodies of `load_chat_thread` and `save_chat_thread` into `_load_thread_at(d: Path, thread_id: str)` and `_save_thread_at(d: Path, thread: ChatThread)`. The program methods become one-line calls with `self.chat_thread_dir(program_id, id)`. Add:

```python
    def survey_thread_dir(self, name: str) -> Path:
        return self.repo_root / ".coscience" / "host-surveys" / name

    def load_survey_thread(self, name: str) -> "ChatThread | None":
        return self._load_thread_at(self.survey_thread_dir(name), name)

    def save_survey_thread(self, name: str, thread: ChatThread) -> None:
        self._save_thread_at(self.survey_thread_dir(name), thread)

    def list_survey_threads(self) -> list[ChatThread]:
        d = self.repo_root / ".coscience" / "host-surveys"
        found = [self.load_survey_thread(sub.name) for sub in (d.iterdir() if d.is_dir() else [])
                 if (sub / "thread.md").is_file()]
        return [t for t in found if t is not None]
```

  - In `chat_agent.py`, move `collect_thread`'s body into `collect_into(repo_root, thread, tdir: Path, save, commit, label: str)`. `save(thread)` persists the thread; `commit(msg)` commits; `label` prefixes the commit messages (for program chat it is `f"program {program_id}: chat {thread.id}"`, which gives the same messages as today). `collect_thread(substrate, program_id, thread)` becomes a call to it. The interrupted-turn message stays as it is for chat. `collect_into` takes an `interrupted_text` argument, defaulting to today's chat text, so a survey can say `_(The survey agent stopped before replying — send a message to continue.)_`.
  - Look at how chat thread directories are handled in the substrate's git ignore rules (`turn.out` and prompt files). Give `.coscience/host-surveys/*/turn.out` and `turn.exit` the same treatment: if chat's are ignored, add the survey paths to the same ignore mechanism; if chat's are committed, do nothing.
  - Run the existing chat tests (`grep -l chat tests/*.py`) and confirm they pass unchanged.

- [ ] **Step 2: Write the failing tests** in `tests/test_host_survey.py`. Use a tmp substrate. Write a probe record in `.coscience/host-probes/<name>.json` shaped like `host_probe`'s result plus `declared` and `probed_at`, as `tests/test_host_config.py` does. Set `COSCIENCE_ALLOW_ONBOARDING=1` with `monkeypatch` for route tests. Inject a fake `launch` that records its kwargs and returns `"123:456"`, and monkeypatch `claude_usage_ok` and `is_paused` the way chat tests do. Cases:
  - Starting a survey launches with `scope="full"`, `resume=False`, `workdir == str(substrate.survey_thread_dir("gpu1"))`, and a prompt that contains `SURVEY_BRIEF`, the record's `declared.ssh`, and every check name with its ok/failed state. The thread is saved `pending` with `agent_call` set. The call log has a `start` row with `kind == "survey"`.
  - A follow-up after a collected turn launches with `resume=True`, the stored `session_id`, and a prompt equal to the message.
  - A follow-up while pending raises `ValueError`. No probe record raises `NotFoundError`. A record with `ok: false` raises `ValueError` naming the failed login.
  - When paused, starting raises `ValueError` whose text starts with `Paused`, and `launch` is not called. Usage exhausted behaves the same with `Claude usage is exhausted`.
  - Collection: write `turn.exit` = `0` and a `turn.out` stream with a result event (copy the minimal stream shape from an existing chat collection test). `get_survey` then shows the agent's reply as a `pm` message and `pending: False`, and the call log has an `end` row. A dead token with no `turn.exit` gives the interrupted text.
  - A dispatcher cycle collects a finished survey turn without anyone calling `get_survey`. Use the dispatcher test fixture that already covers `_collect_chats`, or call `Dispatcher._collect_surveys()` directly.
  - `read_proposal`:
    - no file → `(None, "")`;
    - valid → the dict, with `vram_gb` as float;
    - non-JSON → `(None, "proposal.json is not valid JSON: …")`;
    - negative cpu → an error naming `capacity.cpu`;
    - a GPU missing `vram_gb` → the `_parse_gpus` message;
    - an override naming a check that did not fail → an error naming that check;
    - an override with an empty reason → an error naming that check.
  - `confirm_host`:
    - with a failed check and `accept_overrides=False` → the existing refusal;
    - with `accept_overrides=True` but no valid proposal, or a proposal missing an override for some failed check → `ValueError` naming the check without a reason;
    - with `accept_overrides=True` and a reason for every failed check → written, and the entry's `notes` end with `Override run_root_writable: <reason>`.
  - `update_host` with a changed SSH target whose probe has a failed check follows the same rule under `accept_overrides=True`.
  - Routes:
    - `POST /api/hosts/gpu1/survey` → 200 with `pending: true` (fake launch injected by monkeypatching `chat_agent.launch_turn`);
    - `GET` → 200;
    - with the switch unset, both → 403 and nothing launched.

- [ ] **Step 3: `host_survey.py`.**

```python
"""An agent's survey of a server, on top of the standard probe (O11). The agent owns
the final word on what a server offers; the human decides what enters the pool."""
import json
from pathlib import Path

from coscience.resources import _parse_gpus

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
    capacity = {}
    for key, val in (raw.get("capacity") or {}).items():
        if isinstance(val, bool) or not isinstance(val, (int, float)) or val < 0:
            raise ValueError(f"proposal capacity.{key}: must be a non-negative number")
        capacity[str(key)] = float(val)
    gpus = [{"model": g.model, "vram_gb": g.vram_gb}
            for g in _parse_gpus("proposal ", raw.get("gpus") or [])]
    failed = {c.get("name") for c in record.get("checks", []) if not c.get("ok")}
    overrides = []
    for o in raw.get("overrides") or []:
        check, reason = str((o or {}).get("check", "")), str((o or {}).get("reason", "")).strip()
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
```

Check `_parse_gpus`'s real signature and return type in `resources.py`, and adapt the `gpus` line to it.

- [ ] **Step 4: Service.**
  - `survey_host(name, message="", launch=None)`:
    - Load the probe record via `self._host_probe_path(name)` (`NotFoundError` if missing; `ValueError(f"the last probe of {name} could not log in; fix SSH and probe again")` if not `ok`).
    - Apply the pause and usage gates exactly as the chat send path does, but raise the `ValueError`s from Global Constraints instead of appending messages. Skip the gates when `launch` is injected, as chat does.
    - Load or create the thread (`ChatThread(id=name, title=f"Survey of {name}", scope="full", created_at=now)`).
    - First turn: prompt `render_first_prompt(record, current hosts entry or None)`, followed by `"\n\nHuman: " + message` when a message is given.
    - Later turns: prompt = `message`.
    - Append the human message to `messages` when non-empty.
    - Launch with `workdir=str(self.substrate.survey_thread_dir(name))` (create it first), `scope="full"`, `session_id=thread.session_id`, `resume=thread.turns_done > 0`, `model=""`.
    - Then `start_call(repo_root, "survey", token=...)`, save, and commit `f"server {name}: survey message"`.
  - `get_survey(name)`: if a thread exists and is pending, collect it through `chat_agent.collect_into` with `save=lambda t: self.substrate.save_survey_thread(name, t)`, `label=f"server {name}: survey"` and the survey interrupted text. Then return the shape from Interfaces, with `proposal, proposal_error = host_survey.read_proposal(dir, record)` when a record exists.
  - `confirm_host(..., accept_overrides=False)` and the probe gate in `update_host(..., accept_overrides=False)`: where they refuse failed checks today, when `accept_overrides` is true instead call `host_survey.override_notes(record, proposal)`. The proposal comes from `read_proposal`; a `proposal_error` is raised as `ValueError`. Append the returned lines to the entry's `notes`, on a new line after any existing notes, then continue.

- [ ] **Step 5: Dispatcher.** Add `_collect_surveys()`, called once per cycle right after the per-program chat loop. It catches every exception, as `_collect_chats` does. For each `substrate.list_survey_threads()` thread with `pending`, it collects through `chat_agent.collect_into` with the same arguments `get_survey` uses. Factor those arguments into one helper, `host_survey.collect(substrate, name, thread)`, so the service and the dispatcher share it.

- [ ] **Step 6: Routes.** `class SurveyIn(BaseModel): message: str = ""`. Add `POST /hosts/{name}/survey` and `GET /hosts/{name}/survey` with `_require_onboarding()` first, mapping `NotFoundError` to 404 and `ValueError` to 422. Add `accept_overrides: bool = False` to `HostConfirmIn` and `HostUpdateIn`, and pass it through. Define the survey routes before `PUT /hosts/{name}` if route order matters in this app.

- [ ] **Step 7: Run.** `PYTHONPATH=src ~/venvs/coscience/bin/python -m pytest tests/test_host_survey.py`, then every chat test file and `tests/test_host_config.py tests/test_host_onboarding.py tests/test_cli_dispatch.py tests/test_http_api.py`, then the full suite. Expected: PASS.

- [ ] **Step 8: Spec paragraph**, appended at the end of the spec:

```markdown
**O11 as built.** After a probe, a human can ask an agent to survey the server. The survey
is a full-scope chat session scoped to the server (`.coscience/host-surveys/<name>/`),
built on program chat's thread store, launch, collection, gates and call log (kind
`survey`). The agent reaches the server over SSH, may write only inside the run root and
its own working directory, and writes `proposal.json`: capacity, GPU cards, notes and
overrides. The backend validates the proposal before showing it; an invalid one is shown
as an error and never applied. A failed check stays failed: the server enters the pool
over it only when the human accepts overrides and every failed check has the agent's
written reason, and those reasons are kept in the server's notes. Behind
`COSCIENCE_ALLOW_ONBOARDING`. This machine's Detect is unchanged.
```

---

### Task 2: The server dialog shows the survey and uses its proposal

**Files:** `frontend/src/api.ts`, `frontend/src/components/SurveyPanel.tsx` (+ `SurveyPanel.test.tsx`), `frontend/src/components/AddHostModal.tsx` (+ its test).

**Interfaces:**
- Consumes (Task 1):
  - `POST /api/hosts/{name}/survey` body `{message}`, and `GET /api/hosts/{name}/survey`. Both return `HostSurvey`.
  - `accept_overrides` on `POST /api/hosts` and `PUT /api/hosts/{name}`.
  - The 403 when onboarding is off.
- Produces:

```ts
export interface SurveyOverride { check: string; reason: string }
export interface SurveyProposal {
  capacity: Record<string, number>; gpus: { model: string; vram_gb: number }[];
  notes: string; overrides: SurveyOverride[];
}
export interface HostSurvey {
  name: string; started: boolean; pending: boolean;
  messages: { role: string; text: string; at: number }[];
  proposal: SurveyProposal | null; proposal_error: string;
}
// api
surveyHost: (name: string, message = "") => POST, returns HostSurvey
getSurvey: (name: string) => GET, returns HostSurvey
```

  plus `accept_overrides?: boolean` on `confirmHost`'s and `HostUpdate`'s bodies.

- [ ] **Step 1: `api.ts`** — the types and methods above, following `probeHost`'s fetch/`j<>` pattern.

- [ ] **Step 2: `SurveyPanel.tsx`, test first.** Props: `{ name: string; onUseProposal: (p: SurveyProposal) => void }`.
  - It reads the survey with `useQuery({ queryKey: ["survey", name], queryFn: () => api.getSurvey(name), refetchInterval: (q) => q.state.data?.pending ? 3000 : false })`.
  - When `started` is false, it shows a button `Survey with an agent`, with the description `An agent logs in, checks what the probe found, and proposes capacity, cards and notes. It can run commands on the server.` Clicking calls `api.surveyHost(name)`, then sets the query data to the result.
  - When started, it shows the messages in order: `pm` messages rendered with the dashboard's existing markdown component (`Md`, as `ProgramDetail` uses), human messages as plain text. While pending it shows `The agent is working…`. When not pending it shows a `Textarea` plus `Send`, which calls `api.surveyHost(name, text)`.
  - When `proposal` is set, it shows a block titled `Agent's proposal`:
    - CPU, memory and cards in the same wording as the probe summary;
    - the notes;
    - each override as `Override <check>: <reason>` in orange;
    - a `Use proposal` button calling `onUseProposal(proposal)`.
  - `proposal_error` shows in red: `The agent's proposal can't be used: <error>`.
  - Errors from the calls (including 403 and 422) show their text in red below the panel.
  - Tests: not started → button → `surveyHost` called; pending shows the working text and no Send; messages render; the proposal block and `Use proposal` call back with the proposal; the error text renders; a rejected `surveyHost` shows its message.

- [ ] **Step 3: `AddHostModal.tsx`.**
  - In add and edit modes, once `probe?.ok`, render `<SurveyPanel name={name.trim()} onUseProposal={use} />` below the probe result block. In edit mode, when no fresh probe was run, render it whenever the host has a probe on record. Use `api.listHostProbes()` if the dialog already has it; otherwise render the panel only after Re-probe. Pick the simpler one and say which in the report.
  - `use(p)` sets `cpu` to `p.capacity.cpu ?? cpu`, `memory` to `p.capacity.memory_gb ?? memory`, `cards` from `p.gpus`, and `notes` to `p.notes`. It sets these directly, without `declare(...)`, so in add mode it does not clear the probe.
  - It remembers the last used proposal in state (`usedProposal`).
  - When the probe has failed checks and `usedProposal` has an override for every failed check, the add-mode refusal text is replaced by the list of overrides and a `Add with the agent's overrides` button. That button calls `confirmHost` with `accept_overrides: true`. Edit mode does the same for Update: `Update with the agent's overrides`, sending `accept_overrides: true`.
  - Otherwise, failed checks keep today's text and blocked button.
  - Tests:
    - `Use proposal` fills CPU, memory, cards and notes;
    - a failed check plus a proposal with its override shows `Add with the agent's overrides` and sends `accept_overrides: true`;
    - a failed check with a proposal lacking that override keeps the refusal text;
    - mock `SurveyPanel` or `api.getSurvey` as fits the existing test's mocking style.

- [ ] **Step 4: Run** from `frontend/`: `npx vitest run src/components/SurveyPanel.test.tsx src/components/AddHostModal.test.tsx src/components/HostsCard.test.tsx`, then `npx vitest run`, then `npx tsc -b`. Expected: PASS.
