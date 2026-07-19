# Artifacts Phase 3b — Frontend UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** The dashboard shows a program's artifacts, opens an artifact to view the
current version (by kind), browse/revert/archive the version tree, comment (→ PM),
download, and see sprint↔artifact links; and sprint create/edit can bind existing
artifacts and declare new ones.

**Architecture:** New api-client methods + types (`api.ts`); a pure version-tree
layout helper (`artifactTree.ts`) with vitest; a new `ArtifactDetail` route/view
(`/programs/:id/artifacts/:aid`); an Artifacts section on `ProgramDetail`; and
cross-links + bind/create fields on `SprintDetail`/the sprint modals. Reuses
`Md`, `FeedbackThread`, `RelTime`/`UserChip` (`components/ui`, `auth`), Mantine v7,
`@tanstack/react-query`.

**Tech Stack:** React 18 + TypeScript, Mantine v7, react-query, react-router v6,
react-markdown, vitest + @testing-library/react (jsdom). Build = `npm run build`
(`tsc -b && vite build`); tests = `npm run test` (vitest). Both run on the **dev
host** (`~/coscience-dev/frontend`, `PATH=$HOME/node20/bin:$PATH`).

## Global Constraints

- Runtime/build is on the **Linux dev host**; the Windows controller does not run npm.
- **API base is `/api`** (all fetch paths are `/api/programs/{pid}/artifacts/...`).
- **Interactive `page` artifacts render in a `<iframe sandbox>` WITHOUT
  `allow-same-origin`** (may include `allow-scripts`), `src` = the page-serve
  endpoint. Never render agent HTML inline in the app DOM.
- **Comment threads reuse `<FeedbackThread>`** (its props: `thread, onReply,
  onComplete, onReopen, onDelete, onSeen, respondsNow`). Artifact threads are
  always `target:"pm"`.
- **Revert/archive are the only mutations** the UI issues on versions — no hard
  delete. Archive is the "discard" action (reversible).
- Follow existing view conventions: `useQuery`/`useMutation` with
  `queryClient.invalidateQueries`, Mantine components, `var(--...)` design tokens.
  Read `views/SprintDetail.tsx` and `views/ResultDetail.tsx` as the reference
  patterns for a detail page (data loading, layout, mutations, `<Md>` usage).
- **Every task ends green on BOTH** `npm run build` (no TS errors) and `npm run
  test` (vitest) on the dev host.

**Base commit for this phase:** current `feat/artifacts` HEAD (Phase 3a complete).

---

### Task 1: api client — artifact types + methods

**Files:**
- Modify: `frontend/src/api.ts`
- Test: `frontend/src/api.test.ts` (append)

**Interfaces:**
- Produces (TypeScript):
  - `ArtifactVersionT { id: string; parent: string; created_at: number; created_by: string; archived: boolean; note: string }`
  - `ArtifactLock { holder_kind?: string; holder_id?: string; acquired_at?: number; last_activity?: number }`
  - `ArtifactRow { id: string; title: string; kind: string; current: string; archived: boolean; lock: ArtifactLock; version_count: number }`
  - `LinkedSprint { id: string; status: string; title: string }`
  - `ArtifactDetailT { id: string; program: string; title: string; kind: string; current: string; archived: boolean; lock: ArtifactLock; versions: ArtifactVersionT[]; threads: FeedbackThreadT[]; current_files: string[]; linked_sprints: LinkedSprint[] }`
  - `ArtifactFileT { name: string; size: number; content: string; binary: boolean }`
  - api methods: `listArtifacts(pid)`, `getArtifact(pid,aid)`, `readArtifactFile(pid,aid,vid,name)`, `revertArtifact(pid,aid,vid)`, `archiveArtifact(pid,aid,archived)`, `archiveArtifactVersion(pid,aid,vid,archived)`, `addArtifactComment(pid,aid,text,threadId?)`, `completeArtifactThread(pid,aid,tid)`, `reopenArtifactThread(pid,aid,tid)`, `seenArtifactThread(pid,aid,tid)`, `deleteArtifactThread(pid,aid,tid)`.
  - Non-fetch URL helpers (used as `<img src>` / `<iframe src>` / download `href`): `artifactDownloadUrl(pid,aid,vid)`, `artifactPageUrl(pid,aid,vid,path)`.

