import { Badge, Button, Card, Group, Loader, Stack, Text } from "@mantine/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
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
 *  Activity section here is the only place a human sees one happened at all. */
export default function WikiLintView() {
  const { id = "" } = useParams();
  const qc = useQueryClient();

  const activity = useQuery({ queryKey: ["wiki-activity", id],
                              queryFn: () => api.getWikiActivity(id) });
  const lint = useQuery({ queryKey: ["wiki-lint", id],
                          queryFn: () => api.getWikiLint(id) });
  const merges = useQuery({ queryKey: ["wiki-merges", id],
                            queryFn: () => api.listWikiMerges(id) });

  // A proposal, once actioned, is gone either way — either applied (so the
  // activity list now shows it) or refused (so it should stop being offered).
  const invalidateProposals = () => {
    qc.invalidateQueries({ queryKey: ["wiki-merges", id] });
    qc.invalidateQueries({ queryKey: ["wiki-activity", id] });
  };
  const accept = useMutation({ mutationFn: (mid: string) => api.acceptWikiMerge(id, mid),
                               onSuccess: invalidateProposals });
  const reject = useMutation({ mutationFn: (mid: string) => api.rejectWikiMerge(id, mid),
                               onSuccess: invalidateProposals });

  if (activity.isLoading || lint.isLoading) return <Loader color="machine" />;

  const runs = activity.data ?? [];
  const reports = lint.data?.reports ?? [];
  const findings = lint.data?.findings ?? [];
  const proposals = merges.data ?? [];
  const nothingHasRun = runs.length === 0 && reports.length === 0;
  // Neither mutation should be able to fire while the other is in flight —
  // a double click on Accept then Reject must not apply and then also reject.
  const busy = accept.isPending || reject.isPending;

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

      {/* Spec §11.3 (amended): renders first, above the audit sections, when
          non-empty — it only appears under the `propose` merge policy, and
          it is the one section a human still owes an action to. Accept
          applies immediately; reject records the pair in merges_refused so
          no later run re-proposes it. */}
      {proposals.length > 0 && (
        <Card padding="lg" radius="md" style={cardStyle}>
          <div className="eyebrow" style={{ marginBottom: 10 }}>proposals</div>
          <Stack gap="md">
            {proposals.map((p) => (
              <div key={p.id} data-testid="merge-proposal">
                <Text size="sm" mb={4}>
                  <Link to={wikiHref(id, p.loser)}>{slugOf(p.loser)}</Link>
                  {" → "}
                  <Link to={wikiHref(id, p.winner)}>{slugOf(p.winner)}</Link>
                </Text>
                <Text size="sm" c="dimmed" mb={8}>{p.why}</Text>
                <Group gap={8}>
                  <Button size="xs" variant="default" disabled={busy}
                          onClick={() => accept.mutate(p.id)}>Accept</Button>
                  <Button size="xs" variant="default" color="red" disabled={busy}
                          onClick={() => reject.mutate(p.id)}>Reject</Button>
                </Group>
              </div>
            ))}
          </Stack>
        </Card>
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
