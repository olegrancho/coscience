import { Button, Modal, Stack, Text, TextInput } from "@mantine/core";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { api } from "../api";

/** The two numbers that belong to the platform rather than to any machine (G1): how
 *  many agent processes may run at once, wherever their work lands. A machine's own
 *  CPUs, memory and cards are edited on its card in the servers list — this used to
 *  be a free-form name/value table over one flat map, where those sat
 *  undifferentiated beside these and any name at all could be typed in. */
export const LIMITS = [
  { key: "workers", label: "Worker agents at once",
    help: "Sprint agents running together, on every server combined. Empty means no limit." },
  { key: "housekeepers", label: "Housekeeping agents at once",
    help: "Planner and wiki agents running together. Empty means no limit." },
] as const;

interface Props {
  opened: boolean;
  onClose: () => void;
  capacity: Record<string, number>;
  used: Record<string, number>;
}

export default function PlatformLimitsModal({ opened, onClose, capacity, used }: Props) {
  const qc = useQueryClient();
  const [values, setValues] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const savingRef = useRef(false);
  const wasOpened = useRef(false);

  // Re-seed on the false->true open transition only: the ledger poll hands back a
  // fresh `capacity` object every ~10s, and re-seeding on that would throw away
  // whatever is being typed.
  useEffect(() => {
    if (opened && !wasOpened.current) {
      setValues(Object.fromEntries(LIMITS.map(({ key }) =>
        [key, key in capacity ? String(capacity[key]) : ""])));
      setError("");
    }
    wasOpened.current = opened;
  }, [opened]);

  const collect = (): Record<string, number | null> | null => {
    const out: Record<string, number | null> = {};
    for (const { key } of LIMITS) {
      const raw = (values[key] ?? "").trim();
      if (raw === "") { out[key] = null; continue; }
      const n = Number(raw);
      if (!Number.isFinite(n) || n < 0) { setError(`${key}: the limit must be zero or more.`); return null; }
      out[key] = n;
    }
    return out;
  };

  const save = async () => {
    // A ref, not just state: a double-click runs both handlers before React re-renders.
    if (savingRef.current) return;
    setError("");
    const payload = collect();
    if (!payload) return;
    savingRef.current = true;
    setSaving(true);
    try {
      await api.setPlatformLimits(payload);
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
    <Modal opened={opened} onClose={onClose} title="Platform limits">
      <Stack>
        <Text size="xs" c="dimmed">
          A machine's CPUs, memory and cards are set on its own card in the servers list.
        </Text>
        {LIMITS.map(({ key, label, help }) => {
          const raw = (values[key] ?? "").trim();
          const below = raw !== "" && (used[key] ?? 0) > Number(raw);
          return (
            <div key={key}>
              <TextInput label={label} description={help} placeholder="no limit"
                         inputMode="numeric" value={values[key] ?? ""}
                         onChange={(e) => {
                           const v = e.currentTarget.value;
                           setValues((prev) => ({ ...prev, [key]: v }));
                         }} />
              {below && (
                <Text size="xs" c="dimmed" mt={4}>
                  {used[key]} running now — lowering below that lets them finish and holds
                  back new ones.
                </Text>
              )}
            </div>
          );
        })}
        {error && <Text size="sm" c="red">{error}</Text>}
        <Button onClick={save} disabled={saving} loading={saving}>Save</Button>
      </Stack>
    </Modal>
  );
}
