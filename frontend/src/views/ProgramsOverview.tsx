import { Button, Card, Group, Loader, SimpleGrid, Stack, Text } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, type SprintRow } from "../api";
import NewProgramModal from "../components/NewProgramModal";
import { EmptyState, Heartbeat, StateBar, StatusBadge } from "../components/ui";

function progOf(s: SprintRow) {
  if (s.program) return s.program;
  const i = s.id.indexOf("-");
  return i === -1 ? s.id : s.id.slice(0, i);
}

export default function ProgramsOverview() {
  const [newOpen, setNewOpen] = useState(false);
  const [closedOpen, setClosedOpen] = useState(false);
  const programs = useQuery({ queryKey: ["programs"], queryFn: api.listPrograms });
  const sprints = useQuery({ queryKey: ["sprints"], queryFn: api.listSprints });
  if (programs.isLoading || sprints.isLoading) return <Loader color="machine" />;
  if (programs.error) return <EmptyState title="Couldn't load programs">Try again in a moment.</EmptyState>;

  const counts: Record<string, Record<string, number>> = {};
  for (const s of sprints.data ?? []) {
    const pid = progOf(s);
    (counts[pid] ??= {})[s.status] = (counts[pid]?.[s.status] ?? 0) + 1;
  }
  const progs = programs.data ?? [];
  const group = (status: string) => progs.filter((p) => p.status === status);

  return (
    <>
      <Group justify="space-between" align="center" mb={20}>
        <h1 style={{ fontFamily: "'Space Grotesk', sans-serif", fontSize: 24, fontWeight: 600, margin: 0 }}>Programs</h1>
        <Button onClick={() => setNewOpen(true)}>New Program</Button>
      </Group>
      <NewProgramModal opened={newOpen} onClose={() => setNewOpen(false)} />
      {progs.length === 0 ? (
        <Stack gap={14} align="center">
          <EmptyState title="No programs yet">
            A program is a research direction you hand to the AI. Create one and it'll start proposing experiments.
          </EmptyState>
          <Button onClick={() => setNewOpen(true)}>New Program</Button>
        </Stack>
      ) : (
        <Stack gap={26}>
          {(["active", "paused", "closed"] as const).map((status) => {
            const inGroup = group(status);
            if (inGroup.length === 0) return null;
            const folded = status === "closed" && !closedOpen;
            return (
              <div key={status}>
                {status === "closed" ? (
                  <button className="eyebrow" onClick={() => setClosedOpen(!closedOpen)}
                          style={{ background: "none", border: "none", padding: 0, marginBottom: 12,
                                   cursor: "pointer", font: "inherit", letterSpacing: "0.14em",
                                   textTransform: "uppercase", color: "var(--ink-faint)" }}>
                    {closedOpen ? "▾" : "▸"} closed · {inGroup.length}
                  </button>
                ) : (
                  <div className="eyebrow" style={{ marginBottom: 12 }}>{status} · {inGroup.length}</div>
                )}
                {!folded && <SimpleGrid cols={{ base: 1, sm: 2 }}>{inGroup.map(renderCard)}</SimpleGrid>}
              </div>
            );
          })}
        </Stack>
      )}
    </>
  );

  function renderCard(p: (typeof progs)[number]) {
    const c = counts[p.id] ?? {};
    const total = Object.values(c).reduce((a, b) => a + b, 0);
    const waiting = p.status === "active" ? (c.proposed ?? 0) : 0;
    const quiet = p.status !== "active";
    return (
      <Card key={p.id} component={Link} to={`/programs/${p.id}`} padding="lg" radius="md"
        style={{ border: "1px solid var(--hairline)", boxShadow: "var(--shadow-card)",
          textDecoration: "none", color: "inherit",
          // Anything not active goes quiet, so the running work carries the page.
          background: quiet ? "var(--paper)" : undefined,
          opacity: quiet ? 0.6 : 1,
          filter: quiet ? "grayscale(1)" : undefined }}>
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
  }
}
