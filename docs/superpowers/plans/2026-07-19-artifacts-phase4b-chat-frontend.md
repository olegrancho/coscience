# Artifacts Phase 4b — Chat-Mode Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** From an artifact you can open a chat bound to it; the chat view shows a
split — the conversation beside a live render of the artifact's `work/` — with a
"Save as version" button.

**Architecture:** A small backend helper lists `work/` files (for the live view).
The frontend adds api methods, an "Open chat" action on `ArtifactDetail`, and a
bound split-view in `ChatView` (conversation + live `work/` text/markdown render,
polled, plus a Save button).

**Tech Stack:** React 18 + TS, Mantine v7, react-query, react-router v6; pytest +
`fastapi.testclient` (backend task); vitest (frontend). Build/test on the dev host.

## Global Constraints

- Runtime/build on the **Linux dev host**.
- **Backend for chat binding already exists** (Phase 4a): `create_chat(program,
  artifacts=[...])` (POST `/chats` body `{artifacts}`), `POST /chats/{tid}/save`,
  `GET /artifacts/{aid}/work/{name:path}` (read a live work file), `_chat_public`
  exposes `artifacts`. This phase only adds a `work/`-file **list** helper + the UI.
- **Live render is TEXT/markdown of `work/`** for this phase (manuscript/report/
  data). A `figure`/`page` live-preview is a deferred follow-up — for those kinds
  the panel shows a "Save a version to preview" note + a link to the artifact page.
- Follow existing conventions (`useQuery`/`useMutation`, Mantine tokens); reuse
  `Md`, `ArtifactDetail`'s render idiom, `ChatView`'s message column.

**Base commit for this phase:** current `feat/artifacts` HEAD (Phase 4a complete).

---

### Task 1: Backend — list `work/` files

**Files:**
- Modify: `src/coscience/service.py` (artifacts section — add `list_artifact_work_files`)
- Modify: `src/coscience/http_api.py` (add `GET /programs/{pid}/artifacts/{aid}/work`)
- Test: `tests/test_artifact_work_list.py`

**Interfaces:**
- Produces:
  - `Service.list_artifact_work_files(program_id, aid) -> list[str]` — sorted relative
    paths of files in the artifact's live `work/` dir (`[]` if no `work/`).
  - `GET /programs/{pid}/artifacts/{aid}/work` → that list.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_artifact_work_list.py
from fastapi.testclient import TestClient

from coscience import artifacts
from coscience.http_api import build_app
from coscience.service import Service


def test_list_work_files(substrate):
    artifacts.create_artifact(substrate, "p", "doc", "Doc", "md")
    artifacts.acquire_lock(substrate, "p", ["doc"], "chat", "chat:x", now=1.0)  # seeds work/
    (substrate.artifact_dir("p", "doc") / "work" / "content.md").write_text("hi")
    svc = Service(substrate.repo_root)
    assert svc.list_artifact_work_files("p", "doc") == ["content.md"]


def test_list_work_files_empty_when_no_work(substrate):
    artifacts.create_artifact(substrate, "p", "doc", "Doc", "md")
    svc = Service(substrate.repo_root)
    assert svc.list_artifact_work_files("p", "doc") == []


