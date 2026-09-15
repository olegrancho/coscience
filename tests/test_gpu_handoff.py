"""O3: the worker agent is told which cards its sprint holds."""
from pathlib import Path

from coscience.claude_executor import build_instructions
from coscience.dispatcher import _WorkerSlots
from coscience.executor import ExecutionContext
from coscience.ledger import Ledger
from coscience.models import Sprint, SprintStatus
from coscience.resources import ResourcePool
from coscience.worker import Worker

SCRATCH = Path("/tmp/s1/scratchpad.md")


def _sprint():
    return Sprint(id="sp1", status=SprintStatus.APPROVED, goals="train", plan=["train"])


def test_whole_cards_are_named_with_cuda_visible_devices():
    text = build_instructions(_sprint(), ExecutionContext(gpu_devices=[0, 2]), SCRATCH)
    assert "## GPUs for this sprint" in text
    assert "CUDA_VISIBLE_DEVICES=0,2" in text
    assert "whole" in text


def test_a_share_states_the_vram_it_may_use():
    text = build_instructions(_sprint(), ExecutionContext(gpu_devices=[1], gpu_vram_gb=8.0),
                              SCRATCH)
    assert "CUDA_VISIBLE_DEVICES=1" in text
    assert "8 GB" in text


def test_no_gpu_section_without_cards():
    assert "CUDA_VISIBLE_DEVICES" not in build_instructions(_sprint(), ExecutionContext(), SCRATCH)
    assert "CUDA_VISIBLE_DEVICES" not in build_instructions(_sprint(), None, SCRATCH)


def test_the_dispatcher_slot_handle_reports_the_leases_cards(tmp_path):
    led = Ledger(ResourcePool.from_dict({"gpus": [{"vram_gb": 24}]}), tmp_path / "leases.json")
    led.load()
    led.acquire("sp1", {"gpu_vram_gb": 8}, now=0.0, ttl=60.0)
    slots = _WorkerSlots(led)
    assert slots.gpus("sp1") == ([0], 8.0)
    assert slots.gpus("nobody") == ([], None)


def test_the_worker_puts_the_cards_into_the_agent_context(substrate):
    class Slots:
        def release(self, sprint_id):
            pass

        def acquire(self, sprint_id):
            return True

        def gpus(self, sprint_id):
            return [3], None

        def host(self, sprint_id):
            return {"name": "local", "ssh": "", "run_root": "", "facts": "", "notes": ""}

        def ssh_for(self, host_name):
            return ""

    substrate.save_sprint(_sprint())
    worker = Worker(substrate, agent=None, slots=Slots())
    ctx = worker._build_context(substrate.load_sprint("sp1"))
    assert ctx.gpu_devices == [3]
    assert ctx.gpu_vram_gb is None


def test_progress_remembers_the_cards_an_agent_was_told(substrate):
    prog = substrate.load_progress("sp1")
    assert prog.gpu_devices == []
    prog.gpu_devices = [1]
    substrate.save_progress(prog)
    assert substrate.load_progress("sp1").gpu_devices == [1]


def test_a_regranted_sprint_with_a_live_job_keeps_its_cards(substrate, monkeypatch):
    from tests.conftest import FakeAgent
    from coscience.dispatcher import Dispatcher
    from coscience.models import BeatOutcome
    from coscience.scheduler import SchedulerPolicy

    orph = Sprint(id="ORPH", status=SprintStatus.EXECUTING, goals="g", plan=["work"],
                  resources_required={"gpu": 1.0})
    substrate.save_sprint(orph)
    prog = substrate.load_progress("ORPH")
    prog.job_token, prog.job_out = "1:1", "j.out"
    prog.job_started_at, prog.job_max_seconds = 0.0, 9e18
    prog.gpu_devices = [1]
    substrate.save_progress(prog)
    pool = ResourcePool.from_dict({"gpus": [{"vram_gb": 24}, {"vram_gb": 24}]})
    disp = Dispatcher(substrate, FakeAgent(), pool, SchedulerPolicy(aging_interval=0.0))
    # Only the grant is under test; keep the beat from touching the fake job.
    monkeypatch.setattr(disp.worker, "run_sprint_beat", lambda sprint: BeatOutcome.PROGRESSED)

    disp.run_one_cycle(now=0.0)

    disp.ledger.load()
    assert disp.ledger.lease_for("ORPH").gpu_devices == [1]
