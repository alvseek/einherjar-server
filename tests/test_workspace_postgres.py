"""Workspace access across the authenticated HTTP face and MCP registry tools."""

from __future__ import annotations

import os
import uuid

import psycopg
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from tests.test_http import _body, auth_headers

PG = os.getenv("EINHERJAR_PG_TEST_URL")
pytestmark = pytest.mark.skipif(not PG, reason="requires migrated PostgreSQL")


async def test_workspace_reads_and_home_writes(client, mint, monkeypatch):
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

    # Verify the exact token that the MCP request's token dependency will return.
    verified = await client.app.state.verifier.verify_token(alice_token)
    assert verified is not None
    monkeypatch.setattr("einherjar.business.tenant.get_access_token", lambda: verified)
    async with Client(client.app.state.mcp) as mcp:
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
        assert [r["uuid"] for r in (await mcp.call_tool("fleet_sessions", {})).data] == [
            bob["uuid"]
        ]
        bob_mcp = (await mcp.call_tool("fleet_session", {"uuid": bob["uuid"]})).data
        assert bob_mcp["uuid"] == bob["uuid"]
        assert (await mcp.call_tool("fleet_session", {"uuid": alice["uuid"]})).data is None
        assert client.patch(
            f"/v1/fleet/sessions/{bob['uuid']}", json={"status": "failed"}, headers=alice_headers
        ).status_code == 404

        replay = client.post("/v1/fleet/sessions", json=alice_body, headers=alice_headers).json()
        assert replay["uuid"] == alice["uuid"] and replay["user_id"] == a
        with psycopg.connect(PG) as conn:
            conn.execute(
                "UPDATE tenant_access SET revoked_date = '2026-01-02' "
                "WHERE tenant_user_id = %s AND member_user_id = %s",
                (b, a),
            )
        assert client.get("/v1/fleet/sessions", headers=alice_headers).status_code == 403
        assert client.get(
            f"/v1/fleet/sessions/{bob['uuid']}", headers=alice_headers
        ).status_code == 403
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
        assert client.post("/v1/fleet/sessions", json=alice_body, headers=alice_headers).json()[
            "status"
        ] == "completed"
        with psycopg.connect(PG) as conn:
            assert conn.execute(
                "SELECT count(*) FROM fleet_session WHERE user_id = %s AND machine = %s "
                "AND session_ref = %s",
                (a, alice_body["machine"], alice_body["session_ref"]),
            ).fetchone()[0] == 1
            conn.execute("UPDATE account SET active_user_id = NULL WHERE user_id = %s", (a,))
        assert client.get(f"/v1/fleet/sessions/{alice['uuid']}", headers=alice_headers).json()[
            "status"
        ] == "completed"
        assert (await mcp.call_tool("fleet_session", {"uuid": alice["uuid"]})).data["user_id"] == a
