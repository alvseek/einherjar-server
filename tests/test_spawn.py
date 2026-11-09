"""Anonymous spawning shares the fleet execution paths, not its awakening prompt."""

from __future__ import annotations

import io
import subprocess
from pathlib import Path

import pytest

from einherjar.cli import _main, ask, build_parser, delegate, spawn
from einherjar.state.pending import PendingBuffer
from einherjar.transport.adapters import TurnRequest, argv_for, platform_for_ref
from tests.test_cli import SETTINGS, DownRegistry, FakeRegistry


@pytest.mark.parametrize("platform", ["claude", "opencode"])
@pytest.mark.parametrize("background", [False, True])
async def test_spawn_is_new_anonymous_and_preserves_the_prompt(
    platform: str,
    background: bool,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("EINHERJAR_PLATFORM", platform)
    registry, out = FakeRegistry(), io.StringIO()
    requests: list[TurnRequest] = []
    payloads: list[dict] = []
    prompt = "Review this diff.\nKeep this prompt exactly as written."

    def runner(request: TurnRequest) -> subprocess.CompletedProcess:
        requests.append(request)
        return subprocess.CompletedProcess([], 0, stdout="reviewed\n", stderr="")

    def detached(payload: dict, ref: str) -> Path:
        assert payload["session_ref"] == ref
        payloads.append(payload)
        return tmp_path / "output"

    code = await spawn(
        prompt=prompt,
        background=background,
        theme="review",
        model="provider/model",
        settings=SETTINGS,
        registry=registry,
        project="proj",
        runner=runner,
        spawn_detached=detached,
        buffer=PendingBuffer(tmp_path / "pending.jsonl"),
        out=out,
        err=io.StringIO(),
    )

    assert code == 0
    ref = out.getvalue().splitlines()[0]
    assert platform_for_ref(ref) == platform
    assert registry.created[0]["domain"] is None
    assert registry.created[0]["theme"] == "review"
    assert registry.created[0]["session_ref"] == ref
    if background:
        assert not requests
        assert out.getvalue() == ref + "\n"
        assert registry.updated == []
        request = TurnRequest(
            platform=payloads[0]["platform"],
            mode=payloads[0]["mode"],
            session_ref=ref,
            prompt=payloads[0]["prompt"],
            model=payloads[0]["model"],
        )
    else:
        assert not payloads
        assert "reviewed" in out.getvalue()
        assert registry.updated == [("u-1", "completed", None)]
        request = requests[0]
    assert request.mode == "new"
    assert request.prompt == prompt
    assert request.model == "provider/model"
    if platform == "claude":
        assert "--session-id" in argv_for(request)
        assert "--resume" not in argv_for(request)

    # The exact reference returned from spawn is the target for an ordinary follow-up.
    requests.clear()
    await ask(
        ref,
        prompt="Follow up",
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


async def test_spawn_buffers_when_the_registry_is_down(tmp_path: Path) -> None:
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
async def test_named_ask_and_delegate_still_awaken(
    platform: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("EINHERJAR_PLATFORM", platform)
    prompts: list[str] = []

    def runner(request: TurnRequest) -> subprocess.CompletedProcess:
        prompts.append(request.prompt)
        return subprocess.CompletedProcess([], 0, stdout="ok", stderr="")

    def detached(payload: dict, ref: str) -> Path:
        prompts.append(payload["prompt"])
        return tmp_path / "output"

    options = dict(
        prompt="Task",
        settings=SETTINGS,
        registry=FakeRegistry(),
        project="proj",
        out=io.StringIO(),
        err=io.StringIO(),
        buffer=PendingBuffer(tmp_path / "pending"),
    )
    await ask("software-architect", runner=runner, **options)
    await delegate("software-architect", spawn=detached, **options)
    assert len(prompts) == 2
    assert all(text.startswith("Awaken as the software-architect agent") for text in prompts)


@pytest.mark.parametrize("background", [False, True])
async def test_spawn_cli_dispatches_options(
    background: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from einherjar import cli

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
    if background:
        argv.append("--background")
    assert await _main(build_parser().parse_args(argv)) == 0
    assert seen[0]["prompt"] == "Task"
    assert seen[0]["background"] is background
    assert seen[0]["theme"] == "review"
    assert seen[0]["model"] == "provider/model"


def test_spawn_requires_a_prompt_and_accepts_no_target() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["spawn"])
    with pytest.raises(SystemExit):
        build_parser().parse_args(["spawn", "software-architect", "--prompt", "Task"])
