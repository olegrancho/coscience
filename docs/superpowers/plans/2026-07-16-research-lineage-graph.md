# Research Lineage Graph Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give a program's ideas and experiments (sprints) a persistent typed-edge lineage graph that the PM agent produces and consumes, and that survives promote/demote.

**Architecture:** A new pure-logic module `src/coscience/graph.py` owns the edge vocabulary, node-stage rules, validation, cycle detection, the reverse index, and edge rewiring. Edges are stored outbound on each node's frontmatter (`Idea.edges`, `Sprint.edges`). All writes stay in `pm_agent.py`/`service.py` (the reasoner-seam rule). This plan is backend only — it ships a graph usable by the PM before any UI exists.

**Tech Stack:** Python 3 (dataclasses, stdlib `hashlib`), pytest, FastAPI (existing HTTP layer). No new dependencies.

**Scope:** Spec sections 2–5, 7 and phases 1–4 of §8. Out of this plan (each its own follow-up plan): the frontend graph view (spec §6 / phase 5) and enabling the Extended edge tier (phase 6). Both are named in the enum here but Extended is disabled at runtime.

**Spec:** `docs/superpowers/specs/2026-07-16-research-lineage-graph-design.md`

## Global Constraints

- **Edge types are a frozen enum.** Only the Core 5 are runtime-enabled: `inspired_by`, `builds_on`, `supersedes`, `confirms`, `refutes`. Extended (`refines`, `follows`, `replicates`, `duplicate_of`, `contradicts`) are defined but NOT in `ENABLED_TYPES`.
- **Edges stored in ONE canonical direction**, outbound on the source node. Never store the inverse.
- **Every edge is a dict:** `{id, type, src, dst, source, by, at, rationale, confidence, evidence}`. `source ∈ {system, pm, human}`.
- **Rationale required** on every asserted (`pm`/`human`) `add`; `confidence ∈ {low, med, high}` required on evidential edges (`confirms`/`refutes`/`contradicts`).
- **Evidential edges require both endpoints at result stage** (a Sprint with `status == done`).
- **Lineage edges must stay a DAG** — reject an add that closes a cycle. Evidential edges may cycle.
- **Pinning:** the PM may only delete edges whose `source == "pm"`; `human`/`system` edges are immune to PM deletion.
- **Result = done experiment.** No separate Result node type; node stage is derived from `Sprint.status`.
- **Runtime is Linux-only** (`/proc`, `fcntl`); tests run on the Linux host via `~/venvs/coscience-dev/bin/python -m pytest` (or prod venv). Ignore the optional-`mcp` test files (`test_mcp_*`, `test_transport_programs`).
- **Back-compat:** every new frontmatter key is read with `.get(...)` and written only when non-empty, so existing substrate files load unchanged.
- Never commit or push to git without explicit user approval (project rule). The `git commit` steps below stage + commit locally only.

---

### Task 1: Edge vocabulary, node stage/kind, edge minting

**Files:**
- Create: `src/coscience/graph.py`
- Test: `tests/test_graph_core.py`

**Interfaces:**
- Consumes: `coscience.models.Idea`, `coscience.models.Sprint`, `coscience.models.SprintStatus`.
- Produces:
  - Constants `LINEAGE`, `EVIDENTIAL`, `IDEA`, `EXPERIMENT`, `RESULT` (all str).
  - `EDGE_SPEC: dict[str, dict]`, `CORE_TYPES: set[str]`, `EXTENDED_TYPES: set[str]`, `ENABLED_TYPES: set[str]`.
  - `node_kind(node) -> str` → `IDEA` | `EXPERIMENT`.
  - `node_stage(node) -> str` → `IDEA` | `EXPERIMENT` | `RESULT`.
  - `edge_id(etype: str, src: str, dst: str) -> str` (12-hex, deterministic).
  - `new_edge(etype, src, dst, source, by="", at=0.0, rationale="", confidence="", evidence="") -> dict`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_graph_core.py
from coscience import graph
from coscience.models import Idea, Sprint, SprintStatus


def test_core_types_enabled_extended_not():
    assert graph.CORE_TYPES == {"inspired_by", "builds_on", "supersedes", "confirms", "refutes"}
    assert "contradicts" in graph.EXTENDED_TYPES
    assert graph.ENABLED_TYPES == graph.CORE_TYPES          # extended defined but off
    assert graph.EXTENDED_TYPES & graph.ENABLED_TYPES == set()


def test_node_stage_and_kind():
    idea = Idea(id="i1", text="x")
    running = Sprint(id="s1", status=SprintStatus.EXECUTING, goals="g")
    done = Sprint(id="s2", status=SprintStatus.DONE, goals="g")
    assert graph.node_kind(idea) == graph.IDEA
    assert graph.node_kind(done) == graph.EXPERIMENT
    assert graph.node_stage(idea) == graph.IDEA
    assert graph.node_stage(running) == graph.EXPERIMENT
    assert graph.node_stage(done) == graph.RESULT          # done experiment == result


def test_new_edge_is_deterministic_and_shaped():
    e1 = graph.new_edge("builds_on", "s2", "s1", "pm", by="pm", at=5.0, rationale="uses method")
    e2 = graph.new_edge("builds_on", "s2", "s1", "human")
    assert e1["id"] == e2["id"]                            # id depends only on (type, src, dst)
    assert e1["id"] == graph.edge_id("builds_on", "s2", "s1")
    assert set(e1) == {"id", "type", "src", "dst", "source", "by", "at",
                       "rationale", "confidence", "evidence"}
    assert (e1["type"], e1["src"], e1["dst"], e1["source"]) == ("builds_on", "s2", "s1", "pm")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/venvs/coscience-dev/bin/python -m pytest tests/test_graph_core.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'coscience.graph'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/coscience/graph.py
"""Research lineage graph: typed edges between idea / experiment / result nodes.

Pure logic — no IO. Each edge is stored on its SOURCE node (outbound). This
module owns the vocabulary, node-stage rules, edge minting, the reverse index,
validation, cycle detection, and rewiring. Every substrate write happens in
pm_agent.py / service.py, keeping writes out of the reasoner (the seam rule)."""
from __future__ import annotations

import hashlib

from coscience.models import Idea, Sprint, SprintStatus

# families
LINEAGE = "lineage"
EVIDENTIAL = "evidential"

# node stages / kinds
IDEA = "idea"
EXPERIMENT = "experiment"
RESULT = "result"

# The frozen edge vocabulary. `src`/`dst` are kind slots: "idea", "experiment",
# or "any". `same_kind` forces src and dst to the same kind. `require_done` gates
# evidential edges to result-stage (done) endpoints.
EDGE_SPEC: dict[str, dict] = {
    "inspired_by":  {"family": LINEAGE,    "tier": "core",     "src": "any",       "dst": "any"},
    "builds_on":    {"family": LINEAGE,    "tier": "core",     "src": EXPERIMENT,  "dst": EXPERIMENT},
    "supersedes":   {"family": LINEAGE,    "tier": "core",     "src": EXPERIMENT,  "dst": EXPERIMENT},
    "confirms":     {"family": EVIDENTIAL, "tier": "core",     "src": EXPERIMENT,  "dst": EXPERIMENT, "require_done": True},
    "refutes":      {"family": EVIDENTIAL, "tier": "core",     "src": EXPERIMENT,  "dst": EXPERIMENT, "require_done": True},
    "refines":      {"family": LINEAGE,    "tier": "extended", "src": IDEA,        "dst": IDEA},
    "follows":      {"family": LINEAGE,    "tier": "extended", "src": "any",       "dst": "any", "same_kind": True},
    "replicates":   {"family": LINEAGE,    "tier": "extended", "src": EXPERIMENT,  "dst": EXPERIMENT},
    "duplicate_of": {"family": LINEAGE,    "tier": "extended", "src": "any",       "dst": "any", "same_kind": True},
    "contradicts":  {"family": EVIDENTIAL, "tier": "extended", "src": EXPERIMENT,  "dst": EXPERIMENT, "require_done": True},
}

CORE_TYPES = {t for t, s in EDGE_SPEC.items() if s["tier"] == "core"}
EXTENDED_TYPES = {t for t, s in EDGE_SPEC.items() if s["tier"] == "extended"}
# Ship Core first. Enabling Extended later is a one-line change: ENABLED_TYPES |= EXTENDED_TYPES
ENABLED_TYPES = set(CORE_TYPES)


