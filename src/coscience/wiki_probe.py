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

Questions can also come as a JSON list of {"id", "question", "key", "sources"}: the
key is a grader's reference (L5, L7), never shown to the answering agent.

Two modes (L6). `agent` is how the platform's agents use a wiki: the whole bundle,
Read/Glob/Grep, anything goes. `nav` is how a person browses it: the only tool is
`open_page` (coscience.wiki_nav), which serves index.md and then only pages linked
from pages already opened — no search, no listing. With --grade, a grader scores
each answer on path and clarity first and treats the key as a reference (L7).

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
NAV_TOOL = "mcp__wiki__open_page"

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

- `index.md` is the map: start there.
- Links inside pages are root-relative: `/x/y.md` means `x/y.md` in your working
  directory. Follow them with Read.
- `ls`-style listing: use Glob (e.g. `**/*.md`); full-text search: use Grep.

Answer from the wiki. If a page points at a raw result outside the wiki (a
`/results/...` resource) and the wiki itself is not enough, you may read that raw
file — but say in your answer that you had to, and why.

Before each read, say in one short sentence what you are looking for. Then give
your answer, citing the wiki pages it rests on by path. If the wiki does not
answer the question, say so plainly rather than filling the gap yourself.

QUESTION:
{question}"""


def nav_prompt(question: str) -> str:
    return f"""You are answering a question by browsing a research program's wiki the way a
person does: open the index, then click through links. Your only tool is
`open_page`. Start with `index.md`; after that you can open any page linked from a
page you have already opened — pass the link as written and the page it was on as
`from_page`. There is no search.

Before each click, say in one short sentence what you are looking for and why this
link. Then give your answer, citing the pages it rests on by path. If you could not
find the answer by following links, say so plainly, say where you looked, and do
not fill the gap yourself.

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
                    if name == "Read" or name == NAV_TOOL:
                        path = (str(inp.get("file_path") or "") if name == "Read"
                                else str(inp.get("link") or "").lstrip("/"))
                        rec = _read_record(path, bundle)
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


def load_questions(text: str) -> list[dict]:
    """Questions as dicts {id, question, key, sources}: from a JSON list when the
    text is one, else from a markdown list (no keys)."""
    stripped = text.lstrip()
    if stripped.startswith("["):
        out = []
        for i, q in enumerate(json.loads(stripped), 1):
            out.append({"id": str(q.get("id") or f"q{i:02d}"), "question": str(q["question"]),
                        "key": str(q.get("key") or ""), "sources": list(q.get("sources") or [])})
        return out
    return [{"id": f"q{i:02d}", "question": q, "key": "", "sources": []}
            for i, q in enumerate(parse_questions(text), 1)]


GRADE_PROMPT = """You are grading one answer produced by an agent that read a research wiki.
Judge what the wiki gave the reader, not the agent's prose style. Two things matter
most, in this order:

1. PATH (1-5): how directly the wiki led to the answer. 5 = the index or first page
   routed straight to it; 3 = found, but by stitching several pages or after dead
   ends; 1 = not found, or found only outside the wiki.
2. CLARITY (1-5): does the answer address what was asked, clearly scoped, with
   uncertainty stated and gaps admitted rather than filled in?

Then the KEY, if one is given. It is a reference written from the raw results, not a
checklist: a different but defensible answer is fine and missing detail is not a
fault. Classify only: "agrees", "differs-defensibly", "contradicts" (the answer
states something the key's sources contradict), "key-superseded" (the answer shows,
with a cited later result, that the key's point was overturned — this is a GOOD
outcome), or "no-key".

QUESTION:
{question}

KEY (reference only):
{key}

HOW THE ANSWER WAS FOUND ({mode} mode):
{path}

ANSWER:
{answer}

Reply with ONLY a JSON object:
{{"path": n, "path_note": "one sentence", "clarity": n, "clarity_note": "one sentence",
  "key": "agrees|differs-defensibly|contradicts|key-superseded|no-key",
  "key_note": "one sentence, or empty", "admits_gaps": true|false}}"""


def _path_summary(answer: dict, nav: dict | None) -> str:
    lines = [f"{i}. {r['path']}" + ("" if r["inside_wiki"] else " (OUTSIDE the wiki)")
             for i, r in enumerate(answer["reads"], 1)]
    if answer["searches"]:
        lines.append(f"searches: {len(answer['searches'])}")
    if nav:
        lines.append(f"refused clicks (not linked from an opened page): {nav['refused']}; "
                     f"broken links: {nav['broken']}")
    return "\n".join(lines) or "(opened nothing)"


