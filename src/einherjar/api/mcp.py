"""The MCP face: the served fleet content, how to install the CLI, and read-only registry tools.

Every procedure is served two ways. A **prompt** is what a person invokes by name; a
``read_procedure`` **tool** is what an agent can open mid-task, which is the only path that works on
a client that turns prompts into commands. Templates are resources, so a procedure never points at
a local clone.

``resource://client/install`` is the one resource that is not fleet content: it is how a machine
gets the CLI, written from the wheel this server actually serves. It is registered whether or not
the content checkout is present, and its text is generated per read so it never describes a wheel
the running server would not hand out.

No registry write exists on this face. A consult is recorded by the CLI on the machine that owns
the spawn; this face exists so an agent can read what has been recorded, not claim a row.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any, Callable

from fastmcp import FastMCP

from einherjar.business.queries import FleetQueries
from einherjar.business.tenant import TenantResolver
from einherjar.configuration.config import DEFAULT_CLI_DIST_DIR, DEFAULT_PUBLIC_BASE_URL
from einherjar.content.loader import ContentLoader
from einherjar.distribution.install import client_install_text

INSTRUCTIONS = (
    "Einherjar serves the fleet layer: consult or delegate to specialized agents running on your "
    "own machine, and track their sessions. Call help() for setup, client installation and usage. "
    "Read a procedure with read_procedure(name) and follow it. "
    "The registry tools are read-only: a consult is recorded by the einherjar CLI on the machine "
    "where the agent runs."
)

#: The stable discovery point for installing the local client. It is served natively and through
#: the template tools, so an agent reaches it whichever primitive its client exposes.
CLIENT_INSTALL_URI = "resource://client/install"
CLIENT_INSTALL_NAME = "client-install"
CLIENT_INSTALL_TITLE = "Install the Einherjar CLI"
CLIENT_INSTALL_MIME = "text/markdown"

_READ = {"readOnlyHint": True, "openWorldHint": False}


def _procedure_rows(content: ContentLoader) -> list[dict[str, str]]:
    """Use the same live procedure index for discovery and help, even without a checkout."""
    if not content.available():
        return []
    return [
        {
            "name": name,
            "title": content.title_procedure(name),
            "description": content.describe_procedure(name),
        }
        for name in content.list_procedures()
    ]


def _not_served(kind: str, name: str, listing: str) -> dict[str, Any]:
    """An unknown name is an answer, not an error: the caller typed what a procedure told it to."""
    return {
        "served": False,
        "name": name,
        "note": f"no served {kind} named {name!r}; call {listing} to see what is",
    }


def _register_content(
    mcp: FastMCP, content: ContentLoader, install_text: Callable[[], str]
) -> None:
    """Register procedures as prompts and tools, templates and the install guide as resources."""

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
        return _procedure_rows(content)

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
        """List the templates this server serves, plus how to install the CLI."""
        return [
            {
                "name": name,
                "title": content.title_resource(name),
                "uri": f"resource://templates/{name}",
            }
            for name in content.list_resources()
        ] + [
            {
                "name": CLIENT_INSTALL_NAME,
                "title": CLIENT_INSTALL_TITLE,
                "uri": CLIENT_INSTALL_URI,
            }
        ]

    @mcp.tool(title="Read a served template", annotations=_READ)
    def read_resource(name: str) -> dict[str, Any]:
        """Read a served template verbatim, or the CLI installation instructions.

        An unknown name returns served=false.
        """
        if name == CLIENT_INSTALL_NAME:
            return {"served": True, "name": name, "content": install_text()}
        try:
            text = content.get_resource(name)
        except KeyError:
            return _not_served("template", name, "list_resources()")
        return {"served": True, "name": name, "content": text}


def _register_client_install(mcp: FastMCP, install_text: Callable[[], str]) -> None:
    """The install resource exists even when there is no artifact: its text then says so.

    Registered outside the content face on purpose. It describes how to get the client, not the
    fleet's procedures, so it must not disappear when the content checkout is missing.
    """

    def fn() -> str:
        return install_text()

    fn.__name__ = "client_install"
    fn.__doc__ = "How to install or upgrade the local Einherjar CLI on this machine."
    mcp.resource(
        CLIENT_INSTALL_URI,
        name=CLIENT_INSTALL_NAME,
        title=CLIENT_INSTALL_TITLE,
        mime_type=CLIENT_INSTALL_MIME,
        annotations={"audience": ["assistant", "user"], "priority": 0.5},
    )(fn)


def build_mcp(
    *,
    auth=None,
    resolver: TenantResolver,
    queries: FleetQueries,
    content: ContentLoader,
    cli_dist_dir: Path | str = DEFAULT_CLI_DIST_DIR,
    public_base_url: str = DEFAULT_PUBLIC_BASE_URL,
) -> FastMCP:
    mcp = FastMCP(name="einherjar", instructions=INSTRUCTIONS, auth=auth)

    def install_text() -> str:
        """Read the artifact on every call, so a wheel added after boot is described correctly."""
        return client_install_text(public_base_url, cli_dist_dir)

    _register_client_install(mcp, install_text)

    @mcp.tool(name="help", title="What Einherjar is and how to use it", annotations=_READ)
    def help_() -> dict[str, Any]:
        """Explain the fleet layer, local CLI installation, storage locations and usage.

        Includes the current wheel's download URL, hash and install command, plus the served
        procedures. Available even without fleet content or a client wheel. Read-only.
        """
        return {
            "instructions": INSTRUCTIONS,
            "access": "mcp",
            "endpoint": f"{public_base_url.rstrip('/')}/mcp",
            "procedures": _procedure_rows(content),
            "client_install": install_text(),
            "usage": (
                "The MCP server serves instructions and reads the session registry; it does not "
                "run agents remotely. Install the CLI on each machine that will run agents, "
                "with OpenCode or Claude Code available there. Run it from the project directory. "
                "Read load-fleet to discover teammates, ask-agent for a blocking consultation, "
                "delegate-agent for background work, spawn for a fresh session without fleet "
                "awakening, or setup-fleet to initialize a roster. Use read_procedure(name) "
                "and follow the returned procedure; read_resource(name) reads its templates. "
                "fleet_sessions and fleet_session only read your tenant's recorded consults. "
                "A completed process does not prove memory was saved: request wrap-up explicitly "
                "for an awakened fleet agent. References resume on the originating machine and "
                "platform. For long or multiline tasks, put the task in a file and name it in a "
                "short single-line --prompt; Windows command shims can truncate multiline "
                "arguments."
            ),
            "commands": {
                "consult": 'einherjar ask <agent-name> --prompt "<task>"',
                "delegate": 'einherjar delegate <agent-name> --prompt "<task>"',
                "spawn": 'einherjar spawn --prompt "<task>"',
                "spawn_background": 'einherjar spawn --background --prompt "<task>"',
                "resume": 'einherjar ask <session-ref> --prompt "<next task>"',
                "result": "einherjar result <session-ref>",
                "full_log": "einherjar result <session-ref> --full",
                "sessions": "einherjar sessions",
                "wrap_up": "einherjar wrap-up <session-ref>",
            },
        }

    if content.available():
        _register_content(mcp, content, install_text)

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
