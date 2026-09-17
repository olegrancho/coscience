import { Alert, Badge, Button, Group, Modal, NumberInput, Stack, Switch, Text, TextInput } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { api, type HostProbe, type LedgerHost, type LocalDetect, type SurveyProposal } from "../api";
import { accessFromHost, accessInvalid, accessPayload, cutOffMessage, sameAccess, type Access } from "./programAccess";
import ProgramAccessInput from "./ProgramAccessInput";
import SurveyPanel from "./SurveyPanel";

interface Props {
  opened: boolean; onClose: () => void;
  host?: LedgerHost; local?: boolean; localCapacity?: Record<string, number>;
}
type Amount = number | "";
interface CardRow { model: string; vram_gb: Amount }
const DEFAULT_RUN_ROOT = "~/coscience-runs";
const IN_USE_SUFFIX = "in use — lowering below that lets running work finish and blocks new grants.";

export default function AddHostModal({ opened, onClose, host, local, localCapacity }: Props) {
  const qc = useQueryClient();
  const mode: "add" | "edit" | "local" = local ? "local" : host ? "edit" : "add";
  const [name, setName] = useState("");
  const [ssh, setSsh] = useState("");
  const [runRoot, setRunRoot] = useState(DEFAULT_RUN_ROOT);
  const [shared, setShared] = useState(false);
  const [access, setAccess] = useState<Access>({ all: true, list: [] });
  const [owner, setOwner] = useState("");
  const [notes, setNotes] = useState("");
  const programsQuery = useQuery({ queryKey: ["programs"], queryFn: api.listPrograms });
  // Local mode's change check: has access moved from what the server declared,
  // seeded once on open (mirrors `wasOpened` below, not re-derived on rerender).
  const initialAccess = useRef<Access>({ all: true, list: [] });
  const [probe, setProbe] = useState<HostProbe | null>(null);
  const [detect, setDetect] = useState<LocalDetect | null>(null);
  const [cpu, setCpu] = useState<Amount>("");
  const [memory, setMemory] = useState<Amount>("");
  const [cards, setCards] = useState<CardRow[]>([]);
  // The last proposal a survey agent's "Use proposal" filled in, kept so its
  // written overrides can still authorize Add/Update after a failed check —
  // see `overridesCoverFailedChecks` below.
  const [usedProposal, setUsedProposal] = useState<SurveyProposal | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const wasOpened = useRef(false);
  // Bumped whenever the declared settings change (including on open-reset and
  // at the start of every probe/detect). A probe that resolves after the
  // generation it was launched under has moved on is stale — its answer
  // described a server/settings combination that isn't declared any more —
  // so its result is dropped instead of applied.
  const generation = useRef(0);

  // Start clean on each open, never on a background refresh while it is open.
  useEffect(() => {
    if (opened && !wasOpened.current) {
      generation.current += 1;
      setProbe(null); setDetect(null); setError(""); setUsedProposal(null);
      if (mode === "edit" && host) {
        setName(host.name); setSsh(host.ssh); setRunRoot(host.run_root); setShared(!!host.shared);
        setAccess(accessFromHost(host)); setOwner(host.owner ?? ""); setNotes(host.notes ?? "");
        setCpu(host.capacity.cpu ?? ""); setMemory(host.capacity.memory_gb ?? "");
        setCards(host.gpus.map((g) => ({ model: g.model, vram_gb: g.vram_gb ?? "" })));
      } else if (mode === "local") {
        setName(host?.name ?? "local"); setSsh(""); setRunRoot(""); setShared(false);
        const seeded = accessFromHost(host);
        setAccess(seeded); initialAccess.current = seeded;
        setOwner(""); setNotes("");
        setCpu((localCapacity?.cpu as Amount) ?? "");
        setMemory((localCapacity?.memory_gb as Amount) ?? "");
        setCards((host?.gpus ?? []).map((g) => ({ model: g.model, vram_gb: g.vram_gb ?? "" })));
      } else {
        setName(""); setSsh(""); setRunRoot(DEFAULT_RUN_ROOT); setShared(false);
        setAccess({ all: true, list: [] }); setOwner(""); setNotes("");
        setCpu(""); setMemory(""); setCards([]);
      }
    }
    wasOpened.current = opened;
  }, [opened, mode, host, localCapacity]);

  // In add mode every declared field invalidates the last probe: `confirmHost`
  // writes those declarations, and the probe record they came with is what
  // vouches for the offered capacity/cards, so all of it goes with any edit.
  // In edit mode the probe only vouches for reachability — only the SSH
  // target or run root actually changes what gets probed, so only those two
  // invalidate a passing re-probe; notes/owner/shared/programs/capacity/cards
  // can change afterward without re-blocking Update.
  const declare = <T,>(set: (v: T) => void, key: "ssh" | "run_root" | "other" = "other") => (v: T) => {
    const invalidates = mode === "add" || key === "ssh" || key === "run_root";
    if (invalidates) { generation.current += 1; setProbe(null); setUsedProposal(null); }
    set(v);
    if (mode === "add") { setCpu(""); setMemory(""); setCards([]); }
  };

  const runProbe = async () => {
    generation.current += 1;
    const gen = generation.current;
    setBusy(true); setError(""); setProbe(null); setUsedProposal(null);
    try {
      const result = await api.probeHost({
        name: name.trim(), ssh: ssh.trim(), run_root: runRoot.trim(), shared,
        ...accessPayload(access),
        owner: owner.trim(), notes: notes.trim(),
      });
      if (generation.current !== gen) return; // a field changed while this was in flight
      setProbe(result);
      if (result.ok) {
        setCpu(result.proposal?.capacity?.cpu ?? "");
        setMemory(result.proposal?.capacity?.memory_gb ?? "");
        setCards((result.proposal?.gpus ?? []).map((g) => ({ model: g.model, vram_gb: g.vram_gb })));
      }
    } catch (e) {
      if (generation.current !== gen) return;
      setError(String(e));
    } finally { setBusy(false); }
  };

  const runDetect = async () => {
    generation.current += 1;
    const gen = generation.current;
    setBusy(true); setError(""); setDetect(null);
    try {
      const result = await api.detectLocal();
      if (generation.current !== gen) return;
      setDetect(result);
      // Detect only fills the cards — CPU/memory stay whatever is currently
      // declared (localCapacity.cpu, and memory empty when not declared).
      // Silently raising CPU or declaring memory off a hardware reading
      // would over-commit this machine without anyone deciding to; "use
      // detected" (below) makes that an explicit choice per field instead.
      if (result.ok) {
        setCards((result.proposal?.gpus ?? []).map((g) => ({ model: g.model, vram_gb: g.vram_gb })));
      }
    } catch (e) {
      if (generation.current !== gen) return;
      setError(String(e));
    } finally { setBusy(false); }
  };

  // `acceptOverrides` is only ever true when a failed check is covered by the
  // survey agent's written overrides (`overridesCoverFailedChecks` below) — it
  // is the sole way a server with failed checks is added.
  const confirmAdd = async (acceptOverrides = false) => {
    setBusy(true); setError("");
    const capacity: Record<string, number> = {};
    if (cpu !== "" && cpu > 0) capacity.cpu = cpu;
    if (memory !== "" && memory > 0) capacity.memory_gb = memory;
    try {
      await api.confirmHost({
        name: name.trim(), capacity, gpus: cardsPayload(), notes: notes.trim(),
        probed_at: probe?.probed_at,
        ...(acceptOverrides ? { accept_overrides: true } : {}),
      });
      qc.invalidateQueries({ queryKey: ["ledger"] });
      onClose();
    } catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  };

  // Fills in what the survey agent proposed. Direct setters (not `declare`),
  // so in add mode this doesn't clear the very probe the proposal came from.
  const use = (p: SurveyProposal) => {
    setUsedProposal(p);
    setCpu(p.capacity.cpu ?? cpu);
    setMemory(p.capacity.memory_gb ?? memory);
    setCards(p.gpus.map((g) => ({ model: g.model, vram_gb: g.vram_gb })));
    // Keeps any `Override …` lines already recorded (from an earlier accepted
    // proposal) below the agent's fresh notes, instead of silently dropping
    // reasons a previous Update wrote down.
    setNotes((prev) => {
      const overrideLines = prev.split("\n").filter((line) => line.startsWith("Override "));
      return [p.notes, ...overrideLines].filter((line) => line.length > 0).join("\n");
    });
  };

  // A card needs both halves to mean anything: a model with no VRAM (or vice
  // versa) isn't a card the backend can place work on — it 422s. A row with
  // neither is just an unused "+ add card" click and is dropped silently.
  const cardHasModel = (c: CardRow) => c.model.trim().length > 0;
  const cardHasVram = (c: CardRow) => c.vram_gb !== "" && Number(c.vram_gb) > 0;
  const cardIssue = (c: CardRow) => cardHasModel(c) !== cardHasVram(c);
  const cardsInvalid = cards.some(cardIssue);

  const cardsPayload = () =>
    cards
      .filter((c) => cardHasModel(c) && cardHasVram(c))
      .map((c) => ({ model: c.model.trim(), vram_gb: Number(c.vram_gb) }));

  const submitEdit = async (acceptOverrides = false) => {
    if (!host) return;
    setBusy(true); setError("");
    // Same shape as `confirmAdd`'s capacity, plus whatever other resource
    // keys the host already carries (workers/housekeepers-style extras),
    // left exactly as they were. `gpu` is a count the backend derives from
    // the card list, never a declared capacity — echoing it back is stale
    // the moment cards change, so it's dropped like cpu/memory_gb and
    // rebuilt (implicitly, via `gpus` below) from the card rows instead.
    const capacity: Record<string, number> = {};
    Object.entries(host.capacity).forEach(([k, v]) => {
      if (k !== "cpu" && k !== "memory_gb" && k !== "gpu") capacity[k] = v;
    });
    if (cpu !== "" && cpu > 0) capacity.cpu = cpu;
    if (memory !== "" && memory > 0) capacity.memory_gb = memory;
    try {
      const result = await api.updateHost(host.name, {
        ssh: ssh.trim(), run_root: runRoot.trim(), shared,
        ...accessPayload(access),
        owner: owner.trim(), notes: notes.trim(),
        capacity, gpus: cardsPayload(),
        ...(probe?.probed_at ? { probed_at: probe.probed_at } : {}),
        ...(acceptOverrides ? { accept_overrides: true } : {}),
      });
      qc.invalidateQueries({ queryKey: ["ledger"] });
      if (result.cut_off?.length) {
        notifications.show({
          color: "yellow", title: "Pinned work is cut off", message: cutOffMessage(result.cut_off),
        });
      }
      onClose();
    } catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  };

  const submitLocal = async () => {
    setBusy(true); setError("");
    const capacity: Record<string, number> = {};
    Object.entries(localCapacity ?? {}).forEach(([k, v]) => {
      if (k !== "cpu" && k !== "memory_gb" && k !== "gpu") capacity[k] = v;
    });
    if (cpu !== "") capacity.cpu = cpu;
    if (memory !== "" && memory > 0) capacity.memory_gb = memory;
    try {
      await api.setCapacity(capacity, cardsPayload());
      if (!sameAccess(access, initialAccess.current)) {
        const result = await api.setHostPrograms("local", accessPayload(access));
        if (result.cut_off?.length) {
          notifications.show({
            color: "yellow", title: "Pinned work is cut off", message: cutOffMessage(result.cut_off),
          });
        }
      }
      qc.invalidateQueries({ queryKey: ["ledger"] });
      onClose();
    } catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  };

  const updateCard = (i: number, patch: Partial<CardRow>) =>
    setCards(cards.map((c, j) => (j === i ? { ...c, ...patch } : c)));
  const removeCard = (i: number) => setCards(cards.filter((_, j) => j !== i));
  const addCard = () => setCards([...cards, { model: "", vram_gb: "" }]);

  const checksFailed = !!probe?.checks?.some((c) => !c.ok);
  // True only once the last-used proposal's overrides cover every failed
  // check by name — a survey agent's written justification for each one,
  // not a blanket bypass.
  const overridesCoverFailedChecks = checksFailed && !!usedProposal &&
    (probe?.checks ?? []).filter((c) => !c.ok)
      .every((fc) => usedProposal.overrides.some((o) => o.check === fc.name));
  const facts = probe?.facts ?? detect?.facts;
  const summary = facts
    ? [
        facts.os,
        facts.cpu_model,
        typeof facts.threads === "number" ? `${facts.threads} threads` : null,
        typeof facts.mem_total_kb === "number"
          ? `${Math.round(facts.mem_total_kb / 1024 / 1024)} GB memory` : null,
      ].filter((p): p is string => typeof p === "string" && p.length > 0).join(" · ")
    : "";

  // What Detect actually read off the hardware, for the "Detected: …" hints
  // beside CPU/memory in local mode — distinct from `cpu`/`memory` state,
  // which Detect no longer overwrites on its own.
  const detectedThreads = detect?.ok && typeof detect.facts.threads === "number" ? detect.facts.threads : undefined;
  const detectedMemGb = detect?.ok && typeof detect.facts.mem_total_kb === "number"
    ? Math.round(detect.facts.mem_total_kb / 1024 / 1024) : undefined;

  const sshChanged = mode === "edit" && !!host && ssh.trim() !== host.ssh;
  const runRootChanged = mode === "edit" && !!host && runRoot.trim() !== host.run_root;
  // A passing probe is either clean or, failing checks, backed by an agent's
  // overrides for every one of them.
  const hasPassingProbe = !!probe?.ok && (!checksFailed || overridesCoverFailedChecks);
  // O10 rule: a failed check only blocks Update when the SSH target or run
  // root changed — that's the only case `update_host` re-checks the probe
  // record for. A human who re-probes an unchanged target just to look, then
  // edits notes or capacity, is not blocked by what it found.
  const needsProbe = mode === "edit" && (sshChanged || runRootChanged) && !hasPassingProbe;
  const updateBlocked = needsProbe || (mode !== "add" && cardsInvalid) || accessInvalid(access);
  // The override path is only "in play" when it's the reason a changed
  // SSH/run-root target isn't blocking Update — an unchanged target's failed
  // checks never block anything, so there's nothing for overrides to unblock.
  const overrideUnblocksUpdate = mode === "edit" && (sshChanged || runRootChanged) &&
    checksFailed && overridesCoverFailedChecks;

  const used = host?.used ?? {};
  const inUseWarnings: string[] = [];
  if (mode !== "add") {
    if (typeof used.cpu === "number" && cpu !== "" && cpu < used.cpu) {
      inUseWarnings.push(`${used.cpu} CPU cores ${IN_USE_SUFFIX}`);
    }
    if (typeof used.memory_gb === "number" && memory !== "" && memory < used.memory_gb) {
      inUseWarnings.push(`${used.memory_gb} GB memory ${IN_USE_SUFFIX}`);
    }
  }

  const title = mode === "local" ? "Configure this machine"
    : mode === "edit" ? `Configure ${host!.name}` : "Add a server";

  const cardRows = (
    <Stack gap={4}>
      {cards.map((c, i) => (
        <Group key={i} gap="xs" wrap="nowrap" align="flex-end">
          <TextInput label={`GPU ${i + 1} model`} placeholder="Model" style={{ flex: 1 }}
                     value={c.model} onChange={(e) => updateCard(i, { model: e.currentTarget.value })} />
          <NumberInput label={`GPU ${i + 1} VRAM (GB)`} placeholder="VRAM (GB)" min={0} style={{ width: 140 }}
                       value={c.vram_gb} onChange={(v) => updateCard(i, { vram_gb: v === "" ? "" : Number(v) })} />
          <Button variant="subtle" color="gray" aria-label={`Remove GPU ${i + 1}`}
                  onClick={() => removeCard(i)}>✕</Button>
        </Group>
      ))}
      <button type="button" className="linklike" style={{ textAlign: "left" }} onClick={addCard}>
        + add card
      </button>
    </Stack>
  );

  const probeResultBlock = (
    <>
      {summary && <Text size="sm">{summary}</Text>}
      {probe?.ok && (
        <Stack gap={4}>
          {probe.checks.map((c) => (
            <Group key={c.name} gap="xs">
              <Badge color={c.ok ? "teal" : "red"}>{c.ok ? "ok" : "failed"}</Badge>
              <Text size="sm">{c.name}{c.detail ? ` — ${c.detail}` : ""}</Text>
            </Group>
          ))}
        </Stack>
      )}
      {(probe?.warnings ?? detect?.warnings ?? []).map((w, i) => (
        <Text key={`${i}-${w}`} size="sm" c="orange">{w}</Text>
      ))}
    </>
  );

  return (
    <Modal opened={opened} onClose={onClose} title={title} size="lg">
      <Stack>
        {mode === "edit" && <TextInput label="Name" value={name} disabled />}
        {mode === "add" && (
          <TextInput label="Name" description="How the platform refers to it" value={name}
                     onChange={(e) => declare(setName)(e.currentTarget.value)} />
        )}

        {mode !== "local" && (
          <>
            <TextInput label="SSH target" description="An alias from ~/.ssh/config, user@host or user@host:port — key login only"
                       value={ssh} onChange={(e) => declare(setSsh, "ssh")(e.currentTarget.value)} />
            <TextInput label="Run root" description="Where sprint work goes on the server; not inside a synced folder"
                       value={runRoot} onChange={(e) => declare(setRunRoot, "run_root")(e.currentTarget.value)} />
            <Switch label="Shared with other people" checked={shared}
                    onChange={(e) => declare(setShared)(e.currentTarget.checked)} />
            <ProgramAccessInput value={access} onChange={declare(setAccess)} programs={programsQuery.data ?? []} />
            <TextInput label="Owner or contact" value={owner} onChange={(e) => declare(setOwner)(e.currentTarget.value)} />
            <TextInput label="Notes" description="Usage rules, e.g. hours or longest job" value={notes}
                       onChange={(e) => declare(setNotes)(e.currentTarget.value)} />
            <Button onClick={runProbe} loading={busy && !probe}
                    disabled={busy || !name.trim() || !ssh.trim() || accessInvalid(access)}>
              {mode === "edit" ? "Re-probe" : "Probe"}
            </Button>
          </>
        )}

        {mode === "local" && <Button onClick={runDetect} loading={busy && !detect}>Detect</Button>}

        {probe && !probe.ok && <Alert color="red" title="Probe failed">{probe.error}</Alert>}
        {detect && !detect.ok && <Alert color="red" title="Detect failed">{detect.error}</Alert>}

        {mode === "add" ? (
          probe?.ok && (
            <>
              {probeResultBlock}
              <NumberInput label="CPU cores offered" min={0} value={cpu}
                           onChange={(v) => setCpu(v === "" ? "" : Number(v))} />
              <NumberInput label="Memory offered (GB)" min={0} value={memory}
                           onChange={(v) => setMemory(v === "" ? "" : Number(v))} />
              <Text size="sm" c="dimmed">
                {cards.length ? `${cards.map((c) => `${c.vram_gb} GB ${c.model}`).join(", ")}. ` : "No GPUs found. "}
                A server added now waits for remote launch before it takes work.
              </Text>
              <SurveyPanel name={name.trim()} onUseProposal={use} />
              {checksFailed && !overridesCoverFailedChecks && (
                <Text size="sm" c="red">Fix the failed checks on the server, then probe again.</Text>
              )}
              {checksFailed && overridesCoverFailedChecks && (
                <Text size="sm" c="dimmed">
                  Failed checks will be accepted on the agent's written reasons shown above.
                </Text>
              )}
              {(!checksFailed || overridesCoverFailedChecks) && (
                <Button onClick={() => confirmAdd(overridesCoverFailedChecks)} loading={busy}
                        disabled={accessInvalid(access)}>
                  {overridesCoverFailedChecks ? "Add with the agent's overrides" : "Add to the pool"}
                </Button>
              )}
            </>
          )
        ) : (
          <>
            {(probe?.ok || detect?.ok) && probeResultBlock}
            {mode === "edit" && probe?.ok && <SurveyPanel name={name.trim()} onUseProposal={use} />}
            <NumberInput label="CPU cores" min={0} value={cpu}
                         onChange={(v) => setCpu(v === "" ? "" : Number(v))} />
            {mode === "local" && detectedThreads !== undefined && (
              <Group gap={6} wrap="nowrap">
                <Text size="xs" c="dimmed">Detected: {detectedThreads} threads</Text>
                <button type="button" className="linklike" aria-label="Use detected CPU cores"
                        onClick={() => setCpu(detectedThreads)}>use detected</button>
              </Group>
            )}
            <NumberInput label="Memory (GB)"
                         description={mode === "local"
                           ? "Declaring memory lets sprints request memory on this machine."
                           : "Optional"}
                         min={0} value={memory}
                         onChange={(v) => setMemory(v === "" ? "" : Number(v))} />
            {mode === "local" && detectedMemGb !== undefined && (
              <Group gap={6} wrap="nowrap">
                <Text size="xs" c="dimmed">Detected: {detectedMemGb} GB</Text>
                <button type="button" className="linklike" aria-label="Use detected memory"
                        onClick={() => setMemory(detectedMemGb)}>use detected</button>
              </Group>
            )}
            {cardRows}
            {mode === "local" && (
              <ProgramAccessInput value={access} onChange={setAccess} programs={programsQuery.data ?? []} />
            )}
            {inUseWarnings.map((w) => <Text key={w} size="sm" c="dimmed">{w}</Text>)}
            {mode === "edit" && needsProbe && (
              <Text size="sm" c="red">Probe the new SSH target before updating</Text>
            )}
            {overrideUnblocksUpdate && (
              <Text size="sm" c="dimmed">
                Failed checks will be accepted on the agent's written reasons shown above.
              </Text>
            )}
            {cardsInvalid && (
              <Text size="sm" c="red">Every GPU card needs a model and its VRAM (GB)</Text>
            )}
            <Button onClick={() => (mode === "edit" ? submitEdit(overrideUnblocksUpdate) : submitLocal())}
                    loading={busy} disabled={updateBlocked}>
              {overrideUnblocksUpdate ? "Update with the agent's overrides" : "Update configuration"}
            </Button>
          </>
        )}

        {error && <Text size="sm" c="red">{error}</Text>}
      </Stack>
    </Modal>
  );
}
