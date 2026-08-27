import { Badge, Card, Group, Loader, Stack, Text } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import Md from "../components/Md";
import { wikiHref } from "../components/wikiPage";
import { AbsTime, BackLink, EmptyState } from "../components/ui";
import { api } from "../api";

const cardStyle = { border: "1px solid var(--hairline)", boxShadow: "var(--shadow-card)" };

/** A path like "concepts/compute-lease.md" is what the API carries; a reader
 *  scanning a list of runs wants the name, not the directory. */
function slugOf(path: string): string {
  return path.split("/").pop()?.replace(/\.md$/, "") ?? path;
}

/** This is the page that answers "is the wiki being looked after" — per the
 *  design's §11.3/§9.1, nothing else gates an automatic merge but git, so the
 *  Activity section below is the only place a human sees one happened at all. */
export default function WikiLintView() {
  const { id = "" } = useParams();

  const activity = useQuery({ queryKey: ["wiki-activity", id],
                              queryFn: () => api.getWikiActivity(id) });
  const lint = useQuery({ queryKey: ["wiki-lint", id],
                          queryFn: () => api.getWikiLint(id) });

  if (activity.isLoading || lint.isLoading) return <Loader color="machine" />;

  const runs = activity.data ?? [];
  const reports = lint.data?.reports ?? [];
  const findings = lint.data?.findings ?? [];
  const nothingHasRun = runs.length === 0 && reports.length === 0;

  return (
    <Stack gap="lg">
      <div>
        <BackLink to={`/programs/${id}/wiki`}>Wiki</BackLink>
        <Text fw={600} size="xl">Wiki maintenance</Text>
      </div>

      {nothingHasRun && (
        <EmptyState title="Nothing has run yet">
          Ingest and lint runs will show up here once the wiki has been touched.
        </EmptyState>
      )}

      <Card padding="lg" radius="md" style={cardStyle}>
        <div className="eyebrow" style={{ marginBottom: 10 }}>activity</div>
        {runs.length === 0 ? (
          <Text size="sm" c="dimmed">No runs yet.</Text>
        ) : (
          <Stack gap={10}>
            {runs.map((r) => (
              <div key={r.id} data-testid="wiki-run">
                <Group gap={8} wrap="wrap">
                  <code className="mono" style={{ fontSize: 11 }}>{r.id}</code>
                  <Badge size="xs" variant="light" color="gray">{r.kind}</Badge>
                  <Badge size="xs" variant="light" color={r.status === "ok" ? "gray" : "red"}>
                    {r.status}
                  </Badge>
                  <AbsTime at={r.at} />
                  {typeof r.pages_created === "number" && r.pages_created > 0 && (
                    <Text size="xs" c="dimmed">+{r.pages_created} created</Text>
                  )}
                  {typeof r.pages_updated === "number" && r.pages_updated > 0 && (
                    <Text size="xs" c="dimmed">{r.pages_updated} updated</Text>
                  )}
                </Group>
                {r.merged.length > 0 && (
                  <ul style={{ margin: "6px 0 0" }}>
                    {r.merged.map(([loser, winner], i) => (
                      <li key={i}>
                        <Text size="xs">
                          <b>{slugOf(loser)}</b> {"→"}{" "}
                          <Link to={wikiHref(id, winner)}><b>{slugOf(winner)}</b></Link>
                        </Text>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            ))}
          </Stack>
        )}
      </Card>

      <Card padding="lg" radius="md" style={cardStyle}>
        <div className="eyebrow" style={{ marginBottom: 10 }}>reports</div>
        {reports.length === 0 ? (
          <Text size="sm" c="dimmed">No filed reports yet.</Text>
        ) : (
          <Stack gap="md">
            {reports.map((rep) => (
              <div key={rep.date}>
                <Text fw={600} size="sm" mb={4}>{rep.date}</Text>
                <div className="report-leaf">
                  <Md>{rep.text}</Md>
                </div>
              </div>
            ))}
          </Stack>
        )}
      </Card>

      <Card padding="lg" radius="md" style={cardStyle}>
        <div className="eyebrow" style={{ marginBottom: 10 }}>findings</div>
        {/* Task 12 fills this in with the live findings grouped by rule. */}
        {findings.length === 0 ? (
          <Text size="sm" c="dimmed">No findings.</Text>
        ) : (
          <Text size="sm" c="dimmed">{findings.length} finding(s).</Text>
        )}
      </Card>
    </Stack>
  );
}
