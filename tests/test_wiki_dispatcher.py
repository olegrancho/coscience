from coscience.dispatcher import Dispatcher
from coscience.models import Program, ProgramStatus
from coscience.resources import ResourcePool
from coscience.scheduler import SchedulerPolicy
from tests.test_wiki_beat import FakeWikiAgent


def _dispatcher(substrate, agent, wiki_agent):
    return Dispatcher(substrate, agent, ResourcePool(), SchedulerPolicy(),
                      wiki_agent=wiki_agent)


def test_cycle_runs_the_wiki_beat_for_each_active_program(substrate, agent, monkeypatch):
    substrate.save_program(Program(id="p1", title="P1", goals="g"))
    substrate.save_program(Program(id="p2", title="P2", goals="g",
                                   status=ProgramStatus.CLOSED))
    seen = []

    import coscience.dispatcher as dmod
    monkeypatch.setattr(dmod.wiki, "beat",
                        lambda sub, prog, now, ag, **kw: (seen.append(prog.id), "wiki: x")[1])
    report = _dispatcher(substrate, agent, FakeWikiAgent()).run_one_cycle()
    assert seen == ["p1"]
    assert report.wiki == ["wiki: x"]


def test_empty_beat_lines_are_dropped(substrate, agent):
    substrate.save_program(Program(id="p1", title="P1", goals="g"))
    report = _dispatcher(substrate, agent, FakeWikiAgent()).run_one_cycle()
    assert report.wiki == []          # nothing pending, so the beat says nothing


def test_a_raising_wiki_beat_does_not_break_the_cycle(substrate, agent, monkeypatch):
    substrate.save_program(Program(id="p1", title="P1", goals="g"))

    def boom(*a, **kw):
        raise RuntimeError("wiki exploded")

    import coscience.dispatcher as dmod
    monkeypatch.setattr(dmod.wiki, "beat", boom)
    report = _dispatcher(substrate, agent, FakeWikiAgent()).run_one_cycle()
    assert report.wiki == ["wiki: error — wiki exploded"]
