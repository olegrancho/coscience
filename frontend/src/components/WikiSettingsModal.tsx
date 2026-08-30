import { Button, Checkbox, Divider, Group, Modal, Stack, Text } from "@mantine/core";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, type WikiSummary } from "../api";
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

  const save = async () => {
    setSaving(true);
    try {
      const was = seeded.current;
      if (model !== was.model) await api.setProgramWikiModel(programId, model);
      if (merge !== was.merge) await api.setWikiMergePolicy(programId, merge);
      if (enabled !== was.enabled) await api.setProgramWikiEnabled(programId, enabled);
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
