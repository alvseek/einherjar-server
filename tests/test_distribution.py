"""The download face: one validated wheel, its real bytes, and nothing else reachable.

The fixtures are **wheel-shaped metadata fixtures**: a zip carrying the `.dist-info/METADATA` this
reader validates plus the payload bytes it measures. They are not proven installable wheels — no
real build, resolution or `uv pip install` runs against them — and that is deliberate, because the
unit under test here is "does the server describe and serve exactly the file it was given". Real
wheel build and clean-environment install checks live in `tests/test_packaging.py`, opt-in behind
`EINHERJAR_WHEEL_TEST=1`.

The HTTP tests build the app with a throwaway DSN and never reach a database: the resource routes
must work with the store down, which is the state a fresh machine is in when it installs the client.
"""

from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path
from urllib.parse import urlparse

import pytest
from fastapi.testclient import TestClient
from fastmcp.server.auth.providers.jwt import JWTVerifier

from einherjar.app import build_app
from einherjar.configuration.config import Config
from einherjar.distribution.reader import (
    CLI_PACKAGE,
    CliArtifactUnavailable,
    download_url,
    read_current,
)

DSN = "postgresql://einherjar:secret@127.0.0.1:5432/valaskjalf"
BASE = "http://127.0.0.1:8220"
ISSUER = "https://auth.example/oidc"
RESOURCE = "https://einherjar.example/mcp"
WHEEL = "einherjar-9.8.7-py3-none-any.whl"


def make_wheel(
    directory: Path,
    *,
    name: str = CLI_PACKAGE,
    version: str = "9.8.7",
    filename: str | None = None,
    body: bytes = b"# the client\n",
    declared_name: str | None = None,
    declared_version: str | None = None,
) -> Path:
    """A wheel-shaped fixture: a zip whose METADATA declares this name and version.

    Enough for the reader — name, version and measurable bytes — and deliberately not asserted
    installable. Real wheel installation is covered by the opt-in packaging test.
    """
    directory.mkdir(parents=True, exist_ok=True)
    wheel = directory / (filename or f"{name.replace('-', '_')}-{version}-py3-none-any.whl")
    dist_info = f"{name.replace('-', '_')}-{version}.dist-info"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("einherjar_cli/__init__.py", body)
        archive.writestr(
            f"{dist_info}/METADATA",
            "Metadata-Version: 2.4\n"
            f"Name: {declared_name or name}\n"
            f"Version: {declared_version or version}\n",
        )
        archive.writestr(f"{dist_info}/WHEEL", "Wheel-Version: 1.0\n")
        archive.writestr(f"{dist_info}/RECORD", "")
    return wheel


@pytest.fixture()
def dist_dir(tmp_path: Path) -> Path:
    return tmp_path / "dist"


@pytest.fixture()
def public_client(dist_dir: Path, content_root: Path) -> TestClient:
    make_wheel(dist_dir)
    config = Config(db_url=DSN, content_root=content_root, cli_dist_dir=dist_dir)
    return TestClient(build_app(config))


def _client(config: Config) -> TestClient:
    return TestClient(build_app(config))


# --- the reader -------------------------------------------------------------------


def test_every_field_comes_from_the_wheel_itself(dist_dir: Path) -> None:
    wheel = make_wheel(dist_dir, version="9.8.7")
    payload = wheel.read_bytes()

    artifact = read_current(dist_dir)

    assert artifact.path == wheel
    assert artifact.filename == WHEEL
    assert artifact.version == "9.8.7"
    assert artifact.size == len(payload)
    assert artifact.sha256 == hashlib.sha256(payload).hexdigest()
    assert artifact.metadata(BASE) == {
        "package": "einherjar",
        "version": "9.8.7",
        "filename": WHEEL,
        "size": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "download_url": f"{BASE}/v1/resources/cli/{WHEEL}",
        "install_command": f"uv tool install --force {BASE}/v1/resources/cli/{WHEEL}",
    }


def test_the_download_url_ignores_a_trailing_slash_on_the_base() -> None:
    assert download_url("https://einherjar.example/", WHEEL) == (
        f"https://einherjar.example/v1/resources/cli/{WHEEL}"
    )


def test_a_missing_directory_is_refused(tmp_path: Path) -> None:
    with pytest.raises(CliArtifactUnavailable, match="no CLI artifact directory"):
        read_current(tmp_path / "absent")


