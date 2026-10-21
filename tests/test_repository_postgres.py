"""The registry against a real Postgres: the key, the upsert and the tenant boundary."""

from __future__ import annotations

import os
import uuid

import pytest

from einherjar.data.repository import FleetRepository

PG = os.getenv("EINHERJAR_PG_TEST_URL")
pytestmark = pytest.mark.skipif(
    not PG, reason="no EINHERJAR_PG_TEST_URL: the registry needs a real Postgres"
)


@pytest.fixture()
def repo() -> FleetRepository:
    assert PG is not None
    repository = FleetRepository(db_url=PG)
    repository.ensure_schema()
    return repository


def _ref() -> str:
    return f"ses_{uuid.uuid4().hex}"


def test_schema_applies_twice_without_error(repo: FleetRepository) -> None:
    repo.ensure_schema()
    FleetRepository(db_url=PG).ensure_schema()  # a second process sees the marker


def test_the_same_key_is_one_row(repo: FleetRepository) -> None:
    user = f"user-{uuid.uuid4().hex}"
    ref = _ref()

    first = repo.upsert_session(
        user_id=user, machine="machine-a", session_ref=ref, platform="opencode", theme="one"
    )
    second = repo.upsert_session(
        user_id=user, machine="machine-a", session_ref=ref, platform="opencode", theme="two"
    )

    assert first.uuid == second.uuid
    assert second.theme == "two"
    assert len(repo.list_sessions(user_id=user)) == 1


def test_a_replayed_create_does_not_reopen_a_finished_row(repo: FleetRepository) -> None:
    user = f"user-{uuid.uuid4().hex}"
    ref = _ref()
    created = repo.upsert_session(
        user_id=user, machine="machine-a", session_ref=ref, platform="claude"
    )
    repo.update_session(uuid=created.uuid, user_id=user, status="completed")

    replayed = repo.upsert_session(
        user_id=user, machine="machine-a", session_ref=ref, platform="claude"
    )

    assert replayed.status == "completed"


def test_a_foreign_row_is_invisible(repo: FleetRepository) -> None:
    owner = f"user-{uuid.uuid4().hex}"
    stranger = f"user-{uuid.uuid4().hex}"
    row = repo.upsert_session(
        user_id=owner, machine="machine-a", session_ref=_ref(), platform="opencode"
    )

    assert repo.get_session(uuid=row.uuid, user_id=stranger) is None
    assert repo.update_session(uuid=row.uuid, user_id=stranger, status="failed") is None
    assert repo.list_sessions(user_id=stranger) == []


def test_two_users_may_share_a_machine_and_ref(repo: FleetRepository) -> None:
    ref = _ref()
    one = repo.upsert_session(
        user_id=f"user-{uuid.uuid4().hex}", machine="machine-a", session_ref=ref, platform="claude"
    )
    two = repo.upsert_session(
        user_id=f"user-{uuid.uuid4().hex}", machine="machine-a", session_ref=ref, platform="claude"
    )

    assert one.uuid != two.uuid


def test_completion_stamps_ended_at(repo: FleetRepository) -> None:
    user = f"user-{uuid.uuid4().hex}"
    row = repo.upsert_session(
        user_id=user, machine="machine-a", session_ref=_ref(), platform="opencode"
    )
    assert row.ended_at is None

    done = repo.update_session(uuid=row.uuid, user_id=user, status="completed")

    assert done is not None
    assert done.status == "completed"
    assert done.ended_at is not None


def test_an_unknown_status_is_refused(repo: FleetRepository) -> None:
    with pytest.raises(ValueError, match="unknown status"):
        repo.update_session(uuid="whatever", user_id="whoever", status="paused")
