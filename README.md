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

## Status

Scaffold: config guard, health route and the app shape. The content face, the store and the CLI are being built against the [plan](https://github.com/alvseek/agent-memory-fleet/blob/main/plans/2026-10-20-agent-memory-fleet-fleet-delivery.md).

## License

Licensed under the Apache License 2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE).
