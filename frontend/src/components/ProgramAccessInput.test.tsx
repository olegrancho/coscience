import { describe, it, expect, vi, beforeAll } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import ProgramAccessInput from "./ProgramAccessInput";

beforeAll(() => {
  window.matchMedia = window.matchMedia || (((query: string) => ({
    matches: false, media: query, onchange: null,
    addListener() {}, removeListener() {},
    addEventListener() {}, removeEventListener() {}, dispatchEvent() { return false; },
  })) as unknown as typeof window.matchMedia);
});

const PROGRAMS = [{ id: "p1", title: "Aging" }, { id: "p2" }, { id: "p3", title: "Longevity" }];

function renderInput(value: string[], onChange = vi.fn(), programs = PROGRAMS) {
  render(
    <MantineProvider>
      <ProgramAccessInput value={value} onChange={onChange} programs={programs} />
    </MantineProvider>,
  );
  return onChange;
}

describe("ProgramAccessInput", () => {
  it("seeds the dropdown with the value passed in", () => {
    const { container } = render(
      <MantineProvider>
        <ProgramAccessInput value={["p1", "p2"]} onChange={vi.fn()} programs={PROGRAMS} />
      </MantineProvider>,
    );
    const pillLabels = [...container.querySelectorAll(".mantine-Pill-label")].map((el) => el.textContent);
    expect(pillLabels).toEqual(["Aging (p1)", "p2"]);
  });

  it("selects every program on 'select all'", () => {
    const onChange = renderInput(["p1"]);
    fireEvent.click(screen.getByRole("button", { name: "select all" }));
    expect(onChange).toHaveBeenCalledWith(["p1", "p2", "p3"]);
  });

  it("empties the list on 'clear'", () => {
    const onChange = renderInput(["p1", "p2"]);
    fireEvent.click(screen.getByRole("button", { name: "clear" }));
    expect(onChange).toHaveBeenCalledWith([]);
  });

  it("still shows and can remove an id that is no longer a program", () => {
    const onChange = renderInput(["p1", "gone"]);
    const pillLabel = screen.getAllByText("gone")[0];
    const pill = pillLabel.closest(".mantine-Pill-root") as HTMLElement;
    fireEvent.click(pill.querySelector("button")!);
    expect(onChange).toHaveBeenCalledWith(["p1"]);
  });

  it("never disables the input and shows no error", () => {
    renderInput([]);
    const input = screen.getByRole("textbox", { name: /Programs this server runs/ }) as HTMLInputElement;
    expect(input.disabled).toBe(false);
    expect(screen.queryByText(/pick at least one/i)).toBeNull();
  });

  it("shows the description", () => {
    renderInput([]);
    expect(screen.getByText("Empty means this server takes no work.")).toBeTruthy();
  });
});
