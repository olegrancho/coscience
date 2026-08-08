import { Button, Group, Modal, Stack, Text, TextInput } from "@mantine/core";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { api } from "../api";

interface Props {
  opened: boolean;
  onClose: () => void;
  capacity: Record<string, number>;
  used: Record<string, number>;
}

interface Row { key: string; value: string }

export default function CapacityModal({ opened, onClose, capacity, used }: Props) {
  const qc = useQueryClient();
  const [rows, setRows] = useState<Row[]>([]);
  const [adding, setAdding] = useState(false);
  const [newKey, setNewKey] = useState("");
  const [newValue, setNewValue] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const savingRef = useRef(false);
  const wasOpened = useRef(false);

  // Re-seed from the server on the false->true open transition only, so a
  // stale local edit can't overwrite a change made elsewhere. Gating on
  // `opened` alone (or including `capacity` in the deps) would also re-seed
  // on every background ledger poll while the modal is already open,
  // silently discarding whatever the user is mid-typing. `capacity` is read
  // from the closure, not the deps array, precisely because it can't be
  // trusted to signal "did the server value actually change" — a new poll
  // hands back a fresh object every ~10s even when nothing changed.
  useEffect(() => {
    if (opened && !wasOpened.current) {
      setRows(Object.entries(capacity).map(([key, v]) => ({ key, value: String(v) })));
      setAdding(false); setNewKey(""); setNewValue(""); setError("");
    }
    wasOpened.current = opened;
  }, [opened]);

  const collect = (): Record<string, number> | null => {
    const out: Record<string, number> = {};
    const entries = [...rows, ...(adding && newKey.trim() ? [{ key: newKey, value: newValue }] : [])];
    for (const row of entries) {
      const key = row.key.trim();
      if (!key) { setError("Every resource needs a name."); return null; }
      if (key in out) { setError(`Two resources are both called "${key}".`); return null; }
      const n = Number(row.value);
      if (row.value.trim() === "" || !Number.isFinite(n) || n < 0) {
        setError(`${key}: capacity must be zero or more.`); return null;
      }
      out[key] = n;
    }
    return out;
  };

  // Limits being lowered under what's already leased — worth saying out loud,
  // because the answer (drain, don't kill) isn't obvious.
  const shrinking = rows.filter((r) => (used[r.key] ?? 0) > Number(r.value));

  const save = async () => {
    // Guard on a ref, not just the `saving` state: a double-click dispatches
    // both handlers before React has a chance to re-render and disable the
    // button, so the check has to be synchronous with the first call.
    if (savingRef.current) return;
    setError("");
    const payload = collect();
    if (!payload) return;
    savingRef.current = true;
    setSaving(true);
    try {
      await api.setCapacity(payload);
      qc.invalidateQueries({ queryKey: ["ledger"] });
      onClose();
    } catch (e) {
      setError(String(e));
    } finally {
      savingRef.current = false;
      setSaving(false);
    }
  };

  return (
    <Modal opened={opened} onClose={onClose} title="Edit capacity">
      <Stack>
        {rows.map((row, i) => (
          <Group key={row.key} gap="xs" wrap="nowrap">
            <TextInput label={`${row.key} capacity`} style={{ flex: 1 }}
                       value={row.value}
                       onChange={(e) => setRows(rows.map((r, j) =>
                         j === i ? { ...r, value: e.currentTarget.value } : r))} />
            <Button variant="subtle" color="gray" aria-label={`remove ${row.key}`}
                    style={{ alignSelf: "flex-end" }}
                    onClick={() => setRows(rows.filter((_, j) => j !== i))}>✕</Button>
          </Group>
        ))}

        {adding ? (
          <Group gap="xs" wrap="nowrap">
            <TextInput label="new resource name" style={{ flex: 1 }} value={newKey}
                       onChange={(e) => setNewKey(e.currentTarget.value)} />
            <TextInput label="new resource capacity" style={{ width: 120 }} value={newValue}
                       onChange={(e) => setNewValue(e.currentTarget.value)} />
          </Group>
        ) : (
          <button type="button" className="linklike" style={{ textAlign: "left" }}
                  onClick={() => setAdding(true)}>
            + add resource
          </button>
        )}

        {shrinking.map((r) => (
          <Text key={r.key} size="sm" c="dimmed">
            {used[r.key]} {r.key} in use — lowering below that lets running work finish
            and blocks new grants.
          </Text>
        ))}

        {error && <div style={{ color: "red" }}>{error}</div>}
        <Button onClick={save} disabled={saving} loading={saving}>Save</Button>
      </Stack>
    </Modal>
  );
}
