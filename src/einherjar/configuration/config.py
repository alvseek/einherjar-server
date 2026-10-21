"""Einherjar configuration.

**Two shapes, one switch: the issuer.** With an issuer configured, every request carries a token
this server verifies, and the token names the tenant whose rows the call may touch. With none, the
server runs the **local** shape: loopback only and unverified, and the guards below refuse any
configuration that would leave it reachable from elsewhere.

The store is required in both shapes. A local run is a way to answer `/health` and exercise the
routes, not a way to keep fleet rows somewhere else: there is no file backend and no fallback.

``db_url`` is the one secret this server holds. It is the memory database's DSN, because the
service reads ``user_identity`` for tenant resolution and creates its own ``fleet_session`` table.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse

DEFAULT_PUBLIC_BASE_URL = "http://127.0.0.1:8220"
DEFAULT_PORT = 8220

_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


@dataclass(frozen=True)
class Config:
    """Runtime settings. Env vars override defaults where present."""

    host: str = "127.0.0.1"
    # 8220, beside Munnin's 8200 and Hermod's 8210: a coding client connects all three.
    port: int = DEFAULT_PORT
    # The memory database. Required, because the service reads `user_identity` and owns exactly
    # one table in the same database. It carries the password, so it is the one secret here.
    db_url: str = ""
    # Logto tenant endpoint, e.g. "https://auth.lok.quest". When set, tokens are verified against
    # it; when absent, the local shape applies and the guards below take over.
    logto_endpoint: str = ""
    # This server's public URL. Tokens bind to it as their audience, so it must match the
    # Resource Indicator registered with the issuer character for character.
    public_base_url: str = DEFAULT_PUBLIC_BASE_URL
    # Local mode's second guard, reached only when no issuer is configured. A process bound to
    # 0.0.0.0 with no authentication is open to every network the machine is on, unless the
    # operator states that something in front of it keeps it local.
    local_bind_all: bool = False


def is_loopback_host(host: str) -> bool:
    return host.strip().lower() in _LOOPBACK_HOSTS


def is_loopback_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and is_loopback_host(parsed.hostname or "")


def load_config() -> Config:
    """Build config from defaults plus optional ``EINHERJAR_*`` overrides."""
    return Config(
        host=os.getenv("EINHERJAR_HOST", "127.0.0.1"),
        port=int(os.getenv("EINHERJAR_PORT", str(DEFAULT_PORT))),
        db_url=os.getenv("EINHERJAR_DB_URL", ""),
        logto_endpoint=os.getenv("EINHERJAR_LOGTO_ENDPOINT", ""),
        public_base_url=os.getenv("EINHERJAR_PUBLIC_BASE_URL", DEFAULT_PUBLIC_BASE_URL),
        local_bind_all=os.getenv("EINHERJAR_LOCAL_BIND_ALL", "").lower() in {"1", "true", "yes"},
    )


def validate(config: Config) -> None:
    """Refuse a configuration that cannot be what it claims to be.

    A missing DSN is refused in both shapes, because the service has nowhere else to keep a row.
    In the local shape, the bind and the advertised URL must both be loopback: without an issuer
    there is nobody to verify, so reachability is the only thing separating a laptop from a
    network service.
    """
    if not config.db_url.strip():
        raise ValueError(
            "EINHERJAR_DB_URL is required: the service owns its fleet_session table there and "
            "reads user_identity from the same database."
        )

    if not config.logto_endpoint.strip():
        if not is_loopback_host(config.host) and not config.local_bind_all:
            raise ValueError(
                f"refusing an unauthenticated bind on {config.host!r}: with no issuer configured "
                "the server is local only. Bind loopback, or set EINHERJAR_LOCAL_BIND_ALL=1 when "
                "something in front of it keeps the port private."
            )
        if not config.local_bind_all and not is_loopback_url(config.public_base_url):
            raise ValueError(
                f"refusing a public URL {config.public_base_url!r} in local mode: with no issuer, "
                "the advertised URL must be loopback too."
            )
