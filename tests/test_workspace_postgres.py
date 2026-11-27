"""Workspace access across the authenticated HTTP face and MCP registry tools."""

from __future__ import annotations

import os
import uuid

import httpx2
import psycopg
import pytest
from fastapi.testclient import TestClient
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import RemoteAuthProvider
from pydantic import AnyHttpUrl

from einherjar.app import build_app
from tests.test_http import _body, auth_headers

PG = os.getenv("EINHERJAR_PG_TEST_URL")
pytestmark = pytest.mark.skipif(not PG, reason="requires migrated PostgreSQL")


async def test_workspace_reads_and_home_writes(client, mint):
    verifier = client.app.state.verifier
    provider = RemoteAuthProvider(
        token_verifier=verifier,
        authorization_servers=[AnyHttpUrl("https://auth.example/oidc")],
        base_url="https://einherjar.example",
        resource_base_url="https://einherjar.example/mcp",
    )
    app = build_app(
        client.app.state.config,
        auth=provider,
        verifier=verifier,
        repository=client.app.state.repository,
    )
    client = TestClient(app)

    def http_factory(headers=None, auth=None, follow_redirects=True, **_):
        return httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            base_url="http://test",
            headers=headers,
            auth=auth,
            follow_redirects=follow_redirects,
        )

    alice_token, bob_token = mint(uuid.uuid4().hex), mint(uuid.uuid4().hex)
    alice_headers, bob_headers = auth_headers(alice_token), auth_headers(bob_token)
    alice_body, bob_body = _body(), _body()
    alice = client.post("/v1/fleet/sessions", json=alice_body, headers=alice_headers).json()
    bob = client.post("/v1/fleet/sessions", json=bob_body, headers=bob_headers).json()
    a, b = alice["user_id"], bob["user_id"]
    assert a != b
    assert [r["uuid"] for r in client.get("/v1/fleet/sessions", headers=alice_headers).json()] == [
        alice["uuid"]
    ]
    assert client.get(f"/v1/fleet/sessions/{bob['uuid']}", headers=alice_headers).status_code == 404

    transport = StreamableHttpTransport(
        url="http://test/mcp", auth=alice_token, httpx_client_factory=http_factory
    )
    async with app.router.lifespan_context(app):
        async with http_factory() as anonymous:
            response = await anonymous.post(
                "/mcp",
                headers={"Accept": "application/json, text/event-stream"},
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            )
            assert response.status_code == 401
            response = await anonymous.post(
                "/mcp",
                headers={
                    "Accept": "application/json, text/event-stream",
                    "Authorization": f"Bearer {mint(uuid.uuid4().hex, audience='https://other.example')}",
                },
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            )
            assert response.status_code == 401
        async with Client(transport) as mcp:
            await _check_mcp_workspace(mcp, client, alice, bob, alice_headers, alice_body)


async def _check_mcp_workspace(mcp, client, alice, bob, alice_headers, alice_body):
    a, b = alice["user_id"], bob["user_id"]
    assert [r["uuid"] for r in (await mcp.call_tool("fleet_sessions", {})).data] == [alice["uuid"]]
    assert (await mcp.call_tool("fleet_session", {"uuid": alice["uuid"]})).data["user_id"] == a
    assert (await mcp.call_tool("fleet_session", {"uuid": bob["uuid"]})).data is None
    with psycopg.connect(PG) as conn:
        conn.execute(
            "INSERT INTO tenant_access (tenant_user_id, member_user_id, granted_date) "
            "VALUES (%s, %s, '2026-01-01')",
            (b, a),
        )
        conn.execute("UPDATE account SET active_user_id = %s WHERE user_id = %s", (b, a))
    listed = client.get("/v1/fleet/sessions", headers=alice_headers).json()
    assert [r["uuid"] for r in listed] == [bob["uuid"]]
    bob_read = client.get(f"/v1/fleet/sessions/{bob['uuid']}", headers=alice_headers)
    alice_read = client.get(f"/v1/fleet/sessions/{alice['uuid']}", headers=alice_headers)
    assert bob_read.status_code == 200
    assert alice_read.status_code == 404
    assert [r["uuid"] for r in (await mcp.call_tool("fleet_sessions", {})).data] == [bob["uuid"]]
    bob_mcp = (await mcp.call_tool("fleet_session", {"uuid": bob["uuid"]})).data
    assert bob_mcp["uuid"] == bob["uuid"]
    assert (await mcp.call_tool("fleet_session", {"uuid": alice["uuid"]})).data is None
    assert (
        client.patch(
            f"/v1/fleet/sessions/{bob['uuid']}", json={"status": "failed"}, headers=alice_headers
        ).status_code
        == 404
    )

    replay = client.post("/v1/fleet/sessions", json=alice_body, headers=alice_headers).json()
    assert replay["uuid"] == alice["uuid"] and replay["user_id"] == a
    with psycopg.connect(PG) as conn:
        conn.execute(
            "UPDATE tenant_access SET revoked_date = '2026-01-02' "
            "WHERE tenant_user_id = %s AND member_user_id = %s",
            (b, a),
        )
    assert client.get("/v1/fleet/sessions", headers=alice_headers).status_code == 403
    assert client.get(f"/v1/fleet/sessions/{bob['uuid']}", headers=alice_headers).status_code == 403
    with pytest.raises(ToolError, match="no longer accessible"):
        await mcp.call_tool("fleet_sessions", {})
    with pytest.raises(ToolError, match="no longer accessible"):
        await mcp.call_tool("fleet_session", {"uuid": bob["uuid"]})
    closed = client.patch(
        f"/v1/fleet/sessions/{alice['uuid']}",
        json={"status": "completed"},
        headers=alice_headers,
    )
    assert closed.status_code == 200
    assert (
        client.post("/v1/fleet/sessions", json=alice_body, headers=alice_headers).json()["status"]
        == "completed"
    )
    with psycopg.connect(PG) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM fleet_session WHERE user_id = %s AND machine = %s "
                "AND session_ref = %s",
                (a, alice_body["machine"], alice_body["session_ref"]),
            ).fetchone()[0]
            == 1
        )
        conn.execute("UPDATE account SET active_user_id = NULL WHERE user_id = %s", (a,))
    assert (
        client.get(f"/v1/fleet/sessions/{alice['uuid']}", headers=alice_headers).json()["status"]
        == "completed"
    )
    assert (await mcp.call_tool("fleet_session", {"uuid": alice["uuid"]})).data["user_id"] == a
    assert [r["uuid"] for r in (await mcp.call_tool("fleet_sessions", {})).data] == [alice["uuid"]]
