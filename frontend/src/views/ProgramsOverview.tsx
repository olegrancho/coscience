import { Badge, Loader, Table, Title } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api";

const STATUS_COLOR: Record<string, string> = {
  active: "green", paused: "yellow", closed: "gray",
};

export default function ProgramsOverview() {
  const programs = useQuery({ queryKey: ["programs"], queryFn: api.listPrograms });
  const sprints = useQuery({ queryKey: ["sprints"], queryFn: api.listSprints });
  if (programs.isLoading || sprints.isLoading) return <Loader />;
  if (programs.error) return <div>Failed to load programs.</div>;

  const counts = (programId: string) => {
    const rows = (sprints.data ?? []).filter((s) => s.id.startsWith(`${programId}-`));
    const by: Record<string, number> = {};
    for (const s of rows) by[s.status] = (by[s.status] ?? 0) + 1;
    return Object.entries(by).map(([k, v]) => `${k}: ${v}`).join(", ") || "—";
  };

  return (
    <>
      <Title order={2} mb="md">Programs</Title>
      <Table striped highlightOnHover withTableBorder>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Title</Table.Th><Table.Th>Status</Table.Th>
            <Table.Th>Sprints</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {(programs.data ?? []).map((p) => (
            <Table.Tr key={p.id}>
              <Table.Td><Link to={`/programs/${p.id}`}>{p.title || p.id}</Link></Table.Td>
              <Table.Td>
                <Badge color={STATUS_COLOR[p.status] ?? "blue"}>{p.status}</Badge>
              </Table.Td>
              <Table.Td>{counts(p.id)}</Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </>
  );
}
