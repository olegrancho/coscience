import { ActionIcon, Button, Card, Group, Loader, Stack, Text, TextInput, Tooltip } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { type Components } from "react-markdown";
import Md from "../components/Md";
import { FeedbackThread } from "../components/FeedbackThread";
import { api } from "../api";
import { AbsTime, BackLink, EmptyState, ModelSelect, RelTime, StatusBadge, VoteControl } from "../components/ui";
import ProposeSprintModal from "../components/ProposeSprintModal";
import LineageCard from "../components/LineageCard";

const cardStyle = { border: "1px solid var(--hairline)", boxShadow: "var(--shadow-card)" };

export default function ProgramDetail() {
  const { id = "" } = useParams();
  const qc = useQueryClient();
  const [note, setNote] = useState("");
  const [proposing, setProposing] = useState(false);
  const [replanning, setReplanning] = useState(false);
  const [statusFilter, setStatusFilter] = useState("all");
  const [showAll, setShowAll] = useState(false);
  const [ideasExpanded, setIdeasExpanded] = useState(false);

  const program = useQuery({ queryKey: ["program", id], queryFn: () => api.getProgram(id) });
  const guidance = useQuery({ queryKey: ["guidance", id], queryFn: () => api.listGuidance(id) });
  const ideas = useQuery({ queryKey: ["ideas", id], queryFn: () => api.listIdeas(id) });
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["program", id] });
    qc.invalidateQueries({ queryKey: ["guidance", id] });
    qc.invalidateQueries({ queryKey: ["ideas", id] });
    qc.invalidateQueries({ queryKey: ["sprints"] });
  };

  if (program.isLoading) return <Loader color="machine" />;
  if (program.error || !program.data) {
    return <EmptyState title="Program not found">Nothing here at “{id}”.</EmptyState>;
  }
  const p = program.data;

  // The PM report mentions experiments by id (as `code` chips). Turn any that
  // belong to this program into links to the experiment page.
  const sprintIds = new Set(p.sprints.map((s) => s.id));
  const reportComponents: Components = {
    code({ className, children, node: _node, ...rest }) {
      const text = String(children).replace(/\n$/, "");
      if (sprintIds.has(text)) {
        return <Link to={`/sprints/${text}`} className="report-sprint-link">{text}</Link>;
      }
      return <code className={className} {...rest}>{children}</code>;
    },
  };

  const setStatus = async (status: string, verb: string) => {
    try { await api.setProgramStatus(id, status); notifications.show({ color: "teal", title: verb, message: `Program ${status}.` }); refresh(); }
    catch (e) { notifications.show({ color: "red", title: `Couldn't ${verb.toLowerCase()}`, message: String(e) }); }
  };
  const addNote = async () => {
    if (!note.trim()) return;
    try { await api.addGuidance(id, note.trim()); setNote(""); notifications.show({ color: "teal", title: "Guidance added", message: "The AI will weigh it next cycle." }); refresh(); }
    catch (e) { notifications.show({ color: "red", title: "Couldn't add", message: String(e) }); }
  };
  const replyGuidance = async (tid: string, text: string) => {
    try { await api.addGuidance(id, text, tid); refresh(); }
    catch (e) { notifications.show({ color: "red", title: "Couldn't reply", message: String(e) }); }
  };
  const completeGuidance = async (tid: string) => {
    try { await api.completeGuidanceThread(id, tid); refresh(); }
    catch (e) { notifications.show({ color: "red", title: "Couldn't complete", message: String(e) }); }
  };
  const reopenGuidance = async (tid: string) => {
    try { await api.reopenGuidanceThread(id, tid); refresh(); }
    catch (e) { notifications.show({ color: "red", title: "Couldn't reopen", message: String(e) }); }
  };
  const deleteGuidance = async (tid: string) => {
    try { await api.deleteGuidance(id, tid); refresh(); }
    catch (e) { notifications.show({ color: "red", title: "Couldn't delete", message: String(e) }); }
  };
  const seenGuidance = async (tid: string) => {
    try { await api.seenGuidanceThread(id, tid); refresh(); }
    catch (e) { notifications.show({ color: "red", title: "Couldn't mark seen", message: String(e) }); }
  };
  const setPmModel = async (model: string) => {
    try { await api.setProgramModel(id, model); notifications.show({ color: "teal", title: "Planner model set", message: model ? `The PM will plan on ${model}.` : "Back to the default model." }); refresh(); }
    catch (e) { notifications.show({ color: "red", title: "Couldn't set model", message: String(e) }); }
  };
  const replan = async () => {
    setReplanning(true);
    try {
      const r = await api.replan(id);
      const msg = r.busy ? "The PM is already reasoning — try again in a moment."
        : r.throttled ? "Claude usage is exhausted; it will resume after the reset."
        : r.submitted?.length ? `Proposed ${r.submitted.join(", ")}.`
        : "Re-planned — no new proposals.";
      notifications.show({ color: r.busy || r.throttled ? "yellow" : "teal", title: "Replan", message: msg });
      refresh();
    } catch (e) { notifications.show({ color: "red", title: "Replan failed", message: String(e) }); }
    finally { setReplanning(false); }
  };
  const saveWorkdir = async (value: string) => {
    try {
      const r = await api.setProgramWorkdir(id, value.trim());
      notifications.show({
        color: r.workdir && !r.exists ? "yellow" : "teal",
        title: "Project folder set",
        message: !r.workdir ? "Agents for this program run in the control repo."
          : r.exists ? `Agents run in ${r.workdir}.`
          : `Saved, but ${r.workdir} doesn't exist yet — agents fall back to the control repo until it does.`,
      });
      refresh();
    } catch (e) { notifications.show({ color: "red", title: "Couldn't set folder", message: String(e) }); }
  };

  return (
    <Stack gap="lg">
      <div>
        <BackLink to="/programs">Programs</BackLink>
        <Group justify="space-between" align="flex-start" wrap="nowrap">
          <h1 style={{ fontFamily: "'Space Grotesk', sans-serif", fontSize: 24, fontWeight: 600, margin: 0 }}>{p.title || p.id}</h1>
          <Group gap={8} wrap="nowrap">
            {p.status !== "active" && <Button variant="light" color="machine" onClick={() => setStatus("active", "Resumed")}>Resume</Button>}
            {p.status === "active" && <Button variant="light" color="signal" onClick={() => setStatus("paused", "Paused")}>Pause</Button>}
            {p.status !== "closed" && <Button variant="default" onClick={() => setStatus("closed", "Closed")}>Close</Button>}
            <Button variant="light" color="machine" loading={replanning} onClick={replan}
                    title="Run the PM planner now instead of waiting for its next cycle">Replan now</Button>
            <Button color="machine" onClick={() => setProposing(true)}>Propose experiment</Button>
            <Tooltip label="Chat with the PM planner" withArrow>
              <ActionIcon variant="light" color="green" size="lg" radius="md"
                          component={Link} to={`/programs/${id}/chat`} aria-label="chat with the planner">
                💬
              </ActionIcon>
            </Tooltip>
          </Group>
        </Group>
        <Group gap={10} mt={9} align="center">
          <StatusBadge status={p.status} />
          <Text size="sm" c="dimmed">the AI has run <span className="mono">{p.cycle}</span> planning {p.cycle === 1 ? "cycle" : "cycles"}</Text>
          <ModelSelect value={p.pm_model} onChange={setPmModel} label="planner model" />
        </Group>
        <Group gap={8} mt={8} align="center" wrap="nowrap">
          <span className="eyebrow" style={{ whiteSpace: "nowrap" }}>project folder</span>
          <TextInput
            key={p.workdir}
            size="xs"
            className="mono"
            defaultValue={p.workdir}
            placeholder="control repo — set a path to run this program's agents there"
            style={{ minWidth: 380, flex: 1, maxWidth: 560 }}
            onKeyDown={(e) => { if (e.key === "Enter") (e.currentTarget as HTMLInputElement).blur(); }}
            onBlur={(e) => { if (e.currentTarget.value.trim() !== p.workdir) saveWorkdir(e.currentTarget.value); }}
          />
        </Group>
      </div>

      {p.goals && <Text c="dimmed" style={{ maxWidth: 680 }}>{p.goals}</Text>}

      <Card padding="lg" radius="md" style={cardStyle}>
        <div className="eyebrow" style={{ marginBottom: 12 }}>the AI's status report</div>
        {p.report ? <div className="report-leaf"><Md components={reportComponents}>{p.report}</Md></div>
          : <Text size="sm" c="dimmed">No report yet — the AI writes one each planning cycle.</Text>}
      </Card>

      {p.activations?.length > 0 && (
        <Card padding="lg" radius="md" style={cardStyle}>
          <div className="eyebrow" style={{ marginBottom: 10 }}>PM activity — when it planned and why</div>
          <Stack gap={7}>
            {p.activations.slice(0, 12).map((a, i) => (
              <Group key={i} justify="space-between" wrap="nowrap" align="baseline"
                style={{ borderBottom: "1px solid var(--hairline)", paddingBottom: 6 }}>
                <Text size="sm" style={{ minWidth: 0 }}>
                  <span className="mono" style={{ color: "var(--ink-faint)" }}>#{a.cycle}</span>{" "}
                  {(a.triggers?.length ? a.triggers.join(", ") : "reasoned")}
                  {a.forced && a.triggers?.[0] !== "manual replan" && " · manual"}
                  {a.submitted?.length ? <span style={{ color: "var(--machine)" }}> → proposed {a.submitted.length}</span> : null}
                </Text>
                <RelTime at={a.at} />
              </Group>
            ))}
          </Stack>
        </Card>
      )}

      <Card padding="lg" radius="md" style={cardStyle}>
        <div className="eyebrow" style={{ marginBottom: 4 }}>your guidance to the AI</div>
        <Text size="xs" c="dimmed" mb="sm">Standing direction the AI weighs every cycle and replies to — mark a thread complete once it's handled.</Text>
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
            <TextInput style={{ flex: 1 }} placeholder="Add a note for the AI…" value={note}
              onChange={(e) => setNote(e.currentTarget.value)}
              onKeyDown={(e) => e.key === "Enter" && addNote()} />
            <Button variant="light" color="machine" onClick={addNote}>Add</Button>
          </Group>
        </Stack>
      </Card>

      {(() => {
        // Filter by status, then (unless "show all") cap the noisy terminal
        // statuses — done and canceled — at their 3 most recent (sprints arrive
        // newest-first). Active statuses are always shown in full.
        const CAPPED = new Set(["done", "canceled"]);
        const CAP = 3;
        const filtered = statusFilter === "all"
          ? p.sprints : p.sprints.filter((s) => s.status === statusFilter);
        const seen: Record<string, number> = {};
        const hidden = new Set<string>();
        if (!showAll) {
          for (const s of filtered) {
            if (!CAPPED.has(s.status)) continue;
            seen[s.status] = (seen[s.status] ?? 0) + 1;
            if (seen[s.status] > CAP) hidden.add(s.id);
          }
        }
        const shown = filtered.filter((s) => !hidden.has(s.id));
        const counts = p.sprints.reduce<Record<string, number>>((a, s) => {
          a[s.status] = (a[s.status] ?? 0) + 1; return a;
        }, {});
        const order = ["proposed", "approved", "queued", "executing", "failed", "done", "canceled"];
        return (
          <Card padding="lg" radius="md" style={cardStyle}>
            <Group justify="space-between" align="center" mb={12} wrap="nowrap">
              <div className="eyebrow">experiments · {p.sprints.length}</div>
              <select className="mono" value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value)}
                style={{ fontSize: 12, padding: "3px 6px", background: "var(--surface)",
                         color: "var(--ink)", border: "1px solid var(--hairline)", borderRadius: 6 }}>
                <option value="all">all statuses ({p.sprints.length})</option>
                {order.filter((st) => counts[st]).map((st) => (
                  <option key={st} value={st}>{st} ({counts[st]})</option>
                ))}
              </select>
            </Group>
            {p.sprints.length === 0 ? (
              <Text size="sm" c="dimmed">None yet. Propose one, or let the AI propose on its next cycle.</Text>
            ) : shown.length === 0 ? (
              <Text size="sm" c="dimmed">No {statusFilter} experiments.</Text>
            ) : (
              <Stack gap={2}>
                {shown.map((s) => (
                  <div key={s.id}
                    style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, padding: "10px 6px", borderBottom: "1px solid var(--hairline)" }}>
                    <div style={{ minWidth: 0, flex: 1, display: "flex", alignItems: "center", gap: 10 }}>
                      <Link to={`/sprints/${s.id}`} style={{ minWidth: 0, textDecoration: "none", color: "inherit" }}>
                        <Text size="sm" truncate>{s.title || s.goals || s.id}</Text>
                      </Link>
                      {s.results.length > 0 && (
                        <Link to={`/results/${s.results[0]}`} className="view" style={{ fontSize: 12, whiteSpace: "nowrap", flexShrink: 0 }}>result ready →</Link>
                      )}
                    </div>
                    <Group gap={12} wrap="nowrap">
                      {(s.votes.up > 0 || s.votes.down > 0) && <VoteControl votes={s.votes} size="xs" />}
                      {s.last_status_at ? (
                        <Text size="xs" c="dimmed" style={{ whiteSpace: "nowrap" }}>
                          <AbsTime at={s.last_status_at} dateOnly />
                        </Text>
                      ) : null}
                      <StatusBadge status={s.status} />
                    </Group>
                  </div>
                ))}
                {(hidden.size > 0 || showAll) && (
                  <button type="button" className="linklike" style={{ alignSelf: "flex-start", marginTop: 8 }}
                    onClick={() => setShowAll((v) => !v)}>
                    {showAll ? "Show fewer" : `Show all (${hidden.size} more)`}
                  </button>
                )}
              </Stack>
            )}
          </Card>
        );
      })()}

      <Card padding="lg" radius="md" style={cardStyle}>
        <Group justify="space-between" align="center" mb={ideas.data?.summary.trim() ? 10 : 0}>
          <div className="eyebrow">ideas · {ideas.data?.ideas.length ?? 0}</div>
          <Link to={`/programs/${id}/ideas`} className="view" style={{ fontSize: 13 }}>open ideas →</Link>
        </Group>
        {ideas.data?.summary.trim()
          ? (
            <div onClick={() => setIdeasExpanded((v) => !v)} style={{ cursor: "pointer" }}
                 title={ideasExpanded ? "Click to collapse" : "Click to read the full summary"}>
              {ideasExpanded
                ? <div className="report-leaf"><Md>{ideas.data.summary}</Md></div>
                : <Text size="sm" c="dimmed" lineClamp={2}>{ideas.data.summary.replace(/[#*`>_]/g, "")}</Text>}
            </div>
          )
          : <Text size="sm" c="dimmed">A pool of candidate directions the AI grows, prunes, and promotes into experiments.</Text>}
      </Card>

      <LineageCard programId={id} />

      <ProposeSprintModal programId={id} opened={proposing} onClose={() => setProposing(false)} onDone={refresh} />
    </Stack>
  );
}
