/** The experiments list keeps its filters across a visit to an experiment (B2).
 *
 *  "Only new", the status filter and "show all" are per program and per tab
 *  (sessionStorage), like the return row: they belong to one reading session, so a
 *  fresh tab starts unfiltered. */

export type ListFilters = { statusFilter: string; onlyNew: boolean; showAll: boolean };

export const DEFAULT_FILTERS: ListFilters = { statusFilter: "all", onlyNew: false, showAll: false };

const key = (programId: string) => `coscience:list-filters:${programId}`;

export function loadFilters(programId: string): ListFilters {
  try {
    const raw = sessionStorage.getItem(key(programId));
    if (!raw) return DEFAULT_FILTERS;
    const v = JSON.parse(raw);
    return {
      statusFilter: typeof v.statusFilter === "string" ? v.statusFilter : "all",
      onlyNew: v.onlyNew === true,
      showAll: v.showAll === true,
    };
  } catch { return DEFAULT_FILTERS; }
}

export function saveFilters(programId: string, f: ListFilters): void {
  try { sessionStorage.setItem(key(programId), JSON.stringify(f)); } catch { /* storage off */ }
}
