"""The CLI's settings: where the service is, who issues its tokens, and which overlay to awaken.

Every value is an environment override with a hosted default, so a laptop talks to the deployment
without configuration and a test or a local run points somewhere else.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_BASE_URL = "https://einherjar.lok.quest"
DEFAULT_ISSUER = "https://auth.lok.quest"
#: The id Logto generated for the registered native app "Einherjar CLI". It is generated rather
#: than chosen, because POST /applications ignores a supplied id, so it is a fact about the
#: registration and changes only with it (authentra's deploy/add-einherjar-client.py).
DEFAULT_CLIENT_ID = "gpghr678hhojnw7w4jj55"
DEFAULT_PROJECT_MCP_URL = "https://hermod.lok.quest/mcp"
#: The registered loopback redirect. The tenant accepts any port on 127.0.0.1, but the URI it is
#: sent still has to match a registration as a string, so this one is fixed and registered rather
#: than discovered, and a busy port is reported instead of silently taking another. Override it
#: only together with a registration that names the same string.
DEFAULT_REDIRECT_URI = "http://127.0.0.1:8123/callback"


@dataclass(frozen=True)
class ClientSettings:
    base_url: str = DEFAULT_BASE_URL
    issuer: str = DEFAULT_ISSUER
    client_id: str = DEFAULT_CLIENT_ID
    project_mcp_url: str = DEFAULT_PROJECT_MCP_URL
    redirect_uri: str = DEFAULT_REDIRECT_URI

    @property
    def resource(self) -> str:
        """The audience both faces verify. Derived from the base URL, never a separate setting."""
        return f"{self.base_url.rstrip('/')}/mcp"

    @classmethod
    def load(cls) -> "ClientSettings":
        return cls(
            base_url=os.getenv("EINHERJAR_BASE_URL", DEFAULT_BASE_URL),
            issuer=os.getenv("EINHERJAR_ISSUER", DEFAULT_ISSUER),
            client_id=os.getenv("EINHERJAR_CLIENT_ID", DEFAULT_CLIENT_ID),
            project_mcp_url=os.getenv("EINHERJAR_PROJECT_MCP_URL", DEFAULT_PROJECT_MCP_URL),
            redirect_uri=os.getenv("EINHERJAR_REDIRECT_URI", DEFAULT_REDIRECT_URI),
        )
