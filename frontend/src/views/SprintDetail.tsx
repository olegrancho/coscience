import { Button, Card, Group, Loader, SimpleGrid, Stack, Text, Textarea } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import Md from "../components/Md";
import { api, type SprintFile } from "../api";
import { availableActions, type SprintStatus } from "../sprintActions";
import { BackLink, EmptyState, RelTime, StatusBadge } from "../components/ui";
import SprintEditModal from "../components/SprintEditModal";

/** Inline result: shows a clamped preview that unfolds in place, plus a link to
 *  the full report page. Long reports start folded; short ones show whole. */
function ResultPreview({ id }: { id: string }) {
  const [open, setOpen] = useState(false);
  const result = useQuery({ queryKey: ["result", id], queryFn: () => api.getResult(id) });
  if (result.isLoading) return <Text size="sm" c="dimmed">Loading the result…</Text>;
  if (result.error || !result.data) {
    return <Link to={`/results/${id}`} className="view">Open result →</Link>;
  }
  const summary = result.data.summary;
  const long = summary.length > 240 || summary.split("\n").length > 6;
  const folded = long && !open;
  return (
    <div>
      <div
        className={"report-leaf" + (folded ? " clamped clickable" : "")}
        onClick={folded ? () => setOpen(true) : undefined}
        role={folded ? "button" : undefined}
        tabIndex={folded ? 0 : undefined}
        aria-label={folded ? "Unfold result" : undefined}
        onKeyDown={folded ? (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setOpen(true); } } : undefined}
      >
        <Md>{summary}</Md>
      </div>
      <Group gap={16} mt={10} align="center">
        {long && (
          <button type="button" className="linklike" onClick={() => setOpen((o) => !o)}>
            {open ? "Fold ▴" : "Unfold ▾"}
          </button>
        )}
        <Link to={`/results/${id}`} className="view">Full report →</Link>
        {result.data.completed_at && (
          <Text size="xs" c="dimmed">finished <RelTime at={result.data.completed_at} /></Text>
        )}
      </Group>
    </div>
  );
}

function programOf(id: string) {
  const i = id.indexOf("-");
  return i === -1 ? id : id.slice(0, i);
}

const cardStyle = { border: "1px solid var(--hairline)", boxShadow: "var(--shadow-card)" };

