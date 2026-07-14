# User Accounts & Attribution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add username-only login (curated registry) and attribute every human action to the logged-in user, behind one identity seam that a later Keycloak/OIDC swap replaces without touching attribution code.

**Architecture:** A new `coscience/auth.py` owns the registry, a signed session cookie, and a `current_user` FastAPI dependency. HTTP routes derive the actor from `current_user` (never from the client) and pass `by=<username>` into service methods, which stamp it on chat messages, comments, votes, and a per-sprint `decisions` trail. The React app gates on `/api/me` and shows authors as initials chips.

**Tech Stack:** Python 3.11+ (stdlib `hmac`/`hashlib`/`base64`/`secrets` — no new deps), PyYAML (already present), FastAPI, React + Mantine + TanStack Query.

## Global Constraints

- **No new Python dependencies.** Cookie signing uses stdlib only.
- **Actor is always server-derived** from `current_user`. Never trust a client-sent `by`. (Votes keep a browser-id fallback used only when auth is disabled.)
- **Empty/absent registry ⇒ auth disabled** (`.coscience/users.yaml` missing or no users): the gate passes anonymously, attributed actions record `by=""` (rendered "—"). Preserves current behavior and keeps the existing auth-less test suite green.
- **Cookie:** name `coscience_user`; value `username.<base64url-hmac-sha256>`; flags `HttpOnly`, `SameSite=Lax`, `Path=/`, no `Secure` (plain-HTTP tunnel). Secret from `COSCIENCE_SECRET` env, else generated once into `.coscience/secret` (mode 600, gitignored).
- **`username`** is the stable attribution key and future Keycloak subject. `by` fields default to `""` for back-compat.
- Follow existing patterns: dataclasses in `models.py`, `frontmatter_io` for persistence, routes as closures over `service` in `build_app`, tests via `TestClient(build_app(Service(tmp_path)))`.
- Seed users: **Oleg Stroganov (stroganov, OS)** and **Aish Pathak (apathak, AP)**.

---

### Task 1: Auth registry + `User` model

**Files:**
- Create: `src/coscience/auth.py`
- Test: `tests/test_auth_registry.py`

**Interfaces:**
- Produces: `User(username: str, name: str, initials: str)` (frozen dataclass); `load_users(repo_root) -> dict[str, User]` (username → User; `{}` when file absent/empty); `_derive_initials(name: str) -> str`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_auth_registry.py
from coscience import auth


def _write_registry(tmp_path, text):
    d = tmp_path / ".coscience"
    d.mkdir(parents=True, exist_ok=True)
    (d / "users.yaml").write_text(text)


def test_load_seeded_users(tmp_path):
    _write_registry(tmp_path, """
users:
  - username: stroganov
    name: Oleg Stroganov
    initials: OS
  - username: apathak
    name: Aish Pathak
""")
    users = auth.load_users(tmp_path)
    assert set(users) == {"stroganov", "apathak"}
    assert users["stroganov"].name == "Oleg Stroganov"
    assert users["stroganov"].initials == "OS"
    assert users["apathak"].initials == "AP"   # derived from name


def test_empty_and_missing_registry(tmp_path):
    assert auth.load_users(tmp_path) == {}          # no file
    _write_registry(tmp_path, "users: []\n")
    assert auth.load_users(tmp_path) == {}          # empty list


