import { describe, it, expect, vi, beforeAll, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MantineProvider } from "@mantine/core";

vi.mock("../api", () => ({
  api: { setCapacity: vi.fn().mockResolvedValue({ capacity: {}, used: {}, available: {}, leases: [] }) },
}));

import { api } from "../api";
import CapacityModal from "./CapacityModal";

beforeAll(() => {
  window.matchMedia = window.matchMedia || (((query: string) => ({
    matches: false, media: query, onchange: null,
    addListener() {}, removeListener() {},
    addEventListener() {}, removeEventListener() {}, dispatchEvent() { return false; },
  })) as unknown as typeof window.matchMedia);
});

// Explicit Record type (rather than letting TS infer from the defaults) so a
// partial capacity/used map — e.g. a pool missing the "workers" key — type-checks.
function renderModal(capacity: Record<string, number> = { cpu: 16, workers: 1 }, used: Record<string, number> = { cpu: 2, workers: 1 }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MantineProvider>
      <QueryClientProvider client={qc}>
        <CapacityModal opened onClose={() => {}} capacity={capacity} used={used} />
      </QueryClientProvider>
    </MantineProvider>,
  );
}

describe("CapacityModal", () => {
  // The mocked api.setCapacity is a module-level spy shared across every `it`
  // below; without a reset, calls from an earlier test leak into a later
  // `.not.toHaveBeenCalled()` assertion.
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows a row per resource, pre-filled with current capacity", () => {
    renderModal();
    expect((screen.getByLabelText("cpu capacity") as HTMLInputElement).value).toBe("16");
    expect((screen.getByLabelText("workers capacity") as HTMLInputElement).value).toBe("1");
  });

  it("saves the edited map", async () => {
    renderModal();
    fireEvent.change(screen.getByLabelText("workers capacity"), { target: { value: "3" } });
    fireEvent.click(screen.getByRole("button", { name: /save/i }));
    await waitFor(() => expect(api.setCapacity).toHaveBeenCalledWith({ cpu: 16, workers: 3 }));
  });

  it("removes a resource", async () => {
    renderModal();
    fireEvent.click(screen.getByLabelText("remove cpu"));
    fireEvent.click(screen.getByRole("button", { name: /save/i }));
    await waitFor(() => expect(api.setCapacity).toHaveBeenCalledWith({ workers: 1 }));
  });

  it("adds a resource", async () => {
    renderModal();
    fireEvent.click(screen.getByRole("button", { name: /add resource/i }));
    fireEvent.change(screen.getByLabelText("new resource name"), { target: { value: "gpu" } });
    fireEvent.change(screen.getByLabelText("new resource capacity"), { target: { value: "1" } });
    fireEvent.click(screen.getByRole("button", { name: /save/i }));
    await waitFor(() => expect(api.setCapacity).toHaveBeenCalledWith({ cpu: 16, workers: 1, gpu: 1 }));
  });

  it("refuses a negative value without calling the API", async () => {
    renderModal();
    fireEvent.change(screen.getByLabelText("cpu capacity"), { target: { value: "-2" } });
    fireEvent.click(screen.getByRole("button", { name: /save/i }));
    await waitFor(() => expect(screen.getByText(/zero or more/i)).toBeTruthy());
    expect(api.setCapacity).not.toHaveBeenCalled();
  });

  it("warns when a new limit is below what is in use", () => {
    renderModal({ cpu: 16 }, { cpu: 14 });
    fireEvent.change(screen.getByLabelText("cpu capacity"), { target: { value: "4" } });
    expect(screen.getByText(/14 cpu in use/i)).toBeTruthy();
    expect(screen.getByText(/running work finish/i)).toBeTruthy();
  });

  it("cannot fire Save twice from a fast double-click", async () => {
    // api.setCapacity resolves only when we say so, so both clicks land while
    // the first call is still in flight — exactly the window a real double-click
    // (or an impatient double-tap) races through.
    let resolveSave: ((v: unknown) => void) | undefined;
    (api.setCapacity as ReturnType<typeof vi.fn>).mockImplementation(
      () => new Promise((resolve) => { resolveSave = resolve; }),
    );
    renderModal();
    const saveButton = screen.getByRole("button", { name: /save/i }) as HTMLButtonElement;

    fireEvent.click(saveButton);
    expect(saveButton.disabled).toBe(true);
    fireEvent.click(saveButton);

    expect(api.setCapacity).toHaveBeenCalledTimes(1);

    resolveSave?.({ capacity: {}, used: {}, available: {}, leases: [] });
    await waitFor(() => expect(saveButton.disabled).toBe(false));
    expect(api.setCapacity).toHaveBeenCalledTimes(1);
  });

  it("keeps an in-progress edit when the ledger poll hands back a same-valued capacity object", () => {
    // main.tsx polls ["ledger"] every ~10s while the modal is open (Task 7 mounts
    // it over that page), which hands the modal a new `capacity` object on every
    // tick even when nothing changed server-side. Re-seeding on that reference
    // change — instead of only on open — would silently wipe this edit.
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { rerender } = render(
      <MantineProvider>
        <QueryClientProvider client={qc}>
          <CapacityModal opened onClose={() => {}} capacity={{ cpu: 16, workers: 1 }} used={{ cpu: 2, workers: 1 }} />
        </QueryClientProvider>
      </MantineProvider>,
    );

    fireEvent.change(screen.getByLabelText("cpu capacity"), { target: { value: "20" } });

    // A fresh object literal with the same values simulates the poll's new reference.
    rerender(
      <MantineProvider>
        <QueryClientProvider client={qc}>
          <CapacityModal opened onClose={() => {}} capacity={{ cpu: 16, workers: 1 }} used={{ cpu: 2, workers: 1 }} />
        </QueryClientProvider>
      </MantineProvider>,
    );

    expect((screen.getByLabelText("cpu capacity") as HTMLInputElement).value).toBe("20");
  });
});
