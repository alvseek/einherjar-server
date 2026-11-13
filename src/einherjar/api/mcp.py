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
from typing import Annotated, Any, Callable

from fastmcp import FastMCP
from pydantic import Field

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
CLIENT_INSTALL_DESCRIPTION = (
    "Installation and upgrade instructions for the local Einherjar CLI, using this deployment's "
    "wheel. Includes its download URL, SHA-256 hash and install command, or explains why no wheel "
    "is available. Reading this guide does not install anything."
)

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
        if not content.accepts_argument(procedure):
            # No placeholder means no slot: do not accept text the body cannot consume.
            fn.__annotations__ = {"return": str}
            fn.__signature__ = inspect.Signature([], return_annotation=str)  # type: ignore[attr-defined]
            return fn
        fn.__doc__ = (
            f"{content.title_procedure(procedure)}\n\n"
            "Read this procedure, then follow its steps; fetching it does not run an agent.\n\n"
            "Args:\n    argument: Optional text substituted into $ARGUMENTS. Use the format "
            "in the procedure's Arguments section; omit to supply no argument.\n"
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
            description=content.describe_resource(name),
            mime_type=content.mime_type_resource(name),
            annotations={"audience": ["assistant"], "priority": 0.1},
        )(_make_resource(name))

    # --- the same content as tools: the primitive an agent may invoke itself -------------

    @mcp.tool(title="List served procedures", annotations=_READ)
    def list_procedures() -> list[dict[str, str]]:
        """Discover fleet procedures before choosing which instructions to read.

        Takes no parameters. Returns name, title and description for each served procedure.
        Pass a returned name to read_procedure; this listing does not execute instructions or
        start an agent. Procedures cover setup, teammate discovery, consultation and spawning.
        """
        return _procedure_rows(content)

    @mcp.tool(title="Read a served procedure", annotations=_READ)
    def read_procedure(
        name: Annotated[
            str, Field(description="Exact name from list_procedures, without / or .md.")
        ],
        argument: Annotated[
            str | None,
            Field(
                description="Text substituted for $ARGUMENTS, using the procedure's Arguments "
                "format. Omit or pass null to read authored text without substitution."
            ),
        ] = None,
    ) -> dict[str, Any]:
        """Fetch a fleet procedure's full instructions so you can follow them.

        Use this for agent-invoked access instead of relying on an installed slash command.
        Returns served=true, name and content; an unknown name returns served=false, name and a
        discovery note. Reading does not execute the steps, spawn an agent or change the registry.
        """
        try:
            text = content.get_procedure(name, argument)
        except KeyError:
            return _not_served("procedure", name, "list_procedures()")
        return {"served": True, "name": name, "content": text}

    @mcp.tool(title="List served templates", annotations=_READ)
    def list_resources() -> list[dict[str, str]]:
        """Discover served templates and the local CLI installation guide.

        Takes no parameters. Returns name, title, uri and description for each item. Pass its
        name (not URI) to read_resource, or read the URI through native MCP resource access.
        This only lists content; it does not download a wheel, install the CLI or create a roster.
        """
        return [
            {
                "name": name,
                "title": content.title_resource(name),
                "uri": f"resource://templates/{name}",
                "description": content.describe_resource(name),
            }
            for name in content.list_resources()
        ] + [
            {
                "name": CLIENT_INSTALL_NAME,
                "title": CLIENT_INSTALL_TITLE,
                "uri": CLIENT_INSTALL_URI,
                "description": CLIENT_INSTALL_DESCRIPTION,
            }
        ]

    @mcp.tool(title="Read a served resource", annotations=_READ)
    def read_resource(
        name: Annotated[
            str,
            Field(
                description="Exact name from list_resources, not a URI or file path. "
                "Use client-install for the CLI installation and upgrade guide."
            ),
        ],
    ) -> dict[str, Any]:
        """Read a served template verbatim, or the CLI installation instructions.

        Returns served=true, name and content; an unknown name returns served=false, name and a
        discovery note. Follow the relevant procedure to use a template. Reading does not save
        a file, create a roster, download the CLI or install it.
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
    fn.__doc__ = CLIENT_INSTALL_DESCRIPTION
    mcp.resource(
        CLIENT_INSTALL_URI,
        name=CLIENT_INSTALL_NAME,
        title=CLIENT_INSTALL_TITLE,
        description=CLIENT_INSTALL_DESCRIPTION,
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
        """Start here for Einherjar setup, capabilities and command examples.

        Takes no parameters. Returns instructions, endpoint, procedure discovery, installation
        guidance, local state locations and command examples. Describes this deployment's
        current wheel or explains its absence. Available even without fleet content or a wheel.
        Read-only guidance: MCP does not run agents remotely; the local CLI runs them.
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
    def fleet_sessions(
        project: Annotated[
            str | None,
            Field(
                description="Exact recorded project name. Omit or pass null for all projects; "
                "an empty string is a literal filter, not all projects."
            ),
        ] = None,
        status: Annotated[
            str | None,
            Field(
                description="Exact status: active, completed or failed. Omit or pass null for "
                "all statuses. Unrecognized values match no rows."
            ),
        ] = None,
    ) -> list[dict]:
        """List the authenticated tenant's recorded sessions, newest created first.

        Optional project and status filters are exact matches combined with AND. Returns
        records with UUID, platform session reference, machine, project, domain, theme, status,
        timestamps and error details; no matches returns an empty list.
        Use a row's uuid with fleet_session. This does not poll a process or return its reply/log;
        use the local CLI for results. Recorded status is not a live health check.
        """
        return queries.list_sessions(
            user_id=resolver.current_user_id(),
            project=project,
            status=status,
        )

    @mcp.tool(title="One recorded consult", annotations=_READ)
    def fleet_session(
        uuid: Annotated[
            str,
            Field(
                description="Full registry UUID from fleet_sessions, not the platform "
                "session_ref (such as ses_...). Prefixes are not resolved."
            ),
        ],
    ) -> dict | None:
        """Read one session registry record belonging to the authenticated tenant.

        Returns identifiers, machine, platform, project, domain, theme, status, timestamps and
        error details. Returns null when unknown or owned by another tenant. Does not resume or
        poll the agent, or retrieve its answer/log; use the CLI on the originating machine.
        """
        return queries.get_session(user_id=resolver.current_user_id(), uuid=uuid)

    return mcp
