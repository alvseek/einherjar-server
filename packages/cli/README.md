# einherjar

The local client of the Einherjar fleet layer. One executable, `einherjar`, that signs this machine
in, starts an agent session, records the consult in the fleet registry, and closes it.

It is a distribution of its own: `httpx` and a keychain, nothing else. The server that serves the
procedures and owns the registry is `einherjar-server`, installed separately. This package never
imports it and never runs it.

Python 3.12+ and [uv](https://docs.astral.sh/uv/).

## Install

The client is published by the Einherjar server, not by a package index: the running server hands out
the one wheel bundled with it. Read the instructions from that server — the MCP resource
`resource://client/install`, or `GET /v1/resources/cli` on the same server — and run the command it
reports:

```sh
uv tool install --force <the wheel URL that endpoint reports>
einherjar login
```

That endpoint is authoritative: the version, size and SHA-256 it states describe the bytes it will
serve, and its version need not match the server's. `--force` replaces an older combined install of
the same distribution, and upgrades are explicit — nothing checks for, or installs, a new version on
its own.

For development against this checkout, `uv tool install ./packages/cli` installs the same package
from source.

`einherjar login` opens one browser sign-in and keeps the refresh token in the OS keychain (a
`0600` file with a warning on a machine that has no keychain). The authorization request asks for
consent explicitly, because the issuer otherwise withholds `offline_access` and the client would get
no refresh token; with one, it renews the access token by itself, so signing in again is only needed
once that stored credential expires or is revoked.

## Commands

```sh
einherjar login                                  # sign this machine in, once
einherjar ask <agent-or-ref> --prompt "..."      # consult a teammate and wait
einherjar delegate <agent-or-ref> --prompt "..." # hand the task off in the background
einherjar spawn --prompt "..."                   # start a fresh session, no fleet awakening
einherjar spawn --background --prompt "..."      # the same, returning the ref immediately
einherjar result <session-ref>                   # read a delegated turn's output
einherjar sessions [--project P] [--status S]    # list recorded consults
einherjar wrap-up <session-ref>                  # ask an awakened agent to save its memory
einherjar install --harness opencode             # register the served MCP server in a harness
einherjar install --harness claude
```

`ask` and `delegate` take an agent name — which awakens as that agent — or a session reference,
which resumes that conversation without an awakening. `spawn` takes no target and no awakening.
OpenCode references start with `ses_`; Claude Code references are UUIDs. Both forms accept
`--model` and `--theme`.

A session reference can be continued later with `einherjar ask <ref> --prompt "..."`, on the
machine and platform that own that session history.

## Local state

Everything below `EINHERJAR_CONFIG_DIR` (default: the platform's per-user config directory, in an
`einherjar` folder):

- `machine-id` — a minted UUID naming this machine. Stable across runs; never derived from the
  hostname, because the hostname changes and can collide.
- `pending.jsonl` — rows that could not reach the registry, replayed on the next call.
- `credentials.json` — only when the OS keychain is unavailable.
- `turns/<session-ref>.json` and `.out` — a background turn's payload and captured output.

## Configuration

Every value is an `EINHERJAR_*` environment override with a hosted default:

| Variable | Meaning |
|---|---|
| `EINHERJAR_BASE_URL` | The registry server |
| `EINHERJAR_ISSUER` | The OAuth issuer |
| `EINHERJAR_CLIENT_ID` | The registered native client id |
| `EINHERJAR_REDIRECT_URI` | The registered loopback callback |
| `EINHERJAR_PROJECT_MCP_URL` | The coding overlay an awakening fetches its procedure from |
| `EINHERJAR_PLATFORM` | Force `opencode` or `claude` instead of detecting the harness |
| `EINHERJAR_CONFIG_DIR` | Where the local state above lives |
| `EINHERJAR_MODEL_<ALIAS>` | Map a bare model alias to a provider-qualified name |

## License

Licensed under the Apache License 2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE).
