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

function renderModal(capacity = { cpu: 16, workers: 1 }, used = { cpu: 2, workers: 1 }) {
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
});
