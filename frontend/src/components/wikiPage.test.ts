import { describe, expect, it } from "vitest";
import { headingId, isInternalLink, outline, wikiHref, linkFootnotes } from "./wikiPage";

describe("wikiHref", () => {
  it("routes a bundle-absolute page link into the wiki view", () => {
    expect(wikiHref("p1", "/concepts/auth-gate.md"))
      .toBe("/programs/p1/wiki/concepts/auth-gate");
  });

  it("routes a relative link the same way", () => {
    expect(wikiHref("p1", "concepts/auth-gate.md"))
      .toBe("/programs/p1/wiki/concepts/auth-gate");
  });

  it("keeps an anchor on the route", () => {
    expect(wikiHref("p1", "/concepts/a.md#evidence"))
      .toBe("/programs/p1/wiki/concepts/a#evidence");
  });

  it("leaves an external link alone", () => {
    expect(wikiHref("p1", "https://example.org/x")).toBe("https://example.org/x");
  });

  it("leaves a same-page anchor alone", () => {
    // GFM renders every footnote marker as `#user-content-fn-<id>`. Treating the
    // empty path as "the bundle root" turned each one into a trip to the index.
    expect(wikiHref("p1", "#user-content-fn-c1")).toBe("#user-content-fn-c1");
    expect(wikiHref("p1", "#evidence")).toBe("#evidence");
  });
});

describe("isInternalLink", () => {
  it("is true for a bundle page and false for anything with a scheme", () => {
    expect(isInternalLink("/concepts/a.md")).toBe(true);
    expect(isInternalLink("concepts/a.md")).toBe(true);
    expect(isInternalLink("https://example.org")).toBe(false);
    expect(isInternalLink("mailto:a@b.c")).toBe(false);
  });

  it("is false for a protocol-relative url and for nothing at all", () => {
    expect(isInternalLink("//example.org/x")).toBe(false);
    expect(isInternalLink("")).toBe(false);
  });
});

describe("outline", () => {
  it("lists headings with their level and a stable anchor id", () => {
    const items = outline("# Definition\n\ntext\n\n## Detail\n\n# Evidence\n");
    expect(items).toEqual([
      { level: 1, text: "Definition", id: "definition" },
      { level: 2, text: "Detail", id: "detail" },
      { level: 1, text: "Evidence", id: "evidence" },
    ]);
  });

  it("ignores a heading inside a fenced code block", () => {
    expect(outline("# Real\n\n```\n# Not a heading\n```\n")).toEqual([
      { level: 1, text: "Real", id: "real" },
    ]);
  });

  it("disambiguates two headings with the same text", () => {
    const items = outline("# Notes\n\n# Notes\n");
    expect(items.map((i) => i.id)).toEqual(["notes", "notes-2"]);
  });

  it("returns nothing for a body with no headings", () => {
    expect(outline("just prose")).toEqual([]);
  });
});

describe("headingId", () => {
  it("slugifies punctuation and spacing", () => {
    expect(headingId("Auth gate (401 on forged session)"))
      .toBe("auth-gate-401-on-forged-session");
  });
});

describe("linkFootnotes", () => {
  const sources = [
    { id: "wt-r1", resource: "/sources/result-wt-r1.md",
      title: "Sprint wt1 result: Baseline regression" },
  ];

  it("turns a bare-path definition into a link with the source's own title", () => {
    const out = linkFootnotes("[^wt-r1]: sources/result-wt-r1.md", sources);
    expect(out).toBe(
      "[^wt-r1]: [Sprint wt1 result: Baseline regression](/sources/result-wt-r1.md)");
  });

  it("leaves the prose and its markers untouched", () => {
    const body = "The floor.[^wt-r1]\n\n# References\n\n[^wt-r1]: sources/result-wt-r1.md\n";
    const out = linkFootnotes(body, sources);
    expect(out).toContain("The floor.[^wt-r1]");
    expect(out).toContain("# References");
    expect(out.split("\n")).toHaveLength(body.split("\n").length);
  });

  it("resolves a footnote whose id is missing from sources, using its own path", () => {
    expect(linkFootnotes("[^x]: sources/result-x.md", sources))
      .toBe("[^x]: [sources/result-x.md](sources/result-x.md)");
  });

  it("leaves a definition that is already a link alone", () => {
    const already = "[^wt-r1]: [already](/sources/result-wt-r1.md)";
    expect(linkFootnotes(already, sources)).toBe(already);
  });

  it("leaves a definition that is not a path alone", () => {
    const prose = "[^note]: measured on a warm afternoon";
    expect(linkFootnotes(prose, sources)).toBe(prose);
  });

  it("does not rewrite inside a fenced code block", () => {
    const body = "```\n[^wt-r1]: sources/result-wt-r1.md\n```\n";
    expect(linkFootnotes(body, sources)).toBe(body);
  });

  it("escapes brackets in a title so the link text cannot break the link", () => {
    const out = linkFootnotes("[^a]: sources/x.md", [
      { id: "a", resource: "/sources/x.md", title: "Result [draft]" }]);
    expect(out).toBe("[^a]: [Result \\[draft\\]](/sources/x.md)");
  });

  it("survives an empty body", () => {
    expect(linkFootnotes("", sources)).toBe("");
  });
});
