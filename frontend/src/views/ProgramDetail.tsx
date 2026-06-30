import { ActionIcon, Button, Card, Group, Loader, Stack, Text, TextInput } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { type Components } from "react-markdown";
import Md from "../components/Md";
import { api } from "../api";
import { BackLink, EmptyState, ModelSelect, StatusBadge } from "../components/ui";
import ProposeSprintModal from "../components/ProposeSprintModal";

const cardStyle = { border: "1px solid var(--hairline)", boxShadow: "var(--shadow-card)" };

export default function ProgramDetail() {
  const { id = "" } = useParams();
  const qc = useQueryClient();
  const [note, setNote] = useState("");
  const [proposing, setProposing] = useState(false);
  const [showAllRejected, setShowAllRejected] = useState(false);

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
  const delNote = async (nid: string) => {
    try { await api.removeGuidance(id, nid); refresh(); }
    catch (e) { notifications.show({ color: "red", title: "Couldn't remove", message: String(e) }); }
  };
  const setPmModel = async (model: string) => {
    try { await api.setProgramModel(id, model); notifications.show({ color: "teal", title: "Planner model set", message: model ? `The PM will plan on ${model}.` : "Back to the default model." }); refresh(); }
    catch (e) { notifications.show({ color: "red", title: "Couldn't set model", message: String(e) }); }
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
            <Button color="machine" onClick={() => setProposing(true)}>Propose experiment</Button>
          </Group>
        </Group>
        <Group gap={10} mt={9} align="center">
          <StatusBadge status={p.status} />
          <Text size="sm" c="dimmed">the AI has run <span className="mono">{p.cycle}</span> planning {p.cycle === 1 ? "cycle" : "cycles"}</Text>
          <ModelSelect value={p.pm_model} onChange={setPmModel} label="planner model" />
        </Group>
      </div>

      {p.goals && <Text c="dimmed" style={{ maxWidth: 680 }}>{p.goals}</Text>}

      <Card padding="lg" radius="md" style={cardStyle}>
        <div className="eyebrow" style={{ marginBottom: 12 }}>the AI's status report</div>
        {p.report ? <div className="report-leaf"><Md components={reportComponents}>{p.report}</Md></div>
          : <Text size="sm" c="dimmed">No report yet — the AI writes one each planning cycle.</Text>}
      </Card>

      <Card padding="lg" radius="md" style={cardStyle}>
        <div className="eyebrow" style={{ marginBottom: 4 }}>your guidance to the AI</div>
        <Text size="xs" c="dimmed" mb="sm">Standing notes the AI weighs every cycle. Remove one when it's handled.</Text>
        <Stack gap={8}>
          {(guidance.data ?? []).map((g) => (
            <Group key={g.id} justify="space-between" wrap="nowrap" style={{ background: "var(--paper)", borderRadius: 8, padding: "8px 12px" }}>
              <Text size="sm">{g.text}</Text>
              <ActionIcon variant="subtle" color="gray" onClick={() => delNote(g.id)} aria-label="remove">✕</ActionIcon>
            </Group>
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
        // Rejected (canceled) experiments pile up; show only the 3 most recent
        // (sprints arrive newest-first) and tuck the rest behind "show all".
        const rejectedIds = p.sprints.filter((s) => s.status === "canceled").map((s) => s.id);
        const hidden = new Set(showAllRejected ? [] : rejectedIds.slice(3));
        return (
          <Card padding="lg" radius="md" style={cardStyle}>
            <div className="eyebrow" style={{ marginBottom: 12 }}>experiments · {p.sprints.length}</div>
            {p.sprints.length === 0 ? (
              <Text size="sm" c="dimmed">None yet. Propose one, or let the AI propose on its next cycle.</Text>
            ) : (
              <Stack gap={2}>
                {p.sprints.filter((s) => !hidden.has(s.id)).map((s) => (
                  <div key={s.id}
                    style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, padding: "10px 6px", borderBottom: "1px solid var(--hairline)" }}>
                    <Link to={`/sprints/${s.id}`} style={{ minWidth: 0, flex: 1, textDecoration: "none", color: "inherit" }}>
                      <Text size="sm" truncate>{s.title || s.goals || s.id}</Text>
                    </Link>
                    <Group gap={12} wrap="nowrap">
                      {s.results.length > 0 && (
                        <Link to={`/results/${s.results[0]}`} className="view" style={{ fontSize: 13 }}>result →</Link>
                      )}
                      <StatusBadge status={s.status} />
                    </Group>
                  </div>
                ))}
                {rejectedIds.length > 3 && (
                  <button type="button" className="linklike" style={{ alignSelf: "flex-start", marginTop: 8 }}
                    onClick={() => setShowAllRejected((v) => !v)}>
                    {showAllRejected ? "Show fewer" : `Show all ${rejectedIds.length} rejected`}
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
          ? <Text size="sm" c="dimmed" lineClamp={2}>{ideas.data.summary.replace(/[#*`>_]/g, "")}</Text>
          : <Text size="sm" c="dimmed">A pool of candidate directions the AI grows, prunes, and promotes into experiments.</Text>}
      </Card>

      <ProposeSprintModal programId={id} opened={proposing} onClose={() => setProposing(false)} onDone={refresh} />
    </Stack>
  );
}
