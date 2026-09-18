import { describe, it, expect, vi, beforeAll } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { MantineProvider } from "@mantine/core";

const navigate = vi.fn();
vi.mock("react-router-dom", async (orig) => ({
  ...(await orig<typeof import("react-router-dom")>()),
  useNavigate: () => navigate,
}));

vi.mock("../api", () => ({
  api: {
    createProgram: vi.fn().mockResolvedValue({ id: "p7" }),
    listDirs: vi.fn().mockResolvedValue({ path: null, parent: null, roots: [], entries: [] }),
    createDir: vi.fn(),
    getLedger: vi.fn().mockResolvedValue({
      capacity: {}, used: {}, available: {}, leases: [], paused: false,
      hosts: [
        { name: "local", ssh: "", placeable: true, programs: null, run_root: "",
          capacity: {}, available: {}, gpus: [] },
        { name: "gpu1", ssh: "gpu1", placeable: true, programs: null, run_root: "~/runs",
          capacity: {}, available: {}, gpus: [] },
      ],
    }),
  },
}));

import { api } from "../api";
import NewProgramModal from "./NewProgramModal";

// jsdom has no matchMedia; MantineProvider's color-scheme effect needs it.
beforeAll(() => {
  window.matchMedia = window.matchMedia || (((query: string) => ({
    matches: false, media: query, onchange: null,
    addListener() {}, removeListener() {},
    addEventListener() {}, removeEventListener() {}, dispatchEvent() { return false; },
  })) as unknown as typeof window.matchMedia);
});

function renderModal() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MantineProvider>
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <NewProgramModal opened onClose={() => {}} />
        </MemoryRouter>
      </QueryClientProvider>
    </MantineProvider>,
  );
}

describe("NewProgramModal", () => {
  it("blocks submit and shows an error when title or goals is blank", async () => {
    renderModal();
    fireEvent.click(screen.getByRole("button", { name: /create program/i }));
    await waitFor(() => expect(screen.getByText(/required/i)).toBeTruthy());
    expect(api.createProgram).not.toHaveBeenCalled();
  });

  it("calls api.createProgram with the entered values and every server ticked by default", async () => {
    renderModal();
    fireEvent.change(screen.getByLabelText("Title"), { target: { value: "  Aging  " } });
    fireEvent.change(screen.getByLabelText("Goals"), { target: { value: "Reverse it" } });
    expect((await screen.findByLabelText("may run on local") as HTMLInputElement).checked).toBe(true);
    expect((screen.getByLabelText("may run on gpu1") as HTMLInputElement).checked).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: /create program/i }));
    await waitFor(() =>
      expect(api.createProgram).toHaveBeenCalledWith({
        title: "Aging", goals: "Reverse it", workdir: "", hosts: ["local", "gpu1"],
      }));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith("/programs/p7"));
  });

  it("sends the remaining servers once one is unticked", async () => {
    renderModal();
    fireEvent.change(screen.getByLabelText("Title"), { target: { value: "Aging" } });
    fireEvent.change(screen.getByLabelText("Goals"), { target: { value: "Reverse it" } });
    fireEvent.click(await screen.findByLabelText("may run on gpu1"));
    fireEvent.click(screen.getByRole("button", { name: /create program/i }));
    await waitFor(() =>
      expect(api.createProgram).toHaveBeenCalledWith({
        title: "Aging", goals: "Reverse it", workdir: "", hosts: ["local"],
      }));
  });

  it("shows the field even with only one server in the ledger", async () => {
    vi.mocked(api.getLedger).mockResolvedValueOnce({
      capacity: {}, used: {}, available: {}, leases: [], paused: false,
      hosts: [{ name: "local", ssh: "", placeable: true, programs: null, run_root: "",
                capacity: {}, available: {}, gpus: [] }],
    } as never);
    renderModal();
    expect((await screen.findByLabelText("may run on local") as HTMLInputElement).checked).toBe(true);
  });

  it("offers an in-field browse control under advanced", async () => {
    renderModal();
    fireEvent.click(screen.getByRole("button", { name: /\+ advanced/i }));
    await waitFor(() => expect(screen.getByLabelText("browse folders")).toBeTruthy());
  });

  // Fix round 1, Finding 2: `checkedHosts` seeds asynchronously once the ledger
  // resolves. A submit that beats that (or a ledger that never resolves at
  // all) must never send an explicit `hosts: []` — that means "no server",
  // stranding the new program — so `hosts` must be omitted entirely instead.
  describe("seeding race (fix round 1, Finding 2)", () => {
    it("omits hosts when Create is clicked before the ledger resolves", async () => {
      // This file has no per-test `clearAllMocks`, so earlier tests' calls are
      // still on the mock's history — clear it so `mock.calls[0]` below is
      // this test's own call, not a leftover from an earlier one.
      vi.clearAllMocks();
      let resolveLedger: (v: unknown) => void = () => {};
      vi.mocked(api.getLedger).mockImplementationOnce(
        () => new Promise((resolve) => { resolveLedger = resolve; }) as never,
      );
      renderModal();
      fireEvent.change(screen.getByLabelText("Title"), { target: { value: "Aging" } });
      fireEvent.change(screen.getByLabelText("Goals"), { target: { value: "Reverse it" } });
      fireEvent.click(screen.getByRole("button", { name: /create program/i }));
      await waitFor(() => expect(api.createProgram).toHaveBeenCalled());
      const [body] = vi.mocked(api.createProgram).mock.calls[0] as [Record<string, unknown>];
      expect(body).not.toHaveProperty("hosts");
      // Settle the ledger afterward so it doesn't leak an unresolved promise
      // (and an unwrapped `act` state update) into later tests.
      await act(async () => {
        resolveLedger({
          capacity: {}, used: {}, available: {}, leases: [], paused: false,
          hosts: [{ name: "local", ssh: "", placeable: true, programs: null, run_root: "",
                    capacity: {}, available: {}, gpus: [] }],
        });
      });
    });

    it("omits hosts once the ledger query rejects, never falling back to an empty list", async () => {
      vi.clearAllMocks();
      vi.mocked(api.getLedger).mockRejectedValueOnce(new Error("network error"));
      renderModal();
      fireEvent.change(screen.getByLabelText("Title"), { target: { value: "Aging" } });
      fireEvent.change(screen.getByLabelText("Goals"), { target: { value: "Reverse it" } });
      fireEvent.click(screen.getByRole("button", { name: /create program/i }));
      await waitFor(() => expect(api.createProgram).toHaveBeenCalled());
      const [body] = vi.mocked(api.createProgram).mock.calls[0] as [Record<string, unknown>];
      expect(body).not.toHaveProperty("hosts");
    });
  });
});
