"""Lightweight user identity: a curated registry + a signed session cookie.

All identity resolution lives here so a later Keycloak/OIDC swap touches only this
module. `username` is the stable attribution key (and future OIDC subject)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class User:
    username: str
    name: str
    initials: str


def _derive_initials(name: str) -> str:
    parts = [p for p in name.split() if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def _users_path(repo_root) -> Path:
    return Path(repo_root) / ".coscience" / "users.yaml"


def load_users(repo_root) -> dict[str, User]:
    """username -> User from `.coscience/users.yaml`; {} if absent/empty."""
    path = _users_path(repo_root)
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text()) or {}
    out: dict[str, User] = {}
    for row in (data.get("users") or []):
        uname = str(row.get("username", "")).strip()
        if not uname:
            continue
        name = str(row.get("name") or uname).strip()
        initials = str(row.get("initials") or "").strip() or _derive_initials(name)
        out[uname] = User(username=uname, name=name, initials=initials)
    return out


def _secret(repo_root) -> bytes:
    env = os.environ.get("COSCIENCE_SECRET")
    if env:
        return env.encode()
    path = Path(repo_root) / ".coscience" / "secret"
    if path.is_file():
        return path.read_bytes()
    path.parent.mkdir(parents=True, exist_ok=True)
    tok = secrets.token_bytes(32)
    path.write_bytes(tok)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return tok


def make_cookie(username: str, repo_root) -> str:
    mac = hmac.new(_secret(repo_root), username.encode(), hashlib.sha256).digest()
    sig = base64.urlsafe_b64encode(mac).decode().rstrip("=")   # b64url has no '.'
    return f"{username}.{sig}"


def verify_cookie(value: str, repo_root) -> str:
    """Username if the signed cookie is valid and untampered, else ''."""
    if not value or "." not in value:
        return ""
    username = value.rpartition(".")[0]
    if hmac.compare_digest(value, make_cookie(username, repo_root)):
        return username
    return ""
