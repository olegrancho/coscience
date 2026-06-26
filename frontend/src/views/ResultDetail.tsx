import { Card, Loader, Stack, Title } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import Markdown from "react-markdown";
import { api } from "../api";

export default function ResultDetail() {
  const { id = "" } = useParams();
  const result = useQuery({ queryKey: ["result", id], queryFn: () => api.getResult(id) });
  if (result.isLoading) return <Loader />;
  if (result.error || !result.data) return <div>Result not found.</div>;
  const r = result.data;
  return (
    <Stack>
      <Title order={2}>Result {r.id}</Title>
      <div>Sprint: <Link to={`/sprints/${r.sprint}`}>{r.sprint}</Link></div>
      <Card withBorder><Markdown>{r.summary}</Markdown></Card>
    </Stack>
  );
}
