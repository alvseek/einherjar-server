"""The install resource: instructions an agent can follow, written from the served artifact.

The fixture wheel is wheel-shaped metadata, not a proven installable wheel — building and installing
a real one is the opt-in packaging test's job. What is checked here is that the native resource, the
template tools and HTTP discovery all describe the same file, and that a missing artifact is
explained rather than papered over with an invented URL.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from fastmcp import Client, FastMCP

from einherjar import __version__
from einherjar.api.mcp import (
    CLIENT_INSTALL_NAME,
    CLIENT_INSTALL_URI,
    build_mcp,
)
from einherjar.app import build_app
from einherjar.business.queries import FleetQueries
from einherjar.business.tenant import TenantResolver
from einherjar.configuration.config import Config
from einherjar.content.loader import ContentLoader
from einherjar.data.repository import FleetRepository
from einherjar.distribution.install import client_install_text
from tests.test_distribution import make_wheel

VERSION = "4.5.6"
FILENAME = f"einherjar-{VERSION}-py3-none-any.whl"
BASE = "https://einherjar.example"
METADATA_URL = f"{BASE}/v1/resources/cli"
WHEEL_URL = f"{METADATA_URL}/{FILENAME}"
DSN = "postgresql://einherjar:secret@127.0.0.1:5432/valaskjalf"
TOOLS = {
    "fleet_sessions",
    "fleet_session",
    "list_procedures",
    "read_procedure",
    "list_resources",
    "read_resource",
}


def _content(root: Path) -> ContentLoader:
    (root / "procedures").mkdir(parents=True)
    (root / "procedures" / "ask-agent.md").write_text(
        "# Ask Agent\n\nAsk a teammate.\n\n$ARGUMENTS\n", encoding="utf-8"
    )
    (root / "templates").mkdir()
    (root / "templates" / "fleet-agents-template.md").write_text(
        "# Roster\n\n| a |\n", encoding="utf-8"
    )
    return ContentLoader(root)


def _mcp(tmp_path: Path, dist: Path) -> FastMCP:
    return build_mcp(
        auth=None,
        resolver=TenantResolver(db_url="unused"),
        queries=FleetQueries(FleetRepository(db_url="unused")),
        content=_content(tmp_path / "content"),
        cli_dist_dir=dist,
        public_base_url=BASE,
    )


async def test_the_native_resource_and_the_tool_return_the_same_text(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    make_wheel(dist, version=VERSION)

    async with Client(_mcp(tmp_path, dist)) as client:
        native = await client.read_resource(CLIENT_INSTALL_URI)
        through_tool = await client.call_tool("read_resource", {"name": CLIENT_INSTALL_NAME})

    assert len(native) == 1
    assert native[0].text == through_tool.data["content"]


async def test_the_resource_list_names_it_and_keeps_the_templates(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    make_wheel(dist, version=VERSION)

    async with Client(_mcp(tmp_path, dist)) as client:
        resources = await client.list_resources()

    listed = {r.name: str(r.uri) for r in resources}
    assert listed[CLIENT_INSTALL_NAME] == CLIENT_INSTALL_URI
    assert "resource://templates/fleet-agents-template" in listed.values()


async def test_the_instructions_describe_the_actual_artifact(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    payload = make_wheel(dist, version=VERSION).read_bytes()

    async with Client(_mcp(tmp_path, dist)) as client:
        text = (await client.read_resource(CLIENT_INSTALL_URI))[0].text

    assert f"`einherjar` {VERSION}" in text
    assert WHEEL_URL in text
    assert hashlib.sha256(payload).hexdigest() in text
    assert f"{len(payload):,} bytes" in text
    assert f"uv tool install --force {WHEEL_URL}" in text
    assert f"Metadata: {METADATA_URL}" in text

    # The artifact's version, never the server's: nothing here is copied from __version__.
    assert __version__ != VERSION
    assert __version__ not in text

    # Prerequisites, both harnesses, the explicit upgrade, and the separate sign-in.
    assert "Python 3.12" in text
    assert "OpenCode" in text and "Claude Code" in text
    assert "Upgrades are explicit" in text
    assert "replaces the old combined package" in text
    assert "sign-in, machine id and pending records" in text
    assert "einherjar login" in text
    assert "einherjar install --harness opencode" in text
    assert "einherjar install --harness claude" in text
    assert "download and this metadata are public" in text

    # Instructions, never payload.
    assert "UEsDB" not in text
    assert len(text) < 4_000


async def test_an_absent_artifact_is_explained_not_invented(tmp_path: Path) -> None:
    async with Client(_mcp(tmp_path, tmp_path / "absent")) as client:
        text = (await client.read_resource(CLIENT_INSTALL_URI))[0].text

    assert "no CLI artifact directory" in text
    assert METADATA_URL in text
    assert "503" in text
    assert "uv tool install --force" not in text
    assert ".whl" not in text


async def test_a_corrupt_artifact_is_explained(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / FILENAME).write_bytes(b"truncated")

    async with Client(_mcp(tmp_path, dist)) as client:
        text = (await client.read_resource(CLIENT_INSTALL_URI))[0].text

    assert "not a readable wheel" in text
    assert "uv tool install --force" not in text


async def test_build_mcp_without_an_artifact_directory_still_offers_the_resource(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Existing direct callers pass no distribution settings; the resource must stay useful."""
    monkeypatch.chdir(tmp_path)
    mcp = build_mcp(
        auth=None,
        resolver=TenantResolver(db_url="unused"),
        queries=FleetQueries(FleetRepository(db_url="unused")),
        content=_content(tmp_path / "content"),
    )

    async with Client(mcp) as client:
        resources = await client.list_resources()
        text = (await client.read_resource(CLIENT_INSTALL_URI))[0].text

    assert CLIENT_INSTALL_NAME in {r.name for r in resources}
    assert "no CLI artifact directory" in text
    assert "uv tool install --force" not in text


