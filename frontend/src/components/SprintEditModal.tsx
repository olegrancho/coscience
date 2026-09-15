import { Button, Modal, NumberInput, Stack, Switch, Textarea } from "@mantine/core";
import { useEffect, useState } from "react";
import { api, type Sprint, type SprintPatch } from "../api";
import { editableFields, type SprintStatus } from "../sprintActions";

interface Props { sprint: Sprint; opened: boolean; onClose: () => void; onDone: () => void }

const COMPUTE_KEYS = ["cpu", "memory_gb", "gpu", "gpu_vram_gb"];
type Amount = number | "";
const sortKeys = (o: Record<string, number>) =>
  JSON.stringify(Object.entries(o).sort(([a], [b]) => a.localeCompare(b)));

export default function SprintEditModal({ sprint, opened, onClose, onDone }: Props) {
  const f = editableFields(sprint.status as SprintStatus);
  const [goals, setGoals] = useState(sprint.goals);
  const [priority, setPriority] = useState<number>(sprint.priority);
  const [preemptible, setPreemptible] = useState<boolean>(sprint.preemptible);
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
    setGoals(sprint.goals);
    setPriority(sprint.priority);
    setPreemptible(sprint.preemptible);
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

  const save = async () => {
    setError("");
    const patch: SprintPatch = {};
    if (f.goals && goals !== sprint.goals) patch.goals = goals;
    if (f.priority && priority !== sprint.priority) patch.priority = priority;
    if (f.preemptible && preemptible !== sprint.preemptible) patch.preemptible = preemptible;
    if (f.resources) {
      const next = requestFromFields();
      if (sortKeys(next) !== sortKeys(r)) patch.resources_required = next;
      if (distributed !== (sprint.distributed ?? false)) patch.distributed = distributed;
    }
    try { await api.editSprint(sprint.id, patch); onDone(); onClose(); }
    catch (e) { setError(String(e)); }
  };

  return (
    <Modal opened={opened} onClose={onClose} title={`Edit ${sprint.id}`}>
      <Stack>
        <Textarea label="Goals" value={goals} disabled={!f.goals}
                  onChange={(e) => setGoals(e.currentTarget.value)} />
        <NumberInput label="Priority" value={priority} disabled={!f.priority}
                     onChange={(v) => setPriority(Number(v) || 0)} />
        <Switch label="Preemptible" checked={preemptible} disabled={!f.preemptible}
                onChange={(e) => setPreemptible(e.currentTarget.checked)} />
        <NumberInput label="CPU cores" min={0} value={cpu} disabled={!f.resources}
                     onChange={amount(setCpu)} />
        <NumberInput label="Memory (GB)" min={0} value={memory} disabled={!f.resources}
                     onChange={amount(setMemory)} />
        <NumberInput label="GPUs" min={0} allowDecimal={false} value={gpus} disabled={!f.resources}
                     onChange={amount(setGpus)} />
        <NumberInput label="VRAM per GPU (GB)" min={0} value={vram} disabled={!f.resources}
                     description="Empty takes whole cards; a value shares cards with other work"
                     onChange={amount(setVram)} />
        <Switch label="May split across hosts" checked={distributed} disabled={!f.resources}
                description="Recorded for now; placement still uses one host"
                onChange={(e) => setDistributed(e.currentTarget.checked)} />
        {!f.goals && <span style={{ fontSize: 12, color: "gray" }}>
          Goals/plan are editable only while proposed. Priority/resources affect future
          scheduling only, not a lease already held.</span>}
        {error && <div style={{ color: "red" }}>{error}</div>}
        <Button onClick={save}>Save</Button>
      </Stack>
    </Modal>
  );
}
