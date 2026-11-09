"""Where this machine's own state lives.

The memory store is served; these files are not memory. A machine id, a token reference and the
pending rows describe *this* machine, so they stay local by nature and their presence does not
reintroduce a markdown backend.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def config_dir() -> Path:
    """The fleet client's config directory, overridable for tests and unusual installs."""
    override = os.getenv("EINHERJAR_CONFIG_DIR", "").strip()
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = os.getenv("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "einherjar"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "einherjar"
    base = os.getenv("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "einherjar"


def machine_id_file() -> Path:
    return config_dir() / "machine-id"


def pending_file() -> Path:
    return config_dir() / "pending.jsonl"


def token_file() -> Path:
    """Only used when the OS keychain is unavailable; the normal store is the keychain."""
    return config_dir() / "credentials.json"
