import { describe, it, expect, vi, beforeAll } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";

import { Gauge, MODEL_OPTIONS, UsageBar, ZoomableImg, tokenTitle, windowElapsed } from "./ui";

// jsdom has no matchMedia; MantineProvider's color-scheme effect needs it.
beforeAll(() => {
  window.matchMedia = window.matchMedia || (((query: string) => ({
    matches: false, media: query, onchange: null,
    addListener() {}, removeListener() {},
    addEventListener() {}, removeEventListener() {}, dispatchEvent() { return false; },
  })) as unknown as typeof window.matchMedia);
});

const renderImg = (children = <ZoomableImg src="/raw/plot.png" alt="plot.png" />) =>
  render(<MantineProvider>{children}</MantineProvider>);

/** The overlay copy, as opposed to the inline one that was clicked. */
const overlayImg = () => screen.getAllByAltText("plot.png")[1];

describe("ZoomableImg", () => {
  it("shows the image full-size in an overlay when clicked", async () => {
    renderImg();
    expect(screen.getAllByAltText("plot.png")).toHaveLength(1);

    fireEvent.click(screen.getByAltText("plot.png"));

    await waitFor(() => expect(screen.getAllByAltText("plot.png")).toHaveLength(2));
    expect(overlayImg().getAttribute("src")).toBe("/raw/plot.png");
    expect(screen.getByText(/Open original/).getAttribute("href")).toBe("/raw/plot.png");
  });

  it("closes when the full-size image is clicked", async () => {
    renderImg();
    fireEvent.click(screen.getByAltText("plot.png"));
    await waitFor(() => expect(screen.getAllByAltText("plot.png")).toHaveLength(2));

    fireEvent.click(overlayImg());

    await waitFor(() => expect(screen.getAllByAltText("plot.png")).toHaveLength(1));
  });

  it("does not trigger the surrounding link when the thumbnail is clicked", () => {
    const onNavigate = vi.fn();
    renderImg(
      // The program page's thumbnail lives inside a card-wide link.
      <a href="/programs/p1" onClick={onNavigate}>
        <ZoomableImg src="/raw/plot.png" alt="plot.png" />
      </a>,
    );

    fireEvent.click(screen.getByAltText("plot.png"));

    expect(onNavigate).not.toHaveBeenCalled();
  });
});

describe("Gauge steppers", () => {
  const renderGauge = (props: Partial<Parameters<typeof Gauge>[0]> = {}) =>
    render(<MantineProvider><Gauge label="cpu" used={2} capacity={16} {...props} /></MantineProvider>);

  it("renders no stepper buttons when onAdjust is omitted", () => {
    renderGauge();
    expect(screen.queryByLabelText("increase cpu")).toBeNull();
    expect(screen.queryByLabelText("decrease cpu")).toBeNull();
  });

  it("reports +1 and -1 through onAdjust", () => {
    const onAdjust = vi.fn();
    renderGauge({ onAdjust });
    fireEvent.click(screen.getByLabelText("increase cpu"));
    expect(onAdjust).toHaveBeenCalledWith(1);
    fireEvent.click(screen.getByLabelText("decrease cpu"));
    expect(onAdjust).toHaveBeenCalledWith(-1);
  });

  it("won't decrease below zero", () => {
    const onAdjust = vi.fn();
    renderGauge({ capacity: 0, onAdjust });
    fireEvent.click(screen.getByLabelText("decrease cpu"));
    expect(onAdjust).not.toHaveBeenCalled();
  });
});

describe("Gauge pending state", () => {
  const renderGauge = (props: Partial<Parameters<typeof Gauge>[0]> = {}) =>
    render(<MantineProvider><Gauge label="cpu" used={2} capacity={16} {...props} /></MantineProvider>);

  it("reads normally when the capacity is the server's", () => {
    renderGauge({ onAdjust: vi.fn() });
    expect(screen.getByText("2 / 16").style.opacity).toBe("");
  });

  it("dims the readout while the capacity is unsaved", () => {
    renderGauge({ onAdjust: vi.fn(), pending: true });
    const readout = screen.getByText("2 / 16");
    expect(readout.style.opacity).toBe("0.45");
    expect(readout.style.fontStyle).toBe("italic");
  });
});

describe("tokenTitle", () => {
  const base = {
    total: 1, last_hour: 0, last_day: 0, last: null, cost: 1, cost_day: 1,
    tokens: 16650, input_tokens: 2, output_tokens: 5,
    cache_creation_input_tokens: 8570, cache_read_input_tokens: 8073,
    thinking_tokens: 3,
  };

  it("breaks the total into components so it can be read as cost", () => {
    const t = tokenTitle(base);
    expect(t).toContain("16,650 tokens total");
    expect(t).toContain("cache read    8,073");
    expect(t).toContain("(3 thinking)");
  });

  it("flags tokens from rows recorded before the split existed", () => {
    // 999 extra in the total with no matching components — a pre-split row.
    const t = tokenTitle({ ...base, tokens: 16650 + 999 });
    expect(t).toContain("999 from runs recorded before the split");
  });

  it("shows the total alone when no run has a split yet", () => {
    const t = tokenTitle({
      ...base, input_tokens: 0, output_tokens: 0,
      cache_creation_input_tokens: 0, cache_read_input_tokens: 0, thinking_tokens: 0,
    });
    expect(t).toBe("16,650 tokens total");
  });
});

describe("MODEL_OPTIONS", () => {
  it("offers Fable 5.1", () => {
    // The pickers for pm, wiki and sprint models all read this one list, so a
    // model missing here can only be set by typing its id as free text.
    const fable = MODEL_OPTIONS.find((o) => o.value === "claude-fable-5-1");
    expect(fable).toBeTruthy();
    expect(fable!.label).toBe("Fable 5.1");
  });
});

describe("windowElapsed", () => {
  it("places a reading in its window from the reset epoch", () => {
    expect(windowElapsed("5h", 1000 + 3600, 1000)).toBeCloseTo(0.8);   // 1h left of 5h
    expect(windowElapsed("week", 1000 + 7 * 86400, 1000)).toBe(0);     // just reset
    expect(windowElapsed("5h", 900, 1000)).toBe(1);                    // reset already passed
  });

  it("gives nothing to draw without an epoch", () => {
    expect(windowElapsed("5h", undefined, 1000)).toBeNull();
  });
});

describe("UsageBar", () => {
  const bar = (elapsed?: number | null) => (
    <MantineProvider><UsageBar label="5-hour" pct={60} resets="Sun 1:50" elapsed={elapsed} /></MantineProvider>
  );

  it("marks how far into the window we are", () => {
    render(bar(0.25));
    expect(screen.getByTestId("window-tick").style.left).toBe("25%");
  });

  it("draws no mark when the window position is unknown", () => {
    render(bar());
    expect(screen.queryByTestId("window-tick")).toBeNull();
  });
});
