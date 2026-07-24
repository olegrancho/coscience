import { Button, Modal, Stack, Textarea, TextInput } from "@mantine/core";
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";

interface Props { opened: boolean; onClose: () => void }

export default function NewProgramModal({ opened, onClose }: Props) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [title, setTitle] = useState("");
  const [goals, setGoals] = useState("");
  const [workdir, setWorkdir] = useState("");
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [error, setError] = useState("");

  const submit = async () => {
    setError("");
    if (!title.trim() || !goals.trim()) {
      setError("Title and goals are required.");
      return;
    }
    try {
      const program = await api.createProgram({
        title: title.trim(), goals: goals.trim(), workdir: workdir.trim(),
      });
      qc.invalidateQueries({ queryKey: ["programs"] });
      onClose();
      navigate(`/programs/${program.id}`);
    } catch (e) { setError(String(e)); }
  };

  return (
    <Modal opened={opened} onClose={onClose} title="New program">
      <Stack>
        <TextInput label="Title" value={title} onChange={(e) => setTitle(e.currentTarget.value)} />
        <Textarea label="Goals" value={goals} autosize minRows={3}
                  onChange={(e) => setGoals(e.currentTarget.value)} />

        {!showAdvanced ? (
          <button type="button" className="linklike" style={{ textAlign: "left" }}
                  onClick={() => setShowAdvanced(true)}>
            + advanced
          </button>
        ) : (
          <TextInput label="Workdir (optional)" value={workdir}
                     onChange={(e) => setWorkdir(e.currentTarget.value)} />
        )}

        {error && <div style={{ color: "red" }}>{error}</div>}
        <Button onClick={submit}>Create program</Button>
      </Stack>
    </Modal>
  );
}
