/** Coming back to a program lands on the experiment you left it for (P5).
 *
 *  An experiment's page records itself against its program when opened; the program
 *  page takes that record — once — when it is reached by going back, and brings the
 *  row into view. Per tab (sessionStorage), because "where was I" belongs to one
 *  reading session, and cleared on use so a later visit from the nav starts at the top
 *  as it always did. */

const key = (programId: string) => `coscience:return-row:${programId}`;

export function rememberOpened(programId: string, sprintId: string): void {
  try { sessionStorage.setItem(key(programId), sprintId); } catch { /* storage off */ }
}

export function takeReturnRow(programId: string): string | null {
  try {
    const id = sessionStorage.getItem(key(programId));
    sessionStorage.removeItem(key(programId));
    return id;
  } catch { return null; }
}
