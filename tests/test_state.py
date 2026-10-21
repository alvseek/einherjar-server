"""The client's own state: config directory, machine identity, and the pending buffer."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from einherjar.state.machine import MachineIdentityError, machine_id
from einherjar.state.paths import config_dir, pending_file
from einherjar.state.pending import PendingBuffer, PendingCorruptError


def test_the_config_dir_can_be_overridden(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EINHERJAR_CONFIG_DIR", str(tmp_path / "cfg"))
    assert config_dir() == tmp_path / "cfg"
    assert pending_file().name == "pending.jsonl"


def test_the_machine_id_is_minted_once_and_survives(tmp_path: Path) -> None:
    path = tmp_path / "machine-id"

    first = machine_id(path)
    second = machine_id(path)

    assert first == second
    assert str(uuid.UUID(first)) == first


def test_a_corrupt_machine_id_is_not_silently_replaced(tmp_path: Path) -> None:
    path = tmp_path / "machine-id"
    path.write_text("not-a-uuid\n", encoding="utf-8")

    with pytest.raises(MachineIdentityError, match="cannot reach rows"):
        machine_id(path)


def test_the_buffer_round_trips(tmp_path: Path) -> None:
    buffer = PendingBuffer(tmp_path / "pending.jsonl")
    buffer.append({"machine": "m", "session_ref": "ses_1"})
    buffer.append({"machine": "m", "session_ref": "ses_2"})

    assert [r["session_ref"] for r in buffer.read()] == ["ses_1", "ses_2"]


def test_a_corrupt_line_names_itself(tmp_path: Path) -> None:
    path = tmp_path / "pending.jsonl"
    path.write_text('{"ok": true}\nnot json\n', encoding="utf-8")

    with pytest.raises(PendingCorruptError, match=":2 is not valid JSON"):
        PendingBuffer(path).read()


def test_flush_keeps_failures_and_rows_that_arrive_during_it(tmp_path: Path) -> None:
    buffer = PendingBuffer(tmp_path / "pending.jsonl")
    buffer.append({"n": 1})
    buffer.append({"n": 2})

    def send(record: dict) -> None:
        if record["n"] == 1:
            raise RuntimeError("registry down")
        buffer.append({"n": 3})  # a second process appends mid-flush

    sent = buffer.flush(send)

    assert sent == 1
    assert [r["n"] for r in buffer.read()] == [1, 3]


def test_replace_with_nothing_removes_the_file(tmp_path: Path) -> None:
    path = tmp_path / "pending.jsonl"
    buffer = PendingBuffer(path)
    buffer.append({"n": 1})
    buffer.replace([])
    assert not path.exists()
