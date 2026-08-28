"""The PM's lightweight artifact verb: `adopt_artifacts` registers output that
already exists, without routing through a sprint or the sprint cap."""
from coscience import artifacts, pm_agent
from coscience.models import Program, Sprint, SprintStatus
from coscience.pm_agent import pm_beat, read_staging, write_staging
from coscience.pm_claude import parse_response
from coscience.pm_reasoner import FakeReasoner, PMCycleOutput


def _program(substrate, tmp_path):
    substrate.save_program(Program(id="p", title="P", goals="g",
                                   workdir=str(tmp_path)))


def test_reasoner_parses_adopt_artifacts():
    out = parse_response('{"report": "r", "adopt_artifacts": ['
                         '{"aid": "report", "title": "Dossier", "kind": "md",'
                         ' "files": ["REPORT.md"]}]}')
    assert out.adopt_artifacts == [{"aid": "report", "title": "Dossier",
                                   "kind": "md", "files": ["REPORT.md"]}]


def test_adopt_artifacts_survives_staging_roundtrip(substrate):
    out = PMCycleOutput(report="r", adopt_artifacts=[{"aid": "x", "content": "hi"}])
    write_staging(substrate, "p", 0, out)
    assert read_staging(substrate, "p").output.adopt_artifacts == [
        {"aid": "x", "content": "hi"}]


def test_adopt_registers_a_workdir_file_as_an_artifact(substrate, tmp_path):
    _program(substrate, tmp_path)
    (tmp_path / "REPORT.md").write_text("# Findings\n")
    out = PMCycleOutput(report="r", adopt_artifacts=[
        {"aid": "report", "title": "Closeout dossier", "kind": "md",
         "files": ["REPORT.md"], "note": "from c13"}])
    summary = pm_beat(substrate, "p", FakeReasoner([out]), now=1.0)
    art = substrate.load_artifact("p", "report")
    assert art.title == "Closeout dossier"
    assert art.current == "v1"
    assert (substrate.artifact_dir("p", "report") / "v1" / "REPORT.md").read_text() == "# Findings\n"
    assert summary["adopted"] == ["report"]


def test_adopt_writes_inline_content_stub(substrate, tmp_path):
    _program(substrate, tmp_path)
    out = PMCycleOutput(report="r", adopt_artifacts=[
        {"aid": "manuscript", "title": "Draft", "content": "# Draft\n"}])
    pm_beat(substrate, "p", FakeReasoner([out]), now=1.0)
    assert (substrate.artifact_dir("p", "manuscript") / "v1" / "manuscript.md").read_text() == "# Draft\n"


def test_adopt_ignores_files_outside_the_program_workdir(substrate, tmp_path):
    _program(substrate, tmp_path / "inside")
    (tmp_path / "inside").mkdir()
    (tmp_path / "secret.md").write_text("nope")
    out = PMCycleOutput(report="r", adopt_artifacts=[
        {"aid": "leak", "files": ["../secret.md"]}])
    summary = pm_beat(substrate, "p", FakeReasoner([out]), now=1.0)
    assert not (substrate.artifact_dir("p", "leak") / "meta.md").is_file()
    assert summary["adopted"] == []          # the rest of the cycle still applied


def test_adopt_does_not_consume_a_sprint_slot(substrate, tmp_path):
    _program(substrate, tmp_path)
    for n in range(pm_agent.MAX_PROPOSED):   # fill the review queue
        substrate.save_sprint(Sprint(id=f"p-old-{n}", status=SprintStatus.PROPOSED,
                                     goals="g", program="p"))
    (tmp_path / "out.md").write_text("x")
    out = PMCycleOutput(report="r", adopt_artifacts=[
        {"aid": "report", "files": ["out.md"]}])
    pm_beat(substrate, "p", FakeReasoner([out]), now=1.0)
    assert substrate.load_artifact("p", "report").current == "v1"


