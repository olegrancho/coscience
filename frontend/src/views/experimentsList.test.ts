import { describe, it, expect } from "vitest";
import { experimentRows } from "./experimentsList";

// Newest first by last_status_at; `n` marks what this browser has not seen.
const row = (id: string, status: string, at: number, n = false) =>
  ({ id, status, last_status_at: at, n });
type R = ReturnType<typeof row>;
const base = { statusFilter: "all", showAll: false, onlyNew: false, isNew: (s: R) => s.n };
const ids = (rs: R[]) => rs.map((r) => r.id);

describe("the cap on done and canceled", () => {
  const five = [row("d1", "done", 5), row("d2", "done", 4), row("d3", "done", 3),
                row("d4", "done", 2), row("d5", "done", 1), row("q", "queued", 0)];

  it("keeps the three most recent of each and every active row", () => {
    const { shown, hidden } = experimentRows(five, base);
    expect(ids(shown)).toEqual(["d1", "d2", "d3", "q"]);
    expect([...hidden]).toEqual(["d4", "d5"]);
  });

  it("never folds away a row the viewer has not seen (P7)", () => {
    // Four finished overnight: the fourth is still unseen and must stay in view,
    // while the older, seen ones fold behind it.
    const burst = [row("n1", "done", 9, true), row("n2", "done", 8, true),
                   row("n3", "done", 7, true), row("n4", "done", 6, true), ...five];
    expect(ids(experimentRows(burst, base).shown)).toEqual(["n1", "n2", "n3", "n4", "q"]);
  });

  it("keeps an unseen row even when it is the oldest", () => {
    const old = [...five.slice(0, 4), row("d5", "done", 1, true)];
    expect(ids(experimentRows(old, base).shown)).toEqual(["d1", "d2", "d3", "d5"]);
  });

  it("keeps the row being returned to (P5)", () => {
    expect(ids(experimentRows(five, { ...base, keep: new Set(["d5"]) }).shown))
      .toEqual(["d1", "d2", "d3", "d5", "q"]);
  });

  it("lifts entirely with show all", () => {
    expect(experimentRows(five, { ...base, showAll: true }).shown).toHaveLength(6);
  });
});

describe("only new (P6)", () => {
  const rows = [row("a", "proposed", 5, true), row("b", "proposed", 4),
                row("c", "done", 3, true), row("d", "done", 2)];

  it("shows just the unseen rows, and counts them", () => {
    const r = experimentRows(rows, { ...base, onlyNew: true });
    expect(ids(r.shown)).toEqual(["a", "c"]);
    expect(r.newCount).toBe(2);
  });

  it("composes with the status filter, and the count follows it", () => {
    const r = experimentRows(rows, { ...base, onlyNew: true, statusFilter: "done" });
    expect(ids(r.shown)).toEqual(["c"]);
    expect(r.newCount).toBe(1);
  });

  it("shows nothing, not everything, when nothing is new", () => {
    const seen = rows.map((r) => ({ ...r, n: false }));
    const r = experimentRows(seen, { ...base, onlyNew: true });
    expect(r.shown).toEqual([]);
    expect(r.newCount).toBe(0);
  });
});
