"""The config guard: what the server refuses before it can look healthy."""

from __future__ import annotations

import pytest

from einherjar.configuration.config import Config, load_config, validate

DSN = "postgresql://einherjar:secret@127.0.0.1:5432/valaskjalf"


def test_a_missing_dsn_is_refused() -> None:
    with pytest.raises(ValueError, match="EINHERJAR_DB_URL is required"):
        validate(Config(db_url=""))


def test_local_mode_accepts_a_loopback_bind_and_url() -> None:
    validate(Config(db_url=DSN))


def test_local_mode_refuses_an_unauthenticated_public_bind() -> None:
    with pytest.raises(ValueError, match="unauthenticated bind"):
        validate(Config(db_url=DSN, host="0.0.0.0"))


def test_local_mode_refuses_a_public_url() -> None:
    with pytest.raises(ValueError, match="public URL"):
        validate(Config(db_url=DSN, public_base_url="https://einherjar.lok.quest"))


def test_local_bind_all_is_an_acknowledgement_not_a_detection() -> None:
    validate(
        Config(
            db_url=DSN,
            host="0.0.0.0",
            public_base_url="https://einherjar.lok.quest",
            local_bind_all=True,
        )
    )


def test_hosted_mode_may_bind_everywhere() -> None:
    validate(
        Config(
            db_url=DSN,
            host="0.0.0.0",
            logto_endpoint="https://auth.lok.quest",
            public_base_url="https://einherjar.lok.quest",
        )
    )


def test_env_overrides_are_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EINHERJAR_DB_URL", DSN)
    monkeypatch.setenv("EINHERJAR_PORT", "9000")
    monkeypatch.setenv("EINHERJAR_PUBLIC_BASE_URL", "https://einherjar.lok.quest")
    monkeypatch.setenv("EINHERJAR_LOGTO_ENDPOINT", "https://auth.lok.quest")

    config = load_config()

    assert config.db_url == DSN
    assert config.port == 9000
    assert config.public_base_url == "https://einherjar.lok.quest"
    assert config.logto_endpoint == "https://auth.lok.quest"
