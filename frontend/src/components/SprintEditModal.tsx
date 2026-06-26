import { Button, Modal, NumberInput, Stack, Switch, Textarea } from "@mantine/core";
import { useState } from "react";
import { api, type Sprint, type SprintPatch } from "../api";
import { editableFields, type SprintStatus } from "../sprintActions";

interface Props { sprint: Sprint; opened: boolean; onClose: () => void; onDone: () => void }

export default function SprintEditModal({ sprint, opened, onClose, onDone }: Props) {
  const f = editableFields(sprint.status as SprintStatus);
  const [goals, setGoals] = useState(sprint.goals);
  const [priority, setPriority] = useState<number>(sprint.priority);
  const [preemptible, setPreemptible] = useState<boolean>(sprint.preemptible);
  const [error, setError] = useState("");

  const save = async () => {
    setError("");
    const patch: SprintPatch = {};
    if (f.goals && goals !== sprint.goals) patch.goals = goals;
    if (f.priority && priority !== sprint.priority) patch.priority = priority;
    if (f.preemptible && preemptible !== sprint.preemptible) patch.preemptible = preemptible;
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
        {!f.goals && <span style={{ fontSize: 12, color: "gray" }}>
          Goals/plan are editable only while proposed. Priority/resources affect future
          scheduling only, not a lease already held.</span>}
        {error && <div style={{ color: "red" }}>{error}</div>}
        <Button onClick={save}>Save</Button>
      </Stack>
    </Modal>
  );
}
