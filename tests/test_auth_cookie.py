from coscience import auth


def test_sign_verify_roundtrip(tmp_path):
    c = auth.make_cookie("stroganov", tmp_path)
    assert auth.verify_cookie(c, tmp_path) == "stroganov"


def test_tampered_cookie_rejected(tmp_path):
    c = auth.make_cookie("stroganov", tmp_path)
    assert auth.verify_cookie(c[:-1] + ("x" if c[-1] != "x" else "y"), tmp_path) == ""
    assert auth.verify_cookie("apathak." + c.split(".", 1)[1], tmp_path) == ""  # swapped name
    assert auth.verify_cookie("", tmp_path) == ""
    assert auth.verify_cookie("no-dot", tmp_path) == ""


def test_secret_persisted_and_stable(tmp_path):
    c1 = auth.make_cookie("stroganov", tmp_path)
    assert (tmp_path / ".coscience" / "secret").is_file()
    c2 = auth.make_cookie("stroganov", tmp_path)   # same secret reused
    assert c1 == c2


def test_non_ascii_cookie_rejected(tmp_path):
    assert auth.verify_cookie("ünícode.sig", tmp_path) == ""
