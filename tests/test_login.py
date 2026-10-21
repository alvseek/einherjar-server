"""The login flow and the token store, without a browser or a real issuer."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from einherjar.auth.login import LoginError, LoginFlow, login
from einherjar.auth.pkce import (
    authorize_url,
    code_challenge,
    code_verifier,
    exchange_code,
    refresh_tokens,
)
from einherjar.auth.tokens import Tokens, TokenStore

ISSUER = "https://auth.example"
CLIENT = "einherjar-cli"
RESOURCE = "https://einherjar.example/mcp"


def _transport(payload: dict | None = None) -> httpx.MockTransport:
    body = payload or {
        "access_token": "access-1",
        "refresh_token": "refresh-1",
        "expires_in": 3600,
        "token_type": "Bearer",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/oidc/token"
        return httpx.Response(200, json=body)

    return httpx.MockTransport(handler)


def test_the_challenge_is_s256_of_the_verifier() -> None:
    verifier = code_verifier()
    challenge = code_challenge(verifier)
    assert verifier != challenge
    assert "=" not in challenge


def test_the_authorization_url_names_the_resource() -> None:
    url = authorize_url(
        issuer=ISSUER,
        client_id=CLIENT,
        redirect_uri="http://127.0.0.1:9999/callback",
        state="state-1",
        challenge="challenge-1",
        resource=RESOURCE,
    )
    query = parse_qs(urlparse(url).query)
    assert url.startswith(f"{ISSUER}/oidc/auth?")
    assert query["resource"] == [RESOURCE]
    assert query["code_challenge_method"] == ["S256"]
    assert query["client_id"] == [CLIENT]


async def test_the_code_is_exchanged_for_tokens() -> None:
    async with httpx.AsyncClient(transport=_transport()) as client:
        tokens = await exchange_code(
            client,
            issuer=ISSUER,
            client_id=CLIENT,
            code="code-1",
            verifier="verifier-1",
            redirect_uri="http://127.0.0.1:9999/callback",
            resource=RESOURCE,
        )
    assert tokens.access_token == "access-1"
    assert tokens.refresh_token == "refresh-1"
    assert not tokens.expired()


async def test_refresh_keeps_the_old_refresh_token_when_the_issuer_omits_one() -> None:
    payload = {"access_token": "access-2", "expires_in": 60}
    async with httpx.AsyncClient(transport=_transport(payload)) as client:
        tokens = await refresh_tokens(
            client,
            issuer=ISSUER,
            client_id=CLIENT,
            refresh_token="refresh-1",
            resource=RESOURCE,
        )
    assert tokens.access_token == "access-2"
    assert tokens.refresh_token == "refresh-1"


def test_the_store_round_trips_in_a_file_without_a_keychain(tmp_path: Path) -> None:
    store = TokenStore(tmp_path / "credentials.json", use_keyring=False)
    assert store.load() is None

    mode = store.save(Tokens(access_token="a", refresh_token="r", expires_at=time.time() + 60))
    loaded = store.load()

    assert mode == "file"
    assert loaded is not None and loaded.access_token == "a"
    store.clear()
    assert store.load() is None


def test_an_expired_access_token_is_inside_the_refresh_window() -> None:
    soon = Tokens(access_token="a", expires_at=time.time() + 30)
    assert soon.expired()
    later = Tokens(access_token="a", expires_at=time.time() + 3600)
    assert not later.expired()


def _callback(redirect: str, state: str, code: str = "code-1") -> None:
    threading.Thread(
        target=lambda: httpx.get(f"{redirect}?code={code}&state={state}", timeout=5),
        daemon=True,
    ).start()


def test_the_listener_receives_the_code_and_checks_the_state() -> None:
    flow = LoginFlow(issuer=ISSUER, client_id=CLIENT, resource=RESOURCE)
    url = flow.start()
    assert flow.redirect_uri is not None
    _callback(flow.redirect_uri, flow.state)
    assert flow.wait(timeout=5) == "code-1"
    flow.close()

    query = parse_qs(urlparse(url).query)
    assert query["state"] == [flow.state]


def test_a_state_mismatch_is_refused() -> None:
    flow = LoginFlow(issuer=ISSUER, client_id=CLIENT, resource=RESOURCE)
    flow.start()
    assert flow.redirect_uri is not None
    _callback(flow.redirect_uri, "not-the-state")
    with pytest.raises(LoginError, match="state did not match"):
        flow.wait(timeout=5)
    flow.close()


async def test_login_completes_end_to_end(tmp_path: Path) -> None:
    store = TokenStore(tmp_path / "credentials.json", use_keyring=False)

    def fake_browser(url: str) -> None:
        query = parse_qs(urlparse(url).query)
        _callback(query["redirect_uri"][0], query["state"][0])

    async with httpx.AsyncClient(transport=_transport()) as client:
        tokens = await login(
            issuer=ISSUER,
            client_id=CLIENT,
            resource=RESOURCE,
            store=store,
            open_browser=fake_browser,
            client=client,
            timeout=5,
        )

    assert tokens.access_token == "access-1"
    assert store.load() is not None
