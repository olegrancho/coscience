import type { StatusActor } from "./api";

const KEY = "coscience:sprint-seen";
// Which programs this browser has opened before. Without it, "a sprint I have never
// seen" cannot be told apart from "every sprint, because I have never opened this
// program" — and the second must not light up the whole board.
const PROGRAM_KEY = "coscience:program-seen";

function load(): Record<string, number> {
  try { return JSON.parse(localStorage.getItem(KEY) || "{}"); }
  catch { return {}; }
}

function save(data: Record<string, number>) {
  try { localStorage.setItem(KEY, JSON.stringify(data)); } catch { /* private window */ }
}

function loadPrograms(): Record<string, number> {
  try { return JSON.parse(localStorage.getItem(PROGRAM_KEY) || "{}"); }
  catch { return {}; }
}

function now() { return Date.now() / 1000; }

/** True when the sprint was moved by the platform after the user last viewed it, or
 *  is one the platform brought them since they last looked at this program.
 *
 *  The highlight is for work the viewer did not ask for — the PM proposing a sprint or
 *  releasing one, a worker finishing or failing one, an agent escalating. A transition
 *  the human made themselves (propose, approve, park, reject, restore) never
 *  highlights: announcing someone's own click back at them is noise, and it drowns out
 *  the changes that matter.
 *
 *  A sprint with no "seen" record at all is new since the last visit to its program,
 *  which is the case a newly proposed sprint arrives in — the whole reason it deserves
 *  the highlight. On a program's FIRST visit `seedIfNew` records every sprint as seen,
 *  so that never means "all of them".
 *
 *  An actor of undefined is a backend that predates the field; treat it as the
 *  platform, which is how every sprint behaved before this. */
export function isUnseen(sprintId: string, lastStatusAt: number | null,
                         actor?: StatusActor): boolean {
  if (!lastStatusAt) return false;
  if (actor === "human") return false;
  const seenAt = load()[sprintId];
  if (seenAt === undefined) return true;
  return lastStatusAt > seenAt;
}

/** Record that the user just viewed this sprint. */
export function markSeen(sprintId: string) {
  const seen = load();
  seen[sprintId] = now();
  save(seen);
}

/** On the FIRST visit to a program, record every sprint it has as already seen, so an
 *  established board does not arrive as a wall of highlights. On every later visit this
 *  does nothing: a sprint with no record is then genuinely new since the last look, and
 *  `isUnseen` is meant to catch it. */
export function seedIfNew(sprints: { id: string }[], programId?: string) {
  const programs = loadPrograms();
  const key = programId ?? "";
  if (key && key in programs) return;          // seen this program before: nothing to seed

  const seen = load();
  const t = now();
  for (const s of sprints) {
    if (!(s.id in seen)) seen[s.id] = t;
  }
  save(seen);
  if (key) {
    programs[key] = t;
    try { localStorage.setItem(PROGRAM_KEY, JSON.stringify(programs)); }
    catch { /* private window */ }
  }
}
