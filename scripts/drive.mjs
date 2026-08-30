// Drive a real browser over the DevTools protocol: click things, read what
// happened, save screenshots.
//
// `scripts/shoot.sh` proves a route renders. It cannot press anything, so it
// says nothing about whether a control does what it claims — which is where
// the filters went wrong. Node 22 has a global WebSocket, so this needs no
// dependencies at all.
//
//   node scripts/drive.mjs <route> <script.mjs>
//
// The script file gets one export, `run({ eval, click, text, shot, wait })`:
//   eval(js)          -> the value of a JS expression in the page
//   click(selector)   -> click the first match (throws if there is none)
//   text(selector)    -> textContent of the first match, or null
//   shot(name)        -> save a PNG under OUT
//   wait(ms)
import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

const BASE = process.env.BASE || "http://127.0.0.1:8000";
const OUT = process.env.OUT || "/tmp/coscience-shots";
const PORT = Number(process.env.CDP_PORT || 9333);
const route = process.argv[2];
const scriptPath = process.argv[3];
if (!route || !scriptPath) {
  console.error("usage: node scripts/drive.mjs <route> <script.mjs>");
  process.exit(2);
}
fs.mkdirSync(OUT, { recursive: true });

const chrome = spawn("google-chrome", [
  "--headless=new", "--no-sandbox", "--disable-gpu", "--hide-scrollbars",
  `--remote-debugging-port=${PORT}`,
  "--window-size=1600,1100",
  `--user-data-dir=${fs.mkdtempSync("/tmp/cdp-")}`,
  "about:blank",
], { stdio: "ignore" });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// The debugging port takes a moment to listen.
let target = null;
for (let i = 0; i < 60 && !target; i++) {
  await sleep(250);
  try {
    const list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
    target = list.find((t) => t.type === "page" && t.webSocketDebuggerUrl);
  } catch { /* not up yet */ }
}
if (!target) { chrome.kill(); throw new Error("chrome never opened its debug port"); }

const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((r, j) => { ws.onopen = r; ws.onerror = j; });

let seq = 0;
const pending = new Map();
ws.onmessage = (m) => {
  const msg = JSON.parse(m.data);
  const p = pending.get(msg.id);
  if (!p) return;
  pending.delete(msg.id);
  msg.error ? p.reject(new Error(JSON.stringify(msg.error))) : p.resolve(msg.result);
};
const send = (method, params = {}) => new Promise((resolve, reject) => {
  const id = ++seq;
  pending.set(id, { resolve, reject });
  ws.send(JSON.stringify({ id, method, params }));
});

async function evaluate(expression) {
  const r = await send("Runtime.evaluate", {
    expression, returnByValue: true, awaitPromise: true,
  });
  if (r.exceptionDetails) {
    throw new Error("page threw: " + (r.exceptionDetails.exception?.description
      || r.exceptionDetails.text));
  }
  return r.result.value;
}

const api = {
  eval: evaluate,
  wait: sleep,
  async click(sel) {
    const ok = await evaluate(`(() => {
      const el = document.querySelector(${JSON.stringify(sel)});
      if (!el) return false;
      el.click();
      return true;
    })()`);
    if (!ok) throw new Error("no element matched " + sel);
    await sleep(150);
  },
  text: (sel) => evaluate(
    `(document.querySelector(${JSON.stringify(sel)}) || {}).textContent ?? null`),
  /** Viewport rect of the first match, or null. */
  box: (sel) => evaluate(`(() => {
    const el = document.querySelector(${JSON.stringify(sel)});
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return { x: r.x, y: r.y, w: r.width, h: r.height,
             cx: r.x + r.width / 2, cy: r.y + r.height / 2 };
  })()`),
  /** Real mouse input through the protocol — a synthetic el.click() does not
   *  go near the pointer/drag handling that a graph actually runs on. */
  async mouse(type, x, y, button = "left") {
    await send("Input.dispatchMouseEvent", {
      type, x, y, button: type === "mouseMoved" ? "none" : button,
      buttons: type === "mouseMoved" ? 0 : 1, clickCount: 1,
    });
    await sleep(40);
  },
  async drag(fromX, fromY, toX, toY, steps = 12) {
    await send("Input.dispatchMouseEvent", {
      type: "mousePressed", x: fromX, y: fromY, button: "left", buttons: 1, clickCount: 1 });
    await sleep(60);
    for (let i = 1; i <= steps; i++) {
      await send("Input.dispatchMouseEvent", {
        type: "mouseMoved", button: "left", buttons: 1,
        x: fromX + ((toX - fromX) * i) / steps,
        y: fromY + ((toY - fromY) * i) / steps });
      await sleep(30);
    }
    await send("Input.dispatchMouseEvent", {
      type: "mouseReleased", x: toX, y: toY, button: "left", buttons: 0, clickCount: 1 });
    await sleep(120);
  },
  async shot(name) {
    const r = await send("Page.captureScreenshot", { format: "png" });
    const f = path.join(OUT, name.replace(/[^\w.-]/g, "_") + ".png");
    fs.writeFileSync(f, Buffer.from(r.data, "base64"));
    console.log("shot:", f);
    return f;
  },
};

// Collect anything the page logs as an error — the thing a DOM dump cannot see.
const consoleErrors = [];
await send("Runtime.enable");
await send("Page.enable");

// Listen BEFORE navigating, or everything the page says while loading — which
// is most of what it says — is missed.
ws.addEventListener("message", (m) => {
  const msg = JSON.parse(m.data);
  if (msg.method === "Runtime.exceptionThrown") {
    consoleErrors.push("THROWN " + (msg.params.exceptionDetails?.exception?.description
      || msg.params.exceptionDetails?.text));
  }
  if (msg.method === "Runtime.consoleAPICalled"
      && ["error", "warning", "assert"].includes(msg.params.type)) {
    consoleErrors.push(msg.params.type.toUpperCase() + " "
      + msg.params.args.map((a) => a.description ?? a.value ?? a.type).join(" "));
  }
});

// Navigate through the protocol rather than the command line: the URL given
// on the command line lands in a target we are not necessarily attached to,
// and the one we get is about:blank. That is how this driver first "found"
// an empty page and reported zero of everything.
await send("Page.navigate", { url: BASE + route });
for (let i = 0; i < 80; i++) {
  await sleep(250);
  const ready = await evaluate("document.readyState").catch(() => null);
  if (ready === "complete") break;
}

let code = 0;
try {
  const mod = await import(path.resolve(scriptPath));
  await mod.run(api);
} catch (e) {
  console.error("FAILED:", e.message);
  code = 1;
} finally {
  if (consoleErrors.length) {
    console.log("\n--- page errors ---");
    for (const e of consoleErrors) console.log(e);
  }
  ws.close();
  chrome.kill();
}
process.exit(code);
