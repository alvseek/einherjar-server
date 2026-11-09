"""The installation instructions an agent reads, written from the artifact the server serves.

The same reader backs the public JSON discovery endpoint and this text, so an agent that follows the
resource and a client that parses discovery are looking at one description of one file. No version
or URL is written by hand. When there is no usable artifact the text says so and points at the
endpoint that explains why, because an invented install command is worse than an honest "not yet".
"""

from __future__ import annotations

from pathlib import Path

from einherjar.distribution.reader import CliArtifactUnavailable, read_current

#: The public discovery endpoint, relative to the configured base URL.
METADATA_PATH = "/v1/resources/cli"


def client_install_text(public_base_url: str, directory: Path | str) -> str:
    """How to install or upgrade the CLI, or why that cannot be answered right now."""
    metadata_url = f"{public_base_url.rstrip('/')}{METADATA_PATH}"
    try:
        artifact = read_current(directory)
    except CliArtifactUnavailable as exc:
        return _unavailable(metadata_url, str(exc))
    return _available(artifact.metadata(public_base_url), metadata_url)


def _available(fields: dict[str, object], metadata_url: str) -> str:
    size = int(fields["size"])
    return (
        "# Install the Einherjar CLI\n\n"
        "The CLI is a separate package from this server: it signs this machine in, starts agent\n"
        "sessions and records them. It needs `httpx` and a keychain, not a database driver.\n\n"
        "Prerequisites: Python 3.12 or newer, `uv`, and a local model harness (OpenCode or\n"
        "Claude Code).\n\n"
        f"Current release: `einherjar` {fields['version']} ({size:,} bytes)\n"
        f"Wheel: {fields['download_url']}\n"
        f"SHA-256: {fields['sha256']}\n"
        f"Metadata: {metadata_url}\n\n"
        "Install or upgrade - the same command replaces the old combined package:\n\n"
        f"    {fields['install_command']}\n\n"
        "Upgrades are explicit: nothing checks for a newer version or installs one on its own, so\n"
        "run that command again when you want the current release.\n\n"
        "Replacing the package leaves your sign-in, machine id and pending records where they\n"
        "are; there is no state migration.\n\n"
        "Then:\n\n"
        "    einherjar login                        # sign in once; it refreshes afterwards\n"
        "    einherjar install --harness opencode   # optional: register this server's MCP face\n"
        "    einherjar install --harness claude\n\n"
        "The download and this metadata are public. Only the registry calls need the sign-in.\n"
    )


def _unavailable(metadata_url: str, reason: str) -> str:
    return (
        "# Install the Einherjar CLI\n\n"
        "This server has no CLI wheel to hand out right now.\n\n"
        f"{reason}\n\n"
        "No install command is offered until that is fixed, because a guessed URL would not be\n"
        f"installable. `{metadata_url}` answers this with an HTTP 503 and the same explanation;\n"
        "read it for the current state, and use the `download_url` and `install_command` it\n"
        "reports once a wheel is built.\n\n"
        "Prerequisites, once it is available: Python 3.12 or newer, `uv`, and a local model\n"
        "harness (OpenCode or Claude Code). The download is public; signing in comes afterwards.\n"
    )
