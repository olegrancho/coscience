import { Card, Group, Loader, SimpleGrid, Text } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, type SprintRow } from "../api";
import { EmptyState, Heartbeat, StateBar, StatusBadge } from "../components/ui";

function progOf(s: SprintRow) {
  if (s.program) return s.program;
  const i = s.id.indexOf("-");
  return i === -1 ? s.id : s.id.slice(0, i);
}

export default function ProgramsOverview() {
  const programs = useQuery({ queryKey: ["programs"], queryFn: api.listPrograms });
  const sprints = useQuery({ queryKey: ["sprints"], queryFn: api.listSprints });
  if (programs.isLoading || sprints.isLoading) return <Loader color="machine" />;
  if (programs.error) return <EmptyState title="Couldn't load programs">Try again in a moment.</EmptyState>;

  const counts: Record<string, Record<string, number>> = {};
  for (const s of sprints.data ?? []) {
    const pid = progOf(s);
    (counts[pid] ??= {})[s.status] = (counts[pid]?.[s.status] ?? 0) + 1;
  }
  // Active programs first; paused/closed sink to the bottom so they don't distract
  // from running work. Stable sort keeps each group's existing order.
  const rank = (s: string) => (s === "active" ? 0 : s === "paused" ? 1 : 2);
  const progs = [...(programs.data ?? [])].sort((a, b) => rank(a.status) - rank(b.status));

  return (
    <>
      <h1 style={{ fontFamily: "'Space Grotesk', sans-serif", fontSize: 24, fontWeight: 600, margin: "0 0 20px" }}>Programs</h1>
      {progs.length === 0 ? (
        <EmptyState title="No programs yet" command="coscience program create --id … --title … --goals …">
          A program is a research direction you hand to the AI. Create one and it'll start proposing experiments.
        </EmptyState>
      ) : (
        <SimpleGrid cols={{ base: 1, sm: 2 }}>
          {progs.map((p) => {
            const c = counts[p.id] ?? {};
            const total = Object.values(c).reduce((a, b) => a + b, 0);
            const waiting = p.status === "active" ? (c.proposed ?? 0) : 0;
            const paused = p.status === "paused";
            return (
              <Card key={p.id} component={Link} to={`/programs/${p.id}`} padding="lg" radius="md"
                style={{ border: "1px solid var(--hairline)", boxShadow: "var(--shadow-card)",
                  textDecoration: "none", color: "inherit",
                  // paused programs go quiet: dimmed + desaturated so active ones stand out
                  background: paused ? "var(--paper)" : undefined,
                  opacity: paused ? 0.6 : 1,
                  filter: paused ? "grayscale(1)" : undefined }}>
                <Group justify="space-between" mb={7} wrap="nowrap">
                  <Group gap={8} wrap="nowrap" style={{ minWidth: 0 }}>
                    {p.status === "active" && <Heartbeat />}
                    <Text fw={600} truncate style={{ fontFamily: "'Space Grotesk', sans-serif" }}>{p.title || p.id}</Text>
                  </Group>
                  <StatusBadge status={p.status} />
                </Group>
                <Text size="sm" c="dimmed" lineClamp={2} mb="md" style={{ minHeight: 40 }}>{p.goals || "—"}</Text>
                <StateBar counts={c} />
                <Group justify="space-between" mt={11}>
                  <Text size="xs" c="dimmed">{total} {total === 1 ? "experiment" : "experiments"}</Text>
                  {waiting > 0 && (
                    <span className="pill" style={{ "--st": "var(--signal)" } as React.CSSProperties & Record<string, string>}>
                      <span className="dot" />{waiting} awaiting you
                    </span>
                  )}
                </Group>
              </Card>
            );
          })}
        </SimpleGrid>
      )}
    </>
  );
}
