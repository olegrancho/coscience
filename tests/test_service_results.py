from coscience.models import Result
from coscience.substrate import Substrate


def test_iter_results_empty_when_no_dir(tmp_path):
    assert Substrate(tmp_path).iter_results() == []


def test_save_then_load_result(tmp_path):
    sub = Substrate(tmp_path)
    sub.save_result(Result(id="r1", sprint="sp1", summary="found X"))
    loaded = sub.load_result("r1")
    assert loaded == Result(id="r1", sprint="sp1", summary="found X")


def test_iter_results_sorted(tmp_path):
    sub = Substrate(tmp_path)
    sub.save_result(Result(id="r2", sprint="sp2", summary="b"))
    sub.save_result(Result(id="r1", sprint="sp1", summary="a"))
    assert [r.id for r in sub.iter_results()] == ["r1", "r2"]
