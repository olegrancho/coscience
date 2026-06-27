import { Button, Card, Code, Group, Loader, SimpleGrid, Stack, Text } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import Markdown from "react-markdown";
import { api } from "../api";
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
        <Markdown>{summary}</Markdown>
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

/** "check-witness-pair" → "Check witness pair" — a label a human can read. */
function humanizeStep(stepId: string) {
  const s = stepId.replace(/[-_]+/g, " ").trim();
  return s ? s[0].toUpperCase() + s.slice(1) : stepId;
}

const cardStyle = { border: "1px solid var(--hairline)", boxShadow: "var(--shadow-card)" };

export default function SprintDetail() {
  const { id = "" } = useParams();
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
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

      <Card padding="lg" radius="md" style={cardStyle}>
        <div className="eyebrow" style={{ marginBottom: 4 }}>plan · {s.plan.length} {s.plan.length === 1 ? "step" : "steps"}</div>
        <Text size="xs" c="dimmed" mb="md">What the AI will run, in order. The exact command is shown for each step.</Text>
        <Stack gap={14}>
          {s.plan.map((step, i) => {
            const done = s.completed_steps.includes(step.id);
            return (
              <div key={step.id}>
                <Group gap={9} wrap="nowrap" align="center" mb={6}>
                  <span style={{ color: done ? "var(--st-done)" : "var(--ink-faint)", fontSize: 14 }}>{done ? "✓" : `${i + 1}.`}</span>
                  <Text size="sm" fw={600}>{humanizeStep(step.id)}</Text>
                  {done && <span className="mono" style={{ fontSize: 11, color: "var(--st-done)" }}>done</span>}
                </Group>
                <div style={{ paddingLeft: 24 }}>
                  <div className="eyebrow" style={{ fontSize: 9.5, marginBottom: 4 }}>command</div>
                  <Code block style={{ fontSize: 12, whiteSpace: "pre-wrap", wordBreak: "break-word", color: "var(--ink-muted)" }}>{step.run}</Code>
                </div>
              </div>
            );
          })}
        </Stack>
      </Card>

      <SprintEditModal sprint={s} opened={editing} onClose={() => setEditing(false)} onDone={refresh} />
    </Stack>
  );
}
