import { describe, it, expect, vi, beforeAll } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { MemoryRouter } from "react-router-dom";

vi.mock("../api", () => ({
  api: {
    grantApproval: vi.fn().mockResolvedValue({ grant: null }),
    revokeApproval: vi.fn().mockResolvedValue({ grant: null }),
    dismissGrantNotice: vi.fn().mockResolvedValue({ ok: true }),
  },
}));

import { api, type ApprovalGrant } from "../api";
import { GrantBanner, SuperchargeModal } from "./Supercharge";

beforeAll(() => {
  window.matchMedia ??= (() => ({ matches: false, addListener() {}, removeListener() {},
    addEventListener() {}, removeEventListener() {} })) as unknown as typeof window.matchMedia;
  globalThis.ResizeObserver ??= class { observe() {} unobserve() {} disconnect() {} } as never;
});

const wrap = (ui: React.ReactNode) =>
  render(<MantineProvider><MemoryRouter>{ui}</MemoryRouter></MantineProvider>);

const live: ApprovalGrant = { id: "g1", by: "oleg", at: 1, limit: "sprints", sprints: 5,
  approved: ["p1-c3-x"], ended_at: null, end_reason: "", live: true,
  remaining: "4 of 5 approvals left" };

describe("Supercharge (M2)", () => {
  it("grants a number of approvals, or hours from now", async () => {
    const done = vi.fn();
    wrap(<SuperchargeModal programId="p1" opened onClose={() => {}} onDone={done} />);
    fireEvent.click(screen.getByRole("button", { name: "Grant" }));
    await waitFor(() => expect(api.grantApproval).toHaveBeenCalledWith("p1", { limit: "sprints", sprints: 5 }));
    expect(done).toHaveBeenCalled();
    fireEvent.click(screen.getAllByRole("radio")[1]);
    fireEvent.click(screen.getByRole("button", { name: "Grant" }));
    await waitFor(() => expect(vi.mocked(api.grantApproval).mock.calls[1][1]).toMatchObject({ limit: "until" }));
    const until = (vi.mocked(api.grantApproval).mock.calls[1][1] as { until: number }).until;
    expect(Math.round(until - Date.now() / 1000)).toBe(8 * 3600);
  });

  it("shows a live grant with what is left, its approvals, and Revoke", async () => {
    const change = vi.fn();
    wrap(<GrantBanner programId="p1" grant={live} onChange={change} />);
    expect(screen.getByText(/approving sprints on its own/)).toBeTruthy();
    expect(screen.getByText(/4 of 5 approvals left/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "p1-c3-x" }).getAttribute("href")).toBe("/sprints/p1-c3-x");
    fireEvent.click(screen.getByRole("button", { name: "Revoke" }));
    await waitFor(() => expect(api.revokeApproval).toHaveBeenCalledWith("p1"));
    expect(change).toHaveBeenCalled();
  });

  it("says why an ended grant ended, until dismissed", async () => {
    wrap(<GrantBanner programId="p1" onChange={() => {}}
                      grant={{ ...live, live: false, ended_at: 2, end_reason: "its deadline passed" }} />);
    expect(screen.getByText(/has ended/)).toBeTruthy();
    expect(screen.getByText(/its deadline passed/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    await waitFor(() => expect(api.dismissGrantNotice).toHaveBeenCalledWith("p1"));
  });
});
