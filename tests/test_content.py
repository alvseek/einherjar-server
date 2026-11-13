"""The served content: five procedures, one template, and the equality that keeps them honest."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from fastmcp import Client

from einherjar.api.mcp import CLIENT_INSTALL_URI, build_mcp
from einherjar.business.queries import FleetQueries
from einherjar.business.tenant import TenantResolver
from einherjar.content.loader import ContentLoader
from einherjar.data.repository import FleetRepository

PROCEDURES = {"ask-agent", "delegate-agent", "load-fleet", "setup-fleet", "spawn"}
TEMPLATES = {"fleet-agents-template"}


@pytest.fixture()
def loader(content_root: Path) -> ContentLoader:
    return ContentLoader(content_root)


def test_every_procedure_and_template_is_discovered(loader: ContentLoader) -> None:
    assert set(loader.list_procedures()) == PROCEDURES
    assert set(loader.list_resources()) == TEMPLATES


def test_served_text_equals_the_compiled_output(
    loader: ContentLoader, content_root: Path, tmp_path: Path
) -> None:
    """The claim that makes a served prompt and a compiled file the same instruction."""
    out = tmp_path / "compiled"
    subprocess.run(
        [
            sys.executable,
            str(content_root / "setup-scripts" / "compile-procedures.py"),
            "--out",
            str(out),
            "--quiet",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    for name in loader.list_procedures():
        assert loader.get_procedure(name) == (out / f"{name}.md").read_text(encoding="utf-8")


def test_an_argument_fills_the_slot(loader: ContentLoader) -> None:
    text = loader.get_procedure("setup-fleet", "agent-memory-fleet")
    assert "$ARGUMENTS" not in text
    assert "agent-memory-fleet" in text


def test_spawn_is_served_and_says_it_does_not_awaken(loader: ContentLoader) -> None:
    """The served form is the one an agent reads, so the boundary itself is asserted here."""
    text = loader.get_procedure("spawn")
    assert "no awakening" in text
    assert "einherjar spawn --prompt" in text
    assert "resource://client/install" in text
    assert "git+https://github.com" not in text


async def test_prompts_prompts_and_tools_are_the_same_content(loader: ContentLoader) -> None:
    mcp = build_mcp(
        auth=None,
        resolver=TenantResolver(db_url="unused"),
        queries=FleetQueries(FleetRepository(db_url="unused")),
        content=loader,
    )
    async with Client(mcp) as client:
        prompts = await client.list_prompts()
        resources = await client.list_resources()
        read = await client.call_tool("read_procedure", {"name": "ask-agent"})
        missing = await client.call_tool("read_procedure", {"name": "nope"})
        procedure_listing = await client.call_tool("list_procedures")
        resource_listing = await client.call_tool("list_resources")
        template = await client.call_tool("read_resource", {"name": "fleet-agents-template"})
        missing_resource = await client.call_tool("read_resource", {"name": "nope"})

    assert {p.name for p in prompts} == PROCEDURES
    # The fleet's templates plus the one resource that is not fleet content: the client install.
    assert {str(r.uri) for r in resources} == {
        f"resource://templates/{name}" for name in TEMPLATES
    } | {CLIENT_INSTALL_URI}
    assert read.data["served"] is True
    assert "Ask Agent" in read.data["content"]
    assert missing.data["served"] is False
    assert template.data["content"] == loader.get_resource("fleet-agents-template")
    assert missing_resource.data["served"] is False

    procedure_rows = {row["name"]: row for row in procedure_listing.data}
    for prompt in prompts:
        assert prompt.description == procedure_rows[prompt.name]["description"]
        assert "does not execute" in prompt.description
        argument = next(arg for arg in prompt.arguments if arg.name == "argument")
        assert argument.description
        assert "$ARGUMENTS" in argument.description
        assert not argument.required

    resource_rows = {row["name"]: row for row in resource_listing.data}
    for resource in resources:
        assert resource.description
        assert resource.description == resource_rows[resource.name]["description"]
        assert str(resource.uri) == resource_rows[resource.name]["uri"]


def test_resource_description_uses_the_templates_own_introduction(tmp_path: Path) -> None:
    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / "sample.md").write_text(
        "> **A roster for teammates.** Fill it in.\n\n| Agent | Role |\n", encoding="utf-8"
    )
    loader = ContentLoader(tmp_path)
    description = loader.describe_resource("sample")
    assert description.startswith("A roster for teammates.")
    assert "**" not in description
    assert "read_resource" in description
    assert "does not create" in description
