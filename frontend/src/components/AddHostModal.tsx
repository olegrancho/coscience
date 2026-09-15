import { Alert, Badge, Button, Group, Modal, NumberInput, Stack, Switch, Text, TextInput } from "@mantine/core";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { api, type HostProbe } from "../api";

interface Props { opened: boolean; onClose: () => void }
type Amount = number | "";
const DEFAULT_RUN_ROOT = "~/coscience-runs";

export default function AddHostModal({ opened, onClose }: Props) {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [ssh, setSsh] = useState("");
  const [runRoot, setRunRoot] = useState(DEFAULT_RUN_ROOT);
  const [shared, setShared] = useState(false);
  const [programs, setPrograms] = useState("");
  const [owner, setOwner] = useState("");
  const [notes, setNotes] = useState("");
  const [probe, setProbe] = useState<HostProbe | null>(null);
  const [cpu, setCpu] = useState<Amount>("");
  const [memory, setMemory] = useState<Amount>("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const wasOpened = useRef(false);
  // Bumped whenever the declared settings change (including on open-reset and
  // at the start of every probe). A probe that resolves after the generation
  // it was launched under has moved on is stale — its answer described a
  // server/settings combination that isn't what's declared any more — so its
  // result is dropped instead of applied.
  const generation = useRef(0);

  // Start clean on each open, never on a background refresh while it is open.
  useEffect(() => {
    if (opened && !wasOpened.current) {
      generation.current += 1;
      setName(""); setSsh(""); setRunRoot(DEFAULT_RUN_ROOT); setShared(false);
      setPrograms(""); setOwner(""); setNotes(""); setProbe(null);
      setCpu(""); setMemory(""); setError("");
    }
    wasOpened.current = opened;
  }, [opened]);

  // Any declared field changing invalidates the last probe: it checked a
  // different server (or the same one under different settings), so the
  // review and "Add to the pool" must disappear until it is probed again.
  const edit = <T,>(set: (v: T) => void) => (v: T) => {
    generation.current += 1;
    set(v); setProbe(null); setCpu(""); setMemory("");
  };

  const runProbe = async () => {
    generation.current += 1;
    const gen = generation.current;
    setBusy(true); setError(""); setProbe(null);
    try {
      const result = await api.probeHost({
        name: name.trim(), ssh: ssh.trim(), run_root: runRoot.trim(), shared,
        programs: programs.split(",").map((p) => p.trim()).filter(Boolean),
        owner: owner.trim(), notes: notes.trim(),
      });
      if (generation.current !== gen) return; // a field changed while this was in flight
      setProbe(result);
      setCpu(result.proposal?.capacity?.cpu ?? "");
      setMemory(result.proposal?.capacity?.memory_gb ?? "");
    } catch (e) {
      if (generation.current !== gen) return;
      setError(String(e));
    } finally { setBusy(false); }
  };

  const confirm = async () => {
    setBusy(true); setError("");
    const capacity: Record<string, number> = {};
    if (cpu !== "" && cpu > 0) capacity.cpu = cpu;
    if (memory !== "" && memory > 0) capacity.memory_gb = memory;
    try {
      await api.confirmHost({ name: name.trim(), capacity, probed_at: probe?.probed_at });
      qc.invalidateQueries({ queryKey: ["ledger"] });
      onClose();
    } catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  };

  const cards = probe?.proposal?.gpus ?? [];
  const checksFailed = !!probe?.checks?.some((c) => !c.ok);
  const facts = probe?.facts;
  const summary = facts
    ? [
        facts.os,
        facts.cpu_model,
        typeof facts.threads === "number" ? `${facts.threads} threads` : null,
        typeof facts.mem_total_kb === "number"
          ? `${Math.round(facts.mem_total_kb / 1024 / 1024)} GB memory` : null,
      ].filter((p): p is string => typeof p === "string" && p.length > 0).join(" · ")
    : "";
  return (
    <Modal opened={opened} onClose={onClose} title="Add a server" size="lg">
      <Stack>
        <TextInput label="Name" description="How the platform refers to it" value={name}
                   onChange={(e) => edit(setName)(e.currentTarget.value)} />
        <TextInput label="SSH target" description="An alias from ~/.ssh/config, user@host or user@host:port — key login only"
                   value={ssh} onChange={(e) => edit(setSsh)(e.currentTarget.value)} />
        <TextInput label="Run root" description="Where sprint work goes on the server; not inside a synced folder"
                   value={runRoot} onChange={(e) => edit(setRunRoot)(e.currentTarget.value)} />
        <Switch label="Shared with other people" checked={shared}
                onChange={(e) => edit(setShared)(e.currentTarget.checked)} />
        <TextInput label="Programs allowed" description="Comma-separated program ids; empty lets every program use it"
                   value={programs} onChange={(e) => edit(setPrograms)(e.currentTarget.value)} />
        <TextInput label="Owner or contact" value={owner} onChange={(e) => edit(setOwner)(e.currentTarget.value)} />
        <TextInput label="Notes" description="Usage rules, e.g. hours or longest job" value={notes}
                   onChange={(e) => edit(setNotes)(e.currentTarget.value)} />
        <Button onClick={runProbe} loading={busy && !probe} disabled={busy || !name.trim() || !ssh.trim()}>Probe</Button>

        {probe && !probe.ok && <Alert color="red" title="Probe failed">{probe.error}</Alert>}

        {probe?.ok && (
          <>
            {summary && <Text size="sm">{summary}</Text>}
            <Stack gap={4}>
              {probe.checks.map((c) => (
                <Group key={c.name} gap="xs">
                  <Badge color={c.ok ? "teal" : "red"}>{c.ok ? "ok" : "failed"}</Badge>
                  <Text size="sm">{c.name}{c.detail ? ` — ${c.detail}` : ""}</Text>
                </Group>
              ))}
            </Stack>
            {probe.warnings.map((w, i) => <Text key={`${i}-${w}`} size="sm" c="orange">{w}</Text>)}
            <NumberInput label="CPU cores offered" min={0} value={cpu}
                         onChange={(v) => setCpu(v === "" ? "" : Number(v))} />
            <NumberInput label="Memory offered (GB)" min={0} value={memory}
                         onChange={(v) => setMemory(v === "" ? "" : Number(v))} />
            <Text size="sm" c="dimmed">
              {cards.length ? `${cards.map((g) => `${g.vram_gb} GB ${g.model}`).join(", ")}. ` : "No GPUs found. "}
              A server added now waits for remote launch before it takes work.
            </Text>
            {checksFailed
              ? <Text size="sm" c="red">Fix the failed checks on the server, then probe again.</Text>
              : <Button onClick={confirm} loading={busy}>Add to the pool</Button>}
          </>
        )}
        {error && <Text size="sm" c="red">{error}</Text>}
      </Stack>
    </Modal>
  );
}