def test_an_empty_directory_is_refused(tmp_path: Path) -> None:
    with pytest.raises(CliArtifactUnavailable, match="no CLI wheel in"):
        read_current(tmp_path)


def test_two_wheels_are_ambiguous(tmp_path: Path) -> None:
    make_wheel(tmp_path, version="9.8.7")
    make_wheel(tmp_path, version="9.8.8")

    with pytest.raises(CliArtifactUnavailable, match="2 wheels in"):
        read_current(tmp_path)


def test_a_server_wheel_is_not_advertised(tmp_path: Path) -> None:
    make_wheel(tmp_path, name="einherjar-server", version="0.1.6")

    with pytest.raises(CliArtifactUnavailable, match="not the 'einherjar' client"):
        read_current(tmp_path)


def test_a_wheel_without_metadata_is_refused(tmp_path: Path) -> None:
    with zipfile.ZipFile(tmp_path / WHEEL, "w") as archive:
        archive.writestr("einherjar_cli/__init__.py", "")

    with pytest.raises(CliArtifactUnavailable, match="no top-level .dist-info/METADATA"):
        read_current(tmp_path)


def test_metadata_that_disagrees_with_the_filename_is_refused(tmp_path: Path) -> None:
    make_wheel(tmp_path, version="9.8.7", declared_version="0.1.6")

    with pytest.raises(CliArtifactUnavailable, match="declares version"):
        read_current(tmp_path)


def test_a_filename_that_is_not_a_wheel_name_is_refused(tmp_path: Path) -> None:
    (tmp_path / "einherjar.whl").write_bytes(b"")

    with pytest.raises(CliArtifactUnavailable, match="not a wheel filename"):
        read_current(tmp_path)


def test_a_file_that_is_not_a_zip_is_refused(tmp_path: Path) -> None:
    (tmp_path / WHEEL).write_bytes(b"not a zip at all")

    with pytest.raises(CliArtifactUnavailable, match="not a readable wheel"):
        read_current(tmp_path)


