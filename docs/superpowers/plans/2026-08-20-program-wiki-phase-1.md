# Program Wiki — Phase 1 (Store & Ingest) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every program an OKF-conformant markdown wiki bundle at
`programs/<pid>/wiki/` that a background dispatch beat fills by ingesting the
program's sprint results and artifact versions, plus a deterministic lint script
— all reachable from `coscience wiki --once`.

**Architecture:** Pure modules do the thinking (`wiki_okf`, `wiki_prompts`,
`wiki_lint`), one module does bundle IO (`wiki_store`), one module is the only
process-launching seam (`wiki_agent`), and `wiki.beat()` is the state machine the
`Dispatcher` calls once per active program per cycle. The agent is a detached
`claude -p` run whose cwd is the bundle directory; the beat never edits pages
itself. All substrate writes stay out of the pure modules (the seam rule).

**Tech Stack:** Python ≥3.11, `pyyaml`, stdlib only otherwise (`hashlib`,
`fcntl`, `subprocess`, `dataclasses`). Tests: `pytest` (`-q`, `pythonpath=["."]`,
`testpaths=["tests"]`). No new dependencies — `pyproject.toml` is untouched by
this phase, so **no reinstall is needed**.

**Spec:** `docs/superpowers/specs/2026-08-20-program-wiki-design.md`
(read §4–§9 and §12–§13 before starting; also read `docs/knowledge-charter.md`)

## Global Constraints

- **Branch:** all work lands on `feat/program-wiki`. Never commit to `main`.
- **Never commit or push without explicit approval** (project CLAUDE.md). Each
  task's commit step is written out, but you must ask the human first, once per
  task, before running it.
- **Never `git add -A`.** Stage the exact paths listed in each commit step. These
  paths are *someone else's uncommitted work* carried over from `main` and must
  never enter a wiki commit: `frontend/src/styles.css`,
  `frontend/src/views/ProgramDetail.tsx`, `frontend/src/views/SprintDetail.tsx`,
  `frontend/src/components/PageToc.tsx`, `frontend/.coscience/`,
  `docs/_tmp_wiki/`.
- **Two repos, don't confuse them.** This repo is code. The *substrate*
  (programs/sprints/results) is a separate git repo pointed at by
  `COSCIENCE_REPO`. Every path this plan writes at runtime is inside the
  substrate; every path it edits at development time is inside this repo.
- **Runtime is Linux-only** (`/proc`, `os.killpg`, `fcntl`). Do not add Windows
  fallbacks.
- **Python ≥3.11**: use `X | None`, `StrEnum`, `from __future__ import annotations`
  at the top of every new module, matching the existing files.
- **The unit suite never calls a live LLM.** `wiki_agent` is injectable exactly
  as `pm_claude.ClaudeCodeReasoner` is; tests pass a fake.
- **OKF v0.2 conformance:** every non-reserved `.md` in the bundle has parseable
  frontmatter with a non-empty `type`. Parsers must **tolerate unknown types,
  unknown keys and broken links** — never raise, never drop unknown keys.
- **Frozen vocabularies.** Relation types are exactly the twelve in spec §7.
  Page types are exactly `Concept | Entity | Synthesis | Source | Question`, but
  consumers tolerate others.
- **Constants are env-overridable**, read at call time (not import time) so tests
  can monkeypatch the environment: `COSCIENCE_WIKI_BATCH` (default 4),
  `COSCIENCE_WIKI_LINT_EVERY` (default 5), `COSCIENCE_WIKI_MAX_FAILURES`
  (default 3), `COSCIENCE_WIKI_COLLECT_GRACE` (default 60.0).
- **Do not touch the frontend in this phase.** Browse UI is phase 2.

## File Structure

New modules in `src/coscience/`:

| File | Responsibility |
|---|---|
| `agent_stream.py` | pure: parse a `stream-json` capture into a result record (extracted from two existing copies) |
| `wiki_okf.py` | pure: the OKF page model — parse/render frontmatter, preserve unknown keys |
| `wiki_store.py` | bundle IO: layout, page read/write, object identity + hashing, `state.json` under flock |
| `wiki_prompts.py` | pure: render the ingest / lint instruction documents |
| `wiki_agent.py` | the only side-effecting seam: launch a detached run, collect its outcome |
| `wiki.py` | `beat()` — the §8.4 state machine |
| `wiki_lint.py` | pure: the 19 lint rules + mechanical auto-fixes + report rendering |

Modified: `models.py` (two `Program` fields), `substrate.py` (persist them),
`claude_executor.py` + `chat_agent.py` (use `agent_stream`),
`dispatcher.py` (call the beat), `cli.py` (`coscience wiki`).

New tests: `tests/test_agent_stream.py`, `test_wiki_okf.py`,
`test_wiki_store.py`, `test_wiki_state.py`, `test_wiki_prompts.py`,
`test_wiki_agent.py`, `test_wiki_beat.py`, `test_wiki_beat_collect.py`,
`test_wiki_dispatcher.py`, `test_wiki_lint.py`, `test_wiki_lint_links.py`,
`test_wiki_lint_sources.py`, `test_cli_wiki.py`, plus a `wiki_bundle` fixture in
`tests/conftest.py`.

---

### Task 1: Extract `agent_stream.py`

Two near-identical stream-json scanners exist today
(`ClaudeAgent._unwrap_envelope` and `chat_agent.collect_turn`). Phase 1 would add
a third. Extract one pure parser first; the callers keep their own status logic,
which genuinely differs.

The behavioural difference to preserve: `claude_executor` requires the final
event to carry a `"result"` key; `chat_agent` does not. That is the
`require_text` flag.

**Files:**
- Create: `src/coscience/agent_stream.py`
- Modify: `src/coscience/claude_executor.py:277-306` (`_unwrap_envelope`)
- Modify: `src/coscience/chat_agent.py:141-167` (`collect_turn`)
- Test: `tests/test_agent_stream.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  ```python
  @dataclass
  class StreamResult:
      text: str
      session_id: str
      usage: dict
      cost: float | None
      turns: int | None
      duration_ms: int | None

  def parse_stream(raw: str, *, require_text: bool = True) -> StreamResult | None
  ```
  Returns `None` when no qualifying `result` event is present.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_agent_stream.py
import json

from coscience.agent_stream import parse_stream


def _line(**kw) -> str:
    return json.dumps(kw)


def test_parse_stream_returns_last_result_event():
    raw = "\n".join([
        _line(type="system", session_id="s-1"),
        _line(type="result", result="first", session_id="s-1"),
        _line(type="result", result="second", session_id="s-1",
              usage={"input_tokens": 10, "output_tokens": 4},
              total_cost_usd=0.02, num_turns=3, duration_ms=1500),
    ])
    got = parse_stream(raw)
    assert got is not None
    assert got.text == "second"
    assert got.session_id == "s-1"
    assert got.cost == 0.02
    assert got.turns == 3
    assert got.duration_ms == 1500
    assert got.usage == {"input_tokens": 10, "output_tokens": 4}


def test_parse_stream_none_when_no_result_event():
    assert parse_stream(_line(type="system", session_id="s-1")) is None


def test_parse_stream_ignores_non_json_noise():
    raw = "Claude usage limit reached\n" + _line(type="result", result="ok")
    assert parse_stream(raw).text == "ok"


def test_parse_stream_require_text_false_accepts_bare_result():
    raw = _line(type="result", session_id="s-9", subtype="error_during_execution")
    assert parse_stream(raw, require_text=True) is None
    got = parse_stream(raw, require_text=False)
    assert got is not None
    assert got.text == ""
    assert got.session_id == "s-9"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_agent_stream.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'coscience.agent_stream'`

- [ ] **Step 3: Write the module**

```python
# src/coscience/agent_stream.py
"""Parse a `claude -p --output-format stream-json` capture.

Pure — no IO. Three callers scan the same JSONL feed for the final `result`
event (the sprint agent, the chat agent, the wiki agent) and each wants a
different thing from it, so this returns the whole record and leaves status
handling to them."""
from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class StreamResult:
    text: str = ""
    session_id: str = ""
    usage: dict = field(default_factory=dict)
    cost: float | None = None
    turns: int | None = None
    duration_ms: int | None = None


def parse_stream(raw: str, *, require_text: bool = True) -> StreamResult | None:
    """The last `result` event in `raw`, or None if there is none.

    `require_text=True` (the sprint agent's rule) only accepts an event that
    carries a `result` key, so a usage-limit message or a bare error envelope
    falls through to the caller's raw-text path. `require_text=False` (the chat
    agent's rule) accepts any `result` event, so an errored turn still yields its
    session id."""
    found = None
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(ev, dict) or ev.get("type") != "result":
            continue
        if require_text and "result" not in ev:
            continue
        found = ev                                  # keep the last one
    if found is None:
        return None
    cost = found.get("total_cost_usd")
    return StreamResult(
        text=str(found.get("result") or ""),
        session_id=str(found.get("session_id") or ""),
        usage=found.get("usage") or {},
        cost=float(cost) if isinstance(cost, (int, float)) else None,
        turns=found.get("num_turns"),
        duration_ms=found.get("duration_ms"),
    )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_agent_stream.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Rewire `claude_executor._unwrap_envelope` onto it**

Replace the body of `_unwrap_envelope` (`src/coscience/claude_executor.py:277-306`)
with:

```python
    @staticmethod
    def _unwrap_envelope(raw: str, sprint_dir: Path) -> str:
        """Scan the JSONL event stream for the final `result` event: return its
        message text and write a cost sidecar. If no such event is present (e.g. a
        usage-limit message instead of a stream), return the raw text unchanged so
        the worker's limit detection still fires."""
        result = agent_stream.parse_stream(raw)
        if result is None:
            return raw
        breakdown = usage_meter.token_breakdown(result.usage)
        sidecar = {"cost": result.cost,
                   "tokens": breakdown.get("tokens"),
                   "usage": breakdown,
                   "turns": result.turns, "duration_ms": result.duration_ms}
        try:
            (sprint_dir / "agent.cost.json").write_text(json.dumps(sidecar))
        except OSError:
            pass
        return result.text
```

Add `from coscience import agent_stream` to the module's imports.

- [ ] **Step 6: Rewire `chat_agent.collect_turn` onto it**

Replace the scan loop in `src/coscience/chat_agent.py:141-167` so the tail of the
function reads:

```python
    result = agent_stream.parse_stream(raw, require_text=False)
    status = "ok" if code == 0 else "failed"
    if result is None:
        return (raw or "(no output)"), "", status
    return result.text, result.session_id, status
```

Add `from coscience import agent_stream` to the module's imports, and drop the
now-unused `json` import only if nothing else in the file uses it (check with
`grep -n "json\." src/coscience/chat_agent.py`).

- [ ] **Step 7: Run the existing suites for both callers — behaviour must be unchanged**

Run: `python -m pytest tests/test_claude_executor.py tests/test_chat_agent.py tests/test_agent_stream.py -v`
Expected: PASS, no test edits required. If any test fails, the extraction changed
behaviour — fix `agent_stream`, not the test.

- [ ] **Step 8: Run the whole suite**

Run: `python -m pytest`
Expected: PASS (same count as before this task, plus 4).

- [ ] **Step 9: Commit** (ask for approval first)

```bash
git add src/coscience/agent_stream.py src/coscience/claude_executor.py \
        src/coscience/chat_agent.py tests/test_agent_stream.py
git commit -m "refactor: extract agent_stream.parse_stream from the two stream-json scanners"
```

---

### Task 2: `Program.wiki_enabled` and `Program.wiki_model`

**Files:**
- Modify: `src/coscience/models.py:197-208` (the `Program` dataclass)
- Modify: `src/coscience/substrate.py:291-315` (`load_program` / `save_program`)
- Test: `tests/test_wiki_program_fields.py`

**Interfaces:**
- Consumes: `models.DEFAULT_MODEL` (`"claude-sonnet-5"`).
- Produces: `Program.wiki_model: str` (`""` resolves to `DEFAULT_MODEL` in
  `__post_init__`, same as `pm_model`) and `Program.wiki_enabled: bool = True`.
  Persisted as frontmatter keys `wiki_model` and `wiki_enabled`. **`wiki_enabled`
  is written only when False**, so existing `program.md` files are untouched and
  default to enabled.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_wiki_program_fields.py
from coscience.models import DEFAULT_MODEL, Program


def test_wiki_model_defaults_to_default_model():
    p = Program(id="p1", title="T", goals="G")
    assert p.wiki_model == DEFAULT_MODEL
    assert p.wiki_enabled is True


def test_wiki_fields_round_trip(substrate):
    substrate.save_program(Program(id="p1", title="T", goals="G",
                                   wiki_model="claude-haiku-4-5-20251001",
                                   wiki_enabled=False))
    got = substrate.load_program("p1")
    assert got.wiki_model == "claude-haiku-4-5-20251001"
    assert got.wiki_enabled is False


def test_enabled_program_writes_no_wiki_enabled_key(substrate):
    substrate.save_program(Program(id="p1", title="T", goals="G"))
    text = (substrate.program_dir("p1") / "program.md").read_text()
    assert "wiki_enabled" not in text


def test_legacy_program_without_wiki_keys_defaults_enabled(substrate):
    d = substrate.program_dir("p1")
    d.mkdir(parents=True)
    (d / "program.md").write_text("---\ntype: program\ntitle: T\nstatus: active\n---\n\nG\n")
    got = substrate.load_program("p1")
    assert got.wiki_enabled is True
    assert got.wiki_model == DEFAULT_MODEL
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_wiki_program_fields.py -v`
Expected: FAIL — `TypeError: Program.__init__() got an unexpected keyword argument 'wiki_model'`

- [ ] **Step 3: Add the fields to `Program`**

In `src/coscience/models.py`, after `max_proposed`:

```python
    wiki_model: str = ""               # Claude model for this program's wiki runs; "" resolves to DEFAULT_MODEL
    wiki_enabled: bool = True          # False opts the program out of wiki ingest entirely
```

and extend `__post_init__`:

```python
    def __post_init__(self) -> None:
        if not self.pm_model:
            self.pm_model = DEFAULT_MODEL
        if not self.wiki_model:
            self.wiki_model = DEFAULT_MODEL
```

- [ ] **Step 4: Persist them**

In `src/coscience/substrate.py`, `load_program` gains:

```python
            wiki_model=str(fm.get("wiki_model", "")),
            wiki_enabled=bool(fm.get("wiki_enabled", True)),
```

and `save_program`, after the `pm_model` block:

```python
        if program.wiki_model:
            fm["wiki_model"] = program.wiki_model
        if not program.wiki_enabled:
            # written only when opting out, so existing program.md files are untouched
            fm["wiki_enabled"] = False
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `python -m pytest tests/test_wiki_program_fields.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Run the whole suite**

Run: `python -m pytest`
Expected: PASS. `test_cli_program.py` and the service program tests exercise
`save_program`/`load_program`; they must still pass unmodified.

- [ ] **Step 7: Commit** (ask for approval first)

```bash
git add src/coscience/models.py src/coscience/substrate.py \
        tests/test_wiki_program_fields.py
git commit -m "feat(wiki): add Program.wiki_model and Program.wiki_enabled"
```

---

### Task 3: `wiki_okf.py` — the page model

Pure. Parses a bundle page into a typed object and renders it back, **preserving
every key it does not know about** (OKF requires consumers to tolerate unknown
keys, and an agent may legitimately add fields we have not specified).

**Files:**
- Create: `src/coscience/wiki_okf.py`
- Test: `tests/test_wiki_okf.py`

**Interfaces:**
- Consumes: `coscience.frontmatter_io.parse(text) -> (dict, str)` and
  `serialize(fm, body) -> str`.
- Produces:
  ```python
  RELATION_TYPES: frozenset[str]      # the twelve of spec §7
  PAGE_TYPES: frozenset[str]          # Concept Entity Synthesis Source Question
  CONFIDENCE: frozenset[str]          # low med high

  @dataclass
  class Relation:
      type: str; target: str; confidence: str = ""; source: str = ""
      extra: dict = {}

  @dataclass
  class Source:
      id: str; resource: str = ""; title: str = ""; last_modified: str = ""
      extra: dict = {}

  @dataclass
  class Page:
      path: str                 # bundle-relative, e.g. "concepts/takeoff.md"
      type: str; title: str = ""; description: str = ""
      tags: list[str] = []; status: str = ""; stale_after: str = ""
      generated: dict = {}; verified: list[dict] = []
      sources: list[Source] = []; relations: list[Relation] = []
      aliases: list[str] = []; graph_excluded: bool = False
      body: str = ""; extra: dict = {}     # every unrecognised frontmatter key
      bad_yaml: bool = False               # frontmatter failed to parse

      def section(self, heading: str) -> str        # body text under "# heading", "" if absent
      def has_section(self, heading: str) -> bool
      def sources_by_id(self) -> dict[str, Source]

  def parse_page(path: str, text: str) -> Page
  def render_page(page: Page) -> str
  def body_links(body: str) -> list[str]     # markdown link targets, in order, duplicates kept
  def wikilinks(body: str) -> list[str]      # [[slug]] targets
  ```
  `render_page(parse_page(p, t))` is stable: re-rendering a rendered page is a
  no-op.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_wiki_okf.py
from coscience import wiki_okf

PAGE = """---
type: Concept
title: Template replication takeoff
description: The regime where template-directed replication outruns hydrolysis.
tags: [abiogenesis, kinetics]
status: draft
stale_after: 2027-02-20
generated: { by: coscience-wiki/claude-sonnet-5, at: 2026-08-20T11:04:00Z }
verified:
  - { by: 'human:oleg', at: 2026-08-21T09:00:00Z }
sources:
  - { id: c14, resource: /sources/result-x.md, title: Sprint x result, last_modified: 2026-08-14 }
relations:
  - { type: requires, target: /concepts/hydrolysis-rate.md, confidence: high, source: c14 }
aliases: [takeoff threshold]
weird_key: kept
---

# Definition

Takeoff occurs above the [hydrolysis rate](/concepts/hydrolysis-rate.md).[^c14]

# Human notes

Oleg: check the 40C case.
"""


def test_parse_page_reads_the_okf_families():
    p = wiki_okf.parse_page("concepts/takeoff.md", PAGE)
    assert p.type == "Concept"
    assert p.title == "Template replication takeoff"
    assert p.tags == ["abiogenesis", "kinetics"]
    assert p.status == "draft"
    assert p.stale_after == "2027-02-20"
    assert p.generated["by"] == "coscience-wiki/claude-sonnet-5"
    assert p.verified[0]["by"] == "human:oleg"
    assert p.sources[0].id == "c14"
    assert p.sources[0].resource == "/sources/result-x.md"
    assert p.relations[0].type == "requires"
    assert p.relations[0].target == "/concepts/hydrolysis-rate.md"
    assert p.relations[0].confidence == "high"
    assert p.relations[0].source == "c14"
    assert p.aliases == ["takeoff threshold"]


def test_unknown_keys_are_preserved():
    p = wiki_okf.parse_page("concepts/takeoff.md", PAGE)
    assert p.extra == {"weird_key": "kept"}
    assert "weird_key: kept" in wiki_okf.render_page(p)


def test_render_round_trips():
    p = wiki_okf.parse_page("concepts/takeoff.md", PAGE)
    again = wiki_okf.parse_page("concepts/takeoff.md", wiki_okf.render_page(p))
    assert wiki_okf.render_page(again) == wiki_okf.render_page(p)
    assert again.relations[0].target == p.relations[0].target


def test_sections():
    p = wiki_okf.parse_page("concepts/takeoff.md", PAGE)
    assert p.has_section("Human notes")
    assert "check the 40C case" in p.section("Human notes")
    assert p.section("Evidence") == ""


def test_bad_yaml_does_not_raise():
    p = wiki_okf.parse_page("concepts/x.md", "---\ntype: [unclosed\n---\n\nbody\n")
    assert p.bad_yaml is True
    assert p.type == ""


def test_unknown_type_is_tolerated():
    p = wiki_okf.parse_page("concepts/x.md", "---\ntype: Protocol\n---\n\nbody\n")
    assert p.type == "Protocol"
    assert p.bad_yaml is False