- [ ] **Step 1: Write the failing test** — append to `frontend/src/api.test.ts`:

```typescript
  it("getArtifact hits the prefixed path", async () => {
    const f = mockFetch(200, { id: "doc", program: "p", title: "Doc", kind: "md",
      current: "v1", archived: false, lock: {}, versions: [], threads: [],
      current_files: ["content.md"], linked_sprints: [] });
    const d = await api.getArtifact("p", "doc");
    expect(f).toHaveBeenCalledWith("/api/programs/p/artifacts/doc");
    expect(d.current).toBe("v1");
  });

  it("revertArtifact POSTs the vid", async () => {
    const f = mockFetch(200, { id: "doc", current: "v1" });
    await api.revertArtifact("p", "doc", "v1");
    const [url, init] = f.mock.calls[0];
    expect(url).toBe("/api/programs/p/artifacts/doc/revert");
    expect((init as RequestInit).method).toBe("POST");
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({ vid: "v1" });
  });

  it("addArtifactComment POSTs text + thread_id", async () => {
    const f = mockFetch(201, { id: "t1" });
    await api.addArtifactComment("p", "doc", "tighten intro");
    const [url, init] = f.mock.calls[0];
    expect(url).toBe("/api/programs/p/artifacts/doc/comments");
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({ text: "tighten intro", thread_id: "" });
  });

  it("download/page url helpers build the right paths", () => {
    expect(api.artifactDownloadUrl("p", "doc", "v2")).toBe("/api/programs/p/artifacts/doc/versions/v2/download");
    expect(api.artifactPageUrl("p", "site", "v1", "index.html")).toBe("/api/programs/p/artifacts/site/versions/v1/page/index.html");
  });
```

- [ ] **Step 2: Run to verify it fails**

Run (on host): `cd ~/coscience-dev/frontend && PATH=$HOME/node20/bin:$PATH npm run test`
Expected: FAIL — `api.getArtifact is not a function`.

- [ ] **Step 3: Implement** — add the interfaces near the other artifact-adjacent
interfaces in `frontend/src/api.ts`, and these methods inside the `api` object
(before the closing `};`). Follow the exact style of the existing methods
(`j<T>`, template paths, `encodeURIComponent` on free path segments like file names):

```typescript
export interface ArtifactVersionT { id: string; parent: string; created_at: number; created_by: string; archived: boolean; note: string }
export interface ArtifactLock { holder_kind?: string; holder_id?: string; acquired_at?: number; last_activity?: number }
export interface ArtifactRow { id: string; title: string; kind: string; current: string; archived: boolean; lock: ArtifactLock; version_count: number }
export interface LinkedSprint { id: string; status: string; title: string }
export interface ArtifactDetailT {
  id: string; program: string; title: string; kind: string; current: string;
  archived: boolean; lock: ArtifactLock; versions: ArtifactVersionT[];
  threads: FeedbackThreadT[]; current_files: string[]; linked_sprints: LinkedSprint[];
}
export interface ArtifactFileT { name: string; size: number; content: string; binary: boolean }
```

