import { createContext } from "react";

/** Every name that markdown should turn into a link -> where it goes (K1): sprint
 *  ids everywhere, a program's idea ids on that program's pages. Empty by default,
 *  which links nothing — a renderer outside the app (a test, a preview) behaves
 *  exactly as before. */
export const LinkTargetsContext = createContext<ReadonlyMap<string, string>>(new Map());

/** The sprint names to link: each full id, and its short form `<program>-c<n>` — the way
 *  people and agents usually write it — when exactly one sprint has that prefix. */
export function sprintNames(ids: Iterable<string>): Map<string, string> {
  const names = new Map<string, string>();
  const short = new Map<string, string[]>();
  for (const id of ids) {
    names.set(id, `/sprints/${id}`);
    const m = /^(.+?-c\d+)-/.exec(id);
    if (m) short.set(m[1], [...(short.get(m[1]) ?? []), id]);
  }
  for (const [s, full] of short) {
    if (full.length === 1 && !names.has(s)) names.set(s, `/sprints/${full[0]}`);
  }
  return names;
}

// A run of id characters that starts and ends on a letter or digit, so a sprint
// named at the end of a sentence does not drag its full stop into the link.
const TOKEN = /[A-Za-z0-9][A-Za-z0-9_-]*[A-Za-z0-9]/g;

type Node = { type: string; value?: string; url?: string; children?: Node[] };

const linkTo = (url: string, children: Node[]): Node => ({ type: "link", url, children });

function splitText(value: string, ids: ReadonlyMap<string, string>): Node[] {
  const out: Node[] = [];
  let last = 0;
  for (const m of value.matchAll(TOKEN)) {
    const url = ids.get(m[0]);
    if (!url) continue;
    const at = m.index ?? 0;
    if (at > last) out.push({ type: "text", value: value.slice(last, at) });
    out.push(linkTo(url, [{ type: "text", value: m[0] }]));
    last = at + m[0].length;
  }
  if (last === 0) return [{ type: "text", value }];
  if (last < value.length) out.push({ type: "text", value: value.slice(last) });
  return out;
}

function walk(node: Node, ids: ReadonlyMap<string, string>): void {
  // Text that is already a link keeps the link its author wrote.
  if (!node.children || node.type === "link" || node.type === "linkReference") return;
  const out: Node[] = [];
  for (const child of node.children) {
    if (child.type === "text") {
      out.push(...splitText(child.value ?? "", ids));
    } else if (child.type === "inlineCode" && ids.has((child.value ?? "").trim())) {
      out.push(linkTo(ids.get((child.value ?? "").trim())!, [child]));
    } else {
      walk(child, ids);
      out.push(child);
    }
  }
  node.children = out;
}

/** A remark plugin: every known name in the text — plain or in `code` — becomes a
 *  link to its target. Agents are told to link sprints by title (K2); this catches
 *  everything written before that, and every bare id since. */
export function remarkLinkTargets(ids: ReadonlyMap<string, string>) {
  return () => (tree: Node) => { if (ids.size) walk(tree, ids); };
}