def node_kind(node) -> str:
    return IDEA if isinstance(node, Idea) else EXPERIMENT


def node_stage(node) -> str:
    if isinstance(node, Idea):
        return IDEA
    return RESULT if node.status == SprintStatus.DONE else EXPERIMENT


def edge_id(etype: str, src: str, dst: str) -> str:
    return hashlib.sha1(f"{etype}|{src}|{dst}".encode("utf-8")).hexdigest()[:12]


def new_edge(etype: str, src: str, dst: str, source: str, by: str = "",
             at: float = 0.0, rationale: str = "", confidence: str = "",
             evidence: str = "") -> dict:
    return {"id": edge_id(etype, src, dst), "type": etype, "src": src, "dst": dst,
            "source": source, "by": by, "at": at, "rationale": rationale,
            "confidence": confidence, "evidence": evidence}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `~/venvs/coscience-dev/bin/python -m pytest tests/test_graph_core.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/coscience/graph.py tests/test_graph_core.py
git commit -m "feat(graph): edge vocabulary, node stage/kind, edge minting"
```

---

### Task 2: Reverse index, edge collection, and edge rewiring

**Files:**
- Modify: `src/coscience/graph.py`
- Test: `tests/test_graph_structure.py`

**Interfaces:**
- Consumes: Task 1 (`edge_id`, `EDGE_SPEC`, `EVIDENTIAL`, `new_edge`), node objects exposing `.id` and `.edges`.
- Produces:
  - `all_edges(nodes) -> list[dict]` — every outbound edge across nodes.
  - `build_reverse_index(edges) -> dict[str, list[dict]]` — dst → incoming edges.
  - `repoint_edges(old_id, new_id, nodes) -> set[str]` — move `old_id`'s outbound edges onto `new_id` and rewrite any edge pointing at `old_id` to point at `new_id`; returns the set of node ids whose `.edges` changed (never includes `old_id`).
  - `drop_evidential_incident(node_id, nodes) -> set[str]` — remove every evidential edge with `src` or `dst == node_id`; returns changed node ids.

**Note on `Idea`/`Sprint` `.edges`:** Task 4 adds the `edges` field. To keep Task 2 self-contained, its tests use tiny stand-in node objects with `.id` and `.edges` — the functions only touch those two attributes.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_graph_structure.py
from dataclasses import dataclass, field
from coscience import graph


@dataclass
class _N:                       # minimal node stand-in: id + outbound edge list
    id: str
    edges: list = field(default_factory=list)


def test_all_edges_and_reverse_index():
    a = _N("a", [graph.new_edge("inspired_by", "a", "b", "pm")])
    b = _N("b", [graph.new_edge("builds_on", "b", "c", "pm")])
    c = _N("c")
    idx = graph.build_reverse_index(graph.all_edges([a, b, c]))
    assert [e["src"] for e in idx["b"]] == ["a"]      # a -> b is incoming to b
    assert [e["src"] for e in idx["c"]] == ["b"]
    assert "a" not in idx                              # nothing points at a


def test_repoint_moves_outbound_and_rewrites_inbound():
    # B inspired_by A (inbound to A); A inspired_by X (outbound from A).
    a = _N("a", [graph.new_edge("inspired_by", "a", "x", "pm")])
    b = _N("b", [graph.new_edge("inspired_by", "b", "a", "pm")])
    s = _N("SA")                                       # the new sprint node
    x = _N("x")
    changed = graph.repoint_edges("a", "SA", [a, b, s, x])
    assert a.edges == []                               # drained
    assert [(e["src"], e["dst"]) for e in s.edges] == [("SA", "x")]   # outbound moved
    assert [(e["src"], e["dst"]) for e in b.edges] == [("b", "SA")]   # inbound rewritten
    assert changed == {"SA", "b"}
    assert s.edges[0]["id"] == graph.edge_id("inspired_by", "SA", "x")  # id refreshed


