"""Probe a program wiki with real questions and keep the evidence (todo L2, L3).

For each question one read-only agent answers from the program's wiki bundle,
then — in the same session — is debriefed on what it could not find and what
misled it. Both event streams are kept, and a report lays out, per question, the
answer, the pages read in the order they were read, the searches, the agent's
visible reasoning, and the debrief. That report is the input to L4.

The questions file is a markdown list, one question per top-level item; indented
lines continue the item above. Anything else in the file (headings, notes) is
ignored:

    # p2 wiki questions
    - Why did rescoring with explicit waters not fix the HB/Sol failures?
    - Which scoring terms have been ruled out, and on what evidence?
      Include the ones ruled out only partially.

Run:  python -m coscience.wiki_probe --program p2
Outputs go under ~/.cache/coscience/wiki-probe/<program>/<UTC stamp>/, never into
the substrate: a probe observes the wiki, it does not change it."""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

TOOLS = "Read,Glob,Grep"

_ITEM = re.compile(r"^(?:[-*]|\d+[.)])\s+(.*\S)\s*$")


def parse_questions(text: str) -> list[str]:
    """Top-level list items, with indented continuation lines folded in."""
    questions: list[str] = []
    for line in text.splitlines():
        m = _ITEM.match(line)
        if m:
            questions.append(m.group(1))
        elif questions and line[:1] in (" ", "\t") and line.strip():
            questions[-1] += " " + line.strip()
    return questions


def answer_prompt(bundle: Path, question: str) -> str:
    return f"""You are answering a question using a research program's wiki. The wiki is the
markdown bundle in your working directory ({bundle}).

How the wiki is laid out:
- `index.md` is the map: start there.
- `concepts/`, `entities/`, `syntheses/` hold knowledge pages; `sources/` holds one
  grounding page per ingested result or artifact.
- Links inside pages are root-relative: `/concepts/x.md` means `concepts/x.md` in
  your working directory. Follow them with Read.
- `ls`-style listing: use Glob (e.g. `concepts/*.md`); full-text search: use Grep.

Answer from the wiki. If a page points at a raw result outside the wiki (a
`/results/...` resource) and the wiki itself is not enough, you may read that raw
file — but say in your answer that you had to, and why.

Before each read, say in one short sentence what you are looking for. Then give
your answer, citing the wiki pages it rests on by path. If the wiki does not
answer the question, say so plainly rather than filling the gap yourself.

QUESTION:
{question}"""


DEBRIEF_PROMPT = """Now a debrief on how that went — not the answer again. Be specific and cite
page paths. Answer each:

1. What did you look for and not find? Name any page you expected to exist.
2. What misled you or cost you reads that led nowhere (a vague title, a stale page,
   a missing link, an index entry that did not match its page)?
3. Did any two pages disagree? How did you decide which to believe?
4. Did you have to leave the wiki for raw results? For what?
5. How confident are you in your answer, and what single change to the wiki would
   have made it easiest to answer?"""


def _events(raw: str):
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            yield json.loads(line)
        except ValueError:
            continue


def _result_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(c.get("text", "")) for c in content if isinstance(c, dict))
    return ""


