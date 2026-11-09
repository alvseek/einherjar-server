# einherjar-server

The fleet layer's server: it serves the fleet procedures, publishes the local client, and owns the session registry. It is a member of the [agent-memory framework](https://github.com/alvseek/agent-memory-system) family, beside Munnin (the memory core) and Hermod (the coding overlay).

Python · FastMCP · FastAPI. Apache-2.0.

This repository ships two Python distributions. **`einherjar-server`** is this service, import package `einherjar`. **`einherjar`** is the local client, import package `einherjar_cli`, kept in `packages/cli/`. They share a repository and a lockfile, and neither depends on the other at runtime: the server builds the client wheel and hands it out, and the client calls the server's private API with a signed-in token.

## What it serves

- **The fleet layer.** The five procedures from [agent-memory-fleet](https://github.com/alvseek/agent-memory-fleet) — `ask-agent`, `delegate-agent`, `spawn`, `load-fleet`, `setup-fleet` — are served as MCP prompts and readable through a `read_procedure` tool, and the fleet's templates are served as resources. An agent fetches a procedure mid-task; a person can still invoke it by name.
- **The client wheel.** `GET /v1/resources/cli` publicly describes the one CLI wheel bundled with this deployment — its real version, filename, size, SHA-256, download URL and exact install command — and `GET /v1/resources/cli/{filename}` serves those bytes. The MCP resource `resource://client/install`, tool name `client-install`, carries the same instructions. Nothing else on that path is reachable, a download needs no token, and a missing wheel is a `503` with the reason rather than a fabricated link.
- **The session registry.** One `fleet_session` row per consult, keyed by `(user_id, machine, ref)`, written by the `einherjar` CLI that runs on the machine where the agent actually spawns.

The service owns its own table and its own migrations. Munnin owns the memory schema; this server never issues DDL against it.

## Running it

Needs `uv`.

```sh
git clone --recurse-submodules https://github.com/alvseek/einherjar-server
cd einherjar-server
uv sync
uv run python -m einherjar        # 127.0.0.1:8220
```

`/health` answers on the same process. With no issuer configured the server is local by construction: it binds loopback, asks nobody to log in, and refuses to start if the bind or the public URL says something else could reach it.

The wheel it hands out is read from `EINHERJAR_CLI_DIST_DIR`, which defaults to `dist/` and must hold exactly one valid `einherjar` wheel. Build it there:

```sh
uv build --package einherjar --out-dir dist
```

An absent or ambiguous directory is not fatal: the two resource routes answer `503` with the reason, and health, the registry and the MCP face keep working.

## Using the CLI

The client is a separate install, published by this server and not by a package index. Read the
instructions from the running server — `resource://client/install` on its MCP face, or
`GET /v1/resources/cli` — and run the command it reports. It looks like:

```sh
uv tool install --force https://<server>/v1/resources/cli/einherjar-<version>-py3-none-any.whl
einherjar login
```

That endpoint is authoritative for the current wheel, which is why the URL and version are not
written here: only the wheel bundled with the running server is served, and the client's version
moves independently of the server's. `uv tool install --force` replaces an older combined install of
the same distribution name. Upgrades are explicit — nothing checks for, or installs, a new version
on its own.

`einherjar login` asks for consent explicitly, so the issuer grants offline access and the client
renews its own token; signing in again is only needed once that stored credential expires or is
revoked.

`ask <agent-name>` starts a new session and instructs it to awaken as that fleet agent.
`delegate <agent-name>` does the same in the background. To start a fresh session without
fleet awakening or an agent name, use `spawn`:

```sh
einherjar spawn --prompt "Review this diff"
einherjar spawn --background --prompt "Review this diff"
```

By default, `spawn` waits and prints the session reference followed by the reply.
With `--background`, it returns the reference immediately; use `einherjar result <session-ref>`
to read the captured output. Both forms support `--model` and `--theme`, and use the same
platform selection and registry tracking as `ask` and `delegate`. Spawned sessions have no
fleet domain. Skipping awakening does not remove the platform's system instructions,
project rules, or configured integrations, and does not prevent an agent from loading
memory if its prompt or other instructions request it.

For a long or multiline task, put the task in a file and name that file in a short single-line
prompt — a multiline `--prompt` passed through a Windows command shim can arrive truncated:

```sh
einherjar spawn --prompt "Read the complete task in C:/Work/project/tasks/review.md and follow it exactly."
```

Continue any session with `einherjar ask <session-ref> --prompt "Continue the review"`.
OpenCode references start with `ses_`; Claude Code references are UUIDs. Passing a reference
resumes that conversation without adding an awakening instruction; passing an agent name
always creates a new session. Session history must remain available on the originating
machine and platform.

`einherjar sessions` lists recorded turns. A successful process exit marks a turn
`completed`; it does not prove memory was saved. For an awakened fleet agent, request
memory saving explicitly with `einherjar wrap-up <session-ref>`.

## Development

`uv sync` installs both distributions into one environment, because the client is a workspace member
and a development dependency of the server. That is convenience, not coupling: the server declares no
runtime dependency on it, and `tests/test_packaging.py` installs each wheel into a clean environment to
prove it. `python -m einherjar` starts the server and nothing else; the client's module is
`python -m einherjar_cli`.

## License

Licensed under the Apache License 2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE).
