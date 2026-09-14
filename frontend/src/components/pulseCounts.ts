/** The rail's pulse counts, kept out of the component so they can be tested alone.
 *
 *  Only active programs count toward the sprint numbers: a paused program's queue
 *  is not work anyone is waiting on. */
export interface PulseCounts {
  active: number;        // active programs
  running: number;       // sprints executing
  awaitingYou: number;   // proposed, needing a human decision
  waiting: number;       // approved or queued: cleared to run, not started yet
  cantStart: number;     // approved or queued, but asking for more than the pool's total
}

interface ProgramLike { id: string; status: string }
interface SprintLike { id: string; status: string; program: string | null; unrunnable?: string }

export function pulseCounts(programs: ProgramLike[], sprints: SprintLike[]): PulseCounts {
  const statusOf: Record<string, string> = {};
  for (const p of programs) statusOf[p.id] = p.status;
  const programOf = (s: SprintLike) =>
    s.program ?? (s.id.includes("-") ? s.id.slice(0, s.id.indexOf("-")) : s.id);
  const live = sprints.filter((s) => (statusOf[programOf(s)] ?? "active") === "active");
  const cleared = live.filter((s) => s.status === "approved" || s.status === "queued");
  const cantStart = cleared.filter((s) => !!s.unrunnable).length;
  return {
    active: programs.filter((p) => p.status === "active").length,
    running: sprints.filter((s) => s.status === "executing").length,
    awaitingYou: live.filter((s) => s.status === "proposed").length,
    waiting: cleared.length - cantStart,
    cantStart,
  };
}
