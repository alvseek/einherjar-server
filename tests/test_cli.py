"""The CLI flows, without a registry server or a real agent behind them."""

from __future__ import annotations

import io
import os
import subprocess
from pathlib import Path

import pytest

from einherjar_cli.cli import (
    WRAP_PROMPT,
    Target,
    ask,
    awaken_prompt,
    delegate,
    resolve_target,
    result,
    sessions,
    wrap_up,
    write_answer,
)
from einherjar_cli.registry.client import RegistryError
from einherjar_cli.settings import ClientSettings
from einherjar_cli.state.pending import PendingBuffer
from einherjar_cli.transport.adapters import PlatformError, TurnRequest

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


async def test_a_foreground_turn_writes_its_log_and_prints_an_envelope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One file, and the shape the caller reads: ref, metadata, then the answer."""
    monkeypatch.setenv("EINHERJAR_CONFIG_DIR", str(tmp_path))
    registry, out = FakeRegistry(), io.StringIO()

    code = await ask(
        "Backend Django",
        prompt="Review the API",
        theme="api review",
        settings=SETTINGS,
        registry=registry,
        project="proj",
        runner=runner_ok,
        out=out,
        err=io.StringIO(),
    )

    assert code == 0
    text = out.getvalue()
    ref = text.splitlines()[0]
    assert ref == registry.created[0]["session_ref"]

    log = tmp_path / "turns" / f"{ref}.out"
    assert log.read_text(encoding="utf-8") == "hello from the agent\n"

    metadata, answer = text.split("-" * 60, 1)
    assert "status:     completed" in metadata
    assert "platform:   claude" in metadata
    assert f"output:     {log}" in metadata
    assert "hello from the agent" not in metadata
    assert answer.lstrip("\n") == "hello from the agent\n"


async def test_a_second_turn_appends_to_the_same_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The log is every turn on that ref; the conclusion is only the last one."""
    monkeypatch.setenv("EINHERJAR_CONFIG_DIR", str(tmp_path))
    registry = FakeRegistry()
    ref = "ses_0123456789abcdef0123456789abcdef"

    for _ in range(2):
        await ask(
            ref,
            prompt="again",
            settings=SETTINGS,
            registry=registry,
            project="proj",
            runner=runner_ok,
            out=io.StringIO(),
            err=io.StringIO(),
        )

    log = tmp_path / "turns" / f"{ref}.out"
    assert log.read_text(encoding="utf-8") == "hello from the agent\n" * 2


async def test_a_new_opencode_session_is_created_before_it_is_printed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EINHERJAR_PLATFORM", "opencode")
    created: list[tuple] = []
    monkeypatch.setattr(
        "einherjar_cli.cli.create_session", lambda ref, **kwargs: created.append((ref, kwargs))
    )
    registry = FakeRegistry()

    await ask(
        "Backend Django",
        prompt="Review the API",
        theme="api review",
        settings=SETTINGS,
        registry=registry,
        project="proj",
        runner=runner_ok,
        out=io.StringIO(),
        err=io.StringIO(),
    )

    assert created == [
        (
            registry.created[0]["session_ref"],
            {"directory": os.getcwd(), "title": "api review"},
        )
    ]


async def test_a_resume_and_a_claude_turn_create_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A resume must not create, and Claude's own ``--session-id`` already does."""
    created: list[str] = []
    monkeypatch.setattr("einherjar_cli.cli.create_session", lambda ref, **k: created.append(ref))

    await ask(
        "ses_abc",
        prompt="again",
        settings=SETTINGS,
        registry=FakeRegistry(),
        project="proj",
        runner=runner_ok,
        out=io.StringIO(),
        err=io.StringIO(),
    )
    await ask(
        "Backend Django",
        prompt="Review the API",
        settings=SETTINGS,
        registry=FakeRegistry(),
        project="proj",
        runner=runner_ok,
        out=io.StringIO(),
        err=io.StringIO(),
    )

    assert created == []


async def test_a_refused_create_closes_the_row_without_starting_a_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EINHERJAR_PLATFORM", "opencode")

    def refuse(ref: str, **kwargs) -> None:
        raise PlatformError("the platform would not create session")

    monkeypatch.setattr("einherjar_cli.cli.create_session", refuse)
    registry, err = FakeRegistry(), io.StringIO()
    ran: list[TurnRequest] = []

    code = await ask(
        "Backend Django",
        prompt="Review the API",
        settings=SETTINGS,
        registry=registry,
        project="proj",
        runner=ran.append,
        out=io.StringIO(),
        err=err,
    )

    assert code == 1
    assert ran == []
    assert registry.updated[0][1] == "failed"
    assert "could not start" in err.getvalue()


async def test_result_prefers_the_envelope_and_falls_back_to_the_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EINHERJAR_CONFIG_DIR", str(tmp_path))
    turns = tmp_path / "turns"
    turns.mkdir(parents=True)
    (turns / "ses_bg.result.md").write_text("envelope body\n", encoding="utf-8")
    (turns / "ses_bg.out").write_text("log body\n", encoding="utf-8")
    (turns / "ses_fg.out").write_text("foreground log\n", encoding="utf-8")

    out = io.StringIO()
    assert await result("ses_bg", out=out, err=io.StringIO()) == 0
    assert out.getvalue() == "envelope body\n"

    # A foreground ref has no envelope, and saying "no recorded output" would be false.
    out = io.StringIO()
    assert await result("ses_fg", out=out, err=io.StringIO()) == 0
    assert out.getvalue() == "foreground log\n"

    out = io.StringIO()
    assert await result("ses_bg", full=True, out=out, err=io.StringIO()) == 0
    assert out.getvalue() == "log body\n"

    err = io.StringIO()
    assert await result("ses_missing", out=io.StringIO(), err=err) == 1
    assert "no recorded output for ses_missing" in err.getvalue()


def test_the_answer_reaches_the_log_byte_for_byte(monkeypatch: pytest.MonkeyPatch) -> None:
    """Text mode would make one log CRLF and the other LF, for the same reply."""
    raw = io.BytesIO()

    class Captured:
        buffer = raw

        def write(self, text: str) -> None:
            raise AssertionError("write_answer must use the byte layer when it exists")

        def flush(self) -> None:
            raise AssertionError("write_answer must use the byte layer when it exists")

    monkeypatch.setattr("einherjar_cli.cli.sys.stdout", Captured())

    write_answer("line one\nline two\n")

    assert raw.getvalue() == b"line one\nline two\n"