async def test_templates_and_procedures_are_unchanged(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    make_wheel(dist, version=VERSION)

    async with Client(_mcp(tmp_path, dist)) as client:
        tools = await client.list_tools()
        template = await client.call_tool("read_resource", {"name": "fleet-agents-template"})
        procedure = await client.call_tool("read_procedure", {"name": "ask-agent"})
        missing = await client.call_tool("read_resource", {"name": "nope"})

    assert {tool.name for tool in tools} == TOOLS
    assert template.data["content"].startswith("# Roster")
    assert procedure.data["served"] is True
    assert missing.data["served"] is False


async def test_the_app_wires_its_configured_artifact_into_the_mcp_face(
    tmp_path: Path, content_root: Path
) -> None:
    dist = tmp_path / "dist"
    make_wheel(dist, version=VERSION)
    config = Config(
        db_url=DSN,
        logto_endpoint="https://auth.example",
        public_base_url=BASE,
        content_root=content_root,
        cli_dist_dir=dist,
    )
    app = build_app(config, auth=None)

    async with Client(app.state.mcp) as client:
        text = (await client.read_resource(CLIENT_INSTALL_URI))[0].text

    assert WHEEL_URL in text


def test_http_discovery_and_the_resource_describe_the_same_wheel(
    tmp_path: Path, content_root: Path
) -> None:
    dist = tmp_path / "dist"
    make_wheel(dist, version=VERSION)
    config = Config(
        db_url=DSN,
        logto_endpoint="https://auth.example",
        public_base_url=BASE,
        content_root=content_root,
        cli_dist_dir=dist,
    )
    discovery = TestClient(build_app(config, auth=None)).get("/v1/resources/cli")

    text = client_install_text(BASE, dist)

    assert discovery.status_code == 200
    body = discovery.json()
    assert body["download_url"] in text
    assert body["sha256"] in text
    assert body["install_command"] in text
    assert f"`einherjar` {body['version']}" in text
