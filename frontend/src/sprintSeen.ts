const KEY = "coscience:sprint-seen";

function load(): Record<string, number> {
  try { return JSON.parse(localStorage.getItem(KEY) || "{}"); }
  catch { return {}; }
}

function save(data: Record<string, number>) {
  localStorage.setItem(KEY, JSON.stringify(data));
}

function now() { return Date.now() / 1000; }

/** True when the sprint's status changed after the user last viewed it. */
export function isUnseen(sprintId: string, lastStatusAt: number | null): boolean {
  if (!lastStatusAt) return false;
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
