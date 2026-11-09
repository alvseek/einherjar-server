"""Einherjar's local client: the `einherjar` executable and the state it keeps on this machine.

A separate distribution from the server on purpose. Nothing here imports the server's modules,
because a machine that only spawns agents needs `httpx` and a keychain, not a database driver.
"""

__version__ = "0.1.6"
