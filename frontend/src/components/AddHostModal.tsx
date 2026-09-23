import { Alert, Badge, Button, Group, Modal, NumberInput, Stack, Switch, Text, TextInput } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { api, type CardSpec, type HostProbe, type LedgerHost, type LocalDetect, type MachineTotals,
         type SurveyProposal } from "../api";
import { cutOffMessage, programsForEdit } from "./programAccess";
import ProgramAccessInput from "./ProgramAccessInput";
import SurveyPanel from "./SurveyPanel";

interface Props {
  opened: boolean; onClose: () => void;
  host?: LedgerHost; local?: boolean; localCapacity?: Record<string, number>;
}
type Amount = number | "";
// `vram_gb` is what Co-Science may use of the card, `total_vram_gb` what it has (G2).
// `on` false keeps the card on file but lends it to no one.
interface CardRow { model: string; vram_gb: Amount; total_vram_gb: Amount; on: boolean }

/** A server's cards for the dialog: the ones in use and the switched-off ones, in
 *  their physical order — the index is the device number, so the order must hold. */
export function cardRowsOf(host?: Pick<LedgerHost, "gpus" | "cards_off">): CardRow[] {
  const on = (host?.gpus ?? []).map((g) => ({ ...g, on: true }));
  const off = (host?.cards_off ?? []).map((g) => ({ ...g, on: false }));
  return [...on, ...off].sort((a, b) => a.index - b.index).map((g) => ({
    model: g.model, vram_gb: g.vram_gb ?? "", total_vram_gb: g.total_vram_gb ?? "", on: g.on,
  }));
}

/** Cards as found by a probe or Detect, keeping what a human already set for a card
 *  in the same position: its available VRAM (while it still fits) and its switch. */
export function mergeDetected(found: CardSpec[], current: CardRow[]): CardRow[] {
  return found.map((g, i) => {
    const total = g.total_vram_gb ?? g.vram_gb;
    const was = current[i]?.model === g.model ? current[i] : undefined;
    const keep = was && was.vram_gb !== "" && Number(was.vram_gb) <= total;
    return { model: g.model, total_vram_gb: total, vram_gb: keep ? was.vram_gb : total,
             on: was ? was.on : true };
  });
}

/** Totals as sent: only what is known; {} clears them. */
export function machinePayload(cpu: Amount, memory: Amount): MachineTotals {
  const out: MachineTotals = {};
  if (cpu !== "" && cpu > 0) out.cpu = cpu;
  if (memory !== "" && memory > 0) out.memory_gb = memory;
  return out;
}
const DEFAULT_RUN_ROOT = "~/coscience-runs";
const IN_USE_SUFFIX = "in use — lowering below that lets running work finish and blocks new grants.";

const samePrograms = (a: string[], b: string[]) => {
  if (a.length !== b.length) return false;
  const setB = new Set(b);
  return a.every((id) => setB.has(id));
};