def test_adopt_skips_an_artifact_another_holder_is_editing(substrate, tmp_path):
    _program(substrate, tmp_path)
    artifacts.create_artifact(substrate, "p", "doc", "Doc", "md")
    artifacts.acquire_lock(substrate, "p", ["doc"], "chat", "chat:abc", 1.0)
    (tmp_path / "doc.md").write_text("x")
    out = PMCycleOutput(report="r", adopt_artifacts=[
        {"aid": "doc", "files": ["doc.md"]}])
    summary = pm_beat(substrate, "p", FakeReasoner([out]), now=2.0)
    assert summary["adopted"] == []
    assert substrate.load_artifact("p", "doc").current == ""    # untouched


def test_adopt_takes_a_sprint_directory_file_from_this_program(substrate, tmp_path):
    """The PM's own sprints write their output to <substrate>/sprints/<id>/, which is
    outside the program workdir. Adoption has to reach it or a finished figure/report
    can never become an artifact — the case the prompt calls "a report a completed
    sprint wrote"."""
    work = tmp_path / "work-p"                 # a real workdir, NOT the substrate root
    work.mkdir()
    substrate.save_program(Program(id="p", title="P", goals="g", workdir=str(work)))
    substrate.save_sprint(Sprint(id="p-c1-figure", program="p", title="Figure", goals="g",
                                 status=SprintStatus.DONE))
    sdir = substrate.sprint_dir("p-c1-figure")
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / "figure.png").write_text("fake-png-bytes")
    out = PMCycleOutput(report="r", adopt_artifacts=[
        {"aid": "fig", "title": "Toy figure", "kind": "figure",
         "files": ["sprints/p-c1-figure/figure.png"]}])
    summary = pm_beat(substrate, "p", FakeReasoner([out]), now=1.0)
    assert summary["adopted"] == ["fig"]
    assert (substrate.artifact_dir("p", "fig") / "v1" / "figure.png").read_text() == "fake-png-bytes"


def test_adopt_refuses_another_programs_sprint_output(substrate, tmp_path):
    """Reaching sprint dirs must not become a hole in program isolation."""
    work = tmp_path / "work-p"
    work.mkdir()
    substrate.save_program(Program(id="p", title="P", goals="g", workdir=str(work)))
    substrate.save_program(Program(id="other", title="O", goals="g",
                                   workdir=str(tmp_path / "work-other")))
    substrate.save_sprint(Sprint(id="other-c1", program="other", title="Theirs", goals="g",
                                 status=SprintStatus.DONE))
    sdir = substrate.sprint_dir("other-c1")
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / "secret.md").write_text("theirs")
    out = PMCycleOutput(report="r", adopt_artifacts=[
        {"aid": "leak", "files": ["sprints/other-c1/secret.md"]}])
    summary = pm_beat(substrate, "p", FakeReasoner([out]), now=1.0)
    assert summary["adopted"] == []
    assert not (substrate.artifact_dir("p", "leak") / "meta.md").is_file()
    assert [k["id"] for k in summary["adopt_skipped"]] == ["leak"]


def test_a_rejected_adoption_is_reported_not_silently_dropped(substrate, tmp_path):
    """A bad source used to `continue` out of the loop, so the whole adoption vanished
    with no trace in the summary, the ledger or the beat log — indistinguishable from
    the PM never asking."""
    _program(substrate, tmp_path)
    out = PMCycleOutput(report="r", adopt_artifacts=[
        {"aid": "fig", "files": ["nope.png"]}])
    summary = pm_beat(substrate, "p", FakeReasoner([out]), now=1.0)
    assert summary["adopted"] == []
    skipped = summary["adopt_skipped"]
    assert [s["id"] for s in skipped] == ["fig"]
    assert "nope.png" in skipped[0]["why"]


def test_a_rejected_adoption_reaches_the_actions_ledger(substrate, tmp_path):
    _program(substrate, tmp_path)
    out = PMCycleOutput(report="r", adopt_artifacts=[
        {"aid": "fig", "files": ["nope.png"]}])
    pm_beat(substrate, "p", FakeReasoner([out]), now=1.0)
    assert "Adopt FAILED" in substrate.load_report("p")
