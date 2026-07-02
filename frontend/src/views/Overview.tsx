import { Button, Card, Group, Loader, SimpleGrid, Stack, Text } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { api, type SprintRow } from "../api";
import { Bars, computeCost, EmptyState, Gauge, Heartbeat, LiveActivity, RelTime, Running, StateBar, StatusBadge, UsagePanel } from "../components/ui";

function programOf(s: SprintRow) {
  if (s.program) return s.program;
  const i = s.id.indexOf("-");
  return i === -1 ? s.id : s.id.slice(0, i);
}

export default function Overview() {
  const qc = useQueryClient();
  const nav = useNavigate();
  const programs = useQuery({ queryKey: ["programs"], queryFn: api.listPrograms });
  const sprints = useQuery({ queryKey: ["sprints"], queryFn: api.listSprints, refetchInterval: 8000 });
  const ledger = useQuery({ queryKey: ["ledger"], queryFn: api.getLedger });
  const results = useQuery({ queryKey: ["results"], queryFn: api.listResults });
  const usage = useQuery({ queryKey: ["usage"], queryFn: api.getUsage });

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["sprints"] });
    qc.invalidateQueries({ queryKey: ["programs"] });
  };
  const approve = useMutation({
    mutationFn: api.approveSprint,
    onSuccess: () => { notifications.show({ color: "teal", title: "Approved", message: "Added to the PM's queue — it schedules and runs it when ready." }); refresh(); },
    onError: (e) => notifications.show({ color: "red", title: "Couldn't approve", message: String(e) }),
  });
  const reject = useMutation({
    mutationFn: api.rejectSprint,
    onSuccess: () => { notifications.show({ color: "gray", title: "Rejected", message: "Canceled — it won't run." }); refresh(); },
    onError: (e) => notifications.show({ color: "red", title: "Couldn't reject", message: String(e) }),
  });
  const busy = approve.isPending || reject.isPending;

  if (programs.isLoading || sprints.isLoading) return <Loader color="machine" />;

  const allSprints: SprintRow[] = sprints.data ?? [];
  const progs = programs.data ?? [];
  const status: Record<string, string> = {};
  const title: Record<string, string> = {};
  for (const p of progs) { status[p.id] = p.status; title[p.id] = p.title || p.id; }
  const sprintTitle: Record<string, string> = {};
  for (const s of allSprints) sprintTitle[s.id] = s.title || s.goals || s.id;

  const groups: Record<string, SprintRow[]> = {};
  for (const s of allSprints.filter((s) => s.status === "proposed")) {
    (groups[programOf(s)] ??= []).push(s);
  }
  const active = Object.entries(groups).filter(([pid]) => (status[pid] ?? "active") === "active");
  const stalled = Object.entries(groups).filter(([pid]) => status[pid] && status[pid] !== "active");
  const waiting = active.reduce((n, [, list]) => n + list.length, 0);

  const runningGroups: Record<string, SprintRow[]> = {};
  for (const s of allSprints.filter((s) => s.status === "executing")) {
    (runningGroups[programOf(s)] ??= []).push(s);
  }
  const runningCount = Object.values(runningGroups).reduce((n, l) => n + l.length, 0);

  const cap = ledger.data?.capacity ?? {};
  const byState: Record<string, number> = {};
  for (const s of allSprints) byState[s.status] = (byState[s.status] ?? 0) + 1;
  const activeP = progs.filter((p) => p.status === "active").length;
  const pausedP = progs.filter((p) => p.status === "paused").length;
  const recent = (results.data ?? []).slice(-4).reverse();

  const SCALE_HINT: Record<string, string> = { light: "light", moderate: "moderate", heavy: "heavy" };

  const card = (s: SprintRow) => {
    const c = computeCost(s.resources_required, cap);
    const heading = s.title || s.goals || s.id;
    const summary = s.summary || s.rationale;
    return (
      <div key={s.id} className="exp" onClick={() => nav(`/sprints/${s.id}`)}>
        <span className="kind"><span className="d" />Experiment · awaiting approval</span>
        <p className="exp-title">{heading}</p>
        {summary && <p className="exp-summary">{summary}</p>}
        <div className="exp-meta">
          <Bars filled={c.filled} /> {c.text} · {SCALE_HINT[c.scale]} · {s.steps}-step plan
        </div>
        <div className="foot">
          <Link className="view" to={`/sprints/${s.id}`} onClick={(e) => e.stopPropagation()}>View plan →</Link>
          <Group gap={8} onClick={(e) => e.stopPropagation()}>
            <Button size="xs" color="signal" disabled={busy} onClick={() => approve.mutate(s.id)}>Approve</Button>
            <Button size="xs" variant="default" disabled={busy} onClick={() => reject.mutate(s.id)}>Reject</Button>
          </Group>
        </div>
      </div>
    );
  };

  return (
    <>
      <h1 style={{ fontFamily: "'Space Grotesk', sans-serif", fontSize: 26, fontWeight: 600, margin: "0 0 22px" }}>
        Overview
      </h1>

      <div className="section-head" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", margin: "0 0 4px" }}>
        <span className="eyebrow" style={{ color: waiting ? "var(--signal)" : "var(--ink-faint)", fontWeight: 600 }}>
          awaiting your decision
        </span>
        {waiting > 0 && <Text className="mono" fw={600} style={{ color: "var(--signal)" }}>{waiting} {waiting === 1 ? "experiment" : "experiments"}</Text>}
      </div>

      {waiting === 0 ? (
        <Card padding="lg" radius="md" mb="lg" style={{ border: "1px solid var(--hairline)", boxShadow: "var(--shadow-card)" }}>
          <EmptyState title="Nothing needs you right now">
            The AI proposes experiments under your active programs. New ones land here for you to
            approve before any compute is spent.
          </EmptyState>
        </Card>
      ) : (
        <>
          <p className="lede">Experiments the AI has proposed for your active programs. Open one for its full plan, or decide here.</p>
          {active.map(([pid, list]) => (
            <div className="group" key={pid}>
              <div className="group-head">
                <Heartbeat />
                <span className="name">{title[pid] ?? pid}</span>
                <span className="eyebrow">program · active</span>
              </div>
              <div className="group-body">{list.map(card)}</div>
            </div>
          ))}
        </>
      )}

      {stalled.length > 0 && (
        <div className="muted-note" style={{ marginBottom: "var(--mantine-spacing-lg)" }}>
          <span style={{ color: "var(--st-paused)" }}>❚❚</span>
          <span>
            {stalled.reduce((n, [, l]) => n + l.length, 0)} more proposed in{" "}
            {stalled.map(([pid], i) => (
              <span key={pid}><b>{title[pid] ?? pid}</b> ({status[pid]}){i < stalled.length - 1 ? ", " : ""}</span>
            ))}. Resume the program to act on them.
          </span>
        </div>
      )}

      {runningCount > 0 && (
        <div style={{ marginTop: "var(--mantine-spacing-lg)" }}>
          <div className="section-head" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", margin: "0 0 4px" }}>
            <span className="eyebrow" style={{ color: "var(--st-executing)", fontWeight: 600 }}>running now</span>
            <Text className="mono" fw={600} style={{ color: "var(--st-executing)" }}>{runningCount} {runningCount === 1 ? "experiment" : "experiments"}</Text>
          </div>
          {Object.entries(runningGroups).map(([pid, list]) => (
            <div className="group" key={pid}>
              <div className="group-head">
                <Heartbeat />
                <span className="name">{title[pid] ?? pid}</span>
                <span className="eyebrow">program</span>
              </div>
              <div className="group-body">
                {list.map((s) => (
                  <div key={s.id} onClick={() => nav(`/sprints/${s.id}`)}
                    style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, padding: "10px 13px", background: "var(--card)", border: "1px solid var(--hairline)", borderRadius: 10, cursor: "pointer" }}>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <Text size="sm" truncate>{sprintTitle[s.id]}</Text>
                      <LiveActivity activity={s.activity} agentRunning />
                    </div>
                    <Running since={s.started_at} />
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* calm strip: what the machine is doing on its own */}
      <SimpleGrid cols={{ base: 1, sm: 3 }} mt="lg" mb="lg">
        <Card padding="lg" radius="md" style={{ border: "1px solid var(--hairline)", boxShadow: "var(--shadow-card)" }}>
          <div className="eyebrow" style={{ marginBottom: 12 }}>programs</div>
          <Text style={{ fontFamily: "'Space Grotesk', sans-serif", fontSize: 30, fontWeight: 600, lineHeight: 1 }}>{progs.length}</Text>
          <Text size="xs" c="dimmed" mt={6}>{activeP} active · {pausedP} paused</Text>
        </Card>
        <Card padding="lg" radius="md" style={{ border: "1px solid var(--hairline)", boxShadow: "var(--shadow-card)" }}>
          <div className="eyebrow" style={{ marginBottom: 12 }}>experiments</div>
          <Text style={{ fontFamily: "'Space Grotesk', sans-serif", fontSize: 30, fontWeight: 600, lineHeight: 1 }}>{allSprints.length}</Text>
          <div style={{ marginTop: 12 }}><StateBar counts={byState} /></div>
        </Card>
        <Card padding="lg" radius="md" style={{ border: "1px solid var(--hairline)", boxShadow: "var(--shadow-card)" }}>
          <div className="eyebrow" style={{ marginBottom: 12 }}>compute</div>
          {Object.keys(cap).length ? (
            <Stack gap={10} mt={2}>
              {Object.keys(cap).map((k) => (
                <Gauge key={k} label={k} used={ledger.data?.used[k] ?? 0} capacity={cap[k]} />
              ))}
            </Stack>
          ) : <Text size="xs" c="dimmed">No compute pool configured yet.</Text>}
        </Card>
      </SimpleGrid>

      {usage.data && (
        <Card padding="lg" radius="md" mb="lg" style={{ border: "1px solid var(--hairline)", boxShadow: "var(--shadow-card)" }}>
          <div className="eyebrow" style={{ marginBottom: 14 }}>Claude usage</div>
          <UsagePanel usage={usage.data} />
        </Card>
      )}

      <Card padding="lg" radius="md" style={{ border: "1px solid var(--hairline)", boxShadow: "var(--shadow-card)" }}>
        <div className="eyebrow" style={{ marginBottom: 14 }}>recent results</div>
        {recent.length === 0 ? (
          <Text size="sm" c="dimmed">No results yet. Experiments produce results once released to run and the dispatcher picks them up.</Text>
        ) : (
          <Stack gap={10}>
            {recent.map((r) => (
              <Link key={r.id} to={`/results/${r.id}`}
                style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, textDecoration: "none", color: "inherit", borderBottom: "1px solid var(--hairline)", paddingBottom: 10 }}>
                <div style={{ minWidth: 0 }}>
                  <Text size="sm" truncate>{sprintTitle[r.sprint] || r.summary.split("\n")[0] || "—"}</Text>
                  <span style={{ fontSize: 11, color: "var(--ink-faint)" }}>
                    {r.completed_at ? <>finished <RelTime at={r.completed_at} /></> : "experiment finding"}
                  </span>
                </div>
                <StatusBadge status="done" />
              </Link>
            ))}
          </Stack>
        )}
      </Card>
    </>
  );
}
