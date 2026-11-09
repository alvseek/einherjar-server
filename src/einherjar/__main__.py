"""The process entry point: always the server.

The client is its own distribution (``einherjar_cli``), so this module has nothing to dispatch to:
``python -m einherjar`` runs the service and nothing else. An agent that spawns a turn starts the
CLI's module instead, which never imports anything from here.
"""

from __future__ import annotations

import uvicorn

from einherjar.app import build_app
from einherjar.configuration.config import load_config


def main() -> None:
    config = load_config()
    uvicorn.run(build_app(config), host=config.host, port=config.port)


if __name__ == "__main__":
    main()
