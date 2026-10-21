"""The Einherjar app: health, the write face, and the read-only MCP face.

The content face arrives in its own step. What is here is the store the CLI writes to and the two
faces over it: `/health` open, `/v1` behind a verified token, `/mcp` behind the same issuer.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from starlette.middleware import Middleware

from einherjar import __version__
from einherjar.api.http import router
from einherjar.api.mcp import build_mcp
from einherjar.api.paths import MCP_MOUNT_PATH, NormaliseMcpPath
from einherjar.auth.verifier import build_auth
from einherjar.business.queries import FleetQueries
from einherjar.business.tenant import TenantResolver
from einherjar.configuration.config import Config, load_config, validate
from einherjar.data.repository import FleetRepository


async def health() -> JSONResponse:
    """Liveness: the service name and version, read from nowhere.

    Open by construction, and honest about what it proves: the process is up, not that the store
    answers or that a token verifies.
    """
    return JSONResponse({"status": "ok", "service": "einherjar", "version": __version__})


_CONFIG_AUTH = object()


def build_app(
    config: Config | None = None,
    *,
    auth=_CONFIG_AUTH,
    verifier=None,
    repository: FleetRepository | None = None,
) -> FastAPI:
    """Wire the app.

    ``auth`` defaults to whatever the config builds; passing ``None`` explicitly means no MCP
    provider (the local shape, or a test that supplies ``verifier`` directly). ``verifier`` exists
    so the HTTP face can be tested with a minted token without standing up an issuer.
    """
    config = config or load_config()
    validate(config)

    provider = build_auth(config) if auth is _CONFIG_AUTH else auth
    check = verifier if verifier is not None else getattr(provider, "token_verifier", None)
    resolver = TenantResolver(db_url=config.db_url)
    repo = repository or FleetRepository(db_url=config.db_url)

    mcp = build_mcp(auth=provider, resolver=resolver, queries=FleetQueries(repo))
    mcp_app = mcp.http_app(path="/")

    app = FastAPI(
        title="einherjar",
        version=__version__,
        lifespan=mcp_app.lifespan,
        middleware=[Middleware(NormaliseMcpPath)],
    )
    app.state.config = config
    app.state.auth = provider
    app.state.verifier = check
    app.state.resolver = resolver
    app.state.repository = repo

    app.add_api_route("/health", health, methods=["GET"])
    app.include_router(router, prefix="/v1")
    app.mount(MCP_MOUNT_PATH, mcp_app)

    # Discovery, served from the root. FastMCP advertises root-level metadata URLs and puts its own
    # copies inside the sub-app, which the mount would leave 404. Without these a client following
    # the 401's resource_metadata pointer fails discovery and never reaches a login screen.
    if provider is not None:
        app.router.routes.extend(provider.get_well_known_routes())
    return app
