import { Button, Card, Group, Modal, NumberInput, Radio, Stack, Text } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, type ApprovalGrant } from "../api";
import { RelTime } from "./ui";

type Limit = "sprints" | "until" | "window5h" | "week";

/** The dialog that lends the planner approval authority (M2): pick one limit, grant. */
export function SuperchargeModal({ programId, opened, onClose, onDone }:
  { programId: string; opened: boolean; onClose: () => void; onDone: () => void }) {
  const [limit, setLimit] = useState<Limit>("sprints");
  const [sprints, setSprints] = useState<number | string>(5);
  const [hours, setHours] = useState<number | string>(8);
  const [busy, setBusy] = useState(false);
  const grant = async () => {
    setBusy(true);
    try {
      await api.grantApproval(programId, {
        limit,
        ...(limit === "sprints" ? { sprints: Number(sprints) } : {}),
        ...(limit === "until" ? { until: Date.now() / 1000 + Number(hours) * 3600 } : {}),
      });
      onDone();
      onClose();
    } catch (e) {
      notifications.show({ color: "red", title: "Couldn't grant", message: String(e) });
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal opened={opened} onClose={onClose} title="Supercharge: let the planner approve" size="md">
      <Stack gap="md">
        <Text size="sm" c="dimmed">
          Until the limit you choose, the planner approves proposed sprints in this program
          itself — any proposal, yours included — and releases them as it sees fit. The
          platform enforces the limit; you can revoke it at any time, and when it ends the
          program page says so.
        </Text>
        <Radio.Group value={limit} onChange={(v) => setLimit(v as Limit)} aria-label="grant limit">
          <Stack gap={10}>
            <Group gap={8} wrap="nowrap">
              <Radio value="sprints" label="For the next" />
              <NumberInput size="xs" w={70} min={1} value={sprints} onChange={setSprints}
                           disabled={limit !== "sprints"} aria-label="number of approvals" />
              <Text size="sm">approvals</Text>
            </Group>
            <Group gap={8} wrap="nowrap">
              <Radio value="until" label="For the next" />
              <NumberInput size="xs" w={70} min={1} value={hours} onChange={setHours}
                           disabled={limit !== "until"} aria-label="hours" />
              <Text size="sm">hours</Text>
            </Group>
            <Radio value="window5h" label="Until the current 5-hour usage window resets or runs low" />
            <Radio value="week" label="Until the current weekly usage window resets or runs low" />
          </Stack>
        </Radio.Group>
        <Group justify="flex-end" gap={8}>
          <Button size="xs" variant="default" onClick={onClose}>Cancel</Button>
          <Button size="xs" color="signal" loading={busy} onClick={grant}>Grant</Button>
        </Group>
      </Stack>
    </Modal>
  );
}

function Approved({ ids }: { ids: string[] }) {
  if (!ids.length) return <>It has approved nothing yet.</>;
  return (
    <>It has approved {ids.length}:{" "}
      {ids.map((s, i) => (
        <span key={s}>{i ? ", " : ""}<Link to={`/sprints/${s}`} className="mono">{s}</Link></span>
      ))}.</>
  );
}

/** The program page's notice of a grant (M2): unmissable while live, because a program
 *  approving its own work is the one state where a glance must not be ambiguous; and
 *  still there after it ends, saying why, until dismissed. */
export function GrantBanner({ programId, grant, onChange }:
  { programId: string; grant: ApprovalGrant; onChange: () => void }) {
  const act = async (fn: () => Promise<unknown>, fail: string) => {
    try { await fn(); onChange(); }
    catch (e) { notifications.show({ color: "red", title: fail, message: String(e) }); }
  };
  const live = grant.live;
  return (
    <Card padding="md" radius="md" data-testid="grant-banner" style={{
      border: `2px solid ${live ? "var(--signal)" : "var(--hairline)"}`,
      background: live ? "var(--signal-weak)" : undefined,
    }}>
      <Group justify="space-between" align="flex-start" wrap="nowrap" gap="md">
        <div>
          <Text size="sm" fw={600}>
            {live ? "⚡ The planner is approving sprints on its own"
                  : "The planner's approval grant has ended"}
          </Text>
          <Text size="sm" mt={4}>
            {live
              ? <>Granted by {grant.by || "someone"} <RelTime at={grant.at} />, {grant.remaining}. </>
              : <>It ended <RelTime at={grant.ended_at} />: {grant.end_reason}. </>}
            <Approved ids={grant.approved} />
          </Text>
        </div>
        {live
          ? <Button size="xs" variant="white" color="signal"
                    onClick={() => act(() => api.revokeApproval(programId), "Couldn't revoke")}>Revoke</Button>
          : <Button size="xs" variant="subtle" color="gray"
                    onClick={() => act(() => api.dismissGrantNotice(programId), "Couldn't dismiss")}>Dismiss</Button>}
      </Group>
    </Card>
  );
}