def test_a_directory_that_cannot_be_listed_is_unavailable(
    dist_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unreadable directory is the reader's failure, not an OSError escaping to a 500."""
    dist_dir.mkdir(parents=True)
    original = Path.iterdir

    def refuse(self: Path):
        if self == dist_dir:
            raise PermissionError(13, "Permission denied", str(self))
        return original(self)

    monkeypatch.setattr(Path, "iterdir", refuse)

    with pytest.raises(CliArtifactUnavailable, match="cannot list"):
        read_current(dist_dir)


def test_a_wheel_that_cannot_be_measured_is_unavailable(
    dist_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wheel = make_wheel(dist_dir)
    original = Path.stat

    def refuse(self: Path, *args: object, **kwargs: object):
        if self == wheel:
            raise OSError("the device is not ready")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", refuse)

    with pytest.raises(CliArtifactUnavailable, match="could not be read"):
        read_current(dist_dir)


def test_a_wheel_that_cannot_be_hashed_is_unavailable(
    dist_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wheel = make_wheel(dist_dir)
    original = Path.open

    def refuse(self: Path, *args: object, **kwargs: object):
        if self == wheel:
            raise PermissionError(13, "Permission denied", str(self))
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", refuse)

    with pytest.raises(CliArtifactUnavailable, match="could not be read"):
        read_current(dist_dir)


# --- the public routes ------------------------------------------------------------


def test_discovery_is_public_and_describes_the_current_wheel(
    public_client: TestClient, dist_dir: Path
) -> None:
    payload = (dist_dir / WHEEL).read_bytes()

    response = public_client.get("/v1/resources/cli")

    assert response.status_code == 200
    body = response.json()
    assert body["package"] == "einherjar"
    # The wheel's version, not the server's: discovery describes the artifact it will serve.
    assert body["version"] == "9.8.7"
    assert body["filename"] == WHEEL
    assert body["size"] == len(payload)
    assert body["sha256"] == hashlib.sha256(payload).hexdigest()
    assert body["download_url"] == f"{BASE}/v1/resources/cli/{WHEEL}"
    assert body["install_command"] == f"uv tool install --force {body['download_url']}"


def test_discovery_is_not_cacheable(public_client: TestClient) -> None:
    response = public_client.get("/v1/resources/cli")

    assert response.headers["cache-control"] == "no-store"


def test_the_advertised_url_comes_from_configuration_not_the_request(
    dist_dir: Path, content_root: Path
) -> None:
    make_wheel(dist_dir)
    # A public URL is only legal with an issuer configured, which is the deployed shape anyway.
    client = TestClient(
        build_app(
            Config(
                db_url=DSN,
                logto_endpoint="https://auth.example",
                content_root=content_root,
                public_base_url="https://einherjar.example",
                cli_dist_dir=dist_dir,
            ),
            auth=None,
        )
    )

    response = client.get("/v1/resources/cli", headers={"Host": "attacker.example"})

    assert response.json()["download_url"] == (
        f"https://einherjar.example/v1/resources/cli/{WHEEL}"
    )
    assert "attacker" not in response.json()["download_url"]


def test_the_download_is_the_advertised_bytes(public_client: TestClient, dist_dir: Path) -> None:
    metadata = public_client.get("/v1/resources/cli").json()

    response = public_client.get(urlparse(metadata["download_url"]).path)

    assert response.status_code == 200
    assert response.content == (dist_dir / WHEEL).read_bytes()
    assert hashlib.sha256(response.content).hexdigest() == metadata["sha256"]
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["content-disposition"] == f'attachment; filename="{WHEEL}"'
    assert int(response.headers["content-length"]) == metadata["size"]
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "name",
    [
        "einherjar-9.8.8-py3-none-any.whl",
        "einherjar-9.8.7-py3-none-any.whl.bak",
        "..%2F..%2Fpyproject.toml",
        "%2e%2e%2f%2e%2e%2fpyproject.toml",
        "..%5C..%5Cpyproject.toml",
        "einherjar-9.8.7-py3-none-any.whl/../../pyproject.toml",
    ],
)
def test_a_name_other_than_the_current_wheel_is_not_found(
    public_client: TestClient, name: str
) -> None:
    response = public_client.get(f"/v1/resources/cli/{name}")

    assert response.status_code == 404
    assert "[project]" not in response.text


def test_an_absent_artifact_is_unavailable_rather_than_guessed(
    dist_dir: Path, content_root: Path
) -> None:
    client = _client(
        Config(db_url=DSN, content_root=content_root, cli_dist_dir=dist_dir / "absent")
    )

    discovery = client.get("/v1/resources/cli")
    download = client.get(f"/v1/resources/cli/{WHEEL}")

    assert discovery.status_code == 503
    assert download.status_code == 503
    assert "EINHERJAR_CLI_DIST_DIR" in discovery.json()["detail"]
    # Absence is the artifact's problem, not the server's.
    assert client.get("/health").status_code == 200


def test_two_wheels_are_unavailable_rather_than_chosen(
    dist_dir: Path, content_root: Path
) -> None:
    make_wheel(dist_dir, version="9.8.7")
    make_wheel(dist_dir, version="9.8.8")
    client = _client(Config(db_url=DSN, content_root=content_root, cli_dist_dir=dist_dir))

    discovery = client.get("/v1/resources/cli")

    assert discovery.status_code == 503
    assert "exactly one current artifact" in discovery.json()["detail"]
    assert client.get(f"/v1/resources/cli/{WHEEL}").status_code == 503


def test_a_corrupt_artifact_is_unavailable(dist_dir: Path, content_root: Path) -> None:
    dist_dir.mkdir(parents=True)
    (dist_dir / WHEEL).write_bytes(b"truncated")
    client = _client(Config(db_url=DSN, content_root=content_root, cli_dist_dir=dist_dir))

    discovery = client.get("/v1/resources/cli")

    assert discovery.status_code == 503
    assert "not a readable wheel" in discovery.json()["detail"]


def test_the_download_is_open_and_the_registry_still_is_not(
    keypair: tuple[bytes, bytes], content_root: Path, dist_dir: Path
) -> None:
    _, public = keypair
    make_wheel(dist_dir)
    verifier = JWTVerifier(
        public_key=public,
        issuer=ISSUER,
        audience=RESOURCE,
        algorithm="RS256",
    )
    config = Config(
        db_url=DSN,
        logto_endpoint="https://auth.example",
        public_base_url="https://einherjar.example",
        content_root=content_root,
        cli_dist_dir=dist_dir,
    )
    client = TestClient(build_app(config, auth=None, verifier=verifier))

    assert client.get("/v1/resources/cli").status_code == 200
    assert client.get(f"/v1/resources/cli/{WHEEL}").status_code == 200
    refused = client.post(
        "/v1/fleet/sessions",
        json={"machine": "m", "session_ref": "ses_1", "platform": "opencode"},
    )
    assert refused.status_code == 401
