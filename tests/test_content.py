"""The served content: four procedures, one template, and the equality that keeps them honest."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from fastmcp import Client

from einherjar.api.mcp import build_mcp
from einherjar.business.queries import FleetQueries
from einherjar.business.tenant import TenantResolver
from einherjar.content.loader import ContentLoader
from einherjar.data.repository import FleetRepository

PROCEDURES = {"ask-agent", "delegate-agent", "load-fleet", "setup-fleet"}
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

    assert {p.name for p in prompts} == PROCEDURES
    assert {str(r.uri) for r in resources} == {f"resource://templates/{name}" for name in TEMPLATES}
    assert read.data["served"] is True
    assert "Ask Agent" in read.data["content"]
    assert missing.data["served"] is False
