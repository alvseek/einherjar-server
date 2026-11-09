"""The public resource face: the current CLI wheel, and how to install it.

Both routes are open by construction. The client has to be installable before anyone can sign in,
and the artifact is public code, so a download gate would protect nothing while making the very
first step impossible on a fresh machine. No token, tenant or database is consulted here, which
also means an unreachable store cannot make the installer unreachable.

A request can only ever reach the one wheel the configured directory holds: the current filename is
compared as a string, and the bytes served come from the path that was read, never from anything the
caller typed.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from einherjar.distribution.reader import (
    CliArtifact,
    CliArtifactUnavailable,
    read_current,
)

router = APIRouter()

#: Discovery must never be cached. A cached copy would keep advertising the previous deployment's
#: wheel after a rollout, and the client does not check for updates on its own. The wheel itself is
#: versioned but not immutable — the same version can be rebuilt — so it is not cached either.
_NO_STORE = {"Cache-Control": "no-store"}


def _current(request: Request) -> CliArtifact:
    """The artifact this deployment serves, or 503 with the operator's next step."""
    try:
        return read_current(request.app.state.config.cli_dist_dir)
    except CliArtifactUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/resources/cli")
async def cli_metadata(request: Request) -> JSONResponse:
    """The current CLI wheel: its actual version, size, hash and install command."""
    artifact = _current(request)
    return JSONResponse(
        artifact.metadata(request.app.state.config.public_base_url),
        headers=_NO_STORE,
    )


@router.get("/resources/cli/{filename}")
async def cli_wheel(filename: str, request: Request) -> FileResponse:
    """The current wheel's bytes, by its exact filename. Nothing else is reachable."""
    artifact = _current(request)
    if filename != artifact.filename:
        raise HTTPException(
            status_code=404,
            detail=(
                f"no CLI wheel named {filename!r} is served; "
                "GET /v1/resources/cli for the current one"
            ),
        )
    return FileResponse(
        artifact.path,
        media_type="application/octet-stream",
        filename=artifact.filename,
        headers=_NO_STORE,
    )
