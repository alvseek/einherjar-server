"""The machine id: stable, generated once, and never a hostname.

A hostname changes and can collide across networks, so it is a label, not a key. The id is minted
here, written atomically, and read back on every run; a file that has been corrupted raises rather
than silently minting a new identity, because the key would change under a row that is still live.
"""

from __future__ import annotations

import re
import socket
import uuid
from pathlib import Path

from einherjar.state.paths import machine_id_file

_UUID = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


class MachineIdentityError(RuntimeError):
    """The stored machine id cannot be trusted, so refusing is safer than minting another."""


def machine_id(path: Path | None = None) -> str:
    """This machine's id, creating it on first use."""
    target = path or machine_id_file()
    if target.is_file():
        stored = target.read_text(encoding="utf-8").strip()
        if not _UUID.match(stored):
            raise MachineIdentityError(
                f"the machine id at {target} is not a UUID (found {stored!r}); "
                "restore it or delete the file deliberately, because a new id "
                "cannot reach rows written under the old one."
            )
        return stored

    target.parent.mkdir(parents=True, exist_ok=True)
    minted = str(uuid.uuid4())
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(minted + "\n", encoding="utf-8", newline="\n")
    tmp.replace(target)
    return minted


def machine_label() -> str:
    """A human-readable label for listings. Never a key."""
    return socket.gethostname()
