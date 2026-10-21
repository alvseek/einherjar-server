"""Installing into a harness: the server entry appears, the old skills disappear."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from einherjar.install.harness import FLEET_MANIFEST, OPENCODE_MANIFEST, install_for
from einherjar.settings import ClientSettings

SETTINGS = ClientSettings(
    base_url="https://einherjar.example",
    issuer="https://auth.example",
    client_id="einherjar-cli",
    project_mcp_url="https://hermod.example/mcp",
)


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, argv, **kwargs) -> subprocess.CompletedProcess:
        self.calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")


def test_opencode_gets_the_server_and_loses_the_old_skills(tmp_path: Path) -> None:
    base = tmp_path / ".config" / "opencode"
    skill = base / "skills" / "agent-fleet-ask-agent"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("old", encoding="utf-8")
    (base / "skills" / OPENCODE_MANIFEST).write_text("agent-fleet-ask-agent\n", encoding="utf-8")
    runner = FakeRunner()

    message = install_for("opencode", home=tmp_path, settings=SETTINGS, runner=runner)

    assert runner.calls == [
        [
            "opencode",
            "mcp",
            "add",
            "einherjar",
            "--global",
            "--url",
            "https://einherjar.example/mcp",
        ]
    ]
    assert not skill.exists()
    assert not (base / "skills" / OPENCODE_MANIFEST).exists()
    assert "removed 1 skill" in message


def test_claude_gets_the_server_and_loses_the_old_commands(tmp_path: Path) -> None:
    commands = tmp_path / ".claude" / "commands"
    commands.mkdir(parents=True)
    (commands / "ask-agent.md").write_text("old", encoding="utf-8")
    (commands / FLEET_MANIFEST).write_text("ask-agent.md\n", encoding="utf-8")
    runner = FakeRunner()

    message = install_for("claude", home=tmp_path, settings=SETTINGS, runner=runner)

    assert runner.calls[0][:4] == ["claude", "mcp", "add", "--transport"]
    assert "https://einherjar.example/mcp" in runner.calls[0]
    assert not (commands / "ask-agent.md").exists()
    assert not (commands / FLEET_MANIFEST).exists()
    assert "removed 1 skill" in message


def test_an_unknown_harness_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown harness"):
        install_for("cursor", home=tmp_path, settings=SETTINGS, runner=FakeRunner())


def test_a_failed_registration_is_not_reported_as_success(tmp_path: Path) -> None:
    commands = tmp_path / ".claude" / "commands"
    commands.mkdir(parents=True)
    (commands / "ask-agent.md").write_text("old", encoding="utf-8")
    (commands / FLEET_MANIFEST).write_text("ask-agent.md\n", encoding="utf-8")

    class FailingRunner:
        def __call__(self, argv, **kwargs) -> subprocess.CompletedProcess:
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="no such command")

    with pytest.raises(RuntimeError, match="no such command"):
        install_for("claude", home=tmp_path, settings=SETTINGS, runner=FailingRunner())

    # Nothing was removed, because the registration is what failed.
    assert (commands / "ask-agent.md").exists()


def test_the_cli_has_a_help_screen() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "einherjar", "--help"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0
    assert "einherjar" in result.stdout.lower()
