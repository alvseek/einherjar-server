"""The verifier and the tenant pair: the two facts every store call depends on."""

from __future__ import annotations

import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastmcp.server.auth.providers.jwt import JWTVerifier

from einherjar.auth.verifier import LogtoAuthProvider, build_auth, mcp_resource_url, verify_bearer
from einherjar.business.tenant import MissingTokenError, claims_to_pair
from einherjar.configuration.config import Config

ISSUER = "https://auth.example/oidc"
RESOURCE = "https://einherjar.example/mcp"


def _keypair() -> tuple[bytes, bytes]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private, public


def _token(private: bytes, **overrides: object) -> str:
    now = int(time.time())
    claims: dict[str, object] = {
        "iss": ISSUER,
        "sub": "subject-1",
        "aud": RESOURCE,
        "iat": now,
        "exp": now + 300,
    }
    claims.update(overrides)
    return jwt.encode(claims, private, algorithm="RS256")


def _verifier(public: bytes, audience: str = RESOURCE) -> JWTVerifier:
    return JWTVerifier(
        public_key=public,
        issuer=ISSUER,
        audience=audience,
        algorithm="RS256",
    )


async def test_a_token_for_this_resource_verifies() -> None:
    private, public = _keypair()
    access = await _verifier(public).verify_token(_token(private))
    assert access is not None
    assert access.claims["sub"] == "subject-1"


async def test_a_token_for_another_resource_is_refused() -> None:
    private, public = _keypair()
    wrong = _token(private, aud="https://other.example/mcp")
    assert await _verifier(public).verify_token(wrong) is None


async def test_a_malformed_header_is_refused() -> None:
    _, public = _keypair()
    verifier = _verifier(public)
    assert await verify_bearer(verifier, None) is None
    assert await verify_bearer(verifier, "Basic abc") is None
    assert await verify_bearer(verifier, "Bearer ") is None
    assert await verify_bearer(verifier, "Bearer not-a-jwt") is None


def test_claims_without_a_subject_name_no_tenant() -> None:
    with pytest.raises(MissingTokenError):
        claims_to_pair({"iss": ISSUER})
    with pytest.raises(MissingTokenError):
        claims_to_pair({"sub": "subject-1"})
    assert claims_to_pair({"iss": ISSUER, "sub": "subject-1"}) == (ISSUER, "subject-1")


def test_the_audience_is_bound_to_the_resource_url() -> None:
    """The trap the port exists to avoid: without this, the audience check never runs."""
    config = Config(
        db_url="postgresql://einherjar:secret@127.0.0.1:5432/valaskjalf",
        logto_endpoint="https://auth.example",
        public_base_url="https://einherjar.example",
    )
    provider = build_auth(config)
    assert isinstance(provider, LogtoAuthProvider)

    provider.set_mcp_path("/mcp")

    assert provider.token_verifier.audience == mcp_resource_url("https://einherjar.example")


def test_local_mode_has_no_auth_provider() -> None:
    config = Config(db_url="postgresql://einherjar:secret@127.0.0.1:5432/valaskjalf")
    assert build_auth(config) is None