def trace(raw: str, bundle: Path) -> dict:
    """What one run did, in order: reads (path, size, inside the wiki or not),
    searches, visible reasoning, and the final answer with its cost."""
    bundle = Path(bundle).resolve()
    pending: dict[str, dict] = {}
    reads: list[dict] = []
    searches: list[dict] = []
    thinking: list[str] = []
    out = {"session_id": "", "model": "", "answer": "", "cost": None, "turns": None,
           "is_error": False}
    for ev in _events(raw):
        kind = ev.get("type")
        if kind == "system" and ev.get("subtype") == "init":
            out["session_id"] = str(ev.get("session_id") or out["session_id"])
            out["model"] = str(ev.get("model") or "")
        elif kind == "assistant":
            for block in (ev.get("message") or {}).get("content") or []:
                btype = block.get("type")
                if btype == "thinking" and block.get("thinking"):
                    thinking.append(str(block["thinking"]).strip())
                elif btype == "text" and str(block.get("text", "")).strip():
                    thinking.append(str(block["text"]).strip())
                elif btype == "tool_use":
                    name, inp = block.get("name"), block.get("input") or {}
                    if name == "Read":
                        rec = _read_record(str(inp.get("file_path") or ""), bundle)
                        reads.append(rec)
                        pending[str(block.get("id"))] = rec
                    elif name in ("Glob", "Grep"):
                        searches.append({"tool": name, "pattern": str(inp.get("pattern") or ""),
                                         "path": str(inp.get("path") or "")})
        elif kind == "user":
            for block in (ev.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    rec = pending.pop(str(block.get("tool_use_id")), None)
                    if rec is not None:
                        rec["bytes"] = len(_result_text(block.get("content")).encode())
        elif kind == "result":
            out["session_id"] = str(ev.get("session_id") or out["session_id"])
            out["answer"] = str(ev.get("result") or "")
            out["cost"] = ev.get("total_cost_usd")
            out["turns"] = ev.get("num_turns")
            out["is_error"] = bool(ev.get("is_error"))
    # The last text block is the answer itself, already in `answer`.
    if thinking and out["answer"] and thinking[-1] == out["answer"].strip():
        thinking.pop()
    out.update(reads=reads, searches=searches, thinking=thinking)
    return out


def _read_record(path: str, bundle: Path) -> dict:
    p = Path(path)
    resolved = (bundle / p).resolve() if not p.is_absolute() else p.resolve()
    try:
        rel = str(resolved.relative_to(bundle))
        inside = True
    except ValueError:
        rel, inside = str(resolved), False
    return {"path": rel, "inside_wiki": inside, "bytes": None}


def _invoke(args: list[str], prompt: str, cwd: str) -> str:
    proc = subprocess.run(args, input=prompt, capture_output=True, text=True, cwd=cwd)
    return proc.stdout or ""


def probe(program_id: str, questions: list[str], bundle: Path, out_dir: Path, *,
          model: str = "", claude_bin: str = "claude", invoke=_invoke) -> dict:
    """Answer and debrief every question in turn; write streams, summary.json and
    report.md under `out_dir`. Returns the summary."""
    bundle, out_dir = Path(bundle), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    base = [claude_bin, "-p", "--output-format", "stream-json", "--verbose", "--tools", TOOLS]
    if model:
        base += ["--model", model]
    rows = []
    for n, question in enumerate(questions, 1):
        raw = invoke(base, answer_prompt(bundle, question), str(bundle))
        (out_dir / f"q{n:02d}.answer.jsonl").write_text(raw)
        answer = trace(raw, bundle)
        debrief = {"answer": "", "cost": None}
        if answer["session_id"]:
            raw_d = invoke(base + ["--resume", answer["session_id"]], DEBRIEF_PROMPT, str(bundle))
            (out_dir / f"q{n:02d}.debrief.jsonl").write_text(raw_d)
            debrief = trace(raw_d, bundle)
        rows.append({"n": n, "question": question, "answer": answer,
                     "debrief": {"text": debrief["answer"], "cost": debrief["cost"]}})
    summary = {"program": program_id, "bundle": str(bundle), "model": model,
               "at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
               "questions": rows}
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    (out_dir / "report.md").write_text(render_report(summary))
    return summary


def _money(v) -> str:
    return "?" if v is None else f"${float(v):.2f}"


def render_report(summary: dict) -> str:
    rows = summary["questions"]
    total = sum(float(r["answer"]["cost"] or 0) + float(r["debrief"]["cost"] or 0) for r in rows)
    hits = Counter(rd["path"] for r in rows for rd in r["answer"]["reads"] if rd["inside_wiki"])
    outside = Counter(rd["path"] for r in rows for rd in r["answer"]["reads"] if not rd["inside_wiki"])
    lines = [f"# Wiki probe — {summary['program']}", "",
             f"{len(rows)} questions · model {summary['model'] or 'default'} · "
             f"{summary['at']} · total {_money(total)}", ""]
    lines += ["## Pages read across all questions", ""]
    lines += [f"- {n}× `{p}`" for p, n in hits.most_common()] or ["- (none)"]
    if outside:
        lines += ["", "## Reads outside the wiki", ""]
        lines += [f"- {n}× `{p}`" for p, n in outside.most_common()]
    for r in rows:
        a = r["answer"]
        size = sum(rd["bytes"] or 0 for rd in a["reads"])
        lines += ["", f"## Q{r['n']}. {r['question']}", "",
                  f"{len(a['reads'])} reads ({size // 1024} KB) · {len(a['searches'])} searches · "
                  f"{a['turns'] or '?'} turns · answer {_money(a['cost'])} · "
                  f"debrief {_money(r['debrief']['cost'])}"
                  + (" · **run ended in error**" if a["is_error"] else ""), "",
                  "### Answer", "", a["answer"] or "_(no answer)_", "",
                  "### Pages, in the order read", ""]
        lines += [f"{i}. `{rd['path']}`" + ("" if rd["inside_wiki"] else " — **outside the wiki**")
                  + (f" ({rd['bytes'] // 1024} KB)" if rd["bytes"] else "")
                  for i, rd in enumerate(a["reads"], 1)] or ["_(none)_"]
        if a["searches"]:
            lines += ["", "### Searches", ""]
            lines += [f"- {s['tool']} `{s['pattern']}`" + (f" in `{s['path']}`" if s["path"] else "")
                      for s in a["searches"]]
        if a["thinking"]:
            lines += ["", "### Reasoning along the way", ""]
            lines += [f"> {t.replace(chr(10), ' ')}" for t in a["thinking"]]
        lines += ["", "### Debrief", "", r["debrief"]["text"] or "_(no debrief)_"]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m coscience.wiki_probe", description=__doc__.split("\n\n")[0])
    ap.add_argument("--program", required=True)
    ap.add_argument("--questions", help="questions file (default: programs/<id>/wiki-questions.md)")
    ap.add_argument("--model", default="", help="model to answer with (default: the program's planner model)")
    ap.add_argument("--repo", default=os.environ.get("COSCIENCE_REPO", "."), help="substrate repo")
    ap.add_argument("--out", help="output dir (default: ~/.cache/coscience/wiki-probe/<id>/<stamp>)")
    args = ap.parse_args(argv)

    from coscience.substrate import Substrate
    substrate = Substrate(Path(args.repo))
    program = substrate.load_program(args.program)
    bundle = substrate.program_dir(args.program) / "wiki"
    qfile = Path(args.questions) if args.questions else substrate.program_dir(args.program) / "wiki-questions.md"
    if not qfile.is_file():
        print(f"no questions file at {qfile}", file=sys.stderr)
        return 2
    questions = parse_questions(qfile.read_text())
    if not questions:
        print(f"{qfile} has no list items to ask", file=sys.stderr)
        return 2
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = Path(args.out) if args.out else Path.home() / ".cache" / "coscience" / "wiki-probe" / args.program / stamp
    summary = probe(args.program, questions, bundle, out, model=args.model or program.pm_model)
    total = sum(float(r["answer"]["cost"] or 0) + float(r["debrief"]["cost"] or 0)
                for r in summary["questions"])
    print(f"{len(questions)} questions probed, {_money(total)} — report: {out / 'report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