def test_work_list_route(substrate):
    artifacts.create_artifact(substrate, "p", "doc", "Doc", "md")
    artifacts.acquire_lock(substrate, "p", ["doc"], "chat", "chat:x", now=1.0)
    (substrate.artifact_dir("p", "doc") / "work" / "a.md").write_text("x")
    c = TestClient(build_app(Service(substrate.repo_root)))
    assert c.get("/api/programs/p/artifacts/doc/work").json() == ["a.md"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_artifact_work_list.py -v`
Expected: FAIL — `list_artifact_work_files` missing.

- [ ] **Step 3: Implement**

In `src/coscience/service.py` artifacts section:

```python
    def list_artifact_work_files(self, program_id: str, aid: str) -> list[str]:
        work = self.substrate.artifact_dir(program_id, aid) / "work"
        if not work.is_dir():
            return []
        return sorted(str(p.relative_to(work)) for p in work.rglob("*") if p.is_file())
```

In `src/coscience/http_api.py`, near the other artifact routes (place BEFORE the
`.../work/{name:path}` route so the bare `/work` doesn't get shadowed):

```python
    @api.get("/programs/{program_id}/artifacts/{aid}/work")
    def list_artifact_work(program_id: str, aid: str) -> list[str]:
        return service.list_artifact_work_files(program_id, aid)
```

- [ ] **Step 4: Run to verify it passes** — `python -m pytest tests/test_artifact_work_list.py -v` → 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/coscience/service.py src/coscience/http_api.py tests/test_artifact_work_list.py
git commit -m "feat(artifacts): list a live work/ dir's files (backs the chat split-view)"
```

---

### Task 2: Frontend api — bound chat + save + work read/list

**Files:**
- Modify: `frontend/src/api.ts`
- Test: `frontend/src/api.test.ts` (append)

**Interfaces:**
- Produces (TypeScript):
  - `ChatThread`/`ChatThreadSummary` gain `artifacts: string[]`.
  - `createChat(id, title?, artifacts?)` — send `{title, artifacts}`.
  - `saveChatVersion(id, tid)` → `POST /chats/{tid}/save` → `Record<string, string|null>`.
  - `listArtifactWorkFiles(id, aid)` → `GET /artifacts/{aid}/work` → `string[]`.
  - `readArtifactWorkFile(id, aid, name)` → `GET /artifacts/{aid}/work/{name}` → `ArtifactFileT`.

- [ ] **Step 1: Write the failing test** — append to `frontend/src/api.test.ts`:

```typescript
  it("createChat sends artifacts when bound", async () => {
    const f = mockFetch(201, { id: "c1", artifacts: ["doc"] });
    await api.createChat("p", "edit", ["doc"]);
    const [url, init] = f.mock.calls[0];
    expect(url).toBe("/api/programs/p/chats");
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({ title: "edit", artifacts: ["doc"] });
  });

  it("saveChatVersion POSTs to the save route", async () => {
    const f = mockFetch(200, { doc: "v1" });
    const r = await api.saveChatVersion("p", "c1");
    expect(f).toHaveBeenCalledWith("/api/programs/p/chats/c1/save", expect.objectContaining({ method: "POST" }));
    expect(r).toEqual({ doc: "v1" });
  });

  it("work list + read hit the right paths", async () => {
    const f = mockFetch(200, ["content.md"]);
    await api.listArtifactWorkFiles("p", "doc");
    expect(f).toHaveBeenCalledWith("/api/programs/p/artifacts/doc/work");
  });
```

- [ ] **Step 2: Run to verify it fails** — `npm run test` → FAIL (`createChat` arity / missing methods).

- [ ] **Step 3: Implement** — update the `ChatThread` and `ChatThreadSummary`
interfaces to add `artifacts: string[]`, and update/add these `api` methods
(replace the existing `createChat`):

```typescript
  createChat: (id: string, title = "", artifacts?: string[]) =>
    fetch(`/api/programs/${id}/chats`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title, ...(artifacts && artifacts.length ? { artifacts } : {}) }),
    }).then(j<ChatThread>),
  saveChatVersion: (id: string, tid: string) =>
    fetch(`/api/programs/${id}/chats/${tid}/save`, { method: "POST" }).then(j<Record<string, string | null>>),
  listArtifactWorkFiles: (id: string, aid: string) =>
    fetch(`/api/programs/${id}/artifacts/${aid}/work`).then(j<string[]>),
  readArtifactWorkFile: (id: string, aid: string, name: string) =>
    fetch(`/api/programs/${id}/artifacts/${aid}/work/${name}`).then(j<ArtifactFileT>),
```

(Note the test asserts `createChat("p","edit",["doc"])` sends `{title:"edit",
artifacts:["doc"]}` — the spread includes `artifacts` because the array is
non-empty.)

- [ ] **Step 4: Run to verify it passes** — `npm run test` (new pass) + `npm run build` (clean).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api.ts frontend/src/api.test.ts
git commit -m "feat(artifacts): frontend api — bound chat create + save + work read/list"
```

---

### Task 3: "Open chat" action on ArtifactDetail

**Files:**
- Modify: `frontend/src/views/ArtifactDetail.tsx`
- Test: `frontend/src/views/ArtifactDetail.test.tsx` (append)

**Requirements:**
- Add an **"Open chat"** button to the ArtifactDetail toolbar. On click it
  `useMutation(() => api.createChat(id, `Edit ${art.title||aid}`, [aid]))`; on
  success it navigates to `/programs/${id}/chat` (react-router `useNavigate`) so
  the new bound chat opens in ChatView. On error (e.g. 422 "artifact busy") show a
  Mantine `notifications.show` red toast.
- Disable/hide the button when the artifact is already locked by someone else
  (`art.lock.holder_id` set) — you can't open a second editor; a tooltip explains
  "busy — {holder}".

- [ ] **Step 1: Write the failing test** — append to `ArtifactDetail.test.tsx`:

```typescript
  it("shows an Open chat action", async () => {
    vi.spyOn(api, "getArtifact").mockResolvedValue({
      id: "doc", program: "p", title: "Doc", kind: "md", current: "", archived: false,
      lock: {}, current_files: [], linked_sprints: [], threads: [], versions: [],
    } as any);
    renderAt();
    await waitFor(() => expect(screen.getByText(/open chat/i)).toBeTruthy());
  });
```

- [ ] **Step 2: Run to verify it fails** — `npm run test` → FAIL (no "Open chat").
- [ ] **Step 3: Implement** the button + navigate + busy-gating.
- [ ] **Step 4: Run to verify it passes** — `npm run test` + `npm run build`.
- [ ] **Step 5: Commit**

```bash
git add frontend/src/views/ArtifactDetail.tsx frontend/src/views/ArtifactDetail.test.tsx
git commit -m "feat(artifacts): Open-chat action on ArtifactDetail"
```

---

### Task 4: ChatView bound split-view + Save as version

**Files:**
- Modify: `frontend/src/views/ChatView.tsx`
- Test: `frontend/src/views/ChatView.test.tsx` (create; mirror the ArtifactDetail render-test harness — MantineProvider + QueryClient + MemoryRouter, matchMedia stub)

**Requirements:**
- When the active thread is **bound** (`t.artifacts?.length`), render a **split
  layout**: the existing conversation column on the left, and a right panel that
  shows the live artifact.
- **Right panel:** `useQuery(["work", id, aid], () => api.listArtifactWorkFiles(id, aid),
  { refetchInterval: 3000 })` for the file list; render the first **text** file via
  `useQuery(["workfile", id, aid, name], () => api.readArtifactWorkFile(id, aid, name),
  { refetchInterval: 3000 })` as `<Md>{content}</Md>` (poll so edits appear as the
  agent writes). A header shows the artifact id + a lock/owner note. If the file is
  `binary`, or the artifact kind is figure/page, show "Save a version to preview"
  + a `<Link to={`/programs/${id}/artifacts/${aid}`}>` to the artifact page.
- **"Save as version" button** in the panel: `useMutation(() => api.saveChatVersion(id, active))`;
  on success a Mantine toast "Saved {result}" and invalidate the work queries.
- Unbound chats render exactly as before (no split, no panel).

- [ ] **Step 1: Write the failing test** — create `ChatView.test.tsx`:

```typescript
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import ChatView from "./ChatView";
import { api } from "../api";

beforeEach(() => {
  window.matchMedia = window.matchMedia || ((q: string) => ({
    matches: false, media: q, onchange: null, addListener: () => {}, removeListener: () => {},
    addEventListener: () => {}, removeEventListener: () => {}, dispatchEvent: () => false,
  })) as any;
});

function renderAt() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}><MantineProvider>
      <MemoryRouter initialEntries={["/programs/p/chat"]}>
        <Routes><Route path="/programs/:id/chat" element={<ChatView />} /></Routes>
      </MemoryRouter>
    </MantineProvider></QueryClientProvider>);
}