def test_body_links_and_wikilinks():
    body = "see [a](/concepts/a.md) and [[b-slug]] and [c](https://x.test)"
    assert wiki_okf.body_links(body) == ["/concepts/a.md", "https://x.test"]
    assert wiki_okf.wikilinks(body) == ["b-slug"]


def test_relation_vocabulary_is_frozen():
    assert "requires" in wiki_okf.RELATION_TYPES
    assert "relates_to" not in wiki_okf.RELATION_TYPES
    assert len(wiki_okf.RELATION_TYPES) == 12
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_wiki_okf.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'coscience.wiki_okf'`

- [ ] **Step 3: Write the module**

```python
# src/coscience/wiki_okf.py
"""The Open Knowledge Format page model for a program wiki bundle.

Pure — no IO. OKF v0.2 requires exactly one thing of a page (parseable
frontmatter with a non-empty `type`) and requires consumers to tolerate unknown
types, unknown keys and broken links. So this parser never raises: a page whose
YAML is broken comes back with `bad_yaml=True` and an empty type, and every key
we do not model is carried in `extra` and rendered back out untouched."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import yaml

from coscience.frontmatter_io import serialize

RELATION_TYPES = frozenset({
    "is_a", "part_of", "requires", "enables", "implements", "exemplifies",
    "measures", "causally_precedes", "contradicts", "refines", "replaces",
    "extends",
})
PAGE_TYPES = frozenset({"Concept", "Entity", "Synthesis", "Source", "Question"})
CONFIDENCE = frozenset({"low", "med", "high"})

# Reserved bundle files: no page frontmatter is required of them.
RESERVED = ("index.md", "log.md", "CLAUDE.md", "QUESTIONS.md")

_LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)[^)]*\)")
_WIKILINK = re.compile(r"\[\[([^\]|#]+)")
_SECTION = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)

_RELATION_KEYS = ("type", "target", "confidence", "source")
_SOURCE_KEYS = ("id", "resource", "title", "last_modified")
_PAGE_KEYS = ("type", "title", "description", "tags", "status", "stale_after",
              "generated", "verified", "sources", "relations", "aliases",
              "graph_excluded")


@dataclass
class Relation:
    type: str = ""
    target: str = ""
    confidence: str = ""
    source: str = ""
    extra: dict = field(default_factory=dict)


@dataclass
class Source:
    id: str = ""
    resource: str = ""
    title: str = ""
    last_modified: str = ""
    extra: dict = field(default_factory=dict)


@dataclass
class Page:
    path: str                              # bundle-relative, POSIX separators
    type: str = ""
    title: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    status: str = ""
    stale_after: str = ""
    generated: dict = field(default_factory=dict)
    verified: list[dict] = field(default_factory=list)
    sources: list[Source] = field(default_factory=list)
    relations: list[Relation] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    graph_excluded: bool = False
    body: str = ""
    extra: dict = field(default_factory=dict)
    bad_yaml: bool = False

    @property
    def slug(self) -> str:
        return self.path.rsplit("/", 1)[-1][:-3] if self.path.endswith(".md") else self.path

    def section(self, heading: str) -> str:
        """The body text under `# <heading>`, up to the next `# ` heading."""
        marks = [(m.group(1), m.start(), m.end()) for m in _SECTION.finditer(self.body)]
        for i, (name, _start, end) in enumerate(marks):
            if name.strip().lower() == heading.strip().lower():
                stop = marks[i + 1][1] if i + 1 < len(marks) else len(self.body)
                return self.body[end:stop].strip()
        return ""

    def has_section(self, heading: str) -> bool:
        return any(m.group(1).strip().lower() == heading.strip().lower()
                   for m in _SECTION.finditer(self.body))

    def sources_by_id(self) -> dict[str, Source]:
        return {s.id: s for s in self.sources if s.id}


def _str(v) -> str:
    return "" if v is None else str(v)


def _strlist(v) -> list[str]:
    if isinstance(v, str):
        return [v]
    return [_str(x) for x in v] if isinstance(v, list) else []


def _split(raw: dict, known: tuple[str, ...]) -> dict:
    return {k: v for k, v in raw.items() if k not in known}


def parse_page(path: str, text: str) -> Page:
    """Parse one bundle page. Never raises."""
    try:
        fm, body = _parse_frontmatter(text)
    except yaml.YAMLError:
        return Page(path=path, body=text, bad_yaml=True)
    if not isinstance(fm, dict):
        return Page(path=path, body=body, bad_yaml=True)
    relations = []
    for r in fm.get("relations") or []:
        if not isinstance(r, dict):
            continue
        relations.append(Relation(
            type=_str(r.get("type")), target=_str(r.get("target")),
            confidence=_str(r.get("confidence")), source=_str(r.get("source")),
            extra=_split(r, _RELATION_KEYS)))
    sources = []
    for s in fm.get("sources") or []:
        if not isinstance(s, dict):
            continue
        sources.append(Source(
            id=_str(s.get("id")), resource=_str(s.get("resource")),
            title=_str(s.get("title")), last_modified=_str(s.get("last_modified")),
            extra=_split(s, _SOURCE_KEYS)))
    gen = fm.get("generated")
    ver = fm.get("verified")
    return Page(
        path=path,
        type=_str(fm.get("type")),
        title=_str(fm.get("title")),
        description=_str(fm.get("description")),
        tags=_strlist(fm.get("tags")),
        status=_str(fm.get("status")),
        stale_after=_str(fm.get("stale_after")),
        generated=gen if isinstance(gen, dict) else {},
        verified=[v for v in (ver or []) if isinstance(v, dict)],
        sources=sources,
        relations=relations,
        aliases=_strlist(fm.get("aliases")),
        graph_excluded=bool(fm.get("graph_excluded", False)),
        body=body,
        extra=_split(fm, _PAGE_KEYS),
    )


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Like frontmatter_io.parse, but lets YAML errors surface so parse_page can
    mark the page rather than silently treating it as body-only."""
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    return (yaml.safe_load(parts[1]) or {}), parts[2].lstrip("\n")


def render_page(page: Page) -> str:
    """Render back to markdown. Key order is the §6 template order, with unknown
    keys appended so nothing an agent wrote is lost."""
    fm: dict = {"type": page.type}
    for key, value in (("title", page.title), ("description", page.description),
                       ("status", page.status), ("stale_after", page.stale_after)):
        if value:
            fm[key] = value
    if page.tags:
        fm["tags"] = list(page.tags)
    if page.generated:
        fm["generated"] = dict(page.generated)
    if page.verified:
        fm["verified"] = [dict(v) for v in page.verified]
    if page.sources:
        fm["sources"] = [_clean({"id": s.id, "resource": s.resource, "title": s.title,
                                 "last_modified": s.last_modified}, s.extra)
                         for s in page.sources]
    if page.relations:
        fm["relations"] = [_clean({"type": r.type, "target": r.target,
                                   "confidence": r.confidence, "source": r.source},
                                  r.extra)
                           for r in page.relations]
    if page.aliases:
        fm["aliases"] = list(page.aliases)
    if page.graph_excluded:
        fm["graph_excluded"] = True
    fm.update(page.extra)
    return serialize(fm, page.body)


def _clean(fields: dict, extra: dict) -> dict:
    out = {k: v for k, v in fields.items() if v not in ("", None)}
    out.update(extra)
    return out


def body_links(body: str) -> list[str]:
    """Markdown link targets in document order. Image links are excluded."""
    return [m.group(1) for m in _LINK.finditer(body)]


def wikilinks(body: str) -> list[str]:
    """`[[slug]]` targets — the shape lint rewrites into real markdown links."""
    return [m.group(1).strip() for m in _WIKILINK.finditer(body)]
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_wiki_okf.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit** (ask for approval first)

```bash
git add src/coscience/wiki_okf.py tests/test_wiki_okf.py
git commit -m "feat(wiki): OKF page model — parse/render preserving unknown keys"
```

---

### Task 4: `wiki_store.py` — bundle layout and page IO

The bundle (`programs/<pid>/wiki/`) is the portable product; `.wiki/` is its
sibling machine state, so copying `wiki/` anywhere yields a valid OKF bundle with
nothing to strip.

**Files:**
- Create: `src/coscience/wiki_store.py`
- Modify: `tests/conftest.py` (add the `wiki_bundle` fixture)
- Test: `tests/test_wiki_store.py`

**Interfaces:**
- Consumes: `Substrate.program_dir(pid) -> Path`; `wiki_okf.parse_page`,
  `wiki_okf.render_page`, `wiki_okf.Page`, `wiki_okf.RESERVED`.
- Produces:
  ```python
  PAGE_DIRS = ("concepts", "entities", "syntheses", "sources")

  def bundle_dir(substrate, program_id) -> Path      # programs/<pid>/wiki
  def state_dir(substrate, program_id) -> Path       # programs/<pid>/.wiki
  def run_dir(substrate, program_id, run_id) -> Path # .wiki/runs/<run_id>
  def ensure_bundle(substrate, program_id) -> Path   # idempotent; writes index/log/CLAUDE/QUESTIONS
  def iter_pages(substrate, program_id) -> list[wiki_okf.Page]
  def read_page(substrate, program_id, rel) -> wiki_okf.Page | None
  def write_page(substrate, program_id, page) -> Path
  def is_empty(substrate, program_id) -> bool        # no pages in PAGE_DIRS
  BUNDLE_CLAUDE_MD: str                              # the schema-layer template
  ```

- [ ] **Step 1: Add the `wiki_bundle` fixture to `tests/conftest.py`**

Append (it uses the existing `substrate` fixture):

```python
@pytest.fixture
def wiki_bundle(substrate):
    """A program with an initialised, empty wiki bundle. Returns (substrate, program_id)."""
    from coscience import wiki_store
    from coscience.models import Program
    substrate.save_program(Program(id="p1", title="P1", goals="goals"))
    wiki_store.ensure_bundle(substrate, "p1")
    return substrate, "p1"
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_wiki_store.py
from coscience import wiki_okf, wiki_store


def test_ensure_bundle_creates_the_okf_skeleton(wiki_bundle):
    substrate, pid = wiki_bundle
    bundle = wiki_store.bundle_dir(substrate, pid)
    assert bundle == substrate.program_dir(pid) / "wiki"
    for name in ("index.md", "log.md", "CLAUDE.md", "QUESTIONS.md"):
        assert (bundle / name).is_file(), name
    for d in wiki_store.PAGE_DIRS:
        assert (bundle / d).is_dir(), d
    fm, _ = __import__("coscience.frontmatter_io", fromlist=["parse"]).parse(
        (bundle / "index.md").read_text())
    assert fm["okf_version"] == "0.2"
    assert fm["type"] == "Index"


def test_state_dir_is_a_sibling_not_a_child(wiki_bundle):
    substrate, pid = wiki_bundle
    assert wiki_store.state_dir(substrate, pid) == substrate.program_dir(pid) / ".wiki"
    assert ".wiki" not in [p.name for p in wiki_store.bundle_dir(substrate, pid).iterdir()]


def test_ensure_bundle_is_idempotent_and_never_clobbers(wiki_bundle):
    substrate, pid = wiki_bundle
    log = wiki_store.bundle_dir(substrate, pid) / "log.md"
    log.write_text("# Log\n\n- 2026-08-20 something happened\n")
    wiki_store.ensure_bundle(substrate, pid)
    assert "something happened" in log.read_text()


def test_write_then_read_page(wiki_bundle):
    substrate, pid = wiki_bundle
    page = wiki_okf.Page(path="concepts/takeoff.md", type="Concept", title="Takeoff",
                         body="# Definition\n\nSomething.\n")
    path = wiki_store.write_page(substrate, pid, page)
    assert path == wiki_store.bundle_dir(substrate, pid) / "concepts" / "takeoff.md"
    got = wiki_store.read_page(substrate, pid, "concepts/takeoff.md")
    assert got.title == "Takeoff"
    assert got.path == "concepts/takeoff.md"


def test_read_missing_page_returns_none(wiki_bundle):
    substrate, pid = wiki_bundle
    assert wiki_store.read_page(substrate, pid, "concepts/nope.md") is None


def test_iter_pages_skips_reserved_files_and_sorts(wiki_bundle):
    substrate, pid = wiki_bundle
    for slug, typ in (("b", "Concept"), ("a", "Concept")):
        wiki_store.write_page(substrate, pid, wiki_okf.Page(
            path=f"concepts/{slug}.md", type=typ, title=slug, body="x"))
    wiki_store.write_page(substrate, pid, wiki_okf.Page(
        path="sources/result-r1.md", type="Source", title="r1", body="x"))
    paths = [p.path for p in wiki_store.iter_pages(substrate, pid)]
    assert paths == ["concepts/a.md", "concepts/b.md", "sources/result-r1.md"]


def test_is_empty(wiki_bundle):
    substrate, pid = wiki_bundle
    assert wiki_store.is_empty(substrate, pid) is True
    wiki_store.write_page(substrate, pid, wiki_okf.Page(
        path="concepts/a.md", type="Concept", title="a", body="x"))
    assert wiki_store.is_empty(substrate, pid) is False


def test_bundle_claude_md_states_the_containment_invariant(wiki_bundle):
    substrate, pid = wiki_bundle
    text = (wiki_store.bundle_dir(substrate, pid) / "CLAUDE.md").read_text()
    assert "# Human notes" in text
    assert "relations" in text
    for rel in ("requires", "contradicts", "causally_precedes"):
        assert rel in text
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `python -m pytest tests/test_wiki_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'coscience.wiki_store'`

- [ ] **Step 4: Write the module (layout + page IO half)**

```python
# src/coscience/wiki_store.py
"""Bundle IO for a program wiki.

`programs/<pid>/wiki/` is the OKF bundle: portable, the actual product, and an
Obsidian vault as-is. `programs/<pid>/.wiki/` is its sibling holding derived
machine state — so copying the bundle anywhere yields something valid with
nothing to strip. Everything here touches the substrate filesystem; the thinking
lives in the pure modules (wiki_okf, wiki_lint, wiki_prompts)."""
from __future__ import annotations

from pathlib import Path

from coscience import wiki_okf

PAGE_DIRS = ("concepts", "entities", "syntheses", "sources")

BUNDLE_CLAUDE_MD = """# This wiki

You are working inside a program's knowledge wiki: a set of interlinked markdown
pages compiled from that program's sprint results and artifacts. Read this file
before writing anything.

## Layout

- `index.md` — the map of the wiki. Reserved; keep its frontmatter.
- `log.md` — append-only chronology, newest first. Reserved.
- `QUESTIONS.md` — open and resolved questions. Reserved.
- `concepts/` — reusable abstractions: mechanisms, methods, phenomena, regimes,
  parameters, frameworks.
- `entities/` — specific named things: people, tools, models, datasets,
  organisms, genes, instruments, papers.
- `syntheses/` — cross-source insight bundles that are valuable but are not
  durable standalone nodes.
- `sources/` — grounding pages: one per ingested result or artifact version.
  These point at the raw object; they are never concepts.

## Every page

```yaml
---
type: Concept                    # Concept | Entity | Synthesis | Source | Question
title: Template replication takeoff
description: One sentence stating what this page is.
tags: [abiogenesis, kinetics]
status: draft                    # lifecycle: draft | stable | deprecated
stale_after: 2027-02-20          # optional declared expiry
generated: { by: coscience-wiki/<model>, at: <ISO-8601 UTC> }
sources:
  - { id: c14, resource: /sources/result-<id>.md, title: "...", last_modified: 2026-08-14 }
relations:
  - { type: requires, target: /concepts/hydrolysis-rate.md, confidence: high, source: c14 }
aliases: [takeoff threshold]
---

# Definition
# Evidence
# Contradictions
# Open questions
# Human notes
```

`type` is the only required field. Never invent a `verified:` entry — trust is
recorded by the platform when a human marks a page verified, never by you.

## Relations — the vocabulary is frozen

`is_a`, `part_of`, `requires`, `enables`, `implements`, `exemplifies`,
`measures`, `causally_precedes`, `contradicts`, `refines`, `replaces`,
`extends`. Anything else is a lint error.

Every relation carries `confidence` (`low` | `med` | `high`) and `source`, which
names an id from this page's `sources` list. **A relation with no source is a
lint error** — an unattributed assertion is exactly what this wiki exists to
prevent.

**The containment invariant:** every typed relation's target must ALSO be linked
from the page body, as a normal markdown link. The frontmatter is the typed
overlay; the prose is the substrate. A relation the prose does not mention is a
lint error.

## Rules that bite

1. **Merge first.** Before creating a page, search for an existing one covering
   the same idea — check titles AND `aliases`. Extend it and add an alias rather
   than creating a near-duplicate.
2. **Over-extract.** A concept mentioned once is still worth a page; a concept
   never written down is knowledge lost.
3. **A source title is never a concept.** "Sprint p3-c14 result" is a source
   page. The concepts are what it established.
4. **Attribute per claim.** Use markdown footnotes (`[^c14]`) tied to
   `sources[].id`.
5. **`# Human notes` is protected.** If a page has one, reproduce it byte for
   byte. It is a human's correction and outranks anything you would write.
6. **Never delete a page.** Propose merges in the run report; a human decides.
7. **Never compute a content hash.** The platform hands you `origin_hash` values;
   copy them exactly. A hash you invent cannot detect drift.
8. **Never write outside this directory.**
"""

_INDEX_MD = """---
type: Index
title: {title}
okf_version: "0.2"
description: Knowledge compiled from this program's sprint results and artifacts.
---

# {title}

This wiki is compiled from the program's results and artifacts. Pages are grouped
below as they are written.

## Concepts

## Entities

## Syntheses
"""

_LOG_MD = """# Log

Newest first. One line per ingest or lint run.
"""

_QUESTIONS_MD = """# Questions

## Open

## Resolved
"""


def bundle_dir(substrate, program_id: str) -> Path:
    return substrate.program_dir(program_id) / "wiki"


def state_dir(substrate, program_id: str) -> Path:
    return substrate.program_dir(program_id) / ".wiki"


def run_dir(substrate, program_id: str, run_id: str) -> Path:
    return state_dir(substrate, program_id) / "runs" / run_id


def ensure_bundle(substrate, program_id: str) -> Path:
    """Create the bundle skeleton if it is missing. Idempotent, and never
    overwrites a file that exists — the log and the index accumulate content."""
    bundle = bundle_dir(substrate, program_id)
    bundle.mkdir(parents=True, exist_ok=True)
    for d in PAGE_DIRS:
        (bundle / d).mkdir(exist_ok=True)
    state_dir(substrate, program_id).mkdir(parents=True, exist_ok=True)
    try:
        title = substrate.load_program(program_id).title or program_id
    except (OSError, ValueError):
        title = program_id
    for name, text in (("index.md", _INDEX_MD.format(title=f"{title} — wiki")),
                       ("log.md", _LOG_MD),
                       ("QUESTIONS.md", _QUESTIONS_MD),
                       ("CLAUDE.md", BUNDLE_CLAUDE_MD)):
        f = bundle / name
        if not f.exists():
            f.write_text(text)
    return bundle


def page_paths(substrate, program_id: str) -> list[str]:
    """Bundle-relative paths of every page, sorted. Reserved files are excluded."""
    bundle = bundle_dir(substrate, program_id)
    out: list[str] = []
    for d in PAGE_DIRS:
        sub = bundle / d
        if not sub.is_dir():
            continue
        for f in sorted(sub.glob("*.md")):
            if f.name not in wiki_okf.RESERVED:
                out.append(f"{d}/{f.name}")
    return out


def read_page(substrate, program_id: str, rel: str) -> wiki_okf.Page | None:
    f = bundle_dir(substrate, program_id) / rel
    try:
        text = f.read_text()
    except OSError:
        return None
    return wiki_okf.parse_page(rel, text)


def iter_pages(substrate, program_id: str) -> list[wiki_okf.Page]:
    pages = [read_page(substrate, program_id, rel)
             for rel in page_paths(substrate, program_id)]
    return [p for p in pages if p is not None]


