import { ActionIcon, Button, Card, Group, Loader, Stack, Text, Textarea, TextInput } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useParams } from "react-router-dom";
import Md from "../components/Md";
import { api, type Idea, type IdeaPool } from "../api";
import { AbsTime, BackLink, EmptyState } from "../components/ui";
import { FeedbackThread } from "../components/FeedbackThread";

const cardStyle = { border: "1px solid var(--hairline)", boxShadow: "var(--shadow-card)" };

// Author badge: an 18px circle only (no visible name) — the full attribution
// shows on hover. The AI planner uses a bot glyph; humans their initials.
const circleStyle = {
  width: 18, height: 18, borderRadius: 9, fontSize: 9, fontWeight: 700,
  display: "inline-flex", alignItems: "center", justifyContent: "center",
  background: "var(--machine-weak)", color: "var(--machine)",
} as const;

function AiChip() {
  return (
    <span title="Proposed by the AI planner"
      style={{ ...circleStyle, fontSize: 11 }}>🤖</span>
  );
}

function PersonChip({ username }: { username?: string }) {
  const users = useQuery({ queryKey: ["users"], queryFn: api.listUsers });
  const u = username ? (users.data ?? []).find((x) => x.username === username) : undefined;
  const initials = u?.initials ?? (username ? username.slice(0, 2).toUpperCase() : "·");
  const name = u?.name ?? username ?? "someone";
  return <span title={`Proposed by ${name}`} style={circleStyle}>{initials}</span>;
}

