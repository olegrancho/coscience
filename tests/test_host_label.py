"""O12: a server's display name — what a human calls it, never what anything keys on."""
import pytest
import yaml

from coscience.service import Service

POOL = ("cpu: 4\nworkers: 4\nhosts:\n"
        "  big:\n    ssh: big\n    run_root: ~/runs\n    capacity: {cpu: 16}\n")


def _svc(tmp_path, text=POOL):
    cos = tmp_path / ".coscience"
    cos.mkdir(parents=True, exist_ok=True)
    (cos / "resources.yaml").write_text(text)
    return Service(tmp_path)


def _doc(tmp_path):
    return yaml.safe_load((tmp_path / ".coscience" / "resources.yaml").read_text())


def _host(status, name):
    return next(h for h in status["hosts"] if h["name"] == name)


def test_a_remote_server_takes_a_display_name_without_changing_its_key(tmp_path, every_host_placeable):
    svc = _svc(tmp_path)
    svc.update_host("big", label="The GPU box")
    assert _doc(tmp_path)["hosts"]["big"]["label"] == "The GPU box"
    status = svc.ledger_status()
    assert _host(status, "big")["label"] == "The GPU box"
    assert _host(status, "big")["name"] == "big"          # the key is untouched


def test_an_empty_display_name_goes_back_to_the_name(tmp_path, every_host_placeable):
    svc = _svc(tmp_path)
    svc.update_host("big", label="The GPU box")
    svc.update_host("big", label="")
    assert "label" not in _doc(tmp_path)["hosts"]["big"]
    assert _host(svc.ledger_status(), "big")["label"] == ""


def test_this_machine_takes_a_display_name_too(tmp_path):
    svc = _svc(tmp_path)
    svc.set_capacity({"cpu": 8, "workers": 3}, label="Avatar")
    assert _doc(tmp_path)["label"] == "Avatar"
    assert _host(svc.ledger_status(), "local")["label"] == "Avatar"


def test_a_capacity_edit_that_says_nothing_about_the_name_keeps_it(tmp_path):
    # set_capacity rebuilds the document from the amounts it is given, so anything
    # that is not an amount has to be carried over on purpose.
    svc = _svc(tmp_path)
    svc.set_capacity({"cpu": 8}, label="Avatar")
    svc.set_capacity({"cpu": 12})
    assert _doc(tmp_path)["label"] == "Avatar"
    assert _doc(tmp_path)["cpu"] == 12


def test_this_machines_display_name_is_never_read_as_capacity(tmp_path):
    # Every other top-level key is a number; a label left among them used to abort
    # the whole pool file.
    svc = _svc(tmp_path, "cpu: 4\nworkers: 4\nlabel: Avatar\n")
    status = svc.ledger_status()
    assert status["host_errors"] == []
    assert status["capacity"]["cpu"] == 4.0
    assert _host(status, "local")["label"] == "Avatar"


def test_a_display_name_is_one_line_and_bounded(tmp_path, every_host_placeable):
    svc = _svc(tmp_path)
    svc.update_host("big", label="  two   words\n")
    assert _doc(tmp_path)["hosts"]["big"]["label"] == "two words"
    with pytest.raises(ValueError, match="at most 60"):
        svc.update_host("big", label="x" * 61)
