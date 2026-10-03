import { describe, it, expect, vi, beforeAll, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";

const page = {
  reports: [
    { id: "c2", title: "Catch-up 3 Oct", created_at: 200, since: 100, sprints: ["a", "b"],
      trigger: "schedule", by: "", busy: false, text: "**Bottom line.** Kernel wins.", followups: 2 },
    { id: "c1", title: "Catch-up 26 Sep", created_at: 100, since: 0, sprints: ["x"],
      trigger: "on demand", by: "oleg", busy: false, text: "Older report.", followups: 0 },
  ],
  schedule: { every_days: 7, min_sprints: 10, last_at: 200, since: 200, finished_since: 4,
              next_check_at: 800 },
};

vi.mock("../api", () => ({
  api: {
    getProgram: vi.fn().mockResolvedValue({ id: "p1", title: "P one" }),
    getCatchup: vi.fn(),
    startCatchup: vi.fn().mockResolvedValue({}),
    setCatchupSchedule: vi.fn().mockResolvedValue({}),
  },
}));

import { api } from "../api";
import CatchupView from "./CatchupView";

beforeAll(() => {
  window.matchMedia ??= (() => ({ matches: false, addListener() {}, removeListener() {},
    addEventListener() {}, removeEventListener() {} })) as unknown as typeof window.matchMedia;
  globalThis.ResizeObserver ??= class { observe() {} unobserve() {} disconnect() {} } as never;
});
beforeEach(() => {
  localStorage.clear();
  vi.mocked(api.getCatchup).mockResolvedValue(page as never);
});

const open = () => render(
  <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <MantineProvider><MemoryRouter initialEntries={["/programs/p1/catchup"]}>
      <Routes><Route path="/programs/:id/catchup" element={<CatchupView />} /></Routes>
    </MemoryRouter></MantineProvider>
  </QueryClientProvider>);

describe("CatchupView (I1)", () => {
  it("shows the newest report open, older ones folded, each with its chat", async () => {
    open();
    expect(await screen.findByText(/Kernel wins/)).toBeTruthy();
    expect(screen.queryByText("Older report.")).toBeNull();
    expect(screen.getAllByText("new")).toHaveLength(2);
    const links = screen.getAllByRole("link", { name: /Continue in chat/ });
    expect(links[0].getAttribute("href")).toBe("/programs/p1/chat?c=c2");
    expect(links[0].textContent).toContain("2 follow-ups");
    fireEvent.click(screen.getByRole("button", { name: "Read" }));
    expect(screen.getByText("Older report.")).toBeTruthy();
    expect(JSON.parse(localStorage.getItem("coscience:catchup-seen")!)).toEqual(["c2", "c1"]);
  });

  it("writes one now from the last report by default", async () => {
    open();
    fireEvent.click(await screen.findByRole("button", { name: "Write one now" }));
    await waitFor(() => expect(api.startCatchup).toHaveBeenCalledWith("p1", undefined));
  });

  it("saves a changed schedule", async () => {
    open();
    const days = await screen.findByLabelText("days between reports");
    fireEvent.change(days, { target: { value: "14" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.setCatchupSchedule).toHaveBeenCalledWith("p1", 14, 10));
  });
});
