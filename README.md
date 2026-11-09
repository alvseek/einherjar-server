# einherjar-server

The fleet layer's server: it serves the fleet procedures and owns the session registry. It is a member of the [agent-memory framework](https://github.com/alvseek/agent-memory-system) family, beside Munnin (the memory core) and Hermod (the coding overlay).

Python · FastMCP · FastAPI. Apache-2.0.

## What it serves

- **The fleet layer.** The four procedures from [agent-memory-fleet](https://github.com/alvseek/agent-memory-fleet) are served as MCP prompts and readable through a `read_procedure` tool, and the fleet's templates are served as resources. An agent fetches a procedure mid-task; a person can still invoke it by name.
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

## Using the CLI

Install the client from source and sign in to the registry:

```sh
uv tool install git+https://github.com/alvseek/einherjar-server
einherjar login
```

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

Continue any session with `einherjar ask <session-ref> --prompt "Continue the review"`.
OpenCode references start with `ses_`; Claude Code references are UUIDs. Passing a reference
resumes that conversation without adding an awakening instruction; passing an agent name
always creates a new session. Session history must remain available on the originating
machine and platform.

`einherjar sessions` lists recorded turns. A successful process exit marks a turn
`completed`; it does not prove memory was saved. For an awakened fleet agent, request
memory saving explicitly with `einherjar wrap-up <session-ref>`.

## License

Licensed under the Apache License 2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE).
