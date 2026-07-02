import { ActionIcon, Button, Card, Group, Loader, Menu, SegmentedControl, SimpleGrid, Stack, Text, Textarea, Tooltip } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import Md from "../components/Md";
import { Transcript } from "../components/Transcript";
import { api, type SprintFile } from "../api";
import { availableActions, type SprintStatus } from "../sprintActions";
import { BackLink, EmptyState, LiveActivity, ModelSelect, RelTime, StatusBadge, VoteControl, voterId } from "../components/ui";
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
function FileBlock({ f, sprintId, live }: { f: SprintFile; sprintId: string; live?: boolean }) {
  const [open, setOpen] = useState(f.kind === "scratchpad" || f.kind === "log");
  const [full, setFull] = useState(false);
  const isMd = f.name.endsWith(".md");

  // Only fetch the untruncated file when the user asks for it (and there's more to
  // show). Poll it too while the agent runs so "full log" stays live like the tail.
  const fullQ = useQuery({
    queryKey: ["sprint-file", sprintId, f.name],
    queryFn: () => api.getSprintFile(sprintId, f.name),
    enabled: open && full && f.truncated,
    refetchInterval: live ? 5000 : false,
  });
  const showingFull = full && f.truncated;
  const content = showingFull ? (fullQ.data?.content ?? f.content) : f.content;

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
          {f.truncated && (
            <Group gap={10} align="center" mb={10}>
              <SegmentedControl
                size="xs" value={full ? "full" : "recent"}
                onChange={(v) => setFull(v === "full")}
                data={[{ label: "Most recent", value: "recent" }, { label: "Full log", value: "full" }]}
              />
              <Text size="xs" c="dimmed">
                {showingFull
                  ? `full file · ${fmtSize(f.size)}`
                  : `tail · most recent ${Math.round(f.content.length / 1024)} KB of ${fmtSize(f.size)}`}
              </Text>
              {showingFull && fullQ.isFetching && <Loader size="xs" />}
            </Group>
          )}
          {f.binary ? (
            <Text size="sm" c="dimmed">Binary file — not shown.</Text>
          ) : showingFull && fullQ.isLoading ? (
            <Group gap={8}><Loader size="xs" /><Text size="sm" c="dimmed">Loading full log…</Text></Group>
          ) : showingFull && fullQ.isError ? (
            <Text size="sm" c="red">Couldn't load the full log.</Text>
          ) : !content.trim() ? (
            <Text size="sm" c="dimmed">Empty.</Text>
          ) : isMd ? (
            <div className="report-leaf"><Md>{content}</Md></div>
          ) : f.kind === "log" ? (
            <Transcript raw={content} />
          ) : (
            <pre className="mono" style={{ margin: 0, whiteSpace: "pre-wrap", wordBreak: "break-word",
                 fontSize: 12.5, lineHeight: 1.5, maxHeight: 440, overflow: "auto" }}>{content}</pre>
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
        {docs.map((f) => <FileBlock key={f.name} f={f} sprintId={sprintId} live={live} />)}
      </Stack>
    </Card>
  );
}

export default function SprintDetail() {
  const { id = "" } = useParams();
  const nav = useNavigate();
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [comment, setComment] = useState("");
  const [commentTarget, setCommentTarget] = useState<"worker" | "pm">("worker");
  const prog = programOf(id);
  const sprint = useQuery({
    queryKey: ["sprint", id], queryFn: () => api.getSprint(id, voterId()),
    refetchInterval: (q) => (q.state.data?.status === "executing" ? 5000 : false),
  });
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
    try { await api.approveSprint(id); notifications.show({ color: "teal", title: "Approved", message: "Added to the PM's queue — it schedules and runs it when ready. Use Run to force it now." }); refresh(); }
    catch (e) { notifications.show({ color: "red", title: "Couldn't approve", message: String(e) }); }
  };
  const run = async () => {
    try { await api.runSprint(id); notifications.show({ color: "teal", title: "Released to run", message: "Queued — it starts as soon as a resource slot is free." }); refresh(); }
    catch (e) { notifications.show({ color: "red", title: "Couldn't run", message: String(e) }); }
  };
  const sendBack = async () => {
    try { await api.sendBackSprint(id); notifications.show({ color: "gray", title: "Sent back", message: "Returned to Proposed for reconsideration." }); refresh(); }
    catch (e) { notifications.show({ color: "red", title: "Couldn't send back", message: String(e) }); }
  };
  const reject = async () => {
    try { await api.rejectSprint(id); notifications.show({ color: "gray", title: "Canceled", message: "This sprint won't run." }); refresh(); }
    catch (e) { notifications.show({ color: "red", title: "Couldn't cancel", message: String(e) }); }
  };
  const vote = async (value: number) => {
    try {
      const votes = await api.voteSprint(id, voterId(), value);
      qc.setQueryData(["sprint", id], (old: typeof s | undefined) => (old ? { ...old, votes } : old));
    } catch (e) { notifications.show({ color: "red", title: "Couldn't vote", message: String(e) }); }
  };
  const demote = async () => {
    if (!window.confirm("Demote this experiment to an idea? The AI won't be able to promote it back to a sprint (you can lift that later in the idea pool).")) return;
    try {
      await api.demoteSprint(id);
      notifications.show({ color: "teal", title: "Demoted to idea", message: "Moved to the idea pool as a demoted idea; the AI can't re-promote it." });
      nav(`/programs/${prog}`);
    } catch (e) { notifications.show({ color: "red", title: "Couldn't demote", message: String(e) }); }
  };
  const setModel = async (model: string) => {
    const live = s.status === "executing" && s.agent_running;
    try {
      await api.editSprint(id, { model });
      notifications.show({ color: "teal", title: "Model set",
        message: live ? "Restarting the agent on the new model — it resumes from its scratchpad."
                      : model ? `This sprint will run on ${model}.` : "Back to the default model." });
      refresh();
    } catch (e) { notifications.show({ color: "red", title: "Couldn't set model", message: String(e) }); }
  };
  const addComment = async () => {
    if (!comment.trim()) return;
    const msg = commentTarget === "pm"
      ? "The AI planner reads this and may revise the sprint or follow up."
      : "The research agent reads this as direction on its next run.";
    try { await api.addSprintComment(id, comment.trim(), commentTarget); setComment(""); notifications.show({ color: "teal", title: "Comment added", message: msg }); refresh(); }
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
            {actions.includes("approve") && (
              <Tooltip label="Authorize this sprint — it joins the PM's queue to schedule and run. (Run forces it now.)" withArrow openDelay={300}>
                <Button color="signal" onClick={approve}>Approve</Button>
              </Tooltip>
            )}
            {actions.includes("run") && s.status === "approved" && (
              <Tooltip label="Release to the scheduler — runs as soon as a resource slot is free (may queue)." withArrow openDelay={300}>
                <Button color="signal" onClick={run}>Run</Button>
              </Tooltip>
            )}
            {actions.includes("sendBack") && (
              <Tooltip label="Return to Proposed for reconsideration." withArrow openDelay={300}>
                <Button variant="default" onClick={sendBack}>Send back</Button>
              </Tooltip>
            )}
            {actions.includes("reject") && (
              <Tooltip label={s.status === "queued" ? "Pull it out of the run queue and cancel." : "Cancel this sprint — it won't run."} withArrow openDelay={300}>
                <Button variant="default" color="red" onClick={reject}>{s.status === "queued" ? "Cancel" : "Reject"}</Button>
              </Tooltip>
            )}
            {(actions.includes("edit") || actions.includes("demote")
              || (actions.includes("run") && s.status === "proposed")) && (
              <Menu position="bottom-end" withArrow>
                <Menu.Target>
                  <Tooltip label="More actions" withArrow openDelay={300}>
                    <ActionIcon variant="default" aria-label="more actions" size="lg">⋯</ActionIcon>
                  </Tooltip>
                </Menu.Target>
                <Menu.Dropdown>
                  {actions.includes("run") && s.status === "proposed" &&
                    <Menu.Item onClick={run}>Run now (approve &amp; release)</Menu.Item>}
                  {actions.includes("edit") &&
                    <Menu.Item onClick={() => setEditing(true)}>Edit goals / plan / resources…</Menu.Item>}
                  {actions.includes("demote") &&
                    <Menu.Item onClick={demote}>Demote to idea…</Menu.Item>}
                </Menu.Dropdown>
              </Menu>
            )}
          </Group>
        </Group>
        <Group gap={10} mt={9} align="center">
          <StatusBadge status={s.status} />
          <span className="mono" style={{ fontSize: 12, color: "var(--ink-faint)" }}>ref {s.id}</span>
          {s.status === "queued" && (
            <span style={{ fontSize: 12, color: "var(--st-queued)" }}>waiting for a compute slot…</span>
          )}
          {s.status === "executing" && <LiveActivity activity={s.activity} agentRunning={s.agent_running} />}
          <span style={{ marginLeft: "auto" }}><VoteControl votes={s.votes} onVote={vote} /></span>
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

      {s.status === "failed" && (
        <Card padding="lg" radius="md" style={{ border: "1px solid var(--signal-line)", background: "var(--signal-weak)" }}>
          <div className="eyebrow" style={{ marginBottom: 8, color: "var(--signal)" }}>this experiment failed</div>
          <Text size="sm" mb={s.error ? 8 : 0}>The research agent gave up after repeated errors. The AI sees this and will rethink the approach.</Text>
          {s.error && (
            <pre className="mono" style={{ margin: 0, whiteSpace: "pre-wrap", wordBreak: "break-word",
              fontSize: 12, lineHeight: 1.5, maxHeight: 240, overflow: "auto", color: "var(--ink-muted)" }}>{s.error}</pre>
          )}
        </Card>
      )}

      <Card padding="lg" radius="md" style={cardStyle}>
        <div className="eyebrow" style={{ marginBottom: 10 }}>your feedback{s.comments.length ? ` · ${s.comments.length}` : ""}</div>
        <Stack gap={10}>
          {s.comments.map((c) => {
            const toPm = c.target === "pm";
            return (
              <div key={c.id} style={{ background: "var(--paper)", borderRadius: 8, padding: "8px 12px" }}>
                <div className="md-tight"><Md>{c.text}</Md></div>
                <Group gap={8} mt={3} wrap="nowrap">
                  <span className="mono" style={{ fontSize: 10.5, padding: "0px 6px", borderRadius: 999,
                    color: toPm ? "var(--signal)" : "var(--machine)",
                    background: toPm ? "var(--signal-weak)" : "var(--machine-weak)" }}>
                    {toPm ? "→ planner" : "→ agent"}
                  </span>
                  <Text size="xs" c="dimmed"><RelTime at={c.added_at} /></Text>
                </Group>
              </div>
            );
          })}
          <SegmentedControl size="xs" value={commentTarget} onChange={(v) => setCommentTarget(v as "worker" | "pm")}
            data={[{ label: "To the agent", value: "worker" }, { label: "To the planner (AI)", value: "pm" }]} />
          <Text size="xs" c="dimmed">
            {commentTarget === "pm"
              ? "The planner reads this and can rewrite the sprint's goals/plan while it's still proposed, or propose a follow-up."
              : "The running agent reads this as direction on its next run."}
          </Text>
          <Group gap={8} align="flex-start">
            <Textarea style={{ flex: 1 }} placeholder={commentTarget === "pm" ? "e.g. rewrite the goals to focus on…" : "Direction for the agent…"}
              autosize minRows={1} value={comment} onChange={(e) => setComment(e.currentTarget.value)} />
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
            <Group justify="space-between" wrap="nowrap">
              <Text size="sm" c="dimmed">Model</Text>
              <ModelSelect value={s.model} onChange={setModel} disabled={isDone || s.status === "canceled"} />
            </Group>
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
