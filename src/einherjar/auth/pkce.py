"""Loopback PKCE: the flow `gh` and `aws sso login` use, and the one a laptop can complete.

The browser returns to a port this process is listening on, so the code never travels through a
copy-paste. The verifier never leaves the machine; only its SHA-256 challenge goes to the issuer.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from urllib.parse import urlencode

import httpx

from einherjar.auth.tokens import Tokens

DEFAULT_SCOPES = "openid profile email offline_access"


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def code_verifier() -> str:
    return _b64url(secrets.token_bytes(32))


def code_challenge(verifier: str) -> str:
    return _b64url(hashlib.sha256(verifier.encode("ascii")).digest())


def authorize_url(
    *,
    issuer: str,
    client_id: str,
    redirect_uri: str,
    state: str,
    challenge: str,
    resource: str,
    scope: str = DEFAULT_SCOPES,
) -> str:
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        # The resource binds the token's audience to the fleet's MCP identifier, so the same token
        # is accepted by the MCP face and by the CLI's HTTP calls.
        "resource": resource,
    }
    return f"{issuer.rstrip('/')}/oidc/auth?{urlencode(params)}"


def _tokens_from(payload: dict) -> Tokens:
    if "access_token" not in payload:
        raise RuntimeError(f"the issuer returned no access token: {payload}")
    return Tokens(
        access_token=str(payload["access_token"]),
        refresh_token=payload.get("refresh_token"),
        expires_at=time.time() + float(payload.get("expires_in", 3600)),
        token_type=str(payload.get("token_type", "Bearer")),
    )


async def exchange_code(
    client: httpx.AsyncClient,
    *,
    issuer: str,
    client_id: str,
    code: str,
    verifier: str,
    redirect_uri: str,
    resource: str,
) -> Tokens:
    response = await client.post(
        f"{issuer.rstrip('/')}/oidc/token",
        data={
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": code,
            "code_verifier": verifier,
            "redirect_uri": redirect_uri,
            "resource": resource,
        },
    )
    response.raise_for_status()
    return _tokens_from(response.json())


async def refresh_tokens(
    client: httpx.AsyncClient,
    *,
    issuer: str,
    client_id: str,
    refresh_token: str,
    resource: str,
) -> Tokens:
    response = await client.post(
        f"{issuer.rstrip('/')}/oidc/token",
        data={
            "grant_type": "refresh_token",
            "client_id": client_id,
            "refresh_token": refresh_token,
            "resource": resource,
        },
    )
    response.raise_for_status()
    tokens = _tokens_from(response.json())
    # Some issuers omit the refresh token on refresh; keep the one that still works.
    return Tokens(
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token or refresh_token,
        expires_at=tokens.expires_at,
        token_type=tokens.token_type,
    )
