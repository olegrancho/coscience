import { Button, Group, Modal, NumberInput, SimpleGrid, Stack, Switch, Text, TextInput, Textarea } from "@mantine/core";
import { useEffect, useState } from "react";
import { api, type Sprint, type SprintPatch } from "../api";
import { editableFields, type SprintStatus } from "../sprintActions";
import { ModelSelect } from "./ui";

interface Props { sprint: Sprint; opened: boolean; onClose: () => void; onDone: () => void }

const COMPUTE_KEYS = ["cpu", "memory_gb", "gpu", "gpu_vram_gb"];
type Amount = number | "";
const sortKeys = (o: Record<string, number>) =>
  JSON.stringify(Object.entries(o).sort(([a], [b]) => a.localeCompare(b)));

/** The plan as the box shows it — one suggested step per line — and back. */
export const planText = (plan: string[]) => plan.join("\n");
export const planSteps = (text: string) => text.split("\n").map((l) => l.trim()).filter(Boolean);

/** Everything a human may change about a sprint, in one wide dialog (P1): what the
 *  work is on the left, how it runs on the right. Which fields are live follows the
 *  sprint's status (`editableFields`); the rest stay visible, greyed, so the dialog
 *  always shows the whole sprint rather than a different form per status. */
export default function SprintEditModal({ sprint, opened, onClose, onDone }: Props) {
  const f = editableFields(sprint.status as SprintStatus);
  const [title, setTitle] = useState(sprint.title);
  const [summary, setSummary] = useState(sprint.summary);
  const [goals, setGoals] = useState(sprint.goals);
  const [plan, setPlan] = useState(planText(sprint.plan));
  const [rationale, setRationale] = useState(sprint.rationale);
  const [priority, setPriority] = useState<number>(sprint.priority);
  const [preemptible, setPreemptible] = useState<boolean>(sprint.preemptible);
  const [model, setModel] = useState(sprint.model);
  const [error, setError] = useState("");

  const r = sprint.resources_required ?? {};
  const [cpu, setCpu] = useState<Amount>(r.cpu ?? "");
  const [memory, setMemory] = useState<Amount>(r.memory_gb ?? "");
  const [gpus, setGpus] = useState<Amount>(r.gpu ?? "");
  const [vram, setVram] = useState<Amount>(r.gpu_vram_gb ?? "");
  const [distributed, setDistributed] = useState<boolean>(sprint.distributed ?? false);
  const amount = (set: (v: Amount) => void) => (v: string | number) => set(v === "" ? "" : Number(v));

  // The dialog stays mounted while `sprint` keeps refreshing underneath it (the PM may
  // edit resources_required/distributed while the page is open). Re-seed every field
  // from the current sprint each time the dialog opens, so a human who opens it and
  // changes only e.g. priority never sends back a stale request.
  useEffect(() => {
    if (!opened) return;
    setTitle(sprint.title);
    setSummary(sprint.summary);
    setGoals(sprint.goals);
    setPlan(planText(sprint.plan));
    setRationale(sprint.rationale);
    setPriority(sprint.priority);
    setPreemptible(sprint.preemptible);
    setModel(sprint.model);
    const req = sprint.resources_required ?? {};
    setCpu(req.cpu ?? "");
    setMemory(req.memory_gb ?? "");
    setGpus(req.gpu ?? "");
    setVram(req.gpu_vram_gb ?? "");
    setDistributed(sprint.distributed ?? false);
    setError("");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opened]);

  const requestFromFields = (): Record<string, number> => {
    const out: Record<string, number> = {};
    for (const [k, v] of Object.entries(r)) if (!COMPUTE_KEYS.includes(k)) out[k] = v;
    if (cpu !== "" && cpu > 0) out.cpu = cpu;
    if (memory !== "" && memory > 0) out.memory_gb = memory;
    if (gpus !== "" && gpus > 0) out.gpu = gpus;
    if (vram !== "" && vram > 0) out.gpu_vram_gb = vram;
    return out;
  };

  // Only what changed is sent: every field the API receives is a field the human set.
  const save = async () => {
    setError("");
    const patch: SprintPatch = {};
    if (f.title && title.trim() !== sprint.title) patch.title = title.trim();
    if (f.summary && summary.trim() !== sprint.summary) patch.summary = summary.trim();
    if (f.goals && goals !== sprint.goals) patch.goals = goals;
    if (f.plan) {
      const steps = planSteps(plan);
      if (JSON.stringify(steps) !== JSON.stringify(sprint.plan)) {
        if (!steps.length) { setError("The plan needs at least one step."); return; }
        patch.plan = steps;
      }
    }
    if (f.rationale && rationale.trim() !== sprint.rationale) patch.rationale = rationale.trim();
    if (f.priority && priority !== sprint.priority) patch.priority = priority;
    if (f.preemptible && preemptible !== sprint.preemptible) patch.preemptible = preemptible;
    if (f.model && model !== sprint.model) patch.model = model;
    if (f.resources) {
      const next = requestFromFields();
      if (sortKeys(next) !== sortKeys(r)) patch.resources_required = next;
      if (distributed !== (sprint.distributed ?? false)) patch.distributed = distributed;
    }
    try { await api.editSprint(sprint.id, patch); onDone(); onClose(); }
    catch (e) { setError(String(e)); }
  };

  const liveAgent = sprint.status === "executing" && sprint.agent_running;

  return (
    <Modal opened={opened} onClose={onClose} size="xl" title={`Edit ${sprint.id}`}>
      <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="xl" verticalSpacing="lg">
        <Stack gap="sm">
          <div className="eyebrow">the work</div>
          <TextInput label="Title" value={title} disabled={!f.title}
                     onChange={(e) => setTitle(e.currentTarget.value)} />
          <Textarea label="Summary" autosize minRows={2} value={summary} disabled={!f.summary}
                    onChange={(e) => setSummary(e.currentTarget.value)} />
          <Textarea label="Goals" autosize minRows={3} maxRows={10} value={goals} disabled={!f.goals}
                    onChange={(e) => setGoals(e.currentTarget.value)} />
          <Textarea label="Plan" description="One suggested step per line" autosize minRows={3} maxRows={12}
                    value={plan} disabled={!f.plan}
                    onChange={(e) => setPlan(e.currentTarget.value)} />
          <Textarea label="Rationale" description="Why this is worth doing" autosize minRows={2} maxRows={8}
                    value={rationale} disabled={!f.rationale}
                    onChange={(e) => setRationale(e.currentTarget.value)} />
          {!f.goals && f.title && (
            <Text size="xs" c="dimmed">
              Goals, plan and rationale are what was approved, so they are editable only
              while proposed. The title and summary can still be improved.
            </Text>
          )}
        </Stack>

        <Stack gap="sm">
          <div className="eyebrow">how it runs</div>
          <Group grow align="flex-start">
            <NumberInput label="Priority" value={priority} disabled={!f.priority}
                         onChange={(v) => setPriority(Number(v) || 0)} />
            <div>
              <Text size="sm" fw={500} mb={4}>Worker model</Text>
              <ModelSelect value={model} onChange={setModel} disabled={!f.model}
                           ariaLabel="Worker model" fullWidth />
            </div>
          </Group>
          {liveAgent && model !== sprint.model && (
            <Text size="xs" c="dimmed">
              Its agent is running: saving restarts it on the new model, resuming from its scratchpad.
            </Text>
          )}
          <Switch label="Preemptible" checked={preemptible} disabled={!f.preemptible}
                  onChange={(e) => setPreemptible(e.currentTarget.checked)} />
          <SimpleGrid cols={2} spacing="sm">
            <NumberInput label="CPU cores" min={0} value={cpu} disabled={!f.resources}
                         onChange={amount(setCpu)} />
            <NumberInput label="Memory (GB)" min={0} value={memory} disabled={!f.resources}
                         onChange={amount(setMemory)} />
            <NumberInput label="GPUs" min={0} allowDecimal={false} value={gpus} disabled={!f.resources}
                         onChange={amount(setGpus)} />
            <NumberInput label="VRAM per GPU (GB)" min={0} value={vram} disabled={!f.resources}
                         onChange={amount(setVram)} />
          </SimpleGrid>
          <Text size="xs" c="dimmed">
            An empty VRAM takes whole cards; a value shares cards with other work.
          </Text>
          <Switch label="May split across hosts" checked={distributed} disabled={!f.resources}
                  description="Recorded for now; placement still uses one host"
                  onChange={(e) => setDistributed(e.currentTarget.checked)} />
          {f.priority && !f.goals && (
            <Text size="xs" c="dimmed">
              Priority and compute affect future scheduling only, not a lease already held.
            </Text>
          )}
        </Stack>
      </SimpleGrid>

      {error && <Text size="sm" c="red" mt="md">{error}</Text>}
      <Group justify="flex-end" mt="lg">
        <Button variant="subtle" color="gray" onClick={onClose}>Cancel</Button>
        <Button onClick={save}>Save</Button>
      </Group>
    </Modal>
  );
}