```typescript
  listArtifacts: (pid: string) => fetch(`/api/programs/${pid}/artifacts`).then(j<ArtifactRow[]>),
  getArtifact: (pid: string, aid: string) =>
    fetch(`/api/programs/${pid}/artifacts/${aid}`).then(j<ArtifactDetailT>),
  readArtifactFile: (pid: string, aid: string, vid: string, name: string) =>
    fetch(`/api/programs/${pid}/artifacts/${aid}/versions/${vid}/files/${encodeURIComponent(name)}`).then(j<ArtifactFileT>),
  revertArtifact: (pid: string, aid: string, vid: string) =>
    fetch(`/api/programs/${pid}/artifacts/${aid}/revert`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ vid }),
    }).then(j<ArtifactDetailT>),
  archiveArtifact: (pid: string, aid: string, archived: boolean) =>
    fetch(`/api/programs/${pid}/artifacts/${aid}/archive`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ archived }),
    }).then(j<ArtifactDetailT>),
  archiveArtifactVersion: (pid: string, aid: string, vid: string, archived: boolean) =>
    fetch(`/api/programs/${pid}/artifacts/${aid}/versions/${vid}/archive`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ archived }),
    }).then(j<ArtifactDetailT>),
  addArtifactComment: (pid: string, aid: string, text: string, threadId?: string) =>
    fetch(`/api/programs/${pid}/artifacts/${aid}/comments`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, thread_id: threadId ?? "" }),
    }).then(j<FeedbackThreadT>),
  completeArtifactThread: (pid: string, aid: string, tid: string) =>
    fetch(`/api/programs/${pid}/artifacts/${aid}/threads/${tid}/complete`, { method: "POST" }).then(j<FeedbackThreadT>),
  reopenArtifactThread: (pid: string, aid: string, tid: string) =>
    fetch(`/api/programs/${pid}/artifacts/${aid}/threads/${tid}/reopen`, { method: "POST" }).then(j<FeedbackThreadT>),
  seenArtifactThread: (pid: string, aid: string, tid: string) =>
    fetch(`/api/programs/${pid}/artifacts/${aid}/threads/${tid}/seen`, { method: "POST" }).then(j<FeedbackThreadT>),
  deleteArtifactThread: (pid: string, aid: string, tid: string) =>
    fetch(`/api/programs/${pid}/artifacts/${aid}/threads/${tid}`, { method: "DELETE" }).then(j<void>),
  artifactDownloadUrl: (pid: string, aid: string, vid: string) =>
    `/api/programs/${pid}/artifacts/${aid}/versions/${vid}/download`,
  artifactPageUrl: (pid: string, aid: string, vid: string, path: string) =>
    `/api/programs/${pid}/artifacts/${aid}/versions/${vid}/page/${path}`,
```

- [ ] **Step 4: Run to verify it passes**

Run (on host): `PATH=$HOME/node20/bin:$PATH npm run test` → new tests pass;
`PATH=$HOME/node20/bin:$PATH npm run build` → no TS errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api.ts frontend/src/api.test.ts
git commit -m "feat(artifacts): frontend api client — artifact types + methods"
```

---

### Task 2: version-tree layout helper

**Files:**
- Create: `frontend/src/components/artifactTree.ts`
- Test: `frontend/src/components/artifactTree.test.ts`

**Interfaces:**
- Consumes: `ArtifactVersionT` (Task 1).
- Produces:
  - `interface TreeRow { v: ArtifactVersionT; depth: number; onCurrentPath: boolean }`
  - `buildArtifactTree(versions: ArtifactVersionT[], current: string): TreeRow[]` — a pre-order DFS from roots (`parent===""`), children in `id` order; `depth` = distance from root; `onCurrentPath` = true if the version is an ancestor of (or equal to) `current`. Tolerates an empty list (`[]`) and orphaned parents (treated as roots).

- [ ] **Step 1: Write the failing test**

```typescript
// frontend/src/components/artifactTree.test.ts
import { describe, expect, it } from "vitest";
import { buildArtifactTree } from "./artifactTree";
import type { ArtifactVersionT } from "../api";

const v = (id: string, parent = ""): ArtifactVersionT =>
  ({ id, parent, created_at: 0, created_by: "", archived: false, note: "" });

