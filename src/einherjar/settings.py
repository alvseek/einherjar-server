"""The CLI's settings: where the service is, who issues its tokens, and which overlay to awaken.

Every value is an environment override with a hosted default, so a laptop talks to the deployment
without configuration and a test or a local run points somewhere else.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_BASE_URL = "https://einherjar.lok.quest"
DEFAULT_ISSUER = "https://auth.lok.quest"
DEFAULT_CLIENT_ID = "einherjar-cli"
DEFAULT_PROJECT_MCP_URL = "https://hermod.lok.quest/mcp"


@dataclass(frozen=True)
class ClientSettings:
    base_url: str = DEFAULT_BASE_URL
    issuer: str = DEFAULT_ISSUER
    client_id: str = DEFAULT_CLIENT_ID
    project_mcp_url: str = DEFAULT_PROJECT_MCP_URL

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
        )
