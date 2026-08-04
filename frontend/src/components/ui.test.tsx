import { describe, it, expect, vi, beforeAll } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";

import { ZoomableImg } from "./ui";

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