describe("buildArtifactTree", () => {
  it("empty list -> empty", () => {
    expect(buildArtifactTree([], "")).toEqual([]);
  });

  it("linear chain has increasing depth, all on current path when current is the leaf", () => {
    const rows = buildArtifactTree([v("v1"), v("v2", "v1"), v("v3", "v2")], "v3");
    expect(rows.map((r) => r.v.id)).toEqual(["v1", "v2", "v3"]);
    expect(rows.map((r) => r.depth)).toEqual([0, 1, 2]);
    expect(rows.every((r) => r.onCurrentPath)).toBe(true);
  });

  it("branch: only the ancestors of current are on the path", () => {
    // v1 -> v2 ; v1 -> v3 (branch); current = v2
    const rows = buildArtifactTree([v("v1"), v("v2", "v1"), v("v3", "v1")], "v2");
    const byId = Object.fromEntries(rows.map((r) => [r.v.id, r]));
    expect(byId["v1"].onCurrentPath).toBe(true);
    expect(byId["v2"].onCurrentPath).toBe(true);
    expect(byId["v3"].onCurrentPath).toBe(false);
    expect(byId["v3"].depth).toBe(1);
  });

  it("orphaned parent is treated as a root", () => {
    const rows = buildArtifactTree([v("v2", "gone")], "v2");
    expect(rows.map((r) => r.v.id)).toEqual(["v2"]);
    expect(rows[0].depth).toBe(0);
  });
});
```

- [ ] **Step 2: Run to verify it fails** — `npm run test` → FAIL (module missing).

- [ ] **Step 3: Implement** — `frontend/src/components/artifactTree.ts`:

```typescript
import type { ArtifactVersionT } from "../api";

export interface TreeRow { v: ArtifactVersionT; depth: number; onCurrentPath: boolean }

export function buildArtifactTree(versions: ArtifactVersionT[], current: string): TreeRow[] {
  const byId = new Map(versions.map((v) => [v.id, v]));
  const ids = new Set(byId.keys());
  // ancestors of `current` (inclusive) — the highlighted path
  const path = new Set<string>();
  let cur: string | undefined = current;
  while (cur && byId.has(cur) && !path.has(cur)) {
    path.add(cur);
    cur = byId.get(cur)!.parent;
  }
  // children index; a version whose parent is missing is a root
  const children = new Map<string, ArtifactVersionT[]>();
  const roots: ArtifactVersionT[] = [];
  for (const v of versions) {
    if (v.parent && ids.has(v.parent)) {
      if (!children.has(v.parent)) children.set(v.parent, []);
      children.get(v.parent)!.push(v);
    } else {
      roots.push(v);
    }
  }
  const byIdOrder = (a: ArtifactVersionT, b: ArtifactVersionT) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0);
  const out: TreeRow[] = [];
  const walk = (v: ArtifactVersionT, depth: number) => {
    out.push({ v, depth, onCurrentPath: path.has(v.id) });
    for (const c of (children.get(v.id) ?? []).slice().sort(byIdOrder)) walk(c, depth + 1);
  };
  for (const r of roots.slice().sort(byIdOrder)) walk(r, 0);
  return out;
}
```

- [ ] **Step 4: Run to verify it passes** — `npm run test` (4 pass) + `npm run build` (clean).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/artifactTree.ts frontend/src/components/artifactTree.test.ts
git commit -m "feat(artifacts): version-tree layout helper"
```

---

### Task 3: ArtifactDetail view — render current + version tree

**Files:**
- Create: `frontend/src/views/ArtifactDetail.tsx`
- Modify: `frontend/src/App.tsx` (add the route)
- Test: `frontend/src/views/ArtifactDetail.test.tsx`

**Interfaces:**
- Consumes: `api.getArtifact/readArtifactFile/revertArtifact/archiveArtifact/archiveArtifactVersion/artifactDownloadUrl/artifactPageUrl` (Task 1); `buildArtifactTree` (Task 2); `Md`, `RelTime`, `UserChip`.
- Produces: a route `/programs/:id/artifacts/:aid` rendering the artifact.

**Requirements (build from `SprintDetail.tsx`/`ResultDetail.tsx` conventions):**
- Load with `useQuery(["artifact", id, aid], () => api.getArtifact(id, aid))`.
  Loading + error states like the reference views.
- **Header:** title, `kind` badge, a `<Link to={`/programs/${id}`}>` back link, and
  a **lock/owner banner** when `art.lock.holder_id` is set: e.g. "🔒 held by
  {lock.holder_kind} {lock.holder_id}".
