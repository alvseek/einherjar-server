"""The fleet session registry: one table, and a schema this service owns.

Munnin owns the memory schema. This repository owns exactly two tables, ``fleet_session`` and the
``einherjar_schema`` marker that records which migrations ran, and it never issues DDL against a
memory table. Migrations apply on the repository's first connection, each in one transaction; a
second process or a second call sees them already recorded and does nothing.

The natural key is ``(user_id, machine, session_ref)``. ``user_id`` is an axis, not an
implementation detail: two people can share a machine name and a ref string, and a row that omitted
the tenant would let one person's session erase another's.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row

#: The lifecycle a row can be in. A terminal status is never downgraded by a replayed create.
STATUSES = ("active", "completed", "failed")
_TERMINAL = ("completed", "failed")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS einherjar_schema (
    name       text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
)
"""

_MIGRATIONS: tuple[tuple[str, str], ...] = (
    (
        "001_fleet_session",
        """
        CREATE TABLE IF NOT EXISTS fleet_session (
            uuid          text PRIMARY KEY,
            user_id       text NOT NULL,
            project       text,
            machine       text NOT NULL,
            machine_label text,
            session_ref   text NOT NULL,
            platform      text NOT NULL,
            domain        text,
            theme         text,
            status        text NOT NULL CHECK (status IN ('active', 'completed', 'failed')),
            created_at    timestamptz NOT NULL DEFAULT now(),
            updated_at    timestamptz NOT NULL DEFAULT now(),
            ended_at      timestamptz,
            error         text,
            UNIQUE (user_id, machine, session_ref)
        )
        """,
    ),
)


@dataclass(frozen=True)
class FleetSession:
    """One consult, as the registry keeps it."""

    uuid: str
    user_id: str
    project: str | None
    machine: str
    machine_label: str | None
    session_ref: str
    platform: str
    domain: str | None
    theme: str | None
    status: str
    created_at: datetime
    updated_at: datetime
    ended_at: datetime | None
    error: str | None


def _row_to_session(row: dict[str, Any]) -> FleetSession:
    return FleetSession(
        uuid=row["uuid"],
        user_id=row["user_id"],
        project=row["project"],
        machine=row["machine"],
        machine_label=row["machine_label"],
        session_ref=row["session_ref"],
        platform=row["platform"],
        domain=row["domain"],
        theme=row["theme"],
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        ended_at=row["ended_at"],
        error=row["error"],
    )


class FleetRepository:
    """The caller's rows, and the schema they live in."""

    def __init__(self, *, db_url: str) -> None:
        self._db_url = db_url
        self._ready = False
        self._lock = threading.Lock()

    # --- schema ---------------------------------------------------------------------

    def ensure_schema(self) -> None:
        """Apply every migration once, in order, recording each by name."""
        if self._ready:
            return
        with self._lock:
            if self._ready:
                return
            with psycopg.connect(self._db_url, row_factory=dict_row) as conn, conn.cursor() as cur:
                cur.execute(_SCHEMA)
                for name, sql in _MIGRATIONS:
                    cur.execute("SELECT 1 FROM einherjar_schema WHERE name = %s", (name,))
                    if cur.fetchone() is None:
                        cur.execute(sql)
                        cur.execute("INSERT INTO einherjar_schema (name) VALUES (%s)", (name,))
                conn.commit()
            self._ready = True

    def _connect(self) -> psycopg.Connection:
        self.ensure_schema()
        return psycopg.connect(self._db_url, row_factory=dict_row)

    # --- writes ---------------------------------------------------------------------

    def upsert_session(
        self,
        *,
        user_id: str,
        machine: str,
        session_ref: str,
        platform: str,
        project: str | None = None,
        machine_label: str | None = None,
        domain: str | None = None,
        theme: str | None = None,
    ) -> FleetSession:
        """Create the row, or refresh it in place when the key already exists.

        A replayed create never revives a finished row: the pending buffer exists so a late write
        can land, not so a completed session can reopen.
        """
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO fleet_session
                    (uuid, user_id, project, machine, machine_label, session_ref, platform,
                     domain, theme, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'active')
                ON CONFLICT (user_id, machine, session_ref) DO UPDATE SET
                    project       = COALESCE(EXCLUDED.project, fleet_session.project),
                    machine_label = COALESCE(EXCLUDED.machine_label, fleet_session.machine_label),
                    platform      = EXCLUDED.platform,
                    domain        = COALESCE(EXCLUDED.domain, fleet_session.domain),
                    theme         = COALESCE(EXCLUDED.theme, fleet_session.theme),
                    updated_at    = now(),
                    status        = CASE
                        WHEN fleet_session.status IN ('completed', 'failed')
                            THEN fleet_session.status
                        ELSE 'active'
                    END
                RETURNING *
                """,
                (
                    uuid4().hex,
                    user_id,
                    project,
                    machine,
                    machine_label,
                    session_ref,
                    platform,
                    domain,
                    theme,
                ),
            )
            row = cur.fetchone()
            conn.commit()
        return _row_to_session(row)

    def update_session(
        self,
        *,
        uuid: str,
        user_id: str,
        status: str,
        error: str | None = None,
    ) -> FleetSession | None:
        """Set a row's status, for its own tenant only. ``None`` when the row is not theirs."""
        if status not in STATUSES:
            raise ValueError(f"unknown status {status!r}; expected one of {STATUSES}")
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE fleet_session
                   SET status     = %s,
                       updated_at = now(),
                       ended_at   = CASE WHEN %s IN ('completed', 'failed') THEN now()
                                         ELSE ended_at END,
                       error      = %s
                 WHERE uuid = %s AND user_id = %s
                RETURNING *
                """,
                (status, status, error, uuid, user_id),
            )
            row = cur.fetchone()
            conn.commit()
        return _row_to_session(row) if row is not None else None

    # --- reads ----------------------------------------------------------------------

    def get_session(self, *, uuid: str, user_id: str) -> FleetSession | None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM fleet_session WHERE uuid = %s AND user_id = %s", (uuid, user_id)
            )
            row = cur.fetchone()
        return _row_to_session(row) if row is not None else None

    def list_sessions(
        self,
        *,
        user_id: str,
        project: str | None = None,
        status: str | None = None,
    ) -> list[FleetSession]:
        where = ["user_id = %s"]
        params: list[Any] = [user_id]
        if project is not None:
            where.append("project = %s")
            params.append(project)
        if status is not None:
            where.append("status = %s")
            params.append(status)
        query = (
            "SELECT * FROM fleet_session WHERE " + " AND ".join(where) + " ORDER BY created_at DESC"
        )
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(query, params)
            rows = cur.fetchall()
        return [_row_to_session(row) for row in rows]
