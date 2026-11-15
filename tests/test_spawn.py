"""Anonymous turns stay blocking; removed detached forms cannot start any work."""

from __future__ import annotations

import io
import subprocess
from pathlib import Path

import pytest

from einherjar_cli.cli import _main, ask, build_parser, main, spawn
from einherjar_cli.state.pending import PendingBuffer
from einherjar_cli.transport.adapters import TurnRequest, argv_for, platform_for_ref
from tests.test_cli import SETTINGS, DownRegistry, FakeRegistry


@pytest.mark.parametrize("platform", ["claude", "opencode"])
async def test_spawn_is_anonymous_blocking_and_resumable(
    platform: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("EINHERJAR_PLATFORM", platform)
    registry, out = FakeRegistry(), io.StringIO()
    requests: list[TurnRequest] = []
    prompt = "Review this diff.\nPreserve this prompt exactly."

    def runner(request: TurnRequest) -> subprocess.CompletedProcess:
        requests.append(request)
        return subprocess.CompletedProcess([], 0, stdout="reviewed\n", stderr="")

    assert (
        await spawn(
            prompt=prompt,
            theme="review",
            model="provider/model",
            settings=SETTINGS,
            registry=registry,
            project="proj",
            runner=runner,
            buffer=PendingBuffer(tmp_path / "pending.jsonl"),
            out=out,
            err=io.StringIO(),
        )
        == 0
    )
    ref = out.getvalue().splitlines()[0]
    assert platform_for_ref(ref) == platform
    assert registry.created[0]["domain"] is None
    assert registry.created[0]["theme"] == "review"
    assert registry.updated == [("u-1", "completed", None)]
    assert "reviewed" in out.getvalue()
    request = requests[0]
    assert request.mode == "new"
    assert request.prompt == prompt
    assert request.model == "provider/model"
    if platform == "claude":
        assert "--session-id" in argv_for(request)
        assert "--resume" not in argv_for(request)

    requests.clear()
    await ask(
        ref,
        prompt="Follow up",
        model="provider/model",
        settings=SETTINGS,
        registry=registry,
        runner=runner,
        project="proj",
        out=io.StringIO(),
        err=io.StringIO(),
        buffer=PendingBuffer(tmp_path / "follow-up.jsonl"),
    )
    assert requests[0].mode == "resume"
    assert requests[0].session_ref == ref
    assert requests[0].prompt == "Follow up"
    assert requests[0].model == "provider/model"


@pytest.mark.parametrize("failure", ["exit", "exception"])
async def test_spawn_preserves_failure_tracking(failure: str, tmp_path: Path) -> None:
    registry, err = FakeRegistry(), io.StringIO()

    def runner(request: TurnRequest) -> subprocess.CompletedProcess:
        if failure == "exception":
            raise OSError("missing executable")
        return subprocess.CompletedProcess([], 1, stdout="", stderr="turn failed")

    assert (
        await spawn(
            prompt="Task",
            registry=registry,
            settings=SETTINGS,
            runner=runner,
            project="proj",
            buffer=PendingBuffer(tmp_path / "pending.jsonl"),
            out=io.StringIO(),
            err=err,
        )
        == 1
    )
    assert registry.updated[0][1] == "failed"
    assert err.getvalue()


async def test_spawn_buffers_when_registry_down(tmp_path: Path) -> None:
    buffer = PendingBuffer(tmp_path / "pending.jsonl")
    assert (
        await spawn(
            prompt="Task",
            registry=DownRegistry(),
            settings=SETTINGS,
            project="proj",
            buffer=buffer,
            out=io.StringIO(),
            err=io.StringIO(),
            runner=lambda request: subprocess.CompletedProcess([], 0, stdout="ok", stderr=""),
        )
        == 0
    )
    records = buffer.read()
    assert [record["action"] for record in records] == ["create", "close"]
    assert records[0]["row"]["domain"] is None
    assert records[1]["status"] == "completed"


@pytest.mark.parametrize("platform", ["claude", "opencode"])
async def test_named_ask_still_awakens(platform: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EINHERJAR_PLATFORM", platform)
    requests: list[TurnRequest] = []

    def runner(request: TurnRequest) -> subprocess.CompletedProcess:
        requests.append(request)
        return subprocess.CompletedProcess([], 0, stdout="ok", stderr="")

    await ask(
        "software-architect",
        prompt="Task",
        settings=SETTINGS,
        registry=FakeRegistry(),
        project="proj",
        runner=runner,
        out=io.StringIO(),
        err=io.StringIO(),
    )
    assert requests[0].prompt.startswith("Awaken as the software-architect agent")


async def test_spawn_cli_dispatches_options(monkeypatch: pytest.MonkeyPatch) -> None:
    from einherjar_cli import cli

    class RegistryContext:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            pass

    seen: list[dict] = []

    async def fake_spawn(**kwargs):
        seen.append(kwargs)
        return 0

    monkeypatch.setattr(cli, "RegistryClient", RegistryContext)
    monkeypatch.setattr(cli, "spawn", fake_spawn)
    argv = ["spawn", "--prompt", "Task", "--theme", "review", "--model", "provider/model"]
    assert await _main(build_parser().parse_args(argv)) == 0
    assert seen[0]["prompt"] == "Task"
    assert seen[0]["theme"] == "review"
    assert seen[0]["model"] == "provider/model"
    assert "background" not in seen[0]


@pytest.mark.parametrize(
    "argv",
    [
        ["delegate", "software-architect", "--prompt", "Task"],
        ["spawn", "--background", "--prompt", "Task"],
        ["_turn", "payload.json"],
        ["spawn"],
        ["spawn", "software-architect", "--prompt", "Task"],
    ],
)
def test_invalid_or_removed_forms_do_not_execute(
    argv: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args, **kwargs):
        raise AssertionError("argument rejection must precede any execution or registry access")

    monkeypatch.setattr("einherjar_cli.cli._main", forbidden)
    monkeypatch.setattr("einherjar_cli.cli.RegistryClient", forbidden)
    monkeypatch.setattr("einherjar_cli.cli.create_session", forbidden)
    monkeypatch.setattr("einherjar_cli.cli.subprocess.Popen", forbidden)
    monkeypatch.setattr("sys.argv", ["einherjar", *argv])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2


@pytest.mark.parametrize("target", [None, "software-architect", "ses_existing"])
async def test_unmapped_model_fails_before_registry_or_platform(
    target: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EINHERJAR_PLATFORM", "opencode")
    monkeypatch.delenv("EINHERJAR_MODEL_UNMAPPED_TEST_MODEL", raising=False)

    def forbidden(*args, **kwargs):
        raise AssertionError("unresolved model must not reach the platform")

    monkeypatch.setattr("einherjar_cli.cli.create_session", forbidden)
    registry, err = FakeRegistry(), io.StringIO()
    options = dict(
        prompt="Task",
        model="unmapped-test-model",
        settings=SETTINGS,
        registry=registry,
        runner=forbidden,
        out=io.StringIO(),
        err=err,
    )
    code = await spawn(**options) if target is None else await ask(target, **options)
    assert code == 1
    assert registry.created == registry.updated == []
    assert "EINHERJAR_MODEL_UNMAPPED_TEST_MODEL" in err.getvalue()
