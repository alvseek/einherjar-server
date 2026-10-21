"""The liveness route: open, and honest about what it proves."""

from __future__ import annotations

from fastapi.testclient import TestClient

from einherjar import __version__
from einherjar.app import build_app
from einherjar.configuration.config import Config

DSN = "postgresql://einherjar:secret@127.0.0.1:5432/valaskjalf"


def test_health_answers_without_a_credential(content_root) -> None:
    with TestClient(build_app(Config(db_url=DSN, content_root=content_root))) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "einherjar",
        "version": __version__,
    }


def test_build_app_refuses_an_absent_content_checkout(content_root) -> None:
    from einherjar.app import ContentUnavailableError

    try:
        build_app(Config(db_url=DSN, content_root=content_root / "missing"))
    except ContentUnavailableError as exc:
        assert "no content to serve" in str(exc)
    else:
        raise AssertionError("a server with no procedures must refuse to boot")


def test_build_app_refuses_a_bad_store_config() -> None:
    try:
        build_app(Config(db_url=""))
    except ValueError as exc:
        assert "EINHERJAR_DB_URL" in str(exc)
    else:
        raise AssertionError("a missing DSN must fail at boot")
