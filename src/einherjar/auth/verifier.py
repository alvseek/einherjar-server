"""Logto as the authorization server; Einherjar stays a pure resource server.

The shape is the family's: a JWKS verifier plus the issuer's address, with the audience pinned to
the MCP resource identifier. It is ported from Hermod for one reason that must not be lost in the
copying: ``RemoteAuthProvider`` records the resource URL without handing it to the verifier, so
``audience`` stays ``None`` and every token clears an audience check that never ran.
``set_mcp_path`` is overridden to close that, and it fails silently when omitted — the trap in
copying ``KeycloakAuthProvider`` as-is.

One audience covers both faces. A token minted for this resource is accepted by the MCP face and by
the CLI's HTTP calls, which is why the CLI asks for this resource rather than one of its own.
"""

from __future__ import annotations

from collections.abc import Sequence

from fastmcp.server.auth import AccessToken, RemoteAuthProvider
from fastmcp.server.auth.providers.jwt import JWTVerifier
from pydantic import AnyHttpUrl

from einherjar.configuration.config import Config

MCP_MOUNT_PATH = "/mcp"

#: Advertised so a client knows what to ask for. ``openid`` makes it an OIDC request at all;
#: ``offline_access`` buys the refresh token the CLI lives on between consults.
DEFAULT_SCOPES = ("openid", "profile", "email", "offline_access")


def mcp_resource_url(public_base_url: str) -> str:
    """The identifier that names this server's MCP face, without a trailing slash.

    A client sends this string as ``resource`` on the authorization and refresh requests, and the
    issuer matches it character for character. The MCP specification settles the slash question:
    use the no-slash form, because a client can be made to send nothing else.
    """
    return f"{public_base_url.rstrip('/')}{MCP_MOUNT_PATH}"


class LogtoAuthProvider(RemoteAuthProvider):
    """Logto as the issuer, with the audience bound to the resource identifier."""

    DEFAULT_SCOPES = DEFAULT_SCOPES

    def __init__(
        self,
        *,
        endpoint: str,
        base_url: str,
        audience: Sequence[str] = (),
        resource_base_url: str | None = None,
    ) -> None:
        oidc = f"{endpoint.rstrip('/')}/oidc"
        self._pinned_audience = tuple(audience)
        super().__init__(
            token_verifier=JWTVerifier(
                jwks_uri=f"{oidc}/jwks",
                issuer=oidc,
                algorithm="RS256",
                audience=list(self._pinned_audience) or None,
            ),
            authorization_servers=[AnyHttpUrl(oidc)],
            base_url=AnyHttpUrl(base_url.rstrip("/")),
            resource_base_url=resource_base_url,
            scopes_supported=list(self.DEFAULT_SCOPES),
        )

    def _get_resource_url(self, path: str | None = None) -> AnyHttpUrl | None:
        """The resource this server advertises, ignoring the mount path the base derives.

        ``resource_base_url`` already names the face in full, so the path handed in must not be
        joined onto it a second time.
        """
        if self.resource_base_url is not None:
            return self.resource_base_url
        return super()._get_resource_url(path)

    def set_mcp_path(self, path: str | None = None) -> None:
        """Bind the verifier's audience to the resource URL this server advertises."""
        super().set_mcp_path(path)
        if self._resource_url is not None and isinstance(self.token_verifier, JWTVerifier):
            self.token_verifier.audience = str(self._resource_url)


def build_auth(config: Config) -> RemoteAuthProvider | None:
    """The auth provider for the call, or ``None`` in the local shape.

    The issuer's presence is the switch. There is no mode flag to get wrong: a server with no
    issuer has nothing to verify, which is what the config guard means by local.
    """
    if not config.logto_endpoint.strip():
        return None
    resource = mcp_resource_url(config.public_base_url)
    return LogtoAuthProvider(
        endpoint=config.logto_endpoint,
        base_url=config.public_base_url,
        resource_base_url=resource,
    )


async def verify_bearer(verifier: JWTVerifier, authorization: str | None) -> AccessToken | None:
    """Verify an ``Authorization`` header. ``None`` for a missing, malformed or invalid token.

    Used by the HTTP face, which sits outside the MCP transport's guard and therefore has to run
    the same check itself rather than assume it happened.
    """
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return await verifier.verify_token(token.strip())
