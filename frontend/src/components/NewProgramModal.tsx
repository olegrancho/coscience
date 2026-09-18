import { ActionIcon, Button, Checkbox, Modal, Stack, Text, Textarea, TextInput, Tooltip } from "@mantine/core";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import DirectoryPickerModal from "./DirectoryPickerModal";

interface Props { opened: boolean; onClose: () => void }

export default function NewProgramModal({ opened, onClose }: Props) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [title, setTitle] = useState("");
  const [goals, setGoals] = useState("");
  const [workdir, setWorkdir] = useState("");
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [error, setError] = useState("");
  const [browsing, setBrowsing] = useState(false);
  const [checkedHosts, setCheckedHosts] = useState<Set<string>>(new Set());
  const ledger = useQuery({ queryKey: ["ledger"], queryFn: api.getLedger, enabled: opened });
  // Seed every server ticked the first time the ledger loads while open — not
  // on every background refetch, or a click mid-edit would be discarded.
  const seededHosts = useRef(false);
  useEffect(() => {
    if (!opened) { seededHosts.current = false; return; }
    if (seededHosts.current || !ledger.data?.hosts) return;
    setCheckedHosts(new Set(ledger.data.hosts.map((h) => h.name)));
    seededHosts.current = true;
  }, [opened, ledger.data]);

  const submit = async () => {
    setError("");
    if (!title.trim() || !goals.trim()) {
      setError("Title and goals are required.");
      return;
    }
    try {
      const program = await api.createProgram({
        title: title.trim(), goals: goals.trim(), workdir: workdir.trim(),
        // Omit `hosts` entirely (rather than the not-yet-seeded `[]`) until the
        // ledger has actually loaded — the backend takes an absent `hosts` as
        // "every server", which is the intended default; an explicit `[]`
        // means "no server" and would strand the program on creation.
        ...(seededHosts.current ? { hosts: [...checkedHosts] } : {}),
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

        <div>
          <Text size="sm" fw={500}>Servers</Text>
          <Text size="xs" c="dimmed" mb={8}>
            Where this program's sprints may run. You can change this later in the program's settings.
          </Text>
          <Stack gap={4}>
            {(ledger.data?.hosts ?? []).map((h) => (
              <Checkbox
                key={h.name}
                label={h.name === "local" ? "this machine" : h.name}
                aria-label={`may run on ${h.name}`}
                checked={checkedHosts.has(h.name)}
                onChange={() => setCheckedHosts((prev) => {
                  const next = new Set(prev);
                  if (next.has(h.name)) next.delete(h.name); else next.add(h.name);
                  return next;
                })}
              />
            ))}
          </Stack>
        </div>

        {!showAdvanced ? (
          <button type="button" className="linklike" style={{ textAlign: "left" }}
                  onClick={() => setShowAdvanced(true)}>
            + advanced
          </button>
        ) : (
          <>
            <TextInput label="Workdir (optional)" value={workdir}
                       rightSectionPointerEvents="all"
                       rightSection={
                         <Tooltip label="Browse folders on the server" withArrow>
                           <ActionIcon variant="subtle" size="sm" aria-label="browse folders"
                                       onClick={() => setBrowsing(true)}>📁</ActionIcon>
                         </Tooltip>
                       }
                       onChange={(e) => setWorkdir(e.currentTarget.value)} />
            <DirectoryPickerModal opened={browsing} initialPath={workdir}
                                  onClose={() => setBrowsing(false)} onPick={setWorkdir} />
          </>
        )}

        {error && <div style={{ color: "red" }}>{error}</div>}
        <Button onClick={submit}>Create program</Button>
      </Stack>
    </Modal>
  );
}
