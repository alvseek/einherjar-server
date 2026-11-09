"""The package boundary: two distributions, and neither one imports or ships the other.

The workspace shares one lockfile and one development environment, which is convenient and hides
cross-package imports completely. These checks read the sources and the metadata instead, so an
import that only works because both packages are installed here is caught as what it is. The wheel
test at the bottom is opt-in because it builds and installs both packages for real.
"""

from __future__ import annotations

import ast
import email.parser
import hashlib
import os
import subprocess
import tomllib
import zipfile
from pathlib import Path

import pytest

from einherjar.distribution.install import client_install_text
from einherjar.distribution.reader import read_current

ROOT = Path(__file__).resolve().parents[1]
SERVER_SRC = ROOT / "src" / "einherjar"
CLIENT_SRC = ROOT / "packages" / "cli" / "src" / "einherjar_cli"


def _project(path: Path) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _client_project() -> dict:
    return _project(ROOT / "packages" / "cli" / "pyproject.toml")


def _imported_roots(path: Path) -> set[str]:
    """The top-level module names one source file imports, however deeply it imports them."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def _source_files(root: Path) -> list[Path]:
    return sorted(root.rglob("*.py"))


def _dependency_names(requirements: list[str]) -> set[str]:
    """``psycopg[binary,pool]>=3.3.6`` -> ``psycopg``, in its normalised distribution form."""
    names = set()
    for requirement in requirements:
        name = requirement.split("[")[0].split(">")[0].split("=")[0].split("<")[0].strip()
        names.add(name.lower().replace("_", "-"))
    return names


def test_the_client_never_imports_the_server() -> None:
    offenders = {
        str(path.relative_to(ROOT)): sorted(roots & {"einherjar"})
        for path in _source_files(CLIENT_SRC)
        if "einherjar" in (roots := _imported_roots(path))
    }
    assert offenders == {}


def test_the_server_never_imports_the_client() -> None:
    offenders = {
        str(path.relative_to(ROOT)): sorted(roots & {"einherjar_cli"})
        for path in _source_files(SERVER_SRC)
        if "einherjar_cli" in (roots := _imported_roots(path))
    }
    assert offenders == {}


def test_the_client_modules_left_the_server_package() -> None:
    moved = [
        SERVER_SRC / "cli.py",
        SERVER_SRC / "settings.py",
        SERVER_SRC / "install",
        SERVER_SRC / "registry",
        SERVER_SRC / "state",
        SERVER_SRC / "transport",
        SERVER_SRC / "auth" / "login.py",
        SERVER_SRC / "auth" / "pkce.py",
        SERVER_SRC / "auth" / "tokens.py",
    ]
    assert [str(path.relative_to(ROOT)) for path in moved if path.exists()] == []
    # Token verification is the server's, and stays with it.
    assert (SERVER_SRC / "auth" / "verifier.py").is_file()


def test_each_distribution_owns_its_import_namespace_and_executable() -> None:
    server = _project(ROOT / "pyproject.toml")
    client = _client_project()

    assert server["project"]["name"] == "einherjar-server"
    assert server["tool"]["uv"]["build-backend"] == {
        "module-name": "einherjar",
        "module-root": "src",
    }
    assert server["project"]["scripts"] == {"einherjar-server": "einherjar.__main__:main"}

    assert client["project"]["name"] == "einherjar"
    assert client["tool"]["uv"]["build-backend"] == {
        "module-name": "einherjar_cli",
        "module-root": "src",
    }
    assert client["project"]["scripts"] == {"einherjar": "einherjar_cli.cli:main"}

    assert server["project"]["version"] == "0.1.6"
    assert client["project"]["version"] == "0.1.6"


def test_the_client_declares_only_client_dependencies() -> None:
    client = _client_project()["project"]
    assert _dependency_names(client["dependencies"]) == {"httpx", "keyring"}


def test_the_server_declares_no_client_dependency() -> None:
    server = _project(ROOT / "pyproject.toml")["project"]
    names = _dependency_names(server["dependencies"])
    assert names == {"fastapi", "fastmcp", "psycopg", "uvicorn"}
    assert "keyring" not in names
    assert "httpx" not in names


def test_the_client_is_a_workspace_development_dependency_not_a_runtime_one() -> None:
    server = _project(ROOT / "pyproject.toml")
    assert server["tool"]["uv"]["workspace"]["members"] == ["packages/cli"]
    assert server["tool"]["uv"]["sources"] == {"einherjar": {"workspace": True}}
    assert "einherjar" in server["dependency-groups"]["dev"]
    assert "einherjar" not in _dependency_names(server["project"]["dependencies"])


def test_the_server_module_entrypoint_starts_only_the_server() -> None:
    path = SERVER_SRC / "__main__.py"
    assert "einherjar_cli" not in _imported_roots(path)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    constants = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert "_turn" not in constants
    assert "einherjar_cli" not in constants


def test_a_detached_turn_starts_the_client_module() -> None:
    source = (CLIENT_SRC / "cli.py").read_text(encoding="utf-8")
    assert '"-m", "einherjar_cli", "_turn"' in source
    assert '"-m", "einherjar", "_turn"' not in source


def test_both_packages_carry_the_licence_verbatim() -> None:
    for name in ("LICENSE", "NOTICE"):
        assert (ROOT / name).read_bytes() == (ROOT / "packages" / "cli" / name).read_bytes()


# --- the same claim, made against the built wheels -----------------------------------

WHEEL_TEST = os.getenv("EINHERJAR_WHEEL_TEST") == "1"


def _run(argv: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, check=check, timeout=900)


def _venv_python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _venv_script(venv: Path, name: str) -> Path:
    return venv / ("Scripts" if os.name == "nt" else "bin") / (
        f"{name}.exe" if os.name == "nt" else name
    )


def _installed(venv: Path, module: str) -> bool:
    result = _run([str(_venv_python(venv)), "-c", f"import {module}"], check=False)
    return result.returncode == 0


def _metadata(wheel: Path) -> tuple[set[str], dict[str, str]]:
    """The wheel's declared runtime requirements and its console scripts, read from inside it."""
    with zipfile.ZipFile(wheel) as archive:
        metadata_name = next(n for n in archive.namelist() if n.endswith(".dist-info/METADATA"))
        declared = _dependency_names(
            email.parser.Parser()
            .parsestr(archive.read(metadata_name).decode("utf-8"))
            .get_all("Requires-Dist", [])
        )
        scripts: dict[str, str] = {}
        entry_points = next(
            (n for n in archive.namelist() if n.endswith(".dist-info/entry_points.txt")), None
        )
        if entry_points is not None:
            for line in archive.read(entry_points).decode("utf-8").splitlines():
                if "=" in line and not line.strip().startswith("["):
                    name, _, target = line.partition("=")
                    scripts[name.strip()] = target.strip()
    return declared, scripts


