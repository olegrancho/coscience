import { describe, it, expect, vi, beforeAll, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MantineProvider } from "@mantine/core";

vi.mock("../api", () => ({
  api: {
    setPlatformLimits: vi.fn().mockResolvedValue({ capacity: {}, used: {}, available: {}, leases: [] }),
    setCapacity: vi.fn(),
  },
}));

import { api } from "../api";
import PlatformLimitsModal from "./PlatformLimitsModal";

beforeAll(() => {
  window.matchMedia = window.matchMedia || (((query: string) => ({
    matches: false, media: query, onchange: null,
    addListener() {}, removeListener() {},
    addEventListener() {}, removeEventListener() {}, dispatchEvent() { return false; },
  })) as unknown as typeof window.matchMedia);
});

function renderModal(capacity: Record<string, number> = { cpu: 16, workers: 4, housekeepers: 2 },
                     used: Record<string, number> = { cpu: 2, workers: 1 }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(
    <MantineProvider>
      <QueryClientProvider client={qc}>
        <PlatformLimitsModal opened onClose={() => {}} capacity={capacity} used={used} />
      </QueryClientProvider>
    </MantineProvider>,
  );
  return { ...view, qc };
}

const field = (label: string) => screen.getByLabelText(new RegExp(label)) as HTMLInputElement;

describe("PlatformLimitsModal (G1)", () => {
  beforeEach(() => vi.clearAllMocks());

  it("offers exactly the two platform limits, filled in, and nothing of the machine's", () => {
    renderModal();
    expect(field("Worker agents at once").value).toBe("4");
    expect(field("Housekeeping agents at once").value).toBe("2");
    expect(screen.queryByLabelText(/cpu/i)).toBeNull();
    expect(screen.queryByText(/add resource/i)).toBeNull();     // no free-form names
  });

  it("saves both limits, and only them", async () => {
    renderModal();
    fireEvent.change(field("Worker agents at once"), { target: { value: "6" } });
    fireEvent.click(screen.getByText("Save"));
    await waitFor(() => expect(api.setPlatformLimits)
      .toHaveBeenCalledWith({ workers: 6, housekeepers: 2 }));
    expect(api.setCapacity).not.toHaveBeenCalled();
  });

  it("reads an empty field as no limit", async () => {
    renderModal({ cpu: 16 });
    expect(field("Worker agents at once").value).toBe("");
    fireEvent.click(screen.getByText("Save"));
    await waitFor(() => expect(api.setPlatformLimits)
      .toHaveBeenCalledWith({ workers: null, housekeepers: null }));
  });

  it("refuses a negative limit without calling the API", async () => {
    renderModal();
    fireEvent.change(field("Worker agents at once"), { target: { value: "-1" } });
    fireEvent.click(screen.getByText("Save"));
    expect(await screen.findByText(/must be zero or more/)).toBeTruthy();
    expect(api.setPlatformLimits).not.toHaveBeenCalled();
  });

  it("warns when a new limit is below what is running", () => {
    renderModal({ workers: 4 }, { workers: 3 });
    fireEvent.change(field("Worker agents at once"), { target: { value: "1" } });
    expect(screen.getByText(/3 running now/)).toBeTruthy();
  });

  it("cannot fire Save twice from a fast double-click", async () => {
    let release!: () => void;
    vi.mocked(api.setPlatformLimits).mockReturnValueOnce(
      new Promise((r) => { release = () => r({} as never); }));
    renderModal();
    const save = screen.getByText("Save");
    fireEvent.click(save);
    fireEvent.click(save);
    release();
    await waitFor(() => expect(api.setPlatformLimits).toHaveBeenCalledTimes(1));
  });

  it("keeps an in-progress edit when the ledger poll hands back a fresh capacity object", () => {
    const { rerender, qc } = renderModal();
    fireEvent.change(field("Worker agents at once"), { target: { value: "9" } });
    rerender(
      <MantineProvider>
        <QueryClientProvider client={qc}>
          <PlatformLimitsModal opened onClose={() => {}}
                               capacity={{ cpu: 16, workers: 4, housekeepers: 2 }} used={{}} />
        </QueryClientProvider>
      </MantineProvider>,
    );
    expect(field("Worker agents at once").value).toBe("9");
  });
});
