"""Shared fixtures: a real store, a minted token, and a client wired to both."""

from __future__ import annotations

import os
import time
from types import SimpleNamespace
from typing import Callable

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from fastmcp.server.auth.providers.jwt import JWTVerifier

from einherjar.app import build_app
from einherjar.configuration.config import Config
from einherjar.data.repository import FleetRepository

ISSUER = "https://auth.example/oidc"
RESOURCE = "https://einherjar.example/mcp"
PG = os.getenv("EINHERJAR_PG_TEST_URL")


@pytest.fixture(scope="session")
def keypair() -> tuple[bytes, bytes]:
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


@pytest.fixture()
def mint(keypair: tuple[bytes, bytes]) -> Callable[..., str]:
    private, _ = keypair

    def _mint(sub: str, *, audience: str = RESOURCE) -> str:
        now = int(time.time())
        return jwt.encode(
            {
                "iss": ISSUER,
                "sub": sub,
                "aud": audience,
                "iat": now,
                "exp": now + 300,
            },
            private,
            algorithm="RS256",
        )

    return _mint


@pytest.fixture()
def repository() -> FleetRepository:
    if not PG:
        pytest.skip("no EINHERJAR_PG_TEST_URL")
    repo = FleetRepository(db_url=PG)
    repo.ensure_schema()
    return repo


@pytest.fixture()
def client(keypair: tuple[bytes, bytes], repository: FleetRepository) -> TestClient:
    _, public = keypair
    verifier = JWTVerifier(
        public_key=public,
        issuer=ISSUER,
        audience=RESOURCE,
        algorithm="RS256",
    )
    config = Config(
        db_url=PG or "",
        logto_endpoint="https://auth.example",
        public_base_url="https://einherjar.example",
    )
    app = build_app(
        config,
        auth=SimpleNamespace(token_verifier=verifier),
        repository=repository,
    )
    return TestClient(app)
