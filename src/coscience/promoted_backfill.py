"""Recover which idea each past sprint was promoted from (K4), for sprints promoted
before `from_idea` was recorded.

    python -m coscience.promoted_backfill            # report what it would record
    python -m coscience.promoted_backfill --apply    # record it and commit

Two sources in the substrate's git history:

- a person's promotion commits "program P: idea X promoted to sprint Y" — exact;
- the planner's promotions left no record, but the commit that applied one removed
  the idea from ideas.md and added the sprint. A removed idea is matched to a sprint
  added in the same commit by one of the idea's lineage edges reappearing on the
  sprint (a promotion moves them), by the sprint naming the idea's id, or else by
  wording: the share of the idea's content words the sprint's title and goals reuse.
  On one substrate's history, promotions people recorded scored 0.65-0.92 and
  pruned-plus-unrelated pairs up to 0.60, so a wording match needs 0.70 and a clear
  lead (0.15) over the next idea, and an idea claimed by two sprints links neither:
  a missing link is better than one to the wrong sprint.

A sprint that already records `from_idea` is never changed."""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

from coscience.frontmatter_io import parse

STOP = set("a an the of on in to for and or with from by is are be it its this that as at "
           "one each every all not no into than then when which what run runs".split())
MIN_WORDING, MIN_LEAD = 0.70, 0.15

PROMOTED = re.compile(r"^program (\S+): idea (\S+) promoted to sprint (\S+)$")
SPRINT_MD = re.compile(r"^sprints/([^/]+)/sprint\.md$")


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True,
                          capture_output=True, text=True).stdout


def _show(root: Path, rev: str, path: str) -> str | None:
    r = subprocess.run(["git", "-C", str(root), "show", f"{rev}:{path}"],
                       capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


def _ideas_at(root: Path, rev: str, program: str) -> dict[str, dict]:
    text = _show(root, rev, f"programs/{program}/ideas.md")
    if not text:
        return {}
    try:
        fm, _ = parse(text)
    except Exception:
        return {}
    return {str(n.get("id")): n for n in fm.get("ideas") or [] if isinstance(n, dict) and n.get("id")}


def _edge_ids(node: dict) -> set[str]:
    return {str(e.get("id")) for e in node.get("edges") or [] if isinstance(e, dict) and e.get("id")}


def _words(text: str) -> set[str]:
    return {w[:5] for w in re.findall(r"[a-z]{3,}", text.lower()) if w not in STOP}


def wording(idea: str, sprint: str) -> float:
    """Share of content words two texts have in common, over the shorter of them."""
    a, b = _words(idea), _words(sprint)
    return len(a & b) / max(1, min(len(a), len(b)))


def from_messages(root: Path) -> dict[str, tuple[str, str]]:
    """sprint id -> (program, idea id), from a person's promotion commits."""
    out = {}
    for line in _git(root, "log", "--format=%s", "--grep=promoted to sprint").splitlines():
        m = PROMOTED.match(line.strip())
        if m:
            out[m.group(3)] = (m.group(1), m.group(2))
    return out


def from_planner(root: Path, program: str) -> dict[str, str]:
    """sprint id -> idea id, from the commits that applied the planner's promotions."""
    claims: dict[str, list[tuple[float, str]]] = {}   # idea -> [(strength, sprint)]
    revs = _git(root, "rev-list", "--reverse", "HEAD", "--", f"programs/{program}/ideas.md").split()
    for rev in revs:
        added = []
        for line in _git(root, "diff-tree", "--no-commit-id", "--name-status", "-r", "--root",
                         rev, "--", "sprints/").splitlines():
            status, _, path = line.partition("\t")
            m = SPRINT_MD.match(path)
            if status == "A" and m:
                added.append(m.group(1))
        if not added:
            continue
        before, after = _ideas_at(root, f"{rev}^", program), _ideas_at(root, rev, program)
        removed = {i: n for i, n in before.items() if i not in after}
        if not removed:
            continue
        for sid in added:
            text = _show(root, rev, f"sprints/{sid}/sprint.md") or ""
            try:
                fm, body = parse(text)
            except Exception:
                continue
            if fm.get("program") != program:
                continue
            edges = _edge_ids(fm)
            words = " ".join(str(fm.get(k) or "") for k in ("title", "goals", "rationale", "summary"))
            sure = [i for i, n in removed.items()
                    if (edges & _edge_ids(n)) or re.search(rf"\b{re.escape(i)}\b", words)]
            if len(sure) == 1:
                claims.setdefault(sure[0], []).append((2.0, sid))
                continue
            told = " ".join(str(fm.get(k) or "") for k in ("title", "goals"))
            ranked = sorted(((wording(str(n.get("text") or ""), told), i) for i, n in removed.items()),
                            reverse=True)
            best, idea = ranked[0]
            runner = ranked[1][0] if len(ranked) > 1 else 0.0
            if best >= MIN_WORDING and best - runner >= MIN_LEAD:
                claims.setdefault(idea, []).append((best, sid))
    # An idea leaves the pool once; two sprints claiming it means one guess is wrong.
    return {c[0][1]: idea for idea, c in claims.items() if len(c) == 1}


def plan(root: Path, substrate) -> list[tuple[str, str, str]]:
    """(sprint id, idea id, source) for every sprint that should record its idea."""
    sprints = {s.id: s for s in substrate.iter_sprints()}
    found: dict[str, tuple[str, str]] = {}
    for sid, (_program, idea) in from_messages(root).items():
        found[sid] = (idea, "promotion commit")
    for program in sorted({s.program for s in sprints.values() if s.program}):
        for sid, idea in from_planner(root, program).items():
            found.setdefault(sid, (idea, "planner commit"))
    return [(sid, idea, src) for sid, (idea, src) in sorted(found.items())
            if sid in sprints and not sprints[sid].from_idea]


def main(argv: list[str] | None = None) -> int:
    from coscience.substrate import Substrate
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true", help="record and commit (default: report only)")
    ap.add_argument("--repo", default=os.environ.get("COSCIENCE_REPO", "."),
                    help="substrate root (default: COSCIENCE_REPO)")
    args = ap.parse_args(argv)
    root = Path(args.repo)
    substrate = Substrate(root)
    rows = plan(root, substrate)
    for sid, idea, src in rows:
        print(f"{sid}  <-  idea {idea}  ({src})")
    print(f"{len(rows)} sprint(s) {'recorded' if args.apply else 'would record'} their idea")
    if args.apply and rows:
        for sid, idea, _src in rows:
            s = substrate.load_sprint(sid)
            s.from_idea = idea
            substrate.save_sprint(s)
        substrate.commit(f"backfill: {len(rows)} sprint(s) record the idea they were promoted from")
    return 0


if __name__ == "__main__":
    sys.exit(main())
