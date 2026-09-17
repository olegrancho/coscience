import type { CutOff, LedgerHost } from "../api";

/** The dialog's view of a server's access. `list` is the exceptions when `all`, the
 *  allowed programs otherwise. */
export interface Access { all: boolean; list: string[] }

export const accessFromHost = (h?: Pick<LedgerHost, "programs" | "exclude_programs">): Access =>
  h?.programs?.length ? { all: false, list: [...h.programs] }
    : { all: true, list: [...(h?.exclude_programs ?? [])] };

export const accessPayload = (a: Access) =>
  a.all ? { programs: [], exclude_programs: a.list } : { programs: a.list, exclude_programs: [] };

/** "Only these programs" with none picked would read as every program server-side. */
export const accessInvalid = (a: Access) => !a.all && a.list.length === 0;

export const sameAccess = (a: Access, b: Access) =>
  a.all === b.all && [...a.list].sort().join("\n") === [...b.list].sort().join("\n");

export const accessLabel = (h: Pick<LedgerHost, "programs" | "exclude_programs">) =>
  h.programs.length ? h.programs.join(", ")
    : h.exclude_programs?.length ? `all except ${h.exclude_programs.join(", ")}` : "all";

export const hostAllows = (h: Pick<LedgerHost, "programs" | "exclude_programs">, program: string) =>
  h.programs.length ? h.programs.includes(program) : !(h.exclude_programs ?? []).includes(program);

/** The one program an "only these" server takes — unchecking it would be refused. */
export const onlyProgram = (h: Pick<LedgerHost, "programs">, program: string) =>
  h.programs.length === 1 && h.programs[0] === program;

export const cutOffMessage = (cut: CutOff[]) =>
  cut.map((c) => `${c.sprint_id} is pinned to ${c.host === "local" ? "this machine" : c.host}`).join("; ")
  + ". It keeps its work there and waits until the program is allowed back on that server or the sprint is stopped.";
