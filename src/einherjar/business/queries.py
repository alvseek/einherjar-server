"""The read side the MCP tools expose, kept separate so it is testable without a transport."""

from __future__ import annotations

from dataclasses import asdict

from einherjar.data.repository import FleetRepository


class FleetQueries:
    """Reads over the registry, always for one tenant."""

    def __init__(self, repository: FleetRepository) -> None:
        self._repository = repository

    def list_sessions(
        self,
        *,
        user_id: str,
        project: str | None = None,
        status: str | None = None,
    ) -> list[dict]:
        return [
            asdict(session)
            for session in self._repository.list_sessions(
                user_id=user_id, project=project, status=status
            )
        ]

    def get_session(self, *, user_id: str, uuid: str) -> dict | None:
        session = self._repository.get_session(uuid=uuid, user_id=user_id)
        return asdict(session) if session is not None else None
