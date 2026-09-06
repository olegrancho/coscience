import { describe, it, expect, vi, beforeAll, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MantineProvider } from "@mantine/core";

const callLog = vi.fn();
vi.mock("../api", () => ({ api: { getCallLog: () => callLog() } }));

import CallLog from "./CallLog";

/** Seconds-since-epoch for a local date, so tests read in the same timezone the
 *  date filter compares in. */
const at = (iso: string) => new Date(iso).getTime() / 1000;

const row = (over: Record<string, unknown> = {}) => ({
  id: Math.random().toString(36).slice(2),
  kind: "pm", program: "p2", sprint: "", model: "claude-opus-5",
  status: "ok", cost: 1, started_at: at("2026-09-05T10:00:00"),
  ended_at: at("2026-09-05T10:02:00"), duration: 120,
  limits_before: null, limits_after: null,
  ...over,
});

beforeAll(() => {
  window.matchMedia = window.matchMedia || (((query: string) => ({
    matches: false, media: query, onchange: null,
    addListener() {}, removeListener() {},
    addEventListener() {}, removeEventListener() {}, dispatchEvent() { return false; },
  })) as unknown as typeof window.matchMedia);
});

beforeEach(() => callLog.mockReset());

function renderIt() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MantineProvider>
      <QueryClientProvider client={qc}><CallLog /></QueryClientProvider>
    </MantineProvider>,
  );
}

/** Varies `sprint`, not `program`: program values also render as filter options,
 *  so a per-row program makes every lookup ambiguous. */
const many = (n: number) =>
  Array.from({ length: n }, (_, i) => row({ sprint: `s${i}` }));

describe("CallLog pagination", () => {
  it("shows only the first page when there are more calls than fit", async () => {
    callLog.mockResolvedValue({ calls: many(60) });
    renderIt();
    await waitFor(() => expect(screen.getByText("s0")).toBeTruthy());
    expect(screen.queryByText("s55")).toBeNull();
  });

  it("moves to the next page and back", async () => {
    callLog.mockResolvedValue({ calls: many(60) });
    renderIt();
    await waitFor(() => expect(screen.getByText("s0")).toBeTruthy());

    fireEvent.click(screen.getByRole("button", { name: /next/i }));
    await waitFor(() => expect(screen.getByText("s55")).toBeTruthy());
    expect(screen.queryByText("s0")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /previous/i }));
    await waitFor(() => expect(screen.getByText("s0")).toBeTruthy());
  });

  it("says which page you are on and how many there are", async () => {
    callLog.mockResolvedValue({ calls: many(60) });
    const { container } = renderIt();
    await waitFor(() => expect(container.textContent).toMatch(/page\s*1\s*(of|\/)\s*2/i));
  });

  it("hides the pager when everything fits on one page", async () => {
    callLog.mockResolvedValue({ calls: many(3) });
    renderIt();
    await waitFor(() => expect(screen.getByText("s0")).toBeTruthy());
    expect(screen.queryByRole("button", { name: /next/i })).toBeNull();
  });

  it("returns to page one when a filter changes", async () => {
    // Otherwise a narrowed result set leaves you stranded on a now-empty page.
    callLog.mockResolvedValue({ calls: [
      ...many(60),
      row({ kind: "wiki-ingest", sprint: "wonly" }),
    ] });
    renderIt();
    await waitFor(() => expect(screen.getByText("s0")).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: /next/i }));
    await waitFor(() => expect(screen.getByText("s55")).toBeTruthy());

    fireEvent.change(screen.getByLabelText(/filter by kind/i),
                     { target: { value: "wiki-ingest" } });
    await waitFor(() => expect(screen.getByText("wonly")).toBeTruthy());
  });
});

describe("CallLog date filter", () => {
  it("keeps only calls on or after the from-date", async () => {
    callLog.mockResolvedValue({ calls: [
      row({ sprint: "older", started_at: at("2026-09-01T10:00:00"),
            ended_at: at("2026-09-01T10:01:00") }),
      row({ sprint: "newer", started_at: at("2026-09-05T10:00:00"),
            ended_at: at("2026-09-05T10:01:00") }),
    ] });
    renderIt();
    await waitFor(() => expect(screen.getByText("older")).toBeTruthy());

    fireEvent.change(screen.getByLabelText(/from/i), { target: { value: "2026-09-03" } });
    await waitFor(() => expect(screen.queryByText("older")).toBeNull());
    expect(screen.getByText("newer")).toBeTruthy();
  });

  it("keeps only calls on or before the to-date, including that whole day", async () => {
    callLog.mockResolvedValue({ calls: [
      row({ sprint: "onthatday", started_at: at("2026-09-03T23:30:00"),
            ended_at: at("2026-09-03T23:31:00") }),
      row({ sprint: "later", started_at: at("2026-09-05T10:00:00"),
            ended_at: at("2026-09-05T10:01:00") }),
    ] });
    renderIt();
    await waitFor(() => expect(screen.getByText("later")).toBeTruthy());

    fireEvent.change(screen.getByLabelText(/to/i), { target: { value: "2026-09-03" } });
    await waitFor(() => expect(screen.queryByText("later")).toBeNull());
    // A to-date must include everything up to that day's end, not midnight.
    expect(screen.getByText("onthatday")).toBeTruthy();
  });

  it("dates a still-running call by when it started", async () => {
    callLog.mockResolvedValue({ calls: [
      row({ sprint: "live", status: "running", ended_at: null, duration: null,
            started_at: at("2026-09-05T10:00:00") }),
    ] });
    renderIt();
    await waitFor(() => expect(screen.getByText("live")).toBeTruthy());
    fireEvent.change(screen.getByLabelText(/from/i), { target: { value: "2026-09-05" } });
    await waitFor(() => expect(screen.getByText("live")).toBeTruthy());
  });

  it("the spend total reflects the filtered rows, not everything", async () => {
    callLog.mockResolvedValue({ calls: [
      row({ sprint: "older", cost: 5, started_at: at("2026-09-01T10:00:00"),
            ended_at: at("2026-09-01T10:01:00") }),
      row({ sprint: "newer", cost: 2, started_at: at("2026-09-05T10:00:00"),
            ended_at: at("2026-09-05T10:01:00") }),
    ] });
    const { container } = renderIt();
    await waitFor(() => expect(container.textContent).toContain("$7.00"));
    fireEvent.change(screen.getByLabelText(/from/i), { target: { value: "2026-09-03" } });
    await waitFor(() => expect(container.textContent).toContain("$2.00"));
  });
});
