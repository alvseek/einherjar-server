"""Who the current caller is acting as.

Every fleet row belongs to a tenant, and the tenant is not a setting: it comes from the verified
token of the call in progress, resolved through the same ``user_identity`` mapping Munnin writes.
That is what makes the same Google account land on the same rows through every server in the
family, and a configured value could drift from the login it is meant to mirror.

The mechanism is Munnin's, deliberately, including the parts that look optional:

- **An unmapped pair creates a tenant rather than refusing one.** Admission is the authorization
  server's job; a second gate here would have to agree with a dashboard forever, with nothing
  reporting divergence. Creation is logged loudly instead.
- **The tenant id is ours, never the issuer's subject.** ``user_id`` is minted here.
- **The write is re-read afterwards**, because two simultaneous first logins for one pair both
  reach the create and only one row survives the idempotent insert.
- **Email and display name are labels for a new tenant only, never a lookup key.**
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any
from uuid import uuid4

import psycopg
from fastmcp.server.auth import AccessToken
from fastmcp.server.dependencies import get_access_token
from psycopg.rows import tuple_row

_log = logging.getLogger("einherjar.identity")

#: One row per (issuer, subject) pair, which is the pair OpenID Connect guarantees to be stable.
_LINK = (
    "INSERT INTO user_identity (iss, sub, user_id, linked_date) VALUES (%s, %s, %s, %s) "
    "ON CONFLICT (iss, sub) DO NOTHING"
)

_ACCOUNT = (
    "INSERT INTO account (user_id, display_name, email, created_date) "
    "VALUES (%s, %s, %s, now()::date::text)"
)


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
        self._db_url = db_url

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
        with psycopg.connect(self._db_url) as conn, conn.cursor(row_factory=tuple_row) as cur:
            cur.execute(
                "SELECT user_id FROM user_identity WHERE iss = %s AND sub = %s", (iss, sub)
            )
            found = cur.fetchone()
            if found is not None:
                return found[0]

            user_id = uuid4().hex
            cur.execute(_ACCOUNT, (user_id, display_name, email))
            cur.execute(_LINK, (iss, sub, user_id, _today()))
            # Re-read rather than returning what we minted: a concurrent first login for the same
            # pair reaches the same branch, and only one of the two rows survives.
            cur.execute(
                "SELECT user_id FROM user_identity WHERE iss = %s AND sub = %s", (iss, sub)
            )
            settled = cur.fetchone()
            conn.commit()

        resolved = settled[0] if settled is not None else user_id
        # WARNING, not INFO: a tenant appearing should only happen when somebody was invited.
        _log.warning("new tenant created: user_id=%s iss=%s sub=%s", resolved, iss, sub)
        return resolved

    def resolve_existing(self, iss: str, sub: str) -> str:
        """The tenant for a pair that must already be mapped. For an offline caller, never a login.

        Importing into a tenant that did not exist a moment ago is almost certainly a typo, and it
        is the kind of mistake that takes longest to notice: the rows land somewhere valid and
        nothing errors.
        """
        with psycopg.connect(self._db_url, row_factory=tuple_row) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT user_id FROM user_identity WHERE iss = %s AND sub = %s", (iss, sub)
            )
            found = cur.fetchone()
        if found is None:
            raise LookupError(
                f"no tenant is mapped to iss={iss!r} sub={sub!r}: sign in once with that account, "
                "or check the pair."
            )
        return found[0]


def _today() -> str:
    return date.today().isoformat()
