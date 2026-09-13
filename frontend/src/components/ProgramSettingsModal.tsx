import { ActionIcon, Button, Checkbox, Group, Modal, NumberInput, Stack, Text, Textarea, TextInput, Tooltip } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { api, type Program } from "../api";
import DirectoryPickerModal from "./DirectoryPickerModal";
import { MergePolicySelect, ModelSelect } from "./ui";

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
  const [wikiModel, setWikiModel] = useState("");
  const [chatModel, setChatModel] = useState("");
  const [workerModel, setWorkerModel] = useState("");
  const [wikiEnabled, setWikiEnabled] = useState(true);
  const [wikiMerge, setWikiMerge] = useState("auto");
  const [workdir, setWorkdir] = useState("");
  const [maxProposed, setMaxProposed] = useState<number | string>("");
  const [instructions, setInstructions] = useState("");
  const [saving, setSaving] = useState(false);
  const [browsing, setBrowsing] = useState(false);
  const seeded = useRef({ goals: "", model: "", wikiModel: "", chatModel: "", workerModel: "",
                          wikiEnabled: true,
                          wikiMerge: "auto", workdir: "", maxProposed: 0, instructions: "" });
  const wasOpened = useRef(false);

  // Seed on the false->true open transition only. The program is refetched by a
  // background poll every few seconds; re-seeding on every render (or listing
  // `program` in the deps) would silently discard whatever the user is mid-typing.
  useEffect(() => {
    if (opened && !wasOpened.current) {
      setGoals(program.goals);
      setModel(program.pm_model);
      setWikiModel(program.wiki_model);
      setChatModel(program.chat_model);
      setWorkerModel(program.worker_model);
      setWikiEnabled(program.wiki_enabled);
      setWikiMerge(program.wiki_merge || "auto");
      setWorkdir(program.workdir);
      setMaxProposed(program.max_proposed || "");
      setInstructions(program.instructions);
      seeded.current = {
        goals: program.goals, model: program.pm_model, workdir: program.workdir,
        wikiModel: program.wiki_model, chatModel: program.chat_model,
        workerModel: program.worker_model,
        wikiEnabled: program.wiki_enabled, wikiMerge: program.wiki_merge || "auto",
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
      if (wikiModel !== was.wikiModel) await api.setProgramWikiModel(program.id, wikiModel);
      if (chatModel !== was.chatModel) await api.setProgramChatModel(program.id, chatModel);
      if (workerModel !== was.workerModel) await api.setProgramWorkerModel(program.id, workerModel);
      if (wikiEnabled !== was.wikiEnabled) {
        await api.setProgramWikiEnabled(program.id, wikiEnabled);
      }
      if (wikiMerge !== was.wikiMerge) await api.setWikiMergePolicy(program.id, wikiMerge);
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

        {/* One dial per job. The planner reasons over the program's state to
            propose experiments; the wiki reads finished results and writes prose
            about them; chat has a human waiting on it. They reward different
            models, so none of them is "the model for this program". */}
        <div>
          <Text size="sm" fw={500}>Models</Text>
          <Text size="xs" c="dimmed" mb={8}>Each job runs on its own model.</Text>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 10 }}>
            <ModelCard title="Planner" hint="Proposes experiments each cycle">
              <ModelSelect fullWidth value={model} onChange={setModel} ariaLabel="planner model" />
            </ModelCard>
            <ModelCard title="Chat" hint="Answers you in program chat">
              <ModelSelect fullWidth value={chatModel} onChange={setChatModel} ariaLabel="chat model" />
            </ModelCard>
            <ModelCard title="Workers" hint="Runs new experiments, unless one names its own">
              <ModelSelect fullWidth value={workerModel} onChange={setWorkerModel} ariaLabel="worker model" />
            </ModelCard>
            <ModelCard title="Wiki" hint="Writes up finished results">
              <ModelSelect fullWidth value={wikiModel} onChange={setWikiModel} ariaLabel="wiki model"
                           disabled={!wikiEnabled} />
            </ModelCard>
          </div>
        </div>

        <div>
          <Text size="sm" fw={500} mb={6}>Wiki</Text>
          <Group gap={16} align="center">
            <Checkbox
              size="xs"
              label="build a wiki for this program"
              aria-label="wiki enabled"
              checked={wikiEnabled}
              onChange={(e) => setWikiEnabled(e.currentTarget.checked)}
            />
            <MergePolicySelect value={wikiMerge} onChange={setWikiMerge}
                                disabled={!wikiEnabled} />
          </Group>
          <Text size="xs" c="dimmed" mt={4}>
            Unchecking stops wiki runs for this program entirely — no ingest is
            launched and no quota is spent on it.
          </Text>
        </div>

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

/** One agent job and the model it runs on, as a small titled tile. */
function ModelCard({ title, hint, children }: { title: string; hint: string; children: ReactNode }) {
  return (
    <div style={{ border: "1px solid var(--hairline)", borderRadius: 8, padding: "8px 10px",
                  display: "grid", gap: 6 }}>
      <div>
        <div style={{ fontSize: 13, fontWeight: 500 }}>{title}</div>
        <div style={{ fontSize: 11, color: "var(--ink-muted)" }}>{hint}</div>
      </div>
      {children}
    </div>
  );
}
