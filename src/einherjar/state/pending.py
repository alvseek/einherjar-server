"""The pending buffer: a row that could not reach the registry, kept until it can.

A registry failure never stops a consult. The intended row is appended here and replayed on the
next successful call; the service upserts on the natural key, so replaying one twice is a refresh,
not a duplicate.

The flush is written for a second process appending while it runs: it re-reads after sending and
keeps anything that arrived during the flush, rather than rewriting the file from a stale read and
dropping it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from einherjar.state.paths import pending_file


class PendingCorruptError(RuntimeError):
    """A buffered line is not JSON. Named with its line so it can be inspected, never skipped."""


class PendingBuffer:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path or pending_file()

    @property
    def path(self) -> Path:
        return self._path

    def append(self, record: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, separators=(",", ":")) + "\n")

    def read(self) -> list[dict]:
        if not self._path.is_file():
            return []
        records: list[dict] = []
        for number, line in enumerate(self._path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise PendingCorruptError(
                    f"{self._path}:{number} is not valid JSON ({exc.msg}); "
                    "the buffer was left untouched."
                ) from exc
        return records

    def replace(self, records: list[dict]) -> None:
        """Write the remaining records atomically."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not records:
            self._path.unlink(missing_ok=True)
            return
        body = "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in records)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(body, encoding="utf-8", newline="\n")
        tmp.replace(self._path)

    def flush(self, send: Callable[[dict], None]) -> int:
        """Send every buffered row, keep the failures, and preserve anything appended meanwhile."""
        records = self.read()
        remaining: list[dict] = []
        sent = 0
        for record in records:
            try:
                send(record)
                sent += 1
            except Exception:  # noqa: BLE001 - a failed send is buffered, not fatal
                remaining.append(record)

        # A second process may have appended while we were sending. Re-read and keep the tail;
        # rewriting from the first read would silently drop it.
        current = self.read()
        arrived_meanwhile = current[len(records) :]
        self.replace(remaining + arrived_meanwhile)
        return sent
