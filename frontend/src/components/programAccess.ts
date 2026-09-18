import type { CutOff, LedgerHost } from "../api";

/** Whether a server takes work for `program`. `null` (never set) admits everything. */
export const hostAllows = (h: Pick<LedgerHost, "programs">, program: string) =>
  h.programs === null || h.programs.includes(program);

/** A server's program list, in words: "all" when never set, "none" when set
 *  empty, else the ids it runs. */
export const accessLabel = (h: Pick<LedgerHost, "programs">) =>
  h.programs === null ? "all" : h.programs.length === 0 ? "none" : h.programs.join(", ");

/** What the server dialog shows ticked: the server's own list, or every
 *  program when it has never set one. */
export const programsForEdit = (h: Pick<LedgerHost, "programs"> | undefined, allIds: string[]) =>
  h?.programs ?? allIds;

export const cutOffMessage = (cut: CutOff[]) =>
  cut.map((c) => `${c.sprint_id} is pinned to ${c.host === "local" ? "this machine" : c.host}`).join("; ")
  + ". It keeps its work there and waits until the program is allowed back on that server or the sprint is stopped.";
