import { ActionIcon, Badge, Button, Card, Group, Loader, Select, Stack, Text, Textarea, TextInput, Tooltip } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Link, useLocation, useNavigationType, useParams } from "react-router-dom";
import { type Components } from "react-markdown";
import Md from "../components/Md";
import { FeedbackThread } from "../components/FeedbackThread";
import DirectoryPickerModal from "../components/DirectoryPickerModal";
import { api } from "../api";
import { AbsTime, BackLink, EmptyState, ModelSelect, RelTime, StatusBadge, VoteControl, ZoomableImg, isImageName, liveChatId, sendOnCtrlEnter } from "../components/ui";
import ProposeSprintModal from "../components/ProposeSprintModal";
import ProgramSettingsModal from "../components/ProgramSettingsModal";
import LineageCard from "../components/LineageCard";
import HostNotesCard, { noteRows } from "../components/HostNotesCard";
import type { ArtifactRow, WikiSummary } from "../api";
import { TYPE_HUE } from "../components/wikiGraphStyle";
import { isUnseen, seedIfNew } from "../sprintSeen";
import { takeReturnRow } from "../returnRow";
import { experimentRows } from "./experimentsList";
import PageToc, { type TocEntry } from "../components/PageToc";

const cardStyle = { border: "1px solid var(--hairline)", boxShadow: "var(--shadow-card)" };

const THUMB_PX = 44;

/** The picture an artifact holds, riding at the right of the card's subtitle row.
 *  Sized to that row so the card keeps the height it had before thumbnails existed.
 *  Null when the current version has no image. */
function ArtifactThumb({ pid, a }: { pid: string; a: ArtifactRow }) {
  const img = (a.files ?? []).find(isImageName);
  if (!img || !a.current) return null;
  return (
    <ZoomableImg src={api.artifactVersionRawUrl(pid, a.id, a.current, img)} alt={img} loading="lazy"
         style={{ width: THUMB_PX, height: THUMB_PX, flexShrink: 0, objectFit: "contain",
                  borderRadius: 4, padding: 2,
                  // Plots are usually saved on a transparent background — without an
                  // explicit white tile they disappear against the dark theme.
                  background: "#fff", border: "1px solid var(--hairline)" }} />
  );
}

