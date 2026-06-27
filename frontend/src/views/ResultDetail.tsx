import { Card, Loader, Stack } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import Markdown from "react-markdown";
import { api } from "../api";
import { EmptyState } from "../components/ui";

export default function ResultDetail() {
  const { id = "" } = useParams();
  const result = useQuery({ queryKey: ["result", id], queryFn: () => api.getResult(id) });
  if (result.isLoading) return <Loader color="machine" />;
  if (result.error || !result.data) return <EmptyState title="Result not found">Nothing here at “{id}”.</EmptyState>;
  const r = result.data;
  return (
    <Stack gap="lg">
      <div>
        <div className="eyebrow" style={{ marginBottom: 7 }}>
          result · from <Link to={`/sprints/${r.sprint}`} style={{ color: "var(--machine)", textDecoration: "none" }}>{r.sprint}</Link>
        </div>
        <h1 style={{ fontFamily: "'Space Grotesk', sans-serif", fontSize: 23, fontWeight: 600, margin: 0 }}>What the experiment found</h1>
      </div>
      <Card padding="lg" radius="md" style={{ border: "1px solid var(--hairline)", boxShadow: "var(--shadow-card)" }}>
        <div className="report-leaf"><Markdown>{r.summary}</Markdown></div>
      </Card>
    </Stack>
  );
}
