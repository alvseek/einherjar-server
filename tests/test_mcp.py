"""The MCP face: two read tools, and the queries behind them stay tenant-scoped."""

from __future__ import annotations

import uuid

from fastmcp import Client

from einherjar.api.mcp import build_mcp
from einherjar.business.queries import FleetQueries
from einherjar.business.tenant import TenantResolver
from einherjar.content.loader import ContentLoader
from einherjar.data.repository import FleetRepository


def _fake_content(tmp_path):
    (tmp_path / "procedures").mkdir()
    (tmp_path / "procedures" / "ask-agent.md").write_text(
        "# Ask Agent\n\nAsk a teammate.\n\n$ARGUMENTS\n", encoding="utf-8"
    )
    (tmp_path / "templates").mkdir()
    (tmp_path / "templates" / "fleet-agents-template.md").write_text(
        "# Roster\n\n| a |\n", encoding="utf-8"
    )
    return ContentLoader(tmp_path)


async def test_the_mcp_face_exposes_reads_and_content_only(tmp_path) -> None:
    mcp = build_mcp(
        auth=None,
        resolver=TenantResolver(db_url="unused"),
        queries=FleetQueries(FleetRepository(db_url="unused")),
        content=_fake_content(tmp_path),
    )
    async with Client(mcp) as client:
        tools = await client.list_tools()

    assert {tool.name for tool in tools} == {
        "help",
        "fleet_sessions",
        "fleet_session",
        "list_procedures",
        "read_procedure",
        "list_resources",
        "read_resource",
    }

    for tool in tools:
        assert tool.description
        for name, parameter in tool.input_schema.get("properties", {}).items():
            assert parameter.get("description"), (tool.name, name)

    by_name = {tool.name: tool for tool in tools}
    assert by_name["fleet_session"].input_schema["required"] == ["uuid"]
    assert by_name["read_procedure"].input_schema["required"] == ["name"]
    assert by_name["read_resource"].input_schema["required"] == ["name"]
    for tool_name, parameters in {
        "fleet_sessions": ("project", "status"),
        "read_procedure": ("argument",),
    }.items():
        schema = by_name[tool_name].input_schema
        for name in parameters:
            assert name not in schema.get("required", [])
            assert schema["properties"][name]["default"] is None
    assert (
        "Prefixes are not resolved"
        in (by_name["fleet_session"].input_schema["properties"]["uuid"]["description"])
    )
    assert "not a live health check" in by_name["fleet_sessions"].description


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


def test_discovery_is_served_from_the_root_and_mcp_does_not_redirect(content_root) -> None:
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
        content_root=content_root,
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
