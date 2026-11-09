"""The two transport arms, ported from the bash fleet with their argv asserted one for one.

A turn is a child process in the project directory: prompt in, final text on stdout. Detachment
belongs to the caller, not to the platform, so neither arm knows about background — that is what
keeps a delegated turn identical on both.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Callable, Literal, Mapping

Mode = Literal["new", "resume"]

_UUID = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


@dataclass(frozen=True)
class TurnRequest:
    """One blocking turn, exactly as the platform needs it."""

    platform: str
    mode: Mode
    session_ref: str
    prompt: str
    model: str | None = None


def detect_platform(env: Mapping[str, str] | None = None) -> str:
    """The calling harness: an explicit override, OpenCode's markers, else Claude Code."""
    env = os.environ if env is None else env
    override = env.get("EINHERJAR_PLATFORM", "").strip()
    if override:
        return override
    if env.get("AI_AGENT") == "opencode" or env.get("OPENCODE") == "1":
        return "opencode"
    return "claude"


def platform_for_ref(ref: str) -> str | None:
    """Which platform owns a ref, by its own shape. ``None`` for something that is not a ref."""
    if _UUID.match(ref):
        return "claude"
    if ref.startswith("ses_"):
        return "opencode"
    return None


#: What an OpenCode turn runs on when the caller names no model. A spawned run has no session to
#: inherit a model from, and OpenCode's own default is a free-tier alias that refuses to run outside
#: an OpenCode session, so the fleet's model is written here instead of left to the platform.
#: ``--model`` and ``EINHERJAR_MODEL_<ALIAS>`` still win.
OPENCODE_DEFAULT_MODEL = "opencode-go/deepseek-v4.1-flash"


def resolve_model(
    platform: str, model: str | None, env: Mapping[str, str] | None = None
) -> str | None:
    """A platform's model name, or ``None`` to let the platform default apply.

    Claude takes an alias directly, and its own default is a usable model, so naming none leaves the
    flag off. OpenCode takes ``provider/model``: a bare alias is looked up in
    ``EINHERJAR_MODEL_<ALIAS>`` rather than inventing a provider here, an unmapped alias yields no
    flag instead of a guessed one, and no model at all yields the fleet's own.
    """
    if not model:
        return OPENCODE_DEFAULT_MODEL if platform == "opencode" else None
    if platform == "opencode" and "/" not in model:
        env = os.environ if env is None else env
        variable = "EINHERJAR_MODEL_" + model.upper().replace("-", "_")
        return env.get(variable) or None
    return model


def argv_for(request: TurnRequest) -> list[str]:
    """The exact command line for one turn. This is the whole platform-specific surface."""
    if request.platform == "opencode":
        # --auto is what makes a non-interactive run possible: a permission prompt has nobody to
        # answer it, so anything not explicitly denied is approved.
        argv = ["opencode", "run", "--session", request.session_ref, "--auto"]
        if request.model:
            argv += ["--model", request.model]
        return argv + [request.prompt]

    if request.platform == "claude":
        argv = ["claude", "--print"]
        argv += (
            ["--session-id", request.session_ref]
            if request.mode == "new"
            else ["--resume", request.session_ref]
        )
        if request.model:
            argv += ["--model", request.model]
        return argv + [request.prompt]

    raise ValueError(f"unknown platform {request.platform!r}: expected 'claude' or 'opencode'")


def resolve_command(argv: list[str]) -> list[str]:
    """The argv to execute, with the platform's real entry point in front.

    On Windows a CLI installed by npm is a `.cmd` shim, and CreateProcess cannot start one directly,
    so it has to go through ``cmd.exe /c``. ``which`` finds it; passing the bare name does not.
    """
    found = shutil.which(argv[0])
    if found is None:
        return argv
    lowered = found.lower()
    if os.name == "nt" and lowered.endswith((".cmd", ".bat")):
        return [os.environ.get("COMSPEC", "cmd.exe"), "/c", found, *argv[1:]]
    if os.name == "nt" and lowered.endswith(".ps1"):
        return ["powershell", "-NoProfile", "-File", found, *argv[1:]]
    return [found, *argv[1:]]


def run_turn(
    request: TurnRequest,
    *,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> subprocess.CompletedProcess:
    """Run the turn and return the completed process.

    stdout carries the reply and stderr the platform's progress lines.
    """
    return runner(resolve_command(argv_for(request)), capture_output=True, text=True)
