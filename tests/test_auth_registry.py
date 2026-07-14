from coscience import auth


def _write_registry(tmp_path, text):
    d = tmp_path / ".coscience"
    d.mkdir(parents=True, exist_ok=True)
    (d / "users.yaml").write_text(text)


def test_load_seeded_users(tmp_path):
    _write_registry(tmp_path, """
users:
  - username: stroganov
    name: Oleg Stroganov
    initials: OS
  - username: apathak
    name: Aish Pathak
""")
    users = auth.load_users(tmp_path)
    assert set(users) == {"stroganov", "apathak"}
    assert users["stroganov"].name == "Oleg Stroganov"
    assert users["stroganov"].initials == "OS"
    assert users["apathak"].initials == "AP"   # derived from name


def test_empty_and_missing_registry(tmp_path):
    assert auth.load_users(tmp_path) == {}          # no file
    _write_registry(tmp_path, "users: []\n")
    assert auth.load_users(tmp_path) == {}          # empty list


def test_derive_initials():
    assert auth._derive_initials("Oleg Stroganov") == "OS"
    assert auth._derive_initials("Cher") == "CH"
    assert auth._derive_initials("") == "?"