def test_drop_evidential_incident():
    done = _N("s1", [graph.new_edge("refutes", "s1", "s2", "pm", confidence="high")])
    lineage = _N("s3", [graph.new_edge("builds_on", "s3", "s2", "pm")])
    changed = graph.drop_evidential_incident("s2", [done, lineage])
    assert done.edges == []                            # evidential refutes -> s2 dropped
    assert len(lineage.edges) == 1                     # lineage builds_on -> s2 kept
    assert changed == {"s1"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/venvs/coscience-dev/bin/python -m pytest tests/test_graph_structure.py -v`
Expected: FAIL with `AttributeError: module 'coscience.graph' has no attribute 'all_edges'`.

- [ ] **Step 3: Write minimal implementation**

Append to `src/coscience/graph.py`:

```python
def all_edges(nodes) -> list[dict]:
    out: list[dict] = []
    for n in nodes:
        out.extend(n.edges)
    return out


def build_reverse_index(edges) -> dict[str, list[dict]]:
    idx: dict[str, list[dict]] = {}
    for e in edges:
        idx.setdefault(e["dst"], []).append(e)
    return idx


def repoint_edges(old_id: str, new_id: str, nodes) -> set[str]:
    """Move old_id's outbound edges onto new_id and rewrite every edge that points
    AT old_id to point at new_id. Both `old` and `new` must be present in `nodes`
    for the outbound move; inbound rewrites scan all nodes. Edge ids are refreshed
    (they are a hash of type|src|dst). Returns changed node ids (excludes old_id)."""
    by_id = {n.id: n for n in nodes}
    changed: set[str] = set()
    old = by_id.get(old_id)
    new = by_id.get(new_id)
    if old is not None and new is not None and old.edges:
        for e in old.edges:
            e["src"] = new_id
            e["id"] = edge_id(e["type"], e["src"], e["dst"])
            new.edges.append(e)
        old.edges = []
        changed.add(new_id)
    for n in nodes:
        for e in n.edges:
            if e["dst"] == old_id:
                e["dst"] = new_id
                e["id"] = edge_id(e["type"], e["src"], e["dst"])
                changed.add(n.id)
    return changed


def drop_evidential_incident(node_id: str, nodes) -> set[str]:
    """Remove every evidential edge touching node_id (either direction). Used on
    demote: the node becomes an idea, which has no result to confirm/refute."""
    changed: set[str] = set()
    for n in nodes:
        kept = [e for e in n.edges
                if not (EDGE_SPEC[e["type"]]["family"] == EVIDENTIAL
                        and (e["src"] == node_id or e["dst"] == node_id))]
        if len(kept) != len(n.edges):
            n.edges = kept
            changed.add(n.id)
    return changed
```

- [ ] **Step 4: Run test to verify it passes**

Run: `~/venvs/coscience-dev/bin/python -m pytest tests/test_graph_structure.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/coscience/graph.py tests/test_graph_structure.py
git commit -m "feat(graph): reverse index, edge collection, rewiring helpers"
```

---

### Task 3: Edge validation and cycle detection

**Files:**
- Modify: `src/coscience/graph.py`
- Test: `tests/test_graph_validate.py`

**Interfaces:**
- Consumes: Task 1 (`EDGE_SPEC`, `ENABLED_TYPES`, `node_kind`, `node_stage`, families, `RESULT`), Task 2 (nothing directly).
- Produces:
  - `would_create_cycle(src, dst, lineage_edges) -> bool` — true if adding `src→dst` closes a cycle among the given lineage edges.
  - `validate_edge(edge, nodes, existing_edges, enabled=None) -> str | None` — returns a reason string when invalid, else `None`. `enabled` defaults to `ENABLED_TYPES`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_graph_validate.py
from coscience import graph
from coscience.models import Idea, Sprint, SprintStatus


def _nodes():
    return [
        Idea(id="i1", text="a"),
        Idea(id="i2", text="b"),
        Sprint(id="s1", status=SprintStatus.EXECUTING, goals="g"),
        Sprint(id="s2", status=SprintStatus.DONE, goals="g"),
        Sprint(id="s3", status=SprintStatus.DONE, goals="g"),
    ]


def test_valid_core_edges():
    ns = _nodes()
    assert graph.validate_edge(graph.new_edge("inspired_by", "i2", "i1", "pm", rationale="r"), ns, []) is None
    assert graph.validate_edge(graph.new_edge("builds_on", "s2", "s1", "pm", rationale="r"), ns, []) is None
    ev = graph.new_edge("confirms", "s3", "s2", "pm", rationale="r", confidence="high")
    assert graph.validate_edge(ev, ns, []) is None


def test_rejects_disabled_type():
    ns = _nodes()
    e = graph.new_edge("refines", "i2", "i1", "pm", rationale="r")   # extended, off
    assert graph.validate_edge(e, ns, []) == "type not enabled: refines"


def test_rejects_missing_endpoint_and_self_edge():
    ns = _nodes()
    assert "endpoint" in graph.validate_edge(graph.new_edge("builds_on", "s2", "nope", "pm", rationale="r"), ns, [])
    assert graph.validate_edge(graph.new_edge("builds_on", "s2", "s2", "pm", rationale="r"), ns, []) == "self-edge"


def test_rejects_illegal_kind_pair():
    ns = _nodes()
    # builds_on is experiment->experiment; an idea source is illegal
    e = graph.new_edge("builds_on", "i1", "s1", "pm", rationale="r")
    assert graph.validate_edge(e, ns, []) == "illegal kind pair"


def test_evidential_requires_done_endpoints_and_confidence():
    ns = _nodes()
    # s1 is EXECUTING (not a result) -> refutes not allowed
    e = graph.new_edge("refutes", "s2", "s1", "pm", rationale="r", confidence="high")
    assert "done" in graph.validate_edge(e, ns, [])
    # both done but no confidence
    e2 = graph.new_edge("confirms", "s3", "s2", "pm", rationale="r")
    assert "confidence" in graph.validate_edge(e2, ns, [])


def test_rejects_lineage_cycle():
    ns = _nodes()
    existing = [graph.new_edge("builds_on", "s1", "s2", "pm")]   # s1 -> s2
    # adding s2 -> s1 would close a cycle
    e = graph.new_edge("builds_on", "s2", "s1", "pm", rationale="r")
    assert graph.validate_edge(e, ns, existing) == "would create cycle"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/venvs/coscience-dev/bin/python -m pytest tests/test_graph_validate.py -v`
Expected: FAIL with `AttributeError: module 'coscience.graph' has no attribute 'validate_edge'`.

- [ ] **Step 3: Write minimal implementation**

Append to `src/coscience/graph.py`:

```python
def _kind_ok(kind: str, slot: str) -> bool:
    return slot == "any" or kind == slot


def would_create_cycle(src: str, dst: str, lineage_edges) -> bool:
    """Adding src->dst closes a cycle iff dst can already reach src via existing
    lineage edges (each edge treated as directed src->dst)."""
    adj: dict[str, set[str]] = {}
    for e in lineage_edges:
        adj.setdefault(e["src"], set()).add(e["dst"])
    seen: set[str] = set()
    stack = [dst]
    while stack:
        u = stack.pop()
        if u == src:
            return True
        if u in seen:
            continue
        seen.add(u)
        stack.extend(adj.get(u, ()))
    return False


def validate_edge(edge, nodes, existing_edges, enabled=None) -> str | None:
    """Return a human-readable reason the edge is invalid, or None if it is valid.
    `nodes` are the live node objects; `existing_edges` is the current edge set
    (used for the DAG check on lineage edges)."""
    enabled = ENABLED_TYPES if enabled is None else enabled
    et = edge["type"]
    if et not in enabled:
        return f"type not enabled: {et}"
    src, dst = edge["src"], edge["dst"]
    if src == dst:
        return "self-edge"
    by_id = {n.id: n for n in nodes}
    if src not in by_id or dst not in by_id:
        return "endpoint missing"
    spec = EDGE_SPEC[et]
    ks, kd = node_kind(by_id[src]), node_kind(by_id[dst])
    if not _kind_ok(ks, spec["src"]) or not _kind_ok(kd, spec["dst"]):
        return "illegal kind pair"
    if spec.get("same_kind") and ks != kd:
        return "must be same kind"
    if spec.get("require_done"):
        if node_stage(by_id[src]) != RESULT or node_stage(by_id[dst]) != RESULT:
            return "evidential edge requires done endpoints"
        if edge.get("confidence") not in ("low", "med", "high"):
            return "evidential edge requires confidence"
    if spec["family"] == LINEAGE:
        lin = [e for e in existing_edges if EDGE_SPEC[e["type"]]["family"] == LINEAGE]
        if would_create_cycle(src, dst, lin):
            return "would create cycle"
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `~/venvs/coscience-dev/bin/python -m pytest tests/test_graph_validate.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/coscience/graph.py tests/test_graph_validate.py
git commit -m "feat(graph): edge validation and DAG cycle detection"
```

---

### Task 4: Persist `edges` on Idea and Sprint

**Files:**
- Modify: `src/coscience/models.py:42-61` (Sprint), `src/coscience/models.py:123-135` (Idea)
- Modify: `src/coscience/substrate.py:34-59` (load_sprint), `61-108` (save_sprint), `369-391` (load_ideas), `393-407` (save_ideas)
- Test: `tests/test_graph_persistence.py`

**Interfaces:**
- Consumes: Task 1 (`graph.new_edge` in tests).
- Produces: `Idea.edges: list[dict]` and `Sprint.edges: list[dict]`, round-tripped through the substrate. Missing frontmatter key loads as `[]`; empty list is not written.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_graph_persistence.py
from coscience import graph
from coscience.models import Idea, Sprint, SprintStatus, Program, ProgramStatus
from coscience.substrate import Substrate


def _sub(tmp_path):
    return Substrate(tmp_path)


def test_sprint_edges_roundtrip(tmp_path):
    sub = _sub(tmp_path)
    s = Sprint(id="s1", status=SprintStatus.DONE, goals="g", program="p1",
               edges=[graph.new_edge("builds_on", "s1", "s0", "pm", rationale="uses it")])
    sub.save_sprint(s)
    loaded = sub.load_sprint("s1")
    assert loaded.edges == s.edges


def test_sprint_without_edges_loads_empty(tmp_path):
    sub = _sub(tmp_path)
    sub.save_sprint(Sprint(id="s2", status=SprintStatus.PROPOSED, goals="g"))
    text = (sub.sprint_dir("s2") / "sprint.md").read_text()
    assert "edges" not in text                      # empty list not written
    assert sub.load_sprint("s2").edges == []


def test_idea_edges_roundtrip(tmp_path):
    sub = _sub(tmp_path)
    sub.save_program(Program(id="p1", title="P", goals="g", status=ProgramStatus.ACTIVE))
    ideas = [Idea(id="i1", text="a",
                  edges=[graph.new_edge("inspired_by", "i1", "i0", "human", by="u", rationale="r")]),
             Idea(id="i2", text="b")]
    sub.save_ideas("p1", "sum", ideas)
    _summary, loaded = sub.load_ideas("p1")
    assert loaded[0].edges == ideas[0].edges
    assert loaded[1].edges == []                    # no edges -> empty
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/venvs/coscience-dev/bin/python -m pytest tests/test_graph_persistence.py -v`
Expected: FAIL with `TypeError: __init__() got an unexpected keyword argument 'edges'`.

- [ ] **Step 3: Write minimal implementation**

In `src/coscience/models.py`, add to the `Sprint` dataclass (after the `status_history` field at line 61):

```python
    edges: list[dict] = field(default_factory=list)  # outbound lineage/evidential edges (see coscience.graph)
```

Add to the `Idea` dataclass (after the `demoted` field at line 135):

```python
    edges: list[dict] = field(default_factory=list)  # outbound lineage edges (see coscience.graph)
```

In `src/coscience/substrate.py`, in `load_sprint` add `edges` to the `Sprint(...)` constructor (after `status_history=...`, before the closing paren at line 58):

```python
            edges=list(fm.get("edges", [])),
```

In `save_sprint`, after the `status_history` block (line 105-106) and before `d.mkdir(...)`:

```python
        if sprint.edges:
            fm["edges"] = list(sprint.edges)
```

In `load_ideas`, add `edges` to the `Idea(...)` constructor (after `demoted=...` at line 389):

```python
                edges=list(n.get("edges", [])),
```

In `save_ideas`, extend each idea dict (line 400-403) to include edges when present:

```python
                {"id": i.id, "text": i.text, "source": i.source, "pinned": i.pinned,
                 "by": i.by,
                 "threads": list(i.threads), "created_at": i.created_at,
                 **({"demoted": True} if i.demoted else {}),
                 **({"edges": list(i.edges)} if i.edges else {})}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `~/venvs/coscience-dev/bin/python -m pytest tests/test_graph_persistence.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Run the existing substrate + model suites to confirm no regression**

Run: `~/venvs/coscience-dev/bin/python -m pytest tests/test_service_sprints.py tests/test_pm_ideas.py -q`
Expected: PASS (all existing tests green — new field defaults to empty, nothing else changes).

- [ ] **Step 6: Commit**

```bash
git add src/coscience/models.py src/coscience/substrate.py tests/test_graph_persistence.py
git commit -m "feat(graph): persist edges on Idea and Sprint frontmatter"
```

---

### Task 5: Rewire edges on promotion

**Files:**
- Modify: `src/coscience/pm_agent.py:372-374` (the promotion `pop`) and add a helper near it
- Test: `tests/test_graph_promote.py`

**Interfaces:**
- Consumes: Task 2 (`graph.repoint_edges`), Task 4 (`Idea.edges`, `Sprint.edges`).
- Produces: `_rewire_on_promote(substrate, program_id, old_idea_id, new_sid, ideas_by_id) -> None` in `pm_agent.py`. On promotion, the idea's edges (both directions) move onto the new sprint before the idea is dropped from the pool.

**Context:** In `_run_pm_cycle`, promotion currently is (lines 372-374):
```python
        # A promotion: the originating idea has become a sprint -> drop it from the pool.
        if prop.from_idea:
            ideas_by_id.pop(prop.from_idea, None)
```
The idea's edges must be rewired onto the sprint first. Inbound edges may live on other ideas (in `ideas_by_id`) OR on other sprints (on disk), so the helper loads the program's sprints, includes the freshly-saved sprint, repoints across all of them, and saves the ones that changed. The pool ideas are saved later at line 408.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_graph_promote.py
from coscience import graph
from coscience.models import Idea, Program, ProgramStatus, Sprint, SprintStatus
from coscience.pm_agent import pm_beat
from coscience.pm_reasoner import FakeReasoner, PMCycleOutput, ProposedSprint
from coscience.service import Service


def _svc(tmp_path):
    svc = Service(tmp_path)
    svc.substrate.save_program(Program(id="p1", title="P", goals="g", status=ProgramStatus.ACTIVE))
    return svc


def test_promotion_transfers_idea_edges_onto_sprint(tmp_path):
    svc = _svc(tmp_path)
    # idea A (to be promoted) has an outbound edge to idea X; idea B points AT A.
    ideas = [
        Idea(id="A", text="promote me", source="pm",
             edges=[graph.new_edge("inspired_by", "A", "X", "pm", rationale="r")]),
        Idea(id="B", text="depends on A", source="pm",
             edges=[graph.new_edge("inspired_by", "B", "A", "pm", rationale="r")]),
        Idea(id="X", text="root", source="pm"),
    ]
    svc.substrate.save_ideas("p1", "seed", ideas)

    out = PMCycleOutput(proposals=[ProposedSprint(suffix="go", goals="do it",
                                                  plan=["x"], from_idea="A")])
    pm_beat(svc.substrate, "p1", FakeReasoner([out]), force=True)

    sid = "p1-c0-go"
    sprint = svc.substrate.load_sprint(sid)
    # A's outbound edge now belongs to the sprint, repointed as its source.
    assert [(e["src"], e["dst"]) for e in sprint.edges] == [(sid, "X")]

    _summary, ideas_after = svc.substrate.load_ideas("p1")
    by_id = {i.id: i for i in ideas_after}
    assert "A" not in by_id                                   # idea consumed
    # B's inbound edge to A now points at the sprint.
    assert [(e["src"], e["dst"]) for e in by_id["B"].edges] == [("B", sid)]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/venvs/coscience-dev/bin/python -m pytest tests/test_graph_promote.py -v`
Expected: FAIL — `sprint.edges == []` (edges not transferred), and `B`'s edge still points at `A`.

- [ ] **Step 3: Write minimal implementation**

In `src/coscience/pm_agent.py`, add the helper (place it just above `_run_pm_cycle`, after `pm_beat`/lock helpers). Ensure `from coscience import graph` is imported at the top of the module (add it alongside the other `from coscience import ...` imports if absent):

```python
def _rewire_on_promote(substrate, program_id: str, old_idea_id: str,
                       new_sid: str, ideas_by_id: dict) -> None:
    """Move the promoted idea's edges (both directions) onto the new sprint.
    Inbound edges may live on other ideas or other sprints, so scan the whole
    program node set. The new sprint is saved on disk already; save every node
    the rewire touched. Pool ideas are saved by the caller."""
    program_sprints = [s for s in substrate.iter_sprints() if s.program == program_id]
    nodes = list(ideas_by_id.values()) + program_sprints
    changed = graph.repoint_edges(old_idea_id, new_sid, nodes)
    sprint_by_id = {s.id: s for s in program_sprints}
    for nid in changed:
        if nid in sprint_by_id:
            substrate.save_sprint(sprint_by_id[nid])
```

Then change the promotion block (lines 372-374) to rewire before dropping:

```python
        # A promotion: the originating idea has become a sprint. Move its edges
        # onto the sprint (both directions), then drop it from the pool.
        if prop.from_idea:
            if prop.from_idea in ideas_by_id:
                _rewire_on_promote(substrate, program_id, prop.from_idea, sid, ideas_by_id)
            ideas_by_id.pop(prop.from_idea, None)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `~/venvs/coscience-dev/bin/python -m pytest tests/test_graph_promote.py -v`
Expected: PASS.

- [ ] **Step 5: Run the promotion regression suite**

Run: `~/venvs/coscience-dev/bin/python -m pytest tests/test_pm_ideas.py -q`
Expected: PASS (existing promotion tests unaffected — ideas with no edges rewire to a no-op).

- [ ] **Step 6: Commit**

```bash
git add src/coscience/pm_agent.py tests/test_graph_promote.py
git commit -m "feat(graph): transfer idea edges onto the sprint on promotion"
```

---

### Task 6: Rewire edges on demotion

**Files:**
- Modify: `src/coscience/service.py:719-738` (`demote_sprint`)
- Test: `tests/test_graph_demote.py`

**Interfaces:**
- Consumes: Task 2 (`graph.repoint_edges`, `graph.drop_evidential_incident`), Task 4 (`Sprint.edges`, `Idea.edges`).
- Produces: `demote_sprint` repoints the sprint's surviving edges onto the freshly-minted idea and drops evidential edges incident on the sprint (an idea has no result to confirm/refute).

**Context:** current `demote_sprint` (service.py:729-736) loads ideas, appends the new idea, saves ideas, cancels the sprint. Insert the rewire between appending the idea and the final saves: drop evidential edges incident on the sprint, then repoint across all program ideas + sprints, saving changed sprints.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_graph_demote.py
from coscience import graph
from coscience.models import Idea, Program, ProgramStatus, Sprint, SprintStatus
from coscience.service import Service


def _svc(tmp_path):
    svc = Service(tmp_path)
    svc.substrate.save_program(Program(id="p1", title="P", goals="g", status=ProgramStatus.ACTIVE))
    return svc


def test_demote_transfers_lineage_and_drops_evidential(tmp_path):
    svc = _svc(tmp_path)
    # SA (to demote) has a lineage edge to SB. SC (done) refutes SA (inbound evidential).
    sa = Sprint(id="SA", status=SprintStatus.PROPOSED, goals="doomed", program="p1",
                edges=[graph.new_edge("builds_on", "SA", "SB", "pm", rationale="r")])
    sb = Sprint(id="SB", status=SprintStatus.DONE, goals="base", program="p1")
    sc = Sprint(id="SC", status=SprintStatus.DONE, goals="counter", program="p1",
                edges=[graph.new_edge("refutes", "SC", "SA", "human", by="u",
                                      rationale="r", confidence="high")])
    for s in (sa, sb, sc):
        svc.substrate.save_sprint(s)

    result = svc.demote_sprint("SA", by="u")
    new_idea_id = result["idea"]["id"]

    _summary, ideas = svc.substrate.load_ideas("p1")
    idea = next(i for i in ideas if i.id == new_idea_id)
    # SA's outbound lineage edge moved onto the idea (repointed source).
    assert [(e["type"], e["src"], e["dst"]) for e in idea.edges] == [("builds_on", new_idea_id, "SB")]
    # The inbound evidential refutes edge on SC was dropped (idea has no result).
    assert svc.substrate.load_sprint("SC").edges == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/venvs/coscience-dev/bin/python -m pytest tests/test_graph_demote.py -v`
Expected: FAIL — idea has no edges and SC still holds the `refutes` edge.

- [ ] **Step 3: Write minimal implementation**

Ensure `from coscience import graph` is imported at the top of `src/coscience/service.py`. Then rewrite the body of `demote_sprint` (service.py:729-736) so the rewire happens after the idea is appended and before the commit:

```python
        summary, ideas = self.substrate.load_ideas(sprint.program)
        text = (sprint.title or sprint.goals or sprint.id).strip()
        idea = Idea(id=uuid4().hex[:8], text=text, source="human",
                    demoted=True, pinned=True, created_at=time.time())   # demote auto-pins
        ideas.append(idea)
        # Rewire the sprint's graph edges onto the new idea. Drop evidential edges
        # incident on the sprint first (an idea has no result to confirm/refute),
        # then repoint the rest across every program idea + sprint.
        program_sprints = [s for s in self.substrate.iter_sprints() if s.program == sprint.program]
        nodes = list(ideas) + program_sprints
        changed = graph.drop_evidential_incident(sprint.id, nodes)
        changed |= graph.repoint_edges(sprint.id, idea.id, nodes)
        sprint_by_id = {s.id: s for s in program_sprints}
        for nid in changed:
            if nid in sprint_by_id and nid != sprint.id:
                self.substrate.save_sprint(sprint_by_id[nid])
        self.substrate.save_ideas(sprint.program, summary, ideas)
        set_status(sprint, SprintStatus.CANCELED, by=by, action="demote")
        self.substrate.save_sprint(sprint)
        self.substrate.commit(f"sprint {sprint_id} demoted to idea {idea.id}")
        return {"sprint_id": sprint_id, "idea": self._idea_public(idea)}
```

Note: `program_sprints` is loaded from disk and therefore does NOT include the freshly-minted `idea` object; `nodes` combines the in-memory `ideas` list (which holds the new idea) with the on-disk sprints, so `repoint_edges` can find both endpoints. The demoted sprint itself is saved last with its own (now drained) edge list.

- [ ] **Step 4: Run test to verify it passes**

Run: `~/venvs/coscience-dev/bin/python -m pytest tests/test_graph_demote.py -v`
Expected: PASS.

- [ ] **Step 5: Run the demotion regression suite**

Run: `~/venvs/coscience-dev/bin/python -m pytest tests/test_pm_ideas.py tests/test_pm_directives.py -q`
Expected: PASS (edgeless demotes rewire to a no-op).

- [ ] **Step 6: Commit**

```bash
git add src/coscience/service.py tests/test_graph_demote.py
git commit -m "feat(graph): rewire edges onto the idea on demotion"
```

---

### Task 7: `edge_ops` on PMCycleOutput — staging round-trip and parse

**Files:**
- Modify: `src/coscience/pm_reasoner.py:66-77` (PMCycleOutput)
- Modify: `src/coscience/pm_agent.py:189-238` (write_staging / read_staging)
- Modify: `src/coscience/pm_claude.py:230-261` (parse_response)
- Test: `tests/test_graph_edge_ops_io.py`

**Interfaces:**
- Consumes: nothing from earlier graph tasks (pure plumbing).
- Produces: `PMCycleOutput.edge_ops: list[dict]`, round-tripped through staging and parsed from the reasoner's JSON. Each op: `{op: "add"|"delete", type, src, dst, rationale, confidence?, evidence?}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_graph_edge_ops_io.py
from coscience.models import Program, ProgramStatus
from coscience.pm_agent import read_staging, write_staging
from coscience.pm_claude import parse_response
from coscience.pm_reasoner import PMCycleOutput
from coscience.substrate import Substrate


def test_edge_ops_roundtrip_through_staging(tmp_path):
    sub = Substrate(tmp_path)
    sub.save_program(Program(id="p1", title="P", goals="g", status=ProgramStatus.ACTIVE))
    ops = [{"op": "add", "type": "builds_on", "src": "s2", "dst": "s1", "rationale": "uses it"}]
    write_staging(sub, "p1", 0, PMCycleOutput(edge_ops=ops), "fp")
    staged = read_staging(sub, "p1")
    assert staged.output.edge_ops == ops


def test_parse_response_reads_edge_ops():
    text = ('{"report": "r", "edge_ops": ['
            '{"op": "add", "type": "confirms", "src": "s3", "dst": "s2",'
            ' "rationale": "same result", "confidence": "high"}]}')
    out = parse_response(text)
    assert out.edge_ops == [{"op": "add", "type": "confirms", "src": "s3", "dst": "s2",
                             "rationale": "same result", "confidence": "high"}]


def test_parse_response_defaults_edge_ops_empty():
    assert parse_response('{"report": "r"}').edge_ops == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/venvs/coscience-dev/bin/python -m pytest tests/test_graph_edge_ops_io.py -v`
Expected: FAIL with `TypeError: __init__() got an unexpected keyword argument 'edge_ops'`.

- [ ] **Step 3: Write minimal implementation**

In `src/coscience/pm_reasoner.py`, add to `PMCycleOutput` (after `thread_replies` at line 77):

```python
    edge_ops: list[dict] = field(default_factory=list)  # [{op:"add"|"delete", type, src, dst, rationale, confidence?, evidence?}]
```

In `src/coscience/pm_agent.py` `write_staging`, add to the `data` dict (after `"thread_replies": ...` at line 205):

```python
        "edge_ops": list(output.edge_ops),
```

In `read_staging`, add to the `PMCycleOutput(...)` construction (after `thread_replies=...` at line 233):

```python
        edge_ops=list(data.get("edge_ops", [])),
```

In `src/coscience/pm_claude.py` `parse_response`, add to the returned `PMCycleOutput(...)` (after `thread_replies=...` at line 259-260):

```python
        edge_ops=[dict(o) for o in data.get("edge_ops", [])
                  if isinstance(o, dict) and o.get("op") and o.get("type")],
```

- [ ] **Step 4: Run test to verify it passes**

Run: `~/venvs/coscience-dev/bin/python -m pytest tests/test_graph_edge_ops_io.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Run the reasoner IO regression suite**

Run: `~/venvs/coscience-dev/bin/python -m pytest tests/test_pm_directives.py -q`
Expected: PASS (staging round-trip for directive/idea_order still green).

- [ ] **Step 6: Commit**

```bash
git add src/coscience/pm_reasoner.py src/coscience/pm_agent.py src/coscience/pm_claude.py tests/test_graph_edge_ops_io.py
git commit -m "feat(graph): edge_ops on PMCycleOutput with staging + parse round-trip"
```

---

### Task 8: Apply `edge_ops` deterministically in the PM cycle

**Files:**
- Modify: `src/coscience/pm_agent.py:376-408` region (add an edge-apply block after the idea-pool block, before/around `save_ideas`) and the return dict at `534-537`
- Add a module constant `MAX_EDGE_OPS` near the other caps
- Test: `tests/test_graph_edge_apply.py`

**Interfaces:**
- Consumes: Task 3 (`graph.validate_edge`), Task 1 (`graph.new_edge`, `graph.EDGE_SPEC`), Task 2 (`graph.all_edges`), Task 4 (node `.edges`).
- Produces: `_apply_edge_ops(substrate, program_id, ops, ideas_by_id, now_ts) -> tuple[int, int]` in `pm_agent.py`, returning `(edges_added, edges_removed)`. The `_run_pm_cycle` return dict gains `"edges_added"` and `"edges_removed"`.

**Rules enforced** (Global Constraints): type in `ENABLED_TYPES`; endpoints resolve to live nodes; legal kind pair; evidential gated to done+confidence; lineage stays a DAG; `add` requires non-empty `rationale`; dedup by edge id; PM may only `delete` edges whose `source == "pm"`; at most `MAX_EDGE_OPS` adds applied; invalid ops silently dropped.

**Node set:** ideas come from `ideas_by_id` (already loaded in the cycle); sprints from `substrate.iter_sprints()` for this program. An `add` appends to the source node's `.edges` and the changed sprint is saved; ideas are saved by the existing `save_ideas` at line 408. Apply this block AFTER the idea prune/add/rerank (so promoted/pruned ideas are already gone) and BEFORE `save_ideas` at line 408.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_graph_edge_apply.py
from coscience import graph
from coscience.models import Idea, Program, ProgramStatus, Sprint, SprintStatus
from coscience.pm_agent import pm_beat
from coscience.pm_reasoner import FakeReasoner, PMCycleOutput
from coscience.service import Service


def _svc(tmp_path):
    svc = Service(tmp_path)
    svc.substrate.save_program(Program(id="p1", title="P", goals="g", status=ProgramStatus.ACTIVE))
    return svc


def test_pm_adds_valid_edge_and_drops_invalid(tmp_path):
    svc = _svc(tmp_path)
    svc.substrate.save_sprint(Sprint(id="s1", status=SprintStatus.DONE, goals="g", program="p1"))
    svc.substrate.save_sprint(Sprint(id="s2", status=SprintStatus.DONE, goals="g", program="p1"))
    ops = [
        {"op": "add", "type": "builds_on", "src": "s2", "dst": "s1", "rationale": "uses it"},
        {"op": "add", "type": "builds_on", "src": "s2", "dst": "ghost", "rationale": "bad"},  # missing endpoint
        {"op": "add", "type": "builds_on", "src": "s2", "dst": "s1"},                          # no rationale
    ]
    out = pm_beat(svc.substrate, "p1", FakeReasoner([PMCycleOutput(edge_ops=ops)]), force=True)
    assert out["edges_added"] == 1
    s2 = svc.substrate.load_sprint("s2")
    assert [(e["type"], e["dst"], e["source"]) for e in s2.edges] == [("builds_on", "s1", "pm")]


def test_pm_cannot_delete_human_edge(tmp_path):
    svc = _svc(tmp_path)
    human_edge = graph.new_edge("builds_on", "s2", "s1", "human", by="u", rationale="mine")
    svc.substrate.save_sprint(Sprint(id="s1", status=SprintStatus.DONE, goals="g", program="p1"))
    svc.substrate.save_sprint(Sprint(id="s2", status=SprintStatus.DONE, goals="g", program="p1",
                                     edges=[human_edge]))
    ops = [{"op": "delete", "type": "builds_on", "src": "s2", "dst": "s1"}]
    out = pm_beat(svc.substrate, "p1", FakeReasoner([PMCycleOutput(edge_ops=ops)]), force=True)
    assert out["edges_removed"] == 0
    assert len(svc.substrate.load_sprint("s2").edges) == 1     # human edge survives


def test_pm_deletes_its_own_edge(tmp_path):
    svc = _svc(tmp_path)
    pm_edge = graph.new_edge("builds_on", "s2", "s1", "pm", rationale="mine")
    svc.substrate.save_sprint(Sprint(id="s1", status=SprintStatus.DONE, goals="g", program="p1"))
    svc.substrate.save_sprint(Sprint(id="s2", status=SprintStatus.DONE, goals="g", program="p1",
                                     edges=[pm_edge]))
    ops = [{"op": "delete", "type": "builds_on", "src": "s2", "dst": "s1"}]
    out = pm_beat(svc.substrate, "p1", FakeReasoner([PMCycleOutput(edge_ops=ops)]), force=True)
    assert out["edges_removed"] == 1
    assert svc.substrate.load_sprint("s2").edges == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/venvs/coscience-dev/bin/python -m pytest tests/test_graph_edge_apply.py -v`
Expected: FAIL with `KeyError: 'edges_added'` (return dict lacks the counts and no apply happens).

- [ ] **Step 3: Write minimal implementation**

In `src/coscience/pm_agent.py`, add the cap near the top-level constants (next to `MAX_PROPOSED`):

```python
MAX_EDGE_OPS = 12   # bound the edges the PM may add per cycle (anti-spam)
```

Add the apply helper (place near `_rewire_on_promote`):

```python
def _apply_edge_ops(substrate, program_id: str, ops: list[dict],
                    ideas_by_id: dict, now_ts: float) -> tuple[int, int]:
    """Apply the PM's edge diffs deterministically: validate each, silently drop
    invalid ones, dedup, cap adds, and forbid deleting non-PM edges. Returns
    (added, removed). Ideas are mutated in place (saved by the caller); changed
    sprints are saved here."""
    program_sprints = [s for s in substrate.iter_sprints() if s.program == program_id]
    nodes = list(ideas_by_id.values()) + program_sprints
    node_by_id = {n.id: n for n in nodes}
    existing = graph.all_edges(nodes)
    existing_ids = {e["id"] for e in existing}
    changed_sprint_ids: set[str] = set()
    added = removed = 0
    for op in ops:
        kind = str(op.get("op", ""))
        etype, src, dst = str(op.get("type", "")), str(op.get("src", "")), str(op.get("dst", ""))
        if kind == "add":
            if added >= MAX_EDGE_OPS:
                continue
            if not str(op.get("rationale", "")).strip():
                continue                                   # asserted adds must justify
            edge = graph.new_edge(
                etype, src, dst, "pm", by="pm", at=now_ts,
                rationale=str(op.get("rationale", "")),
                confidence=str(op.get("confidence", "")),
                evidence=str(op.get("evidence", "")))
            if edge["id"] in existing_ids:
                continue                                   # dedup
            if graph.validate_edge(edge, nodes, existing) is not None:
                continue                                   # invalid -> drop
            node_by_id[src].edges.append(edge)
            existing.append(edge)
            existing_ids.add(edge["id"])
            if src in {s.id for s in program_sprints}:
                changed_sprint_ids.add(src)
            added += 1
        elif kind == "delete":
            eid = graph.edge_id(etype, src, dst)
            holder = node_by_id.get(src)
            if holder is None:
                continue
            kept = [e for e in holder.edges
                    if not (e["id"] == eid and e.get("source") == "pm")]  # PM deletes only its own
            if len(kept) != len(holder.edges):
                holder.edges = kept
                existing_ids.discard(eid)
                if src in {s.id for s in program_sprints}:
                    changed_sprint_ids.add(src)
                removed += 1
    sprint_by_id = {s.id: s for s in program_sprints}
    for sid in changed_sprint_ids:
        substrate.save_sprint(sprint_by_id[sid])
    return added, removed
```

In `_run_pm_cycle`, apply the ops right after the idea-pool block and before `save_ideas` (line 408). Insert immediately before `new_summary = ...` (line 407):

```python
    edges_added, edges_removed = _apply_edge_ops(
        substrate, program_id, staged.output.edge_ops, ideas_by_id, now_ts)
```

Update the final return dict (lines 534-537) to include the counts:

```python
    return {"program": program_id, "cycle": cycle, "submitted": submitted,
            "proposed": proposed, "dropped": dropped, "skipped": False,
            "ideas_added": ideas_added, "ideas_removed": ideas_removed,
            "pool_size": len(ideas_by_id),
            "edges_added": edges_added, "edges_removed": edges_removed}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `~/venvs/coscience-dev/bin/python -m pytest tests/test_graph_edge_apply.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Run the full PM suite for regressions**

Run: `~/venvs/coscience-dev/bin/python -m pytest tests/test_pm_ideas.py tests/test_pm_directives.py tests/test_graph_promote.py -q`
Expected: PASS (the early-return "skipped" paths don't touch edges; the reasoned path returns the new keys).

- [ ] **Step 6: Commit**

```bash
git add src/coscience/pm_agent.py tests/test_graph_edge_apply.py
git commit -m "feat(graph): apply PM edge_ops with validation, dedup, caps, pinning"
```

---

### Task 9: Show the graph to the PM (windowed) and document `edge_ops` in the prompt

**Files:**
- Modify: `src/coscience/pm_reasoner.py:27-49` (PMContext — add `graph_lines`)
- Modify: `src/coscience/pm_agent.py:126-149` (gather_context — build the windowed adjacency)
- Modify: `src/coscience/pm_claude.py:88-210` (render_prompt — a GRAPH block + the `edge_ops` schema/instructions)
- Test: `tests/test_graph_prompt.py`

**Interfaces:**
- Consumes: Task 1 (`graph.EDGE_SPEC` not needed here), Task 4 (node `.edges`), Task 2 (`graph.all_edges`).
- Produces: `PMContext.graph_lines: list[str]` — one line per shown node that has edges, of the form `"<node-id>: builds_on s1; refutes s2"`. `render_prompt` renders these under a `LINEAGE GRAPH` heading and documents `edge_ops` in the JSON schema.

**Windowing:** include edges whose **source** is a node already shown in the prompt (idea pool + open + completed sprints). This keeps the block proportional to the rendered window, not the whole program. (Distal-cluster summaries from spec §5.2 are deferred — not needed for correctness.)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_graph_prompt.py
from coscience import graph
from coscience.models import Idea, Program, ProgramStatus, Sprint, SprintStatus
from coscience.pm_agent import gather_context
from coscience.pm_claude import render_prompt
from coscience.substrate import Substrate


def _sub(tmp_path):
    sub = Substrate(tmp_path)
    sub.save_program(Program(id="p1", title="P", goals="cure", status=ProgramStatus.ACTIVE))
    return sub


def test_gather_context_builds_windowed_graph_lines(tmp_path):
    sub = _sub(tmp_path)
    sub.save_sprint(Sprint(id="s1", status=SprintStatus.DONE, goals="base", program="p1"))
    sub.save_sprint(Sprint(id="s2", status=SprintStatus.DONE, goals="next", program="p1",
                           edges=[graph.new_edge("builds_on", "s2", "s1", "pm", rationale="r")]))
    ctx = gather_context(sub, "p1")
    assert "s2: builds_on s1" in ctx.graph_lines


def test_render_prompt_shows_graph_and_edge_ops_schema():
    from coscience.pm_reasoner import PMContext
    ctx = PMContext(program_id="p1", goals="g", cycle=0,
                    graph_lines=["s2: builds_on s1"])
    prompt = render_prompt(ctx)
    assert "s2: builds_on s1" in prompt
    assert "edge_ops" in prompt                       # PM is told it can emit edges
    assert "builds_on" in prompt                       # vocabulary surfaced
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/venvs/coscience-dev/bin/python -m pytest tests/test_graph_prompt.py -v`
Expected: FAIL with `TypeError: __init__() got an unexpected keyword argument 'graph_lines'`.

- [ ] **Step 3: Write minimal implementation**

In `src/coscience/pm_reasoner.py`, add to `PMContext` (after `directive` at line 45):

```python
    graph_lines: list[str] = field(default_factory=list)  # windowed lineage-graph adjacency, one line per node with edges
```

In `src/coscience/pm_agent.py` `gather_context`, build the lines before the `return PMContext(...)` (after `idea_feedback` is built, around line 138). The shown-node set is the ideas plus the open/completed/failed sprints already gathered:

```python
    # Windowed lineage graph: adjacency for edges whose SOURCE is a node already
    # shown in this prompt (ideas + open/completed/failed sprints). Keeps the
    # block proportional to the rendered window, not the whole program.
    shown_ids = ({i.id for i in ideas}
                 | {s["id"] for s in open_sprints}
                 | {s["id"] for s in completed} | {s["id"] for s in failed})
    graph_lines: list[str] = []
    for s in substrate.iter_sprints():
        if s.program == program_id and s.id in shown_ids and s.edges:
            rel = "; ".join(f"{e['type']} {e['dst']}" for e in s.edges)
            graph_lines.append(f"{s.id}: {rel}")
    for i in ideas:
        if i.id in shown_ids and i.edges:
            rel = "; ".join(f"{e['type']} {e['dst']}" for e in i.edges)
            graph_lines.append(f"{i.id}: {rel}")
```

Add `graph_lines=graph_lines,` to the `PMContext(...)` construction (after `workdir=...` at line 148).

In `src/coscience/pm_claude.py` `render_prompt`, build a graph block. Near the other block builders (after `ideas_block`/`directive_block`, around line 86) add:

```python
    graph_block = _lines(context.graph_lines, lambda ln: f"- {ln}") if context.graph_lines else "(none yet)"
```

Insert a GRAPH section into the prompt body — place it just before the `SPRINT CAP:` line (line 144):

```python
LINEAGE GRAPH (existing typed edges among the ideas/experiments above, "<node>: <type> <target>"; each edge points from a node to its antecedent). Use it to avoid re-running refuted or superseded directions and to spot dead-end chains:
{graph_block}
```

Note `render_prompt` uses an f-string; add the `LINEAGE GRAPH` text with `{graph_block}` inside it, mirroring the existing blocks. Then document `edge_ops` in the JSON schema — add this entry to the response-shape object (after the `thread_replies` entry, around line 169):

```python
  "edge_ops": [
    {{"op": "add",
      "type": "<one of: inspired_by | builds_on | supersedes | confirms | refutes>",
      "src": "<node id the edge points FROM>", "dst": "<antecedent node id it points TO>",
      "rationale": "<REQUIRED one line: why this relationship holds>",
      "confidence": "<low|med|high — REQUIRED for confirms/refutes>",
      "evidence": "<optional: the result/finding that decides it>"}},
    {{"op": "delete", "type": "<type>", "src": "<id>", "dst": "<id>"}}
  ],
```

And add a guidance bullet in the curation list (near the idea-pool bullets around line 191):

```python
- RECORD LINEAGE with edge_ops: link an experiment that extends another (builds_on),
  obsoletes it (supersedes), or — once BOTH are done — confirms/refutes its result.
  inspired_by links a direction to what provoked it. Every add needs a one-line rationale;
  confirms/refutes need confidence. You may only delete edges YOU created; endpoints must
  exist. Don't over-link: add an edge only when it changes how the program should be read.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `~/venvs/coscience-dev/bin/python -m pytest tests/test_graph_prompt.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Run the prompt/reasoner regression suite**

Run: `~/venvs/coscience-dev/bin/python -m pytest tests/test_pm_ideas.py tests/test_pm_directives.py -q`
Expected: PASS (render_prompt still builds for contexts with empty `graph_lines`).

- [ ] **Step 6: Commit**

```bash
git add src/coscience/pm_reasoner.py src/coscience/pm_agent.py src/coscience/pm_claude.py tests/test_graph_prompt.py
git commit -m "feat(graph): windowed lineage graph in PM context + edge_ops prompt schema"
```

---

### Task 10: Service + HTTP — human edge add/delete and graph read

**Files:**
- Modify: `src/coscience/service.py` (add `add_edge`, `delete_edge`, `get_graph`)
- Modify: `src/coscience/http_api.py` (three routes + a request model)
- Test: `tests/test_graph_http.py`

**Interfaces:**
- Consumes: Task 3 (`graph.validate_edge`), Task 1 (`graph.new_edge`, `graph.edge_id`), Task 2 (`graph.all_edges`), Task 4 (node `.edges`).
- Produces (service):
  - `add_edge(program_id, etype, src, dst, by, rationale, confidence="", evidence="") -> dict` — validates and appends a `human`-sourced edge to the source node; raises `ValueError` on invalid, `NotFoundError` on unknown program. Returns the edge dict.
  - `delete_edge(program_id, edge_id) -> dict` — removes the edge with that id from whichever program node holds it (humans may delete any edge from the UI). Returns `{"deleted": edge_id}`; raises `NotFoundError` if absent.
  - `get_graph(program_id) -> dict` — `{"nodes": [{id, kind, stage, label}], "edges": [...]}` for the whole program.
- Produces (HTTP): `POST /programs/{id}/edges`, `DELETE /programs/{id}/edges/{edge_id}`, `GET /programs/{id}/graph`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_graph_http.py
from fastapi.testclient import TestClient
from coscience.http_api import build_app
from coscience.models import Program, ProgramStatus, Sprint, SprintStatus
from coscience.service import Service


def _client(tmp_path):
    svc = Service(tmp_path)
    svc.substrate.save_program(Program(id="p1", title="P", goals="g", status=ProgramStatus.ACTIVE))
    svc.substrate.save_sprint(Sprint(id="s1", status=SprintStatus.DONE, goals="g", program="p1"))
    svc.substrate.save_sprint(Sprint(id="s2", status=SprintStatus.DONE, goals="g", program="p1"))
    return svc, TestClient(build_app(svc))


def test_add_and_read_and_delete_edge(tmp_path):
    svc, c = _client(tmp_path)
    r = c.post("/api/programs/p1/edges",
               json={"type": "builds_on", "src": "s2", "dst": "s1", "rationale": "uses it"})
    assert r.status_code == 200
    edge = r.json()
    assert (edge["type"], edge["src"], edge["dst"], edge["source"]) == ("builds_on", "s2", "s1", "human")

    g = c.get("/api/programs/p1/graph").json()
    assert any(e["id"] == edge["id"] for e in g["edges"])
    assert {n["id"] for n in g["nodes"]} >= {"s1", "s2"}

    d = c.delete(f"/api/programs/p1/edges/{edge['id']}")
    assert d.status_code == 200
    assert svc.substrate.load_sprint("s2").edges == []


def test_add_invalid_edge_is_422(tmp_path):
    _svc, c = _client(tmp_path)
    r = c.post("/api/programs/p1/edges",
               json={"type": "builds_on", "src": "s2", "dst": "ghost", "rationale": "x"})
    assert r.status_code == 422


def test_add_edge_unknown_program_is_404(tmp_path):
    _svc, c = _client(tmp_path)
    r = c.post("/api/programs/nope/edges",
               json={"type": "builds_on", "src": "s2", "dst": "s1", "rationale": "x"})
    assert r.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/venvs/coscience-dev/bin/python -m pytest tests/test_graph_http.py -v`
Expected: FAIL with 404/405 (routes don't exist) or AttributeError on `service.add_edge`.

- [ ] **Step 3: Write minimal implementation**

Ensure `from coscience import graph` is imported in `src/coscience/service.py`. Add these methods to `Service` (near the ideas section, after `list_ideas`):

```python
    # --- lineage graph ---
    def _program_nodes(self, program_id: str):
        """(ideas, sprints) for a program — the live node set the graph spans."""
        _summary, ideas = self.substrate.load_ideas(program_id)
        sprints = [s for s in self.substrate.iter_sprints() if s.program == program_id]
        return ideas, sprints

    def add_edge(self, program_id: str, etype: str, src: str, dst: str, by: str = "",
                 rationale: str = "", confidence: str = "", evidence: str = "") -> dict:
        self._require_program(program_id)
        ideas, sprints = self._program_nodes(program_id)
        nodes = list(ideas) + sprints
        node_by_id = {n.id: n for n in nodes}
        edge = graph.new_edge(etype, src, dst, "human", by=by, at=time.time(),
                              rationale=rationale, confidence=confidence, evidence=evidence)
        reason = graph.validate_edge(edge, nodes, graph.all_edges(nodes))
        if reason is not None:
            raise ValueError(reason)
        if any(e["id"] == edge["id"] for e in node_by_id[src].edges):
            raise ValueError("edge already exists")
        node_by_id[src].edges.append(edge)
        self._save_node(program_id, node_by_id[src], ideas)
        self.substrate.commit(f"program {program_id}: add edge {edge['id']} ({etype})")
        return edge

    def delete_edge(self, program_id: str, edge_id: str) -> dict:
        self._require_program(program_id)
        ideas, sprints = self._program_nodes(program_id)
        for n in list(ideas) + sprints:
            kept = [e for e in n.edges if e["id"] != edge_id]
            if len(kept) != len(n.edges):
                n.edges = kept
                self._save_node(program_id, n, ideas)
                self.substrate.commit(f"program {program_id}: delete edge {edge_id}")
                return {"deleted": edge_id}
        raise NotFoundError(edge_id)

    def _save_node(self, program_id: str, node, ideas) -> None:
        """Persist one node after an edge change: a sprint saves directly; an idea
        requires re-saving the whole pool (single ideas.md)."""
        if isinstance(node, Sprint):
            self.substrate.save_sprint(node)
        else:
            summary, _ = self.substrate.load_ideas(program_id)
            self.substrate.save_ideas(program_id, summary, ideas)

    def get_graph(self, program_id: str) -> dict:
        self._require_program(program_id)
        ideas, sprints = self._program_nodes(program_id)
        nodes = [{"id": i.id, "kind": graph.node_kind(i), "stage": graph.node_stage(i),
                  "label": i.text[:80]} for i in ideas]
        nodes += [{"id": s.id, "kind": graph.node_kind(s), "stage": graph.node_stage(s),
                   "label": (s.title or s.goals)[:80]} for s in sprints]
        return {"nodes": nodes, "edges": graph.all_edges(list(ideas) + sprints)}
```

In `src/coscience/http_api.py`, add a request model near the other Pydantic models:

```python
class EdgeCreate(BaseModel):
    type: str
    src: str
    dst: str
    rationale: str = ""
    confidence: str = ""
    evidence: str = ""
```

Add the three routes (near the program routes, mirroring the demote route's error handling at http_api.py:338-346):

```python
    @api.post("/programs/{program_id}/edges")
    def add_edge(program_id: str, body: EdgeCreate,
                 user: "auth.User | None" = Depends(current_user)) -> dict:
        try:
            return service.add_edge(program_id, body.type, body.src, body.dst,
                                    by=(user.username if user else ""),
                                    rationale=body.rationale, confidence=body.confidence,
                                    evidence=body.evidence)
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"program not found: {program_id}")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    @api.delete("/programs/{program_id}/edges/{edge_id}")
    def delete_edge(program_id: str, edge_id: str) -> dict:
        try:
            return service.delete_edge(program_id, edge_id)
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"edge not found: {edge_id}")

    @api.get("/programs/{program_id}/graph")
    def get_graph(program_id: str) -> dict:
        try:
            return service.get_graph(program_id)
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"program not found: {program_id}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `~/venvs/coscience-dev/bin/python -m pytest tests/test_graph_http.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Run the full backend suite**

