"""The write face: the tenant comes from the token, and the contract is checked."""

from __future__ import annotations

import uuid
from typing import Callable

from fastapi.testclient import TestClient


def _sub() -> str:
    return f"user-{uuid.uuid4().hex}"


def auth_headers(token: str, *, contract: str | None = "1") -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if contract is not None:
        headers["X-Einherjar-Contract"] = contract
    return headers


def _body(ref: str | None = None) -> dict:
    return {
        "machine": "machine-a",
        "session_ref": ref or f"ses_{uuid.uuid4().hex}",
        "platform": "opencode",
        "project": "agent-memory-fleet",
        "machine_label": "laptop",
        "domain": "admiral",
        "theme": "review the plan",
    }


def test_a_consult_is_recorded_under_the_token_tenant(
    client: TestClient, mint: Callable[..., str]
) -> None:
    sub = _sub()
    response = client.post(
        "/v1/fleet/sessions", json=_body(), headers=auth_headers(mint(sub))
    )

    assert response.status_code == 200
    row = response.json()
    assert row["project"] == "agent-memory-fleet"
    assert row["status"] == "active"
    assert row["machine"] == "machine-a"
    assert row["user_id"] != sub  # a minted tenant id, never the issuer's subject


def test_the_same_key_updates_in_place(client: TestClient, mint: Callable[..., str]) -> None:
    token = mint(_sub())
    ref = f"ses_{uuid.uuid4().hex}"
    first = client.post("/v1/fleet/sessions", json=_body(ref), headers=auth_headers(token)).json()
    second = client.post(
        "/v1/fleet/sessions",
        json={**_body(ref), "theme": "changed"},
        headers=auth_headers(token),
    ).json()

    assert first["uuid"] == second["uuid"]
    assert second["theme"] == "changed"


def test_a_missing_token_is_refused(client: TestClient) -> None:
    response = client.post("/v1/fleet/sessions", json=_body())
    assert response.status_code == 401


def test_a_token_for_another_resource_is_refused(
    client: TestClient, mint: Callable[..., str]
) -> None:
    token = mint(_sub(), audience="https://other.example/mcp")
    response = client.post(
        "/v1/fleet/sessions", json=_body(), headers=auth_headers(token)
    )
    assert response.status_code == 401


def test_a_missing_contract_asks_for_an_upgrade(
    client: TestClient, mint: Callable[..., str]
) -> None:
    response = client.post(
        "/v1/fleet/sessions",
        json=_body(),
        headers=auth_headers(mint(_sub()), contract=None),
    )
    assert response.status_code == 400
    assert "Upgrade the einherjar CLI" in response.json()["detail"]


def test_completion_and_read_back(client: TestClient, mint: Callable[..., str]) -> None:
    token = mint(_sub())
    row = client.post("/v1/fleet/sessions", json=_body(), headers=auth_headers(token)).json()

    patched = client.patch(
        f"/v1/fleet/sessions/{row['uuid']}",
        json={"status": "completed"},
        headers=auth_headers(token),
    )
    assert patched.status_code == 200
    assert patched.json()["status"] == "completed"
    assert patched.json()["ended_at"] is not None

    read = client.get(f"/v1/fleet/sessions/{row['uuid']}", headers=auth_headers(token))
    assert read.status_code == 200
    assert read.json()["uuid"] == row["uuid"]


def test_a_foreign_row_is_not_found(client: TestClient, mint: Callable[..., str]) -> None:
    owner = mint(_sub())
    stranger = mint(_sub())
    row = client.post("/v1/fleet/sessions", json=_body(), headers=auth_headers(owner)).json()

    patched = client.patch(
        f"/v1/fleet/sessions/{row['uuid']}",
        json={"status": "failed"},
        headers=auth_headers(stranger),
    )
    read = client.get(f"/v1/fleet/sessions/{row['uuid']}", headers=auth_headers(stranger))

    assert patched.status_code == 404
    assert read.status_code == 404


def test_an_unknown_status_is_a_validation_error(
    client: TestClient, mint: Callable[..., str]
) -> None:
    token = mint(_sub())
    row = client.post("/v1/fleet/sessions", json=_body(), headers=auth_headers(token)).json()
    response = client.patch(
        f"/v1/fleet/sessions/{row['uuid']}",
        json={"status": "paused"},
        headers=auth_headers(token),
    )
    assert response.status_code == 422