function IdeaRow({ programId, idea, onChange }: { programId: string; idea: Idea; onChange: () => void }) {
  const [open, setOpen] = useState(false);
  const [comment, setComment] = useState("");

  const qc = useQueryClient();
  const act = async (fn: () => Promise<unknown>, fail: string) => {
    try { await fn(); onChange(); }
    catch (e) { notifications.show({ color: "red", title: fail, message: String(e) }); }
  };
  // Optimistic: flip the pin in the cache immediately so the icon + highlight
  // update on click, then reconcile with the server (~1s round-trip otherwise).
  const togglePin = () => {
    const next = !idea.pinned;   // protection is pinned-only, so protected tracks pinned exactly
    qc.setQueryData<IdeaPool>(["ideas", programId], (old) =>
      old ? { ...old, ideas: old.ideas.map((i) =>
        i.id === idea.id ? { ...i, pinned: next, protected: next } : i) } : old);
    api.setIdeaPin(programId, idea.id, next)
      .then(() => qc.invalidateQueries({ queryKey: ["ideas", programId] }))
      .catch((e) => {
        qc.invalidateQueries({ queryKey: ["ideas", programId] });   // revert to server truth
        notifications.show({ color: "red", title: "Couldn't change pin", message: String(e) });
      });
  };
  const del = () => act(() => api.deleteIdea(programId, idea.id), "Couldn't delete");
  const liftDemote = () => act(() => api.setIdeaDemoted(programId, idea.id, false), "Couldn't lift demotion");
  const addComment = () => {
    if (!comment.trim()) return;
    act(async () => { await api.addIdeaComment(programId, idea.id, comment.trim()); setComment(""); },
      "Couldn't comment");
  };
  // Idea threads always target the PM — it reads and answers every cycle, so
  // (unlike a worker thread) it always "responds now".
  const replyThread = (tid: string, text: string) =>
    act(() => api.addIdeaComment(programId, idea.id, text, tid), "Couldn't reply");
  const completeThread = (tid: string) =>
    act(() => api.completeIdeaThread(programId, idea.id, tid), "Couldn't complete");
  const reopenThread = (tid: string) =>
    act(() => api.reopenIdeaThread(programId, idea.id, tid), "Couldn't reopen");
  const deleteThread = (tid: string) =>
    act(() => api.deleteIdeaThread(programId, idea.id, tid), "Couldn't delete");
  const seenThread = (tid: string) =>
    act(() => api.seenIdeaThread(programId, idea.id, tid), "Couldn't mark seen");

  // Pin off → light-gray pin; pin on → colored pin (in a tinted chip) plus a
  // colored box around the whole idea. Protection from other sources (human,
  // comments, demote) still holds on the backend; the pin control reflects the
  // explicit human pin.
  const pinTitle = idea.pinned
    ? "Pinned — protected from AI pruning. Click to unpin."
    : "Pin — protect from AI pruning.";

  return (
    <div style={{
      border: `1px solid ${idea.pinned ? "var(--signal)" : "var(--hairline)"}`,
      background: idea.pinned ? "var(--signal-weak)" : undefined,
      borderRadius: 8, overflow: "hidden",
    }}>
      <Group gap={9} wrap="nowrap" align="flex-start" style={{ padding: "10px 12px" }}>
        <button type="button" onClick={() => setOpen((o) => !o)} aria-label="expand"
          style={{ background: "none", border: "none", cursor: "pointer", color: "var(--ink-faint)", fontSize: 12, paddingTop: 2 }}>
          {open ? "▾" : "▸"}
        </button>
        <div style={{ flex: 1, minWidth: 0, cursor: "pointer" }}
          onClick={() => setOpen((o) => !o)}>
          <div className={"md-tight" + (open ? "" : " clamp2")}><Md>{idea.text}</Md></div>
          <Group gap={8} mt={5} wrap="nowrap">
            {idea.source === "pm" ? <AiChip /> : <PersonChip username={idea.by} />}
            {idea.created_at ? (
              <Text size="xs" c="dimmed" style={{ whiteSpace: "nowrap" }}>
                <AbsTime at={idea.created_at} dateOnly />
              </Text>
            ) : null}
            {idea.threads.length > 0 && (
              <Text size="xs" c="dimmed">{idea.threads.length} thread{idea.threads.length === 1 ? "" : "s"}</Text>
            )}
            {idea.demoted && (
              <Text size="xs" fw={600} style={{ color: "var(--signal)" }}
                title="Demoted from a sprint — the AI can't promote it back">demoted</Text>
            )}
          </Group>
        </div>
        <Group gap={4} wrap="nowrap">
          {idea.demoted && (
            <ActionIcon variant="subtle" color="teal" onClick={liftDemote} aria-label="lift demotion"
              title="Lift demotion — let the AI promote it to a sprint again">↑</ActionIcon>
          )}
          <ActionIcon variant={idea.pinned ? "light" : "subtle"} color="signal" onClick={togglePin}
            aria-label={idea.pinned ? "unpin" : "pin"} title={pinTitle}
            style={{ opacity: idea.pinned ? 1 : 0.55, filter: idea.pinned ? "none" : "grayscale(1)" }}>
            📌
          </ActionIcon>
          <ActionIcon variant="subtle" color="gray" onClick={del} aria-label="delete" title="Delete idea">✕</ActionIcon>
        </Group>
      </Group>

      {open && (
        <div style={{ padding: "0 12px 12px 33px", borderTop: "1px solid var(--hairline)" }}>
          {idea.threads.length > 0 && (
            <Stack gap={8} mt={10}>
              {idea.threads.map((t) => (
                <FeedbackThread key={t.id} thread={t}
                  onReply={(text) => replyThread(t.id, text)}
                  onComplete={() => completeThread(t.id)}
                  onReopen={() => reopenThread(t.id)}
                  onDelete={() => deleteThread(t.id)}
                  onSeen={() => seenThread(t.id)}
                  respondsNow />
              ))}
            </Stack>
          )}
          <Group gap={8} mt={10}>
            <Textarea style={{ flex: 1 }} placeholder="Comment (this protects the idea from AI pruning)…"
              autosize minRows={1} value={comment} onChange={(e) => setComment(e.currentTarget.value)} />
            <Button variant="light" color="machine" onClick={addComment}>Comment</Button>
          </Group>
        </div>
      )}
    </div>
  );
}