export default function AddHostModal({ opened, onClose, host, local, localCapacity }: Props) {
  const qc = useQueryClient();
  const mode: "add" | "edit" | "local" = local ? "local" : host ? "edit" : "add";
  const [name, setName] = useState("");
  const [ssh, setSsh] = useState("");
  const [runRoot, setRunRoot] = useState(DEFAULT_RUN_ROOT);
  const [shared, setShared] = useState(false);
  const [programs, setPrograms] = useState<string[]>([]);
  const [owner, setOwner] = useState("");
  const [label, setLabel] = useState("");
  const [notes, setNotes] = useState("");
  const programsQuery = useQuery({ queryKey: ["programs"], queryFn: api.listPrograms });
  // Local mode's change check: has the program list moved from what the server
  // declared, seeded once the program list is known while open (see the
  // seeding effect below) — not re-derived on every rerender.
  const initialPrograms = useRef<string[]>([]);
  // Guards the program-list seeding effect so it runs once per open, after
  // the program list has loaded — reset alongside `wasOpened` on close.
  const seededPrograms = useRef(false);
  const [probe, setProbe] = useState<HostProbe | null>(null);
  const [detect, setDetect] = useState<LocalDetect | null>(null);
  const [cpu, setCpu] = useState<Amount>("");
  const [memory, setMemory] = useState<Amount>("");
  const [cards, setCards] = useState<CardRow[]>([]);
  // The machine's own totals (G2): what the probe found or a human typed in.
  const [totalCpu, setTotalCpu] = useState<Amount>("");
  const [totalMemory, setTotalMemory] = useState<Amount>("");
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
  // The program list itself is seeded separately below, once the program
  // catalog has loaded.
  useEffect(() => {
    if (opened && !wasOpened.current) {
      generation.current += 1;
      setProbe(null); setDetect(null); setError(""); setUsedProposal(null);
      setPrograms([]); seededPrograms.current = false;
      if (mode === "edit" && host) {
        setName(host.name); setSsh(host.ssh); setRunRoot(host.run_root); setShared(!!host.shared);
        setOwner(host.owner ?? ""); setNotes(host.notes ?? ""); setLabel(host.label ?? "");
        setCpu(host.capacity.cpu ?? ""); setMemory(host.capacity.memory_gb ?? "");
        setCards(cardRowsOf(host));
        setTotalCpu(host.machine?.cpu ?? ""); setTotalMemory(host.machine?.memory_gb ?? "");
      } else if (mode === "local") {
        setName(host?.name ?? "local"); setSsh(""); setRunRoot(""); setShared(false);
        setOwner(""); setNotes(""); setLabel(host?.label ?? "");
        setCpu((localCapacity?.cpu as Amount) ?? "");
        setMemory((localCapacity?.memory_gb as Amount) ?? "");
        setCards(cardRowsOf(host));
        setTotalCpu(host?.machine?.cpu ?? ""); setTotalMemory(host?.machine?.memory_gb ?? "");
      } else {
        setName(""); setSsh(""); setRunRoot(DEFAULT_RUN_ROOT); setShared(false);
        setOwner(""); setNotes(""); setLabel("");
        setCpu(""); setMemory(""); setCards([]); setTotalCpu(""); setTotalMemory("");
      }
    }
    wasOpened.current = opened;
  }, [opened, mode, host, localCapacity]);

  // Seeds the ticked programs once the program catalog is loaded, at most once
  // per open. Split from the reset effect above because the seed itself needs
  // every program id (`programsForEdit`), which isn't known until the
  // `listPrograms` query resolves — often after the very render that opens
  // the dialog.
  useEffect(() => {
    if (!opened) { seededPrograms.current = false; return; }
    if (seededPrograms.current || !programsQuery.data) return;
    const allIds = programsQuery.data.map((p) => p.id);
    if (mode === "add") {
      setPrograms(allIds);
    } else {
      const seeded = programsForEdit(host, allIds);
      setPrograms(seeded);
      if (mode === "local") initialPrograms.current = seeded;
    }
    seededPrograms.current = true;
  }, [opened, mode, host, programsQuery.data]);

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
    if (mode === "add") { setCpu(""); setMemory(""); setCards([]); setTotalCpu(""); setTotalMemory(""); }
  };

  // Omits `programs` entirely (rather than sending the not-yet-seeded `[]`)
  // from probe/confirm/update bodies until the program catalog has actually
  // loaded — an unresolved (or failed) `listPrograms` must never look like an
  // intentional "no programs" write. The backend takes an absent `programs`
  // as "leave it alone" on update, and "use the declaration" on add/probe.
  const programsField = () => (seededPrograms.current ? { programs } : {});

  const runProbe = async () => {
    generation.current += 1;
    const gen = generation.current;
    setBusy(true); setError(""); setProbe(null); setUsedProposal(null);
    try {
      const result = await api.probeHost({
        name: name.trim(), ssh: ssh.trim(), run_root: runRoot.trim(), shared,
        ...programsField(),
        owner: owner.trim(), notes: notes.trim(),
      });
      if (generation.current !== gen) return; // a field changed while this was in flight
      setProbe(result);
      if (result.ok) {
        setCpu(result.proposal?.capacity?.cpu ?? "");
        setMemory(result.proposal?.capacity?.memory_gb ?? "");
        setCards((prev) => mergeDetected(result.proposal?.gpus ?? [], prev));
        // What the probe found is the machine's total; what is offered stays a choice.
        setTotalCpu(result.proposal?.machine?.cpu ?? "");
        setTotalMemory(result.proposal?.machine?.memory_gb ?? "");
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
      // Detect fills in what the machine HAS (G2) — never what is offered, which
      // stays whatever is declared until a human changes it.
      if (result.ok) {
        setCards((prev) => mergeDetected(result.proposal?.gpus ?? [], prev));
        if (result.proposal?.machine?.cpu) setTotalCpu(result.proposal.machine.cpu);
        if (result.proposal?.machine?.memory_gb) setTotalMemory(result.proposal.machine.memory_gb);
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
        machine: machinePayload(totalCpu, totalMemory),
        probed_at: probe?.probed_at,
        ...programsField(),
        ...(acceptOverrides ? { accept_overrides: true } : {}),
      });
      qc.invalidateQueries({ queryKey: ["ledger"] });
      onClose();
    } catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  };

  // O12: taking a server out of the pool lives here, beside its configuration,
  // rather than as a button on every row of the servers table.
  const removeThis = async () => {
    const leftover = host?.leftover ?? [];
    // M5: name what stays behind, so the confirm prompt isn't a leap of faith.
    const extra = leftover.length
      ? ` It leaves behind: ${leftover.slice(0, 5).map((l) => l.path).join(", ")}`
        + (leftover.length > 5 ? ` and ${leftover.length - 5} more.` : ".")
      : "";
    if (!window.confirm(
      `Remove ${host!.name}? It takes no new work now and leaves the pool as soon as nothing runs there. `
      + `Its run directories stay on the server.${extra}`,
    )) return;
    setBusy(true); setError("");
    try {
      await api.removeHost(host!.name);
      qc.invalidateQueries({ queryKey: ["ledger"] });
      onClose();
    } catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  };

  const keepThis = async () => {
    setBusy(true); setError("");
    try {
      await api.keepHost(host!.name);
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
    // A card's total belongs to that card: it carries over only when the proposal
    // names the same model in the same position, else the proposed VRAM stands for it.
    setCards((prev) => p.gpus.map((g, i) => {
      const same = prev[i] && prev[i].model === g.model && prev[i].total_vram_gb !== "";
      return { model: g.model, vram_gb: g.vram_gb, on: prev[i]?.on ?? true,
               total_vram_gb: same ? prev[i].total_vram_gb : g.vram_gb };
    }));
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

  const cardsPayload = (): CardSpec[] =>
    cards
      .filter((c) => cardHasModel(c) && cardHasVram(c))
      .map((c) => ({
        model: c.model.trim(), vram_gb: Number(c.vram_gb),
        ...(c.total_vram_gb !== "" && Number(c.total_vram_gb) > 0 ? { total_vram_gb: Number(c.total_vram_gb) } : {}),
        ...(c.on ? {} : { disabled: true }),
      }));

  // G2: what is offered never exceeds what the machine has. Said here rather than
  // left to a 422, and Save waits until it is right.
  const over = (avail: Amount, total: Amount) =>
    avail !== "" && total !== "" && Number(total) > 0 && Number(avail) > Number(total);
  const overTotals = [
    ...(over(cpu, totalCpu) ? [`${cpu} CPU cores available is more than the machine's ${totalCpu}`] : []),
    ...(over(memory, totalMemory) ? [`${memory} GB memory available is more than the machine's ${totalMemory} GB`] : []),
    ...cards.flatMap((c, i) => over(c.vram_gb, c.total_vram_gb)
      ? [`GPU ${i + 1}: ${c.vram_gb} GB available is more than its ${c.total_vram_gb} GB`] : []),
  ];

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
        label: label.trim(),
        ssh: ssh.trim(), run_root: runRoot.trim(), shared,
        ...programsField(),
        owner: owner.trim(), notes: notes.trim(),
        capacity, gpus: cardsPayload(), machine: machinePayload(totalCpu, totalMemory),
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
    // Anything else this machine declares rides along unchanged. The platform limits
    // do not: they are not this machine's, and the server keeps them itself (G2).
    Object.entries(localCapacity ?? {}).forEach(([k, v]) => {
      if (!["cpu", "memory_gb", "gpu", "workers", "housekeepers"].includes(k)) capacity[k] = v;
    });
    if (cpu !== "") capacity.cpu = cpu;
    if (memory !== "" && memory > 0) capacity.memory_gb = memory;
    try {
      await api.setCapacity(capacity, cardsPayload(), label.trim(), machinePayload(totalCpu, totalMemory));
      if (!samePrograms(programs, initialPrograms.current)) {
        const result = await api.setHostPrograms("local", programs);
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
  const addCard = () => setCards([...cards, { model: "", vram_gb: "", total_vram_gb: "", on: true }]);

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
  const updateBlocked = needsProbe || (mode !== "add" && cardsInvalid) || overTotals.length > 0;
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

  // G2: what Co-Science may use beside what the machine has, for CPU and memory.
  const amountPair = (what: string, unit: string, avail: Amount, setAvail: (v: Amount) => void,
                      total: Amount, setTotal: (v: Amount) => void, description?: string) => (
    <Group grow align="flex-start" gap="sm">
      <NumberInput label={`${what} available to Co-Science${unit}`} min={0} value={avail}
                   description={description}
                   max={total !== "" && Number(total) > 0 ? Number(total) : undefined}
                   clampBehavior="none"
                   onChange={(v) => setAvail(v === "" ? "" : Number(v))} />
      <NumberInput label={`${what} on the machine${unit}`} min={0} value={total}
                   description="As found by Detect or a probe; type it in otherwise"
                   onChange={(v) => setTotal(v === "" ? "" : Number(v))} />
    </Group>
  );

  const cardRows = (
    <Stack gap={4}>
      {cards.map((c, i) => (
        <Group key={i} gap="xs" wrap="nowrap" align="flex-end">
          <Switch aria-label={`Use GPU ${i + 1}`} checked={c.on} mb={8}
                  onChange={(e) => updateCard(i, { on: e.currentTarget.checked })} />
          <TextInput label={`GPU ${i + 1} model`} placeholder="Model" style={{ flex: 1 }}
                     value={c.model} onChange={(e) => updateCard(i, { model: e.currentTarget.value })} />
          <NumberInput label="VRAM available (GB)" placeholder="GB" min={0} style={{ width: 130 }}
                       aria-label={`GPU ${i + 1} VRAM available (GB)`} disabled={!c.on}
                       value={c.vram_gb} onChange={(v) => updateCard(i, { vram_gb: v === "" ? "" : Number(v) })} />
          <NumberInput label="on the card (GB)" placeholder="GB" min={0} style={{ width: 110 }}
                       aria-label={`GPU ${i + 1} VRAM on the card (GB)`}
                       value={c.total_vram_gb}
                       onChange={(v) => updateCard(i, { total_vram_gb: v === "" ? "" : Number(v) })} />
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
        {mode !== "add" && (
          <TextInput label="Display name"
                     description={`What the dashboard calls it. Leave it empty to go by "${name}", `
                                  + "which is the name every lease, sprint record and probe is filed "
                                  + "under and never changes."}
                     placeholder={name}
                     value={label} onChange={(e) => setLabel(e.currentTarget.value)} />
        )}
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
            <ProgramAccessInput value={programs} onChange={declare(setPrograms)} programs={programsQuery.data ?? []} />
            <TextInput label="Owner or contact" value={owner} onChange={(e) => declare(setOwner)(e.currentTarget.value)} />
            <TextInput label="Notes" description="Usage rules, e.g. hours or longest job" value={notes}
                       onChange={(e) => declare(setNotes)(e.currentTarget.value)} />
            <Button onClick={runProbe} loading={busy && !probe}
                    disabled={busy || !name.trim() || !ssh.trim()}>
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
              {amountPair("CPU cores", "", cpu, setCpu, totalCpu, setTotalCpu)}
              {amountPair("Memory", " (GB)", memory, setMemory, totalMemory, setTotalMemory)}
              {cards.length ? cardRows : <Text size="sm" c="dimmed">No GPUs found.</Text>}
              {overTotals.map((w) => <Text key={w} size="sm" c="red">{w}</Text>)}
              <Text size="sm" c="dimmed">
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
                        disabled={overTotals.length > 0}>
                  {overridesCoverFailedChecks ? "Add with the agent's overrides" : "Add to the pool"}
                </Button>
              )}
            </>
          )
        ) : (
          <>
            {(probe?.ok || detect?.ok) && probeResultBlock}
            {mode === "edit" && probe?.ok && <SurveyPanel name={name.trim()} onUseProposal={use} />}
            {amountPair("CPU cores", "", cpu, setCpu, totalCpu, setTotalCpu)}
            {amountPair("Memory", " (GB)", memory, setMemory, totalMemory, setTotalMemory,
                        mode === "local" ? "Declaring memory lets sprints request memory on this machine."
                                         : "Optional")}
            {cardRows}
            {overTotals.map((w) => <Text key={w} size="sm" c="red">{w}</Text>)}
            {mode === "local" && (
              <ProgramAccessInput value={programs} onChange={setPrograms} programs={programsQuery.data ?? []} />
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
            {mode === "edit" && (
              <Stack gap={4} style={{ borderTop: "1px solid var(--hairline)", paddingTop: 12 }}>
                <Text size="xs" c="dimmed" fw={600}>Taking it out of the pool</Text>
                {host!.removing || host!.drain ? (
                  <>
                    <Text size="xs" c="dimmed">
                      {host!.removing
                        ? "Marked for removal: it takes no new work and leaves the pool once nothing runs on it."
                        : "Draining: it takes no new work, and what is already running finishes."}
                    </Text>
                    <Button variant="default" size="xs" style={{ alignSelf: "flex-start" }}
                            aria-label={`Keep ${host!.name}`} loading={busy}
                            onClick={() => void keepThis()}>
                      Keep it in the pool
                    </Button>
                  </>
                ) : (
                  <>
                    <Text size="xs" c="dimmed">
                      It takes no new work from the moment you press this, and leaves the pool as soon
                      as nothing runs on it. Its run directories stay on the server.
                    </Text>
                    <Button variant="default" color="red" size="xs" style={{ alignSelf: "flex-start" }}
                            aria-label={`Remove ${host!.name}`} loading={busy}
                            onClick={() => void removeThis()}>
                      Remove this server
                    </Button>
                  </>
                )}
              </Stack>
            )}
          </>
        )}

        {error && <Text size="sm" c="red">{error}</Text>}
      </Stack>
    </Modal>
  );
}
