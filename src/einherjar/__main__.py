"""The process entry point: build the app from config, then serve it."""

from __future__ import annotations

import uvicorn

from einherjar.app import build_app
from einherjar.configuration.config import load_config


def main() -> None:
    config = load_config()
    uvicorn.run(build_app(config), host=config.host, port=config.port)


if __name__ == "__main__":
    main()
