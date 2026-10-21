"""The MCP face: read-only registry tools.

No write tool exists here. A consult is recorded by the CLI on the machine that owns the spawn;
this face exists so an agent can look at what has been recorded, not so it can claim a row.
"""

from __future__ import annotations

from fastmcp import FastMCP

from einherjar.business.queries import FleetQueries
from einherjar.business.tenant import TenantResolver

INSTRUCTIONS = (
    "Einherjar serves the fleet layer. Read a procedure with read_procedure(name) and follow it. "
    "The registry tools are read-only: a consult is recorded by the einherjar CLI on the machine "
    "where the agent runs."
)


def build_mcp(
    *,
    auth=None,
    resolver: TenantResolver,
    queries: FleetQueries,
) -> FastMCP:
    mcp = FastMCP(name="einherjar", instructions=INSTRUCTIONS, auth=auth)

    @mcp.tool
    def fleet_sessions(project: str | None = None, status: str | None = None) -> list[dict]:
        """The caller's recorded consults, newest first. Read-only."""
        return queries.list_sessions(
            user_id=resolver.current_user_id(),
            project=project,
            status=status,
        )

    @mcp.tool
    def fleet_session(uuid: str) -> dict | None:
        """One recorded consult by uuid, for the caller's tenant only. None when absent."""
        return queries.get_session(user_id=resolver.current_user_id(), uuid=uuid)

    return mcp
