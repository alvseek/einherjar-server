"""The client's module entry point: ``python -m einherjar_cli``.

Runs the blocking client without importing anything the server owns.
"""

from __future__ import annotations

from einherjar_cli.cli import main


def run() -> None:
    main()


if __name__ == "__main__":
    run()
