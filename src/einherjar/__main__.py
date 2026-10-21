"""The process entry point.

With no arguments it is the server: the container starts it that way. With arguments it is the
CLI, so `python -m einherjar _turn <payload>` (what a delegated turn spawns) reaches the client.
"""

from __future__ import annotations

import sys

import uvicorn

from einherjar.app import build_app
from einherjar.configuration.config import load_config


def main() -> None:
    if len(sys.argv) > 1:
        from einherjar.cli import main as cli_main

        cli_main()
        return
    config = load_config()
    uvicorn.run(build_app(config), host=config.host, port=config.port)


if __name__ == "__main__":
    main()
