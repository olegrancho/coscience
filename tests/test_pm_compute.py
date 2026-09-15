"""N1: the PM is told the compute pool and to request only what a sprint needs."""
import json

from coscience.models import Program
from coscience.pm_agent import _context_payload, gather_context
from coscience.pm_claude import parse_response, render_compute, render_prompt
from coscience.pm_reasoner import PMContext


def test_the_context_carries_capacity_and_what_is_leased_without_platform_keys(substrate):
    substrate.save_program(Program(id="p1", title="P", goals="g"))
    cos = substrate.repo_root / ".coscience"
    cos.mkdir(parents=True, exist_ok=True)
    (cos / "resources.yaml").write_text("cpu: 24\ngpu: 1\nworkers: 3\nhousekeepers: 2\n")
    (cos / "leases.json").write_text(json.dumps([{
        "id": "l1", "sprint_id": "p2-c60", "amounts": {"cpu": 24.0, "workers": 1.0},
        "granted_at": 0.0, "expires_at": 1e12, "priority": 0, "preemptible": True}]))

    ctx = gather_context(substrate, "p1")

    assert ctx.compute_capacity == {"cpu": 24.0, "gpu": 1.0}
    assert ctx.compute_leased == {"cpu": 24.0}
    # Not a trigger: a lease turning over must not wake every program's PM.
    assert "compute" not in json.dumps(_context_payload(ctx))
    assert ctx.compute_hosts == [
        {"name": "local", "capacity": {"cpu": 24.0}, "gpus": [None], "held": {"cpu": 24.0}, "closed": ""}]


def test_the_prompt_states_each_host_and_the_sizing_rule():
    ctx = PMContext(program_id="p1", goals="g", cycle=0, compute_hosts=[
        {"name": "local", "capacity": {"cpu": 24.0, "memory_gb": 62.0}, "gpus": [24.0, None],
         "held": {"cpu": 8.0, "gpu": 1.0}}])
    block = render_compute(ctx)
    assert ("- local: cpu 24, memory_gb 62; 2 GPU card(s): 24 GB, VRAM not declared"
            " — running sprints hold cpu 8, gpu 1") in block
    assert "A sprint runs on ONE host" in block
    assert "`gpu_vram_gb`" in block
    assert "Request only what the work actually needs" in block
    assert block in render_prompt(ctx)


def test_an_undeclared_pool_still_gets_the_sizing_rule():
    assert "Request only what" in render_compute(PMContext(program_id="p1", goals="g", cycle=0))


def test_a_program_is_told_only_the_hosts_it_may_use(substrate, every_host_placeable):
    substrate.save_program(Program(id="p2", title="P2", goals="g"))
    substrate.save_program(Program(id="p5", title="P5", goals="g"))
    cos = substrate.repo_root / ".coscience"
    cos.mkdir(parents=True, exist_ok=True)
    (cos / "resources.yaml").write_text(
        "cpu: 24\ngpu: 1\nworkers: 3\n"
        "hosts:\n  remote1:\n    ssh: remote1\n    programs: [p2]\n    capacity: {cpu: 28}\n")
    (cos / "leases.json").write_text(json.dumps([{
        "id": "l1", "sprint_id": "p2-c1", "amounts": {"cpu": 28.0, "workers": 1.0},
        "granted_at": 0.0, "expires_at": 1e12, "priority": 0, "preemptible": True,
        "host": "remote1"}]))

    p2, p5 = gather_context(substrate, "p2"), gather_context(substrate, "p5")

    assert p2.compute_capacity == {"cpu": 52.0, "gpu": 1.0}
    assert p2.compute_leased == {"cpu": 28.0}
    assert p5.compute_capacity == {"cpu": 24.0, "gpu": 1.0}
    assert p5.compute_leased == {}          # remote1's lease is on a host p5 never gets
    assert [h["name"] for h in p2.compute_hosts] == ["local", "remote1"]
    assert [h["name"] for h in p5.compute_hosts] == ["local"]


def test_undeclared_cards_are_named_as_whole_cards_only():
    ctx = PMContext(program_id="p1", goals="g", cycle=0, compute_hosts=[
        {"name": "local", "capacity": {"cpu": 4.0}, "gpus": [None], "held": {}}])
    assert "1 GPU card(s), VRAM not declared (whole cards only)" in render_compute(ctx)


def test_a_pool_with_nothing_declared_gets_the_plain_sizing_rule():
    ctx = PMContext(program_id="p1", goals="g", cycle=0, compute_hosts=[
        {"name": "local", "capacity": {}, "gpus": [], "held": {}}])
    assert render_compute(ctx).startswith("COMPUTE: no capacity is declared")