def write_page(substrate, program_id: str, page: wiki_okf.Page) -> Path:
    f = bundle_dir(substrate, program_id) / page.path
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(wiki_okf.render_page(page))
    return f


def is_empty(substrate, program_id: str) -> bool:
    return not page_paths(substrate, program_id)
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `python -m pytest tests/test_wiki_store.py -v`
Expected: PASS (8 tests)

- [ ] **Step 6: Commit** (ask for approval first)

```bash
git add src/coscience/wiki_store.py tests/test_wiki_store.py tests/conftest.py
git commit -m "feat(wiki): bundle layout, page IO and the schema-layer CLAUDE.md"
```

---

### Task 5: Object identity — what is ingestable, and its hash

The wiki points at raw objects rather than copying them, so an object needs a
stable id and a content hash. The hash is what makes re-ingest correct: edit a
result after ingest and it reappears as pending.

**Files:**
- Modify: `src/coscience/wiki_store.py` (append)
- Test: `tests/test_wiki_objects.py`

**Interfaces:**
- Consumes: `Substrate.iter_results() -> list[Result]`,
  `Substrate.load_sprint(sid) -> Sprint` (raises if missing),
  `Substrate.iter_artifacts(pid, include_archived=False) -> list[Artifact]`,
  `Substrate.artifact_dir(pid, aid) -> Path`; `Artifact.current`,
  `Artifact.versions[].id/.created_at`; `Result.sprint`, `Result.completed_at`.
- Produces:
  ```python
  @dataclass
  class WikiObject:
      oid: str          # "result:<rid>" | "artifact:<aid>@<vid>"
      kind: str         # "result" | "artifact"
      title: str
      at: float         # completed_at / created_at; 0.0 when unknown
      paths: list[Path] # absolute paths the agent must read
      resource: str     # substrate-relative OKF pointer, leading slash
      slug: str         # platform-assigned source page slug, e.g. "sources/result-r1.md"

  def hash_file(path: Path) -> str          # "sha256:<hex>"
  def hash_dir(path: Path) -> str           # "sha256:<hex>" over sorted (relpath, filehash)
  def object_hash(obj: WikiObject) -> str
  def program_objects(substrate, program_id) -> list[WikiObject]   # oldest first
  def pending_objects(substrate, program_id, ingested: dict[str, dict],
                      quarantined: set[str] | None = None) -> list[WikiObject]
  ```

- [ ] **Step 1: Write the failing test**

```python
# tests/test_wiki_objects.py
import time

from coscience import artifacts, wiki_store
from coscience.models import Program, Result, Sprint, SprintStatus


def _program(substrate, pid="p1"):
    substrate.save_program(Program(id=pid, title=pid.upper(), goals="g"))


def _result(substrate, rid, sprint_id, program, at):
    substrate.save_sprint(Sprint(id=sprint_id, status=SprintStatus.DONE, goals="g",
                                 program=program))
    substrate.save_result(Result(id=rid, sprint=sprint_id, summary=f"summary {rid}",
                                 completed_at=at))


def test_program_objects_includes_results_of_this_program_only(substrate):
    _program(substrate, "p1")
    _program(substrate, "p2")
    _result(substrate, "r1", "s1", "p1", 100.0)
    _result(substrate, "r2", "s2", "p2", 200.0)
    oids = [o.oid for o in wiki_store.program_objects(substrate, "p1")]
    assert oids == ["result:r1"]


def test_result_with_missing_sprint_is_skipped(substrate):
    _program(substrate, "p1")
    substrate.save_result(Result(id="r9", sprint="gone", summary="s", completed_at=1.0))
    assert wiki_store.program_objects(substrate, "p1") == []


def test_objects_are_ordered_oldest_first(substrate):
    _program(substrate, "p1")
    _result(substrate, "late", "s2", "p1", 300.0)
    _result(substrate, "early", "s1", "p1", 100.0)
    assert [o.oid for o in wiki_store.program_objects(substrate, "p1")] == [
        "result:early", "result:late"]


def test_artifact_current_version_only(substrate, tmp_path):
    _program(substrate, "p1")
    src = tmp_path / "fig.md"
    src.write_text("v1\n")
    v1 = artifacts.adopt(substrate, "p1", "fig", title="Figure", kind="figure",
                         now=10.0, created_by="cli",
                         sources=artifacts.resolve_sources(tmp_path, [src], restrict=False))
    src.write_text("v2\n")
    v2 = artifacts.adopt(substrate, "p1", "fig", title="Figure", kind="figure",
                         now=20.0, created_by="cli",
                         sources=artifacts.resolve_sources(tmp_path, [src], restrict=False))
    assert v1 != v2
    oids = [o.oid for o in wiki_store.program_objects(substrate, "p1")]
    assert oids == [f"artifact:fig@{v2}"]


def test_slug_and_resource_are_platform_assigned(substrate):
    _program(substrate, "p1")
    _result(substrate, "r1", "s1", "p1", 100.0)
    obj = wiki_store.program_objects(substrate, "p1")[0]
    assert obj.slug == "sources/result-r1.md"
    assert obj.resource == "/results/r1.md"
    assert obj.paths == [substrate.repo_root / "results" / "r1.md"]


def test_object_hash_changes_when_the_file_changes(substrate):
    _program(substrate, "p1")
    _result(substrate, "r1", "s1", "p1", 100.0)
    obj = wiki_store.program_objects(substrate, "p1")[0]
    before = wiki_store.object_hash(obj)
    assert before.startswith("sha256:")
    (substrate.repo_root / "results" / "r1.md").write_text("edited\n")
    assert wiki_store.object_hash(obj) != before


def test_pending_excludes_ingested_but_returns_drifted(substrate):
    _program(substrate, "p1")
    _result(substrate, "r1", "s1", "p1", 100.0)
    obj = wiki_store.program_objects(substrate, "p1")[0]
    ingested = {obj.oid: {"hash": wiki_store.object_hash(obj), "at": 1.0, "run": "r1"}}
    assert wiki_store.pending_objects(substrate, "p1", ingested) == []
    (substrate.repo_root / "results" / "r1.md").write_text("edited\n")
    assert [o.oid for o in wiki_store.pending_objects(substrate, "p1", ingested)] == ["result:r1"]


def test_pending_excludes_quarantined(substrate):
    _program(substrate, "p1")
    _result(substrate, "r1", "s1", "p1", 100.0)
    assert wiki_store.pending_objects(substrate, "p1", {}, {"result:r1"}) == []
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_wiki_objects.py -v`
Expected: FAIL — `AttributeError: module 'coscience.wiki_store' has no attribute 'program_objects'`

- [ ] **Step 3: Append the object layer to `wiki_store.py`**

Add `import hashlib` and `from dataclasses import dataclass, field` to the
imports, then append:

```python
@dataclass
class WikiObject:
    """One ingestable raw object: a sprint result or an artifact version."""
    oid: str
    kind: str
    title: str
    at: float = 0.0
    paths: list[Path] = field(default_factory=list)
    resource: str = ""
    slug: str = ""


def hash_file(path: Path) -> str:
    h = hashlib.sha256()
    try:
        h.update(path.read_bytes())
    except OSError:
        return ""
    return f"sha256:{h.hexdigest()}"


def hash_dir(path: Path) -> str:
    """Digest of a directory: sha256 over the sorted (relpath, file-sha256) list.
    Deterministic across machines; insensitive to mtime and to walk order."""
    if not path.is_dir():
        return ""
    entries = []
    for f in sorted(p for p in path.rglob("*") if p.is_file()):
        rel = f.relative_to(path).as_posix()
        entries.append(f"{rel}\0{hash_file(f)}")
    h = hashlib.sha256("\n".join(entries).encode())
    return f"sha256:{h.hexdigest()}"


def object_hash(obj: WikiObject) -> str:
    """"" when the object's bytes are gone — the caller reads that as src/missing."""
    if obj.kind == "result":
        return hash_file(obj.paths[0]) if obj.paths else ""
    return hash_dir(obj.paths[0]) if obj.paths else ""


def program_objects(substrate, program_id: str) -> list[WikiObject]:
    """Every ingestable object belonging to this program, oldest first.

    Results resolve to a program through their sprint; a result whose sprint is
    missing or belongs elsewhere is skipped rather than guessed at. Artifacts
    contribute their CURRENT version only, so a figure revised five times leaves
    one page trail instead of five near-identical source pages."""
    out: list[WikiObject] = []
    for result in substrate.iter_results():
        try:
            sprint = substrate.load_sprint(result.sprint)
        except Exception:
            continue
        if sprint.program != program_id:
            continue
        out.append(WikiObject(
            oid=f"result:{result.id}", kind="result",
            title=(sprint.title or sprint.goals or result.id).strip()[:120],
            at=float(result.completed_at or 0.0),
            paths=[substrate.repo_root / "results" / f"{result.id}.md"],
            resource=f"/results/{result.id}.md",
            slug=f"sources/result-{result.id}.md"))
    for art in substrate.iter_artifacts(program_id):
        vid = art.current
        if not vid:
            continue
        version = next((v for v in art.versions if v.id == vid), None)
        if version is None or version.archived:
            continue
        out.append(WikiObject(
            oid=f"artifact:{art.id}@{vid}", kind="artifact",
            title=(art.title or art.id).strip()[:120],
            at=float(version.created_at or 0.0),
            paths=[substrate.artifact_dir(program_id, art.id) / vid],
            resource=f"/programs/{program_id}/artifacts/{art.id}/{vid}",
            slug=f"sources/artifact-{art.id}-{vid}.md"))
    out.sort(key=lambda o: (o.at, o.oid))
    return out


def pending_objects(substrate, program_id: str, ingested: dict[str, dict],
                    quarantined: set[str] | None = None) -> list[WikiObject]:
    """Objects needing ingest, oldest first: never ingested, or ingested under a
    hash that no longer matches. One code path covers both, which is why drift
    can never go unnoticed."""
    skip = quarantined or set()
    out = []
    for obj in program_objects(substrate, program_id):
        if obj.oid in skip:
            continue
        known = (ingested.get(obj.oid) or {}).get("hash", "")
        current = object_hash(obj)
        if not current:
            continue                       # the bytes are gone; lint reports src/missing
        if known != current:
            out.append(obj)
    return out
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_wiki_objects.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit** (ask for approval first)

```bash
git add src/coscience/wiki_store.py tests/test_wiki_objects.py
git commit -m "feat(wiki): object identity, content hashing and pending detection"
```

---

### Task 6: `state.json` under a repo-level flock

Both the dispatcher and the HTTP process can mutate wiki state, so it is guarded
the same way `artifacts._lock_guard` guards artifact writes: an exclusive `flock`
on a lock file in `.coscience/`.

**Files:**
- Modify: `src/coscience/wiki_store.py` (append)
- Test: `tests/test_wiki_state.py`

**Interfaces:**
- Consumes: `Substrate.repo_root: Path`.
- Produces:
  ```python
  DEFAULT_STATE = {"ingested": {}, "ingests_since_lint": 0, "run": None,
                   "last_run": None, "failures": 0, "quarantined": []}

  def load_state(substrate, program_id) -> dict          # merged onto DEFAULT_STATE
  def save_state(substrate, program_id, state: dict) -> None
  @contextmanager
  def state_guard(substrate, program_id) -> Iterator[dict]
      # yields the loaded state under an exclusive repo-level flock and saves it
      # on clean exit; on exception the lock is released without saving
  ```

- [ ] **Step 1: Write the failing test**

```python
# tests/test_wiki_state.py
import pytest

from coscience import wiki_store


def test_load_state_defaults_when_absent(wiki_bundle):
    substrate, pid = wiki_bundle
    state = wiki_store.load_state(substrate, pid)
    assert state == {"ingested": {}, "ingests_since_lint": 0, "run": None,
                     "last_run": None, "failures": 0, "quarantined": []}


def test_save_then_load_round_trips(wiki_bundle):
    substrate, pid = wiki_bundle
    state = wiki_store.load_state(substrate, pid)
    state["ingested"]["result:r1"] = {"hash": "sha256:aa", "at": 1.0, "run": "r0001"}
    state["ingests_since_lint"] = 2
    wiki_store.save_state(substrate, pid, state)
    assert (wiki_store.state_dir(substrate, pid) / "state.json").is_file()
    got = wiki_store.load_state(substrate, pid)
    assert got["ingested"]["result:r1"]["hash"] == "sha256:aa"
    assert got["ingests_since_lint"] == 2


def test_load_state_survives_corruption(wiki_bundle):
    substrate, pid = wiki_bundle
    (wiki_store.state_dir(substrate, pid) / "state.json").write_text("{not json")
    assert wiki_store.load_state(substrate, pid)["ingested"] == {}


def test_partial_state_file_is_merged_onto_defaults(wiki_bundle):
    substrate, pid = wiki_bundle
    (wiki_store.state_dir(substrate, pid) / "state.json").write_text('{"failures": 2}')
    state = wiki_store.load_state(substrate, pid)
    assert state["failures"] == 2
    assert state["quarantined"] == []
    assert state["run"] is None


def test_state_guard_saves_on_clean_exit(wiki_bundle):
    substrate, pid = wiki_bundle
    with wiki_store.state_guard(substrate, pid) as state:
        state["failures"] = 3
    assert wiki_store.load_state(substrate, pid)["failures"] == 3


def test_state_guard_does_not_save_on_exception(wiki_bundle):
    substrate, pid = wiki_bundle
    with pytest.raises(RuntimeError):
        with wiki_store.state_guard(substrate, pid) as state:
            state["failures"] = 9
            raise RuntimeError("boom")
    assert wiki_store.load_state(substrate, pid)["failures"] == 0


def test_lock_file_lives_in_dot_coscience(wiki_bundle):
    substrate, pid = wiki_bundle
    with wiki_store.state_guard(substrate, pid):
        pass
    assert (substrate.repo_root / ".coscience" / "wiki.lock").exists()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_wiki_state.py -v`
Expected: FAIL — `AttributeError: module 'coscience.wiki_store' has no attribute 'load_state'`

- [ ] **Step 3: Append the state layer to `wiki_store.py`**

Add `import fcntl`, `import json`, `from contextlib import contextmanager` to the
imports, then append:

```python
DEFAULT_STATE: dict = {"ingested": {}, "ingests_since_lint": 0, "run": None,
                       "last_run": None, "failures": 0, "quarantined": []}


def load_state(substrate, program_id: str) -> dict:
    """The program's wiki state, defaults filled in. Never raises: a corrupt
    state file must not wedge the beat — worst case we re-ingest."""
    state = {k: (v.copy() if isinstance(v, (dict, list)) else v)
             for k, v in DEFAULT_STATE.items()}
    f = state_dir(substrate, program_id) / "state.json"
    try:
        loaded = json.loads(f.read_text())
    except (OSError, ValueError):
        return state
    if isinstance(loaded, dict):
        state.update(loaded)
    return state


def save_state(substrate, program_id: str, state: dict) -> None:
    d = state_dir(substrate, program_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "state.json").write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