- **Current-version render**, by `art.kind`, of the `current` version:
  - `md` (or any text file): `useQuery` `readArtifactFile(id, aid, current, current_files[0])`; render `<Md>{content}</Md>` (or a "no content yet" note when `current===""`/no files).
  - `figure`: `<img src={api.artifactDownloadUrl(id, aid, current)} style={{ maxWidth: "100%" }} />`.
  - `page`: `<iframe title="artifact page" sandbox="allow-scripts" src={api.artifactPageUrl(id, aid, current, "index.html")} style={{ width: "100%", height: 520, border: "1px solid var(--hairline)", borderRadius: 8 }} />` — **`sandbox` must NOT include `allow-same-origin`**.
  - `data`: read the first current file; show `<pre>` of the (possibly truncated) content, or a "binary — download to view" note when `binary`.
- **Version-tree sidebar:** `buildArtifactTree(art.versions, art.current)` → one row per
  version, indented by `depth`, marked when `onCurrentPath`, showing `id`,
  `created_by`, `RelTime` of `created_at`, and `note`; archived rows dimmed.
  Each non-current row: a **"View/Revert"** action → `useMutation(api.revertArtifact)`
  (invalidate `["artifact", id, aid]`). Each row: an **archive/unarchive** toggle
  → `api.archiveArtifactVersion`. The `current` row is marked "current".
- **Toolbar:** a **Download** link (`<a href={api.artifactDownloadUrl(id, aid, current)}>`),
  and a **Discard** button that `api.archiveArtifact(id, aid, true)` after a
  `window.confirm` (and, if already archived, an **Un-discard** that sets it false).
- All mutations invalidate the artifact query; use Mantine `Button`/`ActionIcon`/`Badge`/`Group`/`Stack`.

**Route:** in `App.tsx`, add
`<Route path="/programs/:id/artifacts/:aid" element={<ArtifactDetail />} />`
(import it alongside the other views) and extend `activeSection` so a path under
`/programs` keeps the Programs rail active (it already matches `startsWith("/programs")`).

- [ ] **Step 1: Write the failing test** (render smoke test; mirror `LineageCard.test.tsx` — it stubs `window.matchMedia` for MantineProvider under jsdom):

```typescript
// frontend/src/views/ArtifactDetail.test.tsx
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import ArtifactDetail from "./ArtifactDetail";
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
    <QueryClientProvider client={qc}>
      <MantineProvider>
        <MemoryRouter initialEntries={["/programs/p/artifacts/doc"]}>
          <Routes><Route path="/programs/:id/artifacts/:aid" element={<ArtifactDetail />} /></Routes>
        </MemoryRouter>
      </MantineProvider>
    </QueryClientProvider>);
}

describe("ArtifactDetail", () => {
  it("renders the title and the version tree", async () => {
    vi.spyOn(api, "getArtifact").mockResolvedValue({
      id: "doc", program: "p", title: "Manuscript", kind: "md", current: "v2",
      archived: false, lock: {}, current_files: ["content.md"], linked_sprints: [],
      threads: [],
      versions: [
        { id: "v1", parent: "", created_at: 1, created_by: "human", archived: false, note: "first" },
        { id: "v2", parent: "v1", created_at: 2, created_by: "chat:x", archived: false, note: "" },
      ],
    } as any);
    vi.spyOn(api, "readArtifactFile").mockResolvedValue({ name: "content.md", size: 5, content: "hello", binary: false } as any);
    renderAt();
    await waitFor(() => expect(screen.getByText("Manuscript")).toBeTruthy());
    expect(screen.getByText("v1")).toBeTruthy();
    expect(screen.getByText("v2")).toBeTruthy();
  });

  it("shows the lock/owner banner when held", async () => {
    vi.spyOn(api, "getArtifact").mockResolvedValue({
      id: "doc", program: "p", title: "Doc", kind: "md", current: "", archived: false,
      lock: { holder_kind: "sprint", holder_id: "s1" }, current_files: [], linked_sprints: [],
      threads: [], versions: [],
    } as any);
    renderAt();
    await waitFor(() => expect(screen.getByText(/s1/)).toBeTruthy());
  });
});
```

- [ ] **Step 2: Run to verify it fails** — `npm run test` → FAIL (module missing).
- [ ] **Step 3: Implement** the view + route per the Requirements above.
- [ ] **Step 4: Run to verify it passes** — `npm run test` (2 pass) + `npm run build` (clean).
- [ ] **Step 5: Commit**