def test_derive_initials():
    assert auth._derive_initials("Oleg Stroganov") == "OS"
    assert auth._derive_initials("Cher") == "CH"
    assert auth._derive_initials("") == "?"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/venvs/coscience/bin/pytest tests/test_auth_registry.py -v` (locally on the Linux host; there is no Windows runtime)
Expected: FAIL with `ModuleNotFoundError: No module named 'coscience.auth'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/coscience/auth.py
"""Lightweight user identity: a curated registry + a signed session cookie.

All identity resolution lives here so a later Keycloak/OIDC swap touches only this
module. `username` is the stable attribution key (and future OIDC subject)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class User:
    username: str
    name: str
    initials: str


def _derive_initials(name: str) -> str:
    parts = [p for p in name.split() if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def _users_path(repo_root) -> Path:
    return Path(repo_root) / ".coscience" / "users.yaml"


def load_users(repo_root) -> dict[str, User]:
    """username -> User from `.coscience/users.yaml`; {} if absent/empty."""
    path = _users_path(repo_root)
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text()) or {}
    out: dict[str, User] = {}
    for row in (data.get("users") or []):
        uname = str(row.get("username", "")).strip()
        if not uname:
            continue
        name = str(row.get("name") or uname).strip()
        initials = str(row.get("initials") or "").strip() or _derive_initials(name)
        out[uname] = User(username=uname, name=name, initials=initials)
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `~/venvs/coscience/bin/pytest tests/test_auth_registry.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/coscience/auth.py tests/test_auth_registry.py
git commit -m "feat(auth): curated user registry loader + User model"
```

---

### Task 2: Signed session cookie

**Files:**
- Modify: `src/coscience/auth.py` (append)
- Test: `tests/test_auth_cookie.py`

**Interfaces:**
- Produces: `make_cookie(username: str, repo_root) -> str`; `verify_cookie(value: str, repo_root) -> str` (returns username if valid, else `""`); `_secret(repo_root) -> bytes`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_auth_cookie.py
from coscience import auth


def test_sign_verify_roundtrip(tmp_path):
    c = auth.make_cookie("stroganov", tmp_path)
    assert auth.verify_cookie(c, tmp_path) == "stroganov"


def test_tampered_cookie_rejected(tmp_path):
    c = auth.make_cookie("stroganov", tmp_path)
    assert auth.verify_cookie(c[:-1] + ("x" if c[-1] != "x" else "y"), tmp_path) == ""
    assert auth.verify_cookie("apathak." + c.split(".", 1)[1], tmp_path) == ""  # swapped name
    assert auth.verify_cookie("", tmp_path) == ""
    assert auth.verify_cookie("no-dot", tmp_path) == ""


def test_secret_persisted_and_stable(tmp_path):
    c1 = auth.make_cookie("stroganov", tmp_path)
    assert (tmp_path / ".coscience" / "secret").is_file()
    c2 = auth.make_cookie("stroganov", tmp_path)   # same secret reused
    assert c1 == c2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/venvs/coscience/bin/pytest tests/test_auth_cookie.py -v`
Expected: FAIL with `AttributeError: module 'coscience.auth' has no attribute 'make_cookie'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to src/coscience/auth.py
import base64
import hashlib
import hmac
import os
import secrets


def _secret(repo_root) -> bytes:
    env = os.environ.get("COSCIENCE_SECRET")
    if env:
        return env.encode()
    path = Path(repo_root) / ".coscience" / "secret"
    if path.is_file():
        return path.read_bytes()
    path.parent.mkdir(parents=True, exist_ok=True)
    tok = secrets.token_bytes(32)
    path.write_bytes(tok)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return tok


def make_cookie(username: str, repo_root) -> str:
    mac = hmac.new(_secret(repo_root), username.encode(), hashlib.sha256).digest()
    sig = base64.urlsafe_b64encode(mac).decode().rstrip("=")   # b64url has no '.'
    return f"{username}.{sig}"


def verify_cookie(value: str, repo_root) -> str:
    """Username if the signed cookie is valid and untampered, else ''."""
    if not value or "." not in value:
        return ""
    username = value.rpartition(".")[0]
    if hmac.compare_digest(value, make_cookie(username, repo_root)):
        return username
    return ""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `~/venvs/coscience/bin/pytest tests/test_auth_cookie.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/coscience/auth.py tests/test_auth_cookie.py
git commit -m "feat(auth): HMAC-signed session cookie + persisted secret"
```

---

### Task 3: `current_user` dependency + login/logout/me/users + gate

**Files:**
- Modify: `src/coscience/http_api.py` (imports; split routers in `build_app`; add auth endpoints)
- Test: `tests/test_http_auth.py`

**Interfaces:**
- Consumes: `auth.load_users`, `auth.verify_cookie`, `auth.make_cookie`, `Service.repo_root`.
- Produces: dependency `current_user(request) -> auth.User | None` (None when auth disabled; raises 401 when enabled and no valid cookie); `COOKIE = "coscience_user"`. Endpoints: `POST /api/login`, `POST /api/logout`, `GET /api/me`, `GET /api/users`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_http_auth.py
import pytest
from fastapi.testclient import TestClient
from coscience.http_api import build_app
from coscience.service import Service
from coscience.models import Program, ProgramStatus


def _svc(tmp_path, seed=True):
    svc = Service(tmp_path)
    svc.substrate.save_program(Program(id="p1", title="P", goals="g", status=ProgramStatus.ACTIVE))
    if seed:
        d = tmp_path / ".coscience"; d.mkdir(parents=True, exist_ok=True)
        (d / "users.yaml").write_text(
            "users:\n  - username: stroganov\n    name: Oleg Stroganov\n    initials: OS\n")
    return svc


def test_gate_blocks_when_seeded(tmp_path):
    c = TestClient(build_app(_svc(tmp_path)))
    assert c.get("/api/health").status_code == 200          # open
    assert c.get("/api/sprints").status_code == 401         # gated
    assert c.get("/api/me").status_code == 401


def test_login_me_logout(tmp_path):
    c = TestClient(build_app(_svc(tmp_path)))
    assert c.post("/api/login", json={"username": "ghost"}).status_code == 401
    r = c.post("/api/login", json={"username": "stroganov"})
    assert r.status_code == 200 and r.json()["initials"] == "OS"
    assert c.get("/api/sprints").status_code == 200         # cookie now carried
    assert c.get("/api/me").json()["user"]["username"] == "stroganov"
    c.post("/api/logout")
    assert c.get("/api/sprints").status_code == 401


def test_auth_disabled_when_no_registry(tmp_path):
    c = TestClient(build_app(_svc(tmp_path, seed=False)))
    assert c.get("/api/sprints").status_code == 200         # open
    assert c.get("/api/me").json() == {"user": None, "required": False}
    assert c.get("/api/users").json() == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/venvs/coscience/bin/pytest tests/test_http_auth.py -v`
Expected: FAIL (`/api/sprints` returns 200 not 401 — no gate yet; `/api/login` 404).

- [ ] **Step 3: Implement — imports + routers + endpoints**

In `src/coscience/http_api.py`, extend the imports near the top:

```python
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, Response
from coscience import auth
```

Add the request model near the other `BaseModel`s (after `IdeaDemoteIn`):

```python
class LoginIn(BaseModel):
    username: str
```

In `build_app`, replace the single-router setup. Change:

```python
    api = APIRouter(prefix="/api")

    @api.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @api.get("/version")
    def version() -> dict:
        return {"sha": server_version()}
```

to:

```python
    COOKIE = "coscience_user"

    def current_user(request: Request) -> "auth.User | None":
        users = auth.load_users(service.repo_root)
        if not users:
            return None                                   # auth disabled (empty registry)
        uname = auth.verify_cookie(request.cookies.get(COOKIE, ""), service.repo_root)
        u = users.get(uname)
        if u is None:
            raise HTTPException(status_code=401, detail="not authenticated")
        return u

    pub = APIRouter(prefix="/api")                          # open endpoints
    api = APIRouter(prefix="/api", dependencies=[Depends(current_user)])  # gated

    @pub.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @pub.get("/version")
    def version() -> dict:
        return {"sha": server_version()}

    @pub.get("/users")
    def list_users() -> list[dict]:
        return [{"username": u.username, "name": u.name, "initials": u.initials}
                for u in auth.load_users(service.repo_root).values()]

    @pub.get("/me")
    def me(user: "auth.User | None" = Depends(current_user)) -> dict:
        return {"user": None if user is None else
                {"username": user.username, "name": user.name, "initials": user.initials},
                "required": bool(auth.load_users(service.repo_root))}

    @pub.post("/login")
    def login(body: LoginIn, response: Response) -> dict:
        u = auth.load_users(service.repo_root).get(body.username.strip())
        if u is None:
            raise HTTPException(status_code=401, detail="unknown user")
        response.set_cookie(COOKIE, auth.make_cookie(u.username, service.repo_root),
                            httponly=True, samesite="lax", path="/",
                            max_age=60 * 60 * 24 * 30)
        return {"username": u.username, "name": u.name, "initials": u.initials}

    @pub.post("/logout")
    def logout(response: Response) -> dict:
        response.delete_cookie(COOKIE, path="/")
        return {"ok": True}
```

At the end of `build_app`, change `app.include_router(api)` to include both:

```python
    app.include_router(pub)
    app.include_router(api)
    return app
```

(All existing `@api.<method>` routes stay on `api` and are now gated.)

- [ ] **Step 4: Run test to verify it passes**

Run: `~/venvs/coscience/bin/pytest tests/test_http_auth.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run the full existing HTTP suite (must stay green — auth disabled with no registry)**

Run: `~/venvs/coscience/bin/pytest tests/test_http_api.py tests/test_http_guidance.py tests/test_http_ideas.py -q`
Expected: PASS (these tests write no registry, so auth is disabled)

- [ ] **Step 6: Commit**

```bash
git add src/coscience/http_api.py tests/test_http_auth.py
git commit -m "feat(auth): current_user gate + login/logout/me/users endpoints"
```

---

### Task 4: Attribution data model (`by` + per-sprint `decisions`)

**Files:**
- Modify: `src/coscience/models.py` (Sprint, Idea)
- Modify: `src/coscience/substrate.py` (load_sprint/save_sprint; load_ideas/save_ideas)
- Test: `tests/test_attribution_model.py`

**Interfaces:**
- Produces: `Sprint.decisions: list[dict]` (`[{by, action, at}]`); `Idea.by: str`. Both persist and round-trip; absent in old files → default `[]` / `""`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_attribution_model.py
from coscience.substrate import Substrate
from coscience.models import Sprint, SprintStatus


def test_sprint_decisions_roundtrip(tmp_path):
    sub = Substrate(tmp_path)
    s = Sprint(id="s1", status=SprintStatus.PROPOSED, goals="g", plan=["a"])
    s.decisions.append({"by": "stroganov", "action": "approve", "at": 1.0})
    sub.save_sprint(s)
    got = sub.load_sprint("s1")
    assert got.decisions == [{"by": "stroganov", "action": "approve", "at": 1.0}]


def test_sprint_defaults_empty_decisions(tmp_path):
    sub = Substrate(tmp_path)
    sub.save_sprint(Sprint(id="s2", status=SprintStatus.PROPOSED, goals="g", plan=["a"]))
    assert sub.load_sprint("s2").decisions == []


def test_idea_by_roundtrip(tmp_path):
    sub = Substrate(tmp_path)
    from coscience.models import Idea
    sub.save_ideas("p1", "", [Idea(id="i1", text="t", source="human", by="apathak")])
    _summary, ideas = sub.load_ideas("p1")
    assert ideas[0].by == "apathak"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/venvs/coscience/bin/pytest tests/test_attribution_model.py -v`
Expected: FAIL (`TypeError: __init__() got an unexpected keyword argument 'by'` for Idea; `decisions` attribute missing).

- [ ] **Step 3a: Add model fields**

In `src/coscience/models.py`, in `Sprint` (after the `votes` field):

```python
    decisions: list[dict] = field(default_factory=list)  # governance trail [{by, action, at}]
```

In `Idea` (after the `source` field):

```python
    by: str = ""                        # username who added it (human-added); "" if unknown/PM
```

- [ ] **Step 3b: Persist on the sprint**

In `src/coscience/substrate.py` `load_sprint`, add to the `Sprint(...)` call (after `votes=[...]`):

```python
            decisions=[{"by": str(d.get("by", "")), "action": str(d.get("action", "")),
                        "at": float(d.get("at", 0.0))} for d in fm.get("decisions", [])],
```

In `save_sprint`, after the `if sprint.votes:` block:

```python
        if sprint.decisions:
            fm["decisions"] = list(sprint.decisions)
```

- [ ] **Step 3c: Persist on the idea**

In `src/coscience/substrate.py` `load_ideas`, in the `Idea(...)` construction add:

```python
                by=str(n.get("by", "")),
```

In `save_ideas`, in the per-idea dict (the one with `"comments": list(i.comments)`) add:

```python
                 "by": i.by,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `~/venvs/coscience/bin/pytest tests/test_attribution_model.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/coscience/models.py src/coscience/substrate.py tests/test_attribution_model.py
git commit -m "feat(attribution): persist sprint decisions + idea author"
```

---

### Task 5: Wire attribution through service + HTTP routes

**Files:**
- Modify: `src/coscience/service.py` (governance methods, comments, chat, idea add — add `by`)
- Modify: `src/coscience/http_api.py` (attributed routes derive `by` from `current_user`)
- Test: `tests/test_attribution_flow.py`

**Interfaces:**
- Consumes: `current_user` (Task 3); `Sprint.decisions`, `Idea.by` (Task 4).
- Produces: service signatures gain `by: str = ""` — `approve_sprint(id, by="")`, `run_sprint(id, by="")`, `send_back_sprint(id, by="")`, `reject_sprint(id, by="")`, `demote_sprint(id, by="")`, `add_sprint_comment(id, text, target="worker", by="")`, `add_idea(program_id, text, source="human", by="")`, `add_idea_comment(program_id, idea_id, text, by="")`, `post_chat_message(program_id, thread_id, message, by="", launch=None)`. Static helper `Service._decide(sprint, by, action)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_attribution_flow.py
from fastapi.testclient import TestClient
from coscience.http_api import build_app
from coscience.service import Service
from coscience.models import Program, ProgramStatus, Sprint, SprintStatus


def _client(tmp_path):
    svc = Service(tmp_path)
    svc.substrate.save_program(Program(id="p1", title="P", goals="g", status=ProgramStatus.ACTIVE))
    svc.substrate.save_sprint(Sprint(id="s1", status=SprintStatus.PROPOSED, goals="g",
                                     plan=["a"], program="p1"))
    d = tmp_path / ".coscience"; d.mkdir(parents=True, exist_ok=True)
    (d / "users.yaml").write_text("users:\n  - username: stroganov\n    name: Oleg Stroganov\n")
    c = TestClient(build_app(svc))
    c.post("/api/login", json={"username": "stroganov"})
    return c, svc


def test_approve_records_actor(tmp_path):
    c, svc = _client(tmp_path)
    assert c.post("/api/sprints/s1/approve").status_code == 200
    decisions = svc.substrate.load_sprint("s1").decisions
    assert decisions[-1]["by"] == "stroganov" and decisions[-1]["action"] == "approve"


def test_comment_actor_is_server_derived_not_client(tmp_path):
    c, svc = _client(tmp_path)
    # client tries to spoof a different author in the body — must be ignored
    r = c.post("/api/sprints/s1/comments", json={"text": "hi", "target": "pm", "by": "apathak"})
    assert r.status_code == 201 and r.json()["by"] == "stroganov"


def test_vote_uses_username_when_authed(tmp_path):
    c, svc = _client(tmp_path)
    c.post("/api/sprints/s1/vote", json={"by": "browser-xyz", "value": 1})
    votes = svc.substrate.load_sprint("s1").votes
    assert votes[0]["by"] == "stroganov"   # server identity, not the body's browser id
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/venvs/coscience/bin/pytest tests/test_attribution_flow.py -v`
Expected: FAIL (`decisions` empty; comment has no `by`; vote `by` == "browser-xyz").

- [ ] **Step 3a: Service — decision helper + governance methods**

In `src/coscience/service.py`, add the helper (near `_vote_tally`):

```python
    @staticmethod
    def _decide(sprint, by: str, action: str) -> None:
        sprint.decisions.append({"by": str(by or ""), "action": action, "at": time.time()})
```

Add `by: str = ""` to each governance method and record before `save_sprint`:

- `approve_sprint(self, sprint_id: str, by: str = "")` → before `self.substrate.save_sprint(sprint)` add `self._decide(sprint, by, "approve")`
- `run_sprint(self, sprint_id: str, by: str = "")` → `self._decide(sprint, by, "run")`
- `send_back_sprint(self, sprint_id: str, by: str = "")` → `self._decide(sprint, by, "send_back")`
- `reject_sprint(self, sprint_id: str, by: str = "")` → `self._decide(sprint, by, "reject")`
- `demote_sprint(self, sprint_id: str, by: str = "")` → `self._decide(sprint, by, "demote")` before its save

- [ ] **Step 3b: Service — comments, idea add, chat**

`add_sprint_comment(self, sprint_id, text, target="worker", by="")` — change the comment dict to:

```python
        comment = {"id": uuid4().hex[:8], "text": text, "added_at": time.time(),
                   "target": target, "by": str(by or "")}
```

`add_idea_comment(self, program_id, idea_id, text, by="")` — change the appended dict to:

```python
        target.comments.append({"id": uuid4().hex[:8], "text": text,
                                "added_at": time.time(), "by": str(by or "")})
```

`add_idea(self, program_id, text, source="human", by="")` — set `by` on the created `Idea` (find the `Idea(...)` it constructs and add `by=str(by or "")`).

`post_chat_message(self, program_id, thread_id, message, by="", launch=None)` — change the user message append to:

```python
        thread.messages.append({"role": "user", "text": message, "at": time.time(), "by": str(by or "")})
```

- [ ] **Step 3c: Service — persist `by` on comments load (back-compat)**

In `src/coscience/substrate.py` `load_sprint`, the comments comprehension — add `"by"`:

```python
            comments=[{"id": str(c["id"]), "text": str(c["text"]),
                       "added_at": float(c["added_at"]),
                       "target": str(c.get("target", "worker")),
                       "by": str(c.get("by", ""))} for c in fm.get("comments", [])],
```

In `load_ideas`, the idea-comments comprehension — add `"by"`:

```python
                comments=[{"id": str(c["id"]), "text": str(c["text"]),
                           "added_at": float(c["added_at"]),
                           "by": str(c.get("by", ""))} for c in n.get("comments", [])],
```

Chat message load (`load_chat_thread` in substrate) — add `by` to the messages comprehension:

```python
            messages=[{"role": str(m.get("role", "user")), "text": str(m.get("text", "")),
                       "at": float(m.get("at", 0.0)), "by": str(m.get("by", ""))}
                      for m in fm.get("messages", [])],
```

- [ ] **Step 3d: HTTP routes — derive `by` from `current_user`**

In `src/coscience/http_api.py`, add `user: "auth.User | None" = Depends(current_user)` to each attributed route and pass `by`. The helper value is `by = user.username if user else ""`. Concretely:

```python
    @api.post("/sprints/{sprint_id}/comments", status_code=201)
    def comment_sprint(sprint_id: str, body: SprintCommentIn,
                       user: "auth.User | None" = Depends(current_user)) -> dict:
        try:
            return service.add_sprint_comment(sprint_id, body.text, target=body.target,
                                              by=(user.username if user else ""))
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"sprint not found: {sprint_id}")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
```

Apply the same pattern (add the `user=Depends(current_user)` param; pass `by=(user.username if user else "")`) to: `approve_sprint`, `run_sprint`, `send_back_sprint`, `reject_sprint`, `demote_sprint`, `add_idea` (pass `by=`), `comment_idea` (→ `add_idea_comment(..., by=)`), and the chat message route (`post_chat_message(..., by=)`).

For the **vote** route, the actor falls back to the client browser id only when auth is disabled:

```python
    @api.post("/sprints/{sprint_id}/vote")
    def vote_sprint(sprint_id: str, body: VoteIn,
                    user: "auth.User | None" = Depends(current_user)) -> dict:
        by = user.username if user else body.by
        try:
            return service.vote_sprint(sprint_id, by, body.value)
        except NotFoundError:
            raise HTTPException(status_code=404, detail=f"sprint not found: {sprint_id}")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `~/venvs/coscience/bin/pytest tests/test_attribution_flow.py tests/test_service_programs.py -v`
Expected: PASS (new flow tests + existing service tests — the latter call service methods directly and rely on the `by=""` defaults).

- [ ] **Step 5: Full suite regression**

Run: `~/venvs/coscience/bin/pytest -q`
Expected: PASS except the 3 pre-existing `mcp`-extra collection errors (unrelated — `mcp` not installed).

- [ ] **Step 6: Commit**

```bash
git add src/coscience/service.py src/coscience/substrate.py src/coscience/http_api.py tests/test_attribution_flow.py
git commit -m "feat(attribution): stamp server-derived actor on all human actions"
```

---

### Task 6: Frontend — login gate, user menu, author chips

**Files:**
- Modify: `frontend/src/api.ts` (types + auth endpoints + 401 signal)
- Create: `frontend/src/auth.tsx` (AuthGate + useMe + UserChip)
- Modify: `frontend/src/main.tsx` (wrap App in AuthGate)
- Modify: `frontend/src/App.tsx` (user menu in header)
- Modify: `frontend/src/views/SprintDetail.tsx` (vote uses current user; show comment/decision authors)
- Modify: `frontend/src/views/ChatView.tsx` (show message author)

**Interfaces:**
- Consumes: `GET /api/me`, `GET /api/users`, `POST /api/login`, `POST /api/logout`; `by` fields from Tasks 4–5.
- Produces: `api.me/login/logout/listUsers`; `<AuthGate>`, `useMe()`, `<UserChip username=... />`.

- [ ] **Step 1: api.ts — types + endpoints**

Add types near the top of `frontend/src/api.ts`:

```typescript
export interface CurrentUser { username: string; name: string; initials: string }
export interface MeResponse { user: CurrentUser | null; required: boolean }
```

Extend existing interfaces: add `by?: string` to `ChatMessage`, `SprintComment`, and `IdeaComment`; add `decisions?: { by: string; action: string; at: number }[]` to `Sprint`.

Add endpoints to the `api` object:

```typescript
  me: () => fetch("/api/me").then(j<MeResponse>),
  listUsers: () => fetch("/api/users").then(j<CurrentUser[]>),
  login: (username: string) =>
    fetch("/api/login", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username }),
    }).then(j<CurrentUser>),
  logout: () => fetch("/api/logout", { method: "POST" }).then(j<{ ok: boolean }>),
```

- [ ] **Step 2: auth.tsx — gate, hook, chip**

```tsx
// frontend/src/auth.tsx
import { useState } from "react";
import { Button, Group, Select, Stack, Text, Tooltip } from "@mantine/core";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type CurrentUser } from "./api";

export function useMe() {
  return useQuery({ queryKey: ["me"], queryFn: api.me });
}

/** Initials avatar + name for an attributed action. Resolves display from the
 *  registry; falls back to the raw username, or "—" when unattributed. */
export function UserChip({ username }: { username?: string }) {
  const users = useQuery({ queryKey: ["users"], queryFn: api.listUsers });
  if (!username) return <Text component="span" size="xs" c="dimmed">—</Text>;
  const u = (users.data ?? []).find((x) => x.username === username);
  const initials = u?.initials ?? username.slice(0, 2).toUpperCase();
  const name = u?.name ?? username;
  return (
    <Tooltip label={name}>
      <span style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
        <span style={{ width: 18, height: 18, borderRadius: 9, fontSize: 9, fontWeight: 700,
          display: "inline-flex", alignItems: "center", justifyContent: "center",
          background: "var(--machine-weak)", color: "var(--machine)" }}>{initials}</span>
        <Text component="span" size="xs" c="dimmed">{name}</Text>
      </span>
    </Tooltip>
  );
}

function Login() {
  const qc = useQueryClient();
  const users = useQuery({ queryKey: ["users"], queryFn: api.listUsers });
  const [who, setWho] = useState<string | null>(null);
  const [err, setErr] = useState("");
  const submit = async () => {
    if (!who) return;
    try { await api.login(who); qc.invalidateQueries(); }
    catch { setErr("Login failed"); }
  };
  return (
    <div style={{ minHeight: "100vh", display: "grid", placeItems: "center" }}>
      <Stack gap="sm" style={{ width: 320 }}>
        <Text fw={700} size="lg">Sign in</Text>
        <Select label="Who are you?" placeholder="Pick your name" searchable
          data={(users.data ?? []).map((u) => ({ value: u.username, label: u.name }))}
          value={who} onChange={setWho} />
        {err && <Text size="xs" c="red">{err}</Text>}
        <Button onClick={submit} disabled={!who}>Enter</Button>
      </Stack>
    </div>
  );
}

export function AuthGate({ children }: { children: React.ReactNode }) {
  const me = useMe();
  if (me.isLoading) return null;
  if (me.data?.required && !me.data.user) return <Login />;
  return <>{children}</>;
}
```

- [ ] **Step 3: main.tsx — wrap App**

In `frontend/src/main.tsx`, import and wrap:

```tsx
import { AuthGate } from "./auth";
```

Change `<App />` (inside `<BrowserRouter>`) to:

```tsx
        <BrowserRouter>
          <AuthGate>
            <App />
          </AuthGate>
        </BrowserRouter>
```

- [ ] **Step 4: App.tsx — user menu + logout in header**

In `frontend/src/App.tsx`, add imports:

```tsx
import { useQueryClient } from "@tanstack/react-query";
import { api } from "./api";
import { useMe, UserChip } from "./auth";
```

Add a component:

```tsx
function UserMenu() {
  const me = useMe();
  const qc = useQueryClient();
  if (!me.data?.user) return null;
  return (
    <Group gap={10} wrap="nowrap">
      <UserChip username={me.data.user.username} />
      <button type="button" className="linklike"
        onClick={async () => { await api.logout(); qc.invalidateQueries(); }}>log out</button>
    </Group>
  );
}
```

In the header `<Group ... justify="flex-end">`, add `<UserMenu />` before the existing "live · refreshes" group.

- [ ] **Step 5: SprintDetail.tsx — vote as current user + show authors**

In `frontend/src/views/SprintDetail.tsx`:
- Import: `import { useMe, UserChip } from "../auth";`
- Where it votes/reads votes, prefer the logged-in username, keeping `voterId()` as the anonymous fallback:

```tsx
  const me = useMe();
  const voter = me.data?.user?.username ?? voterId();
```
  Replace `voterId()` at the `getSprint` call and `voteSprint` call with `voter`.
- Render `<UserChip username={c.by} />` next to each comment `c`, and render the `decisions` trail if present:

```tsx
  {(s.decisions ?? []).map((d, i) => (
    <div key={i} style={{ fontSize: 12, color: "var(--ink-muted)" }}>
      {d.action} by <UserChip username={d.by} /> · <RelTime at={d.at} />
    </div>
  ))}
```

- [ ] **Step 6: ChatView.tsx — show message author**

In `frontend/src/views/ChatView.tsx`, import `UserChip` from `../auth` and, for each user-role message, render `<UserChip username={m.by} />` in the bubble header (PM messages keep the "PM" label).

- [ ] **Step 7: Build to verify it compiles**

Run: `cd frontend && PATH=$HOME/node20/bin:$PATH npm run build`
Expected: `✓ built` with no TypeScript errors.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/api.ts frontend/src/auth.tsx frontend/src/main.tsx frontend/src/App.tsx frontend/src/views/SprintDetail.tsx frontend/src/views/ChatView.tsx
git commit -m "feat(auth): login gate, user menu, and author chips in the dashboard"
```

---

### Task 7: Seed the deployment + gitignore the secret

**Files:**
- Create (on the host substrate): `~/coscience-substrate/.coscience/users.yaml`
- Modify (on the host substrate): `~/coscience-substrate/.gitignore`

**Interfaces:** none (deployment/data).

- [ ] **Step 1: Verify the flag behavior locally first**

Confirm that with no registry the app is open and with a registry it gates — already covered by `tests/test_http_auth.py::test_auth_disabled_when_no_registry` and `::test_gate_blocks_when_seeded`. Re-run:

Run: `~/venvs/coscience/bin/pytest tests/test_http_auth.py -q`
Expected: PASS

- [ ] **Step 2: Deploy the code** (from the code checkout on the host)

Run: `git push rancho main` (local), then on the host `cd ~/coscience && bash scripts/deploy.sh`
Expected: health ok; version = new SHA.

- [ ] **Step 3: Seed the registry on the substrate**

Write `~/coscience-substrate/.coscience/users.yaml` (use the Write tool / editor, not a heredoc):

```yaml
users:
  - username: stroganov
    name: Oleg Stroganov
    initials: OS
  - username: apathak
    name: Aish Pathak
    initials: AP
```

- [ ] **Step 4: Ignore the generated secret in the substrate repo**

Append to `~/coscience-substrate/.gitignore`:

```
# per-deployment session-signing secret — never commit
.coscience/secret
```

Then commit the substrate change:

```bash
cd ~/coscience-substrate && git add .coscience/users.yaml .gitignore && git commit -m "seed user registry (Oleg, Aish); ignore session secret"
```

- [ ] **Step 5: Verify auth is live**

Run (on the host): `curl -s -o /dev/null -w '%{http_code}\n' localhost:8000/api/sprints`
Expected: `401` (gate now active). `curl -s localhost:8000/api/users` lists both users. Then log in via the dashboard (pick a name) and confirm actions attribute correctly.

- [ ] **Step 6: Confirm no restart needed**

`load_users` reads the registry live per request (like the resource pool), so seeding takes effect immediately. Hard-reload the dashboard; the login screen appears.

---

## Notes for the implementer

- Run tests on the Linux host (`~/venvs/coscience/bin/pytest`) — the runtime is Linux-only and there is no Windows venv.
- The `by=""` defaults everywhere are load-bearing for back-compat: old records and direct service calls (existing tests) keep working and render "—".
- Do not add `by` to the `VoteIn` model removal — keep it; it's the anonymous fallback when auth is disabled.
- Keycloak later: replace only `login`/cookie issuance and the body of `current_user` in `auth.py`; every `by=` call site is unchanged.
