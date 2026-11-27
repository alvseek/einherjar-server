"""Who the current caller is acting as.

Every fleet row belongs to a tenant, and the tenant is not a setting: it comes from the verified
token of the call in progress, resolved through the same ``user_identity`` mapping Munnin writes.
That is what makes the same Google account land on the same rows through every server in the
family, and a configured value could drift from the login it is meant to mirror.

The shared Workspace Access package owns identity mapping and concurrent account creation.
Email and display name are labels for a new account only, never lookup keys.
"""

from __future__ import annotations

import logging
from typing import Any

from fastmcp.server.auth import AccessToken
from fastmcp.server.dependencies import get_access_token
from valaskjalf_workspace_access import PostgresWorkspaceAccess

_log = logging.getLogger("einherjar.identity")

class MissingTokenError(RuntimeError):
    """Raised when a tenant is asked for on a call that carries no verified token.

    Unreachable in the deployed shape: the guard refuses an unauthenticated request before any
    handler runs. It exists so a route added outside the guard fails loudly on its first call
    rather than quietly serving somebody a tenant.
    """


def claims_to_pair(claims: dict[str, Any]) -> tuple[str, str]:
    """The ``(iss, sub)`` pair a token names, refusing one that names none.

    Separated from the store so the refusal is testable without a database: a verified token with
    no subject names no tenant, and guessing one is the exact failure the pair exists to prevent.
    """
    iss, sub = claims.get("iss"), claims.get("sub")
    if not iss or not sub:
        raise MissingTokenError(
            "verified token carries no issuer/subject pair, so it names no tenant"
        )
    return str(iss), str(sub)


class TenantResolver:
    """Resolves the call's token to a tenant, creating it on a miss."""

    def __init__(self, *, db_url: str) -> None:
        self._access = PostgresWorkspaceAccess(db_url)

    # --- the seam every handler asks ----------------------------------------------

    def current_user_id(self) -> str:
        """The tenant named by the verified token of the MCP call in progress."""
        token = get_access_token()
        if token is None:
            raise MissingTokenError(
                "no verified token on this call — a handler is running outside the "
                "authentication guard"
            )
        return self.for_token(token)

    def for_token(self, token: AccessToken) -> str:
        """The tenant named by one already-verified token, whichever face produced it."""
        claims = token.claims
        iss, sub = claims_to_pair(claims)
        return self.resolve(
            iss,
            sub,
            email=claims.get("email"),
            display_name=claims.get("name"),
        )

    # --- the read and the write -----------------------------------------------------

    def resolve(
        self,
        iss: str,
        sub: str,
        *,
        email: str | None = None,
        display_name: str | None = None,
    ) -> str:
        """The tenant for one issuer-and-subject pair, created if it has never been seen."""
        result = self._access.get_or_create_home(
            iss, sub, email=email, display_name=display_name
        )
        if result.created:
            _log.warning("new tenant created: user_id=%s iss=%s sub=%s", result.user_id, iss, sub)
        return result.user_id

    def resolve_existing(self, iss: str, sub: str) -> str:
        """The tenant for a pair that must already be mapped. For an offline caller, never a login.

        Importing into a tenant that did not exist a moment ago is almost certainly a typo, and it
        is the kind of mistake that takes longest to notice: the rows land somewhere valid and
        nothing errors.
        """
        found = self._access.lookup_home(iss, sub)
        if found is None:
            raise LookupError(
                f"no tenant is mapped to iss={iss!r} sub={sub!r}: sign in once with that account, "
                "or check the pair."
            )
        return found