export default function ProgramDetail() {
  const { id = "" } = useParams();
  const qc = useQueryClient();
  const [note, setNote] = useState("");
  // The instructions being edited, or null when the card is just displaying them.
  const [draft, setDraft] = useState<string | null>(null);
  const [proposing, setProposing] = useState(false);
  const [replanning, setReplanning] = useState(false);
  const [statusFilter, setStatusFilter] = useState("all");
  const [showAll, setShowAll] = useState(false);
  const [onlyNew, setOnlyNew] = useState(false);
  // The experiment this page was left for, when it is reached by going back (P5).
  const [returned, setReturned] = useState<string | null>(null);
  const location = useLocation();
  const navType = useNavigationType();
  const [ideasExpanded, setIdeasExpanded] = useState(false);
  const [pmExpanded, setPmExpanded] = useState(false);
  const [browsing, setBrowsing] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  // null = the current report; a number = an earlier cycle's, kept since E2.
  const [pastCycle, setPastCycle] = useState<number | null>(null);

  const program = useQuery({ queryKey: ["program", id], queryFn: () => api.getProgram(id) });
  const past = useQuery({
    queryKey: ["program-report", id, pastCycle],
    queryFn: () => api.getProgramReport(id, pastCycle as number),
    enabled: pastCycle !== null,
  });
  const guidance = useQuery({ queryKey: ["guidance", id], queryFn: () => api.listGuidance(id) });
  const ideas = useQuery({ queryKey: ["ideas", id], queryFn: () => api.listIdeas(id) });
  const artifacts = useQuery({ queryKey: ["artifacts", id], queryFn: () => api.listArtifacts(id) });
  const wiki = useQuery({ queryKey: ["wiki", id], queryFn: () => api.getWikiSummary(id) });
  // The same query the server-notes card uses, by the same key — React Query hands
  // back the one cached result, so this costs no extra request. The nav needs to know
  // whether that card will draw anything, and `noteRows` is what decides. The servers
  // come with the notes (O23); this page no longer polls the ledger at all.
  const hostNotes = useQuery({ queryKey: ["host-notes", id], queryFn: () => api.getHostNotes(id) });
  const hasServerNotes = !!hostNotes.data && noteRows(hostNotes.data).length > 0;
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["program", id] });
    qc.invalidateQueries({ queryKey: ["guidance", id] });
    qc.invalidateQueries({ queryKey: ["ideas", id] });
    qc.invalidateQueries({ queryKey: ["sprints"] });
  };

  useEffect(() => {
    if (program.data) seedIfNew(program.data.sprints, program.data.id);
  }, [program.data]);

  // P5. Once per arrival, as soon as the list exists: going back (the back link, or the
  // browser's Back) lands on the experiment just left; any other arrival discards the
  // record, so a later visit from the nav still starts at the top.
  const loaded = !!program.data;
  useEffect(() => {
    if (!loaded) return;
    const sid = takeReturnRow(id);
    const back = navType === "POP" || !!(location.state as { back?: boolean } | null)?.back;
    setReturned(back ? sid : null);
    // Arrival is what matters, not every later change of navigation state.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loaded, id]);
  useEffect(() => {
    if (!returned) return;
    const raf = requestAnimationFrame(() => {
      // The row, or the section if a filter somehow still hides it.
      (document.getElementById(`exp-${returned}`) ?? document.getElementById("sec-experiments"))
        ?.scrollIntoView({ block: "center" });
    });
    return () => cancelAnimationFrame(raf);
  }, [returned]);

  const tocEntries = useMemo<TocEntry[]>(() => {
    const d = program.data;
    if (!d) return [];
    return [
      { id: "sec-report", label: "Report" },
      { id: "sec-instructions", label: "Instructions" },
      { id: "sec-guidance", label: "Guidance" },
      { id: "sec-experiments", label: "Experiments" },
      { id: "sec-ideas", label: "Ideas" },
      { id: "sec-artifacts", label: "Artifacts" },
      { id: "sec-wiki", label: "Wiki" },
      { id: "sec-lineage", label: "Lineage" },
      // Housekeeping, last and in page order. Server notes is listed whenever this
      // program has a server to speak of — which is what the card itself decides, so
      // the entry is gated on the same fact (the servers sent with the notes) rather than on
      // the card having drawn, which the nav cannot see.
      ...(d.activations?.length > 0 ? [{ id: "sec-activity", label: "PM activity" }] : []),
      ...(hasServerNotes ? [{ id: "sec-host-notes", label: "Server notes" }] : []),
    ];
  }, [program.data, hasServerNotes]);

  if (program.isLoading) return <Loader color="machine" />;
  // A failed poll must NEVER replace loaded content: every query polls on a 10s
  // interval and on window focus (main.tsx), so one blip — a backend restart, a
  // proxy hiccup — used to swap a working page for "not found", and it did not
  // come back on the next successful poll. Only absence means absent.
  if (!program.data) {
    return <EmptyState title="Program not found">Nothing here at “{id}”.</EmptyState>;
  }
  const p = program.data;

  // The PM report mentions experiments by id (as `code` chips). Turn any that
  // belong to this program into links to the experiment page.
  const shownReport = pastCycle === null ? p.report : (past.data?.text ?? "");
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
  const saveInstructions = async () => {
    if (draft === null) return;
    try { await api.setProgramInstructions(id, draft); setDraft(null); notifications.show({ color: "teal", title: "Instructions saved", message: "The AI follows them from its next cycle." }); refresh(); }
    catch (e) { notifications.show({ color: "red", title: "Couldn't save", message: String(e) }); }
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
        // Before the throttle line: a paused platform fails the usage gate too, and
        // pointing the human at a usage reset would send them to wait for something
        // that never clears it. Only Resume does.
        : r.paused ? "Paused — Resume in Compute to re-plan."
        : r.throttled ? "Claude usage is exhausted; it will resume after the reset."
        // Stuck, not idle: the planner failed repeatedly on this input and stood
        // down, so it did NOT reason. Never let that read as a healthy re-plan.
        : r.backoff ? "The planner has failed repeatedly on this input and stood down — it did not re-plan. See the failed PM runs (ok: false) in .coscience/runs.jsonl."
        : r.submitted?.length ? `Proposed ${r.submitted.join(", ")}.`
        : "Re-planned — no new proposals.";
      notifications.show({ color: r.busy || r.throttled || r.backoff || r.paused ? "yellow" : "teal", title: "Replan", message: msg });
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
      <PageToc entries={tocEntries} />
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
            <Tooltip label="Program settings" withArrow>
              <ActionIcon variant="light" color="gray" size="lg" radius="md"
                          onClick={() => setSettingsOpen(true)} aria-label="program settings">
                ⚙
              </ActionIcon>
            </Tooltip>
            <Tooltip label="Chat with the PM planner" withArrow>
              <ActionIcon variant="light" color="green" size="lg" radius="md"
                          component={Link} to={`/programs/${id}/chat`} aria-label="chat with the planner">
                💬
              </ActionIcon>
            </Tooltip>
          </Group>
        </Group>
        <ProgramSettingsModal
          opened={settingsOpen}
          onClose={() => setSettingsOpen(false)}
          program={p}
          onSaved={refresh}
        />
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
            rightSectionPointerEvents="all"
            rightSection={
              <Tooltip label="Browse folders on the server" withArrow>
                <ActionIcon variant="subtle" size="sm" aria-label="browse folders"
                            onClick={() => setBrowsing(true)}>📁</ActionIcon>
              </Tooltip>
            }
            onKeyDown={(e) => { if (e.key === "Enter") (e.currentTarget as HTMLInputElement).blur(); }}
            onBlur={(e) => { if (e.currentTarget.value.trim() !== p.workdir) saveWorkdir(e.currentTarget.value); }}
          />
          <DirectoryPickerModal
            opened={browsing}
            initialPath={p.workdir}
            onClose={() => setBrowsing(false)}
            onPick={(picked) => saveWorkdir(picked)}
          />
        </Group>
      </div>

      {p.goals && <Text c="dimmed" style={{ maxWidth: 680 }}>{p.goals}</Text>}

      <Card id="sec-report" padding="lg" radius="md" style={cardStyle}>
        <Group justify="space-between" align="baseline" mb={12} wrap="nowrap">
          <div className="eyebrow">
            {pastCycle === null ? "the AI's status report" : `the AI's report — cycle ${pastCycle}`}
          </div>
          {/* Every cycle's report is kept now (E2); this card used to show only the
              latest, which the next cycle overwrites. */}
          {(p.report_cycles?.length ?? 0) > 0 && (
            <Select size="xs" w={150} allowDeselect={false}
                    aria-label="Which cycle's report to show"
                    value={pastCycle === null ? "latest" : String(pastCycle)}
                    onChange={(v) => setPastCycle(v && v !== "latest" ? Number(v) : null)}
                    data={[{ value: "latest", label: "latest" },
                           ...(p.report_cycles ?? []).map((c) => ({ value: String(c), label: `cycle ${c}` }))]} />
          )}
        </Group>
        {pastCycle !== null && past.isLoading && <Loader size="sm" color="machine" />}
        {shownReport
          ? <div className="report-leaf"><Md components={reportComponents}>{shownReport}</Md></div>
          : pastCycle === null
            ? <Text size="sm" c="dimmed">No report yet — the AI writes one each planning cycle.</Text>
            : !past.isLoading && <Text size="sm" c="dimmed">That cycle's report is no longer kept.</Text>}
      </Card>

      <Card id="sec-instructions" padding="lg" radius="md" style={cardStyle}>
        <Group justify="space-between" align="baseline" mb={4} wrap="nowrap">
          <div className="eyebrow">general instructions</div>
          {draft === null && (
            <button type="button" className="linklike" onClick={() => setDraft(p.instructions)}
              title={p.instructions ? "Edit the general instructions" : "Add general instructions"}>
              {p.instructions ? "Edit" : "Add"}
            </button>
          )}
        </Group>
        <Text size="xs" c="dimmed" mb="sm">
          House rules for this program — style, conventions, what never to do. They sit in
          every planner prompt, so the AI just follows them; unlike guidance, it doesn't
          reply to them or mark them done.
        </Text>
        {draft !== null ? (
          <Stack gap={8}>
            <Textarea autosize minRows={4} value={draft} autoFocus
              placeholder="e.g. Cite a source for every claim. Never propose work needing new equipment. Report numbers in SI units."
              onChange={(e) => setDraft(e.currentTarget.value)} />
            <Group gap={8}>
              <Button variant="light" color="machine" onClick={saveInstructions}>Save</Button>
              <Button variant="subtle" color="gray" onClick={() => setDraft(null)}>Cancel</Button>
            </Group>
          </Stack>
        ) : p.instructions ? (
          <Text size="sm" style={{ whiteSpace: "pre-wrap", maxWidth: 680 }}>{p.instructions}</Text>
        ) : (
          <Text size="sm" c="dimmed">None — the AI works from the goals and your guidance alone.</Text>
        )}
      </Card>

      <Card id="sec-guidance" padding="lg" radius="md" style={cardStyle}>
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
          <Group gap={8} align="flex-end">
            <Textarea style={{ flex: 1 }} autosize minRows={1}
              placeholder="Add a note for the AI… (⌘↵ to send)" value={note}
              onChange={(e) => setNote(e.currentTarget.value)}
              onKeyDown={sendOnCtrlEnter(addNote)} />
            <Button variant="light" color="machine" onClick={addNote}>Add</Button>
          </Group>
        </Stack>
      </Card>

      {(() => {
        // The rules live in experimentsList.ts: status filter, "only new", and a cap on
        // done/canceled that never folds away an unseen row or the one just returned to.
        const { shown, hidden, newCount } = experimentRows(p.sprints, {
          statusFilter, showAll, onlyNew,
          isNew: (s) => isUnseen(s.id, s.last_status_at, s.last_status_by),
          keep: returned ? new Set([returned]) : undefined,
        });
        const counts = p.sprints.reduce<Record<string, number>>((a, s) => {
          a[s.status] = (a[s.status] ?? 0) + 1; return a;
        }, {});
        const order = ["proposed", "approved", "queued", "executing", "escalated", "hibernated",
                      "parked", "failed", "done", "canceled"];
        return (
          <Card id="sec-experiments" padding="lg" radius="md" style={cardStyle}>
            <Group justify="space-between" align="center" mb={12} wrap="nowrap">
              <div className="eyebrow">experiments · {p.sprints.length}</div>
              <Group gap={12} wrap="nowrap">
              {/* P6. "New" is this browser's own record of what it has seen, so the
                  count can differ between machines and the filter cannot be server-side. */}
              <label className="mono" style={{ fontSize: 12, display: "flex", alignItems: "center", gap: 5,
                                               color: newCount ? "var(--ink)" : "var(--ink-faint)", cursor: "pointer" }}>
                <input type="checkbox" checked={onlyNew} onChange={(e) => setOnlyNew(e.target.checked)} />
                only new ({newCount})
              </label>
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
            </Group>
            {p.sprints.length === 0 ? (
              <Text size="sm" c="dimmed">None yet. Propose one, or let the AI propose on its next cycle.</Text>
            ) : shown.length === 0 && onlyNew ? (
              <Text size="sm" c="dimmed">
                Nothing new{statusFilter === "all" ? "" : ` among ${statusFilter} experiments`} since you last looked.
              </Text>
            ) : shown.length === 0 ? (
              <Text size="sm" c="dimmed">No {statusFilter} experiments.</Text>
            ) : (
              <Stack gap={2}>
                {shown.map((s) => (
                  <div key={s.id} id={`exp-${s.id}`}
                    className={[isUnseen(s.id, s.last_status_at, s.last_status_by) ? "sprint-unseen" : "",
                                s.id === returned ? "sprint-returned" : ""].join(" ").trim() || undefined}
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
                      {s.hold?.why && (
                        <Tooltip label={`Held by the PM: ${s.hold.why}`} withArrow openDelay={300} multiline w={280}>
                          <Badge size="xs" color="gray" variant="light" style={{ cursor: "help" }}>held</Badge>
                        </Tooltip>
                      )}
                      {s.escalation_level === "human" && (
                        <Badge size="xs" color="red" variant="filled">needs you</Badge>
                      )}
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

      <Card id="sec-ideas" padding="lg" radius="md" style={cardStyle}>
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

      <Card id="sec-artifacts" padding="lg" radius="md" style={cardStyle}>
        <Group justify="space-between" align="center" mb={12}>
          <div className="eyebrow">artifacts · {artifacts.data?.length ?? 0}</div>
          <Link to={`/programs/${id}/artifacts`} className="view" style={{ fontSize: 13 }}>open artifacts →</Link>
        </Group>
        {!artifacts.data || artifacts.data.length === 0 ? (
          <Text size="sm" c="dimmed">No artifacts yet.</Text>
        ) : (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(230px, 1fr))", gap: 10 }}>
            {artifacts.data.map((a) => (
              <Link key={a.id} style={{ textDecoration: "none", color: "inherit" }}
                    to={liveChatId(a.lock)
                      ? `/programs/${id}/chat?c=${liveChatId(a.lock)}`
                      : `/programs/${id}/artifacts/${a.id}`}>
                <Card withBorder padding="sm" radius="md" style={{ height: "100%" }}
                      title={a.excerpt?.trim() ? a.excerpt.replace(/^#+\s*/gm, "") : undefined}>
                  <Group justify="space-between" align="flex-start" wrap="nowrap" gap={6}>
                    <Text size="sm" fw={600} truncate style={{ minWidth: 0 }}>{a.title || a.id}</Text>
                    {a.lock.holder_id && (
                      <Badge size="xs" color="signal" variant="light" title={`locked by ${a.lock.holder_kind ?? "agent"} ${a.lock.holder_id}`}>
                        {liveChatId(a.lock) ? "💬 in chat" : "🔒 locked"}
                      </Badge>
                    )}
                  </Group>
                  <Group align="flex-start" wrap="nowrap" gap={8} mt={6}>
                    <div style={{ minWidth: 0, flex: 1 }}>
                      <Group gap={6} wrap="wrap">
                        <Badge size="xs" color="machine" variant="light">{a.kind}</Badge>
                        {a.archived && <Badge size="xs" color="gray" variant="light">archived</Badge>}
                        {(a.tags ?? []).map((t) => (
                          <Badge key={t} size="xs" color="grape" variant="light">{t}</Badge>
                        ))}
                      </Group>
                      <Text size="xs" c="dimmed" mt={8} className="mono">
                        {a.current || "—"} · {a.version_count} version{a.version_count === 1 ? "" : "s"}
                      </Text>
                    </div>
                    <ArtifactThumb pid={id!} a={a} />
                  </Group>
                </Card>
              </Link>
            ))}
          </div>
        )}
      </Card>

      <Card id="sec-wiki" padding="lg" radius="md" style={cardStyle}>
        <Group justify="space-between" align="center">
          <div className="eyebrow">
            wiki · {wiki.data?.pages ?? 0}
          </div>
          <Link to={`/programs/${id}/wiki`} className="view" style={{ fontSize: 13 }}>
            open wiki →
            {wiki.data?.pending
              ? <Badge size="xs" variant="light" color="machine" ml={6}>{wiki.data.pending}</Badge>
              : null}
          </Link>
        </Group>
        {wiki.data && (wiki.data.pages > 0 || wiki.data.pending > 0) ? <WikiStats s={wiki.data} /> : (
          <Text size="sm" c="dimmed" mt={6}>
            Concepts and entities compiled from this program's results and artifacts,
            with every claim cited back to the object it came from.
          </Text>
        )}
      </Card>

      <div id="sec-lineage"><LineageCard programId={id} /></div>

      {p.activations?.length > 0 && (
        <Card id="sec-activity" padding="lg" radius="md" style={cardStyle}>
          <div className="eyebrow" style={{ marginBottom: 10 }}>PM activity — when it planned and why</div>
          <Stack gap={7}>
            {(pmExpanded ? p.activations.slice(0, 12) : p.activations.slice(0, 3)).map((a, i) => (
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
          {p.activations.length > 3 && (
            <button type="button" className="linklike" style={{ alignSelf: "flex-start", marginTop: 8 }}
              onClick={() => setPmExpanded((v) => !v)}>
              {pmExpanded ? "Show fewer" : `Show all (${Math.min(12, p.activations.length) - 3} more)`}
            </button>
          )}
        </Card>
      )}

      {/* Housekeeping, kept below the science: how the planner has been running,
          then what this program knows about each server it runs on. */}
      <HostNotesCard programId={id} />

      <ProposeSprintModal programId={id} opened={proposing} onClose={() => setProposing(false)} onDone={refresh} />
    </Stack>
  );
}

const WIKI_TYPES: [type: string, one: string, many: string][] = [
  ["Concept", "concept", "concepts"], ["Entity", "entity", "entities"],
  ["Synthesis", "synthesis", "syntheses"], ["Source", "source", "sources"],
];

/** What the wiki holds and how fresh it is: page types as a bar in the graph's hues,
 *  then the pending backlog and when the last run landed. */
function WikiStats({ s }: { s: WikiSummary }) {
  const counts = s.counts ?? {};
  const types = WIKI_TYPES.filter(([t]) => counts[t]);
  const total = types.reduce((n, [t]) => n + counts[t], 0);
  const hue = (t: string) => TYPE_HUE[t] ?? "var(--ink-faint)";
  return (
    <Stack gap={8} mt={12}>
      {total > 0 && (
        <div className="statebar" aria-hidden>
          {types.map(([t]) => (
            <span key={t} style={{ width: `${(counts[t] / total) * 100}%`, background: hue(t) }} />
          ))}
        </div>
      )}
      <Group gap={14}>
        {types.map(([t, one, many]) => (
          <span key={t} className="mono" style={{ fontSize: 12, display: "inline-flex", alignItems: "center", gap: 5 }}>
            <span style={{ width: 8, height: 8, borderRadius: 2, background: hue(t) }} />
            {counts[t]} {counts[t] === 1 ? one : many}
          </span>
        ))}
      </Group>
      <Text size="xs" c="dimmed">
        {s.pending} pending
        {s.last_run && <> · last {s.last_run.kind} <RelTime at={s.last_run.at} /></>}
      </Text>
    </Stack>
  );
}