export default function IdeasView() {
  const { id = "" } = useParams();
  const qc = useQueryClient();
  const [draft, setDraft] = useState("");

  const program = useQuery({ queryKey: ["program", id], queryFn: () => api.getProgram(id) });
  const pool = useQuery({ queryKey: ["ideas", id], queryFn: () => api.listIdeas(id) });
  const refresh = () => qc.invalidateQueries({ queryKey: ["ideas", id] });

  // "Your feedback" reuses the same standing-guidance stream as the program page
  // (shared query key), so edits here and there stay in sync.
  const guidance = useQuery({ queryKey: ["guidance", id], queryFn: () => api.listGuidance(id) });
  const [note, setNote] = useState("");
  const refreshGuidance = () => qc.invalidateQueries({ queryKey: ["guidance", id] });
  const guard = (p: Promise<unknown>, fail: string) =>
    p.then(refreshGuidance).catch((e) => notifications.show({ color: "red", title: fail, message: String(e) }));
  const addNote = async () => {
    if (!note.trim()) return;
    try {
      await api.addGuidance(id, note.trim()); setNote("");
      notifications.show({ color: "teal", title: "Feedback added", message: "The AI will weigh it next cycle." });
      refreshGuidance();
    } catch (e) { notifications.show({ color: "red", title: "Couldn't add", message: String(e) }); }
  };
  const replyGuidance = (tid: string, text: string) => guard(api.addGuidance(id, text, tid), "Couldn't reply");
  const completeGuidance = (tid: string) => guard(api.completeGuidanceThread(id, tid), "Couldn't complete");
  const reopenGuidance = (tid: string) => guard(api.reopenGuidanceThread(id, tid), "Couldn't reopen");
  const deleteGuidance = (tid: string) => guard(api.deleteGuidance(id, tid), "Couldn't delete");
  const seenGuidance = (tid: string) => guard(api.seenGuidanceThread(id, tid), "Couldn't mark seen");

  const propose = async () => {
    if (!draft.trim()) return;
    try {
      await api.addIdea(id, draft.trim()); setDraft("");
      notifications.show({ color: "teal", title: "Idea added", message: "It's protected — the AI can develop it but not delete it." });
      refresh();
    } catch (e) { notifications.show({ color: "red", title: "Couldn't add", message: String(e) }); }
  };

  const [running, setRunning] = useState<"" | "compress" | "brainstorm">("");
  const runDirective = async (mode: "compress" | "brainstorm") => {
    setRunning(mode);
    try {
      const r = await api.pmDirective(id, mode);
      const label = mode === "compress" ? "Compress" : "Brainstorm";
      const added = r.ideas_added ?? 0;
      const removed = r.ideas_removed ?? 0;
      const noChange = added === 0 && removed === 0;
      const quiet = r.busy || r.throttled || r.skipped || noChange;
      const msg = r.busy ? "The PM is already reasoning — try again in a moment."
        : r.throttled ? "Claude usage is exhausted; it will resume after the reset."
        : r.skipped ? "Nothing to do this cycle."
        : mode === "brainstorm"
          ? (added > 0 ? `Added ${added} new idea${added === 1 ? "" : "s"} — ${r.pool_size ?? "?"} in the pool.`
                       : "The PM added no new ideas this time — try again.")
          : (noChange ? "No changes — the pool was already tight."
                      : `Compressed: removed ${removed}, added ${added} merged — ${r.pool_size ?? "?"} in the pool.`);
      notifications.show({ color: quiet ? "yellow" : "teal", title: label, message: msg });
      qc.invalidateQueries({ queryKey: ["ideas", id] });
      qc.invalidateQueries({ queryKey: ["program", id] });
    } catch (e) { notifications.show({ color: "red", title: "Couldn't run", message: String(e) }); }
    finally { setRunning(""); }
  };

  if (pool.isLoading) return <Loader color="machine" />;
  if (pool.error || !pool.data) {
    return <EmptyState title="Program not found">Nothing here at “{id}”.</EmptyState>;
  }
  const { summary, ideas } = pool.data;
  const title = program.data?.title || id;

  return (
    <Stack gap="lg">
      <div>
        <BackLink to={`/programs/${id}`}>{title}</BackLink>
        <Group justify="space-between" align="flex-start" wrap="nowrap">
          <h1 style={{ fontFamily: "'Space Grotesk', sans-serif", fontSize: 23, fontWeight: 600, margin: 0 }}>Ideas</h1>
          <Group gap={8} wrap="nowrap">
            <Button variant="light" color="machine" loading={running === "compress"} disabled={!!running}
              onClick={() => runDirective("compress")}
              title="Ask the PM to merge similar ideas, prune weak ones, and re-rank the pool (pinned ideas stay intact)">Compress</Button>
            <Button color="machine" loading={running === "brainstorm"} disabled={!!running}
              onClick={() => runDirective("brainstorm")}
              title="Ask the PM to add fresh candidate ideas to the pool">Brainstorm</Button>
          </Group>
        </Group>
        <Text size="sm" c="dimmed" mt={6} style={{ maxWidth: 640 }}>
          A pool of candidate directions. The AI grows and prunes it as results come in and promotes
          promising ones into experiments. Yours are protected; pin or comment to protect the AI's.
        </Text>
      </div>

      <Card padding="lg" radius="md" style={cardStyle}>
        <div className="eyebrow" style={{ marginBottom: 12 }}>the AI's read on the pool</div>
        {summary.trim()
          ? <div className="report-leaf"><Md>{summary}</Md></div>
          : <Text size="sm" c="dimmed">No summary yet — the AI writes one as it curates ideas.</Text>}
      </Card>

      <Card padding="lg" radius="md" style={cardStyle}>
        <div className="eyebrow" style={{ marginBottom: 4 }}>your feedback</div>
        <Text size="xs" c="dimmed" mb="sm">Standing direction the AI weighs every cycle and replies to — mark a thread complete once it's handled. (Shared with the program page.)</Text>
        <Stack gap={8}>
          {(guidance.data ?? []).map((t) => (
            <FeedbackThread key={t.id} thread={t}
              onReply={(text) => replyGuidance(t.id, text)}
              onComplete={() => completeGuidance(t.id)}
              onReopen={() => reopenGuidance(t.id)}
              onDelete={() => deleteGuidance(t.id)}
              onSeen={() => seenGuidance(t.id)} />
          ))}
          <Group gap={8}>
            <TextInput style={{ flex: 1 }} placeholder="Add feedback for the AI…" value={note}
              onChange={(e) => setNote(e.currentTarget.value)}
              onKeyDown={(e) => e.key === "Enter" && addNote()} />
            <Button variant="light" color="machine" onClick={addNote}>Add</Button>
          </Group>
        </Stack>
      </Card>

      <Card padding="lg" radius="md" style={cardStyle}>
        <div className="eyebrow" style={{ marginBottom: 10 }}>ideas · {ideas.length}</div>
        <Group gap={8} mb={ideas.length ? "md" : 0} align="flex-start">
          <Textarea style={{ flex: 1 }} placeholder="Propose an idea — one short paragraph…"
            autosize minRows={1} value={draft} onChange={(e) => setDraft(e.currentTarget.value)} />
          <Button color="machine" onClick={propose}>Add idea</Button>
        </Group>
        {ideas.length === 0
          ? <Text size="sm" c="dimmed">No ideas yet. Add one above, or let the AI seed the pool next cycle.</Text>
          : (
            <Stack gap={8}>
              {ideas.map((i) => <IdeaRow key={i.id} programId={id} idea={i} onChange={refresh} />)}
            </Stack>
          )}
      </Card>
    </Stack>
  );
}
