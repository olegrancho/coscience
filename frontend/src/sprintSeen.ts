import type { StatusActor } from "./api";

const KEY = "coscience:sprint-seen";

function load(): Record<string, number> {
  try { return JSON.parse(localStorage.getItem(KEY) || "{}"); }
  catch { return {}; }
}

function save(data: Record<string, number>) {
  localStorage.setItem(KEY, JSON.stringify(data));
}

function now() { return Date.now() / 1000; }

/** True when the sprint was moved by the platform after the user last viewed it.
 *
 *  The highlight is for work the viewer did not ask for — the PM releasing a sprint,
 *  a worker finishing or failing one, an agent escalating. A transition the human made
 *  themselves (approve, park, reject, restore) never highlights: announcing someone's
 *  own click back at them is noise, and it drowns out the changes that matter.
 *
 *  An actor of undefined is a backend that predates the field; treat it as the
 *  platform, which is how every sprint behaved before this. */
export function isUnseen(sprintId: string, lastStatusAt: number | null,
                         actor?: StatusActor): boolean {
  if (!lastStatusAt) return false;
  if (actor === "human") return false;
  const seenAt = load()[sprintId];
  if (seenAt === undefined) return false;
  return lastStatusAt > seenAt;
}

/** Record that the user just viewed this sprint. */
export function markSeen(sprintId: string) {
  const seen = load();
  seen[sprintId] = now();
  save(seen);
}

/** Seed tracking for sprints not yet in localStorage (first encounter).
 *  Sets their "seen" time to now so they don't highlight on the very first visit. */
export function seedIfNew(sprints: { id: string }[]) {
  const seen = load();
  let changed = false;
  const t = now();
  for (const s of sprints) {
    if (!(s.id in seen)) {
      seen[s.id] = t;
      changed = true;
    }
  }
  if (changed) save(seen);
}
