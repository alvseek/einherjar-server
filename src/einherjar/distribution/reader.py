"""The CLI wheel this server hands out: one artifact, described by its own bytes.

Only the build that shipped with the running server is downloadable, and everything discovery says
about it is read from the file itself — the version and name inside its METADATA, the byte count,
the SHA-256. A version written by hand is a promise about bytes nobody checked, and the one failure
mode this face must not have is advertising a wheel that cannot be installed.

A directory that does not hold exactly one valid client wheel is an error, never a guess. Nothing
here touches the registry or a request, so the HTTP route and the MCP resource can share it and a
missing artifact fails only what needs one.
"""

from __future__ import annotations

import hashlib
import re
import zipfile
from dataclasses import dataclass
from email.parser import Parser
from pathlib import Path

#: The distribution name of the client. An `einherjar-server` wheel in the directory is a mistake,
#: and serving it would hand a server install to somebody who asked for the CLI.
CLI_PACKAGE = "einherjar"

_METADATA = ".dist-info/METADATA"
_SUFFIX = ".whl"
_CHUNK = 1024 * 1024
#: What a wheel filename may contain. It is also what makes the filename safe in a URL as it stands
#: and safe as a single path segment, so a validated name needs no escaping anywhere after this.
_SAFE_NAME = re.compile(r"^[A-Za-z0-9._+-]+$")


class CliArtifactUnavailable(RuntimeError):
    """The configured directory does not hold exactly one valid CLI wheel.

    The message is written for an operator: it names the directory, says what was found, and names
    the setting that decides it. It never invents a version to look healthy.

    Filesystem failure is reported through this exception too. A directory that cannot be listed
    and a wheel that cannot be measured are the same answer to the caller as a missing one, so a
    caller handles one failure rather than a domain error plus whatever ``OSError`` slipped past it.
    """


@dataclass(frozen=True)
class CliArtifact:
    """One wheel, as discovery describes it and the download serves it."""

    path: Path
    filename: str
    version: str
    size: int
    sha256: str

    def metadata(self, public_base_url: str) -> dict[str, object]:
        """What discovery advertises: the current file, its bytes, and how to install it."""
        url = download_url(public_base_url, self.filename)
        return {
            "package": CLI_PACKAGE,
            "version": self.version,
            "filename": self.filename,
            "size": self.size,
            "sha256": self.sha256,
            "download_url": url,
            "install_command": f"uv tool install --force {url}",
        }


def download_url(public_base_url: str, filename: str) -> str:
    """The absolute URL of one wheel, built from configuration rather than the request's host.

    The filename is validated before it reaches here (letters, digits, dots, underscores, dashes,
    and the ``.whl`` suffix only), so it is safe to place in a URL as it stands.
    """
    return f"{public_base_url.rstrip('/')}/v1/resources/cli/{filename}"


def read_current(directory: Path | str) -> CliArtifact:
    """The one current client wheel in ``directory``, refusing to guess when there is not one."""
    root = Path(directory)
    if not root.is_dir():
        raise CliArtifactUnavailable(
            f"no CLI artifact directory at {root}: build the client wheel into it, or point "
            "EINHERJAR_CLI_DIST_DIR at the directory that holds it"
        )

    # Chosen by name, not by ``is_file()``: an entry that cannot even be stat'ed is reported as an
    # unreadable artifact instead of silently disappearing from the set.
    try:
        wheels = sorted(
            (path for path in root.iterdir() if path.suffix.lower() == _SUFFIX),
            key=lambda path: path.name,
        )
    except OSError as exc:
        raise CliArtifactUnavailable(f"cannot list {root}: {exc}") from exc
    if not wheels:
        raise CliArtifactUnavailable(
            f"no CLI wheel in {root}: build one there (uv build --package einherjar --out-dir "
            f"{root}), or point EINHERJAR_CLI_DIST_DIR at the directory that holds it"
        )
    if len(wheels) > 1:
        found = ", ".join(path.name for path in wheels)
        raise CliArtifactUnavailable(
            f"{len(wheels)} wheels in {root} ({found}): the server serves exactly one current "
            "artifact, so remove the ones that are not current"
        )
    try:
        return _describe(wheels[0])
    except OSError as exc:
        raise CliArtifactUnavailable(f"{wheels[0].name} could not be read: {exc}") from exc


def _describe(path: Path) -> CliArtifact:
    """Validate one wheel and derive everything discovery will say about it."""
    filename_name, filename_version = _filename_parts(path.name)
    declared_name, declared_version = _declaration(path)

    if _normalise(declared_name) != CLI_PACKAGE:
        raise CliArtifactUnavailable(
            f"{path.name} is the {declared_name!r} distribution, not the {CLI_PACKAGE!r} client: "
            "only the einherjar CLI wheel is served"
        )
    if _normalise(filename_name) != CLI_PACKAGE:
        raise CliArtifactUnavailable(
            f"{path.name} is named for {filename_name!r}, not the {CLI_PACKAGE!r} client: only "
            "the einherjar CLI wheel is served"
        )
    if declared_version != filename_version:
        raise CliArtifactUnavailable(
            f"{path.name} declares version {declared_version!r} inside it, but its filename says "
            f"{filename_version!r}: the wheel is inconsistent, so neither can be advertised"
        )

    return CliArtifact(
        path=path,
        filename=path.name,
        version=filename_version,
        size=path.stat().st_size,
        sha256=_sha256(path),
    )


def _filename_parts(filename: str) -> tuple[str, str]:
    """The distribution and version a wheel filename claims, refusing one that claims nothing."""
    stem = filename[: -len(_SUFFIX)]
    parts = stem.split("-")
    # name-version-python-abi-platform, with an optional build tag before the python tag.
    if len(parts) not in {5, 6} or not all(parts) or not _SAFE_NAME.match(stem):
        raise CliArtifactUnavailable(
            f"{filename} is not a wheel filename: expected "
            "name-version-python-abi-platform.whl"
        )
    return parts[0], parts[1]


def _declaration(path: Path) -> tuple[str, str]:
    """The name and version the wheel's own METADATA declares."""
    try:
        with zipfile.ZipFile(path) as archive:
            entry = next(
                (
                    name
                    for name in archive.namelist()
                    if name.endswith(_METADATA) and name.count("/") == 1
                ),
                None,
            )
            if entry is None:
                raise CliArtifactUnavailable(
                    f"{path.name} carries no top-level .dist-info/METADATA, so it is not a wheel "
                    "this server can describe"
                )
            metadata = Parser().parsestr(archive.read(entry).decode("utf-8"))
    except (zipfile.BadZipFile, UnicodeDecodeError, OSError) as exc:
        raise CliArtifactUnavailable(f"{path.name} is not a readable wheel ({exc})") from exc

    name, version = (metadata.get("Name") or "").strip(), (metadata.get("Version") or "").strip()
    if not name or not version:
        raise CliArtifactUnavailable(
            f"{path.name} declares no Name or Version in its METADATA, so it cannot be described"
        )
    return name, version


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalise(name: str) -> str:
    """PEP 503 normalisation, because a wheel may spell `einherjar` with underscores."""
    return re.sub(r"[-_.]+", "-", name).lower()
