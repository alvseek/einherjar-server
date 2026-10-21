"""Serve the MCP face and its discovery document under both slash forms, without a redirect.

Starlette's ``Mount`` matches only paths *below* the mount, so a request to the bare ``/mcp`` is
answered with a slash-appending 307 instead of reaching the sub-app. Behind a TLS-terminating proxy
that redirect is worse than a detour: it is built from the scheme uvicorn saw, so it points at
``http://``, and a client that dropped the slash (which the MCP specification tells it to do) is
bounced to plaintext before it ever sees a 401. FastMCP also builds its app believing it sits at
``/``, so the discovery document it advertises exists inside the mount rather than at the root.

Rewriting the two exact paths before routing removes the redirect entirely; every other route keeps
the default behaviour.
"""

from __future__ import annotations

MCP_MOUNT_PATH = "/mcp"

#: RFC 9728 derives the document from the resource identifier: the well-known prefix followed by the
#: identifier's own path.
PROTECTED_RESOURCE_METADATA_PATH = f"/.well-known/oauth-protected-resource{MCP_MOUNT_PATH}"

_REWRITES = {
    MCP_MOUNT_PATH: f"{MCP_MOUNT_PATH}/",
    f"{PROTECTED_RESOURCE_METADATA_PATH}/": PROTECTED_RESOURCE_METADATA_PATH,
}


class NormaliseMcpPath:
    """ASGI middleware that rewrites the two paths a client may name either way."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http" and scope.get("path") in _REWRITES:
            target = _REWRITES[scope["path"]]
            scope = dict(scope)
            scope["path"] = target
            scope["raw_path"] = target.encode("ascii")
        await self.app(scope, receive, send)
