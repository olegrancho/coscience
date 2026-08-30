import { Badge, Button, Card, Group, Loader, Stack, Text } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import Md from "../components/Md";
import { wikiHref } from "../components/wikiPage";
import { AbsTime, BackLink, EmptyState } from "../components/ui";
import { api, type WikiLintFinding, type WikiRun } from "../api";

const cardStyle = { border: "1px solid var(--hairline)", boxShadow: "var(--shadow-card)" };

// error, then warn, then info (or anything else) — the maintenance page is
// the one place a human decides what to look at first, so what's most
// urgent goes on top.
const SEVERITY_ORDER: Record<string, number> = { error: 0, warn: 1, info: 2 };

/** Findings grouped by rule, ordered by severity then by descending count —
 *  spec §11.3's "Findings — live lint output grouped by rule". */
function groupFindings(findings: WikiLintFinding[]): [string, WikiLintFinding[]][] {
  const byRule = new Map<string, WikiLintFinding[]>();
  for (const f of findings) {
    const list = byRule.get(f.rule);
    if (list) list.push(f); else byRule.set(f.rule, [f]);
  }
  return [...byRule.entries()].sort(([, a], [, b]) => {
    const sevA = SEVERITY_ORDER[a[0].severity] ?? 99;
    const sevB = SEVERITY_ORDER[b[0].severity] ?? 99;
    return sevA !== sevB ? sevA - sevB : b.length - a.length;
  });
}

/** A path like "concepts/compute-lease.md" is what the API carries; a reader
 *  scanning a list of runs wants the name, not the directory. */
function slugOf(path: string): string {
  return path.split("/").pop()?.replace(/\.md$/, "") ?? path;
}

/** A run's `merged` entries were `[loser, winner]` pairs before this change
 *  started capturing the commit (spec 9.1); tolerate both shapes rather than
 *  crash on state written before today, same spirit as the OKF parsers. */
function normaliseMerge(
  entry: WikiRun["merged"][number],
): { loser: string; winner: string; commit?: string } {
  return Array.isArray(entry) ? { loser: entry[0], winner: entry[1] } : entry;
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
  // On the PROPOSE path a human just personally authorised a destructive
  // merge (spec 9.1) — service.accept_wiki_merge already returns the commit
  // that is its undo, so this is the one place that SHA must reach a reader
  // rather than be thrown away.
  const accept = useMutation({
    mutationFn: (mid: string) => api.acceptWikiMerge(id, mid),
    onSuccess: (out) => {
      invalidateProposals();
      if (out.applied && out.commit) {
        notifications.show({ color: "teal", title: "Merge applied",
                             message: `Commit ${out.commit.slice(0, 7)}` });
      }
    },
  });
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
        <Text fw={600} size="xl">Maintenance log</Text>
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
                  {/* Amends spec §11.3 (ruled 2026-08-28): a human-accepted
                      merge lands in this same list as agent runs, so it must
                      say so — a reader must not mistake it for an agent's
                      own work. */}
                  {r.by === "human" && (
                    <Badge size="xs" variant="filled" color="grape" data-testid="run-by-human">
                      human
                    </Badge>
                  )}
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
                    {r.merged.map(normaliseMerge).map(({ loser, winner, commit }, i) => (
                      <li key={i}>
                        <Text size="xs">
                          <b>{slugOf(loser)}</b> {"→"}{" "}
                          <Link to={wikiHref(id, winner)}><b>{slugOf(winner)}</b></Link>
                          {commit && (
                            <>
                              {" "}
                              <code className="mono" title={commit} style={{ fontSize: 11 }}>
                                {commit.slice(0, 7)}
                              </code>
                            </>
                          )}
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
        {findings.length === 0 ? (
          <Text size="sm" c="dimmed">No findings.</Text>
        ) : (
          <Stack gap="md">
            {groupFindings(findings).map(([rule, items]) => (
              <div key={rule} data-testid={`finding-group-${rule}`}>
                <Group gap={8} mb={6}>
                  <code className="mono" style={{ fontSize: 12 }}>{rule}</code>
                  <Badge size="xs" variant="light"
                         color={items[0].severity === "error" ? "red" : "gray"}>
                    {items[0].severity}
                  </Badge>
                  <Badge size="xs" variant="light" color="gray">{items.length}</Badge>
                </Group>
                <ul style={{ margin: 0 }}>
                  {items.map((f, i) => (
                    <li key={i}>
                      <Link to={wikiHref(id, f.path)}>{slugOf(f.path)}</Link>
                      <Text component="span" size="xs" c="dimmed"> — {f.message}</Text>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </Stack>
        )}
      </Card>
    </Stack>
  );
}
