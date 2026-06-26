import { Button, Modal, NumberInput, Stack, Textarea, TextInput } from "@mantine/core";
import { useState } from "react";
import { api } from "../api";

interface Props { programId: string; opened: boolean; onClose: () => void; onDone: () => void }

export default function ProposeSprintModal({ programId, opened, onClose, onDone }: Props) {
  const [id, setId] = useState("");
  const [goals, setGoals] = useState("");
  const [run, setRun] = useState("");
  const [priority, setPriority] = useState<number>(0);
  const [error, setError] = useState("");

  const submit = async () => {
    setError("");
    try {
      await api.submitSprint({
        id, goals, program: programId, priority,
        plan: [{ id: "s1", run }],
      });
      onDone(); onClose();
    } catch (e) { setError(String(e)); }
  };

  return (
    <Modal opened={opened} onClose={onClose} title="Propose sprint">
      <Stack>
        <TextInput label="Sprint id" value={id} onChange={(e) => setId(e.currentTarget.value)} />
        <Textarea label="Goals" value={goals} onChange={(e) => setGoals(e.currentTarget.value)} />
        <TextInput label="First step command" value={run}
                   onChange={(e) => setRun(e.currentTarget.value)} />
        <NumberInput label="Priority" value={priority}
                     onChange={(v) => setPriority(Number(v) || 0)} />
        {error && <div style={{ color: "red" }}>{error}</div>}
        <Button onClick={submit}>Submit proposal</Button>
      </Stack>
    </Modal>
  );
}
