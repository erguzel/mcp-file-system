"""Application service behind the ``peek`` tool."""

from pathlib import Path

from mcp_file_system.config import Settings
from mcp_file_system.core.etag import compute_etag
from mcp_file_system.core.models import PeekResult
from mcp_file_system.core.peek import structured, tabular, text
from mcp_file_system.core.render import truncate_text
from mcp_file_system.core.textio import (
    TextFormat,
    check_size,
    count_lines,
    open_lines,
    read_text,
    require_text,
)
from mcp_file_system.errors import InvalidArgumentError, ParseError
from mcp_file_system.security.paths import PathGuard, ResolvedPath

_MANIFESTS = frozenset({"pyproject.toml", "package.json", "cargo.toml"})
_MANIFEST_NAMES = {"cargo.toml": "Cargo.toml"}
_SUFFIX_KINDS = {
    ".csv": "csv",
    ".tsv": "csv",
    ".json": "json",
    ".jsonc": "json",
    ".toml": "toml",
    ".log": "log",
    ".md": "markdown",
    ".markdown": "markdown",
}
SUPPORTED = (
    "CSV/TSV, JSON (incl. comments, .jsonc), TOML, logs (.log), Markdown, pyproject.toml, "
    "package.json, Cargo.toml"
)


class Peeker:
    """Summarizes the shape of data files without returning their content."""

    def __init__(self, guard: PathGuard, settings: Settings) -> None:
        self._guard = guard
        self._settings = settings

    def peek(self, path: str) -> PeekResult:
        """Return a compact summary of a data file's structure.

        Raises:
            InvalidArgumentError: The file type has no preview.
            ParseError: The file is malformed for its type.
        """
        resolved = self._guard.resolve_file(path)
        name = resolved.path.name.lower()
        kind = "manifest" if name in _MANIFESTS else _SUFFIX_KINDS.get(resolved.path.suffix.lower())
        if kind is None:
            raise InvalidArgumentError(
                f"No preview for {resolved.display!r}. Supported: {SUPPORTED}. "
                "Use outline or read_lines instead."
            )
        stat = resolved.path.stat()
        fmt = require_text(resolved)
        total_lines = count_lines(resolved.path, fmt, stat)
        try:
            rows = self._summarize(kind, name, resolved.path, fmt, stat.st_size, resolved)
        except ParseError as exc:
            raise ParseError(
                f"Cannot parse {resolved.display}: {exc}. Use read_lines or grep instead."
            ) from None
        body, truncated = truncate_text(
            "\n".join(rows), self._settings.max_output_chars, "Preview cut: output limit."
        )
        return PeekResult(
            path=resolved.display,
            kind=kind,
            size_bytes=stat.st_size,
            total_lines=total_lines,
            etag=compute_etag(stat),
            summary=body,
            truncated=truncated,
        )

    def _summarize(
        self, kind: str, name: str, path: Path, fmt: TextFormat, size: int, resolved: ResolvedPath
    ) -> list[str]:
        if kind == "csv":
            return tabular.summarize_csv(path, fmt)
        if kind in ("log", "markdown"):
            with open_lines(path, fmt) as lines:
                if kind == "log":
                    return text.summarize_log(lines, path, fmt)
                return text.summarize_markdown(lines)
        check_size(resolved, size, self._settings.max_file_bytes)
        content = read_text(path, fmt)
        if kind == "toml":
            return structured.key_tree(structured.parse_toml(content))
        if kind == "json":
            return structured.key_tree(structured.parse_json(content))
        data = (
            structured.parse_json(content)
            if name == "package.json"
            else structured.parse_toml(content)
        )
        return structured.manifest_rows(_MANIFEST_NAMES.get(name, name), data)