Run: `~/venvs/coscience-dev/bin/python -m pytest -q --ignore=tests/test_mcp_server.py --ignore=tests/test_mcp_tools.py --ignore=tests/test_transport_programs.py`
Expected: PASS (all graph tests + no regressions in the existing suite).

- [ ] **Step 6: Commit**

```bash
git add src/coscience/service.py src/coscience/http_api.py tests/test_graph_http.py
git commit -m "feat(graph): human edge add/delete + program graph read endpoints"
```

---

## Follow-up plans (not in this plan)

- **Frontend graph view** (spec §6 / phase 5) — needs a viz-library decision (reactflow vs cytoscape vs plain SVG) and its own plan. Consumes `GET /programs/{id}/graph` and the `POST`/`DELETE` edge routes built here.
- **Enable the Extended edge tier** (phase 6) — flip `ENABLED_TYPES |= EXTENDED_TYPES` in `graph.py`, extend the prompt vocabulary, and add tests for `refines`/`follows`/`replicates`/`duplicate_of`/`contradicts`. Small, deferred until Core is proven in production.

## Self-Review

**Spec coverage:**
- §2.1 one-node/three-stage → Task 1 (`node_stage`), enforced in validation Task 3, promote Task 5, demote Task 6.
- §2.2 edge vocabulary + families + tiers + DAG/evidential → Task 1 (`EDGE_SPEC`), Task 3 (validation, cycle).
- §2.3 edge attributes + pinning → Task 1 (`new_edge`), Task 8 (rationale/confidence/pin enforcement), Task 10 (human source).
- §3 storage (outbound frontmatter + reverse index) → Task 4 (persistence), Task 2 (`build_reverse_index`). (The rebuildable index is computed in-memory per spec's "or in-memory, rebuilt on load"; no `.coscience/graph.json` file — documented deviation, simpler.)
- §4 transitions (deterministic rewire + PM curation next cycle) → Task 5 (promote), Task 6 (demote), Task 8 (PM curation via edge_ops), §4.3 finish = stage derivation (Task 1).
- §5 PM producer/consumer → Task 7 (edge_ops IO), Task 8 (apply), Task 9 (windowed prompt).
- §7 failure modes → caps (Task 8 `MAX_EDGE_OPS`), validation drop (Task 3/8), pinning (Task 8), dedup (Task 8), canonical direction (Task 1 single-direction storage).
- §8 phases 1-4 → Tasks 1-10. Phases 5-6 → follow-up plans (documented).

**Placeholder scan:** none — every code step carries full code; every run step names the exact command + expected result.

**Type consistency:** `graph.new_edge` signature identical across Tasks 1, 5, 6, 8, 10. `repoint_edges`/`drop_evidential_incident` return `set[str]` used consistently in Tasks 5/6. `validate_edge(edge, nodes, existing_edges)` signature identical in Tasks 3, 8, 10. `_run_pm_cycle` return dict extended additively (Task 8) — existing keys from Task-0 baseline preserved. `PMContext.graph_lines` (Task 9) and `PMCycleOutput.edge_ops` (Task 7) defined before use.

**One deliberate spec deviation:** reverse index is in-memory/rebuilt, not a persisted `.coscience/graph.json`. Spec explicitly permits this ("or in-memory, rebuilt on load"). Node sets per program are small; rebuild cost is negligible and it removes an index-staleness failure mode.
