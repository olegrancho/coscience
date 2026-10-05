import { Button, Group, Modal, NumberInput, Stack, Table, Text } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, type GateKind, type UsageGates } from "../api";
import { GATE_NAMES } from "./ui";

const KINDS: GateKind[] = ["pm", "worker", "wiki"];
const WHAT: Record<GateKind, string> = {
  pm: "planner cycles and catch-up reports",
  worker: "experiment agents",
  wiki: "wiki ingests, lints and sweeps",
};

/** G1: set where each kind of agent stops launching. The loops read the new lines on
 *  their next check — nothing restarts. Chats and a replan someone asks for are not
 *  gated by these. */
export default function UsageGatesModal({ gates, opened, onClose }:
  { gates: UsageGates; opened: boolean; onClose: () => void }) {
  const qc = useQueryClient();
  const [draft, setDraft] = useState<UsageGates>(gates);
  const [saving, setSaving] = useState(false);
  useEffect(() => { if (opened) setDraft(gates); }, [opened, gates]);
  const set = (k: GateKind, w: "5h" | "week", v: number | string) =>
    setDraft((d) => ({ ...d, [k]: { ...d[k], [w]: Number(v) } }));
  const save = async () => {
    setSaving(true);
    try {
      await api.setUsageGates(draft);
      await qc.invalidateQueries({ queryKey: ["usage"] });
      onClose();
    } catch (e) {
      notifications.show({ color: "red", title: "Couldn't save", message: String(e) });
    } finally {
      setSaving(false);
    }
  };
  return (
    <Modal opened={opened} onClose={onClose} title="Where agents stop launching" size="lg">
      <Stack gap="md">
        <Text size="sm" c="dimmed">
          Each kind of agent stops starting new work once Claude usage reaches its line in a
          window, leaving the rest for you. A chat, or a replan you ask for, can still use
          the whole window. Changes apply on the loops' next check.
        </Text>
        <Table>
          <Table.Thead>
            <Table.Tr><Table.Th>Agent</Table.Th><Table.Th>5-hour window</Table.Th><Table.Th>Weekly window</Table.Th></Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {KINDS.map((k) => (
              <Table.Tr key={k}>
                <Table.Td>
                  <Text size="sm" fw={500}>{GATE_NAMES[k]}</Text>
                  <Text size="xs" c="dimmed">{WHAT[k]}</Text>
                </Table.Td>
                {(["5h", "week"] as const).map((w) => (
                  <Table.Td key={w}>
                    <NumberInput size="xs" w={90} min={1} max={100} suffix="%" clampBehavior="strict"
                      aria-label={`${GATE_NAMES[k]} ${w === "5h" ? "5-hour" : "weekly"} line`}
                      value={draft[k]?.[w]} onChange={(v) => set(k, w, v)} />
                  </Table.Td>
                ))}
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>Cancel</Button>
          <Button color="machine" loading={saving} onClick={save}>Save</Button>
        </Group>
      </Stack>
    </Modal>
  );
}
