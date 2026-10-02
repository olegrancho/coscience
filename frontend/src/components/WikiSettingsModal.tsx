import { Button, Checkbox, Divider, Group, Modal, ScrollArea, Stack, Text } from "@mantine/core";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, type WikiDocs, type WikiMigration, type WikiSummary } from "../api";
import { MergePolicySelect, ModelSelect } from "./ui";

interface Props {
  opened: boolean;
  onClose: () => void;
  programId: string;
  summary: WikiSummary;
  /** True while a wiki run holds the bundle. The model and the merge policy are
   *  both read when a run collects, so changing either mid-run would take
   *  effect somewhere unpredictable in the middle of it (spec §11.3). */
  locked: boolean;
  onSaved: () => void;
}

/**
 * The wiki's own settings.
 *
 * These dials also live in Program settings, which is where they belong when
 * you are thinking about the program. But they were additionally sitting loose
 * in the wiki's header — two select boxes wedged between the action buttons —
 * which is what made that row unreadable. Here they are a dialog you open on
 * purpose, and the header goes back to being actions.
 */
export default function WikiSettingsModal(
  { opened, onClose, programId, summary, locked, onSaved }: Props,
) {
  const [model, setModel] = useState(summary.wiki_model);
  const [merge, setMerge] = useState<string>(summary.wiki_merge);
  const [enabled, setEnabled] = useState(summary.wiki_enabled);
  const [saving, setSaving] = useState(false);
  // What the server had when the dialog opened, so only real changes are sent.
  const seeded = useRef({ model: "", merge: "", enabled: true });
  const wasOpened = useRef(false);

  useEffect(() => {
    if (opened && !wasOpened.current) {
      setModel(summary.wiki_model);
      setMerge(summary.wiki_merge);
      setEnabled(summary.wiki_enabled);
      seeded.current = {
        model: summary.wiki_model, merge: summary.wiki_merge, enabled: summary.wiki_enabled,
      };
    }
    wasOpened.current = opened;
  }, [opened, summary]);

  // Documentation sources: fetched when the dialog opens, saved with the rest.
  const [docs, setDocs] = useState<WikiDocs | null>(null);
  const [docsError, setDocsError] = useState("");
  const [picked, setPicked] = useState<string[]>([]);
  const seededDocs = useRef<string[]>([]);
  useEffect(() => {
    if (!opened) return;
    let live = true;
    setDocsError("");
    api.getWikiDocs(programId).then((d) => {
      if (!live) return;
      const on = d.files.filter((f) => f.selected).map((f) => f.path);
      setDocs(d); setPicked(on); seededDocs.current = on;
    }).catch((e) => { if (live) setDocsError(String(e)); });
    return () => { live = false; };
  }, [opened, programId]);
  const togglePick = (path: string, on: boolean) =>
    setPicked((cur) => on ? [...cur, path] : cur.filter((p) => p !== path));

  const [migrating, setMigrating] = useState(false);
  const [migrateError, setMigrateError] = useState("");
  const migrationAction = async (act: () => Promise<unknown>) => {
    setMigrating(true);
    setMigrateError("");
    try {
      await act();
      onSaved();
    } catch (e) {
      setMigrateError(String(e));
    } finally {
      setMigrating(false);
    }
  };

  const save = async () => {
    setSaving(true);
    try {
      const was = seeded.current;
      if (model !== was.model) await api.setProgramWikiModel(programId, model);
      if (merge !== was.merge) await api.setWikiMergePolicy(programId, merge);
      if (enabled !== was.enabled) await api.setProgramWikiEnabled(programId, enabled);
      const docsChanged = picked.length !== seededDocs.current.length
        || picked.some((p) => !seededDocs.current.includes(p));
      if (docs && docsChanged) {
        // In the order the list shows them, so program.md reads the same way.
        const order = docs.files.map((f) => f.path);
        await api.setWikiDocs(programId, [...picked].sort((a, b) => order.indexOf(a) - order.indexOf(b)));
      }
      onSaved();
      onClose();
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal opened={opened} onClose={onClose} title="Wiki settings" size="lg">
      <Stack gap="md">
        <Stack gap={8}>
          <ModelSelect value={model} onChange={setModel} label="wiki model"
                       disabled={!enabled || locked} />
          <Text size="xs" c="dimmed">
            The model that reads finished results and writes these pages. It is
            separate from the planner's model, which reasons about what to run
            next — the two jobs reward different models.
          </Text>
        </Stack>

        <Stack gap={8}>
          <MergePolicySelect value={merge} onChange={setMerge}
                              disabled={!enabled || locked} />
          <Text size="xs" c="dimmed">
            What happens when a run finds two pages about the same thing.
            <b> auto</b> merges them unattended; <b>propose</b> queues each one
            for you to accept or reject in the maintenance log.
          </Text>
        </Stack>

        <Checkbox
          size="sm"
          label="build a wiki for this program"
          aria-label="wiki enabled"
          checked={enabled}
          disabled={locked}
          description="Unchecking stops wiki runs entirely — no ingest is launched and no quota is spent on it. Existing pages stay where they are."
          onChange={(e) => setEnabled(e.currentTarget.checked)}
        />

        <Divider />

        <Stack gap={8}>
          <Group justify="space-between" align="baseline" wrap="nowrap">
            <Text size="sm">Documentation</Text>
            {docs && docs.files.length > 0 && (
              <Text size="xs" c="dimmed">
                {picked.length} of {docs.files.length} files are wiki sources
              </Text>
            )}
          </Group>
          <Text size="xs" c="dimmed">
            Files from the program's working folder that the wiki reads as sources for
            how its code, models and data are built. A ticked file is ingested on the
            next run, and again whenever it changes.
          </Text>
          {docsError ? <Text size="xs" c="red">{docsError}</Text>
            : !docs ? null
            : !docs.workdir ? <Text size="xs" c="dimmed">This program has no working folder.</Text>
            : docs.files.length === 0
              ? <Text size="xs" c="dimmed">No markdown files in {docs.workdir}.</Text>
              : (
                <ScrollArea.Autosize mah={210} type="auto" offsetScrollbars>
                  <Stack gap={6}>
                    {docs.files.map((f) => (
                      <Checkbox key={f.path} size="xs" disabled={!enabled}
                        checked={picked.includes(f.path)}
                        onChange={(e) => togglePick(f.path, e.currentTarget.checked)}
                        label={<>
                          <span className="mono">{f.path}</span>
                          {f.missing && <Text span size="xs" c="red"> — missing</Text>}
                        </>} />
                    ))}
                  </Stack>
                </ScrollArea.Autosize>
              )}
        </Stack>

        <Divider />

        <Stack gap={8}>
          <Group justify="space-between" align="center" wrap="nowrap">
            <Text size="sm">
              Layout: <b>{summary.layout}</b>
              {!summary.layout_upgrade && summary.layout === summary.layout_current &&
                <Text span size="xs" c="dimmed"> — current</Text>}
            </Text>
            {summary.migration ? (
              <Group gap={8} wrap="nowrap">
                {summary.migration.error && (
                  <Button size="xs" variant="light" color="machine" loading={migrating}
                          onClick={() => migrationAction(() => api.migrateWiki(programId))}>
                    Resume
                  </Button>
                )}
                <Button size="xs" variant="subtle" color="gray" loading={migrating}
                        disabled={summary.run?.kind === "migrate"}
                        onClick={() => migrationAction(() => api.cancelWikiMigration(programId))}>
                  Cancel migration
                </Button>
              </Group>
            ) : summary.layout_upgrade ? (
              <Button size="xs" variant="light" color="machine" loading={migrating}
                      onClick={() => migrationAction(() => api.migrateWiki(programId))}>
                Migrate to {summary.layout_upgrade}
              </Button>
            ) : null}
          </Group>
          {summary.migration
            ? <MigrationProgress m={summary.migration} />
            : summary.layout_upgrade && (
              <Text size="xs" c="dimmed">
                A newer layout is available. Migrating rebuilds the pages in it, one
                agent run per step — a map, a run per few pages, then the index —
                while this wiki keeps serving; the old wiki is archived, not deleted.
                Ingests wait until it finishes. See docs/wiki-layouts.md.
              </Text>
            )}
          {migrateError && <Text size="xs" c="red">{migrateError}</Text>}
        </Stack>

        {locked && (
          <Text size="xs" c="dimmed">
            A run is in progress. Both dials are read when a run collects, so
            they stay locked until it finishes.
          </Text>
        )}

        <Divider />

        <Group justify="space-between" align="center">
          {/* The health of the wiki is not a setting, but this is where you
              come looking for it once the header stops carrying every link. */}
          <Link to={`/programs/${programId}/wiki/lint`} className="view"
                style={{ fontSize: 13 }} onClick={onClose}>
            Maintenance log ↗
          </Link>
          <Group gap={8}>
            <Button size="xs" variant="default" onClick={onClose}>Cancel</Button>
            <Button size="xs" color="machine" loading={saving} onClick={save}>Save</Button>
          </Group>
        </Group>
      </Stack>
    </Modal>
  );
}


/** Where a migration has got to, in words: the phases are wiki runs the beat
 *  launches one at a time. */
export function migrationText(m: WikiMigration): string {
  const step = {
    setup: "starts on the next wiki beat",
    map: "drawing up the topic map from the old wiki",
    write: `writing pages — batch ${Math.min(m.batch + 1, m.batches)} of ${m.batches}`,
    finish: "writing the index and summary pages",
  }[m.phase] ?? m.phase;
  return `Migrating ${m.from} → ${m.to}: ${step}.`;
}

function MigrationProgress({ m }: { m: WikiMigration }) {
  return (
    <Stack gap={2}>
      <Text size="xs">{migrationText(m)}</Text>
      {m.error
        ? <Text size="xs" c="red">Stopped: {m.error}. The old wiki is still live; Resume retries the step.</Text>
        : <Text size="xs" c="dimmed">The old wiki keeps serving until the last step swaps the new one in.</Text>}
    </Stack>
  );
}