def test_a_proposal_may_ask_to_span_hosts():
    out = parse_response('{"proposals": [{"suffix": "x", "goals": "g", "plan": ["a"],'
                         ' "distributed": true}]}')
    assert out.proposals[0].distributed is True
    out = parse_response('{"proposals": [{"suffix": "y", "goals": "g", "plan": ["a"]}]}')
    assert out.proposals[0].distributed is False


def test_a_proposal_with_distributed_creates_a_distributed_sprint(substrate):
    from coscience.pm_agent import pm_beat
    from coscience.pm_reasoner import FakeReasoner, PMCycleOutput, ProposedSprint

    substrate.save_program(Program(id="p1", title="C", goals="cure"))
    out = PMCycleOutput(proposals=[ProposedSprint(
        suffix="x", goals="g", plan=["a"], distributed=True)])
    pm_beat(substrate, "p1", FakeReasoner([out]))
    assert substrate.load_sprint("p1-c0-x").distributed is True


def test_a_sprint_edit_may_set_distributed(substrate):
    from coscience.models import Sprint, SprintStatus
    from coscience.pm_agent import pm_beat
    from coscience.pm_reasoner import FakeReasoner, PMCycleOutput

    substrate.save_program(Program(id="p1", title="C", goals="cure"))
    substrate.save_sprint(Sprint(id="p1-a", status=SprintStatus.QUEUED, goals="g",
                                 plan=["a"], program="p1"))
    out = PMCycleOutput(report="r", sprint_edits=[
        {"sprint_id": "p1-a", "distributed": True}])
    pm_beat(substrate, "p1", FakeReasoner([out]), force=True)
    assert substrate.load_sprint("p1-a").distributed is True

    out2 = PMCycleOutput(report="r", sprint_edits=[
        {"sprint_id": "p1-a", "distributed": "yes"}])
    pm_beat(substrate, "p1", FakeReasoner([out2]), force=True)
    assert substrate.load_sprint("p1-a").distributed is True   # non-boolean -> unchanged


def test_a_host_line_names_the_keys_it_cannot_give():
    ctx = PMContext(program_id="p1", goals="g", cycle=0, compute_hosts=[
        {"name": "local", "capacity": {"cpu": 24.0}, "gpus": [None], "held": {}}])
    block = render_compute(ctx)
    assert "— not declared here, never request: memory_gb, gpu_vram_gb" in block
    assert "Request only keys a host lists" in block


def test_a_host_without_cards_rules_out_both_gpu_keys():
    ctx = PMContext(program_id="p1", goals="g", cycle=0, compute_hosts=[
        {"name": "local", "capacity": {"cpu": 8.0, "memory_gb": 32.0}, "gpus": [], "held": {}}])
    assert "never request: gpu, gpu_vram_gb" in render_compute(ctx)


def test_a_fully_declared_host_rules_out_nothing():
    ctx = PMContext(program_id="p1", goals="g", cycle=0, compute_hosts=[
        {"name": "local", "capacity": {"cpu": 8.0, "memory_gb": 32.0}, "gpus": [24.0],
         "held": {}}])
    assert "never request" not in render_compute(ctx)


def test_a_staged_proposal_survives_an_unknown_field_on_reload(substrate):
    # Older code reading a staging file a newer version wrote (or with a future
    # field) must not TypeError on ProposedSprint(**p) — it should keep the known
    # fields and ignore the rest.
    from coscience.pm_agent import _staging_path, write_staging, read_staging
    from coscience.pm_reasoner import PMCycleOutput, ProposedSprint

    substrate.save_program(Program(id="p1", title="P", goals="g"))
    out = PMCycleOutput(proposals=[ProposedSprint(suffix="x", goals="g", plan=["a"])])
    write_staging(substrate, "p1", 3, out)

    path = _staging_path(substrate, "p1")
    data = json.loads(path.read_text())
    data["proposals"][0]["gpu_devices"] = [0]
    path.write_text(json.dumps(data))

    staged = read_staging(substrate, "p1")
    assert staged.output.proposals[0].suffix == "x"


def test_a_host_that_takes_no_new_work_says_so():
    ctx = PMContext(program_id="p1", goals="g", cycle=0, compute_hosts=[
        {"name": "big", "capacity": {"cpu": 16.0}, "gpus": [], "held": {}, "closed": "draining"}])
    assert "big" in render_compute(ctx) and "takes no new work (draining)" in render_compute(ctx)
