"""The registry client: one audience, one contract header, one refresh."""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
import pytest

from einherjar.auth.tokens import Tokens, TokenStore
from einherjar.registry.client import RegistryClient, RegistryError

BASE = "https://einherjar.example"
ISSUER = "https://auth.example"
CLIENT = "einherjar-cli"
RESOURCE = f"{BASE}/mcp"


def _store(tmp_path: Path, *, expires_in: float = 3600) -> TokenStore:
    store = TokenStore(tmp_path / "credentials.json", use_keyring=False)
    store.save(
        Tokens(
            access_token="access-1",
            refresh_token="refresh-1",
            expires_at=time.time() + expires_in,
        )
    )
    return store


def _client(http: httpx.AsyncClient, store: TokenStore) -> RegistryClient:
    return RegistryClient(
        base_url=BASE,
        store=store,
        issuer=ISSUER,
        client_id=CLIENT,
        resource=RESOURCE,
        client=http,
    )


async def test_create_posts_the_row_with_the_contract_and_bearer(tmp_path: Path) -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["headers"] = request.headers
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"uuid": "u-1", "session_ref": "ses_1"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        registry = _client(http, _store(tmp_path))
        row = await registry.create_session(
            {"machine": "m", "session_ref": "ses_1", "platform": "opencode"}
        )

    assert row["uuid"] == "u-1"
    assert seen["path"] == "/v1/fleet/sessions"
    assert seen["headers"]["x-einherjar-contract"] == "1"
    assert seen["headers"]["authorization"] == "Bearer access-1"
    assert seen["body"]["session_ref"] == "ses_1"


async def test_an_expired_token_refreshes_once_and_is_saved(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oidc/token":
            return httpx.Response(
                200,
                json={"access_token": "access-2", "refresh_token": "refresh-2", "expires_in": 3600},
            )
        assert request.headers["authorization"] == "Bearer access-2"
        return httpx.Response(200, json=[{"uuid": "u-1"}])

    store = _store(tmp_path, expires_in=-10)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        registry = _client(http, store)
        rows = await registry.list_sessions()

    assert rows == [{"uuid": "u-1"}]
    saved = store.load()
    assert saved is not None and saved.access_token == "access-2"


async def test_a_missing_token_asks_for_a_login(tmp_path: Path) -> None:
    store = TokenStore(tmp_path / "credentials.json", use_keyring=False)
    transport = httpx.MockTransport(lambda request: httpx.Response(500))
    async with httpx.AsyncClient(transport=transport) as http:
        with pytest.raises(RegistryError, match="not signed in"):
            await _client(http, store).list_sessions()


async def test_a_refused_token_asks_for_a_login_again(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "authentication required"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(RegistryError, match="run `einherjar login`"):
            await _client(http, _store(tmp_path)).list_sessions()


async def test_a_server_error_surfaces_its_detail(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="store unavailable")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(RegistryError, match="store unavailable"):
            await _client(http, _store(tmp_path)).list_sessions()


async def test_the_list_builds_its_query(tmp_path: Path) -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await _client(http, _store(tmp_path)).list_sessions(project="p", status="active")

    assert seen["url"] == f"{BASE}/v1/fleet/sessions?project=p&status=active"
