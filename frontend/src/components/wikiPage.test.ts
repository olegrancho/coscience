import { describe, expect, it } from "vitest";
import { headingId, isInternalLink, outline, wikiHref } from "./wikiPage";

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
