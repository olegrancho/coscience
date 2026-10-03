/** Which catch-up reports this browser has opened (I1), so a new one is marked until
 *  it is read. Per browser, like the experiments list's highlights. */
const KEY = "coscience:catchup-seen";

function load(): string[] {
  try { return JSON.parse(localStorage.getItem(KEY) || "[]"); } catch { return []; }
}

export function isReportSeen(id: string): boolean {
  return load().includes(id);
}

export function markReportSeen(id: string) {
  const seen = load();
  if (seen.includes(id)) return;
  try { localStorage.setItem(KEY, JSON.stringify([...seen, id].slice(-200))); }
  catch { /* private window */ }
}
