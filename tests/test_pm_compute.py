"""N1: the PM is told the compute pool and to request only what a sprint needs."""
import json

from coscience.models import Program
from coscience.pm_agent import _context_payload, gather_context
from coscience.pm_claude import render_compute, render_prompt
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


def test_the_prompt_states_the_pool_and_the_sizing_rule():
    ctx = PMContext(program_id="p1", goals="g", cycle=0,
                    compute_capacity={"cpu": 24.0, "gpu": 1.0}, compute_leased={"cpu": 24.0})
    block = render_compute(ctx)
    assert "cpu 24, gpu 1 in total" in block
    assert "hold cpu 24 of it" in block
    assert "Request only what the work actually needs" in block
    assert block in render_prompt(ctx)


def test_an_undeclared_pool_still_gets_the_sizing_rule():
    assert "Request only what" in render_compute(PMContext(program_id="p1", goals="g", cycle=0))