function fmtSize(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

/** One collapsible agent document. Scratchpad + log open by default (the live
 *  view of what the agent is doing); instructions + artifacts start folded. */
function FileBlock({ f }: { f: SprintFile }) {
  const [open, setOpen] = useState(f.kind === "scratchpad" || f.kind === "log");
  const isMd = f.name.endsWith(".md");
  return (
    <div style={{ border: "1px solid var(--hairline)", borderRadius: 8, overflow: "hidden" }}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        style={{ width: "100%", display: "flex", alignItems: "center", gap: 9, padding: "9px 12px",
                 background: "var(--machine-weak)", border: "none", cursor: "pointer", textAlign: "left" }}
      >
        <span style={{ color: "var(--ink-faint)", fontSize: 11, width: 10 }}>{open ? "▾" : "▸"}</span>
        <span style={{ fontWeight: 600, fontSize: 13 }}>{f.label}</span>
        {f.label !== f.name && <span className="mono" style={{ fontSize: 11, color: "var(--ink-faint)" }}>{f.name}</span>}
        <span style={{ marginLeft: "auto", fontSize: 11, color: "var(--ink-faint)" }} className="mono">{fmtSize(f.size)}</span>
      </button>
      {open && (
        <div style={{ padding: "12px 14px" }}>
          {f.binary ? (
            <Text size="sm" c="dimmed">Binary file — not shown.</Text>
          ) : !f.content.trim() ? (
            <Text size="sm" c="dimmed">Empty.</Text>
          ) : isMd ? (
            <div className="report-leaf"><Md>{f.content}</Md></div>
          ) : (
            <pre className="mono" style={{ margin: 0, whiteSpace: "pre-wrap", wordBreak: "break-word",
                 fontSize: 12.5, lineHeight: 1.5, maxHeight: 440, overflow: "auto" }}>{f.content}</pre>
          )}
          {f.truncated && (
            <Text size="xs" c="dimmed" mt={8}>… showing the most recent {Math.round(f.content.length / 1024)} KB.</Text>
          )}
        </div>
      )}
    </div>
  );
}

/** The agent's working documents for a sprint. Polls while the agent runs so
 *  scratchpad + log update live; static once finished. */
function WorkingDocs({ sprintId, live }: { sprintId: string; live: boolean }) {
  const files = useQuery({
    queryKey: ["sprint-files", sprintId],
    queryFn: () => api.getSprintFiles(sprintId),
    refetchInterval: live ? 5000 : false,
  });
  const docs = files.data ?? [];
  if (!docs.length) return null;
  return (
    <Card padding="lg" radius="md" style={cardStyle}>
      <div className="eyebrow" style={{ marginBottom: 4 }}>working documents · {docs.length}</div>
      <Text size="xs" c="dimmed" mb="md">
        The research agent's own files for this experiment{live ? " — refreshing live while it runs." : "."}
      </Text>
      <Stack gap={10}>
        {docs.map((f) => <FileBlock key={f.name} f={f} />)}
      </Stack>
    </Card>
  );
}

export default function SprintDetail() {
  const { id = "" } = useParams();
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [comment, setComment] = useState("");
  const prog = programOf(id);
  const sprint = useQuery({ queryKey: ["sprint", id], queryFn: () => api.getSprint(id) });
  const program = useQuery({ queryKey: ["program", prog], queryFn: () => api.getProgram(prog), enabled: !!prog });
  const refresh = () => qc.invalidateQueries({ queryKey: ["sprint", id] });

  if (sprint.isLoading) return <Loader color="machine" />;
  if (sprint.error || !sprint.data) {
    return <EmptyState title="Experiment not found">Nothing here at “{id}”. It may have been removed.</EmptyState>;
  }
  const s = sprint.data;
  const actions = availableActions(s.status as SprintStatus);
  const progTitle = program.data?.title || prog;
  const isDone = s.status === "done";
  const resources = Object.entries(s.resources_required);

  const approve = async () => {
    try { await api.approveSprint(id); notifications.show({ color: "teal", title: "Approved", message: "It'll run when compute is free." }); refresh(); }
    catch (e) { notifications.show({ color: "red", title: "Couldn't approve", message: String(e) }); }
  };
  const reject = async () => {
    try { await api.rejectSprint(id); notifications.show({ color: "gray", title: "Rejected", message: "Canceled — it won't run." }); refresh(); }
    catch (e) { notifications.show({ color: "red", title: "Couldn't reject", message: String(e) }); }
  };
  const addComment = async () => {
    if (!comment.trim()) return;
    try { await api.addSprintComment(id, comment.trim()); setComment(""); notifications.show({ color: "teal", title: "Comment added", message: "The research agent reads this as direction." }); refresh(); }
    catch (e) { notifications.show({ color: "red", title: "Couldn't comment", message: String(e) }); }
  };

  return (
    <Stack gap="lg">
      <div>
        <BackLink to={`/programs/${prog}`}>{progTitle}</BackLink>
        <Group justify="space-between" align="flex-start" wrap="nowrap">
          <h1 style={{ fontFamily: "'Space Grotesk', sans-serif", fontSize: 23, fontWeight: 600, margin: 0, maxWidth: 620, lineHeight: 1.25 }}>
            {s.title || s.goals || s.id}
          </h1>
          <Group gap={8} wrap="nowrap">
            {actions.includes("approve") && <Button color="signal" onClick={approve}>Approve</Button>}
            {actions.includes("reject") && <Button variant="default" onClick={reject}>Reject</Button>}
            {actions.includes("edit") && <Button variant="light" color="machine" onClick={() => setEditing(true)}>Edit</Button>}
          </Group>
        </Group>
        <Group gap={10} mt={9}>
          <StatusBadge status={s.status} />
          <span className="mono" style={{ fontSize: 12, color: "var(--ink-faint)" }}>ref {s.id}</span>
        </Group>
        {s.summary && <Text mt={12} style={{ maxWidth: 620, color: "var(--ink-muted)", lineHeight: 1.55 }}>{s.summary}</Text>}
      </div>

      {s.title && s.goals && s.goals !== s.title && (
        <Card padding="lg" radius="md" style={cardStyle}>
          <div className="eyebrow" style={{ marginBottom: 8 }}>what this experiment does</div>
          <Text>{s.goals}</Text>
        </Card>
      )}

      {s.rationale && (
        <Card padding="lg" radius="md" style={cardStyle}>
          <div className="eyebrow" style={{ marginBottom: 8 }}>why the AI proposed this</div>
          <Text>{s.rationale}</Text>
        </Card>
      )}

      <Card padding="lg" radius="md" style={cardStyle}>
        <div className="eyebrow" style={{ marginBottom: 4 }}>your feedback{s.comments.length ? ` · ${s.comments.length}` : ""}</div>
        <Text size="xs" c="dimmed" mb="sm">Notes for the research agent — it reads these as direction on its next run.</Text>
        <Stack gap={8}>
          {s.comments.map((c) => (
            <div key={c.id} style={{ background: "var(--paper)", borderRadius: 8, padding: "8px 12px" }}>
              <div className="md-tight"><Md>{c.text}</Md></div>
              <Text size="xs" c="dimmed" mt={2}><RelTime at={c.added_at} /></Text>
            </div>
          ))}
          <Group gap={8} align="flex-start">
            <Textarea style={{ flex: 1 }} placeholder="Leave a comment for the agent…" autosize minRows={1}
              value={comment} onChange={(e) => setComment(e.currentTarget.value)} />
            <Button variant="light" color="machine" onClick={addComment}>Comment</Button>
          </Group>
        </Stack>
      </Card>

      {s.results.length > 0 && (
        <Card padding="lg" radius="md" style={cardStyle}>
          <div className="eyebrow" style={{ marginBottom: 12 }}>
            {s.results.length > 1 ? `results · ${s.results.length}` : "what the experiment found"}
          </div>
          <Stack gap={22}>
            {s.results.map((rid) => <ResultPreview key={rid} id={rid} />)}
          </Stack>
        </Card>
      )}

      <WorkingDocs sprintId={s.id} live={s.agent_running} />

      <SimpleGrid cols={{ base: 1, sm: 2 }}>
        <Card padding="lg" radius="md" style={cardStyle}>
          <div className="eyebrow" style={{ marginBottom: 10 }}>at a glance</div>
          <Stack gap={7}>
            <Group justify="space-between"><Text size="sm" c="dimmed">Priority</Text><Text size="sm" className="mono">{s.priority}</Text></Group>
            <Group justify="space-between"><Text size="sm" c="dimmed">Can pause for urgent work</Text><Text size="sm">{s.preemptible ? "yes" : "no"}</Text></Group>
            <Group justify="space-between">
              <Text size="sm" c="dimmed">State</Text>
              <Text size="sm">{s.lease ? "running now" : isDone ? "finished" : s.status === "approved" ? "queued to run" : "awaiting your decision"}</Text>
            </Group>
          </Stack>
        </Card>
        <Card padding="lg" radius="md" style={cardStyle}>
          <div className="eyebrow" style={{ marginBottom: 10 }}>{isDone ? "compute it used" : "compute it will use"}</div>
          {resources.length ? (
            <Stack gap={6}>
              {resources.map(([k, v]) => (
                <Group key={k} justify="space-between"><Text size="sm" c="dimmed">{k}</Text><Text size="sm" className="mono">{v}</Text></Group>
              ))}
            </Stack>
          ) : <Text size="sm" c="dimmed">Minimal — no reserved resources.</Text>}
        </Card>
      </SimpleGrid>

      {s.plan.length > 0 && (
        <Card padding="lg" radius="md" style={cardStyle}>
          <div className="eyebrow" style={{ marginBottom: 4 }}>suggested approach · {s.plan.length} {s.plan.length === 1 ? "step" : "steps"}</div>
          <Text size="xs" c="dimmed" mb="md">High-level guidance for the research agent — it plans and carries out the actual work itself.</Text>
          <Stack gap={11}>
            {s.plan.map((step, i) => (
              <Group key={i} gap={10} wrap="nowrap" align="flex-start">
                <span style={{ color: "var(--ink-faint)", fontSize: 13, lineHeight: 1.55, minWidth: 16 }}>{i + 1}.</span>
                <Text size="sm" style={{ lineHeight: 1.55 }}>{step}</Text>
              </Group>
            ))}
          </Stack>
        </Card>
      )}

      <SprintEditModal sprint={s} opened={editing} onClose={() => setEditing(false)} onDone={refresh} />
    </Stack>
  );
}
