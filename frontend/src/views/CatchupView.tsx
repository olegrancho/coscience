import { Badge, Button, Card, Group, Loader, NumberInput, Select, Stack, Text } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import Md from "../components/Md";
import { api, type CatchupReport } from "../api";
import { AbsTime, BackLink } from "../components/ui";
import { isReportSeen, markReportSeen } from "../catchupSeen";

const cardStyle = { border: "1px solid var(--hairline)", boxShadow: "var(--shadow-card)" };
const DAY = 86400;

const SINCE = [
  { value: "last", label: "since the last report" },
  { value: "7", label: "the last 7 days" },
  { value: "14", label: "the last 14 days" },
  { value: "30", label: "the last 30 days" },
];

function day(at: number) {
  return new Date(at * 1000).toLocaleDateString("en-GB", { day: "numeric", month: "short" });
}

function Report({ programId, r, open: openAtFirst }:
  { programId: string; r: CatchupReport; open: boolean }) {
  const [open, setOpen] = useState(openAtFirst);
  const [seen] = useState(() => isReportSeen(r.id));
  // Read once it is open with its text: a report still being written is not read yet.
  useEffect(() => { if (open && r.text) markReportSeen(r.id); }, [open, r.text, r.id]);
  const who = r.trigger === "schedule" ? "on schedule" : `asked for${r.by ? ` by ${r.by}` : ""}`;
  return (
    <Card padding="lg" radius="md" style={cardStyle}>
      <Group justify="space-between" align="baseline" wrap="wrap" gap={6}>
        <Group gap={8} align="baseline">
          <Text fw={600}>{r.title}</Text>
          {!seen && r.text && <Badge size="sm" color="machine" variant="light">new</Badge>}
        </Group>
        <Text size="xs" c="dimmed">
          {r.sprints.length} {r.sprints.length === 1 ? "sprint" : "sprints"} finished since{" "}
          {day(r.since)} · {who}
        </Text>
      </Group>
      {r.busy && !r.text ? (
        <Group gap={8} mt="md"><Loader size="xs" color="machine" />
          <Text size="sm" c="dimmed">The planner is writing this report…</Text></Group>
      ) : open ? (
        <div className="report-leaf" style={{ marginTop: 12 }}><Md>{r.text || "_(no reply)_"}</Md></div>
      ) : null}
      <Group gap={8} mt="md">
        {!open && r.text && (
          <Button size="xs" variant="light" color="machine" onClick={() => setOpen(true)}>Read</Button>
        )}
        <Button size="xs" variant={open ? "light" : "subtle"} color="machine" component={Link}
                to={`/programs/${programId}/chat?c=${r.id}`}>
          Continue in chat{r.followups ? ` · ${r.followups} follow-up${r.followups === 1 ? "" : "s"}` : ""}
        </Button>
      </Group>
    </Card>
  );
}

export default function CatchupView() {
  const { id = "" } = useParams();
  const qc = useQueryClient();
  const program = useQuery({ queryKey: ["program", id], queryFn: () => api.getProgram(id) });
  const page = useQuery({
    queryKey: ["catchup", id], queryFn: () => api.getCatchup(id),
    refetchInterval: (q) => (q.state.data?.reports.some((r) => r.busy) ? 3000 : 30000),
  });
  const [since, setSince] = useState("last");
  const [starting, setStarting] = useState(false);
  const [every, setEvery] = useState<number | string>("");
  const [need, setNeed] = useState<number | string>("");
  const s = page.data?.schedule;
  useEffect(() => {
    if (s) { setEvery(s.every_days); setNeed(s.min_sprints); }
  }, [s?.every_days, s?.min_sprints]);   // eslint-disable-line react-hooks/exhaustive-deps

  const refresh = () => qc.invalidateQueries({ queryKey: ["catchup", id] });
  const writeNow = async () => {
    setStarting(true);
    try {
      const from = since === "last" ? undefined : Date.now() / 1000 - Number(since) * DAY;
      await api.startCatchup(id, from);
      refresh();
    } catch (e) {
      notifications.show({ color: "red", title: "Couldn't start a report", message: String(e) });
    } finally {
      setStarting(false);
    }
  };
  const scheduleChanged = !!s && (Number(every) !== s.every_days || Number(need) !== s.min_sprints);
  const saveSchedule = async () => {
    try {
      await api.setCatchupSchedule(id, Number(every) || 0, Number(need) || 0);
      refresh();
    } catch (e) {
      notifications.show({ color: "red", title: "Couldn't save the schedule", message: String(e) });
    }
  };

  const reports = page.data?.reports ?? [];
  const busy = reports.some((r) => r.busy);
  return (
    <Stack gap="lg">
      <div>
        <BackLink to={`/programs/${id}`}>{program.data?.title || id}</BackLink>
        <Group justify="space-between" align="flex-end" wrap="wrap" gap={10}>
          <h1 style={{ fontFamily: "'Space Grotesk', sans-serif", fontSize: 24, fontWeight: 600, margin: 0 }}>
            Catch-up
          </h1>
          <Group gap={8} wrap="nowrap">
            <Select size="xs" w={190} data={SINCE} value={since} allowDeselect={false}
                    onChange={(v) => setSince(v || "last")} aria-label="report period" />
            <Button size="xs" color="machine" loading={starting} disabled={busy} onClick={writeNow}>
              Write one now
            </Button>
          </Group>
        </Group>
        <Text size="sm" c="dimmed" mt={6} style={{ maxWidth: 660 }}>
          Briefs from the planner on what happened since the last one: new results, what they
          changed, and what to do next. Each report is a chat — continue it to ask about any part,
          or to act on a numbered next step.
        </Text>
      </div>

      {s && (
        <Card padding="md" radius="md" style={cardStyle}>
          <Group gap={8} align="center" wrap="wrap">
            <Text size="sm">On schedule: every</Text>
            <NumberInput size="xs" w={70} min={0} value={every} onChange={setEvery} aria-label="days between reports" />
            <Text size="sm">days, when at least</Text>
            <NumberInput size="xs" w={70} min={0} value={need} onChange={setNeed} aria-label="sprints needed" />
            <Text size="sm">sprints have finished since the last report.</Text>
            {scheduleChanged && <Button size="xs" variant="light" color="machine" onClick={saveSchedule}>Save</Button>}
          </Group>
          <Text size="xs" c="dimmed" mt={6}>
            {s.every_days > 0
              ? <>{s.finished_since} finished since <AbsTime at={s.since} dateOnly />
                  {s.next_check_at ? <> · next considered from <AbsTime at={s.next_check_at} dateOnly /></> : null}.
                  A quiet period writes nothing. 0 days turns the schedule off.</>
              : <>The schedule is off; reports are written only when asked for.</>}
          </Text>
        </Card>
      )}

      {page.isLoading ? <Loader color="machine" /> : reports.length === 0 ? (
        <Card padding="lg" radius="md" style={cardStyle}>
          <Text size="sm" c="dimmed">No catch-up reports yet. Write one now, or wait for the schedule.</Text>
        </Card>
      ) : reports.map((r, i) => <Report key={r.id} programId={id} r={r} open={i === 0} />)}
    </Stack>
  );
}
