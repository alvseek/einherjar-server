"""The MCP face: two read tools, and the queries behind them stay tenant-scoped."""

from __future__ import annotations

import uuid

from fastmcp import Client

from einherjar.api.mcp import build_mcp
from einherjar.business.queries import FleetQueries
from einherjar.business.tenant import TenantResolver
from einherjar.data.repository import FleetRepository


async def test_the_mcp_face_exposes_only_reads() -> None:
    mcp = build_mcp(
        auth=None,
        resolver=TenantResolver(db_url="unused"),
        queries=FleetQueries(FleetRepository(db_url="unused")),
    )
    async with Client(mcp) as client:
        tools = await client.list_tools()

    assert {tool.name for tool in tools} == {"fleet_sessions", "fleet_session"}


def test_queries_never_cross_a_tenant(repository: FleetRepository) -> None:
    queries = FleetQueries(repository)
    mine = f"user-{uuid.uuid4().hex}"
    theirs = f"user-{uuid.uuid4().hex}"

    row = repository.upsert_session(
        user_id=mine, machine="machine-a", session_ref=f"ses_{uuid.uuid4().hex}", platform="claude"
    )

    assert [r["uuid"] for r in queries.list_sessions(user_id=mine)] == [row.uuid]
    assert queries.list_sessions(user_id=theirs) == []
    assert queries.get_session(user_id=mine, uuid=row.uuid) is not None
    assert queries.get_session(user_id=theirs, uuid=row.uuid) is None
    assert queries.get_session(user_id=mine, uuid="unknown") is None


def test_discovery_is_served_from_the_root_and_mcp_does_not_redirect() -> None:
    from fastapi.testclient import TestClient

    from einherjar.app import build_app
    from einherjar.auth.verifier import LogtoAuthProvider, mcp_resource_url
    from einherjar.configuration.config import Config

    resource = mcp_resource_url("https://einherjar.example")
    provider = LogtoAuthProvider(
        endpoint="https://auth.example",
        base_url="https://einherjar.example",
        resource_base_url=resource,
    )
    config = Config(
        db_url="postgresql://einherjar:secret@127.0.0.1:5432/valaskjalf",
        logto_endpoint="https://auth.example",
        public_base_url="https://einherjar.example",
    )
    app = build_app(config, auth=provider, verifier=provider.token_verifier)

    with TestClient(app) as client:
        discovery = client.get("/.well-known/oauth-protected-resource/mcp")
        bare = client.get("/mcp", follow_redirects=False)

    assert discovery.status_code == 200
    assert resource in discovery.text
    # The specification tells a client to drop the slash; a 307 here would be built from the
    # scheme uvicorn saw and would bounce it to plaintext behind a TLS proxy.
    assert bare.status_code != 307
