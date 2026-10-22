"""The CLI's client of its own server.

One audience, one token: the CLI asks for the fleet's MCP resource identifier, which is what the
service verifies on both faces. A 401 means sign in again; anything else that is not 2xx surfaces
the server's detail rather than a bare status.

The client refreshes once, silently, when the access token is inside its window. It never retries a
second time on a 401: a token that refreshed and was still refused is a configuration problem, not
a clock.
"""

from __future__ import annotations

import httpx

from einherjar.auth.pkce import refresh_tokens
from einherjar.auth.tokens import TokenStore

CONTRACT = "1"


class RegistryError(RuntimeError):
    """A registry call could not be completed, or the caller is not signed in."""


class RegistryClient:
    def __init__(
        self,
        *,
        base_url: str,
        store: TokenStore,
        issuer: str,
        client_id: str,
        resource: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._store = store
        self._issuer = issuer
        self._client_id = client_id
        self._resource = resource
        self._client = client
        self._owned = client is None

    async def __aenter__(self) -> "RegistryClient":
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30)
        return self

    async def __aexit__(self, *_exc) -> None:
        if self._owned and self._client is not None:
            await self._client.aclose()

    async def access_token(self) -> str:
        tokens = self._store.load()
        if tokens is None:
            raise RegistryError("not signed in: run `einherjar login`")
        if not tokens.expired():
            return tokens.access_token
        if not tokens.refresh_token:
            raise RegistryError(
                "the session expired and has no refresh token: run `einherjar login`"
            )
        assert self._client is not None
        try:
            refreshed = await refresh_tokens(
                self._client,
                issuer=self._issuer,
                client_id=self._client_id,
                refresh_token=tokens.refresh_token,
                resource=self._resource,
            )
        except httpx.HTTPError as exc:
            raise RegistryError(f"could not refresh the session: {exc}") from exc
        self._store.save(refreshed)
        return refreshed.access_token

    async def _request(self, method: str, path: str, *, json: dict | None = None) -> object:
        assert self._client is not None
        token = await self.access_token()
        try:
            response = await self._client.request(
                method,
                f"{self._base}{path}",
                json=json,
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Einherjar-Contract": CONTRACT,
                },
            )
        except httpx.HTTPError as exc:
            # A dead registry is the case the pending buffer exists for; it must arrive as a
            # RegistryError so the caller buffers instead of crashing.
            raise RegistryError(f"the registry is unreachable: {exc}") from exc
        if response.status_code == 401:
            raise RegistryError("the registry refused the token: run `einherjar login`")
        if response.status_code >= 400:
            raise RegistryError(f"{method} {path} failed ({response.status_code}): {response.text}")
        return response.json()

    async def create_session(self, payload: dict) -> dict:
        return await self._request("POST", "/v1/fleet/sessions", json=payload)  # type: ignore[return-value]

    async def update_session(self, uuid: str, *, status: str, error: str | None = None) -> dict:
        return await self._request(  # type: ignore[return-value]
            "PATCH",
            f"/v1/fleet/sessions/{uuid}",
            json={"status": status, "error": error},
        )

    async def get_session(self, uuid: str) -> dict:
        return await self._request("GET", f"/v1/fleet/sessions/{uuid}")  # type: ignore[return-value]

    async def list_sessions(
        self, *, project: str | None = None, status: str | None = None
    ) -> list[dict]:
        params = {k: v for k, v in (("project", project), ("status", status)) if v}
        query = httpx.QueryParams(params)
        suffix = f"?{query}" if query else ""
        return await self._request("GET", f"/v1/fleet/sessions{suffix}")  # type: ignore[return-value]
