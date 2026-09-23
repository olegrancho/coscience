/** Which of a program's experiments its list shows, and which it folds away.
 *
 *  Pulled out of ProgramDetail so the rules can be tested without rendering a page.
 *  The status filter and "only new" are the viewer's; the cap is the platform's own
 *  tidiness, so it is the one that yields (P7): it folds the noisy terminal statuses
 *  down to their most recent few, but never folds away a row the viewer has not seen
 *  yet — a highlight the count announces and the list hides is worse than none. */

export const CAPPED = new Set(["done", "canceled"]);
export const CAP = 3;

export interface ExperimentRowIn {
  id: string;
  status: string;
  last_status_at?: number | null;
}

export interface ListChoice<S extends ExperimentRowIn = ExperimentRowIn> {
  statusFilter: string;          // "all" or one status
  showAll: boolean;              // lift the cap
  onlyNew: boolean;              // P6: just the highlighted rows
  isNew: (s: S) => boolean;
  keep?: Set<string>;            // rows that must stay in view, e.g. the one just returned from (P5)
}

export function experimentRows<S extends ExperimentRowIn>(sprints: S[], c: ListChoice<S>) {
  const byStatus = (c.statusFilter === "all"
    ? sprints : sprints.filter((s) => s.status === c.statusFilter))
    .slice().sort((a, b) => (b.last_status_at ?? 0) - (a.last_status_at ?? 0));
  const newCount = byStatus.filter(c.isNew).length;
  // "Only new" is its own answer to "what should I look at", so the cap has nothing
  // to add there: every row it shows is exempt anyway.
  const base = c.onlyNew ? byStatus.filter(c.isNew) : byStatus;

  const hidden = new Set<string>();
  if (!c.showAll && !c.onlyNew) {
    const counted: Record<string, number> = {};
    for (const s of base) {
      if (!CAPPED.has(s.status)) continue;
      // New rows still count toward the cap — a burst of four overnight finishes shows
      // all four and folds the older ones — they are just never the ones folded.
      counted[s.status] = (counted[s.status] ?? 0) + 1;
      if (counted[s.status] > CAP && !c.isNew(s) && !c.keep?.has(s.id)) hidden.add(s.id);
    }
  }
  return { shown: base.filter((s) => !hidden.has(s.id)), hidden, newCount };
}
