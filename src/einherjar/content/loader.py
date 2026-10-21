"""The served content: the fleet procedures and templates, read live from the content checkout.

The procedures are served verbatim. The fleet repo has no component references, so its compiled
output equals its source; the equality test still compiles the repo and compares, because that is
the property a future component would break silently.

A template is a fill-in artifact an agent copies, so it is served as a resource rather than
inlined into a procedure. Nothing here points at a local clone.
"""

from __future__ import annotations

import re
from pathlib import Path

_NOISE = re.compile(r"[*_`]|\[([^\]]*)\]\([^)]*\)")
_SKIP = re.compile(r"^\s*(#|[-*>|]|\d+\.|```|---)")


class ContentLoader:
    """One checkout, four procedures, two templates."""

    def __init__(self, root: Path | str) -> None:
        self._root = Path(root)

    # --- what is served -------------------------------------------------------------

    def available(self) -> bool:
        return (self._root / "procedures").is_dir()

    def list_procedures(self) -> list[str]:
        return sorted(p.stem for p in (self._root / "procedures").glob("*.md"))

    def get_procedure(self, name: str, argument: str | None = None) -> str:
        """The procedure's text, with ``$ARGUMENTS`` filled when one was passed.

        ``None`` means "as authored", which is what the equality test needs: a served procedure
        with no argument is the compiled file, byte for byte.
        """
        text = self._read(self._root / "procedures" / f"{name}.md")
        if argument is None:
            return text
        return text.replace("$ARGUMENTS", argument)

    def title_procedure(self, name: str) -> str:
        return self._title(self.get_procedure(name), name)

    def describe_procedure(self, name: str) -> str:
        lead = self._lead(self.get_procedure(name))
        trigger = (
            f"Use when the user runs /{name}, asks for the {self.title_procedure(name)} procedure, "
            f"or mentions {name}."
        )
        return f"{lead} {trigger}".strip() if lead else trigger

    def list_resources(self) -> list[str]:
        directory = self._root / "templates"
        return sorted(
            {path.stem for path in directory.iterdir() if path.suffix in {".md", ".csv"}}
            if directory.is_dir()
            else set()
        )

    def get_resource(self, name: str) -> str:
        return self._read(self._resource_path(name))

    def mime_type_resource(self, name: str) -> str:
        return "text/csv" if self._resource_path(name).suffix == ".csv" else "text/markdown"

    def title_resource(self, name: str) -> str:
        return self._title(self.get_resource(name), name)

    def _resource_path(self, name: str) -> Path:
        for suffix in (".md", ".csv"):
            candidate = self._root / "templates" / f"{name}{suffix}"
            if candidate.is_file():
                return candidate
        raise KeyError(name)

    # --- helpers --------------------------------------------------------------------

    @staticmethod
    def _read(path: Path) -> str:
        if not path.is_file():
            raise KeyError(path.stem)
        return path.read_text(encoding="utf-8")

    @staticmethod
    def _title(text: str, fallback: str) -> str:
        for line in text.splitlines():
            if line.startswith("# "):
                return _NOISE.sub(r"\1", line[2:]).strip() or fallback
        return fallback

    @staticmethod
    def _lead(text: str) -> str:
        for raw in text.splitlines():
            line = raw.strip()
            if not line or _SKIP.match(raw):
                continue
            line = _NOISE.sub(r"\1", line).strip()
            if not line:
                continue
            match = re.search(r"(?<=[.!?])\s", line)
            return (line[: match.start()] if match else line).strip()
        return ""
