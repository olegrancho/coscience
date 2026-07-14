from coscience.substrate import Substrate
from coscience.models import ProgressState


def test_job_fields_roundtrip(tmp_path):
    sub = Substrate(tmp_path)
    p = ProgressState(sprint_id="s1", job_token="123:456", job_out="out.log",
                      job_note="train", job_started_at=10.0, job_expected_seconds=100.0,
                      job_next_wake=110.0, job_max_seconds=200.0, assess_reason="")
    sub.save_progress(p)
    got = sub.load_progress("s1")
    assert got.job_token == "123:456" and got.job_out == "out.log"
    assert got.job_next_wake == 110.0 and got.job_max_seconds == 200.0
    assert got.job_note == "train"


def test_old_progress_defaults_empty_job(tmp_path):
    sub = Substrate(tmp_path)
    sub.save_progress(ProgressState(sprint_id="s2"))
    got = sub.load_progress("s2")
    assert got.job_token == "" and got.job_next_wake == 0.0 and got.assess_reason == ""
