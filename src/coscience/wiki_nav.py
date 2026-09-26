"""Click-through access to a wiki bundle, for the probe's navigation mode (todo L6).

A reader who browses a wiki starts at its index and follows links. This module is
that reader's only way in: `Navigator.open` returns `index.md` first, and after
that only pages linked from a page already opened. There is no search and no
listing, so an answer found this way is one the wiki's links actually lead to.

It is served to the answering agent as an MCP tool over stdio (`python -m
coscience.wiki_nav --bundle <dir> --log <file>`), because a rule in the prompt is
not enough: agents told to start at the index skipped it. The log records every
attempt — opened or refused — for the probe's trace."""
from __future__ import annotations

import argparse
import json
import posixpath
import re
import sys
import time
from pathlib import Path

INDEX = "index.md"

_MD_LINK = re.compile(r"\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
_WIKILINK = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")


def normalise(link: str, base: str = "") -> str | None:
    """A link as it appears on page `base`, as a bundle-relative `.md` path, or None
    when it points outside the bundle or at something that is not a page."""
    link = link.strip().split("#", 1)[0].split("?", 1)[0]
    if not link or re.match(r"^[a-z][a-z0-9+.-]*:", link, re.I):
        return None                       # empty, anchor-only, or a URL
    if link.startswith("/"):
        path = link.lstrip("/")
    else:
        path = posixpath.join(posixpath.dirname(base), link)
    path = posixpath.normpath(path)
    if path.startswith("..") or path == ".":
        return None
    if not path.endswith(".md"):
        path += ".md"
    return path


def links_on(text: str, base: str) -> set[str]:
    """Every page a reader of `base` could click to."""
    out = set()
    for m in _MD_LINK.finditer(text):
        p = normalise(m.group(1), base)
        if p:
            out.add(p)
    for m in _WIKILINK.finditer(text):
        # A wikilink names a page by slug; the bundle's own lint rewrites them to
        # real links, so a stray one is resolved the way a reader would guess it.
        p = normalise(m.group(1).strip(), base)
        if p:
            out.add(p)
    return out


class Navigator:
    def __init__(self, bundle: Path):
        self.bundle = Path(bundle).resolve()
        self.opened: list[str] = []
        self.reachable: set[str] = {INDEX}

    def open(self, link: str, from_page: str = "") -> tuple[bool, str, str]:
        """(ok, path, text-or-reason). `from_page` is where the reader clicked; a
        link is resolved against it, and failing that against any opened page."""
        candidates = []
        for base in ([from_page] if from_page else []) + [""] + self.opened:
            p = normalise(link, base)
            if p and p not in candidates:
                candidates.append(p)
        path = next((p for p in candidates if p in self.reachable), None)
        if path is None:
            shown = candidates[0] if candidates else link
            return False, shown, (f"`{shown}` is not linked from any page you have opened. "
                                  f"You can open `{INDEX}` or any page linked from a page "
                                  f"you have already opened.")
        target = (self.bundle / path).resolve()
        try:
            target.relative_to(self.bundle)
            text = target.read_text()
        except (ValueError, OSError):
            return False, path, f"`{path}` is linked, but no such page exists (a broken link)."
        if path not in self.opened:
            self.opened.append(path)
        self.reachable |= links_on(text, path)
        return True, path, text


TOOL = {
    "name": "open_page",
    "description": ("Open a page of the wiki. Start with index.md. After that you can "
                    "open any page linked from a page you have already opened: pass the "
                    "link exactly as it appears (for example /concepts/x.md), and the "
                    "page you found it on as from_page."),
    "inputSchema": {"type": "object",
                    "properties": {"link": {"type": "string"},
                                   "from_page": {"type": "string"}},
                    "required": ["link"]},
}


def serve(bundle: Path, log: Path | None, stdin=sys.stdin, stdout=sys.stdout) -> None:
    """A minimal MCP server over stdio: one tool, no resources, no prompts."""
    nav = Navigator(bundle)

    def reply(msg_id, result=None, error=None):
        msg = {"jsonrpc": "2.0", "id": msg_id}
        msg.update({"error": error} if error else {"result": result})
        stdout.write(json.dumps(msg) + "\n")
        stdout.flush()

    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            continue
        method, msg_id = req.get("method"), req.get("id")
        if msg_id is None:
            continue                      # a notification: nothing to answer
        if method == "initialize":
            reply(msg_id, {"protocolVersion": (req.get("params") or {}).get("protocolVersion", "2025-06-18"),
                           "capabilities": {"tools": {}},
                           "serverInfo": {"name": "wiki", "version": "1"}})
        elif method == "tools/list":
            reply(msg_id, {"tools": [TOOL]})
        elif method == "tools/call":
            args = (req.get("params") or {}).get("arguments") or {}
            ok, path, text = nav.open(str(args.get("link", "")), str(args.get("from_page", "")))
            if log:
                with open(log, "a") as fh:
                    fh.write(json.dumps({"at": time.time(), "link": args.get("link"),
                                         "from": args.get("from_page", ""), "path": path,
                                         "ok": ok, "broken": (not ok) and "broken link" in text,
                                         "bytes": len(text.encode()) if ok else 0}) + "\n")
            reply(msg_id, {"content": [{"type": "text", "text": text}], "isError": not ok})
        elif method == "ping":
            reply(msg_id, {})
        else:
            reply(msg_id, error={"code": -32601, "message": f"unknown method {method}"})


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m coscience.wiki_nav")
    ap.add_argument("--bundle", required=True)
    ap.add_argument("--log")
    args = ap.parse_args(argv)
    serve(Path(args.bundle), Path(args.log) if args.log else None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
