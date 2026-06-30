import { Card, Loader, Stack, Text } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import Md from "../components/Md";
import { api } from "../api";
import { BackLink, EmptyState, RelTime } from "../components/ui";

const cardStyle = { border: "1px solid var(--hairline)", boxShadow: "var(--shadow-card)" };

export default function ResultDetail() {
  const { id = "" } = useParams();
  const result = useQuery({ queryKey: ["result", id], queryFn: () => api.getResult(id) });
  const sprintId = result.data?.sprint;
  const programId = result.data?.program ?? undefined;
  const sprint = useQuery({ queryKey: ["sprint", sprintId], queryFn: () => api.getSprint(sprintId!), enabled: !!sprintId });
  const program = useQuery({ queryKey: ["program", programId], queryFn: () => api.getProgram(programId!), enabled: !!programId });

  if (result.isLoading) return <Loader color="machine" />;
  if (result.error || !result.data) return <EmptyState title="Result not found">Nothing here at “{id}”.</EmptyState>;
  const r = result.data;
  const expTitle = sprint.data?.title || sprint.data?.goals || r.sprint;
  // the gist to show under the title — only if it adds something the title didn't
  const expAskedRaw = sprint.data?.summary || (sprint.data?.title ? sprint.data?.goals : "") || "";
  const expAsked = expAskedRaw && expAskedRaw !== expTitle ? expAskedRaw : "";
  const progTitle = program.data?.title || programId;

  return (
    <Stack gap="lg">
      <div>
        <BackLink to={`/sprints/${r.sprint}`}>{expTitle}</BackLink>
        <h1 style={{ fontFamily: "'Space Grotesk', sans-serif", fontSize: 23, fontWeight: 600, margin: 0 }}>What the experiment found</h1>
        {r.completed_at && (
          <Text size="sm" c="dimmed" mt={6}>Finished <RelTime at={r.completed_at} /></Text>
        )}
      </div>

      {/* context: which experiment asked this, and why — so the finding has meaning */}
      <Card padding="lg" radius="md" style={cardStyle}>
        <div className="eyebrow" style={{ marginBottom: 10 }}>from this experiment</div>
        <Text fw={600} mb={expAsked ? 6 : 0}>
          <Link to={`/sprints/${r.sprint}`} style={{ color: "var(--ink)", textDecoration: "none" }}>{expTitle}</Link>
        </Text>
        {expAsked && <Text size="sm" c="dimmed" style={{ lineHeight: 1.55 }}>{expAsked}</Text>}
        {programId && (
          <Text size="xs" c="dimmed" mt={10}>
            in program <Link to={`/programs/${programId}`} style={{ color: "var(--machine)", textDecoration: "none" }}>{progTitle}</Link>
          </Text>
        )}
      </Card>

      <Card padding="lg" radius="md" style={cardStyle}>
        <div className="eyebrow" style={{ marginBottom: 10 }}>what it produced</div>
        <div className="report-leaf"><Md>{r.summary}</Md></div>
      </Card>
    </Stack>
  );
}
