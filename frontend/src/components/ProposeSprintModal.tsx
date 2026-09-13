import { ActionIcon, Button, Group, Modal, MultiSelect, NumberInput, Select, Stack, Text, Textarea, TextInput } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "../api";

interface Props {
  programId: string; opened: boolean; onClose: () => void; onDone: () => void;
  fromIdea?: { id: string; text: string };   // promoting this pool idea: pre-fills the form
}

const ARTIFACT_KINDS = [
  { value: "md", label: "Markdown" },
  { value: "data", label: "Data" },
  { value: "figure", label: "Figure" },
  { value: "page", label: "Page" },
];

interface NewArtifactRow { aid: string; title: string; kind: string }

export default function ProposeSprintModal({ programId, opened, onClose, onDone, fromIdea }: Props) {
  const [id, setId] = useState("");
  const [goals, setGoals] = useState("");
  const [title, setTitle] = useState("");
  const [summary, setSummary] = useState("");
  const [rationale, setRationale] = useState("");
  const [drafting, setDrafting] = useState(false);

  // Seed from the idea each time the dialog opens for it; typing afterwards wins.
  useEffect(() => {
    if (opened && fromIdea) {
      setGoals(fromIdea.text);
      setId((prev) => prev || `${programId}-idea-${fromIdea.id}`);
    }
  }, [opened, fromIdea?.id]);
  const [steps, setSteps] = useState("");
  const [priority, setPriority] = useState<number>(0);
  const [error, setError] = useState("");
  const [showArtifacts, setShowArtifacts] = useState(false);
  const [boundIds, setBoundIds] = useState<string[]>([]);
  const [newArtifacts, setNewArtifacts] = useState<NewArtifactRow[]>([]);

  // Only fetch the artifact list once the section is opened — keeps the common
  // (no-artifacts) path from firing an extra request.
  const artifacts = useQuery({
    queryKey: ["artifacts", programId],
    queryFn: () => api.listArtifacts(programId),
    enabled: opened && showArtifacts,
  });
  const artifactOptions = (artifacts.data ?? []).map((a) => ({ value: a.id, label: a.title || a.id }));

  const addRow = () => setNewArtifacts((rows) => [...rows, { aid: "", title: "", kind: "md" }]);
  const updateRow = (i: number, patch: Partial<NewArtifactRow>) =>
    setNewArtifacts((rows) => rows.map((r, idx) => (idx === i ? { ...r, ...patch } : r)));
  const removeRow = (i: number) => setNewArtifacts((rows) => rows.filter((_, idx) => idx !== i));

  // The planner drafts every field a PM proposal carries; the human edits, then submits.
  const draft = async () => {
    if (!fromIdea) return;
    setError("");
    setDrafting(true);
    try {
      const d = await api.draftSprintFromIdea(programId, fromIdea.id);
      setId(d.id); setTitle(d.title); setSummary(d.summary); setGoals(d.goals);
      setSteps(d.plan.join("\n")); setPriority(d.priority); setRationale(d.rationale);
    } catch (e) { setError(String(e)); }
    finally { setDrafting(false); }
  };

  const submit = async () => {
    setError("");
    try {
      const plan = steps.split("\n").map((s) => s.trim()).filter(Boolean);
      const artifactsCreate = newArtifacts
        .map((r) => ({ aid: r.aid.trim(), title: r.title.trim(), kind: r.kind }))
        .filter((r) => r.aid && r.title);
      await api.submitSprint({
        id, goals, program: programId, priority,
        plan: plan.length ? plan : [goals],
        ...(boundIds.length ? { artifacts_bound: boundIds } : {}),
        ...(artifactsCreate.length ? { artifacts_create: artifactsCreate } : {}),
        ...(fromIdea ? { from_idea: fromIdea.id } : {}),
        ...(title.trim() ? { title: title.trim() } : {}),
        ...(summary.trim() ? { summary: summary.trim() } : {}),
        ...(rationale.trim() ? { rationale: rationale.trim() } : {}),
      });
      onDone(); onClose();
    } catch (e) { setError(String(e)); }
  };

  return (
    <Modal opened={opened} onClose={onClose} title={fromIdea ? "Make a sprint from this idea" : "Propose sprint"}>
      <Stack>
        {fromIdea && (
          <Group justify="space-between" wrap="nowrap" gap={8}>
            <Text size="xs" c="dimmed">Let the planner fill this in from the idea, then edit it.</Text>
            <Button variant="light" color="machine" size="xs" loading={drafting} onClick={draft}>
              Draft with AI
            </Button>
          </Group>
        )}
        <TextInput label="Sprint id" value={id} onChange={(e) => setId(e.currentTarget.value)} />
        <TextInput label="Title" value={title} onChange={(e) => setTitle(e.currentTarget.value)} />
        <Textarea label="Summary" value={summary} autosize minRows={1}
                  onChange={(e) => setSummary(e.currentTarget.value)} />
        <Textarea label="Goals" value={goals} autosize minRows={2} onChange={(e) => setGoals(e.currentTarget.value)} />
        <Textarea label="Suggested steps (one per line — guidance for the agent)" value={steps}
                  autosize minRows={2} onChange={(e) => setSteps(e.currentTarget.value)} />
        <Textarea label="Rationale" value={rationale} autosize minRows={1}
                  onChange={(e) => setRationale(e.currentTarget.value)} />
        <NumberInput label="Priority" value={priority}
                     onChange={(v) => setPriority(Number(v) || 0)} />

        {!showArtifacts ? (
          <button type="button" className="linklike" style={{ textAlign: "left" }}
                  onClick={() => setShowArtifacts(true)}>
            + bind or create artifacts
          </button>
        ) : (
          <Stack gap={8} style={{ border: "1px solid var(--hairline)", borderRadius: 8, padding: 10 }}>
            <Text size="xs" fw={600} c="dimmed">Artifacts (optional)</Text>
            <MultiSelect
              label="Bind existing artifacts"
              placeholder="Pick artifacts this sprint will work on"
              data={artifactOptions}
              value={boundIds}
              onChange={setBoundIds}
              searchable
              clearable
              disabled={artifacts.isLoading}
            />
            <Stack gap={6}>
              <Text size="xs" c="dimmed">Declare new artifacts this sprint will create</Text>
              {newArtifacts.map((row, i) => (
                <Group key={i} gap={6} wrap="nowrap" align="flex-end">
                  <TextInput size="xs" placeholder="id" style={{ flex: 1 }} value={row.aid}
                             onChange={(e) => updateRow(i, { aid: e.currentTarget.value })} />
                  <TextInput size="xs" placeholder="title" style={{ flex: 2 }} value={row.title}
                             onChange={(e) => updateRow(i, { title: e.currentTarget.value })} />
                  <Select size="xs" data={ARTIFACT_KINDS} value={row.kind} style={{ width: 110 }}
                          onChange={(v) => updateRow(i, { kind: v ?? "md" })} allowDeselect={false} />
                  <ActionIcon variant="default" aria-label="remove artifact row" onClick={() => removeRow(i)}>×</ActionIcon>
                </Group>
              ))}
              <Button variant="subtle" size="xs" onClick={addRow} style={{ alignSelf: "flex-start" }}>
                + add artifact to create
              </Button>
            </Stack>
          </Stack>
        )}

        {error && <div style={{ color: "red" }}>{error}</div>}
        <Button onClick={submit}>Submit proposal</Button>
      </Stack>
    </Modal>
  );
}