```bash
git add frontend/src/views/ArtifactDetail.tsx frontend/src/App.tsx frontend/src/views/ArtifactDetail.test.tsx
git commit -m "feat(artifacts): ArtifactDetail view — render current + version tree"
```

---

### Task 4: ArtifactDetail — comments + linked sprints

**Files:**
- Modify: `frontend/src/views/ArtifactDetail.tsx`
- Test: `frontend/src/views/ArtifactDetail.test.tsx` (append)

**Requirements:**
- **Comment panel** (below or beside the render): map `art.threads` to
  `<FeedbackThread>` with handlers wired to the artifact-thread api:
  `onReply=(text)=>api.addArtifactComment(id, aid, text, thread.id)`,
  `onComplete=()=>api.completeArtifactThread(id, aid, thread.id)`, `onReopen`,
  `onSeen`, `onDelete=()=>api.deleteArtifactThread(id, aid, thread.id)`; pass
  `respondsNow={false}` (the PM reacts on its next cycle, not instantly). A
  new-comment `Textarea` + Send → `api.addArtifactComment(id, aid, text)`. All
  handlers invalidate `["artifact", id, aid]`. This mirrors how `SprintDetail`
  wires `<FeedbackThread>` — read it for the exact mutation/invalidate shape.
- **Linked sprints:** render `art.linked_sprints` as a small list of
  `<Link to={`/sprints/${s.id}`}>` with the sprint's `status`; "No sprints linked"
  when empty.

- [ ] **Step 1: Write the failing test** — append:

```typescript
  it("renders an existing comment thread and the linked sprint", async () => {
    vi.spyOn(api, "getArtifact").mockResolvedValue({
      id: "doc", program: "p", title: "Doc", kind: "md", current: "", archived: false,
      lock: {}, current_files: [], versions: [],
      linked_sprints: [{ id: "p-c1-x", status: "queued", title: "Draft" }],
      threads: [{ id: "t1", target: "pm", status: "open", agent_unseen: false, created_at: 1,
                  messages: [{ role: "human", text: "tighten intro", by: "oleg", at: 1 }] }],
    } as any);
    renderAt();
    await waitFor(() => expect(screen.getByText("tighten intro")).toBeTruthy());
    expect(screen.getByText(/p-c1-x/)).toBeTruthy();
  });
```

- [ ] **Step 2: Run to verify it fails** — `npm run test` (new assertion fails: thread/link not rendered).
- [ ] **Step 3: Implement** the comment panel + linked-sprints list.
- [ ] **Step 4: Run to verify it passes** — `npm run test` (3 pass) + `npm run build`.
- [ ] **Step 5: Commit**

```bash
git add frontend/src/views/ArtifactDetail.tsx frontend/src/views/ArtifactDetail.test.tsx
git commit -m "feat(artifacts): ArtifactDetail comments + linked sprints"
```

---

### Task 5: Artifacts section on ProgramDetail

**Files:**
- Modify: `frontend/src/views/ProgramDetail.tsx`
- Test: (covered by `npm run build`; add a vitest render test only if ProgramDetail already has one)

**Requirements:**
- Add an **Artifacts** section to the program page (a card/panel consistent with
  the program's existing sections — read `ProgramDetail.tsx` for its section
  layout). Load with `useQuery(["artifacts", id], () => api.listArtifacts(id))`.
- Render a **grid/list** of the program's artifacts: each shows `title`, a `kind`
  badge, `current` version id, `version_count`, and a **lock badge** when
  `a.lock.holder_id` is set; the whole row/card is a
  `<Link to={`/programs/${id}/artifacts/${a.id}`}>`.
- Empty state: "No artifacts yet." Keep it visually consistent with the other
  program sections (Mantine, design tokens).

- [ ] **Step 1: Implement** the Artifacts section (no new logic module → verify via build; if a `ProgramDetail.test.tsx` exists, add a case that mocks `api.listArtifacts` and asserts a title renders).
- [ ] **Step 2: Run** — `npm run build` (clean) + `npm run test` (all green).
- [ ] **Step 3: Commit**