@contextmanager
def state_guard(substrate, program_id: str):
    """Yield the program's wiki state under an exclusive repo-level flock, saving
    it on a clean exit. The dispatcher and the HTTP process both mutate this, so
    the lock is the same shape as artifacts._lock_guard — repo-wide rather than
    per-program, because the contention is negligible and one lock is one thing
    to reason about."""
    lockdir = substrate.repo_root / ".coscience"
    lockdir.mkdir(parents=True, exist_ok=True)
    with open(lockdir / "wiki.lock", "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            state = load_state(substrate, program_id)
            yield state
            save_state(substrate, program_id, state)
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_wiki_state.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit** (ask for approval first)

```bash
git add src/coscience/wiki_store.py tests/test_wiki_state.py
git commit -m "feat(wiki): state.json under a repo-level flock"
```

---

### Task 7: `wiki_prompts.py` — the instruction document

Pure and contract-tested: the tests assert the batch block carries every object,
absolute paths, and precomputed hashes, because those are exactly the things that
silently break an unattended run.

**Files:**
- Create: `src/coscience/wiki_prompts.py`
- Test: `tests/test_wiki_prompts.py`

**Interfaces:**
- Consumes: `wiki_store.WikiObject`, `wiki_store.BUNDLE_CLAUDE_MD`.
- Produces:
  ```python
  def render_ingest(program, bundle: Path, objects: list[tuple[WikiObject, str]],
                    run_dir: Path) -> str
      # objects is [(obj, precomputed_hash)] — the platform computes hashes, never the agent
  def render_lint(program, bundle: Path, report: str, run_dir: Path) -> str
  def kickoff(kind: str, run_dir: Path) -> str
      # the short -p prompt that points the agent at instructions.md
  ```

- [ ] **Step 1: Write the failing test**

```python
# tests/test_wiki_prompts.py
from pathlib import Path

from coscience import wiki_prompts, wiki_store
from coscience.models import Program

BUNDLE = Path("/repo/programs/p1/wiki")
RUN = Path("/repo/programs/p1/.wiki/runs/r0003")
PROGRAM = Program(id="p1", title="Abiogenesis", goals="Find the takeoff regime.")

OBJECTS = [
    (wiki_store.WikiObject(
        oid="result:r1", kind="result", title="Beat record 1572", at=100.0,
        paths=[Path("/repo/results/r1.md")], resource="/results/r1.md",
        slug="sources/result-r1.md"), "sha256:aaa"),
    (wiki_store.WikiObject(
        oid="artifact:fig@v2", kind="artifact", title="Takeoff vs hydrolysis", at=200.0,
        paths=[Path("/repo/programs/p1/artifacts/fig/v2")],
        resource="/programs/p1/artifacts/fig/v2",
        slug="sources/artifact-fig-v2.md"), "sha256:bbb"),
]


def test_ingest_prompt_carries_every_object_with_paths_and_hashes():
    text = wiki_prompts.render_ingest(PROGRAM, BUNDLE, OBJECTS, RUN)
    for obj, digest in OBJECTS:
        assert obj.oid in text
        assert obj.title in text
        assert str(obj.paths[0]) in text
        assert digest in text
        assert obj.slug in text
        assert obj.resource in text


def test_ingest_prompt_paths_are_absolute():
    text = wiki_prompts.render_ingest(PROGRAM, BUNDLE, OBJECTS, RUN)
    assert "/repo/results/r1.md" in text
    assert "results/r1.md\n" not in text.replace("/repo/results/r1.md", "")


def test_ingest_prompt_states_the_prohibitions():
    text = wiki_prompts.render_ingest(PROGRAM, BUNDLE, OBJECTS, RUN)
    assert "do not compute" in text.lower()
    assert str(BUNDLE) in text
    assert "# Human notes" in text
    assert "report.json" in text
    assert "log.md" in text
    assert "QUESTIONS.md" in text


def test_ingest_prompt_carries_the_four_pass_protocol():
    text = wiki_prompts.render_ingest(PROGRAM, BUNDLE, OBJECTS, RUN)
    for pass_name in ("structure map", "claim", "relationship", "contradiction"):
        assert pass_name in text.lower()


def test_ingest_prompt_carries_the_program_context():
    text = wiki_prompts.render_ingest(PROGRAM, BUNDLE, OBJECTS, RUN)
    assert "Abiogenesis" in text
    assert "Find the takeoff regime." in text


def test_lint_prompt_carries_the_machine_report():
    text = wiki_prompts.render_lint(PROGRAM, BUNDLE, "concepts/a.md: rel/no-source", RUN)
    assert "rel/no-source" in text
    assert str(BUNDLE) in text
    assert "never delete" in text.lower()


def test_kickoff_points_at_instructions_md():
    text = wiki_prompts.kickoff("ingest", RUN)
    assert str(RUN / "instructions.md") in text
    assert len(text) < 600


def test_render_is_pure_and_stable():
    a = wiki_prompts.render_ingest(PROGRAM, BUNDLE, OBJECTS, RUN)
    b = wiki_prompts.render_ingest(PROGRAM, BUNDLE, OBJECTS, RUN)
    assert a == b
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_wiki_prompts.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'coscience.wiki_prompts'`

- [ ] **Step 3: Write the module**

```python
# src/coscience/wiki_prompts.py
"""Instruction documents for wiki runs. Pure — no IO, no substrate access.

The run is unattended, so everything the agent needs must be in the document:
the schema, the frozen vocabulary, the rules that bite, the absolute paths of the
objects to read, and their precomputed hashes. The bundle's own CLAUDE.md loads
automatically (cwd is the bundle), so this document carries the task, not the
schema — except where a rule is important enough to say twice."""
from __future__ import annotations

from pathlib import Path

from coscience.wiki_store import WikiObject

_PROTOCOL = """## Protocol — four passes, each written out before the next begins

1. **Structure map.** Read each object end to end. Write, into
   `{scratchpad}`, a map of its sections and what each establishes. Do not
   extract yet.
2. **Claim-level extraction.** For each object, list every distinct claim,
   mechanism, parameter, method, named thing and negative result, each with the
   section it came from. Over-extract: a concept mentioned once is still worth a
   page; a concept never written down is knowledge lost.
3. **Relationship triples.** Turn the extraction into `(source, relation,
   target)` triples using the frozen vocabulary only. Each triple carries a
   confidence and the source id supporting it.
4. **Cross-wiki contradiction scan.** Read the existing pages your new material
   touches. Where the new material contradicts them, record a `contradicts`
   relation and write a `# Contradictions` section on the page that asserts it.
   Never silently overwrite the older claim.

Only after all four passes do you write pages.
"""

_RULES = """## Rules that bite

- **Merge first.** Before creating a page, search `concepts/`, `entities/` and
  `syntheses/` for an existing page covering the same idea — check titles AND
  `aliases`. Extend the existing page and add an alias. Near-duplicates are the
  main way a wiki like this rots.
- **A source title is never a concept.** "Sprint p3-c14 result" is a source
  page. The concepts are what it established.
- **Preserve layered insight.** When you extend a page, keep what is there and
  add to it. Do not compress an existing page to make room.
- **Attribute per claim** with markdown footnotes (`[^id]`) tied to an id in the
  page's `sources` list.
- **The containment invariant:** every typed relation in `relations` must also be
  linked from the body as an ordinary markdown link. The frontmatter is a typed
  overlay on the prose, never a substitute for it.
- **`# Human notes` is protected.** If a page has that section, reproduce it byte
  for byte. It is a human's correction and outranks anything you would write.
"""

_PROHIBITIONS = """## Prohibitions

- Do not write anywhere outside `{bundle}` (and this run's directory,
  `{run_dir}`). Everything else in the repository belongs to other systems.
- Do not compute or invent content hashes. The `origin_hash` values are given to
  you above; copy them exactly. A hash you compute yourself cannot detect drift.
- Do not start background tasks, long-running processes or anything that would
  outlive this turn. If the batch is too large to finish, do fewer objects well
  and say so in the report.
- Do not delete or empty any page. Propose merges in `report.json`; a human
  decides.
- Do not write a `verified:` entry. Trust is recorded by the platform when a
  human marks a page verified.
"""

_HOUSEKEEPING = """## Before you finish

1. Update `index.md` so every page you created is reachable from it.
2. Prepend one line per object to `log.md` (newest first): the date, the object
   id, and what it added.
3. Append any genuinely open question to `QUESTIONS.md` under `## Open`.
4. Write `{run_dir}/report.json`:

```json
{{"pages_created": ["concepts/a.md"], "pages_updated": ["concepts/b.md"],
  "objects": ["result:r1"], "notes": "one or two sentences"}}
```
"""


def _object_block(objects: list[tuple[WikiObject, str]]) -> str:
    lines = []
    for obj, digest in objects:
        paths = "\n".join(f"  - read: `{p}`" for p in obj.paths)
        lines.append(
            f"### `{obj.oid}` — {obj.title}\n"
            f"{paths}\n"
            f"  - source page to write: `{obj.slug}`\n"
            f"  - `resource:` value for its frontmatter: `{obj.resource}`\n"
            f"  - `origin_hash:` value for its frontmatter: `{digest}`\n"
            f"  - `origin:` value for its frontmatter: `{obj.oid}`\n")
    return "\n".join(lines)


def render_ingest(program, bundle: Path, objects: list[tuple[WikiObject, str]],
                  run_dir: Path) -> str:
    """The full ingest instruction document, written to the run directory."""
    scratchpad = run_dir / "scratchpad.md"
    return f"""# Wiki ingest run

You are the wiki maintainer for the research program **{program.title}**
(`{program.id}`). You are running unattended: no one will answer a question, so
make the best decision you can and record the uncertainty on the page.

Program goals:

{_indent(program.goals)}

The wiki bundle is `{bundle}` — your working directory. Its `CLAUDE.md` states
the page schema and the frozen relation vocabulary; both are binding. Read it
first.

## Your task

Ingest the {len(objects)} object(s) below into the wiki. For each one: write its
grounding page under `sources/` at the slug given, then create or extend the
concept, entity and synthesis pages it establishes.

A `sources/` page is a pointer plus a summary — it must carry
`graph_excluded: true` and the sections `# Summary`, `# Section map`,
`# Notable insights`, `# Concepts extracted`. It never carries the knowledge
itself; the concept pages do.

## The batch

{_object_block(objects)}
{_PROTOCOL.format(scratchpad=scratchpad)}
{_RULES}
{_HOUSEKEEPING.format(run_dir=run_dir)}
{_PROHIBITIONS.format(bundle=bundle, run_dir=run_dir)}
"""


def render_lint(program, bundle: Path, report: str, run_dir: Path) -> str:
    """The lint instruction document: the machine report plus the judgement calls
    a script cannot make."""
    return f"""# Wiki lint run

You are the wiki maintainer for **{program.title}** (`{program.id}`), running
unattended in `{bundle}`. Its `CLAUDE.md` is binding.

The mechanical fixes have already been applied. What follows is what a script
cannot decide.

## Machine lint report

```
{report}
```

## Your task, in order

1. **Fill missing sources.** A relation or claim with no attribution is the worst
   defect here. Trace it back to a `sources/` page and attribute it, or weaken
   the claim until it is honest.
2. **Resolve contradictions.** Where two pages disagree, do not pick a winner
   silently: record a `contradicts` relation both prose and frontmatter agree on,
   and write the disagreement into `# Contradictions` on both pages.
3. **Refresh drifted sources.** A `src/hash-drift` finding means the raw object
   changed after ingest. Re-read it and update its source page; leave the
   `origin_hash` exactly as the report gives it.
4. **Propose merges for near-duplicates.** Do not merge destructively — write the
   proposal into `report.json` under `"merges"` and add an alias so the pages are
   at least findable as one idea.
5. **Fix stubs and orphans.** A stub either grows or is folded into a fuller page.
   An orphan gets linked from `index.md` or from the page it belongs under.

{_RULES}
{_HOUSEKEEPING.format(run_dir=run_dir)}
Write your own summary of what you changed to `{run_dir}/lint-report.md`; the
platform files it under `.wiki/lint/`. Never delete a page, and never edit
`# Human notes`.

{_PROHIBITIONS.format(bundle=bundle, run_dir=run_dir)}
"""


def kickoff(kind: str, run_dir: Path) -> str:
    """The short `-p` prompt. The real instructions are a file, so a long document
    never has to survive shell quoting."""
    return (f"Read {run_dir / 'instructions.md'} and carry out the wiki {kind} run "
            f"it describes, working only inside your current directory and that run "
            f"directory. Follow it exactly, including the report it asks you to "
            f"write at the end.")


def _indent(text: str, prefix: str = "> ") -> str:
    return "\n".join(prefix + line for line in (text or "").strip().splitlines())
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_wiki_prompts.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit** (ask for approval first)

```bash
git add src/coscience/wiki_prompts.py tests/test_wiki_prompts.py
git commit -m "feat(wiki): ingest and lint instruction documents"
```

---

### Task 8: `wiki_agent.py` — the only side-effecting seam

Everything that launches a process lives here, behind an injectable interface, so
the unit suite never calls a live LLM (the same shape as
`pm_claude.ClaudeCodeReasoner` behind the reasoner seam).

**Files:**
- Create: `src/coscience/wiki_agent.py`
- Test: `tests/test_wiki_agent.py`

**Interfaces:**
- Consumes: `executor.launch_detached(command, cwd=None) -> str`,
  `executor.is_running(token) -> bool`; `wiki_prompts.render_ingest/render_lint/kickoff`;
  `agent_stream.parse_stream`.
- Produces:
  ```python
  class WikiAgent:
      def __init__(self, claude_bin: str = "claude") -> None
      def launch(self, *, kind: str, program, bundle: Path, run_dir: Path,
                 objects: list[tuple[WikiObject, str]] | None = None,
                 report: str = "", model: str = "") -> str      # returns the process token
      def is_running(self, token: str) -> bool
      def collect(self, run_dir: Path) -> tuple[str, dict]
          # ("running" | "ok" | "failed", report_dict)
  ```
  `report_dict` is `report.json` when present and parseable, else `{}`. The beat
  never inspects `agent.out` itself.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_wiki_agent.py
import json
from pathlib import Path

import pytest

from coscience import wiki_agent, wiki_store
from coscience.models import Program

PROGRAM = Program(id="p1", title="P1", goals="g")
OBJ = (wiki_store.WikiObject(oid="result:r1", kind="result", title="R1",
                             paths=[Path("/repo/results/r1.md")],
                             resource="/results/r1.md",
                             slug="sources/result-r1.md"), "sha256:aaa")


@pytest.fixture
def captured(monkeypatch):
    calls = {}

    def fake_launch(command, cwd=None):
        calls["command"] = command
        calls["cwd"] = cwd
        return "4242:99"
    monkeypatch.setattr(wiki_agent.executor, "launch_detached", fake_launch)
    return calls


def test_launch_writes_instructions_and_returns_a_token(tmp_path, captured):
    run = tmp_path / "runs" / "r0001"
    token = wiki_agent.WikiAgent().launch(
        kind="ingest", program=PROGRAM, bundle=tmp_path / "wiki", run_dir=run,
        objects=[OBJ], model="claude-sonnet-5")
    assert token == "4242:99"
    text = (run / "instructions.md").read_text()
    assert "result:r1" in text
    assert "sha256:aaa" in text


def test_launch_runs_in_the_bundle_with_the_carried_over_flags(tmp_path, captured):
    run = tmp_path / "runs" / "r0001"
    wiki_agent.WikiAgent().launch(kind="ingest", program=PROGRAM,
                                  bundle=tmp_path / "wiki", run_dir=run,
                                  objects=[OBJ], model="claude-haiku-4-5-20251001")
    cmd = captured["command"]
    assert captured["cwd"] == tmp_path / "wiki"
    assert "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1" in cmd
    assert "--disallowedTools Monitor" in cmd
    assert "--dangerously-skip-permissions" in cmd
    assert "--output-format stream-json --verbose" in cmd
    assert "--model claude-haiku-4-5-20251001" in cmd
    assert str(run / "agent.out") in cmd
    assert str(run / "agent.exit") in cmd


def test_launch_clears_a_previous_runs_leftovers(tmp_path, captured):
    run = tmp_path / "runs" / "r0001"
    run.mkdir(parents=True)
    (run / "agent.exit").write_text("1\n")
    (run / "report.json").write_text("{}")
    wiki_agent.WikiAgent().launch(kind="ingest", program=PROGRAM,
                                  bundle=tmp_path / "wiki", run_dir=run, objects=[OBJ])
    assert not (run / "agent.exit").exists()
    assert not (run / "report.json").exists()


def test_collect_running_when_no_exit_file(tmp_path):
    run = tmp_path / "r"
    run.mkdir()
    assert wiki_agent.WikiAgent().collect(run) == ("running", {})


def test_collect_ok_with_report(tmp_path):
    run = tmp_path / "r"
    run.mkdir()
    (run / "agent.exit").write_text("0\n")
    (run / "report.json").write_text(json.dumps(
        {"pages_created": ["concepts/a.md"], "objects": ["result:r1"]}))
    status, report = wiki_agent.WikiAgent().collect(run)
    assert status == "ok"
    assert report["pages_created"] == ["concepts/a.md"]


def test_collect_ok_without_report_is_still_ok(tmp_path):
    run = tmp_path / "r"
    run.mkdir()
    (run / "agent.exit").write_text("0\n")
    assert wiki_agent.WikiAgent().collect(run) == ("ok", {})


def test_collect_failed_on_nonzero_exit(tmp_path):
    run = tmp_path / "r"
    run.mkdir()
    (run / "agent.exit").write_text("2\n")
    assert wiki_agent.WikiAgent().collect(run)[0] == "failed"


def test_collect_tolerates_a_corrupt_report(tmp_path):
    run = tmp_path / "r"
    run.mkdir()
    (run / "agent.exit").write_text("0\n")
    (run / "report.json").write_text("{oops")
    assert wiki_agent.WikiAgent().collect(run) == ("ok", {})


def test_collect_lint_run_report_text_is_available(tmp_path):
    run = tmp_path / "r"
    run.mkdir()
    (run / "agent.exit").write_text("0\n")
    (run / "lint-report.md").write_text("# what I changed\n")
    assert "what I changed" in wiki_agent.read_lint_report(run)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_wiki_agent.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'coscience.wiki_agent'`

- [ ] **Step 3: Write the module**

```python
# src/coscience/wiki_agent.py
"""Launch and collect a detached wiki run. The only module here that starts a
process — everything else in the wiki subsystem is pure or plain file IO.

There is no detached-job protocol and no resume: a wiki run either finishes its
batch in one turn or it does not, and a batch that cannot finish in one turn is
too large. The fix is a smaller COSCIENCE_WIKI_BATCH, not more machinery."""
from __future__ import annotations

import json
import shlex
from pathlib import Path

from coscience import executor, wiki_prompts
from coscience.wiki_store import WikiObject

_LEFTOVERS = ("agent.out", "agent.exit", "report.json", "lint-report.md")


class WikiAgent:
    """The real agent. Tests inject a double with the same three methods."""

    def __init__(self, claude_bin: str = "claude") -> None:
        self.claude_bin = claude_bin

    def launch(self, *, kind: str, program, bundle: Path, run_dir: Path,
               objects: list[tuple[WikiObject, str]] | None = None,
               report: str = "", model: str = "") -> str:
        run_dir.mkdir(parents=True, exist_ok=True)
        for name in _LEFTOVERS:
            (run_dir / name).unlink(missing_ok=True)
        if kind == "lint":
            text = wiki_prompts.render_lint(program, bundle, report, run_dir)
        else:
            text = wiki_prompts.render_ingest(program, bundle, objects or [], run_dir)
        (run_dir / "instructions.md").write_text(text)
        bundle.mkdir(parents=True, exist_ok=True)
        return executor.launch_detached(
            self._invocation(kind, run_dir, model), cwd=bundle)

    def _invocation(self, kind: str, run_dir: Path, model: str) -> str:
        """Mirrors ClaudeAgent._invocation, including the two flags that keep a
        run from outliving its turn."""
        out, exitf = run_dir / "agent.out", run_dir / "agent.exit"
        model_flag = f"--model {shlex.quote(model)} " if model else ""
        return (f"CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1 {self.claude_bin} -p "
                f"{shlex.quote(wiki_prompts.kickoff(kind, run_dir))} {model_flag}"
                f"--disallowedTools Monitor "
                f"--dangerously-skip-permissions --output-format stream-json --verbose "
                f"> {shlex.quote(str(out))} 2>&1; echo $? > {shlex.quote(str(exitf))}")

    def is_running(self, token: str) -> bool:
        return bool(token) and executor.is_running(token)

    def collect(self, run_dir: Path) -> tuple[str, dict]:
        """('running' | 'ok' | 'failed', report). A wiki run's completion is not a
        judgement call the way a sprint's is: exit 0 means done."""
        exitf = run_dir / "agent.exit"
        if not exitf.exists():
            return "running", {}
        try:
            code = int((exitf.read_text().strip() or "1"))
        except (ValueError, OSError):
            code = 1
        if code != 0:
            return "failed", {}
        return "ok", read_report(run_dir)


def read_report(run_dir: Path) -> dict:
    """report.json, or {} — a missing or corrupt report never fails a run that
    exited 0. Lint will find anything actually wrong with the pages."""
    try:
        data = json.loads((run_dir / "report.json").read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def read_lint_report(run_dir: Path) -> str:
    try:
        return (run_dir / "lint-report.md").read_text()
    except OSError:
        return ""
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_wiki_agent.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit** (ask for approval first)

```bash
git add src/coscience/wiki_agent.py tests/test_wiki_agent.py
git commit -m "feat(wiki): detached run launch/collect seam"
```

---

### Task 9: `wiki.beat()` — the launch half of the state machine

`beat()` is called once per active program per dispatch cycle and returns a short
line for the beat summary (`""` when there is nothing to say).

This task builds the launch path only: gates, lint cadence, batching. The collect
path is Task 10 and returns a stub until then.

**Files:**
- Create: `src/coscience/wiki.py`
- Test: `tests/test_wiki_beat.py`

**Interfaces:**
- Consumes: everything from Tasks 4–8; `pause.is_paused(repo_root) -> bool`;
  `worker.claude_usage_ok`; `models.ProgramStatus`.
- Produces:
  ```python
  WIKI_THRESHOLD = 70.0
  def wiki_batch() -> int          # COSCIENCE_WIKI_BATCH, default 4
  def lint_every() -> int          # COSCIENCE_WIKI_LINT_EVERY, default 5
  def max_failures() -> int        # COSCIENCE_WIKI_MAX_FAILURES, default 3
  def collect_grace() -> float     # COSCIENCE_WIKI_COLLECT_GRACE, default 60.0
  def default_usage_gate(substrate) -> Callable[[], bool]
  def beat(substrate, program, now: float, agent, *,
           usage_gate: Callable[[], bool] | None = None) -> str
  ```

- [ ] **Step 1: Write the failing test**

```python
# tests/test_wiki_beat.py
from pathlib import Path

import pytest

from coscience import wiki, wiki_store
from coscience.models import Program, ProgramStatus, Result, Sprint, SprintStatus


class FakeWikiAgent:
    """Records launches; never starts a process."""

    def __init__(self):
        self.launches = []
        self.alive = True
        self.exit_code = 0

    def launch(self, *, kind, program, bundle, run_dir, objects=None, report="",
               model=""):
        run_dir.mkdir(parents=True, exist_ok=True)
        self.launches.append({"kind": kind, "objects": [o.oid for o, _ in (objects or [])],
                              "model": model, "run_dir": run_dir, "report": report})
        return f"tok{len(self.launches)}"

    def is_running(self, token):
        return self.alive

    def collect(self, run_dir):
        return ("ok" if self.exit_code == 0 else "failed"), {}


@pytest.fixture
def agent2():
    return FakeWikiAgent()


def _seed_result(substrate, rid, sid, pid, at):
    substrate.save_sprint(Sprint(id=sid, status=SprintStatus.DONE, goals="g", program=pid))
    substrate.save_result(Result(id=rid, sprint=sid, summary=f"s {rid}", completed_at=at))


def test_disabled_program_does_nothing(substrate, agent2):
    p = Program(id="p1", title="P", goals="g", wiki_enabled=False)
    substrate.save_program(p)
    _seed_result(substrate, "r1", "s1", "p1", 1.0)
    assert wiki.beat(substrate, p, 100.0, agent2) == ""
    assert agent2.launches == []


def test_non_active_program_does_nothing(substrate, agent2):
    p = Program(id="p1", title="P", goals="g", status=ProgramStatus.PAUSED)
    substrate.save_program(p)
    _seed_result(substrate, "r1", "s1", "p1", 1.0)
    assert wiki.beat(substrate, p, 100.0, agent2) == ""
    assert agent2.launches == []


def test_nothing_pending_returns_empty(substrate, agent2):
    p = Program(id="p1", title="P", goals="g")
    substrate.save_program(p)
    assert wiki.beat(substrate, p, 100.0, agent2) == ""
    assert agent2.launches == []


def test_launch_ingests_the_oldest_batch_and_records_the_run(substrate, agent2, monkeypatch):
    monkeypatch.setenv("COSCIENCE_WIKI_BATCH", "2")
    p = Program(id="p1", title="P", goals="g")
    substrate.save_program(p)
    for i, at in enumerate([30.0, 10.0, 20.0]):
        _seed_result(substrate, f"r{i}", f"s{i}", "p1", at)
    line = wiki.beat(substrate, p, 100.0, agent2)
    assert line.startswith("wiki: launched ingest")
    assert agent2.launches[0]["objects"] == ["result:r1", "result:r2"]  # oldest first
    state = wiki_store.load_state(substrate, "p1")
    assert state["run"]["kind"] == "ingest"
    assert state["run"]["batch"] == ["result:r1", "result:r2"]
    assert state["run"]["token"] == "tok1"
    assert state["run"]["started_at"] == 100.0


def test_launch_creates_the_bundle(substrate, agent2):
    p = Program(id="p1", title="P", goals="g")
    substrate.save_program(p)
    _seed_result(substrate, "r1", "s1", "p1", 1.0)
    wiki.beat(substrate, p, 100.0, agent2)
    assert (wiki_store.bundle_dir(substrate, "p1") / "CLAUDE.md").is_file()


def test_launch_uses_the_programs_wiki_model(substrate, agent2):
    p = Program(id="p1", title="P", goals="g", wiki_model="claude-haiku-4-5-20251001")
    substrate.save_program(p)
    _seed_result(substrate, "r1", "s1", "p1", 1.0)
    wiki.beat(substrate, p, 100.0, agent2)
    assert agent2.launches[0]["model"] == "claude-haiku-4-5-20251001"


def test_only_one_run_at_a_time(substrate, agent2):
    p = Program(id="p1", title="P", goals="g")
    substrate.save_program(p)
    _seed_result(substrate, "r1", "s1", "p1", 1.0)
    wiki.beat(substrate, p, 100.0, agent2)
    agent2.alive = True
    assert wiki.beat(substrate, p, 110.0, agent2) == "wiki: running"
    assert len(agent2.launches) == 1


def test_usage_gate_blocks_launch(substrate, agent2):
    p = Program(id="p1", title="P", goals="g")
    substrate.save_program(p)
    _seed_result(substrate, "r1", "s1", "p1", 1.0)
    assert wiki.beat(substrate, p, 100.0, agent2, usage_gate=lambda: False) == ""
    assert agent2.launches == []


def test_pause_blocks_launch(substrate, agent2):
    from coscience import pause
    p = Program(id="p1", title="P", goals="g")
    substrate.save_program(p)
    _seed_result(substrate, "r1", "s1", "p1", 1.0)
    pause.set_paused(substrate.repo_root, True)
    assert wiki.beat(substrate, p, 100.0, agent2) == ""
    assert agent2.launches == []


def test_lint_runs_after_the_cadence_and_only_on_a_non_empty_bundle(substrate, agent2,
                                                                    monkeypatch):
    monkeypatch.setenv("COSCIENCE_WIKI_LINT_EVERY", "2")
    from coscience import wiki_okf
    p = Program(id="p1", title="P", goals="g")
    substrate.save_program(p)
    wiki_store.ensure_bundle(substrate, "p1")
    with wiki_store.state_guard(substrate, "p1") as state:
        state["ingests_since_lint"] = 2
    # empty bundle: nothing to lint, and nothing pending either
    assert wiki.beat(substrate, p, 100.0, agent2) == ""
    wiki_store.write_page(substrate, "p1", wiki_okf.Page(
        path="concepts/a.md", type="Concept", title="A", body="# Definition\n\nx\n"))
    line = wiki.beat(substrate, p, 110.0, agent2)
    assert line.startswith("wiki: launched lint")
    assert agent2.launches[-1]["kind"] == "lint"
    assert wiki_store.load_state(substrate, "p1")["ingests_since_lint"] == 2  # not reset yet


def test_run_ids_increment(substrate, agent2):
    p = Program(id="p1", title="P", goals="g")
    substrate.save_program(p)
    _seed_result(substrate, "r1", "s1", "p1", 1.0)
    wiki.beat(substrate, p, 100.0, agent2)
    first = wiki_store.load_state(substrate, "p1")["run"]["id"]
    assert first == "r0001"
    assert (wiki_store.state_dir(substrate, "p1") / "runs" / first).is_dir()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_wiki_beat.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'coscience.wiki'`

- [ ] **Step 3: Write the module (launch half)**

```python
# src/coscience/wiki.py
"""The wiki maintenance beat: the state machine the Dispatcher runs once per
active program per cycle.

Wiki work is the least urgent consumer of the Claude budget — below sprint
execution (90) and below PM planning (80) — so it gets its own, lower threshold
and a fail-closed gate. At most one run per program is in flight at a time; that
single-writer property is what makes the agent's merge-first behaviour safe."""
from __future__ import annotations

import os
from typing import Callable

from coscience import wiki_store
from coscience.models import ProgramStatus
from coscience.pause import is_paused
from coscience.worker import WEEKLY_WORKER_THRESHOLD, claude_usage_ok

WIKI_THRESHOLD = 70.0


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


# Read at call time, not import time, so a deployment (or a test) can change them
# without a restart.
def wiki_batch() -> int:
    return _env_int("COSCIENCE_WIKI_BATCH", 4)


def lint_every() -> int:
    return _env_int("COSCIENCE_WIKI_LINT_EVERY", 5)


def max_failures() -> int:
    return _env_int("COSCIENCE_WIKI_MAX_FAILURES", 3)


def collect_grace() -> float:
    return _env_float("COSCIENCE_WIKI_COLLECT_GRACE", 60.0)


def default_usage_gate(substrate) -> Callable[[], bool]:
    """fail_open=False on purpose: an unmetered autonomous loop is exactly what
    burns a usage window unattended, and a wiki run is never urgent enough to be
    worth that risk. repo_root so the global pause is honoured first."""
    return lambda: claude_usage_ok(WIKI_THRESHOLD,
                                   weekly_threshold=WEEKLY_WORKER_THRESHOLD,
                                   fail_open=False, repo_root=substrate.repo_root)


def _next_run_id(state: dict) -> str:
    previous = ((state.get("run") or {}).get("id")
                or (state.get("last_run") or {}).get("id") or "r0000")
    try:
        n = int(str(previous).lstrip("r"))
    except ValueError:
        n = 0
    return f"r{n + 1:04d}"


def beat(substrate, program, now: float, agent, *,
         usage_gate: Callable[[], bool] | None = None) -> str:
    """One wiki beat for one program. Returns a short line for the dispatch beat
    summary, or "" when there is nothing to say."""
    if program.status != ProgramStatus.ACTIVE or not program.wiki_enabled:
        return ""

    with wiki_store.state_guard(substrate, program.id) as state:
        run = state.get("run")
        if run:
            return _collect(substrate, program, now, agent, state, run)

        if is_paused(substrate.repo_root):
            return ""
        gate = usage_gate or default_usage_gate(substrate)
        if not gate():
            return ""

        quarantined = set(state.get("quarantined") or [])
        pending = wiki_store.pending_objects(
            substrate, program.id, state.get("ingested") or {}, quarantined)
        due_for_lint = (state.get("ingests_since_lint", 0) >= lint_every()
                        and not wiki_store.is_empty(substrate, program.id))
        if not due_for_lint and not pending:
            return ""

        wiki_store.ensure_bundle(substrate, program.id)
        run_id = _next_run_id(state)
        run_dir = wiki_store.run_dir(substrate, program.id, run_id)
        bundle = wiki_store.bundle_dir(substrate, program.id)
        dirty_before = _dirty_paths(substrate)

        if due_for_lint:
            kind, batch, objects, report = "lint", [], None, _lint_report(substrate, program)
        else:
            kind, report = "ingest", ""
            chosen = pending[:wiki_batch()]
            objects = [(o, wiki_store.object_hash(o)) for o in chosen]
            batch = [o.oid for o in chosen]

        token = agent.launch(kind=kind, program=program, bundle=bundle,
                             run_dir=run_dir, objects=objects, report=report,
                             model=program.wiki_model)
        # ingests_since_lint is NOT reset here: it resets when a lint run collects
        # ok, so a lint run that fails is still owed.
        state["run"] = {"id": run_id, "kind": kind, "batch": batch, "token": token,
                        "started_at": now, "model": program.wiki_model,
                        "dirty_before": dirty_before}
        return f"wiki: launched {kind} {run_id}" + (
            f" ({len(batch)} object{'s' if len(batch) != 1 else ''})" if batch else "")


def _lint_report(substrate, program) -> str:
    """The machine lint report handed to a lint run. Task 12 replaces this stub."""
    return ""


def _dirty_paths(substrate) -> list[str]:
    """Task 10 implements the containment check; a stub keeps Task 9 runnable."""
    return []


def _collect(substrate, program, now, agent, state, run) -> str:
    """Task 10 implements this."""
    return "wiki: running"
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_wiki_beat.py -v`
Expected: PASS (11 tests). `test_only_one_run_at_a_time` passes against the
`_collect` stub because the stub returns `"wiki: running"` and launches nothing —
Task 10 replaces it with the real branch and its own tests.

- [ ] **Step 5: Commit** (ask for approval first)

```bash
git add src/coscience/wiki.py tests/test_wiki_beat.py
git commit -m "feat(wiki): beat() launch path — gating, batching, lint cadence"
```

---

### Task 10: `wiki.beat()` — the collect half

Three properties this task exists to guarantee, each of which has a failure mode
that is invisible without it:

1. **No silent wedge.** A run whose process died between the two halves of the
   launch command leaves no `agent.exit`. Without a deadline that branch returns
   "collecting" forever and the program's wiki stops without a symptom.
2. **No poison object.** One malformed result must not stop all knowledge
   accumulation. After `max_failures()` its batch is quarantined and the beat
   moves on.
3. **No silent escape.** Runs use `--dangerously-skip-permissions`; cwd is a
   convention, not a sandbox. If anything changed outside `programs/<pid>/wiki/`
   and `programs/<pid>/.wiki/`, the batch is **not** marked ingested and the beat
   says so.

**Files:**
- Modify: `src/coscience/wiki.py` (`_collect`, `_dirty_paths`)
- Test: `tests/test_wiki_beat_collect.py`

**Interfaces:**
- Consumes: `agent.is_running(token)`, `agent.collect(run_dir) -> (status, report)`;
  `Substrate.commit(message)`; `wiki_store.object_hash`.
- Produces: after a successful ingest,
  `state["ingested"][oid] = {"hash": ..., "at": now, "run": run_id}`;
  `state["last_run"] = {"id", "kind", "status", "at", "pages_created",
  "pages_updated", "notes", "escaped"}`. `beat()` returns one of
  `"wiki: running"`, `"wiki: <kind> ok"`, `"wiki: <kind> failed"`,
  `"wiki: <kind> quarantined <n>"`, `"wiki: <kind> ESCAPED — batch not recorded"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_wiki_beat_collect.py
import pytest

from coscience import wiki, wiki_store
from coscience.models import Program, Result, Sprint, SprintStatus
from tests.test_wiki_beat import FakeWikiAgent


def _seed(substrate, n=1):
    p = Program(id="p1", title="P", goals="g")
    substrate.save_program(p)
    for i in range(n):
        substrate.save_sprint(Sprint(id=f"s{i}", status=SprintStatus.DONE, goals="g",
                                     program="p1"))
        substrate.save_result(Result(id=f"r{i}", sprint=f"s{i}", summary="s",
                                     completed_at=float(i)))
    return p


def test_running_run_is_left_alone(substrate):
    agent = FakeWikiAgent()
    p = _seed(substrate)
    wiki.beat(substrate, p, 100.0, agent)
    agent.alive = True
    assert wiki.beat(substrate, p, 101.0, agent) == "wiki: running"
    assert wiki_store.load_state(substrate, "p1")["run"] is not None


def test_ok_run_records_the_batch_and_bumps_the_lint_counter(substrate):
    agent = FakeWikiAgent()
    p = _seed(substrate)
    wiki.beat(substrate, p, 100.0, agent)
    run_dir = agent.launches[0]["run_dir"]
    (run_dir / "agent.exit").write_text("0\n")
    agent.alive = False
    assert wiki.beat(substrate, p, 200.0, agent) == "wiki: ingest ok"
    state = wiki_store.load_state(substrate, "p1")
    assert state["run"] is None
    assert "result:r0" in state["ingested"]
    assert state["ingested"]["result:r0"]["run"] == "r0001"
    assert state["ingested"]["result:r0"]["hash"].startswith("sha256:")
    assert state["ingests_since_lint"] == 1
    assert state["failures"] == 0
    assert state["last_run"]["status"] == "ok"


def test_ingested_object_is_no_longer_pending(substrate):
    agent = FakeWikiAgent()
    p = _seed(substrate)
    wiki.beat(substrate, p, 100.0, agent)
    (agent.launches[0]["run_dir"] / "agent.exit").write_text("0\n")
    agent.alive = False
    wiki.beat(substrate, p, 200.0, agent)
    assert wiki.beat(substrate, p, 300.0, agent) == ""
    assert len(agent.launches) == 1


def test_failed_run_counts_toward_quarantine(substrate, monkeypatch):
    monkeypatch.setenv("COSCIENCE_WIKI_MAX_FAILURES", "2")
    agent = FakeWikiAgent()
    p = _seed(substrate)
    for i, (t_launch, t_collect) in enumerate([(100.0, 200.0), (300.0, 400.0)]):
        wiki.beat(substrate, p, t_launch, agent)
        (agent.launches[i]["run_dir"] / "agent.exit").write_text("3\n")
        agent.alive = False
        line = wiki.beat(substrate, p, t_collect, agent)
        agent.alive = True
    assert line == "wiki: ingest quarantined 1"
    state = wiki_store.load_state(substrate, "p1")
    assert state["quarantined"] == ["result:r0"]
    assert state["failures"] == 0
    assert "result:r0" not in state["ingested"]


def test_quarantined_object_is_skipped_afterwards(substrate, monkeypatch):
    monkeypatch.setenv("COSCIENCE_WIKI_MAX_FAILURES", "1")
    agent = FakeWikiAgent()
    p = _seed(substrate)
    wiki.beat(substrate, p, 100.0, agent)
    (agent.launches[0]["run_dir"] / "agent.exit").write_text("3\n")
    agent.alive = False
    wiki.beat(substrate, p, 200.0, agent)
    assert wiki.beat(substrate, p, 300.0, agent) == ""
    assert len(agent.launches) == 1


def test_dead_process_without_exit_file_waits_out_the_grace_then_fails(substrate):
    agent = FakeWikiAgent()
    p = _seed(substrate)
    wiki.beat(substrate, p, 100.0, agent)
    agent.alive = False                       # process gone, no agent.exit written
    assert wiki.beat(substrate, p, 130.0, agent) == "wiki: collecting"
    assert wiki_store.load_state(substrate, "p1")["run"] is not None
    assert wiki.beat(substrate, p, 400.0, agent) == "wiki: ingest failed"
    assert wiki_store.load_state(substrate, "p1")["run"] is None


def test_lint_run_ok_resets_the_counter(substrate, monkeypatch):
    monkeypatch.setenv("COSCIENCE_WIKI_LINT_EVERY", "1")
    from coscience import wiki_okf
    agent = FakeWikiAgent()
    p = _seed(substrate, n=0)
    wiki_store.ensure_bundle(substrate, "p1")
    wiki_store.write_page(substrate, "p1", wiki_okf.Page(
        path="concepts/a.md", type="Concept", title="A", body="x"))
    with wiki_store.state_guard(substrate, "p1") as state:
        state["ingests_since_lint"] = 3
    wiki.beat(substrate, p, 100.0, agent)
    assert agent.launches[0]["kind"] == "lint"
    (agent.launches[0]["run_dir"] / "agent.exit").write_text("0\n")
    agent.alive = False
    assert wiki.beat(substrate, p, 200.0, agent) == "wiki: lint ok"
    assert wiki_store.load_state(substrate, "p1")["ingests_since_lint"] == 0


def test_failed_lint_run_still_owes_a_lint(substrate, monkeypatch):
    monkeypatch.setenv("COSCIENCE_WIKI_LINT_EVERY", "1")
    from coscience import wiki_okf
    agent = FakeWikiAgent()
    p = _seed(substrate, n=0)
    wiki_store.ensure_bundle(substrate, "p1")
    wiki_store.write_page(substrate, "p1", wiki_okf.Page(
        path="concepts/a.md", type="Concept", title="A", body="x"))
    with wiki_store.state_guard(substrate, "p1") as state:
        state["ingests_since_lint"] = 3
    wiki.beat(substrate, p, 100.0, agent)
    (agent.launches[0]["run_dir"] / "agent.exit").write_text("1\n")
    agent.alive = False
    assert wiki.beat(substrate, p, 200.0, agent) == "wiki: lint failed"
    assert wiki_store.load_state(substrate, "p1")["ingests_since_lint"] == 3


def test_report_counts_land_in_last_run(substrate):
    agent = FakeWikiAgent()
    p = _seed(substrate)
    wiki.beat(substrate, p, 100.0, agent)
    run_dir = agent.launches[0]["run_dir"]
    (run_dir / "agent.exit").write_text("0\n")
    (run_dir / "report.json").write_text(
        '{"pages_created": ["concepts/a.md", "sources/result-r0.md"],'
        ' "pages_updated": [], "notes": "two pages"}')
    agent.report = {"pages_created": ["concepts/a.md", "sources/result-r0.md"],
                    "pages_updated": [], "notes": "two pages"}
    agent.alive = False
    wiki.beat(substrate, p, 200.0, agent)
    last = wiki_store.load_state(substrate, "p1")["last_run"]
    assert last["pages_created"] == 2
    assert last["notes"] == "two pages"


def test_writes_outside_the_bundle_block_recording(substrate, monkeypatch):
    agent = FakeWikiAgent()
    p = _seed(substrate)
    wiki.beat(substrate, p, 100.0, agent)
    (agent.launches[0]["run_dir"] / "agent.exit").write_text("0\n")
    agent.alive = False
    monkeypatch.setattr(wiki, "_dirty_paths",
                        lambda s: ["sprints/s0/sprint.md", "programs/p1/wiki/index.md"])
    line = wiki.beat(substrate, p, 200.0, agent)
    assert line == "wiki: ingest ESCAPED — batch not recorded"
    state = wiki_store.load_state(substrate, "p1")
    assert state["ingested"] == {}
    assert state["last_run"]["escaped"] == ["sprints/s0/sprint.md"]
```

- [ ] **Step 2: Make `FakeWikiAgent.collect` return its report**

In `tests/test_wiki_beat.py`, change `FakeWikiAgent`:

```python
    def __init__(self):
        self.launches = []
        self.alive = True
        self.report = {}

    def collect(self, run_dir):
        from coscience import wiki_agent
        return wiki_agent.WikiAgent().collect(run_dir)
```

This makes the fake read the real `agent.exit` / `report.json` files the collect
tests write, so only the launch is faked — which is the only part that would
start a process.

- [ ] **Step 3: Run the test to verify it fails**

Run: `python -m pytest tests/test_wiki_beat_collect.py -v`
Expected: FAIL — every collect test fails, most with
`AssertionError: assert 'wiki: running' == 'wiki: ingest ok'`

- [ ] **Step 4: Implement `_collect` and `_dirty_paths`**

Replace both stubs in `src/coscience/wiki.py`. Add `import subprocess` and
`from pathlib import Path` to the imports.

```python
def _dirty_paths(substrate) -> list[str]:
    """Repo-relative paths git reports as changed. Best-effort: a substrate with
    no git repo yields [], which disables the containment check rather than
    blocking every run."""
    try:
        out = subprocess.run(
            ["git", "-C", str(substrate.repo_root), "status", "--porcelain"],
            capture_output=True, text=True, check=False, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    paths = []
    for line in out.splitlines():
        entry = line[3:].strip().strip('"')
        if " -> " in entry:                      # a rename: take the destination
            entry = entry.split(" -> ", 1)[1]
        if entry:
            paths.append(entry)
    return paths


def _escaped(substrate, program_id: str, before: list[str], after: list[str]) -> list[str]:
    """Paths that became dirty during the run and lie outside the program's wiki.

    Runs use --dangerously-skip-permissions, so cwd is a convention rather than a
    sandbox. We detect rather than revert: reverting would risk destroying a
    concurrent sprint's legitimate work, and a wiki run is never worth that."""
    allowed = (f"programs/{program_id}/wiki/", f"programs/{program_id}/.wiki/")
    new = [p for p in after if p not in set(before)]
    return sorted(p for p in new if not p.startswith(allowed))


def _collect(substrate, program, now, agent, state, run) -> str:
    run_id, kind = run.get("id", ""), run.get("kind", "ingest")
    run_dir = wiki_store.run_dir(substrate, program.id, run_id)
    if agent.is_running(run.get("token", "")):
        return "wiki: running"

    status, report = agent.collect(run_dir)
    if status == "running":
        # The process is gone but no exit code was written: the shell was killed
        # between the two halves of the launch command. One grace window covers a
        # slow filesystem; past it the run is dead, and without this deadline the
        # program's wiki would wedge here silently forever.
        if now - float(run.get("started_at") or 0.0) < collect_grace():
            return "wiki: collecting"
        status = "failed"

    batch = list(run.get("batch") or [])
    escaped: list[str] = []
    if status == "ok":
        escaped = _escaped(substrate, program.id,
                           list(run.get("dirty_before") or []), _dirty_paths(substrate))

    state["run"] = None
    state["last_run"] = {
        "id": run_id, "kind": kind, "status": status, "at": now,
        "pages_created": len(report.get("pages_created") or []),
        "pages_updated": len(report.get("pages_updated") or []),
        "notes": str(report.get("notes") or ""),
        "escaped": escaped,
    }

    if status == "ok" and escaped:
        # The batch is deliberately NOT recorded: an agent that wrote outside its
        # bundle may equally have written the wrong thing inside it.
        state["last_run"]["status"] = "escaped"
        substrate.commit(f"wiki {program.id}: {kind} {run_id} wrote outside the bundle")
        return f"wiki: {kind} ESCAPED — batch not recorded"

    line = f"wiki: {kind} {status}"
    if status == "ok":
        objects = {o.oid: o for o in wiki_store.program_objects(substrate, program.id)}
        for oid in batch:
            obj = objects.get(oid)
            state["ingested"][oid] = {
                "hash": wiki_store.object_hash(obj) if obj else "",
                "at": now, "run": run_id}
        if kind == "ingest":
            state["ingests_since_lint"] = state.get("ingests_since_lint", 0) + 1
        else:
            state["ingests_since_lint"] = 0
            _file_lint_report(substrate, program.id, run_dir, now)
        state["failures"] = 0
    else:
        state["failures"] = state.get("failures", 0) + 1
        if state["failures"] >= max_failures() and batch:
            quarantined = list(state.get("quarantined") or [])
            quarantined += [oid for oid in batch if oid not in quarantined]
            state["quarantined"] = quarantined
            state["failures"] = 0
            line = f"wiki: {kind} quarantined {len(batch)}"

    substrate.commit(f"wiki {program.id}: {kind} {run_id} {status}")
    return line


def _file_lint_report(substrate, program_id: str, run_dir: Path, now: float) -> None:
    """Move the agent's lint summary into .wiki/lint/<date>.md."""
    from datetime import datetime, timezone
    from coscience import wiki_agent
    text = wiki_agent.read_lint_report(run_dir)
    if not text.strip():
        return
    day = datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%d")
    d = wiki_store.state_dir(substrate, program_id) / "lint"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{day}.md").write_text(text)
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `python -m pytest tests/test_wiki_beat_collect.py tests/test_wiki_beat.py -v`
Expected: PASS (both files)

- [ ] **Step 6: Run the whole suite**

Run: `python -m pytest`
Expected: PASS

- [ ] **Step 7: Commit** (ask for approval first)

```bash
git add src/coscience/wiki.py tests/test_wiki_beat.py tests/test_wiki_beat_collect.py
git commit -m "feat(wiki): beat() collect path — quarantine, grace deadline, containment check"
```

---

### Task 11: Wire the beat into the `Dispatcher`

The beat joins the per-program pass that already exists in `run_one_cycle` for
the stale-chat-lock reaper. No new process, no change to `deploy.sh`.

**Files:**
- Modify: `src/coscience/dispatcher.py` (`CycleReport`, `Dispatcher.__init__`,
  the tail of `run_one_cycle`)
- Modify: `src/coscience/cli.py:79-84` (`dispatch_once`) and `:200-216` (the
  dispatch beat line)
- Test: `tests/test_wiki_dispatcher.py`

**Interfaces:**
- Consumes: `wiki.beat(substrate, program, now, agent, usage_gate=...) -> str`.
- Produces: `CycleReport.wiki: list[str]` — the non-empty beat lines from this
  cycle, in program order; `Dispatcher.__init__(..., wiki_agent=None)` where
  `None` means "construct the real `WikiAgent` lazily on first use".

- [ ] **Step 1: Write the failing test**

```python
# tests/test_wiki_dispatcher.py
from coscience.dispatcher import Dispatcher
from coscience.models import Program, ProgramStatus
from coscience.resources import ResourcePool
from coscience.scheduler import SchedulerPolicy
from tests.test_wiki_beat import FakeWikiAgent


def _dispatcher(substrate, agent, wiki_agent):
    return Dispatcher(substrate, agent, ResourcePool(), SchedulerPolicy(),
                      wiki_agent=wiki_agent)


def test_cycle_runs_the_wiki_beat_for_each_active_program(substrate, agent, monkeypatch):
    substrate.save_program(Program(id="p1", title="P1", goals="g"))
    substrate.save_program(Program(id="p2", title="P2", goals="g",
                                   status=ProgramStatus.CLOSED))
    seen = []

    import coscience.dispatcher as dmod
    monkeypatch.setattr(dmod.wiki, "beat",
                        lambda sub, prog, now, ag, **kw: (seen.append(prog.id), "wiki: x")[1])
    report = _dispatcher(substrate, agent, FakeWikiAgent()).run_one_cycle()
    assert seen == ["p1"]
    assert report.wiki == ["wiki: x"]


def test_empty_beat_lines_are_dropped(substrate, agent):
    substrate.save_program(Program(id="p1", title="P1", goals="g"))
    report = _dispatcher(substrate, agent, FakeWikiAgent()).run_one_cycle()
    assert report.wiki == []          # nothing pending, so the beat says nothing


def test_a_raising_wiki_beat_does_not_break_the_cycle(substrate, agent, monkeypatch):
    substrate.save_program(Program(id="p1", title="P1", goals="g"))

    def boom(*a, **kw):
        raise RuntimeError("wiki exploded")

    import coscience.dispatcher as dmod
    monkeypatch.setattr(dmod.wiki, "beat", boom)
    report = _dispatcher(substrate, agent, FakeWikiAgent()).run_one_cycle()
    assert report.wiki == ["wiki: error — wiki exploded"]
```

`ResourcePool()` with no arguments is valid — its only field, `capacity`, has a
default factory. The `substrate` and `agent` fixtures are the existing ones in
`tests/conftest.py`.

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_wiki_dispatcher.py -v`
Expected: FAIL — `TypeError: Dispatcher.__init__() got an unexpected keyword argument 'wiki_agent'`

- [ ] **Step 3: Extend `CycleReport` and `Dispatcher.__init__`**

In `src/coscience/dispatcher.py`, add to `CycleReport`:

```python
    wiki: list[str] = field(default_factory=list)   # non-empty wiki beat lines this cycle
```

and to `__init__`, after the existing parameters:

```python
        self._wiki_agent = wiki_agent      # None -> built lazily on first use
```

with the signature gaining `wiki_agent=None`. Add `from coscience import wiki` and
`import time` (if not already imported) at the top.

- [ ] **Step 4: Call the beat in the per-program pass**

In `run_one_cycle`, extend the existing per-program loop:

```python
        now = time.time()
        for program in self.substrate.iter_programs():
            reaped += len(artifacts.reap_stale_chat_locks(
                self.substrate, program.id, now, holder_busy=self._chat_busy(program.id)))
            line = self._wiki_beat(program, now)
            if line:
                report.wiki.append(line)
```

and add the helper:

```python
    def _wiki_beat(self, program, now: float) -> str:
        """Wiki maintenance is the lowest-priority thing this loop does, so it is
        also the thing least allowed to break it: a wiki failure is reported as a
        line, never raised into sprint supervision."""
        try:
            if self._wiki_agent is None:
                from coscience.wiki_agent import WikiAgent
                self._wiki_agent = WikiAgent()
            return wiki.beat(self.substrate, program, now, self._wiki_agent)
        except Exception as exc:
            return f"wiki: error — {exc}"[:200]
```

Make sure `reaped` is still initialised before the loop and `now` is computed once.

- [ ] **Step 5: Include wiki activity in the cycle's commit condition**

The tail of `run_one_cycle` commits only when something happened. `report.wiki`
must count, or a wiki run's state changes sit uncommitted until the next sprint
event:

```python
        if (report.granted or report.completed or report.hibernated
                or report.reconciled or reaped or report.wiki):
            self.substrate.commit("dispatch cycle")
```

- [ ] **Step 6: Surface it in the CLI beat line**

In `src/coscience/cli.py`, the dispatch `_beat` closure, append the wiki lines so
an operator watching the loop sees wiki activity:

```python
        def _beat():
            r = dispatch_once(args.repo)
            line = f"granted {r.granted} · completed {r.completed} · waiting {r.waiting}"
            if r.wiki:
                line += " · " + " · ".join(r.wiki)
            return (line,
                    {"granted": r.granted, "completed": r.completed, "hibernated": r.hibernated},
                    r.beaten)
```

and in the `--once` branch, after the existing print:

```python
            for line in r.wiki:
                print(line, flush=True)
```

- [ ] **Step 7: Run the tests**

Run: `python -m pytest tests/test_wiki_dispatcher.py tests/test_dispatcher.py tests/test_cli_dispatch.py -v`
Expected: PASS. The existing dispatcher tests must pass **unmodified** — if one
fails, the wiki beat is leaking into sprint supervision.

- [ ] **Step 8: Run the whole suite**

Run: `python -m pytest`
Expected: PASS

- [ ] **Step 9: Commit** (ask for approval first)

```bash
git add src/coscience/dispatcher.py src/coscience/cli.py tests/test_wiki_dispatcher.py
git commit -m "feat(wiki): run the wiki beat in the dispatch cycle"
```

---

### Task 12: `wiki_lint.py` — OKF and page rules, and the report

`wiki_lint` is pure: parsed pages in, findings out. It runs two ways — as a cheap
deterministic script, and as the input to an agent lint run. This task builds the
frame plus the rules that need only the pages themselves.

Rules in this task: `okf/bad-yaml`, `okf/missing-type`, `okf/index-frontmatter`,
`page/stub`, `page/stale`, `page/orphan`, `page/duplicate-slug`,
`page/near-duplicate`.

`page/near-duplicate` is spec'd as "title/alias overlap above threshold". This
implements exact match after normalisation (lowercase, non-alphanumerics folded
to hyphens), which catches the case that actually happens — the same idea written
twice with different punctuation or casing — without a similarity threshold to
tune. Fuzzy matching is a later refinement, and the agent's lint run catches what
the script misses.

**Files:**
- Create: `src/coscience/wiki_lint.py`
- Test: `tests/test_wiki_lint.py`

**Interfaces:**
- Consumes: `wiki_okf.Page`, `wiki_okf.RESERVED`, `wiki_okf.body_links`.
- Produces:
  ```python
  SEVERITIES = ("error", "warn", "info")

  @dataclass(frozen=True)
  class Finding:
      rule: str; severity: str; path: str; message: str

  def lint(pages: list[Page], *, index_body: str = "",
           objects: dict[str, str] | None = None,      # oid -> current hash; Task 14
           previous: dict[str, str] | None = None,     # path -> previous body; Task 14
           now: float | None = None) -> list[Finding]
      # sorted by (severity rank, path, rule); never raises

  def render_report(findings: list[Finding]) -> str
  def counts(findings: list[Finding]) -> dict[str, int]   # {"error": n, "warn": n, "info": n}
  ```
  `objects` and `previous` are parameters rather than lookups so the module stays
  pure — the caller (Task 14's `run_lint`) does the git and filesystem work.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_wiki_lint.py
import time

from coscience import wiki_lint, wiki_okf


def _page(path, **kw):
    kw.setdefault("type", "Concept")
    kw.setdefault("title", path.rsplit("/", 1)[-1][:-3])
    kw.setdefault("body", "# Definition\n\n" + "x" * 400 + "\n")
    return wiki_okf.Page(path=path, **kw)


def _rules(findings, prefix=""):
    return sorted({f.rule for f in findings if f.rule.startswith(prefix)})


def test_bad_yaml_is_an_error():
    page = wiki_okf.parse_page("concepts/a.md", "---\ntype: [oops\n---\n\nbody\n")
    findings = wiki_lint.lint([page])
    assert "okf/bad-yaml" in _rules(findings)
    bad = [f for f in findings if f.rule == "okf/bad-yaml"][0]
    assert bad.severity == "error"
    assert bad.path == "concepts/a.md"


def test_missing_type_is_an_error():
    findings = wiki_lint.lint([_page("concepts/a.md", type="")])
    assert "okf/missing-type" in _rules(findings)


def test_unknown_type_is_tolerated_not_flagged():
    findings = wiki_lint.lint([_page("concepts/a.md", type="Protocol")])
    assert _rules(findings, "okf/") == []


def test_stub_page_is_warned():
    findings = wiki_lint.lint([_page("concepts/a.md", body="# Definition\n\ntiny\n")])
    assert "page/stub" in _rules(findings)
    assert [f for f in findings if f.rule == "page/stub"][0].severity == "warn"


def test_stale_after_in_the_past_is_warned():
    findings = wiki_lint.lint([_page("concepts/a.md", stale_after="2020-01-01")],
                              now=time.time())
    assert "page/stale" in _rules(findings)


def test_stale_after_in_the_future_is_not_warned():
    findings = wiki_lint.lint([_page("concepts/a.md", stale_after="2099-01-01")],
                              now=time.time())
    assert "page/stale" not in _rules(findings)


def test_old_generated_at_falls_back_to_the_age_heuristic():
    old = {"by": "coscience-wiki/x", "at": "2020-01-01T00:00:00Z"}
    findings = wiki_lint.lint([_page("concepts/a.md", generated=old)], now=time.time())
    assert "page/stale" in _rules(findings)


def test_orphan_page_is_info():
    findings = wiki_lint.lint([_page("concepts/a.md")], index_body="# Index\n")
    assert "page/orphan" in _rules(findings)


def test_page_linked_from_index_is_not_an_orphan():
    findings = wiki_lint.lint([_page("concepts/a.md")],
                              index_body="- [a](/concepts/a.md)\n")
    assert "page/orphan" not in _rules(findings)


def test_page_linked_from_another_page_is_not_an_orphan():
    linker = _page("concepts/b.md", body="# Definition\n\nsee [a](/concepts/a.md)\n" + "x" * 400)
    findings = wiki_lint.lint([_page("concepts/a.md"), linker])
    assert not [f for f in findings if f.rule == "page/orphan" and f.path == "concepts/a.md"]


def test_duplicate_slug_across_directories_is_an_error():
    findings = wiki_lint.lint([_page("concepts/a.md"), _page("entities/a.md", type="Entity")])
    assert "page/duplicate-slug" in _rules(findings)


def test_near_duplicate_titles_are_warned():
    findings = wiki_lint.lint([
        _page("concepts/a.md", title="Template replication takeoff"),
        _page("concepts/b.md", title="Template replication takeoff ")])
    assert "page/near-duplicate" in _rules(findings)


def test_alias_colliding_with_another_title_is_a_near_duplicate():
    findings = wiki_lint.lint([
        _page("concepts/a.md", title="Takeoff regime"),
        _page("concepts/b.md", title="Something else", aliases=["takeoff regime"])])
    assert "page/near-duplicate" in _rules(findings)


def test_index_frontmatter_rule_only_fires_on_a_non_root_index():
    findings = wiki_lint.lint([_page("concepts/index.md")])
    assert "okf/index-frontmatter" in _rules(findings)


def test_findings_are_sorted_errors_first():
    findings = wiki_lint.lint([
        _page("concepts/a.md", type="", body="tiny"),
    ])
    assert findings[0].severity == "error"


def test_render_report_is_readable_and_groups_by_rule():
    findings = wiki_lint.lint([_page("concepts/a.md", type="", body="tiny")])
    text = wiki_lint.render_report(findings)
    assert "okf/missing-type" in text
    assert "concepts/a.md" in text
    assert "error" in text


def test_render_report_on_a_clean_wiki():
    assert "no findings" in wiki_lint.render_report([]).lower()


def test_counts():
    findings = wiki_lint.lint([_page("concepts/a.md", type="", body="tiny")])
    c = wiki_lint.counts(findings)
    assert c["error"] >= 1
    assert set(c) == {"error", "warn", "info"}


def test_lint_never_raises_on_junk():
    junk = wiki_okf.Page(path="concepts/x.md", type="Concept", tags=["a"],
                         generated={"at": 12345}, stale_after="not-a-date", body="")
    wiki_lint.lint([junk], now=time.time())        # must not raise
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_wiki_lint.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'coscience.wiki_lint'`

- [ ] **Step 3: Write the module**

```python
# src/coscience/wiki_lint.py
"""Wiki lint: pure rules over parsed pages.

Two consumers, one implementation. As a script it is cheap, deterministic and
gives the dashboard a health badge; as the input to an agent lint run it is the
list of things a machine could find so the agent can spend its turn on the things
only judgement can fix.

Ported from docs/_tmp_wiki/llm-wiki-skills/skills/llm-wiki-lint/lint.py and
extended for OKF and for the containment invariant."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from coscience import wiki_okf

SEVERITIES = ("error", "warn", "info")
_RANK = {s: i for i, s in enumerate(SEVERITIES)}

STUB_CHARS = 200
STALE_DAYS = 180
_NORMALISE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class Finding:
    rule: str
    severity: str
    path: str
    message: str


def lint(pages: list[wiki_okf.Page], *, index_body: str = "",
         objects: dict[str, str] | None = None,
         previous: dict[str, str] | None = None,
         now: float | None = None) -> list[Finding]:
    """Every finding for this bundle, worst first. Never raises — a lint that
    dies on a malformed page is a lint that stops running exactly when it is
    needed."""
    out: list[Finding] = []
    out += _okf_rules(pages)
    out += _page_rules(pages, index_body, now)
    out.sort(key=lambda f: (_RANK.get(f.severity, 9), f.path, f.rule))
    return out


def _okf_rules(pages: list[wiki_okf.Page]) -> list[Finding]:
    out = []
    for p in pages:
        if p.bad_yaml:
            out.append(Finding("okf/bad-yaml", "error", p.path,
                               "frontmatter does not parse as YAML"))
            continue
        if not p.type.strip():
            out.append(Finding("okf/missing-type", "error", p.path,
                               "no `type` — the page is not OKF-conformant"))
        if p.path.rsplit("/", 1)[-1] == "index.md" and p.path != "index.md":
            out.append(Finding("okf/index-frontmatter", "warn", p.path,
                               "a non-root index.md should not carry page frontmatter"))
    return out


def _page_rules(pages: list[wiki_okf.Page], index_body: str,
                now: float | None) -> list[Finding]:
    out = []
    linked = set(wiki_okf.body_links(index_body))
    for p in pages:
        linked.update(wiki_okf.body_links(p.body))
    by_slug: dict[str, list[str]] = {}
    titles: dict[str, str] = {}

    for p in pages:
        if len(p.body.strip()) < STUB_CHARS:
            out.append(Finding("page/stub", "warn", p.path,
                               f"body is under {STUB_CHARS} characters"))
        stale = _staleness(p, now)
        if stale:
            out.append(Finding("page/stale", "warn", p.path, stale))
        if not _is_linked(p.path, linked):
            out.append(Finding("page/orphan", "info", p.path,
                               "no inbound links and absent from index.md"))
        by_slug.setdefault(p.slug, []).append(p.path)
        for name in [p.title, *p.aliases]:
            key = _norm(name)
            if not key:
                continue
            other = titles.get(key)
            if other and other != p.path:
                out.append(Finding("page/near-duplicate", "warn", p.path,
                                   f"title or alias '{name}' also names {other}"))
            titles.setdefault(key, p.path)

    for slug, paths in by_slug.items():
        if len(paths) > 1:
            for path in paths:
                out.append(Finding("page/duplicate-slug", "error", path,
                                   f"slug '{slug}' also exists at "
                                   + ", ".join(p for p in paths if p != path)))
    return out


def _is_linked(path: str, linked: set[str]) -> bool:
    """Links are written bundle-absolute (/concepts/a.md) but may appear
    relative; accept either rather than crying orphan on a working link."""
    return any(target.lstrip("/").endswith(path) or target.endswith(path)
               for target in linked)


def _norm(text: str) -> str:
    return _NORMALISE.sub("-", (text or "").strip().lower()).strip("-")


def _staleness(page: wiki_okf.Page, now: float | None) -> str:
    if now is None:
        return ""
    today = datetime.fromtimestamp(now, timezone.utc)
    declared = _date(page.stale_after)
    if declared is not None:
        return (f"stale_after {page.stale_after} has passed"
                if declared < today else "")
    generated = _date(str(page.generated.get("at", "")))
    if generated is not None and (today - generated).days > STALE_DAYS:
        return (f"no stale_after and generated {(today - generated).days} days ago "
                f"(over {STALE_DAYS})")
    return ""


def _date(text: str):
    """Parse a date or ISO timestamp; None when it is absent or unparseable —
    an unreadable date is not a lint finding of its own, it just disables the
    check for that page."""
    text = (text or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            parsed = datetime.strptime(text, fmt)
        except (ValueError, TypeError):
            continue
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def counts(findings: list[Finding]) -> dict[str, int]:
    out = {s: 0 for s in SEVERITIES}
    for f in findings:
        if f.severity in out:
            out[f.severity] += 1
    return out


def render_report(findings: list[Finding]) -> str:
    """A markdown report, grouped by rule so an agent reading it sees one class of
    problem at a time rather than a shuffled list of paths."""
    if not findings:
        return "# Wiki lint\n\nNo findings.\n"
    c = counts(findings)
    lines = ["# Wiki lint", "",
             f"{c['error']} error(s), {c['warn']} warning(s), {c['info']} info.", ""]
    grouped: dict[tuple[str, str], list[Finding]] = {}
    for f in findings:
        grouped.setdefault((f.severity, f.rule), []).append(f)
    for (severity, rule) in sorted(grouped, key=lambda k: (_RANK.get(k[0], 9), k[1])):
        items = grouped[(severity, rule)]
        lines.append(f"## `{rule}` — {severity} ({len(items)})")
        lines += [f"- `{f.path}`: {f.message}" for f in items]
        lines.append("")
    return "\n".join(lines)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_wiki_lint.py -v`
Expected: PASS (19 tests)

- [ ] **Step 5: Commit** (ask for approval first)

```bash
git add src/coscience/wiki_lint.py tests/test_wiki_lint.py
git commit -m "feat(wiki): lint frame plus OKF and page rules"
```

---

### Task 13: Link and relation rules, and the mechanical auto-fixes

`rel/no-link` is the approach-C invariant: the typed relations in frontmatter are
an overlay on the prose, never a substitute for it. It is auto-fixable, and
fixing it mechanically before the agent runs is free.

Rules in this task: `link/wikilink` (auto), `link/broken`, `rel/unknown-type`,
`rel/no-link` (auto), `rel/no-source`, `rel/dangling`, `rel/cycle`.

**Files:**
- Modify: `src/coscience/wiki_lint.py` (append; extend `lint`)
- Test: `tests/test_wiki_lint_links.py`

**Interfaces:**
- Consumes: `wiki_okf.RELATION_TYPES`, `wiki_okf.CONFIDENCE`, `wiki_okf.wikilinks`,
  `wiki_okf.render_page`.
- Produces:
  ```python
  AUTOFIXABLE = frozenset({"link/wikilink", "rel/no-link", "okf/index-frontmatter"})

  def autofix(pages: list[Page]) -> tuple[list[Page], list[Finding]]
      # returns (changed pages only, findings describing what was fixed)
  ```

- [ ] **Step 1: Write the failing test**

```python
# tests/test_wiki_lint_links.py
from coscience import wiki_lint, wiki_okf


def _page(path, body="", relations=(), **kw):
    kw.setdefault("type", "Concept")
    kw.setdefault("title", path.rsplit("/", 1)[-1][:-3])
    return wiki_okf.Page(path=path, body=body or ("x" * 400),
                         relations=[wiki_okf.Relation(**r) for r in relations], **kw)


def _rules(findings, prefix=""):
    return sorted({f.rule for f in findings if f.rule.startswith(prefix)})


def test_wikilink_is_warned():
    findings = wiki_lint.lint([_page("concepts/a.md", body="see [[hydrolysis-rate]] " + "x" * 400)])
    assert "link/wikilink" in _rules(findings)


def test_broken_link_is_a_warning_never_an_error():
    findings = wiki_lint.lint([_page("concepts/a.md", body="see [b](/concepts/b.md) " + "x" * 400)])
    broken = [f for f in findings if f.rule == "link/broken"]
    assert broken and broken[0].severity == "warn"


def test_external_links_are_not_broken():
    findings = wiki_lint.lint([_page("concepts/a.md", body="see [x](https://x.test) " + "x" * 400)])
    assert "link/broken" not in _rules(findings)


def test_unknown_relation_type_is_an_error():
    findings = wiki_lint.lint([_page(
        "concepts/a.md", body="see [b](/concepts/b.md) " + "x" * 400,
        relations=[{"type": "relates_to", "target": "/concepts/b.md", "source": "c1"}])])
    assert "rel/unknown-type" in _rules(findings)


def test_relation_without_a_body_link_is_the_containment_error():
    findings = wiki_lint.lint([_page(
        "concepts/a.md", body="x" * 400,
        relations=[{"type": "requires", "target": "/concepts/b.md", "source": "c1"}])])
    rel = [f for f in findings if f.rule == "rel/no-link"]
    assert rel and rel[0].severity == "error"


def test_relation_without_a_source_is_an_error():
    findings = wiki_lint.lint([_page(
        "concepts/a.md", body="see [b](/concepts/b.md) " + "x" * 400,
        relations=[{"type": "requires", "target": "/concepts/b.md"}])])
    assert "rel/no-source" in _rules(findings)


def test_relation_source_naming_an_unknown_source_id_is_an_error():
    page = _page("concepts/a.md", body="see [b](/concepts/b.md) " + "x" * 400,
                 relations=[{"type": "requires", "target": "/concepts/b.md",
                             "source": "nope"}])
    page.sources = [wiki_okf.Source(id="c1", resource="/sources/result-r1.md")]
    assert "rel/no-source" in _rules(wiki_lint.lint([page]))


def test_relation_to_a_missing_page_is_dangling():
    findings = wiki_lint.lint([_page(
        "concepts/a.md", body="see [b](/concepts/b.md) " + "x" * 400,
        relations=[{"type": "requires", "target": "/concepts/b.md", "source": "c1"}])])
    dangling = [f for f in findings if f.rule == "rel/dangling"]
    assert dangling and dangling[0].severity == "warn"


def test_replaces_cycle_is_info():
    a = _page("concepts/a.md", body="see [b](/concepts/b.md) " + "x" * 400,
              relations=[{"type": "replaces", "target": "/concepts/b.md", "source": "c1"}])
    b = _page("concepts/b.md", body="see [a](/concepts/a.md) " + "x" * 400,
              relations=[{"type": "replaces", "target": "/concepts/a.md", "source": "c1"}])
    findings = wiki_lint.lint([a, b])
    cycle = [f for f in findings if f.rule == "rel/cycle"]
    assert cycle and cycle[0].severity == "info"


def test_contradicts_is_not_treated_as_a_cycle():
    a = _page("concepts/a.md", body="see [b](/concepts/b.md) " + "x" * 400,
              relations=[{"type": "contradicts", "target": "/concepts/b.md", "source": "c1"}])
    b = _page("concepts/b.md", body="see [a](/concepts/a.md) " + "x" * 400,
              relations=[{"type": "contradicts", "target": "/concepts/a.md", "source": "c1"}])
    assert "rel/cycle" not in _rules(wiki_lint.lint([a, b]))


def test_autofix_rewrites_wikilinks_into_markdown_links():
    a = _page("concepts/a.md", body="see [[hydrolysis-rate]] " + "x" * 400)
    b = _page("concepts/hydrolysis-rate.md")
    changed, fixed = wiki_lint.autofix([a, b])
    assert [p.path for p in changed] == ["concepts/a.md"]
    assert "[hydrolysis-rate](/concepts/hydrolysis-rate.md)" in changed[0].body
    assert "[[hydrolysis-rate]]" not in changed[0].body
    assert "link/wikilink" in {f.rule for f in fixed}


def test_autofix_leaves_a_wikilink_with_no_target_page_alone():
    a = _page("concepts/a.md", body="see [[nowhere]] " + "x" * 400)
    changed, _ = wiki_lint.autofix([a])
    assert changed == []


def test_autofix_appends_a_relation_link_to_satisfy_containment():
    a = _page("concepts/a.md", body="# Definition\n\n" + "x" * 400,
              relations=[{"type": "requires", "target": "/concepts/b.md", "source": "c1"}])
    b = _page("concepts/b.md", title="B")
    changed, fixed = wiki_lint.autofix([a, b])
    assert "(/concepts/b.md)" in changed[0].body
    assert "rel/no-link" in {f.rule for f in fixed}
    assert "rel/no-link" not in {f.rule for f in wiki_lint.lint(changed + [b])}


def test_autofix_is_idempotent():
    a = _page("concepts/a.md", body="see [[b]] " + "x" * 400)
    b = _page("concepts/b.md")
    once, _ = wiki_lint.autofix([a, b])
    twice, findings = wiki_lint.autofix(once + [b])
    assert twice == []
    assert findings == []


def test_autofixable_set():
    # okf/index-frontmatter is reported but not auto-fixed — see the comment on
    # AUTOFIXABLE for why stripping frontmatter mechanically is not worth it.
    assert wiki_lint.AUTOFIXABLE == {"link/wikilink", "rel/no-link"}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_wiki_lint_links.py -v`
Expected: FAIL — `AttributeError: module 'coscience.wiki_lint' has no attribute 'autofix'`

- [ ] **Step 3: Extend `lint()` and add the link/relation rules**

In `src/coscience/wiki_lint.py`, add to `lint()` after `_page_rules`:

```python
    out += _link_rules(pages)
    out += _relation_rules(pages)
```

and append:

```python
# Spec §9 also marks `okf/index-frontmatter` auto-fixable. Its fix is to strip a
# page's frontmatter entirely, which needs a raw-write path write_page does not
# have — and stripping frontmatter mechanically is how you lose content. It is
# reported and left to the agent's lint run instead.
AUTOFIXABLE = frozenset({"link/wikilink", "rel/no-link"})

# Directional relations that must stay acyclic. `contradicts` is symmetric in
# meaning and stored on one side only, so a mutual pair is correct, not a cycle.
_ACYCLIC = ("replaces", "causally_precedes")


def _known_paths(pages: list[wiki_okf.Page]) -> set[str]:
    return {p.path for p in pages}


def _resolve(target: str) -> str:
    """A link target as a bundle-relative page path, or "" if it is not one."""
    target = (target or "").split("#", 1)[0].strip()
    if not target or "://" in target or target.startswith("mailto:"):
        return ""
    return target.lstrip("/")


def _link_rules(pages: list[wiki_okf.Page]) -> list[Finding]:
    known = _known_paths(pages)
    out = []
    for p in pages:
        for slug in wiki_okf.wikilinks(p.body):
            out.append(Finding("link/wikilink", "warn", p.path,
                               f"`[[{slug}]]` should be a markdown link"))
        for target in wiki_okf.body_links(p.body):
            rel = _resolve(target)
            # OKF requires consumers to tolerate broken links, and in a living
            # wiki a broken link often marks knowledge not yet written. Report,
            # never fail.
            if rel and rel not in known:
                out.append(Finding("link/broken", "warn", p.path,
                                   f"link target `{target}` does not exist"))
    return out


def _relation_rules(pages: list[wiki_okf.Page]) -> list[Finding]:
    known = _known_paths(pages)
    out = []
    edges: dict[str, set[str]] = {}
    for p in pages:
        body_targets = {_resolve(t) for t in wiki_okf.body_links(p.body)}
        source_ids = set(p.sources_by_id())
        for r in p.relations:
            if r.type not in wiki_okf.RELATION_TYPES:
                out.append(Finding("rel/unknown-type", "error", p.path,
                                   f"relation type `{r.type}` is outside the frozen "
                                   f"vocabulary"))
            rel = _resolve(r.target)
            if rel and rel not in body_targets:
                out.append(Finding("rel/no-link", "error", p.path,
                                   f"relation `{r.type}` -> `{r.target}` is not linked "
                                   f"from the body"))
            if not r.source:
                out.append(Finding("rel/no-source", "error", p.path,
                                   f"relation `{r.type}` -> `{r.target}` names no source"))
            elif r.source not in source_ids:
                out.append(Finding("rel/no-source", "error", p.path,
                                   f"relation `{r.type}` -> `{r.target}` names unknown "
                                   f"source id `{r.source}`"))
            if r.confidence and r.confidence not in wiki_okf.CONFIDENCE:
                out.append(Finding("rel/unknown-type", "error", p.path,
                                   f"confidence `{r.confidence}` is not low|med|high"))
            if rel and rel not in known:
                out.append(Finding("rel/dangling", "warn", p.path,
                                   f"relation target `{r.target}` does not exist"))
            if r.type in _ACYCLIC and rel:
                edges.setdefault(p.path, set()).add(rel)
    for path in sorted(_cycle_nodes(edges)):
        out.append(Finding("rel/cycle", "info", path,
                           "part of a cycle in `replaces` / `causally_precedes`"))
    return out


def _cycle_nodes(edges: dict[str, set[str]]) -> set[str]:
    """Nodes on at least one directed cycle. Iterative DFS with a colour map —
    the graph is small, but a recursive walk on an adversarial bundle is not
    worth the risk."""
    on_cycle: set[str] = set()
    colour: dict[str, int] = {}
    for start in list(edges):
        if colour.get(start):
            continue
        stack = [(start, iter(sorted(edges.get(start, ()))))]
        path = [start]
        colour[start] = 1
        while stack:
            node, children = stack[-1]
            nxt = next(children, None)
            if nxt is None:
                colour[node] = 2
                stack.pop()
                path.pop()
                continue
            state = colour.get(nxt, 0)
            if state == 1:
                on_cycle.update(path[path.index(nxt):])
            elif state == 0:
                colour[nxt] = 1
                path.append(nxt)
                stack.append((nxt, iter(sorted(edges.get(nxt, ())))))
    return on_cycle


def autofix(pages: list[wiki_okf.Page]) -> tuple[list[wiki_okf.Page], list[Finding]]:
    """Apply the deterministic fixes, returning only the pages that changed.

    These run before an agent lint run so the agent's turn is spent on judgement
    calls rather than on mechanical edits it would do worse and slower."""
    by_slug = {p.slug: p.path for p in pages}
    changed: list[wiki_okf.Page] = []
    fixed: list[Finding] = []
    for p in pages:
        body = p.body
        for slug in wiki_okf.wikilinks(body):
            target = by_slug.get(slug)
            if not target:
                continue                     # nothing to point at; leave it for the agent
            body = body.replace(f"[[{slug}]]", f"[{slug}](/{target})")
            fixed.append(Finding("link/wikilink", "warn", p.path,
                                 f"rewrote `[[{slug}]]` as a markdown link"))
        body_targets = {_resolve(t) for t in wiki_okf.body_links(body)}
        missing = []
        for r in p.relations:
            rel = _resolve(r.target)
            if rel and rel not in body_targets and rel not in missing:
                missing.append(rel)
        if missing:
            lines = [f"- `{r.type}` [{_title(pages, rel)}](/{rel})"
                     for r, rel in ((r, _resolve(r.target)) for r in p.relations)
                     if rel in missing]
            body = body.rstrip("\n") + "\n\n# Related\n\n" + "\n".join(dict.fromkeys(lines)) + "\n"
            for rel in missing:
                fixed.append(Finding("rel/no-link", "error", p.path,
                                     f"linked `{rel}` from the body to satisfy containment"))
        if body != p.body:
            p.body = body
            changed.append(p)
    return changed, fixed


def _title(pages: list[wiki_okf.Page], path: str) -> str:
    for p in pages:
        if p.path == path:
            return p.title or p.slug
    return path.rsplit("/", 1)[-1][:-3]
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_wiki_lint_links.py tests/test_wiki_lint.py -v`
Expected: PASS. If `test_autofix_appends_a_relation_link_to_satisfy_containment`
fails on the second `lint` call, the appended `# Related` links are not being
seen by `_relation_rules` — check that `_resolve` strips the leading slash on
both sides.

- [ ] **Step 5: Commit** (ask for approval first)

```bash
git add src/coscience/wiki_lint.py tests/test_wiki_lint_links.py
git commit -m "feat(wiki): link and relation lint rules with mechanical autofixes"
```

---

### Task 14: Source, trust and human-notes rules, and `run_lint`

The last three rule families need information from outside the bundle — the raw
objects' current hashes, and each page's previous git revision. `lint()` stays
pure by taking those as parameters; `run_lint()` is the IO wrapper that gathers
them.

Rules in this task: `src/hash-drift`, `src/missing`, `src/is-concept`,
`human-notes/removed`, `trust/unverified-stable`.

**Files:**
- Modify: `src/coscience/wiki_lint.py` (append; extend `lint`)
- Modify: `src/coscience/wiki_store.py` (append `previous_bodies`)
- Modify: `src/coscience/wiki.py` (`_lint_report` → real implementation)
- Test: `tests/test_wiki_lint_sources.py`

**Interfaces:**
- Consumes: `wiki_store.program_objects`, `wiki_store.object_hash`,
  `wiki_store.iter_pages`, `wiki_store.bundle_dir`.
- Produces:
  ```python
  # wiki_lint
  def run_lint(substrate, program_id, *, now: float | None = None,
               fix: bool = False) -> tuple[list[Finding], int]
      # (findings, pages_fixed); with fix=True the mechanical fixes are written
      # back before the findings are computed

  # wiki_store
  def previous_bodies(substrate, program_id) -> dict[str, str]
      # bundle-relative path -> body at HEAD; {} when git is unavailable
  ```

- [ ] **Step 1: Write the failing test**

```python
# tests/test_wiki_lint_sources.py
import subprocess

from coscience import wiki_lint, wiki_okf, wiki_store
from coscience.models import Program, Result, Sprint, SprintStatus


def _src(path="sources/result-r1.md", **kw):
    kw.setdefault("type", "Source")
    kw.setdefault("title", "Sprint s1 result")
    kw.setdefault("body", "# Summary\n\n" + "x" * 400)
    kw.setdefault("graph_excluded", True)
    return wiki_okf.Page(path=path, **kw)


def _rules(findings, prefix=""):
    return sorted({f.rule for f in findings if f.rule.startswith(prefix)})


def test_hash_drift_is_an_error():
    page = _src(extra={"origin": "result:r1", "origin_hash": "sha256:old"})
    findings = wiki_lint.lint([page], objects={"result:r1": "sha256:new"})
    drift = [f for f in findings if f.rule == "src/hash-drift"]
    assert drift and drift[0].severity == "error"


def test_matching_hash_is_clean():
    page = _src(extra={"origin": "result:r1", "origin_hash": "sha256:same"})
    findings = wiki_lint.lint([page], objects={"result:r1": "sha256:same"})
    assert _rules(findings, "src/") == []


def test_missing_origin_object_is_an_error():
    page = _src(extra={"origin": "result:gone", "origin_hash": "sha256:x"})
    findings = wiki_lint.lint([page], objects={"result:r1": "sha256:x"})
    assert "src/missing" in _rules(findings)


def test_source_checks_are_skipped_when_objects_is_none():
    page = _src(extra={"origin": "result:r1", "origin_hash": "sha256:old"})
    assert _rules(wiki_lint.lint([page]), "src/") == []


def test_a_concept_titled_like_a_source_is_an_error():
    source = _src(title="Sprint p3-c14 result")
    concept = wiki_okf.Page(path="concepts/sprint-p3-c14-result.md", type="Concept",
                            title="Sprint p3-c14 result", body="x" * 400)
    assert "src/is-concept" in _rules(wiki_lint.lint([source, concept]))


def test_removed_human_notes_is_an_error():
    page = wiki_okf.Page(path="concepts/a.md", type="Concept", title="A",
                         body="# Definition\n\n" + "x" * 400)
    previous = {"concepts/a.md": "# Definition\n\nold\n\n# Human notes\n\nOleg: careful.\n"}
    findings = wiki_lint.lint([page], previous=previous)
    removed = [f for f in findings if f.rule == "human-notes/removed"]
    assert removed and removed[0].severity == "error"


def test_kept_human_notes_is_clean():
    body = "# Definition\n\n" + "x" * 400 + "\n\n# Human notes\n\nOleg: careful.\n"
    page = wiki_okf.Page(path="concepts/a.md", type="Concept", title="A", body=body)
    previous = {"concepts/a.md": "# Human notes\n\nOleg: careful.\n"}
    assert "human-notes/removed" not in _rules(wiki_lint.lint([page], previous=previous))


def test_stable_without_verification_is_info():
    page = wiki_okf.Page(path="concepts/a.md", type="Concept", title="A",
                         status="stable", body="x" * 400)
    findings = wiki_lint.lint([page])
    trust = [f for f in findings if f.rule == "trust/unverified-stable"]
    assert trust and trust[0].severity == "info"


def test_stable_with_verification_is_clean():
    page = wiki_okf.Page(path="concepts/a.md", type="Concept", title="A",
                         status="stable", body="x" * 400,
                         verified=[{"by": "human:oleg", "at": "2026-08-21T09:00:00Z"}])
    assert "trust/unverified-stable" not in _rules(wiki_lint.lint([page]))


def test_run_lint_reads_the_bundle_and_the_real_objects(wiki_bundle):
    substrate, pid = wiki_bundle
    substrate.save_sprint(Sprint(id="s1", status=SprintStatus.DONE, goals="g", program=pid))
    substrate.save_result(Result(id="r1", sprint="s1", summary="s", completed_at=1.0))
    obj = wiki_store.program_objects(substrate, pid)[0]
    wiki_store.write_page(substrate, pid, _src(
        extra={"origin": obj.oid, "origin_hash": "sha256:stale"}))
    findings, fixed = wiki_lint.run_lint(substrate, pid)
    assert fixed == 0
    assert "src/hash-drift" in _rules(findings)


def test_run_lint_with_fix_writes_pages_back(wiki_bundle):
    substrate, pid = wiki_bundle
    wiki_store.write_page(substrate, pid, wiki_okf.Page(
        path="concepts/b.md", type="Concept", title="B", body="x" * 400))
    wiki_store.write_page(substrate, pid, wiki_okf.Page(
        path="concepts/a.md", type="Concept", title="A",
        body="see [[b]]\n\n" + "x" * 400))
    findings, fixed = wiki_lint.run_lint(substrate, pid, fix=True)
    assert fixed == 1
    text = (wiki_store.bundle_dir(substrate, pid) / "concepts" / "a.md").read_text()
    assert "[b](/concepts/b.md)" in text
    assert "link/wikilink" not in _rules(wiki_lint.run_lint(substrate, pid)[0])


def test_previous_bodies_reads_head(wiki_bundle):
    # the `substrate` fixture is a bare tmp_path with no git repo, so this test
    # makes one — which is also why previous_bodies returns {} everywhere else
    substrate, pid = wiki_bundle
    root = str(substrate.repo_root)
    subprocess.run(["git", "-C", root, "init", "-q"], check=True)
    subprocess.run(["git", "-C", root, "config", "user.email", "t@t.test"], check=True)
    subprocess.run(["git", "-C", root, "config", "user.name", "t"], check=True)
    wiki_store.write_page(substrate, pid, wiki_okf.Page(
        path="concepts/a.md", type="Concept", title="A",
        body="# Human notes\n\nkeep me\n"))
    subprocess.run(["git", "-C", str(substrate.repo_root), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(substrate.repo_root), "commit", "-q", "-m", "x"],
                   check=True)
    bodies = wiki_store.previous_bodies(substrate, pid)
    assert "keep me" in bodies["concepts/a.md"]


def test_previous_bodies_empty_without_git(substrate):
    from coscience.models import Program
    substrate.save_program(Program(id="p9", title="P", goals="g"))
    wiki_store.ensure_bundle(substrate, "p9")
    assert isinstance(wiki_store.previous_bodies(substrate, "p9"), dict)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_wiki_lint_sources.py -v`
Expected: FAIL — `AttributeError: module 'coscience.wiki_lint' has no attribute 'run_lint'`

- [ ] **Step 3: Add `previous_bodies` to `wiki_store.py`**

```python
def previous_bodies(substrate, program_id: str) -> dict[str, str]:
    """Each page's body at git HEAD, keyed by bundle-relative path.

    This is what makes the protected `# Human notes` section enforceable: the
    only way to know a section was removed is to look at what was there before.
    Best-effort — no git, no history, no finding."""
    import subprocess
    prefix = f"programs/{program_id}/wiki/"
    out: dict[str, str] = {}
    for rel in page_paths(substrate, program_id):
        try:
            text = subprocess.run(
                ["git", "-C", str(substrate.repo_root), "show", f"HEAD:{prefix}{rel}"],
                capture_output=True, text=True, check=False, timeout=30)
        except (OSError, subprocess.SubprocessError):
            return out
        if text.returncode == 0:
            out[rel] = wiki_okf.parse_page(rel, text.stdout).body
    return out
```

- [ ] **Step 4: Add the rules and `run_lint` to `wiki_lint.py`**

In `lint()`, after the existing rule calls:

```python
    out += _source_rules(pages, objects)
    out += _trust_rules(pages, previous)
```

and append:

```python
HUMAN_NOTES = "Human notes"


def _source_rules(pages: list[wiki_okf.Page],
                  objects: dict[str, str] | None) -> list[Finding]:
    out = []
    source_titles = {_norm(p.title): p.path for p in pages if p.type == "Source"}
    for p in pages:
        if p.type == "Source" and objects is not None:
            oid = str(p.extra.get("origin", ""))
            declared = str(p.extra.get("origin_hash", ""))
            if not oid:
                continue
            current = objects.get(oid)
            if current is None:
                out.append(Finding("src/missing", "error", p.path,
                                   f"origin object `{oid}` no longer exists"))
            elif declared and declared != current:
                out.append(Finding("src/hash-drift", "error", p.path,
                                   f"`{oid}` changed since ingest "
                                   f"({declared} -> {current})"))
        elif p.type in ("Concept", "Entity"):
            # The reference implementation's most common failure: the article
            # itself becomes a concept, and the wiki fills with pages named after
            # their sources instead of after ideas.
            other = source_titles.get(_norm(p.title))
            if other:
                out.append(Finding("src/is-concept", "error", p.path,
                                   f"title matches the source page {other} — a source "
                                   f"title is never a concept"))
    return out


def _trust_rules(pages: list[wiki_okf.Page],
                 previous: dict[str, str] | None) -> list[Finding]:
    out = []
    for p in pages:
        if p.status == "stable" and not p.verified:
            out.append(Finding("trust/unverified-stable", "info", p.path,
                               "status is stable but nothing has verified it"))
        if previous is None:
            continue
        before = previous.get(p.path)
        if before is None:
            continue
        had = wiki_okf.Page(path=p.path, body=before).has_section(HUMAN_NOTES)
        if had and not p.has_section(HUMAN_NOTES):
            out.append(Finding("human-notes/removed", "error", p.path,
                               "the protected `# Human notes` section was removed"))
    return out


def run_lint(substrate, program_id: str, *, now: float | None = None,
             fix: bool = False) -> tuple[list[Finding], int]:
    """Lint a real bundle. The only function here that touches the filesystem;
    everything above it is pure so the rules can be tested without a substrate.

    With `fix=True` the mechanical fixes are applied and written back first, so
    the findings returned are what is actually left for a human or an agent."""
    from coscience import wiki_store
    pages = wiki_store.iter_pages(substrate, program_id)
    fixed_count = 0
    if fix:
        changed, _ = autofix(pages)
        for page in changed:
            wiki_store.write_page(substrate, program_id, page)
        fixed_count = len(changed)
    objects = {o.oid: wiki_store.object_hash(o)
               for o in wiki_store.program_objects(substrate, program_id)}
    try:
        index_body = (wiki_store.bundle_dir(substrate, program_id) / "index.md").read_text()
    except OSError:
        index_body = ""
    findings = lint(pages, index_body=index_body, objects=objects,
                    previous=wiki_store.previous_bodies(substrate, program_id),
                    now=now)
    return findings, fixed_count
```

- [ ] **Step 5: Give `wiki._lint_report` its real body**

In `src/coscience/wiki.py`, replace the stub:

```python
def _lint_report(substrate, program) -> str:
    """The machine report a lint run is handed. The deterministic fixes are
    applied here, before the agent starts, so its turn goes on judgement calls."""
    from coscience import wiki_lint
    findings, _fixed = wiki_lint.run_lint(substrate, program.id, fix=True)
    return wiki_lint.render_report(findings)
```

- [ ] **Step 6: Run the tests**

Run: `python -m pytest tests/test_wiki_lint_sources.py tests/test_wiki_lint.py tests/test_wiki_lint_links.py tests/test_wiki_beat_collect.py -v`
Expected: PASS

- [ ] **Step 7: Run the whole suite**

Run: `python -m pytest`
Expected: PASS

- [ ] **Step 8: Commit** (ask for approval first)

```bash
git add src/coscience/wiki_lint.py src/coscience/wiki_store.py src/coscience/wiki.py \
        tests/test_wiki_lint_sources.py
git commit -m "feat(wiki): source, trust and human-notes lint rules plus run_lint"
```

---

### Task 15: `coscience wiki` — the CLI entry point

Phase 1's definition of done: a real program's results and artifacts produce a
valid, linted bundle from the command line, without the dispatcher.

**Files:**
- Modify: `src/coscience/cli.py` (new subparser + dispatch)
- Test: `tests/test_cli_wiki.py`

**Interfaces:**
- Consumes: `wiki.beat`, `wiki_lint.run_lint`, `wiki_lint.render_report`,
  `wiki_lint.counts`, `wiki_agent.WikiAgent`, `wiki_store.load_state`.
- Produces the command:
  ```
  coscience wiki --repo PATH [--program ID] [--once | --lint | --status] [--fix]
  ```
  `--once` runs one beat per program (the default), `--lint` runs the
  deterministic lint and prints the report, `--status` prints one line per
  program. Exit code is 0 unless `--lint` found errors, in which case 1 — so it
  is usable in a pre-commit or CI check on the substrate.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli_wiki.py
from coscience import wiki_okf, wiki_store
from coscience.cli import main
from coscience.models import Program, Result, Sprint, SprintStatus


def _seed(substrate, pid="p1"):
    substrate.save_program(Program(id=pid, title="P", goals="g"))
    substrate.save_sprint(Sprint(id="s1", status=SprintStatus.DONE, goals="g", program=pid))
    substrate.save_result(Result(id="r1", sprint="s1", summary="s", completed_at=1.0))


def test_wiki_once_launches_a_beat(substrate, monkeypatch, capsys):
    _seed(substrate)
    import coscience.cli as cli_mod
    monkeypatch.setattr(cli_mod.wiki, "beat",
                        lambda sub, prog, now, agent, **kw: "wiki: launched ingest r0001")
    assert main(["wiki", "--repo", str(substrate.repo_root), "--once"]) == 0
    assert "launched ingest" in capsys.readouterr().out


def test_wiki_program_filter(substrate, monkeypatch):
    _seed(substrate, "p1")
    substrate.save_program(Program(id="p2", title="P2", goals="g"))
    seen = []
    import coscience.cli as cli_mod
    monkeypatch.setattr(cli_mod.wiki, "beat",
                        lambda sub, prog, now, agent, **kw: (seen.append(prog.id), "")[1])
    main(["wiki", "--repo", str(substrate.repo_root), "--once", "--program", "p2"])
    assert seen == ["p2"]


def test_wiki_lint_prints_the_report_and_exits_zero_when_clean(substrate, capsys):
    _seed(substrate)
    wiki_store.ensure_bundle(substrate, "p1")
    assert main(["wiki", "--repo", str(substrate.repo_root), "--lint"]) == 0
    assert "No findings" in capsys.readouterr().out


def test_wiki_lint_exits_one_on_errors(substrate, capsys):
    _seed(substrate)
    wiki_store.ensure_bundle(substrate, "p1")
    wiki_store.write_page(substrate, "p1", wiki_okf.Page(
        path="concepts/a.md", type="", title="A", body="x" * 400))
    assert main(["wiki", "--repo", str(substrate.repo_root), "--lint"]) == 1
    assert "okf/missing-type" in capsys.readouterr().out


def test_wiki_lint_fix_applies_mechanical_fixes(substrate, capsys):
    _seed(substrate)
    wiki_store.ensure_bundle(substrate, "p1")
    wiki_store.write_page(substrate, "p1", wiki_okf.Page(
        path="concepts/b.md", type="Concept", title="B", body="x" * 400))
    wiki_store.write_page(substrate, "p1", wiki_okf.Page(
        path="concepts/a.md", type="Concept", title="A", body="see [[b]]\n" + "x" * 400))
    main(["wiki", "--repo", str(substrate.repo_root), "--lint", "--fix"])
    text = (wiki_store.bundle_dir(substrate, "p1") / "concepts" / "a.md").read_text()
    assert "[b](/concepts/b.md)" in text
    assert "fixed 1" in capsys.readouterr().out


def test_wiki_status_lists_each_program(substrate, capsys):
    _seed(substrate)
    assert main(["wiki", "--repo", str(substrate.repo_root), "--status"]) == 0
    out = capsys.readouterr().out
    assert "p1" in out
    assert "pending 1" in out
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_cli_wiki.py -v`
Expected: FAIL — `SystemExit: 2` from argparse (`invalid choice: 'wiki'`)

- [ ] **Step 3: Add the subparser**

In `src/coscience/cli.py`, after the `pm` subparser block:

```python
    wk = sub.add_parser("wiki", help="run the program wiki: ingest, lint, status")
    wk.add_argument("--repo", required=True, type=Path)
    wk.add_argument("--program", default="", help="limit to one program id")
    wkmode = wk.add_mutually_exclusive_group()
    wkmode.add_argument("--once", action="store_true", help="run one beat per program")
    wkmode.add_argument("--lint", action="store_true",
                        help="run the deterministic lint and print the report")
    wkmode.add_argument("--status", action="store_true")
    wk.add_argument("--fix", action="store_true",
                    help="with --lint: apply the mechanical fixes")
```

and the imports:

```python
from coscience import artifacts, wiki, wiki_lint, wiki_store
from coscience.wiki_agent import WikiAgent
```

- [ ] **Step 4: Add the command body**

Before the final `parser.error(...)`:

```python
    if args.command == "wiki":
        substrate = Substrate(args.repo)
        programs = [p for p in substrate.iter_programs()
                    if not args.program or p.id == args.program]

        if args.status:
            for program in programs:
                state = wiki_store.load_state(substrate, program.id)
                pending = wiki_store.pending_objects(
                    substrate, program.id, state.get("ingested") or {},
                    set(state.get("quarantined") or []))
                run = state.get("run") or {}
                print(f"{program.id}: pending {len(pending)} · "
                      f"ingested {len(state.get('ingested') or {})} · "
                      f"since lint {state.get('ingests_since_lint', 0)} · "
                      f"quarantined {len(state.get('quarantined') or [])} · "
                      f"{'running ' + run.get('kind', '') if run else 'idle'}", flush=True)
            return 0

        if args.lint:
            worst = 0
            for program in programs:
                findings, fixed = wiki_lint.run_lint(substrate, program.id, fix=args.fix)
                print(f"## {program.id} — fixed {fixed}", flush=True)
                print(wiki_lint.render_report(findings), flush=True)
                worst = max(worst, wiki_lint.counts(findings)["error"])
            if args.fix:
                substrate.commit("wiki lint: mechanical fixes")
            return 1 if worst else 0

        agent = WikiAgent()
        now = time.time()
        for program in programs:
            line = wiki.beat(substrate, program, now, agent)
            if line:
                print(f"{program.id}: {line}", flush=True)
        return 0
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `python -m pytest tests/test_cli_wiki.py -v`
Expected: PASS (6 tests)

- [ ] **Step 6: Run the whole suite**

Run: `python -m pytest`
Expected: PASS

- [ ] **Step 7: Manual end-to-end check against a real substrate**

This is the phase's definition of done and cannot be asserted in a unit test —
it is the first time a live Claude run touches the bundle. Read
`local_setup_<hostname>.md` for the substrate path first, then, with `COSCIENCE_REPO`
pointing at a **scratch copy** of the substrate (never the live one):

```bash
git -C "$COSCIENCE_REPO" status --porcelain     # must be clean before you start
coscience wiki --repo "$COSCIENCE_REPO" --status
coscience wiki --repo "$COSCIENCE_REPO" --program <pid> --once
# wait for the run, then:
coscience wiki --repo "$COSCIENCE_REPO" --program <pid> --once   # collects
coscience wiki --repo "$COSCIENCE_REPO" --program <pid> --lint
git -C "$COSCIENCE_REPO" log --oneline -5
git -C "$COSCIENCE_REPO" show --stat HEAD       # every path under programs/<pid>/
```

Verify by hand: the bundle has `sources/`, `concepts/` and a populated
`index.md`; `log.md` gained a line; no file outside `programs/<pid>/` changed;
`.wiki/state.json` lists the batch under `ingested`. Then open
`programs/<pid>/wiki` as an Obsidian vault and confirm the links resolve.

- [ ] **Step 8: Update the charter's status table**

In `docs/knowledge-charter.md` §2, mark phase 1 done and note anything the
end-to-end run revealed (prompt weaknesses, batch size, model choice). The next
instance reads that table first.

- [ ] **Step 9: Commit** (ask for approval first)

```bash
git add src/coscience/cli.py tests/test_cli_wiki.py docs/knowledge-charter.md
git commit -m "feat(wiki): coscience wiki CLI — once, lint, status"
```

---

## Done when

- `python -m pytest` passes with the ~90 new tests.
- `coscience wiki --repo <substrate> --status` lists every active program's
  pending count.
- One `--once` beat on a real program produces a bundle that opens as an Obsidian
  vault, and `--lint` reports zero errors on it.
- Nothing outside `programs/<pid>/wiki/` and `programs/<pid>/.wiki/` was written
  by the run.
- `docs/knowledge-charter.md` §2 shows phase 1 done and phase 2 next.

## Not in this phase

Browse UI, endpoints, service methods, and the `wiki_model` / `wiki_enabled`
controls in `ProgramSettingsModal` (phase 2); agent lint runs on the dispatch
cadence and quarantine retry from the UI (phase 3); `wiki_graph`, `graph.json`,
`d3-force` (phase 4); wiki chat, research runs, MCP tools (phase 5); external
literature ingestion (deferred, see spec §14).
