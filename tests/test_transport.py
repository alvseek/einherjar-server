"""The two arms, asserted one argv at a time — the same cases the bash suite pinned."""

from __future__ import annotations

import subprocess

import pytest

from einherjar.transport.adapters import (
    OPENCODE_DEFAULT_MODEL,
    TurnRequest,
    argv_for,
    detect_platform,
    platform_for_ref,
    resolve_model,
    run_turn,
)

UUID = "3f8a2b1c-9d4e-4f7a-8e2b-5c6d9a1b4e7f"


def test_claude_new_uses_session_id() -> None:
    argv = argv_for(TurnRequest("claude", "new", UUID, "hello", "sonnet"))
    assert argv == ["claude", "--print", "--session-id", UUID, "--model", "sonnet", "hello"]
    assert "--dangerously-skip-permissions" not in argv


def test_claude_resume_uses_resume() -> None:
    argv = argv_for(TurnRequest("claude", "resume", UUID, "hello"))
    assert argv == ["claude", "--print", "--resume", UUID, "hello"]


def test_opencode_new_and_resume_are_the_same_call() -> None:
    new = argv_for(TurnRequest("opencode", "new", "ses_abc", "hello"))
    resume = argv_for(TurnRequest("opencode", "resume", "ses_abc", "hello"))
    assert new == ["opencode", "run", "--session", "ses_abc", "--auto", "hello"]
    assert new == resume


def test_opencode_model_is_passed_when_provider_qualified() -> None:
    argv = argv_for(
        TurnRequest("opencode", "new", "ses_abc", "hello", "commandcode/claude-sonnet-5-5")
    )
    assert argv[argv.index("--model") + 1] == "commandcode/claude-sonnet-5-5"


def test_an_unknown_platform_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown platform"):
        argv_for(TurnRequest("cursor", "new", "x", "hello"))


def test_detection_prefers_the_override_then_opencode_then_claude() -> None:
    assert detect_platform({"EINHERJAR_PLATFORM": "opencode", "AI_AGENT": ""}) == "opencode"
    assert detect_platform({"AI_AGENT": "opencode"}) == "opencode"
    assert detect_platform({"OPENCODE": "1"}) == "opencode"
    assert detect_platform({}) == "claude"


def test_a_ref_names_its_platform() -> None:
    assert platform_for_ref(UUID) == "claude"
    assert platform_for_ref("ses_abc123") == "opencode"
    assert platform_for_ref("Backend Django") is None


def test_model_resolution_is_per_platform() -> None:
    assert resolve_model("claude", "sonnet", {}) == "sonnet"
    assert resolve_model("opencode", "commandcode/x", {}) == "commandcode/x"
    env = {"EINHERJAR_MODEL_SONNET": "commandcode/claude-sonnet-5-5"}
    assert resolve_model("opencode", "sonnet", env) == "commandcode/claude-sonnet-5-5"
    # An unmapped alias asks for the platform default rather than a guessed provider.
    assert resolve_model("opencode", "sonnet", {}) is None


def test_opencode_names_the_fleet_model_when_none_is_given() -> None:
    """A spawned run inherits nothing, so the model has to be stated rather than defaulted.

    OpenCode's own default is a free-tier alias that refuses to run outside an OpenCode session,
    which made every consult fail at the first spawn until the model was passed by hand.
    """
    assert resolve_model("opencode", None, {}) == OPENCODE_DEFAULT_MODEL

    argv = argv_for(
        TurnRequest(
            "opencode", "new", "ses_abc", "hello", model=resolve_model("opencode", None)
        )
    )
    assert argv[argv.index("--model") + 1] == OPENCODE_DEFAULT_MODEL


def test_claude_keeps_its_own_default_model() -> None:
    assert resolve_model("claude", None, {}) is None
    assert "--model" not in argv_for(TurnRequest("claude", "new", UUID, "hello"))


def test_run_turn_captures_the_reply() -> None:
    seen: dict = {}

    def runner(argv, **kwargs):
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        return subprocess.CompletedProcess(argv, 0, stdout="reply\n", stderr="progress\n")

    result = run_turn(TurnRequest("opencode", "new", "ses_abc", "hello"), runner=runner)

    assert result.stdout == "reply\n"
    assert "--session" in seen["argv"]
    assert seen["kwargs"] == {"capture_output": True, "text": True}


def test_a_windows_shim_is_run_through_the_command_interpreter(tmp_path) -> None:
    import os
    import sys

    from einherjar.transport.adapters import resolve_command

    shim = tmp_path / "opencode.cmd"
    shim.write_text("@echo off\n", encoding="utf-8")

    resolved = resolve_command([str(shim), "--version"])

    if sys.platform == "win32":
        assert resolved[0] == os.environ.get("COMSPEC", "cmd.exe")
        assert resolved[1] == "/c"
        assert resolved[2] == str(shim)
    else:
        # Outside Windows a .cmd is just a file, and the resolver leaves it alone.
        assert resolved[0] == str(shim)
