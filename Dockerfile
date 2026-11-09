# syntax=docker/dockerfile:1
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.9.28 /uv /uvx /bin/

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# The client wheel is built into this directory and served from it. Absolute, because both the build
# step and the running process read it, and a relative path would depend on the workdir.
ENV EINHERJAR_CLI_DIST_DIR=/app/cli-dist

# Both workspace manifests first, so a source change does not re-resolve dependencies. The member's
# manifest is all the resolution needs; its sources arrive with the rest of the context.
COPY pyproject.toml uv.lock ./
COPY packages/cli/pyproject.toml packages/cli/pyproject.toml
RUN uv sync --frozen --no-dev --no-install-project

COPY . .

# The runtime environment is the server only. The client is a separate distribution: it is handed out
# as a wheel and never installed here. Both halves are asserted, so a dependency change cannot
# quietly reverse that and the build fails instead of shipping a server that depends on the client.
RUN uv sync --frozen --no-dev \
 && /app/.venv/bin/python -c "import einherjar" \
 && /app/.venv/bin/python -c "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('einherjar_cli') is None else 1)"

# Exactly one current client wheel, built from these sources and validated by the same reader that
# serves it — the build fails here rather than deploying a directory the download face would refuse.
# `--wheel` keeps the sdist out, because only the wheel is ever served.
RUN uv build --package einherjar --wheel --out-dir "$EINHERJAR_CLI_DIST_DIR" \
 && /app/.venv/bin/python -c "from einherjar.distribution.reader import read_current; print('serving', read_current('$EINHERJAR_CLI_DIST_DIR').filename)"

EXPOSE 8220

CMD ["uv", "run", "--no-dev", "python", "-m", "einherjar"]
