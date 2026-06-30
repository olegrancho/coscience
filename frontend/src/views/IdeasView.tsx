import { ActionIcon, Button, Card, Group, Loader, Stack, Text, Textarea } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useParams } from "react-router-dom";
import Md from "../components/Md";
import { api, type Idea } from "../api";
import { BackLink, EmptyState, RelTime } from "../components/ui";

const cardStyle = { border: "1px solid var(--hairline)", boxShadow: "var(--shadow-card)" };

function SourceChip({ source }: { source: Idea["source"] }) {
  const pm = source === "pm";
  return (
    <span className="mono" style={{
      fontSize: 11, padding: "1px 7px", borderRadius: 999,
      color: pm ? "var(--machine)" : "var(--signal)",
      background: pm ? "var(--machine-weak)" : "var(--signal-weak)",
    }}>{pm ? "AI" : "you"}</span>
  );
}

function IdeaRow({ programId, idea, onChange }: { programId: string; idea: Idea; onChange: () => void }) {
  const [open, setOpen] = useState(false);
  const [comment, setComment] = useState("");

  const act = async (fn: () => Promise<unknown>, fail: string) => {
    try { await fn(); onChange(); }
    catch (e) { notifications.show({ color: "red", title: fail, message: String(e) }); }
  };
  const togglePin = () => act(() => api.setIdeaPin(programId, idea.id, !idea.pinned),
    "Couldn't change pin");
  const del = () => act(() => api.deleteIdea(programId, idea.id), "Couldn't delete");
  const addComment = () => {
    if (!comment.trim()) return;
    act(async () => { await api.addIdeaComment(programId, idea.id, comment.trim()); setComment(""); },
      "Couldn't comment");
  };

  // protected-but-not-pinned (e.g. a human comment) — show why it can't be auto-pruned
  const autoProtected = idea.protected && !idea.pinned;

  return (
    <div style={{ border: "1px solid var(--hairline)", borderRadius: 8, overflow: "hidden" }}>
      <Group gap={9} wrap="nowrap" align="flex-start" style={{ padding: "10px 12px" }}>
        <button type="button" onClick={() => setOpen((o) => !o)} aria-label="expand"
          style={{ background: "none", border: "none", cursor: "pointer", color: "var(--ink-faint)", fontSize: 12, paddingTop: 2 }}>
          {open ? "▾" : "▸"}
        </button>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className={"md-tight" + (open ? "" : " clamp2")}><Md>{idea.text}</Md></div>
          <Group gap={8} mt={5} wrap="nowrap">
            <SourceChip source={idea.source} />
            {idea.comments.length > 0 && (
              <Text size="xs" c="dimmed">{idea.comments.length} comment{idea.comments.length === 1 ? "" : "s"}</Text>
            )}
            {autoProtected && <Text size="xs" c="dimmed">protected</Text>}
          </Group>
        </div>
        <Group gap={4} wrap="nowrap">
          <ActionIcon variant={idea.pinned ? "filled" : "subtle"} color="signal" onClick={togglePin}
            aria-label={idea.pinned ? "unpin" : "pin"} title={idea.pinned ? "Unpin (let the AI prune it)" : "Pin (protect from AI pruning)"}>
            📌
          </ActionIcon>
          <ActionIcon variant="subtle" color="gray" onClick={del} aria-label="delete" title="Delete idea">✕</ActionIcon>
        </Group>
      </Group>

      {open && (
        <div style={{ padding: "0 12px 12px 33px", borderTop: "1px solid var(--hairline)" }}>
          {idea.comments.length > 0 && (
            <Stack gap={6} mt={10}>
              {idea.comments.map((c) => (
                <div key={c.id} style={{ background: "var(--paper)", borderRadius: 8, padding: "7px 11px" }}>
                  <div className="md-tight"><Md>{c.text}</Md></div>
                  <Text size="xs" c="dimmed" mt={2}><RelTime at={c.added_at} /></Text>
                </div>
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

  const propose = async () => {
    if (!draft.trim()) return;
    try {
      await api.addIdea(id, draft.trim()); setDraft("");
      notifications.show({ color: "teal", title: "Idea added", message: "It's protected — the AI can develop it but not delete it." });
      refresh();
    } catch (e) { notifications.show({ color: "red", title: "Couldn't add", message: String(e) }); }
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
        <h1 style={{ fontFamily: "'Space Grotesk', sans-serif", fontSize: 23, fontWeight: 600, margin: 0 }}>Ideas</h1>
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