@pytest.mark.skipif(
    not WHEEL_TEST, reason="set EINHERJAR_WHEEL_TEST=1 to build and install both wheels"
)
def test_both_wheels_install_into_clean_environments(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    _run(["uv", "build", "--package", "einherjar-server", "--out-dir", str(dist)])
    _run(["uv", "build", "--package", "einherjar", "--out-dir", str(dist)])
    wheels = sorted(dist.glob("*.whl"))
    assert len(wheels) == 2, [wheel.name for wheel in wheels]
    client_wheel = next(w for w in wheels if w.name.startswith("einherjar-"))
    server_wheel = next(w for w in wheels if w.name.startswith("einherjar_server-"))

    client_requires, client_scripts = _metadata(client_wheel)
    server_requires, server_scripts = _metadata(server_wheel)
    assert client_requires == {"httpx", "keyring"}
    assert client_scripts == {"einherjar": "einherjar_cli.cli:main"}
    assert server_scripts == {"einherjar-server": "einherjar.__main__:main"}
    assert server_requires == {"fastapi", "fastmcp", "psycopg", "uvicorn"}

    client_env, server_env = tmp_path / "client-env", tmp_path / "server-env"
    for venv, wheel in ((client_env, client_wheel), (server_env, server_wheel)):
        _run(["uv", "venv", str(venv), "--python", "3.12"])
        _run(["uv", "pip", "install", "--python", str(_venv_python(venv)), str(wheel)])

    assert _installed(client_env, "einherjar_cli.cli")
    for server_module in ("einherjar", "fastapi", "fastmcp", "psycopg", "uvicorn"):
        assert not _installed(client_env, server_module), server_module
    assert _venv_script(client_env, "einherjar").exists()
    assert not _venv_script(client_env, "einherjar-server").exists()
    help_text = _run([str(_venv_script(client_env, "einherjar")), "--help"])
    assert help_text.returncode == 0
    assert "einherjar" in help_text.stdout.lower()

    assert _installed(server_env, "einherjar.__main__")
    assert not _installed(server_env, "einherjar_cli")
    assert not _installed(server_env, "keyring")
    assert _venv_script(server_env, "einherjar-server").exists()
    assert not _venv_script(server_env, "einherjar").exists()

    # The module entry points the deployment and the detached turn rely on.
    assert _run(
        [str(_venv_python(server_env)), "-c", "import einherjar; print(einherjar.__version__)"]
    ).stdout.strip() == "0.1.6"
    assert _run(
        [
            str(_venv_python(client_env)),
            "-c",
            "import einherjar_cli, einherjar_cli.__main__; print(einherjar_cli.__version__)",
        ]
    ).stdout.strip() == "0.1.6"


@pytest.mark.skipif(
    not WHEEL_TEST,
    reason="set EINHERJAR_WHEEL_TEST=1 to build the wheel the image advertises",
)
def test_the_wheel_the_image_builds_is_what_the_server_advertises(tmp_path: Path) -> None:
    """The image runs exactly this build command, and the reader must accept its output.

    This is behaviour, not a constant: the directory the image builds into is handed to the same
    reader the download face uses, and every advertised field is compared with the file that was
    actually produced.
    """
    dist = tmp_path / "cli-dist"
    _run(["uv", "build", "--package", "einherjar", "--wheel", "--out-dir", str(dist)])

    wheels = sorted(dist.glob("*.whl"))
    assert len(wheels) == 1, [wheel.name for wheel in wheels]
    payload = wheels[0].read_bytes()

    artifact = read_current(dist)

    assert artifact.filename == wheels[0].name
    assert artifact.size == len(payload)
    assert artifact.sha256 == hashlib.sha256(payload).hexdigest()

    base = "https://einherjar.example"
    text = client_install_text(base, dist)
    assert artifact.metadata(base)["download_url"] in text
    assert f"{artifact.size:,} bytes" in text
    assert artifact.sha256 in text
