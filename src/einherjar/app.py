"""The Einherjar app: one process, one health route, and the write face.

The content face and the MCP server arrive in their own step. What is here now is the store the
CLI writes to and the guard in front of it: `/health` open, `/v1` behind a verified token.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from einherjar import __version__
from einherjar.api.http import router
from einherjar.auth.verifier import build_auth
from einherjar.business.tenant import TenantResolver
from einherjar.configuration.config import Config, load_config, validate
from einherjar.data.repository import FleetRepository


async def health() -> JSONResponse:
    """Liveness: the service name and version, read from nowhere.

    Open by construction, and honest about what it proves: the process is up, not that the store
    answers or that a token verifies.
    """
    return JSONResponse({"status": "ok", "service": "einherjar", "version": __version__})


def build_app(
    config: Config | None = None,
    *,
    auth=None,
    repository: FleetRepository | None = None,
) -> FastAPI:
    """Wire the app. ``auth`` and ``repository`` exist so tests can double the issuer and the
    store; neither switches verification off when a config has an issuer."""
    config = config or load_config()
    validate(config)

    provider = auth if auth is not None else build_auth(config)
    verifier = getattr(provider, "token_verifier", None) if provider is not None else None

    app = FastAPI(title="einherjar", version=__version__)
    app.state.config = config
    app.state.auth = provider
    app.state.verifier = verifier
    app.state.resolver = TenantResolver(db_url=config.db_url)
    app.state.repository = repository or FleetRepository(db_url=config.db_url)

    app.add_api_route("/health", health, methods=["GET"])
    app.include_router(router, prefix="/v1")
    return app
