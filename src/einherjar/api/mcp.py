"""The MCP face: the served fleet content, and read-only registry tools.

Every procedure is served two ways. A **prompt** is what a person invokes by name; a
``read_procedure`` **tool** is what an agent can open mid-task, which is the only path that works on
a client that turns prompts into commands. Templates are resources, so a procedure never points at
a local clone.

No registry write exists on this face. A consult is recorded by the CLI on the machine that owns
the spawn; this face exists so an agent can read what has been recorded, not claim a row.
"""

from __future__ import annotations

import inspect
from typing import Any, Callable

from fastmcp import FastMCP

from einherjar.business.queries import FleetQueries
from einherjar.business.tenant import TenantResolver
from einherjar.content.loader import ContentLoader

INSTRUCTIONS = (
    "Einherjar serves the fleet layer. Read a procedure with read_procedure(name) and follow it. "
    "The registry tools are read-only: a consult is recorded by the einherjar CLI on the machine "
    "where the agent runs."
)

_READ = {"readOnlyHint": True, "openWorldHint": False}


def _not_served(kind: str, name: str, listing: str) -> dict[str, Any]:
    """An unknown name is an answer, not an error: the caller typed what a procedure told it to."""
    return {
        "served": False,
        "name": name,
        "note": f"no served {kind} named {name!r}; call {listing} to see what is",
    }


def _register_content(mcp: FastMCP, content: ContentLoader) -> None:
    """Register procedures as prompts and tools, templates as resources."""

    def _make_prompt(procedure: str) -> Callable[..., str]:
        # FastMCP derives a prompt's arguments from the function signature, so the parameter must
        # be real. Both ``__signature__`` and ``__annotations__`` are set: pydantic reads the
        # second and raises on the first alone. A plain ``str`` annotation is deliberate — an
        # Annotated field makes FastMCP append a JSON-schema instruction to the description.
        def fn(**kwargs: str) -> str:
            return content.get_procedure(procedure, kwargs.get("argument") or None)

        fn.__name__ = procedure.replace("-", "_")
        fn.__doc__ = (
            f"{content.title_procedure(procedure)}\n\n"
            "Args:\n    argument: the procedure's arguments, as its Arguments section describes\n"
        )
        fn.__annotations__ = {"argument": str, "return": str}
        parameter = inspect.Parameter(
            "argument", inspect.Parameter.KEYWORD_ONLY, default="", annotation=str
        )
        fn.__signature__ = inspect.Signature(  # type: ignore[attr-defined]
            [parameter], return_annotation=str
        )
        return fn

    for name in content.list_procedures():
        mcp.prompt(
            name=name,
            title=content.title_procedure(name),
            description=content.describe_procedure(name),
        )(_make_prompt(name))

    def _make_resource(resource: str) -> Callable[[], str]:
        def fn() -> str:
            return content.get_resource(resource)

        fn.__name__ = f"resource_{resource.replace('-', '_')}"
        return fn

    for name in content.list_resources():
        mcp.resource(
            f"resource://templates/{name}",
            name=name,
            title=content.title_resource(name),
            mime_type=content.mime_type_resource(name),
            annotations={"audience": ["assistant"], "priority": 0.1},
        )(_make_resource(name))

    # --- the same content as tools: the primitive an agent may invoke itself -------------

    @mcp.tool(title="List served procedures", annotations=_READ)
    def list_procedures() -> list[dict[str, str]]:
        """List the fleet procedures this server serves, with a one-line purpose each."""
        return [
            {
                "name": name,
                "title": content.title_procedure(name),
                "description": content.describe_procedure(name),
            }
            for name in content.list_procedures()
        ]

    @mcp.tool(title="Read a served procedure", annotations=_READ)
    def read_procedure(name: str, argument: str | None = None) -> dict[str, Any]:
        """Read a fleet procedure and follow it. `argument` fills the procedure's $ARGUMENTS slot.

        Use this instead of an installed command: the fleet layer is served, not installed. An
        unknown name returns served=false rather than an error.
        """
        try:
            text = content.get_procedure(name, argument)
        except KeyError:
            return _not_served("procedure", name, "list_procedures()")
        return {"served": True, "name": name, "content": text}

    @mcp.tool(title="List served templates", annotations=_READ)
    def list_resources() -> list[dict[str, str]]:
        """List the templates this server serves."""
        return [
            {
                "name": name,
                "title": content.title_resource(name),
                "uri": f"resource://templates/{name}",
            }
            for name in content.list_resources()
        ]

    @mcp.tool(title="Read a served template", annotations=_READ)
    def read_resource(name: str) -> dict[str, Any]:
        """Read a served template verbatim. An unknown name returns served=false."""
        try:
            text = content.get_resource(name)
        except KeyError:
            return _not_served("template", name, "list_resources()")
        return {"served": True, "name": name, "content": text}


def build_mcp(
    *,
    auth=None,
    resolver: TenantResolver,
    queries: FleetQueries,
    content: ContentLoader,
) -> FastMCP:
    mcp = FastMCP(name="einherjar", instructions=INSTRUCTIONS, auth=auth)

    if content.available():
        _register_content(mcp, content)

    @mcp.tool(title="The caller's recorded consults", annotations=_READ)
    def fleet_sessions(project: str | None = None, status: str | None = None) -> list[dict]:
        """The caller's recorded consults, newest first. Read-only."""
        return queries.list_sessions(
            user_id=resolver.current_user_id(),
            project=project,
            status=status,
        )

    @mcp.tool(title="One recorded consult", annotations=_READ)
    def fleet_session(uuid: str) -> dict | None:
        """One recorded consult by uuid, for the caller's tenant only. None when absent."""
        return queries.get_session(user_id=resolver.current_user_id(), uuid=uuid)

    return mcp
