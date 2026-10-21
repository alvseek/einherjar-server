"""The fleet client: one executable that signs in, records a consult, spawns it, and closes it.

The CLI is the only writer of the registry and the only thing that starts an agent. A session row
is written before the spawn so a crash leaves evidence, and a registry failure buffers the row and
lets the consult run: visibility must never block the work.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, TextIO

from einherjar.auth.login import login as run_login
from einherjar.auth.tokens import TokenStore
from einherjar.registry.client import RegistryClient, RegistryError
from einherjar.settings import ClientSettings
from einherjar.state.machine import machine_id, machine_label
from einherjar.state.paths import config_dir
from einherjar.state.pending import PendingBuffer
from einherjar.transport.adapters import (
    Mode,
    TurnRequest,
    detect_platform,
    platform_for_ref,
    resolve_model,
    run_turn,
)

WRAP_PROMPT = (
    "Please run your wrap-up procedure now: save your episodic memory and conclude this session "
    "cleanly."
)

Runner = Callable[[TurnRequest], subprocess.CompletedProcess]


@dataclass(frozen=True)
class Target:
    platform: str
    mode: Mode
    ref: str


def new_session_ref(platform: str) -> str:
    value = str(uuid.uuid4())
    return f"ses_{value.replace('-', '')}" if platform == "opencode" else value


def resolve_target(target: str) -> Target:
    """A ref names its own platform; a name is a new session on the detected one."""
    platform = platform_for_ref(target)
    if platform is not None:
        return Target(platform, "resume", target)
    detected = detect_platform()
    return Target(detected, "new", new_session_ref(detected))


def domain_of(target: str) -> str | None:
    """The agent id a name refers to. ``None`` for a ref, which is already a session."""
    if platform_for_ref(target) is not None:
        return None
    return target.strip().lower().replace(" ", "-")


def project_name(cwd: Path | None = None) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd else None,
    )
    if result.returncode == 0 and result.stdout.strip():
        return Path(result.stdout.strip()).name
    return Path(cwd or os.getcwd()).name


def awaken_prompt(domain: str, prompt: str, settings: ClientSettings) -> str:
    """The one served shape of an awakening. There is no installed form to fall back to."""
    return (
        f"Awaken as the {domain} agent: fetch the awaken-coder procedure from the served coding "
        f"overlay ({settings.project_mcp_url}) and follow it.\n\n"
        f"After awakening, respond to this request:\n\n---\n{prompt}\n---\n"
    )


def _turn_prompt(target: Target, domain: str | None, prompt: str, settings: ClientSettings) -> str:
    """A new session awakens first; a resume receives the request as it stands."""
    if target.mode == "new":
        return awaken_prompt(domain or "specialist", prompt, settings)
    return prompt


def _row(target: Target, *, project: str, theme: str, domain: str | None) -> dict:
    return {
        "machine": machine_id(),
        "machine_label": machine_label(),
        "session_ref": target.ref,
        "platform": target.platform,
        "project": project,
        "domain": domain,
        "theme": theme or None,
    }


async def _record(
    registry: RegistryClient,
    buffer: PendingBuffer,
    row: dict,
    err: TextIO,
) -> dict | None:
    """Write the row, or buffer it and let the consult proceed. Returns the stored row."""
    try:
        return await registry.create_session(row)
    except RegistryError as exc:
        print(f"warning: {exc}; the row is buffered and will be replayed", file=err)
        buffer.append(row)
        return None


async def ask(
    target_text: str,
    *,
    prompt: str,
    theme: str = "",
    model: str | None = None,
    settings: ClientSettings | None = None,
    registry: RegistryClient,
    buffer: PendingBuffer | None = None,
    runner: Runner = run_turn,
    project: str | None = None,
    out: TextIO = sys.stdout,
    err: TextIO = sys.stderr,
) -> int:
    settings = settings or ClientSettings.load()
    buffer = buffer or PendingBuffer()
    target = resolve_target(target_text)
    resolved_model = resolve_model(target.platform, model)

    try:
        await buffer.flush(registry.create_session)
    except Exception as exc:  # noqa: BLE001 - a corrupt buffer must not block the consult
        print(f"warning: pending rows were left untouched: {exc}", file=err)

    row = _row(
        target,
        project=project or project_name(),
        theme=theme,
        domain=domain_of(target_text),
    )
    stored = await _record(registry, buffer, row, err)

    text = _turn_prompt(target, row["domain"], prompt, settings)
    request = TurnRequest(
        platform=target.platform,
        mode=target.mode,
        session_ref=target.ref,
        prompt=text,
        model=resolved_model,
    )
    result = await asyncio.to_thread(runner, request)

    if result.returncode != 0:
        if stored is not None:
            try:
                await registry.update_session(
                    stored["uuid"], status="failed", error=(result.stderr or "")[-2000:]
                )
            except RegistryError as exc:
                print(f"warning: could not mark the row failed: {exc}", file=err)
        print(result.stderr or "the turn failed", file=err)
        return 1

    if stored is not None:
        try:
            await registry.update_session(stored["uuid"], status="completed")
        except RegistryError as exc:
            print(f"warning: could not mark the row completed: {exc}", file=err)

    print(target.ref, file=out)
    print(result.stdout, end="" if result.stdout.endswith("\n") else "\n", file=out)
    return 0


async def wrap_up(
    ref: str,
    *,
    settings: ClientSettings | None = None,
    registry: RegistryClient,
    runner: Runner = run_turn,
    project: str | None = None,
    out: TextIO = sys.stdout,
    err: TextIO = sys.stderr,
) -> int:
    settings = settings or ClientSettings.load()
    target = resolve_target(ref)
    row = _row(target, project=project or project_name(), theme="wrap-up", domain=None)
    stored = await _record(registry, PendingBuffer(), row, err)

    result = await asyncio.to_thread(
        runner,
        TurnRequest(target.platform, "resume", target.ref, WRAP_PROMPT, None),
    )
    if stored is not None:
        try:
            await registry.update_session(
                stored["uuid"],
                status="completed" if result.returncode == 0 else "failed",
            )
        except RegistryError as exc:
            print(f"warning: could not update the row: {exc}", file=err)
    if result.returncode != 0:
        print(result.stderr or "the wrap-up turn failed", file=err)
        return 1
    print(result.stdout, end="" if result.stdout.endswith("\n") else "\n", file=out)
    return 0


async def sessions(
    *,
    registry: RegistryClient,
    project: str | None = None,
    status: str | None = None,
    out: TextIO = sys.stdout,
) -> int:
    rows = await registry.list_sessions(project=project, status=status)
    if not rows:
        print("no recorded consults", file=out)
        return 0
    for row in rows:
        print(
            f"{row['session_ref']}  {row['platform']:<8}  {row['status']:<9}  "
            f"{(row.get('theme') or ''):<24}  {row.get('machine_label') or row['machine']}",
            file=out,
        )
    return 0


def turn_dir() -> Path:
    return config_dir() / "turns"


def _spawn_detached(payload: dict, ref: str) -> Path:
    directory = turn_dir()
    directory.mkdir(parents=True, exist_ok=True)
    payload_path = directory / f"{ref}.json"
    payload_path.write_text(json.dumps(payload), encoding="utf-8", newline="\n")
    output_path = directory / f"{ref}.out"
    with output_path.open("a", encoding="utf-8", newline="\n") as handle:
        subprocess.Popen(
            [sys.executable, "-m", "einherjar", "_turn", str(payload_path)],
            stdout=handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    return output_path


async def delegate(
    target_text: str,
    *,
    prompt: str,
    theme: str = "",
    model: str | None = None,
    settings: ClientSettings | None = None,
    registry: RegistryClient,
    buffer: PendingBuffer | None = None,
    project: str | None = None,
    spawn=_spawn_detached,
    out: TextIO = sys.stdout,
    err: TextIO = sys.stderr,
) -> int:
    settings = settings or ClientSettings.load()
    buffer = buffer or PendingBuffer()
    target = resolve_target(target_text)
    resolved_model = resolve_model(target.platform, model)

    try:
        await buffer.flush(registry.create_session)
    except Exception as exc:  # noqa: BLE001
        print(f"warning: pending rows were left untouched: {exc}", file=err)

    row = _row(
        target,
        project=project or project_name(),
        theme=theme,
        domain=domain_of(target_text),
    )
    stored = await _record(registry, buffer, row, err)
    text = _turn_prompt(target, row["domain"], prompt, settings)

    spawn(
        {
            "uuid": stored["uuid"] if stored else None,
            "platform": target.platform,
            "mode": target.mode,
            "session_ref": target.ref,
            "prompt": text,
            "model": resolved_model,
        },
        target.ref,
    )
    print(target.ref, file=out)
    return 0


async def result(ref: str, *, out: TextIO = sys.stdout, err: TextIO = sys.stderr) -> int:
    path = turn_dir() / f"{ref}.out"
    if not path.is_file():
        print(f"no recorded output for {ref}", file=err)
        return 1
    print(path.read_text(encoding="utf-8"), end="", file=out)
    return 0


async def execute_turn(payload_path: Path) -> int:
    """The detached half of ``delegate``: run the turn, then close its row."""
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    settings = ClientSettings.load()
    store = TokenStore()
    request = TurnRequest(
        platform=payload["platform"],
        mode=payload["mode"],
        session_ref=payload["session_ref"],
        prompt=payload["prompt"],
        model=payload.get("model"),
    )
    result_process = await asyncio.to_thread(run_turn, request)
    uuid_ = payload.get("uuid")
    if uuid_:
        try:
            async with RegistryClient(
                base_url=settings.base_url,
                store=store,
                issuer=settings.issuer,
                client_id=settings.client_id,
                resource=settings.resource,
            ) as registry:
                error = None
                if result_process.returncode != 0:
                    error = (result_process.stderr or "")[-2000:]
                await registry.update_session(
                    uuid_,
                    status="completed" if result_process.returncode == 0 else "failed",
                    error=error,
                )
        except RegistryError:
            pass
    sys.stdout.write(result_process.stdout or "")
    sys.stdout.flush()
    return 0 if result_process.returncode == 0 else 1


# --- the command line -----------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="einherjar", description="The fleet client.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("login", help="sign in once for this machine")

    ask_p = sub.add_parser("ask", help="consult a teammate and wait")
    ask_p.add_argument("target", help="an agent name, a UUID, or a ses_ ref")
    ask_p.add_argument("--prompt", required=True)
    ask_p.add_argument("--theme", default="")
    ask_p.add_argument("--model", default=None)

    delegate_p = sub.add_parser("delegate", help="hand a task off in the background")
    delegate_p.add_argument("target")
    delegate_p.add_argument("--prompt", required=True)
    delegate_p.add_argument("--theme", default="")
    delegate_p.add_argument("--model", default=None)

    result_p = sub.add_parser("result", help="read a delegated turn's output")
    result_p.add_argument("ref")

    wrap_p = sub.add_parser("wrap-up", help="close a teammate's session")
    wrap_p.add_argument("ref")

    sessions_p = sub.add_parser("sessions", help="list recorded consults")
    sessions_p.add_argument("--project", default=None)
    sessions_p.add_argument("--status", default=None)

    install_p = sub.add_parser("install", help="wire the MCP server into a harness")
    install_p.add_argument("--harness", choices=["claude", "opencode"], required=True)

    turn_p = sub.add_parser("_turn")
    turn_p.add_argument("payload")
    return parser


async def _main(args: argparse.Namespace) -> int:
    settings = ClientSettings.load()
    store = TokenStore()

    if args.command == "login":
        tokens = await run_login(
            issuer=settings.issuer,
            client_id=settings.client_id,
            resource=settings.resource,
            store=store,
        )
        print(f"signed in; token valid until {int(tokens.expires_at)}")
        return 0

    if args.command == "install":
        from einherjar.install.harness import install_for

        print(install_for(args.harness))
        return 0

    if args.command == "_turn":
        return await execute_turn(Path(args.payload))

    if args.command == "result":
        return await result(args.ref)

    async with RegistryClient(
        base_url=settings.base_url,
        store=store,
        issuer=settings.issuer,
        client_id=settings.client_id,
        resource=settings.resource,
    ) as registry:
        if args.command == "ask":
            return await ask(
                args.target,
                prompt=args.prompt,
                theme=args.theme,
                model=args.model,
                settings=settings,
                registry=registry,
            )
        if args.command == "delegate":
            return await delegate(
                args.target,
                prompt=args.prompt,
                theme=args.theme,
                model=args.model,
                settings=settings,
                registry=registry,
            )
        if args.command == "wrap-up":
            return await wrap_up(args.ref, settings=settings, registry=registry)
        if args.command == "sessions":
            return await sessions(
                registry=registry, project=args.project, status=args.status
            )
    return 1


def main() -> None:
    args = build_parser().parse_args()
    raise SystemExit(asyncio.run(_main(args)))


if __name__ == "__main__":
    main()