```bash
git add frontend/src/views/ProgramDetail.tsx
git commit -m "feat(artifacts): Artifacts section on the program page"
```

---

### Task 6: Sprint cross-links + bind/create fields

**Files:**
- Modify: `frontend/src/views/SprintDetail.tsx` (show bound/created artifacts)
- Modify: `frontend/src/components/ProposeSprintModal.tsx` and/or `frontend/src/components/SprintEditModal.tsx` (bind/create inputs)
- Modify: `frontend/src/api.ts` (extend `submitSprint` body typing + `SprintPatch` if edit supports it) and `frontend/src/api.test.ts`
- Test: `frontend/src/api.test.ts` (append)

**Requirements:**
- `SprintDetail`: the sprint dict now includes `artifacts_bound: string[]` and
  `artifacts_create: {aid,title,kind}[]` (Phase 3a). Show them as a small
  "Artifacts" block: bound ids as `<Link to={`/programs/${program}/artifacts/${aid}`}>`,
  and created specs as "{title} ({kind}) — will be created". Add the fields to the
  `Sprint` interface in `api.ts`.
- **Create modal** (`ProposeSprintModal`): add optional inputs — a multi-select /
  comma list to **bind** existing artifacts (fetch `api.listArtifacts(program)` to
  offer choices), and a small repeater to **declare artifacts to create**
  (`{aid,title,kind}` rows, `kind` a Select of md/data/figure/page). Include them
  in the `submitSprint` body. Keep it optional and unobtrusive — an empty section
  by default.
- Extend `api.submitSprint`'s body type with `artifacts_bound?: string[]` and
  `artifacts_create?: {aid:string;title:string;kind:string}[]` and forward them.

- [ ] **Step 1: Write the failing test** — append to `api.test.ts`:

```typescript
  it("submitSprint forwards artifacts_bound/create", async () => {
    const f = mockFetch(201, { id: "s1" });
    await api.submitSprint({ id: "s1", goals: "g", plan: ["x"], program: "p",
      artifacts_bound: ["doc"], artifacts_create: [{ aid: "fig", title: "Fig", kind: "figure" }] });
    const body = JSON.parse((f.mock.calls[0][1] as RequestInit).body as string);
    expect(body.artifacts_bound).toEqual(["doc"]);
    expect(body.artifacts_create).toEqual([{ aid: "fig", title: "Fig", kind: "figure" }]);
  });
```

- [ ] **Step 2: Run to verify it fails** — `npm run test` (TS error / assertion fails until the body type + forwarding exist).
- [ ] **Step 3: Implement** the `Sprint` interface fields, `submitSprint` body extension, the `SprintDetail` block, and the modal inputs.
- [ ] **Step 4: Run to verify it passes** — `npm run test` (green) + `npm run build` (clean).
- [ ] **Step 5: Run the full frontend suite + commit**

Run (host): `PATH=$HOME/node20/bin:$PATH npm run test && PATH=$HOME/node20/bin:$PATH npm run build`
Expected: all vitest green, build clean.

```bash
git add frontend/src/views/SprintDetail.tsx frontend/src/components/ProposeSprintModal.tsx frontend/src/components/SprintEditModal.tsx frontend/src/api.ts frontend/src/api.test.ts
git commit -m "feat(artifacts): sprint artifact cross-links + bind/create modal fields"
```

---

## Phase 3b Done — What Exists Now

The dashboard now surfaces artifacts end-to-end: an Artifacts section on the
program page; an ArtifactDetail page that renders the current version by kind
(markdown, figure, sandboxed interactive page, data), a version-tree sidebar with
revert + archive, download, discard, a lock/owner banner, comment threads (→ PM),
and sprint cross-links; and sprint create/edit can bind existing artifacts and
declare new ones.

**Next: Phase 4** (chat mode bound to an artifact + inactivity reaper wired to the
loop; add the deferred holder-ownership guard to `release_lock`). Then **Phase 5**
(PM `artifact_feedback` + `artifact_task`). Deferred UI nicety carried forward: a
"current" download alias and browsing files of a non-current version.
