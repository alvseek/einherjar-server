"""The client's module entry point: ``python -m einherjar_cli``.

A delegated turn is spawned as ``python -m einherjar_cli _turn <payload>``, so the detached half
reaches the CLI without importing anything the server owns.
"""

from __future__ import annotations

from einherjar_cli.cli import main


def run() -> None:
    main()


if __name__ == "__main__":
    run()
