import { ActionIcon, Button, Group, Modal, NumberInput, Stack, Textarea, TextInput, Tooltip } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useEffect, useRef, useState } from "react";
import { api, type Program } from "../api";
import DirectoryPickerModal from "./DirectoryPickerModal";
import { ModelSelect } from "./ui";

interface Props {
  opened: boolean;
  onClose: () => void;
  program: Program;
  onSaved: () => void;
}

/** Every program setting in one dialog. The same settings stay editable inline on
 *  the program page — this is a second door to the same state, not a replacement. */
export default function ProgramSettingsModal({ opened, onClose, program, onSaved }: Props) {
  const [goals, setGoals] = useState("");
  const [model, setModel] = useState("");
  const [workdir, setWorkdir] = useState("");
  const [maxProposed, setMaxProposed] = useState<number | string>("");
  const [instructions, setInstructions] = useState("");
  const [saving, setSaving] = useState(false);
  const [browsing, setBrowsing] = useState(false);
  const seeded = useRef({ goals: "", model: "", workdir: "", maxProposed: 0, instructions: "" });
  const wasOpened = useRef(false);

  // Seed on the false->true open transition only. The program is refetched by a
  // background poll every few seconds; re-seeding on every render (or listing
  // `program` in the deps) would silently discard whatever the user is mid-typing.
  useEffect(() => {
    if (opened && !wasOpened.current) {
      setGoals(program.goals);
      setModel(program.pm_model);
      setWorkdir(program.workdir);
      setMaxProposed(program.max_proposed || "");
      setInstructions(program.instructions);
      seeded.current = {
        goals: program.goals, model: program.pm_model, workdir: program.workdir,
        maxProposed: program.max_proposed, instructions: program.instructions,
      };
    }
    wasOpened.current = opened;
  }, [opened]);

  // Drop any in-flight folder browse when the dialog closes, so a picker left
  // open doesn't reappear the next time settings is opened. Separate from the
  // seeding effect above — it must not gate on `wasOpened`, or reseed fields.
  useEffect(() => {
    if (!opened) setBrowsing(false);
  }, [opened]);

  const save = async () => {
    const was = seeded.current;
    const cap = maxProposed === "" ? 0 : Number(maxProposed);
    const folder = workdir.trim();
    setSaving(true);
    try {
      let workdirResult: { workdir: string; exists: boolean } | undefined;
      if (goals.trim() !== was.goals) await api.setProgramGoals(program.id, goals.trim());
      if (model !== was.model) await api.setProgramModel(program.id, model);
      if (folder !== was.workdir) workdirResult = await api.setProgramWorkdir(program.id, folder);
      if (cap !== was.maxProposed) await api.setProgramMaxProposed(program.id, cap);
      if (instructions !== was.instructions) await api.setProgramInstructions(program.id, instructions);
      const staleWorkdir = workdirResult?.workdir && !workdirResult.exists;
      notifications.show({
        color: staleWorkdir ? "yellow" : "teal",
        title: "Settings saved",
        message: staleWorkdir
          ? `Saved, but ${workdirResult!.workdir} doesn't exist yet — agents fall back to the control repo until it does.`
          : "Program settings updated.",
      });
      onSaved();
      onClose();
    } catch (e) {
      notifications.show({ color: "red", title: "Couldn't save settings", message: String(e) });
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal opened={opened} onClose={onClose} title="Program settings" size="lg">
      <Stack gap="md">
        <Textarea
          label="Goals"
          aria-label="program goals"
          description="What this program is trying to achieve."
          autosize
          minRows={2}
          value={goals}
          onChange={(e) => setGoals(e.currentTarget.value)}
        />

        <Group gap={8} align="center">
          <ModelSelect value={model} onChange={setModel} label="planner model" />
        </Group>

        <TextInput
          label="Project folder"
          aria-label="project folder"
          className="mono"
          value={workdir}
          placeholder="control repo — set a path to run this program's agents there"
          onChange={(e) => setWorkdir(e.currentTarget.value)}
          rightSectionPointerEvents="all"
          rightSection={
            <Tooltip label="Browse folders on the server" withArrow>
              <ActionIcon variant="subtle" size="sm" aria-label="browse folders"
                          onClick={() => setBrowsing(true)}>📁</ActionIcon>
            </Tooltip>
          }
        />

        <NumberInput
          label="Max proposed experiments"
          aria-label="max proposed experiments"
          description="How many experiments may wait for your review at once. Blank = 4."
          min={1}
          max={20}
          allowDecimal={false}
          value={maxProposed}
          onChange={setMaxProposed}
        />

        <Textarea
          label="Standing instructions"
          aria-label="standing instructions"
          description="House rules the planner follows every cycle."
          autosize
          minRows={4}
          value={instructions}
          onChange={(e) => setInstructions(e.currentTarget.value)}
        />

        <Group justify="flex-end" gap={8}>
          <Button variant="default" onClick={onClose} disabled={saving}>Cancel</Button>
          <Button color="machine" loading={saving} onClick={save}>Save</Button>
        </Group>
      </Stack>

      <DirectoryPickerModal
        opened={browsing}
        initialPath={workdir}
        onClose={() => setBrowsing(false)}
        onPick={(picked) => setWorkdir(picked)}
      />
    </Modal>
  );
}
