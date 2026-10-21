"""The two transport arms, ported from the bash fleet with their argv asserted one for one.

A turn is a child process in the project directory: prompt in, final text on stdout. Detachment
belongs to the caller, not to the platform, so neither arm knows about background — that is what
keeps a delegated turn identical on both.
"""

from __future__ import annotations

import os
import re
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


def resolve_model(
    platform: str, model: str | None, env: Mapping[str, str] | None = None
) -> str | None:
    """A platform's model name, or ``None`` to let the platform default apply.

    Claude takes an alias directly. OpenCode takes ``provider/model``, so a bare alias is looked up
    in ``EINHERJAR_MODEL_<ALIAS>`` rather than inventing a provider here; an unmapped alias yields
    no flag instead of a guessed one.
    """
    if not model:
        return None
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


def run_turn(
    request: TurnRequest,
    *,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> subprocess.CompletedProcess:
    """Run the turn and return the completed process.

    stdout carries the reply and stderr the platform's progress lines.
    """
    return runner(argv_for(request), capture_output=True, text=True)
