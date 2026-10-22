"""The CLI flows, without a registry server or a real agent behind them."""

from __future__ import annotations

import io
import subprocess
from pathlib import Path

import pytest

from einherjar.cli import (
    WRAP_PROMPT,
    Target,
    ask,
    awaken_prompt,
    delegate,
    resolve_target,
    sessions,
    wrap_up,
)
from einherjar.registry.client import RegistryError
from einherjar.settings import ClientSettings
from einherjar.state.pending import PendingBuffer
from einherjar.transport.adapters import TurnRequest

SETTINGS = ClientSettings(
    base_url="https://einherjar.example",
    issuer="https://auth.example",
    client_id="einherjar-cli",
    project_mcp_url="https://hermod.example/mcp",
)


class FakeRegistry:
    def __init__(self) -> None:
        self.created: list[dict] = []
        self.updated: list[tuple] = []
        self.rows: list[dict] = []

    async def create_session(self, row: dict) -> dict:
        self.created.append(row)
        return {"uuid": "u-1", **row}

    async def update_session(self, uuid: str, *, status: str, error: str | None = None) -> dict:
        self.updated.append((uuid, status, error))
        return {"uuid": uuid, "status": status}

    async def list_sessions(self, *, project=None, status=None) -> list[dict]:
        return self.rows


class DownRegistry(FakeRegistry):
    async def create_session(self, row: dict) -> dict:
        raise RegistryError("registry unreachable")


def runner_ok(request: TurnRequest) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(["turn"], 0, stdout="hello from the agent\n", stderr="")


def runner_fail(request: TurnRequest) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(["turn"], 1, stdout="", stderr="awakening failed\n")


def runner_raises(request: TurnRequest) -> subprocess.CompletedProcess:
    raise OSError("the platform CLI is not on PATH")


def test_a_ref_names_its_platform_and_a_name_is_new() -> None:
    assert resolve_target("3f8a2b1c-9d4e-4f7a-8e2b-5c6d9a1b4e7f") == Target(
        "claude", "resume", "3f8a2b1c-9d4e-4f7a-8e2b-5c6d9a1b4e7f"
    )
    assert resolve_target("ses_abc").platform == "opencode"
    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setenv("EINHERJAR_PLATFORM", "opencode")
        target = resolve_target("Backend Django")
    finally:
        monkeypatch.undo()
    assert target.mode == "new" and target.platform == "opencode" and target.ref.startswith("ses_")


def test_the_awakening_names_the_served_overlay() -> None:
    text = awaken_prompt("backend-django", "Review the API", SETTINGS)
    assert "https://hermod.example/mcp" in text
    assert "backend-django" in text
    assert "Review the API" in text


async def test_ask_records_spawns_and_closes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EINHERJAR_PLATFORM", "opencode")
    registry, out, err = FakeRegistry(), io.StringIO(), io.StringIO()

    code = await ask(
        "Backend Django",
        prompt="Review the API",
        theme="api review",
        settings=SETTINGS,
        registry=registry,
        project="proj",
        runner=runner_ok,
        out=out,
        err=err,
    )

    assert code == 0
    row = registry.created[0]
    assert row["session_ref"].startswith("ses_")
    assert row["domain"] == "backend-django"
    assert row["theme"] == "api review"
    assert registry.updated == [("u-1", "completed", None)]
    assert out.getvalue().startswith(row["session_ref"])
    assert "hello from the agent" in out.getvalue()


async def test_a_registry_outage_does_not_stop_the_consult(tmp_path: Path) -> None:
    registry, out, err = DownRegistry(), io.StringIO(), io.StringIO()
    buffer = PendingBuffer(tmp_path / "pending.jsonl")

    code = await ask(
        "Backend Django",
        prompt="Review the API",
        settings=SETTINGS,
        registry=registry,
        buffer=buffer,
        project="proj",
        runner=runner_ok,
        out=out,
        err=err,
    )

    assert code == 0
    records = buffer.read()
    assert records[0]["action"] == "create"
    assert records[0]["row"]["session_ref"] == out.getvalue().splitlines()[0]
    assert records[1]["action"] == "close"
    assert records[1]["status"] == "completed"
    assert "buffered" in err.getvalue()
    assert registry.updated == []


