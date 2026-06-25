import asyncio
import json

import pytest

from coscience.mcp_server import build_server
from coscience.service import Service


def unwrap(result):
    """Normalise FastMCP call_tool output to the tool's Python return value.

    dict-returning tools -> Sequence[ContentBlock]; parse the single text block.
    list/scalar-returning tools -> (blocks, {"result": <value>}); take structured.
    """
    if isinstance(result, tuple):
        return result[1]["result"]
    return json.loads(result[0].text)


def call(server, name, args):
    return unwrap(asyncio.run(server.call_tool(name, args)))


@pytest.fixture
def server(tmp_path):
    return build_server(Service(tmp_path))


def test_lists_all_seven_tools(server):
    names = {t.name for t in asyncio.run(server.list_tools())}
    assert names == {
        "submit_sprint", "approve_sprint", "list_sprints", "get_sprint",
        "list_results", "get_result", "ledger_status",
    }


def test_every_tool_has_a_description(server):
    for t in asyncio.run(server.list_tools()):
        assert t.description and t.description.strip()


def test_submit_then_get_and_list(server):
    created = call(server, "submit_sprint", {
        "id": "sp1", "goals": "cure", "plan": [{"id": "s1", "run": "echo hi"}],
        "priority": 3, "resources_required": {"gpu": 1},
    })
    assert created["id"] == "sp1"
    assert created["status"] == "proposed"
    assert created["plan"] == [{"id": "s1", "run": "echo hi"}]

    rows = call(server, "list_sprints", {"status": "proposed"})
    assert [r["id"] for r in rows] == ["sp1"]

    detail = call(server, "get_sprint", {"id": "sp1"})
    assert detail["priority"] == 3
    assert detail["lease"] is None


def test_approve_changes_status(server):
    call(server, "submit_sprint", {"id": "sp1", "goals": "g",
                                    "plan": [{"id": "s1", "run": "true"}]})
    approved = call(server, "approve_sprint", {"id": "sp1"})
    assert approved["status"] == "approved"
    assert call(server, "list_sprints", {"status": "proposed"}) == []
    assert [r["id"] for r in call(server, "list_sprints", {"status": "approved"})] == ["sp1"]


def test_results_round_trip(server, tmp_path):
    from coscience.models import Result
    # Reuse the same substrate the server's Service is bound to.
    Service(tmp_path).substrate.save_result(Result(id="r1", sprint="sp1", summary="found X"))
    assert call(server, "list_results", {}) == [{"id": "r1", "sprint": "sp1", "summary": "found X"}]
    assert call(server, "get_result", {"id": "r1"})["summary"] == "found X"


def test_ledger_status_shape(server):
    status = call(server, "ledger_status", {})
    assert set(status) == {"capacity", "used", "available", "leases"}


def test_missing_sprint_raises_tool_error(server):
    from mcp.server.fastmcp.exceptions import ToolError
    with pytest.raises(ToolError):
        asyncio.run(server.call_tool("get_sprint", {"id": "nope"}))


def test_duplicate_submit_raises_tool_error(server):
    from mcp.server.fastmcp.exceptions import ToolError
    call(server, "submit_sprint", {"id": "sp1", "goals": "g",
                                   "plan": [{"id": "s1", "run": "true"}]})
    with pytest.raises(ToolError):
        asyncio.run(server.call_tool("submit_sprint", {
            "id": "sp1", "goals": "g", "plan": [{"id": "s1", "run": "true"}]}))
