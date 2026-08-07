# Figure Description Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `figure` artifact carries a `description.md` next to its image, rendered under the image on the artifact page and in the bound-chat preview, and quoted on the program card.

**Architecture:** Pure convention — one filename, `description.md`, living inside the version directory (and its `work/` copy) beside the image. No model field, no API route, no migration. Two backend touches (the card's excerpt, and prompt text telling agents to write one) and two frontend touches (the two places a figure is displayed).

**Tech Stack:** Python 3.12 + FastAPI backend (`src/coscience/`), pytest. React 18 + Mantine 7 + TanStack Query frontend (`frontend/`), vitest + testing-library.

**Spec:** `docs/superpowers/specs/2026-08-04-figure-description-design.md`

## Global Constraints

- The filename is exactly `description.md`. No fallback to `README.md`, `notes.md` or anything else.
- The filename lives as ONE constant per side: `DESCRIPTION_FILE` in `src/coscience/artifacts.py`, `DESCRIPTION_FILE` exported from `frontend/src/components/ui.tsx`. Never inline the string anywhere else.
- **No enforcement.** A figure version with no description still cuts, still renders, still downloads. The only consequence of a missing description is a dimmed line in the UI.
- Existing behaviour for non-figure kinds (`md`, `data`, `page`) must not change.
- Run Python tests with `~/venvs/coscience/bin/python -m pytest` (this host's venv; it has no `pip`, only `pip3`).
- Run frontend tests with `cd frontend && npm test` (vitest run).
- Never `git push` — commit only.

---

### Task 1: The convention constant and the program card's excerpt

`Service._artifact_excerpt` currently short-circuits `kind == "figure"` to `""`. Make a figure's only excerpt candidate its `description.md`.

**Files:**
- Modify: `src/coscience/artifacts.py` (add constants after the imports, above `class ArtifactBusy`)
- Modify: `src/coscience/service.py:1174-1192` (`_artifact_excerpt`) and the import block at `src/coscience/service.py:13-19`
- Test: `tests/test_http_artifacts_read.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `coscience.artifacts.DESCRIPTION_FILE: str` (`"description.md"`) and `coscience.artifacts.FIGURE_DESCRIPTION_NOTE: str` (one paragraph of prose, no leading/trailing newline), both used by Task 2.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_http_artifacts_read.py`:

```python
def test_figure_excerpt_comes_from_its_description(substrate):
    """A figure's card used to be blank — its only quotable file is description.md."""
    artifacts.create_artifact(substrate, "p", "fig", "fig", "figure")
    work = artifacts.seed_work(substrate, "p", "fig")
    (work / "plot.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    (work / "description.md").write_text("Gap size against prime index; log-log axes.")
    artifacts.cut_version(substrate, "p", "fig", "human", now=1.0)
    c = _client(substrate)

    row = {a["id"]: a for a in c.get("/api/programs/p/artifacts").json()}["fig"]
    assert "log-log axes" in row["excerpt"]


def test_figure_excerpt_ignores_prose_under_another_name(substrate):
    """Exactly one filename renders, so a README doesn't quietly stand in for it."""
    artifacts.create_artifact(substrate, "p", "fig", "fig", "figure")
    work = artifacts.seed_work(substrate, "p", "fig")
    (work / "plot.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    (work / "README.md").write_text("how to regenerate this plot")
    artifacts.cut_version(substrate, "p", "fig", "human", now=1.0)
    c = _client(substrate)

    row = {a["id"]: a for a in c.get("/api/programs/p/artifacts").json()}["fig"]
    assert row["excerpt"] == ""
```

- [ ] **Step 2: Run them to verify they fail**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_http_artifacts_read.py -v -k figure_excerpt`
Expected: `test_figure_excerpt_comes_from_its_description` FAILS (`assert "log-log axes" in ""`). `test_figure_excerpt_ignores_prose_under_another_name` passes already — it is the guard that the change doesn't over-reach.

- [ ] **Step 3: Add the constants**

In `src/coscience/artifacts.py`, after the `from coscience.models import ...` line and before `class ArtifactBusy`:

```python
# A figure artifact's caption rides inside the version under exactly this name, so it
# versions with the image instead of drifting from it. One name, no fallbacks: a wrong
# name shows a visible "no description yet" line rather than silently standing in.
DESCRIPTION_FILE = "description.md"

FIGURE_DESCRIPTION_NOTE = (
    f"A figure artifact is the image PLUS a `{DESCRIPTION_FILE}` beside it in the same "
    "directory: what the figure shows, its axes and units, and what a reader should "
    "conclude from it. Write both — an image with no description is half an artifact."
)
```

- [ ] **Step 4: Use it in the excerpt**

In `src/coscience/service.py`, add to the import block at the top (beside `from coscience import graph, threads`):

```python
from coscience.artifacts import DESCRIPTION_FILE, FIGURE_DESCRIPTION_NOTE
```

(`artifacts.py` imports only `coscience.models`, so this creates no cycle. The rest of `service.py` keeps its existing in-method `from coscience import artifacts` imports — do not touch those.)

Then replace `_artifact_excerpt`'s docstring and guard (`src/coscience/service.py:1174-1181`) so the body reads:

```python
    def _artifact_excerpt(self, program_id: str, art, files: list[str]) -> str:
        """The opening of an artifact's current text file, for overview thumbnails.
        A figure's only quotable file is its description.md, so that is its sole
        candidate. Other kinds try candidates in turn rather than trusting the first
        name: a code artifact's alphabetically-first file is often a build leftover
        (a .pyc under __pycache__), which would leave the card blank."""
        if not files or not art.current:
            return ""
        candidates = ([f for f in files if f == DESCRIPTION_FILE] if art.kind == "figure"
                      else self._excerpt_candidates(files))
        for name in candidates:
```

Everything from `try:` down to the closing `return ""` stays exactly as it is — the binary guard and `_THUMB_CHARS` truncation are reused unchanged.

- [ ] **Step 5: Run the artifact-read tests**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_http_artifacts_read.py -v`
Expected: all PASS, including the pre-existing `test_list_artifacts_carries_thumbnail_data` whose figure has no description and must still assert `excerpt == ""`.

- [ ] **Step 6: Run the whole Python suite**

Run: `~/venvs/coscience/bin/python -m pytest`
Expected: PASS. (No other test asserts on a figure's excerpt.)

- [ ] **Step 7: Commit**

```bash
git add src/coscience/artifacts.py src/coscience/service.py tests/test_http_artifacts_read.py
git commit -m "feat(artifacts): quote a figure's description.md on its card

A figure's overview card carried a 44px thumb and nothing else, because
_artifact_excerpt treated figures as unquotable. Its description.md — the
convention this series introduces — is exactly the thing worth quoting, and the
only candidate considered for a figure, so a README can't stand in for one.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Tell the agents to write it

Three prompt sites, no validation anywhere.

**Files:**
- Modify: `src/coscience/claude_executor.py:46-55` (the sprint worker's artifacts section)
- Modify: `src/coscience/service.py:819-822` (the bound-chat `[ARTIFACT]` preamble), plus a new `_artifact_kind` helper beside `get_artifact`
- Modify: `src/coscience/pm_claude.py` (the ARTIFACTS guidance bullet)
- Test: `tests/test_claude_executor.py`, `tests/test_chat_bound_turn.py`

**Interfaces:**
- Consumes: `coscience.artifacts.FIGURE_DESCRIPTION_NOTE` from Task 1; in `service.py` it is already imported by Task 1's import line.
- Produces: `Service._artifact_kind(program_id: str, aid: str) -> str` — the artifact's `kind`, or `""` when no such artifact exists yet.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_claude_executor.py`:

```python
def test_instructions_ask_a_figure_for_a_description(tmp_path):
    ctx = ExecutionContext(artifacts=[
        {"aid": "gap-plot", "kind": "figure", "work_path": str(tmp_path / "w")}])
    text = build_instructions(_sprint(), ctx, tmp_path / "scratchpad.md")
    assert "description.md" in text
    assert "axes and units" in text


def test_instructions_dont_ask_a_report_for_a_description(tmp_path):
    ctx = ExecutionContext(artifacts=[
        {"aid": "report", "kind": "md", "work_path": str(tmp_path / "w")}])
    text = build_instructions(_sprint(), ctx, tmp_path / "scratchpad.md")
    assert "description.md" not in text
```

Append to `tests/test_chat_bound_turn.py`:

```python
def test_bound_figure_turn_asks_for_a_description(substrate):
    substrate.save_program(Program(id="p", title="P", goals="g"))
    artifacts.create_artifact(substrate, "p", "fig", "Fig", "figure")
    svc = Service(substrate.repo_root)
    cid = svc.create_chat("p", artifacts=["fig"])["id"]
    calls = {}
    svc.post_chat_message("p", cid, "redraw it on a log axis",
                          launch=lambda **kw: calls.update(kw) or "tok")
    assert "description.md" in calls["prompt"]


def test_bound_document_turn_says_nothing_about_descriptions(substrate):
    svc, cid = _bound_chat(substrate)                      # binds an `md` artifact
    calls = {}
    svc.post_chat_message("p", cid, "make the title bold",
                          launch=lambda **kw: calls.update(kw) or "tok")
    assert "description.md" not in calls["prompt"]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_claude_executor.py tests/test_chat_bound_turn.py -v -k description`
Expected: the two "asks for a description" tests FAIL on the missing `description.md`; the two negative tests pass already.

- [ ] **Step 3: The sprint worker's instructions**

In `src/coscience/claude_executor.py`, add to the imports at the top:

```python
from coscience.artifacts import FIGURE_DESCRIPTION_NOTE
```

Then in `build_instructions`, replace the `if context.artifacts:` block (`src/coscience/claude_executor.py:46-55`) with:

```python
        if context.artifacts:
            alines = "\n".join(
                f'- `{a["aid"]}` ({a["kind"]}): write this artifact\'s files into {a["work_path"]}'
                for a in context.artifacts)
            figure_note = ("\n\n" + FIGURE_DESCRIPTION_NOTE
                           if any(a.get("kind") == "figure" for a in context.artifacts) else "")
            artifacts_section = (
                "\n\n## Artifacts to produce (deliverables)\n"
                "Write each artifact's files into its working directory below. The platform "
                "snapshots each working copy as a new immutable version when this sprint "
                "completes — you do not manage version numbers yourself; just create and edit "
                "the current files in place.\n" + alines + figure_note)
```

- [ ] **Step 4: The bound-chat preamble**

In `src/coscience/service.py`, add this helper immediately above `get_artifact` (around `src/coscience/service.py:1224`):

```python
    def _artifact_kind(self, program_id: str, aid: str) -> str:
        """The artifact's kind, or "" when it doesn't exist yet — a chat can be bound
        to an id whose artifact the agent has still to create."""
        if not (self.substrate.artifact_dir(program_id, aid) / "meta.md").is_file():
            return ""
        return self.substrate.load_artifact(program_id, aid).kind
```

Then replace the `if thread.artifacts:` prompt block (`src/coscience/service.py:819-822`) with:

```python
        if thread.artifacts:
            figure_note = ""
            if any(self._artifact_kind(program_id, a) == "figure" for a in thread.artifacts):
                figure_note = " " + FIGURE_DESCRIPTION_NOTE
            prompt = (f"[ARTIFACT] You are editing artifact(s) {thread.artifacts} — your working "
                      f"directory IS the artifact's working copy. Create and edit files here; "
                      f"the human snapshots them as versions.{figure_note}\n\n") + prompt
```

- [ ] **Step 5: The PM's guidance**

In `src/coscience/pm_claude.py`, find the line reading:

```
  If the request is to make an ALREADY-PROPOSED sprint deliver its output as an artifact
```

Insert immediately ABOVE it, at the same two-space indent:

```
  A `figure` artifact is the image PLUS a `description.md` beside it — what the figure
  shows, its axes and units, and what a reader should conclude. Ask for both when you
  propose one, and write one when you adopt a plot that arrives without it.
```

This is plain prose inside an f-string template — check that the inserted text contains no `{` or `}` (it doesn't) so the f-string still formats.

- [ ] **Step 6: Run the tests**

Run: `~/venvs/coscience/bin/python -m pytest tests/test_claude_executor.py tests/test_chat_bound_turn.py tests/test_cli_pm.py -v`
Expected: PASS.

- [ ] **Step 7: Run the whole Python suite**

Run: `~/venvs/coscience/bin/python -m pytest`
Expected: PASS. Any failure here is most likely a test asserting on exact prompt text — read it before changing it.

- [ ] **Step 8: Commit**

```bash
git add src/coscience/claude_executor.py src/coscience/service.py src/coscience/pm_claude.py tests/test_claude_executor.py tests/test_chat_bound_turn.py
git commit -m "feat(agents): ask for a figure's description where figures are made

The three prompts that talk about artifacts — a sprint worker's deliverables
section, a bound chat's [ARTIFACT] preamble, and the PM's artifact guidance —
now say a figure is the image plus a description.md. Nothing validates it: a
version with no description still cuts and still renders.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: The artifact page renders the description

**Files:**
- Modify: `frontend/src/components/ui.tsx:297` (export the filename beside `isImageName`)
- Modify: `frontend/src/views/ArtifactDetail.tsx:18-41` (`CurrentVersion`)
- Test: `frontend/src/views/ArtifactDetail.test.tsx`

**Interfaces:**
- Consumes: `api.readArtifactFile(pid, aid, vid, name)` → `{ name, size, content, binary }`; `api.artifactVersionRawUrl(pid, aid, vid, name)` → string.
- Produces: `DESCRIPTION_FILE` exported from `frontend/src/components/ui.tsx`, used by Task 4.

- [ ] **Step 1: Write the failing tests**

Append inside the existing `describe("ArtifactDetail", ...)` block in `frontend/src/views/ArtifactDetail.test.tsx`:

```tsx
  it("renders a figure's description under the image", async () => {
    vi.spyOn(api, "getArtifact").mockResolvedValue({
      id: "fig", program: "p", title: "Gap plot", kind: "figure", current: "v1",
      archived: false, lock: {}, current_files: ["description.md", "plot.png"],
      linked_sprints: [], threads: [], versions: [
        { id: "v1", parent: "", created_at: 1, created_by: "human", archived: false, note: "" }],
    } as any);
    vi.spyOn(api, "readArtifactFile").mockResolvedValue({
      name: "description.md", size: 9, content: "Gap size against prime index.", binary: false } as any);
    renderAt();
    await waitFor(() => expect(screen.getByText("Gap size against prime index.")).toBeTruthy());
    expect(screen.getByAltText("plot.png")).toBeTruthy();
    expect(api.readArtifactFile).toHaveBeenCalledWith("p", "fig", "v1", "description.md");
  });

  it("says so when a figure has no description", async () => {
    vi.spyOn(api, "getArtifact").mockResolvedValue({
      id: "fig", program: "p", title: "Gap plot", kind: "figure", current: "v1",
      archived: false, lock: {}, current_files: ["plot.png"],
      linked_sprints: [], threads: [], versions: [
        { id: "v1", parent: "", created_at: 1, created_by: "human", archived: false, note: "" }],
    } as any);
    const read = vi.spyOn(api, "readArtifactFile");
    renderAt();
    await waitFor(() => expect(screen.getByAltText("plot.png")).toBeTruthy());
    expect(screen.getByText(/no description yet/i)).toBeTruthy();
    expect(read).not.toHaveBeenCalled();          // absent file -> no request
  });

  it("resolves an image the description references by relative path", async () => {
    vi.spyOn(api, "getArtifact").mockResolvedValue({
      id: "fig", program: "p", title: "Gap plot", kind: "figure", current: "v1",
      archived: false, lock: {}, current_files: ["description.md", "plot.png", "panels/b.png"],
      linked_sprints: [], threads: [], versions: [
        { id: "v1", parent: "", created_at: 1, created_by: "human", archived: false, note: "" }],
    } as any);
    vi.spyOn(api, "readArtifactFile").mockResolvedValue({
      name: "description.md", size: 9, content: "Panel B:\n\n![B](panels/b.png)", binary: false } as any);
    renderAt();
    const b = await screen.findByAltText("B");
    expect(b.getAttribute("src")).toContain("panels/b.png");
    expect(b.getAttribute("src")).not.toBe("panels/b.png");   // resolved, not left relative
  });
```

The route in `renderAt()` is `/programs/p/artifacts/doc`, and `useParams` supplies `aid = "doc"`; the mocked `getArtifact` ignores its arguments, so `readArtifactFile` is called with the route's `aid`. Assert `"doc"` instead of `"fig"` if the test reports that mismatch — the point of the assertion is the filename and version, not the id.

- [ ] **Step 2: Run them to verify they fail**

Run: `cd frontend && npm test -- src/views/ArtifactDetail.test.tsx`
Expected: both FAIL — no description text and no "no description yet" line are rendered today.

- [ ] **Step 3: Export the filename**

In `frontend/src/components/ui.tsx`, directly under the `isImageName` export at line 297:

```tsx
/** A figure artifact's caption rides beside its image under exactly this name. */
export const DESCRIPTION_FILE = "description.md";
```

- [ ] **Step 4: Render it**

In `frontend/src/views/ArtifactDetail.tsx`, add `DESCRIPTION_FILE` to the existing `../components/ui` import (keep the list alphabetical: `BackLink, DESCRIPTION_FILE, EmptyState, …`).

Inside `CurrentVersion`, after the existing `file` query and before the `if (kind === "figure")` branch, add a second query — top level, so the Rules of Hooks hold:

```tsx
  // The figure's caption. `enabled` on the file's presence means a figure without one
  // costs no request; the version's file list already told us whether it exists.
  const hasDesc = kind === "figure" && files.includes(DESCRIPTION_FILE);
  const desc = useQuery({
    queryKey: ["artifact-file", pid, aid, current, DESCRIPTION_FILE],
    queryFn: () => api.readArtifactFile(pid, aid, current, DESCRIPTION_FILE),
    enabled: hasDesc && !!current,
  });
```

Then replace the figure branch's `return` with image + description:

```tsx
  if (kind === "figure") {
    if (!current) return <Text size="sm" c="dimmed">No content yet.</Text>;
    if (!imgName) return <Text size="sm" c="dimmed">No image in this version — download to view.</Text>;
    return (
      <Stack gap="md">
        <ZoomableImg src={api.artifactVersionRawUrl(pid, aid, current, imgName)}
                     style={{ maxWidth: "100%" }} alt={imgName} />
        {hasDesc ? (
          // resolveSrc for the same reason the text branch has one: a description may
          // reference a second panel by relative path, which would otherwise resolve
          // against the dashboard route.
          <div className="report-leaf">
            <Md resolveSrc={(src) => api.artifactVersionRawUrl(pid, aid, current, src)}>
              {desc.data?.content ?? ""}
            </Md>
          </div>
        ) : (
          <Text size="sm" c="dimmed">
            No description yet — a figure should ship a{" "}
            <span className="mono">description.md</span> saying what it shows.
          </Text>
        )}
      </Stack>
    );
  }
```

`Stack`, `Text`, `Md` and `ZoomableImg` are all already imported in this file.

- [ ] **Step 5: Run the tests**

Run: `cd frontend && npm test -- src/views/ArtifactDetail.test.tsx`
Expected: PASS, including the pre-existing cases (the md artifact's rendering is untouched).

- [ ] **Step 6: Typecheck**

Run: `cd frontend && npx tsc -b`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/ui.tsx frontend/src/views/ArtifactDetail.tsx frontend/src/views/ArtifactDetail.test.tsx
git commit -m "feat(artifacts): show a figure's description under the image

The artifact page rendered a plot and nothing else, so its axes, units and
point lived only with whoever drew it. It now renders the version's
description.md beneath the image — through Md with the version's resolveSrc, so
a description that references a second panel resolves — and names the missing
file when there isn't one.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: The chat preview renders the live description

The bound-chat pane picks ONE file to show: `textName` (first non-binary file) wins, and only if there is none does it fall back to the image. Adding `description.md` to a figure would therefore flip the pane from the plot to the text — so the description must be excluded from that choice before it can be rendered as a caption.

**Files:**
- Modify: `frontend/src/views/ChatView.tsx:61-72` (the preview's file choice + a description query) and `frontend/src/views/ChatView.tsx:328-336` (the image branch of the pane)
- Test: `frontend/src/views/ChatView.test.tsx`

**Interfaces:**
- Consumes: `DESCRIPTION_FILE` from `../components/ui` (Task 3); `api.listArtifactWorkFiles(id, aid)` → `string[]`; `api.readArtifactWorkFile(id, aid, name)` → `{ name, size, content, binary }`; `api.artifactWorkRawUrl(id, aid, name)` → string.
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Write the failing test**

Append inside the existing `describe("ChatView bound split-view", ...)` block in `frontend/src/views/ChatView.test.tsx`:

```tsx
  it("shows a bound figure's live description under the image", async () => {
    vi.spyOn(api, "getProgram").mockResolvedValue({ id: "p", title: "P" } as any);
    vi.spyOn(api, "listChats").mockResolvedValue([
      { id: "c1", title: "edit fig", scope: "full", created_at: 1, busy: false,
        messages: 0, last_at: 1, artifacts: ["fig"] }] as any);
    vi.spyOn(api, "getChatThread").mockResolvedValue({
      id: "c1", title: "edit fig", scope: "full", created_at: 1, turns_done: 0,
      busy: false, messages: [], live: "", artifacts: ["fig"] } as any);
    vi.spyOn(api, "listArtifactWorkFiles").mockResolvedValue(["description.md", "plot.png"]);
    vi.spyOn(api, "readArtifactWorkFile").mockResolvedValue({
      name: "description.md", size: 9, content: "Log-log gap size.", binary: false } as any);
    renderAt();
    // The image stays the pane's subject even though a text file is now present.
    await waitFor(() => expect(screen.getByAltText("plot.png")).toBeTruthy());
    expect(screen.getByText("Log-log gap size.")).toBeTruthy();
  });
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd frontend && npm test -- src/views/ChatView.test.tsx`
Expected: FAIL at `getByAltText("plot.png")` — today `description.md` becomes `textName`, so the pane renders markdown and no image at all. That failure IS the regression this task prevents.

- [ ] **Step 3: Keep the description out of the file choice**

In `frontend/src/views/ChatView.tsx`, add `DESCRIPTION_FILE` to the existing `../components/ui` import.

Replace the `textName`/`imgName` lines (`frontend/src/views/ChatView.tsx:64-67`, the block whose comment starts "A figure's deliverable IS the image") with:

```tsx
  // A figure's deliverable IS the image — prefer it over any build script so the
  // panel shows the picture, not the .py source. Otherwise render the first text file.
  // ...unless the artifact IS a document that ships figures: then the text file is the
  // deliverable and the images are its illustrations, resolved inside the markdown.
  // The figure's own description.md is its caption, not a text deliverable, so it never
  // wins that choice — it renders under the image instead.
  const descName = files.includes(DESCRIPTION_FILE) ? DESCRIPTION_FILE : "";
  const textName = files.find((n) => !isBinaryName(n) && n !== descName) ?? "";
  const imgName = textName ? "" : files.find(isImageName) ?? "";
```

- [ ] **Step 4: Fetch the live description**

Immediately after the existing `workfile` query (`frontend/src/views/ChatView.tsx:68-73`), add:

```tsx
  // Only alongside an image: with no image the description IS the pane's text file and
  // `workfile` above already renders it, so fetching twice would just double the work.
  const descfile = useQuery({
    queryKey: ["workfile", id, aid, DESCRIPTION_FILE],
    queryFn: () => api.readArtifactWorkFile(id, aid, DESCRIPTION_FILE),
    enabled: !!aid && !!descName && !!imgName,
    refetchInterval: busy ? 2000 : false,
  });
```

- [ ] **Step 5: Render it under the image**

Replace the pane's image branch (`frontend/src/views/ChatView.tsx:328-336`, the `) : imgName ? (` arm) with:

```tsx
                ) : imgName ? (
                  <div style={{ maxHeight: "calc(100vh - 190px)", overflow: "auto", textAlign: "center" }}>
                    <ZoomableImg src={`${api.artifactWorkRawUrl(id, aid, imgName)}?t=${work.dataUpdatedAt}`}
                                 alt={imgName} style={{ maxWidth: "100%" }} />
                    {descName && (
                      <div className="report-leaf" style={{ textAlign: "left", marginTop: 12 }}>
                        <Md resolveSrc={(src) => `${api.artifactWorkRawUrl(id, aid, src)}?t=${work.dataUpdatedAt}`}>
                          {descfile.data?.content ?? ""}
                        </Md>
                      </div>
                    )}
                  </div>
                ) : !workName || workfile.data?.binary ? (
```

`Md` is already imported in this file.

- [ ] **Step 6: Run the tests**

Run: `cd frontend && npm test -- src/views/ChatView.test.tsx`
Expected: PASS, including the pre-existing "Save as version" case (an `md` artifact with only `content.md` — `descName` is `""`, so nothing changes for it).

- [ ] **Step 7: Run the whole frontend suite and typecheck**

Run: `cd frontend && npm test && npx tsc -b`
Expected: PASS, no type errors. `ProgramDetail.test.tsx` must be unaffected — that view is unchanged.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/views/ChatView.tsx frontend/src/views/ChatView.test.tsx
git commit -m "feat(chat): show a bound figure's live description under the image

The bound pane shows one file, preferring text — so a figure that gained a
description.md would have shown the caption INSTEAD of the plot. The
description is now excluded from that choice and rendered beneath the image
from the live work/ copy, so an agent editing it shows on the next poll.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## After the plan

- **Full verification before calling it done:** `~/venvs/coscience/bin/python -m pytest` and `( cd frontend && npm test )`, both green, with the output read.
- **Deploying:** this changes frontend sources, so a deploy must run `npm run build` (`scripts/deploy.sh` always does) and the browser needs a hard reload (Ctrl-Shift-R) to drop the cached bundle.
- **No backfill.** Figures that already exist keep showing the "no description yet" line until someone or some agent writes one. That is the intended prompt to write it, not a bug.
