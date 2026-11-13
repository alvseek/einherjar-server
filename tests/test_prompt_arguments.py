"""Discover argument support from procedure content, never advertise an inert slot."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastmcp import Client

from einherjar.api.mcp import build_mcp
from einherjar.content.loader import ContentLoader

CONTENT = Path(__file__).resolve().parents[1] / "agent-memory-fleet"
LOADER = ContentLoader(CONTENT)
MARKER = "argument-probe: keep spaces and --model provider/model"


def _server(loader: ContentLoader):
    # Only discovery/content tools are called: registry and tenant resolution must not be used.
    return build_mcp(content=loader, resolver=None, queries=None)


@pytest.fixture(scope="module")
def prompt_server():
    return _server(LOADER)


@pytest.mark.parametrize("name", LOADER.list_procedures())
async def test_every_served_prompt_preserves_its_arguments(prompt_server, name: str) -> None:
    authored = LOADER.get_procedure(name)
    async with Client(prompt_server) as client:
        prompts = {prompt.name: prompt for prompt in await client.list_prompts()}
        arguments = prompts[name].arguments or []
        plain = await client.get_prompt(name)
        assert plain.messages[0].content.text == authored
        if "$ARGUMENTS" not in authored:
            assert not arguments, f"{name} advertises an argument its body cannot use"
            return
        assert len(arguments) == 1, f"{name} has a placeholder but no slot"
        assert arguments[0].name == "argument"
        assert not arguments[0].required
        filled = await client.get_prompt(name, {"argument": MARKER})
        expected = authored.replace("$ARGUMENTS", MARKER)
        assert filled.messages[0].content.text == expected
        assert MARKER in expected
        through_tool = await client.call_tool("read_procedure", {"name": name, "argument": MARKER})
        assert through_tool.data["content"] == expected


@pytest.mark.parametrize("has_input", [True, False])
async def test_new_procedure_only_declares_input_when_its_source_has_a_slot(
    tmp_path: Path, has_input: bool
) -> None:
    (tmp_path / "procedures").mkdir()
    (tmp_path / "templates").mkdir()
    authored = "# New Procedure\n\nRead and follow this procedure.\n"
    if has_input:
        authored += "\n## Arguments\n\n$ARGUMENTS\n"
    (tmp_path / "procedures" / "new-procedure.md").write_text(
        authored, encoding="utf-8", newline="\n"
    )
    async with Client(_server(ContentLoader(tmp_path))) as client:
        prompt = (await client.list_prompts())[0]
        arguments = prompt.arguments or []
        assert len(arguments) == (1 if has_input else 0)
        if has_input:
            filled = await client.get_prompt(prompt.name, {"argument": MARKER})
            assert filled.messages[0].content.text == authored.replace("$ARGUMENTS", MARKER)
        else:
            plain = await client.get_prompt(prompt.name)
            assert plain.messages[0].content.text == authored
