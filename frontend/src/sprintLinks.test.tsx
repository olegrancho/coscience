import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import Md from "./components/Md";
import { LinkTargetsContext, sprintNames } from "./sprintLinks";

const ids = sprintNames(["p1-c3-kernel", "p1-c9-water", "p1-c4-a", "p1-c4-b"]);
const html = (md: string, known: ReadonlyMap<string, string> = ids) =>
  render(<LinkTargetsContext.Provider value={known}><Md>{md}</Md></LinkTargetsContext.Provider>)
    .container;

describe("sprint ids in markdown become links (K1)", () => {
  it("links a known id in plain text, without its full stop", () => {
    const a = html("The fix came from p1-c3-kernel.").querySelector("a")!;
    expect(a.getAttribute("href")).toBe("/sprints/p1-c3-kernel");
    expect(a.textContent).toBe("p1-c3-kernel");
  });

  it("links an id written as code, and every id in a sentence", () => {
    const links = html("`p1-c9-water` and p1-c3-kernel, then p1-c9-water").querySelectorAll("a");
    expect([...links].map((a) => a.getAttribute("href"))).toEqual(
      ["/sprints/p1-c9-water", "/sprints/p1-c3-kernel", "/sprints/p1-c9-water"]);
    expect(links[0].querySelector("code")?.textContent).toBe("p1-c9-water");
  });

  it("keeps an author's own link, and leaves unknown ids and longer words alone", () => {
    const c = html("[Kernel scale-up](/sprints/p1-c3-kernel) · p1-c3-kernel-result · p2-c1-x");
    const links = c.querySelectorAll("a");
    expect(links).toHaveLength(1);
    expect(links[0].textContent).toBe("Kernel scale-up");
  });

  it("links nothing outside the app, where no ids are provided", () => {
    expect(render(<Md>{"p1-c3-kernel"}</Md>).container.querySelector("a")).toBeNull();
  });

  it("links the short form when one sprint has it, and not when two share it", () => {
    const links = html("see p1-c3 and p1-c4").querySelectorAll("a");
    expect(links).toHaveLength(1);
    expect(links[0].getAttribute("href")).toBe("/sprints/p1-c3-kernel");
    expect(links[0].textContent).toBe("p1-c3");
  });

  it("links an idea id to its anchor where the page provides it", () => {
    const known = new Map(ids).set("3301b981", "/programs/p1/ideas#3301b981");
    const a = html("family-robust training (3301b981)", known).querySelector("a")!;
    expect(a.getAttribute("href")).toBe("/programs/p1/ideas#3301b981");
  });
});
