import { describe, it, expect } from "vitest";
import {
  applyFilters, emptyFilters, filterOptions, resolveFilters, toggleOption, isAll,
} from "./wikiGraphFilter";
import type { WikiGraphT } from "../api";

const g: WikiGraphT = {
  nodes: [
    { id: "a", slug: "a", title: "A", type: "Concept", status: "draft",
      trust: "unverified", in_degree: 0, out_degree: 1, orphan: false, cluster: 0 },
    { id: "b", slug: "b", title: "B", type: "Entity", status: "draft",
      trust: "human-reviewed", in_degree: 1, out_degree: 0, orphan: false, cluster: 0 },
  ],
  edges: [
    { id: "e1", src: "a", dst: "b", type: "refines", confidence: "high",
      source: "s", typed: true, materialized: false },
    { id: "e2", src: "a", dst: "b", type: "", confidence: "", source: "",
      typed: false, materialized: false },
  ],
};

const o = filterOptions(g);
const all = () => resolveFilters(emptyFilters(), o);

describe("filterOptions", () => {
  it("reads the groups off the graph, sorted, without duplicates", () => {
    expect(o.types).toEqual(["Concept", "Entity"]);
    expect(o.trust).toEqual(["human-reviewed", "unverified"]);
    // Only typed edges name a relation; the untyped one contributes nothing.
    expect(o.relations).toEqual(["refines"]);
  });
});

describe("resolveFilters", () => {
  it("reads an untouched group as everything, so the ticks match the picture", () => {
    const r = all();
    expect([...r.types].sort()).toEqual(["Concept", "Entity"]);
    expect([...r.trust].sort()).toEqual(["human-reviewed", "unverified"]);
    expect([...r.relations]).toEqual(["refines"]);
  });

  it("leaves a touched group exactly as chosen, including empty", () => {
    const r = resolveFilters({ ...emptyFilters(), types: new Set() }, o);
    // The bug this pins: an explicit "nothing selected" used to be
    // indistinguishable from "untouched", so deselecting everything showed
    // everything.
    expect(r.types.size).toBe(0);
  });
});

describe("toggleOption", () => {
  it("turns the clicked option off when everything is on", () => {
    const next = toggleOption(all().types, "Concept");
    expect([...next]).toEqual(["Entity"]);
  });

  it("turns an option back on without disturbing the others", () => {
    expect([...toggleOption(new Set(["Entity"]), "Concept")].sort())
      .toEqual(["Concept", "Entity"]);
  });

  it("does not mutate the set it was given", () => {
    const before = all().types;
    toggleOption(before, "Concept");
    expect(before.size).toBe(2);
  });
});

describe("isAll", () => {
  it("is true only when every option is shown", () => {
    expect(isAll(all().types, o.types)).toBe(true);
    expect(isAll(new Set(["Concept"]), o.types)).toBe(false);
    expect(isAll(new Set(), o.types)).toBe(false);
  });
});

describe("applyFilters", () => {
  it("shows the whole graph when nothing has been touched", () => {
    const out = applyFilters(g, all());
    expect(out.nodes).toHaveLength(2);
    expect(out.edges.map((e) => e.id)).toEqual(["e1", "e2"]);
  });

  it("shows nothing when a group is emptied, rather than everything", () => {
    // The reported inconsistency, pinned: all boxes clear must mean all off.
    const out = applyFilters(g, { ...all(), types: new Set() });
    expect(out.nodes).toEqual([]);
    expect(out.edges).toEqual([]);
  });

  it("typedOnly drops untyped body-link edges", () => {
    const out = applyFilters(g, { ...all(), typedOnly: true });
    expect(out.edges.map((e) => e.id)).toEqual(["e1"]);
  });

  it("filtering by node type hides the node and its edges", () => {
    const out = applyFilters(g, { ...all(), types: new Set(["Concept"]) });
    expect(out.nodes.map((n) => n.id)).toEqual(["a"]);
    expect(out.edges).toEqual([]);
  });

  it("a relation filter leaves untyped edges alone", () => {
    // An untyped edge has no relation to match. It used to vanish the moment
    // any relation chip was touched, so a control labelled "refines" silently
    // hid every body link as well; only `typedOnly` governs them now.
    const out = applyFilters(g, { ...all(), relations: new Set() });
    expect(out.edges.map((e) => e.id)).toEqual(["e2"]);
  });

  it("hides a typed edge whose relation is deselected", () => {
    const out = applyFilters(g, { ...all(), relations: new Set(), typedOnly: true });
    expect(out.edges).toEqual([]);
  });

  it("never manufactures an orphan", () => {
    // The defect this catches: recomputing `orphan` from the FILTERED edge set,
    // which rings a node whose edges the reader merely hid. `orphan` is a
    // whole-graph property computed server-side.
    const out = applyFilters(g, { ...all(), relations: new Set() });
    expect(out.nodes.every((n) => n.orphan === false)).toBe(true);
  });
});