def _json_object(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    try:
        return json.loads(m.group(0)) if m else {}
    except ValueError:
        return {}


def nav_stats(log: Path) -> dict:
    """What the click-through log says: clicks that opened a page, clicks refused
    because nothing opened so far linked there, and links to missing pages."""
    rows = []
    if log.is_file():
        for line in log.read_text().splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
    opened = [r for r in rows if r.get("ok")]
    return {"clicks": len(opened), "pages": len({r["path"] for r in opened}),
            "reopened": len(opened) - len({r["path"] for r in opened}),
            "refused": sum(1 for r in rows if not r.get("ok") and not r.get("broken")),
            "broken": sum(1 for r in rows if r.get("broken")), "log": bool(rows)}


def _nav_args(base: list[str], bundle: Path, log: Path, python: str) -> list[str]:
    config = {"mcpServers": {"wiki": {"command": python, "args": [
        "-m", "coscience.wiki_nav", "--bundle", str(bundle), "--log", str(log)]}}}
    args = [a for a in base]
    i = args.index("--tools")
    args[i + 1] = ""
    return args + ["--mcp-config", json.dumps(config), "--strict-mcp-config",
                   "--allowedTools", NAV_TOOL]


def probe(program_id: str, questions: list, bundle: Path, out_dir: Path, *,
          model: str = "", claude_bin: str = "claude", invoke=_invoke, mode: str = "agent",
          debrief: bool = True, grade_model: str = "", jobs: int = 1,
          python: str = sys.executable) -> dict:
    """Answer (and debrief, and grade) every question; write streams, summary.json
    and report.md under `out_dir`. Returns the summary."""
    from concurrent.futures import ThreadPoolExecutor
    bundle, out_dir = Path(bundle).resolve(), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    qs = [q if isinstance(q, dict) else {"id": f"q{i:02d}", "question": q, "key": "", "sources": []}
          for i, q in enumerate(questions, 1)]
    base = [claude_bin, "-p", "--output-format", "stream-json", "--verbose", "--tools", TOOLS]
    if model:
        base += ["--model", model]

    def one(n: int, q: dict) -> dict:
        stem = f"q{n:02d}"
        nav = None
        if mode == "nav":
            log = out_dir / f"{stem}.nav.jsonl"
            log.unlink(missing_ok=True)
            raw = invoke(_nav_args(base, bundle, log, python), nav_prompt(q["question"]), str(bundle))
            nav = nav_stats(log)
        else:
            raw = invoke(base, answer_prompt(bundle, q["question"]), str(bundle))
        (out_dir / f"{stem}.answer.jsonl").write_text(raw)
        answer = trace(raw, bundle)
        if nav is not None:
            # Only clicks the tool granted count as reads; the trace also saw refusals.
            granted = [r for r in (json.loads(x) for x in (out_dir / f"{stem}.nav.jsonl").read_text().splitlines()
                                   if x.strip()) if r.get("ok")] if nav["log"] else []
            answer["reads"] = [{"path": r["path"], "inside_wiki": True, "bytes": r.get("bytes")}
                               for r in granted]
        deb = {"answer": "", "cost": None}
        if debrief and answer["session_id"]:
            # The debrief needs no tools: it is about the run that just ended.
            d_args = [a for a in base]
            d_args[d_args.index("--tools") + 1] = ""
            raw_d = invoke(d_args + ["--resume", answer["session_id"]], DEBRIEF_PROMPT, str(bundle)) \
                if mode == "nav" else invoke(base + ["--resume", answer["session_id"]], DEBRIEF_PROMPT, str(bundle))
            (out_dir / f"{stem}.debrief.jsonl").write_text(raw_d)
            deb = trace(raw_d, bundle)
        grade = {}
        if grade_model and answer["answer"]:
            g_prompt = GRADE_PROMPT.format(question=q["question"], key=q["key"] or "(none)",
                                           mode=mode, path=_path_summary(answer, nav),
                                           answer=answer["answer"])
            raw_g = invoke([claude_bin, "-p", "--output-format", "stream-json", "--verbose",
                            "--tools", "", "--model", grade_model], g_prompt, str(out_dir))
            (out_dir / f"{stem}.grade.jsonl").write_text(raw_g)
            g = trace(raw_g, bundle)
            grade = _json_object(g["answer"])
            grade["cost"] = g["cost"]
        return {"n": n, "id": q["id"], "question": q["question"], "key": q["key"],
                "answer": answer, "nav": nav,
                "debrief": {"text": deb["answer"], "cost": deb["cost"]}, "grade": grade}

    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        rows = list(pool.map(lambda nq: one(*nq), enumerate(qs, 1)))
    summary = {"program": program_id, "bundle": str(bundle), "model": model, "mode": mode,
               "at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
               "questions": rows}
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    (out_dir / "report.md").write_text(render_report(summary))
    return summary


def _money(v) -> str:
    return "?" if v is None else f"${float(v):.2f}"


def render_report(summary: dict) -> str:
    rows = summary["questions"]
    total = sum(float(r["answer"]["cost"] or 0) + float(r["debrief"]["cost"] or 0)
                + float((r.get("grade") or {}).get("cost") or 0) for r in rows)
    hits = Counter(rd["path"] for r in rows for rd in r["answer"]["reads"] if rd["inside_wiki"])
    outside = Counter(rd["path"] for r in rows for rd in r["answer"]["reads"] if not rd["inside_wiki"])
    lines = [f"# Wiki probe — {summary['program']}", "",
             f"{len(rows)} questions · {summary.get('mode', 'agent')} mode · "
             f"model {summary['model'] or 'default'} · "
             f"{summary['at']} · total {_money(total)}", ""]
    graded = [r for r in rows if r.get("grade", {}).get("path")]
    if graded:
        def avg(k):
            return sum(float(r["grade"][k]) for r in graded) / len(graded)
        keys = Counter(r["grade"].get("key", "?") for r in graded)
        lines += ["## Grades", "",
                  f"path {avg('path'):.1f}/5 · clarity {avg('clarity'):.1f}/5 · "
                  + " · ".join(f"{k} {n}" for k, n in keys.most_common()), "",
                  "| # | question | path | clarity | key | pages | note |",
                  "|---|---|---|---|---|---|---|"]
        for r in rows:
            g = r.get("grade") or {}
            q = r["question"] if len(r["question"]) <= 70 else r["question"][:69] + "…"
            lines.append(f"| {r['n']} | {q.replace('|', '/')} | {g.get('path', '-')} | "
                         f"{g.get('clarity', '-')} | {g.get('key', '-')} | "
                         f"{len(r['answer']['reads'])} | {str(g.get('path_note', '')).replace('|', '/')} |")
        lines.append("")
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
                  + (" · **run ended in error**" if a["is_error"] else "")
                  + (f" · {r['nav']['refused']} refused clicks, {r['nav']['broken']} broken links"
                     if r.get("nav") else ""), "",
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
        g = r.get("grade") or {}
        if g.get("path"):
            lines += ["", "### Grade", "",
                      f"- path {g['path']}/5: {g.get('path_note', '')}",
                      f"- clarity {g.get('clarity')}/5: {g.get('clarity_note', '')}",
                      f"- key: {g.get('key')}" + (f" — {g['key_note']}" if g.get("key_note") else "")]
        if r.get("key"):
            lines += ["", "### Key (reference)", "", r["key"]]
        if r["debrief"]["text"]:
            lines += ["", "### Debrief", "", r["debrief"]["text"]]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m coscience.wiki_probe", description=__doc__.split("\n\n")[0])
    ap.add_argument("--program", required=True)
    ap.add_argument("--questions", help="questions file, markdown list or JSON "
                                        "(default: programs/<id>/wiki-questions.md)")
    ap.add_argument("--bundle", help="wiki bundle to probe (default: the program's live wiki)")
    ap.add_argument("--mode", choices=("agent", "nav"), default="agent")
    ap.add_argument("--model", default="", help="model to answer with (default: the program's planner model)")
    ap.add_argument("--grade", default="", metavar="MODEL", help="grade each answer with this model")
    ap.add_argument("--no-debrief", action="store_true")
    ap.add_argument("--jobs", type=int, default=1, help="questions answered in parallel")
    ap.add_argument("--repo", default=os.environ.get("COSCIENCE_REPO", "."), help="substrate repo")
    ap.add_argument("--out", help="output dir (default: ~/.cache/coscience/wiki-probe/<id>/<stamp>)")
    args = ap.parse_args(argv)

    from coscience.substrate import Substrate
    substrate = Substrate(Path(args.repo))
    program = substrate.load_program(args.program)
    bundle = Path(args.bundle) if args.bundle else substrate.program_dir(args.program) / "wiki"
    qfile = Path(args.questions) if args.questions else substrate.program_dir(args.program) / "wiki-questions.md"
    if not qfile.is_file():
        print(f"no questions file at {qfile}", file=sys.stderr)
        return 2
    questions = load_questions(qfile.read_text())
    if not questions:
        print(f"{qfile} has no questions to ask", file=sys.stderr)
        return 2
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = Path(args.out) if args.out else Path.home() / ".cache" / "coscience" / "wiki-probe" / args.program / stamp
    summary = probe(args.program, questions, bundle, out, model=args.model or program.pm_model,
                    mode=args.mode, debrief=not args.no_debrief, grade_model=args.grade,
                    jobs=args.jobs)
    total = sum(float(r["answer"]["cost"] or 0) + float(r["debrief"]["cost"] or 0)
                + float((r.get("grade") or {}).get("cost") or 0) for r in summary["questions"])
    print(f"{len(questions)} questions probed, {_money(total)} — report: {out / 'report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