describe("ChatView bound split-view", () => {
  it("shows the Save as version button for a bound chat", async () => {
    vi.spyOn(api, "getProgram").mockResolvedValue({ id: "p", title: "P" } as any);
    vi.spyOn(api, "listChats").mockResolvedValue([
      { id: "c1", title: "edit doc", scope: "full", created_at: 1, busy: false,
        messages: 0, last_at: 1, artifacts: ["doc"] }] as any);
    vi.spyOn(api, "getChatThread").mockResolvedValue({
      id: "c1", title: "edit doc", scope: "full", created_at: 1, turns_done: 0,
      busy: false, messages: [], live: "", artifacts: ["doc"] } as any);
    vi.spyOn(api, "listArtifactWorkFiles").mockResolvedValue(["content.md"]);
    vi.spyOn(api, "readArtifactWorkFile").mockResolvedValue({ name: "content.md", size: 2, content: "hi", binary: false } as any);
    renderAt();
    await waitFor(() => expect(screen.getByText(/save as version/i)).toBeTruthy());
  });
});
```

- [ ] **Step 2: Run to verify it fails** — `npm run test` → FAIL (no split / button).
- [ ] **Step 3: Implement** the bound split-view + Save button (unbound path unchanged).
- [ ] **Step 4: Run to verify it passes** — `npm run test` + `npm run build`.
- [ ] **Step 5: Run the full frontend suite + commit**

Run (host): `PATH=$HOME/node20/bin:$PATH npm run test && npm run build`.

```bash
git add frontend/src/views/ChatView.tsx frontend/src/views/ChatView.test.tsx
git commit -m "feat(artifacts): ChatView bound split-view + Save as version"
```

---

## Phase 4b Done — What Exists Now

From an artifact you can Open a chat bound to it; the chat view splits into the
conversation and a live `work/` render (text/markdown, polled) with a Save-as-
version button. Mode 2 (interactive editing) is usable end-to-end.

**Deferred:** figure/page live-preview inside the split (save-then-view works
today). **Next: Phase 5** — PM `artifact_feedback` in context + `artifact_task` →
proposed sprint (comment-on-artifact → PM proposes a fix; ask-PM-to-create).
