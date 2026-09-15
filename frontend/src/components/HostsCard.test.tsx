import { describe, it, expect, vi, beforeAll } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MantineProvider } from "@mantine/core";

vi.mock("../api", () => ({ api: { probeHost: vi.fn(), confirmHost: vi.fn() } }));

import type { LedgerHost } from "../api";
import HostsCard, { hostOffer } from "./HostsCard";

beforeAll(() => {
  window.matchMedia = window.matchMedia || (((query: string) => ({
    matches: false, media: query, onchange: null,
    addListener() {}, removeListener() {},
    addEventListener() {}, removeEventListener() {}, dispatchEvent() { return false; },
  })) as unknown as typeof window.matchMedia);
});

const LOCAL: LedgerHost = {
  name: "local", ssh: "", placeable: true, programs: [], run_root: "",
  capacity: { cpu: 24, gpu: 1 }, available: {},
  gpus: [{ index: 0, model: "", vram_gb: null, whole: true, shared_gb: 0 }],
};
const REMOTE: LedgerHost = {
  name: "gpu1", ssh: "gpu1", placeable: false, programs: ["p2"], run_root: "~/coscience-runs",
  capacity: { cpu: 10, memory_gb: 50, gpu: 1 }, available: {},
  gpus: [{ index: 0, model: "X", vram_gb: 10.8, whole: false, shared_gb: 0 }],
};

function renderCard(errors: string[] = []) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MantineProvider>
      <QueryClientProvider client={qc}>
        <HostsCard hosts={[LOCAL, REMOTE]} errors={errors} />
      </QueryClientProvider>
    </MantineProvider>,
  );
}

describe("hostOffer", () => {
  it("reads cores, memory and each card", () => {
    expect(hostOffer(LOCAL)).toBe("24 CPU cores · GPU (VRAM not declared)");
    expect(hostOffer(REMOTE)).toBe("10 CPU cores · 50 GB memory · 10.8 GB GPU");
  });
});

describe("HostsCard", () => {
  it("lists every server with how it is reached, what it offers and whether it takes work", () => {
    renderCard();
    expect(screen.getByText("this machine")).toBeTruthy();
    expect(screen.getByText("10 CPU cores · 50 GB memory · 10.8 GB GPU")).toBeTruthy();
    expect(screen.getByText("p2")).toBeTruthy();
    expect(screen.getByText("waits for remote launch")).toBeTruthy();
  });

  it("shows host errors from the pool file", () => {
    renderCard(["hosts.typo: needs ssh (an ssh alias or user@host)"]);
    expect(screen.getByText("hosts.typo: needs ssh (an ssh alias or user@host)")).toBeTruthy();
  });

  it("opens the add-server dialog", async () => {
    renderCard();
    fireEvent.click(screen.getByRole("button", { name: "Add server" }));
    expect(await screen.findByLabelText("SSH target")).toBeTruthy();
  });
});
