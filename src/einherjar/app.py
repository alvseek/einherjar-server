"""The Einherjar app: one process, one health route, one surface to grow.

Step 1 of the build is deliberately small: the config guard, a liveness route, and the shape the
rest hangs from. The content face and the store arrive in their own steps, and the auth guard
sits on them rather than here, so ``/health`` stays open for a deploy's gate.
"""

from __future__ import annotations

from fastapi import FastAPI
from starlette.responses import JSONResponse

from einherjar import __version__
from einherjar.configuration.config import Config, load_config, validate


async def health() -> JSONResponse:
    """Liveness: the service name and version, read from nowhere.

    Open by construction, and honest about what it proves: the process is up, not that the store
    answers or that a token verifies.
    """
    return JSONResponse({"status": "ok", "service": "einherjar", "version": __version__})


def build_app(config: Config | None = None) -> FastAPI:
    """Wire the app. The store is validated here, so a bad config fails at boot."""
    config = config or load_config()
    validate(config)

    app = FastAPI(title="einherjar", version=__version__)
    app.add_api_route("/health", health, methods=["GET"])
    return app
