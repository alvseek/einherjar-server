"""Wire the fleet's MCP server into a harness, and remove the markdown it used to install.

Two things happen, in this order, because the second is the only one that can fail safely: the
harness is told about the server through its own CLI (so its config format stays its own business),
and then the skills the old fleet installer left are removed by its manifest.

The removal is deliberate. The fleet layer is served now; a leftover skill would be a second copy
of a procedure that can silently disagree with the one the server serves.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable

from einherjar.settings import ClientSettings
from einherjar.transport.adapters import resolve_command

FLEET_MANIFEST = ".agent-memory-fleet-manifest"
OPENCODE_MANIFEST = ".agent-memory-fleet-opencode-manifest"


def _opencode_base(home: Path) -> Path:
    xdg = os.getenv("XDG_CONFIG_HOME")
    return Path(xdg) / "opencode" if xdg else home / ".config" / "opencode"


def _remove_listed(manifest: Path, base: Path, *, directories: bool) -> int:
    """Remove exactly what the old fleet manifest claims, then the manifest itself."""
    if not manifest.is_file():
        return 0
    removed = 0
    for line in manifest.read_text(encoding="utf-8").splitlines():
        name = line.strip()
        if not name:
            continue
        target = base / name
        if directories and target.is_dir():
            shutil.rmtree(target)
            removed += 1
        elif not directories and target.is_file():
            target.unlink()
            removed += 1
    manifest.unlink()
    return removed


def _run(
    runner: Callable[..., subprocess.CompletedProcess],
    argv: list[str],
    resolver: Callable[[list[str]], list[str]] = resolve_command,
) -> None:
    """Run one harness command and refuse to call a failure a registration."""
    result = runner(resolver(argv), check=False, capture_output=True, text=True)
    if getattr(result, "returncode", 0) != 0:
        detail = (getattr(result, "stderr", "") or "").strip()
        raise RuntimeError(f"{' '.join(argv)} failed ({result.returncode}): {detail}")


def install_for(
    harness: str,
    *,
    home: Path | None = None,
    settings: ClientSettings | None = None,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    resolver: Callable[[list[str]], list[str]] = resolve_command,
) -> str:
    """Register the server for one harness and clear the old markdown install."""
    settings = settings or ClientSettings.load()
    home = home or Path.home()
    url = settings.resource

    if harness == "opencode":
        base = _opencode_base(home)
        _run(
            runner,
            ["opencode", "mcp", "add", "einherjar", "--global", "--url", url],
            resolver,
        )
        removed = _remove_listed(
            base / "skills" / OPENCODE_MANIFEST, base / "skills", directories=True
        )
        return (
            f"registered einherjar for OpenCode at {url}\n"
            f"removed {removed} skill(s) left by the old installer"
        )

    if harness == "claude":
        base = home / ".claude"
        _run(
            runner,
            ["claude", "mcp", "add", "--transport", "http", "einherjar", url, "--scope", "user"],
            resolver,
        )
        removed = _remove_listed(
            base / "commands" / FLEET_MANIFEST, base / "commands", directories=False
        )
        return (
            f"registered einherjar for Claude Code at {url}\n"
            f"removed {removed} skill(s) left by the old installer"
        )

    raise ValueError(f"unknown harness {harness!r}: expected 'claude' or 'opencode'")
