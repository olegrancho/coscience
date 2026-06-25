"""MCP server exposing coscience.service.Service as Model Context Protocol tools.

Thin wrapper: each tool calls one Service method and returns its (already
JSON-serialisable) result. Service NotFoundError/ValueError become ToolError so
clients see a clean message instead of a raw KeyError repr.
"""
from __future__ import annotations

import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from coscience.service import NotFoundError, Service


def build_server(service: Service, name: str = "coscience") -> FastMCP:
    server = FastMCP(name)

    @server.tool()
    def submit_sprint(id: str, goals: str, plan: list[dict],
                      program: str | None = None, priority: int = 0,
                      preemptible: bool = True,
                      resources_required: dict | None = None) -> dict:
        """Submit a new sprint proposal; returns the created sprint detail."""
        try:
            service.submit_sprint(id=id, goals=goals, plan=plan, program=program,
                                  priority=priority, preemptible=preemptible,
                                  resources_required=resources_required)
        except ValueError as exc:
            raise ToolError(str(exc))
        return service.get_sprint(id)

    @server.tool()
    def approve_sprint(id: str) -> dict:
        """Approve a proposed sprint; returns the updated sprint detail."""
        try:
            service.approve_sprint(id)
        except NotFoundError:
            raise ToolError(f"sprint not found: {id}")
        return service.get_sprint(id)

    @server.tool()
    def list_sprints(status: str | None = None) -> list[dict]:
        """List sprints, optionally filtered by status (proposed/approved/...)."""
        return service.list_sprints(status)

    @server.tool()
    def get_sprint(id: str) -> dict:
        """Get full detail for one sprint, including progress and any lease."""
        try:
            return service.get_sprint(id)
        except NotFoundError:
            raise ToolError(f"sprint not found: {id}")

    @server.tool()
    def list_results() -> list[dict]:
        """List all recorded results (id, sprint, summary)."""
        return service.list_results()

    @server.tool()
    def get_result(id: str) -> dict:
        """Get one result by id."""
        try:
            return service.get_result(id)
        except NotFoundError:
            raise ToolError(f"result not found: {id}")

    @server.tool()
    def ledger_status() -> dict:
        """Current resource ledger: capacity, used, available, and active leases."""
        return service.ledger_status()

    return server


def _service_from_env() -> Service:
    repo_root = Path(os.environ.get("COSCIENCE_REPO", os.getcwd()))
    return Service(repo_root)


def main() -> None:
    """Console entry point: run the MCP server over stdio."""
    build_server(_service_from_env()).run()


if __name__ == "__main__":
    main()
