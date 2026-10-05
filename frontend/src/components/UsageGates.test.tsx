import { beforeAll, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { api, type Usage, type UsageGates } from "../api";
import UsageGatesModal from "./UsageGatesModal";
import { UsagePanel, gateMarks } from "./ui";

const gates: UsageGates = { pm: { "5h": 80, week: 99 }, worker: { "5h": 90, week: 99 }, wiki: { "5h": 70, week: 99 } };

beforeAll(() => {
  window.matchMedia = window.matchMedia || (((q: string) => ({
    matches: false, media: q, onchange: null, addListener() {}, removeListener() {},
    addEventListener() {}, removeEventListener() {}, dispatchEvent() { return false; },
  })) as unknown as typeof window.matchMedia);
  window.ResizeObserver = window.ResizeObserver || (class { observe() {} unobserve() {} disconnect() {} } as never);
});

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<MantineProvider><QueryClientProvider client={qc}>{ui}</QueryClientProvider></MantineProvider>);
}

describe("usage gates (G1)", () => {
  it("marks each line on its window, merging kinds that share one", () => {
    expect(gateMarks(gates, "5h")).toEqual([
      { pct: 70, kinds: ["wiki"] }, { pct: 80, kinds: ["PM"] }, { pct: 90, kinds: ["worker"] }]);
    expect(gateMarks(gates, "week")).toEqual([{ pct: 99, kinds: ["PM", "worker", "wiki"] }]);
  });

  it("draws the marks on the usage bars, with a way to change them", () => {
    const usage = {
      budget: { live: true, windows: { "5h": { pct: 40, resets: "", resets_at: 0 }, week: { pct: 70, resets: "", resets_at: 0 } } },
      runs: { pm: {}, worker: {} }, gates,
    } as unknown as Usage;
    const edit = vi.fn();
    wrap(<UsagePanel usage={usage} onEditGates={edit} />);
    expect(screen.getAllByTestId("gate-mark")).toHaveLength(4);
    fireEvent.click(screen.getByText("Change them"));
    expect(edit).toHaveBeenCalled();
  });

  it("saves the edited lines", async () => {
    const put = vi.spyOn(api, "setUsageGates").mockResolvedValue({ gates });
    const close = vi.fn();
    wrap(<UsageGatesModal gates={gates} opened onClose={close} />);
    const wiki = await screen.findByLabelText("wiki 5-hour line");
    fireEvent.change(wiki, { target: { value: "60" } });
    fireEvent.click(screen.getByText("Save"));
    await waitFor(() => expect(put).toHaveBeenCalled());
    expect(put.mock.calls[0][0].wiki["5h"]).toBe(60);
    await waitFor(() => expect(close).toHaveBeenCalled());
  });
});
