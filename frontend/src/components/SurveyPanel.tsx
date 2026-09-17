import { useState } from "react";
import { Button, Stack, Text, Textarea } from "@mantine/core";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type SurveyProposal } from "../api";
import Md from "./Md";

interface Props {
  name: string;
  onUseProposal: (p: SurveyProposal) => void;
}

/** Poll every 3s while the agent is working; stop entirely once it's done —
 *  exported as a pure function so it's tested directly rather than through
 *  fake timers. */
export const surveyRefetchInterval = (pending: boolean) => (pending ? 3000 : false);

/** CPU, memory and cards, in the same wording as the add-mode probe summary's
 *  card line (`${vram_gb} GB ${model}`, joined by ", ") — formatted locally
 *  rather than borrowed from `hostOffer` (which drops the model). */
const offerText = (p: SurveyProposal) => {
  const parts: string[] = [];
  if (p.capacity.cpu) parts.push(`${p.capacity.cpu} CPU cores`);
  if (p.capacity.memory_gb) parts.push(`${p.capacity.memory_gb} GB memory`);
  if (p.gpus.length) parts.push(p.gpus.map((g) => `${g.vram_gb} GB ${g.model}`).join(", "));
  return parts.join(" · ") || "nothing declared";
};

/** The server dialog's "Survey with an agent" panel: starts (or resumes) an
 *  agent conversation that checks what the probe found and proposes capacity,
 *  cards and notes — including, for a server with failed checks, written
 *  overrides that are the only way such a server can be added or updated. */
export default function SurveyPanel({ name, onUseProposal }: Props) {
  const qc = useQueryClient();
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const survey = useQuery({
    queryKey: ["survey", name],
    queryFn: () => api.getSurvey(name),
    refetchInterval: (q) => surveyRefetchInterval(!!q.state.data?.pending),
    retry: false,
  });
  const data = survey.data;

  const call = async (fn: () => ReturnType<typeof api.surveyHost>) => {
    setBusy(true); setError("");
    try {
      const result = await fn();
      qc.setQueryData(["survey", name], result);
      setDraft("");
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };
  const start = () => call(() => api.surveyHost(name));
  const send = () => call(() => api.surveyHost(name, draft));

  // Any getSurvey error — first load or a later poll, whether or not earlier
  // data is still cached — is shown; it never silently reverts to a stale view.
  const queryError = survey.isError ? <Text size="sm" c="red">{String(survey.error)}</Text> : null;

  if (!data) {
    return queryError;
  }

  if (!data.started) {
    return (
      <Stack gap={4}>
        <Button onClick={start} loading={busy}>Survey with an agent</Button>
        <Text size="xs" c="dimmed">
          An agent logs in, checks what the probe found, and proposes capacity, cards and notes.
          It runs with full access on this machine, using this backend's SSH keys, and runs
          commands on the server.
        </Text>
        {queryError}
        {error && <Text size="sm" c="red">{error}</Text>}
      </Stack>
    );
  }

  const proposal = data.proposal;

  return (
    <Stack gap={8}>
      <Stack gap={6}>
        {data.messages.map((m, i) => (
          <div key={i}>
            {m.role === "pm"
              ? <div className="md-tight"><Md>{m.text}</Md></div>
              : <Text size="sm">{m.text}</Text>}
          </div>
        ))}
      </Stack>

      {data.pending ? (
        <Text size="sm" c="dimmed">The agent is working…</Text>
      ) : (
        <Stack gap={6}>
          <Textarea aria-label="Message the agent" placeholder="Message the agent…"
                     value={draft} onChange={(e) => setDraft(e.currentTarget.value)} />
          <Button onClick={send} loading={busy} disabled={!draft.trim()}>Send</Button>
        </Stack>
      )}

      {proposal && (
        <Stack gap={4} style={{ border: "1px solid var(--hairline)", borderRadius: 8, padding: 10 }}>
          <Text fw={600} size="sm">Agent's proposal</Text>
          <Text size="sm">{offerText(proposal)}</Text>
          <Text size="sm">{proposal.notes}</Text>
          {proposal.overrides.map((o) => (
            <Text key={o.check} size="sm" c="orange">Override {o.check}: {o.reason}</Text>
          ))}
          {/* Hidden while pending: the agent may still be rewriting this proposal. */}
          {!data.pending && <Button onClick={() => onUseProposal(proposal)}>Use proposal</Button>}
        </Stack>
      )}

      {data.proposal_error && (
        <Text size="sm" c="red">The agent's proposal can't be used: {data.proposal_error}</Text>
      )}
      {queryError}
      {error && <Text size="sm" c="red">{error}</Text>}
    </Stack>
  );
}
