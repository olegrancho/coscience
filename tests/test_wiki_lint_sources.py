import subprocess

from coscience import wiki_lint, wiki_okf, wiki_store
from coscience.models import Program, Result, Sprint, SprintStatus


def _src(path="sources/result-r1.md", **kw):
    kw.setdefault("type", "Source")
    kw.setdefault("title", "Sprint s1 result")
    kw.setdefault("body", "# Summary\n\n" + "x" * 400)
    kw.setdefault("graph_excluded", True)
    return wiki_okf.Page(path=path, **kw)


def _rules(findings, prefix=""):
    return sorted({f.rule for f in findings if f.rule.startswith(prefix)})


def test_hash_drift_is_an_error():
    page = _src(extra={"origin": "result:r1", "origin_hash": "sha256:old"})
    findings = wiki_lint.lint([page], objects={"result:r1": "sha256:new"})
    drift = [f for f in findings if f.rule == "src/hash-drift"]
    assert drift and drift[0].severity == "error"


def test_matching_hash_is_clean():
    page = _src(extra={"origin": "result:r1", "origin_hash": "sha256:same"})
    findings = wiki_lint.lint([page], objects={"result:r1": "sha256:same"})
    assert _rules(findings, "src/") == []


def test_missing_origin_object_is_an_error():
    page = _src(extra={"origin": "result:gone", "origin_hash": "sha256:x"})
    findings = wiki_lint.lint([page], objects={"result:r1": "sha256:x"})
    assert "src/missing" in _rules(findings)


def test_source_checks_are_skipped_when_objects_is_none():
    page = _src(extra={"origin": "result:r1", "origin_hash": "sha256:old"})
    assert _rules(wiki_lint.lint([page]), "src/") == []


def test_a_concept_titled_like_a_source_is_an_error():
    source = _src(title="Sprint p3-c14 result")
    concept = wiki_okf.Page(path="concepts/sprint-p3-c14-result.md", type="Concept",
                            title="Sprint p3-c14 result", body="x" * 400)
    assert "src/is-concept" in _rules(wiki_lint.lint([source, concept]))


def test_removed_human_notes_is_an_error():
    page = wiki_okf.Page(path="concepts/a.md", type="Concept", title="A",
                         body="# Definition\n\n" + "x" * 400)
    previous = {"concepts/a.md": "# Definition\n\nold\n\n# Human notes\n\nOleg: careful.\n"}
    findings = wiki_lint.lint([page], previous=previous)
    removed = [f for f in findings if f.rule == "human-notes/removed"]
    assert removed and removed[0].severity == "error"


def test_kept_human_notes_is_clean():
    body = "# Definition\n\n" + "x" * 400 + "\n\n# Human notes\n\nOleg: careful.\n"
    page = wiki_okf.Page(path="concepts/a.md", type="Concept", title="A", body=body)
    previous = {"concepts/a.md": "# Human notes\n\nOleg: careful.\n"}
    assert "human-notes/removed" not in _rules(wiki_lint.lint([page], previous=previous))


def test_stable_without_verification_is_info():
    page = wiki_okf.Page(path="concepts/a.md", type="Concept", title="A",
                         status="stable", body="x" * 400)
    findings = wiki_lint.lint([page])
    trust = [f for f in findings if f.rule == "trust/unverified-stable"]
    assert trust and trust[0].severity == "info"


def test_stable_with_verification_is_clean():
    page = wiki_okf.Page(path="concepts/a.md", type="Concept", title="A",
                         status="stable", body="x" * 400,
                         verified=[{"by": "human:oleg", "at": "2026-08-21T09:00:00Z"}])
    assert "trust/unverified-stable" not in _rules(wiki_lint.lint([page]))


def test_run_lint_reads_the_bundle_and_the_real_objects(wiki_bundle):
    substrate, pid = wiki_bundle
    substrate.save_sprint(Sprint(id="s1", status=SprintStatus.DONE, goals="g", program=pid))
    substrate.save_result(Result(id="r1", sprint="s1", summary="s", completed_at=1.0))
    obj = wiki_store.program_objects(substrate, pid)[0]
    wiki_store.write_page(substrate, pid, _src(
        extra={"origin": obj.oid, "origin_hash": "sha256:stale"}))
    findings, fixed = wiki_lint.run_lint(substrate, pid)
    assert fixed == 0
    assert "src/hash-drift" in _rules(findings)


