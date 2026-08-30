// Regression check for the wiki page's neighbourhood pane, in a real browser.
//
//   node scripts/drive.mjs /programs/<id>/wiki/<page> scripts/check-neighbourhood.mjs
//
// Why a browser: jsdom has no hit testing at all, and the bug that made this
// pane feel broken was pure hit testing. An unverified page's disc is drawn
// fill="none" — that is the encoding for "nothing has checked this" — and SVG
// does not hit-test the interior of an unfilled shape. Every disc in a fresh
// wiki is unverified, so grabbing one to move it, or clicking one to open it,
// landed on the <svg> behind it and panned the picture instead. jsdom's
// fireEvent dispatches straight at the element you name, so every test of this
// passed while nothing worked.

const fail = [];
const check = (cond, what) => { if (!cond) fail.push(what); };
const near = (a, b, eps = 1.5) => Math.abs(a - b) <= eps;

export async function run({ eval: ev, wait, drag, shot }) {
  await wait(3000);
  // The pane lives in a sticky right rail, below the fold on a short viewport.
  await ev(`document.querySelector('svg.wiki-nbhd')?.scrollIntoView({block:'center'})`);
  await wait(600);

  const discs = () => ev(`Array.from(document.querySelectorAll('svg.wiki-nbhd circle'))
    .map(c => ({ t: c.getAttribute('title'), x: +c.getAttribute('cx'), y: +c.getAttribute('cy') }))`);
  const vb = () => ev(`document.querySelector('svg.wiki-nbhd')?.getAttribute('viewBox')`);
  const path = () => ev(`location.pathname`);

  const before = await discs();
  check(before.length >= 2, `pane drew ${before.length} discs, expected at least 2`);
  if (!before.length) { report(); return; }

  // 1. Every disc must be hit-testable in its middle, not only on its outline.
  const hits = await ev(`Array.from(document.querySelectorAll('svg.wiki-nbhd circle'))
    .map(c => { const r = c.getBoundingClientRect();
      const el = document.elementFromPoint(r.x + r.width/2, r.y + r.height/2);
      return { t: c.getAttribute('title'), hit: el && el.tagName }; })`);
  for (const h of hits) {
    check(h.hit === "circle", `the middle of "${h.t}" is not grabbable — `
      + `elementFromPoint found <${h.hit}>, so an unfilled disc is passing the `
      + `pointer through to the pane behind it`);
  }

  // 2. Dragging a neighbour moves that one, leaves the others, does not pan,
  //    and does not navigate away.
  const target = await ev(`(() => {
    const c = document.querySelector('svg.wiki-nbhd a circle');
    if (!c) return null;
    const r = c.getBoundingClientRect();
    return { cx: r.x + r.width/2, cy: r.y + r.height/2, t: c.getAttribute('title') };
  })()`);
  check(!!target, "no draggable neighbour disc in the pane");
  if (!target) { report(); return; }

  const vbBefore = await vb();
  const pathBefore = await path();
  await drag(target.cx, target.cy, target.cx + 55, target.cy - 45);
  await wait(500);
  const after = await discs();

  const was = before.find((d) => d.t === target.t);
  const now = after.find((d) => d.t === target.t);
  check(now && (!near(now.x, was.x) || !near(now.y, was.y)),
    `dragging "${target.t}" did not move it`);
  for (const d of before) {
    if (d.t === target.t) continue;
    const still = after.find((x) => x.t === d.t);
    check(still && near(still.x, d.x) && near(still.y, d.y),
      `dragging one node moved "${d.t}" as well`);
  }
  check(await vb() === vbBefore, "dragging a node panned the whole picture");
  check(await path() === pathBefore, "dragging a node navigated away from the page");
  check(await ev(`!!Array.from(document.querySelectorAll('button'))
    .find(b => /reset view/i.test(b.textContent))`), "no way offered to undo the drag");

  // 3. The background still pans — the node grab must not have eaten it.
  const pannedFrom = await vb();
  await drag(target.cx - 95, target.cy + 70, target.cx - 45, target.cy + 90);
  await wait(400);
  check(await vb() !== pannedFrom, "dragging the background no longer pans");

  await shot("neighbourhood");
  report();
}

function report() {
  if (fail.length) {
    console.log("FAILED:");
    for (const f of fail) console.log("  - " + f);
    throw new Error(`${fail.length} neighbourhood check(s) failed`);
  }
  console.log("all neighbourhood checks passed");
}
