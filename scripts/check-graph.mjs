// Regression check for the concept graph, run in a real browser.
//
//   node scripts/drive.mjs /programs/<id>/wiki/graph scripts/check-graph.mjs
//
// This lives here rather than in the vitest suite because jsdom renders NO
// React Flow edges at all — the suite literally cannot see the bug this
// guards. That bug: React Flow attaches what it has measured (a node's size,
// where its handles landed) to the objects it hands back, and it will not draw
// an edge until both endpoints are measured. Building the node array during
// render therefore un-measured every node on every simulation frame, and while
// the physics was ticking the graph drew no edges — for the first few seconds
// after load, and again for a few seconds after every drag. It came back once
// the layout settled, which is what made it look like a filter problem.
//
// So the assertions are about what must NEVER be true, at any moment.

const fail = [];
const check = (cond, what) => { if (!cond) fail.push(what); };

export async function run({ eval: ev, wait, box, drag, mouse }) {
  const snap = () => ev(`(() => {
    const ns = Array.from(document.querySelectorAll('.react-flow__node'));
    return {
      nodes: ns.length,
      edges: document.querySelectorAll('.react-flow__edge').length,
      sized: ns.filter(n => n.getBoundingClientRect().width > 1).length,
      says: (document.querySelector(".wiki-graph .mantine-Text-root[data-size='xs']")
             || {}).textContent || "",
    };
  })()`);

  const watch = async (tag, ms) => {
    // Sample rather than spot-check: the failure was a WINDOW, not an end
    // state, so anything that only looks once misses it.
    const seen = [];
    const until = Date.now() + ms;
    while (Date.now() < until) { seen.push(await snap()); await wait(250); }
    const minEdges = Math.min(...seen.map((s) => s.edges));
    const minNodes = Math.min(...seen.map((s) => s.nodes));
    const unsized = seen.filter((s) => s.sized < s.nodes).length;
    console.log(`${tag.padEnd(18)} samples=${seen.length} minNodes=${minNodes} `
      + `minEdges=${minEdges} unsizedSamples=${unsized}`);
    return { minEdges, minNodes, unsized, last: seen[seen.length - 1] };
  };

  // 1. From first paint, through the whole settling animation.
  const load = await watch("load+settle", 8000);
  check(load.minNodes > 0, "nodes vanished at some point during load");
  check(load.minEdges > 0, "EDGES VANISHED during load — nodes are being rebuilt per frame");
  check(load.unsized === 0, "some node rendered with no size during load");

  const before = load.last;
  check(/showing \d+ of \d+ nodes/.test(before.says), "no node count rendered");

  // 2. Hovering must change nothing structural.
  const b = await box(".react-flow__node");
  await mouse("mouseMoved", b.cx, b.cy);
  const hover = await watch("hover", 1500);
  check(hover.minEdges === before.edges, "hovering changed the edge count");
  check(hover.minNodes === before.nodes, "hovering changed the node count");

  // 3. Dragging reheats the simulation — the window the bug lived in.
  await drag(b.cx, b.cy, b.cx + 150, b.cy + 80);
  const dragged = await watch("drag+resettle", 7000);
  check(dragged.minEdges > 0, "EDGES VANISHED after a drag — the reheated simulation "
    + "is rebuilding node objects and losing React Flow's measurements");
  check(dragged.minNodes === before.nodes, "nodes vanished after a drag");
  check(dragged.unsized === 0, "some node lost its size after a drag");

  // 4. A filter hides what it names and nothing else, and never blanks the view.
  const clicked = await ev(`(() => {
    const l = Array.from(document.querySelectorAll('label'))
      .find(x => x.textContent.trim() === 'Entity');
    if (!l) return false; l.click(); return true; })()`);
  check(clicked, "no Entity filter chip to click");
  const filtered = await watch("filter -Entity", 4000);
  check(filtered.minNodes > 0 && filtered.minNodes < before.nodes,
    `unticking one type should hide some nodes and keep the rest `
    + `(was ${before.nodes}, got ${filtered.minNodes})`);
  check(filtered.minEdges > 0, "EDGES VANISHED after filtering");

  if (fail.length) {
    console.log("\nFAILED:");
    for (const f of fail) console.log("  - " + f);
    throw new Error(`${fail.length} graph check(s) failed`);
  }
  console.log("\nall graph checks passed");
}
