// Maps a program/sprint status to its hue. Warm (amber) = wants a human;
// cool (teal/blue/green/slate) = the machine working on its own.
const STATUS_VAR: Record<string, string> = {
  proposed: "var(--st-proposed)",
  approved: "var(--st-approved)",
  queued: "var(--st-queued)",
  executing: "var(--st-executing)",
  parked: "var(--st-parked)",
  done: "var(--st-done)",
  failed: "var(--signal)",
  paused: "var(--st-paused)",
  canceled: "var(--st-canceled)",
  active: "var(--st-approved)",
  closed: "var(--st-canceled)",
};

export function statusVar(status: string): string {
  return STATUS_VAR[status] ?? "var(--ink-muted)";
}

// The one status that needs a human decision.
export const NEEDS_HUMAN = "proposed";

export const SPRINT_STATE_ORDER = [
  "proposed", "approved", "queued", "executing", "parked", "done", "failed", "paused", "canceled",
];
