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

/** The disk line in the rail's pulse, or null while every machine has room. The pulse
 *  is a place for what needs attention, not a gauge: a healthy pool says nothing, and
 *  the figures live on Compute for anyone who wants to look. */
export interface DiskPulse {
  mark: string;      // ⛔ / ⚠
  color: string;
  text: string;      // the headline: the machine in most trouble
  title: string;     // every machine that reported, on hover
}

interface HostLike { name: string; label?: string | null; free_gb?: number | null; disk?: string }

const amount = (gb: number) =>
  gb < 1 ? `${Math.round(gb * 1024)} MB` : `${gb.toFixed(gb < 10 ? 1 : 0)} GB`;

export function diskPulse(hosts: HostLike[]): DiskPulse | null {
  const known = hosts.filter((h) => typeof h.free_gb === "number");
  const critical = known.filter((h) => h.disk === "critical").length;
  const low = known.filter((h) => h.disk === "low").length;
  if (!critical && !low) return null;          // nothing to say while every machine has room

  const named = (h: HostLike) => (h.label || "").trim() || h.name;
  const sorted = [...known].sort((a, b) => (a.free_gb as number) - (b.free_gb as number));
  const worst = sorted[0];
  // The count of machines in trouble, not just the worst one: two servers filling up
  // is a different situation from one, and the headline would hide the second.
  const more = critical + low > 1 ? ` · ${critical + low} machines` : "";
  const title = sorted.map((h) => `${named(h)}: ${amount(h.free_gb as number)} free`).join("\n");

  if (critical) {
    return { mark: "⛔", color: "var(--st-failed)",
             text: `${named(worst)} out of space${more}`, title };
  }
  return { mark: "⚠", color: "var(--st-queued)",
           text: `${named(worst)} low on disk${more}`, title };
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
