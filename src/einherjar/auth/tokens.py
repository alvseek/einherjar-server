"""Where the CLI keeps its refresh token.

The keychain is the normal store: it is the OS's own secret store, it survives a profile backup
better than a file, and it is not in the repository. A headless box has no keychain, so the fallback
is a ``0600`` file with a warning, because refusing to work there would make the fleet unusable on
exactly the machines that run agents unattended.

The token is the user's, not the agent's: it is obtained by a sign-in the person completes, and it
is never derived from the MCP client's session.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from einherjar.state.paths import token_file

_log = logging.getLogger("einherjar.auth.tokens")

SERVICE = "einherjar"
ACCOUNT = "tokens"


@dataclass(frozen=True)
class Tokens:
    access_token: str
    expires_at: float
    refresh_token: str | None = None
    token_type: str = "Bearer"

    def expired(self, *, skew: float = 60) -> bool:
        """Whether the access token is inside the refresh window."""
        return time.time() >= self.expires_at - skew

    def as_dict(self) -> dict:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
            "token_type": self.token_type,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Tokens":
        return cls(
            access_token=str(data["access_token"]),
            refresh_token=data.get("refresh_token"),
            expires_at=float(data.get("expires_at", 0)),
            token_type=str(data.get("token_type", "Bearer")),
        )


class TokenStore:
    def __init__(self, path: Path | None = None, *, use_keyring: bool = True) -> None:
        self._path = path or token_file()
        self._use_keyring = use_keyring

    def load(self) -> Tokens | None:
        raw = self._read_keyring() if self._use_keyring else None
        if raw is None and self._path.is_file():
            raw = self._path.read_text(encoding="utf-8")
        if not raw:
            return None
        try:
            return Tokens.from_dict(json.loads(raw))
        except (ValueError, KeyError):
            _log.warning("stored credentials at %s are unreadable; sign in again", self._path)
            return None

    def save(self, tokens: Tokens) -> str:
        """Store the tokens; returns ``"keyring"`` or ``"file"`` for what actually held them."""
        body = json.dumps(tokens.as_dict())
        if self._use_keyring and self._write_keyring(body):
            return "keyring"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(body + "\n", encoding="utf-8", newline="\n")
        try:
            self._path.chmod(0o600)
        except OSError:  # Windows reports no POSIX mode; the user profile is the boundary there.
            pass
        _log.warning(
            "no OS keychain available: the fleet token is in %s with restricted permissions",
            self._path,
        )
        return "file"

    def clear(self) -> None:
        if self._use_keyring:
            try:
                import keyring

                keyring.delete_password(SERVICE, ACCOUNT)
            except Exception:  # noqa: BLE001 - absence is the desired end state
                pass
        self._path.unlink(missing_ok=True)

    # --- the two backends -----------------------------------------------------------

    def _read_keyring(self) -> str | None:
        try:
            import keyring

            return keyring.get_password(SERVICE, ACCOUNT)
        except Exception:  # noqa: BLE001 - no backend is a normal headless state
            return None

    def _write_keyring(self, body: str) -> bool:
        try:
            import keyring

            keyring.set_password(SERVICE, ACCOUNT, body)
            return True
        except Exception:  # noqa: BLE001 - fall through to the file
            return False