async def test_a_failed_turn_closes_its_row_as_failed() -> None:
    registry, out, err = FakeRegistry(), io.StringIO(), io.StringIO()

    code = await ask(
        "Backend Django",
        prompt="Review the API",
        settings=SETTINGS,
        registry=registry,
        project="proj",
        runner=runner_fail,
        out=out,
        err=err,
    )

    assert code == 1
    assert registry.updated[0][1] == "failed"
    assert "awakening failed" in err.getvalue()


async def test_a_spawn_that_cannot_start_closes_its_row_failed() -> None:
    registry, out, err = FakeRegistry(), io.StringIO(), io.StringIO()

    code = await ask(
        "Backend Django",
        prompt="Review the API",
        settings=SETTINGS,
        registry=registry,
        project="proj",
        runner=runner_raises,
        out=out,
        err=err,
    )

    assert code == 1
    assert registry.updated[0][1] == "failed"
    assert "could not start" in err.getvalue()


async def test_wrap_up_closes_the_row() -> None:
    registry, out, err = FakeRegistry(), io.StringIO(), io.StringIO()
    seen: list[TurnRequest] = []

    def runner(request: TurnRequest) -> subprocess.CompletedProcess:
        seen.append(request)
        return runner_ok(request)

    code = await wrap_up(
        "ses_abc",
        settings=SETTINGS,
        registry=registry,
        project="proj",
        runner=runner,
        out=out,
        err=err,
    )

    assert code == 0
    assert seen[0].prompt == WRAP_PROMPT
    assert registry.created[0]["session_ref"] == "ses_abc"
    assert registry.updated == [("u-1", "completed", None)]


async def test_delegate_spawns_with_the_chosen_ref() -> None:
    registry, out, err = FakeRegistry(), io.StringIO(), io.StringIO()
    spawned: list[tuple[dict, str]] = []

    def spawn(payload: dict, ref: str) -> Path:
        spawned.append((payload, ref))
        return Path("out")

    code = await delegate(
        "Backend Django",
        prompt="Build the endpoint",
        theme="endpoint",
        settings=SETTINGS,
        registry=registry,
        project="proj",
        spawn=spawn,
        out=out,
        err=err,
    )

    assert code == 0
    payload, ref = spawned[0]
    assert payload["uuid"] == "u-1"
    assert payload["mode"] == "new"
    assert payload["prompt"].startswith("Awaken as the backend-django agent")
    assert out.getvalue().strip() == ref


async def test_sessions_prints_the_rows() -> None:
    registry, out = FakeRegistry(), io.StringIO()
    registry.rows = [
        {
            "session_ref": "ses_1",
            "platform": "opencode",
            "status": "active",
            "theme": "api review",
            "machine": "m",
            "machine_label": "laptop",
        }
    ]

    code = await sessions(registry=registry, out=out)

    assert code == 0
    assert "ses_1" in out.getvalue() and "api review" in out.getvalue()


async def test_sessions_flushes_the_buffer_before_listing(tmp_path: Path) -> None:
    registry, out = FakeRegistry(), io.StringIO()
    buffer = PendingBuffer(tmp_path / "pending.jsonl")
    buffer.append(
        {
            "action": "create",
            "row": {"machine": "m", "session_ref": "ses_late", "platform": "opencode"},
        }
    )

    await sessions(registry=registry, buffer=buffer, out=out)

    assert registry.created[0]["session_ref"] == "ses_late"
    assert buffer.read() == []


async def test_a_buffered_close_replays_as_a_completed_row(tmp_path: Path) -> None:
    registry, out = FakeRegistry(), io.StringIO()
    buffer = PendingBuffer(tmp_path / "pending.jsonl")
    buffer.append(
        {
            "action": "close",
            "row": {"machine": "m", "session_ref": "ses_late", "platform": "opencode"},
            "status": "completed",
        }
    )

    await sessions(registry=registry, buffer=buffer, out=out)

    assert registry.created[0]["session_ref"] == "ses_late"
    assert registry.updated == [("u-1", "completed", None)]
    assert buffer.read() == []