def test_run_lint_with_fix_writes_pages_back(wiki_bundle):
    substrate, pid = wiki_bundle
    wiki_store.write_page(substrate, pid, wiki_okf.Page(
        path="concepts/b.md", type="Concept", title="B", body="x" * 400))
    wiki_store.write_page(substrate, pid, wiki_okf.Page(
        path="concepts/a.md", type="Concept", title="A",
        body="see [[b]]\n\n" + "x" * 400))
    findings, fixed = wiki_lint.run_lint(substrate, pid, fix=True)
    assert fixed == 1
    text = (wiki_store.bundle_dir(substrate, pid) / "concepts" / "a.md").read_text()
    assert "[b](/concepts/b.md)" in text
    assert "link/wikilink" not in _rules(wiki_lint.run_lint(substrate, pid)[0])


def test_previous_bodies_reads_head(wiki_bundle):
    # the `substrate` fixture is a bare tmp_path with no git repo, so this test
    # makes one — which is also why previous_bodies returns {} everywhere else
    substrate, pid = wiki_bundle
    root = str(substrate.repo_root)
    subprocess.run(["git", "-C", root, "init", "-q"], check=True)
    subprocess.run(["git", "-C", root, "config", "user.email", "t@t.test"], check=True)
    subprocess.run(["git", "-C", root, "config", "user.name", "t"], check=True)
    wiki_store.write_page(substrate, pid, wiki_okf.Page(
        path="concepts/a.md", type="Concept", title="A",
        body="# Human notes\n\nkeep me\n"))
    subprocess.run(["git", "-C", str(substrate.repo_root), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(substrate.repo_root), "commit", "-q", "-m", "x"],
                   check=True)
    bodies = wiki_store.previous_bodies(substrate, pid)
    assert "keep me" in bodies["concepts/a.md"]


def test_previous_bodies_empty_without_git(substrate):
    # `substrate` (unlike `wiki_bundle`) is a bare tmp_path with no git repo. A
    # real page must exist so the function's per-page loop actually runs and
    # hits `git show` failing, rather than trivially returning {} on an empty
    # bundle regardless of git.
    substrate.save_program(Program(id="p9", title="P", goals="g"))
    wiki_store.ensure_bundle(substrate, "p9")
    wiki_store.write_page(substrate, "p9", wiki_okf.Page(
        path="concepts/a.md", type="Concept", title="A", body="x" * 400))
    assert wiki_store.previous_bodies(substrate, "p9") == {}


def test_footnote_definitions_in_human_notes_are_an_error():
    # The observed failure: markdown puts `[^id]: ...` last, the template ends with
    # `# Human notes`, so attributions land in the protected section. 12 of 15 pages
    # on the first real bundle had this.
    p = wiki_okf.Page(path="concepts/a.md", type="Concept", title="A",
                      body="# Definition\n\nx[^c1]\n\n# Human notes\n\n"
                           "[^c1]: sources/result-r7.md\n")
    rules = [f.rule for f in wiki_lint.lint([p])]
    assert "human-notes/footnote-definition" in rules


def test_a_genuine_human_note_is_not_flagged():
    p = wiki_okf.Page(path="concepts/a.md", type="Concept", title="A",
                      body="# Definition\n\nx\n\n# Human notes\n\nRe-check at 40 C.\n")
    rules = [f.rule for f in wiki_lint.lint([p])]
    assert "human-notes/footnote-definition" not in rules


def test_content_appearing_under_human_notes_is_flagged_against_the_previous_body():
    before = "# Definition\n\nx\n\n# Human notes\n\n"
    after = wiki_okf.Page(path="concepts/a.md", type="Concept", title="A",
                          body="# Definition\n\nx\n\n# Human notes\n\nthe agent wrote this\n")
    rules = [f.rule for f in wiki_lint.lint([after],
                                            previous={"concepts/a.md": before})]
    assert "human-notes/machine-written" in rules
