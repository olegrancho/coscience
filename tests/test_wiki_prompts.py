from pathlib import Path

from coscience import wiki_prompts, wiki_store
from coscience.models import Program

BUNDLE = Path("/repo/programs/p1/wiki")
RUN = Path("/repo/programs/p1/.wiki/runs/r0003")
PROGRAM = Program(id="p1", title="Abiogenesis", goals="Find the takeoff regime.")

OBJECTS = [
    (wiki_store.WikiObject(
        oid="result:r1", kind="result", title="Beat record 1572", at=100.0,
        paths=[Path("/repo/results/r1.md")], resource="/results/r1.md",
        slug="sources/result-r1.md"), "sha256:aaa"),
    (wiki_store.WikiObject(
        oid="artifact:fig@v2", kind="artifact", title="Takeoff vs hydrolysis", at=200.0,
        paths=[Path("/repo/programs/p1/artifacts/fig/v2")],
        resource="/programs/p1/artifacts/fig/v2",
        slug="sources/artifact-fig-v2.md"), "sha256:bbb"),
]


def test_ingest_prompt_carries_every_object_with_paths_and_hashes():
    text = wiki_prompts.render_ingest(PROGRAM, BUNDLE, OBJECTS, RUN)
    for obj, digest in OBJECTS:
        assert obj.oid in text
        assert obj.title in text
        assert str(obj.paths[0]) in text
        assert digest in text
        assert obj.slug in text
        assert obj.resource in text


def test_ingest_prompt_paths_are_absolute():
    text = wiki_prompts.render_ingest(PROGRAM, BUNDLE, OBJECTS, RUN)
    assert "/repo/results/r1.md" in text
    assert "results/r1.md\n" not in text.replace("/repo/results/r1.md", "")


def test_ingest_prompt_states_the_prohibitions():
    text = wiki_prompts.render_ingest(PROGRAM, BUNDLE, OBJECTS, RUN)
    assert "do not compute" in text.lower()
    # The escape guard diffs the working tree, so a run that commits its own
    # writes makes them invisible to it.
    for forbidden in ("git commit", "git add", "git stash", "git checkout", "git reset"):
        assert forbidden in text
    assert str(BUNDLE) in text
    assert "# Human notes" in text
    assert "report.json" in text
    assert "log.md" in text
    assert "QUESTIONS.md" in text


def test_ingest_prompt_defines_what_objects_means():
    # _collect trusts this field to decide what is marked ingested, so the prompt
    # has to say it means "the ids from this batch you finished" and nothing else.
    text = wiki_prompts.render_ingest(PROGRAM, BUNDLE, OBJECTS, RUN)
    # Each phrase has to sit on one line of the prompt; these substrings must not
    # straddle a wrap, or the assertion fails on formatting rather than meaning.
    assert "`objects` is the list of object ids" in text
    assert "only the ones you finished" in text
    assert "never put page paths in `objects`" in text


def test_ingest_prompt_carries_the_four_pass_protocol():
    text = wiki_prompts.render_ingest(PROGRAM, BUNDLE, OBJECTS, RUN)
    for pass_name in ("structure map", "claim", "relationship", "contradiction"):
        assert pass_name in text.lower()


def test_ingest_prompt_carries_the_program_context():
    text = wiki_prompts.render_ingest(PROGRAM, BUNDLE, OBJECTS, RUN)
    assert "Abiogenesis" in text
    assert "Find the takeoff regime." in text


def test_lint_prompt_carries_the_machine_report():
    text = wiki_prompts.render_lint(PROGRAM, BUNDLE, "concepts/a.md: rel/no-source", RUN)
    assert "rel/no-source" in text
    assert str(BUNDLE) in text
    assert "never delete" in text.lower()


def test_kickoff_points_at_instructions_md():
    text = wiki_prompts.kickoff("ingest", RUN)
    assert str(RUN / "instructions.md") in text
    assert len(text) < 600


def test_render_is_pure_and_stable():
    a = wiki_prompts.render_ingest(PROGRAM, BUNDLE, OBJECTS, RUN)
    b = wiki_prompts.render_ingest(PROGRAM, BUNDLE, OBJECTS, RUN)
    assert a == b
