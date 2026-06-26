import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from coscience.http_api import build_app
from coscience.mcp_server import build_server
from coscience.models import Program
from coscience.service import Service


def _seed(tmp_path):
    svc = Service(tmp_path)
    svc.substrate.save_program(Program(id="p1", title="Cancer", goals="cure"))
    return svc


def test_http_list_and_get_program(tmp_path):
    client = TestClient(build_app(_seed(tmp_path)))
    assert [p["id"] for p in client.get("/api/programs").json()] == ["p1"]
    r = client.get("/api/programs/p1")
    assert r.status_code == 200
    assert r.json()["goals"] == "cure"
    assert client.get("/api/programs/nope").status_code == 404


def test_http_invalid_status_is_422(tmp_path):
    client = TestClient(build_app(_seed(tmp_path)))
    assert client.get("/api/programs", params={"status": "bogus"}).status_code == 422


def test_mcp_list_and_get_program(tmp_path):
    server = build_server(_seed(tmp_path))

    def call(name, args):
        r = asyncio.run(server.call_tool(name, args))
        return r[1]["result"] if isinstance(r, tuple) else json.loads(r[0].text)

    assert [p["id"] for p in call("list_programs", {})] == ["p1"]
    assert call("get_program", {"id": "p1"})["title"] == "Cancer"


def test_mcp_missing_program_raises(tmp_path):
    from mcp.server.fastmcp.exceptions import ToolError
    server = build_server(_seed(tmp_path))
    with pytest.raises(ToolError):
        asyncio.run(server.call_tool("get_program", {"id": "nope"}))
