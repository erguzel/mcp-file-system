"""Application service behind the ``outline`` and ``read_symbol`` tools."""

from mcp_file_system.config import Settings
from mcp_file_system.core.etag import compute_etag
from mcp_file_system.core.models import LinesResult, OutlineResult, Symbol
from mcp_file_system.core.outline.base import OutlineProvider
from mcp_file_system.core.outline.registry import OutlineRegistry
from mcp_file_system.core.render import number_lines
from mcp_file_system.core.textio import check_size, read_text, require_text, split_lines
from mcp_file_system.errors import InvalidArgumentError, ParseError, SymbolNotFoundError
from mcp_file_system.security.paths import PathGuard, ResolvedPath

MAX_SYMBOLS = 2000
_MAX_LISTED_CANDIDATES = 15


class Outliner:
    """Builds file outlines and reads individual symbols by name."""

    def __init__(self, guard: PathGuard, registry: OutlineRegistry, settings: Settings) -> None:
        self._guard = guard
        self._registry = registry
        self._settings = settings

    def outline(self, path: str) -> OutlineResult:
        """Return the structural skeleton of a supported file."""
        resolved, provider, lines, symbols, _ = self._analyze(path)
        return OutlineResult(
            path=resolved.display,
            language=provider.language,
            total_lines=len(lines),
            symbols=symbols[:MAX_SYMBOLS],
            truncated=len(symbols) > MAX_SYMBOLS,
        )

    def read_symbol(self, path: str, name: str) -> LinesResult:
        """Return the source lines of one symbol.

        ``name`` is matched against qualified names first (``Class.method``,
        ``Usage > Install``), then against plain names if that is unambiguous.
        """
        if not name.strip():
            raise InvalidArgumentError("name must not be empty.")
        resolved, _, lines, symbols, etag = self._analyze(path)
        matches = [s for s in symbols if s.qualified_name == name]
        if not matches:
            matches = [s for s in symbols if s.name == name]
            if len({s.qualified_name for s in matches}) > 1:
                listed = ", ".join(f"{s.qualified_name} (line {s.start_line})" for s in matches)
                raise SymbolNotFoundError(
                    f"{name!r} is ambiguous in {resolved.display}: {listed}. Pass a qualified name."
                )
        if not matches:
            available = ", ".join(s.qualified_name for s in symbols[:_MAX_LISTED_CANDIDATES])
            more = " ..." if len(symbols) > _MAX_LISTED_CANDIDATES else ""
            raise SymbolNotFoundError(
                f"No symbol {name!r} in {resolved.display}. "
                f"Available: {available or '(none)'}{more}. Use outline to list symbols."
            )
        # Several definitions with one qualified name (overloads, property setters) are
        # returned together as one span.
        start = min(s.start_line for s in matches)
        end = max(s.end_line for s in matches)
        window_end = min(end, start + self._settings.max_read_lines - 1)
        content, first, last = number_lines(
            ((n, lines[n - 1]) for n in range(start, window_end + 1)),
            width=len(str(len(lines))),
            max_line_chars=self._settings.max_line_chars,
            max_chars=self._settings.max_output_chars,
        )
        first = first or start
        last = last or start
        truncated = last < end
        label = _describe(matches)
        return LinesResult(
            path=resolved.display,
            start_line=first,
            end_line=last,
            total_lines=len(lines),
            etag=etag,
            content=content,
            truncated=truncated,
            next_start=last + 1 if truncated else None,
            note=label,
        )

    def _analyze(
        self, path: str
    ) -> tuple[ResolvedPath, OutlineProvider, list[str], list[Symbol], str]:
        resolved = self._guard.resolve_file(path)
        provider = self._registry.for_path(resolved.path)
        if provider is None:
            supported = ", ".join(self._registry.supported_suffixes)
            raise InvalidArgumentError(
                f"No outline support for {resolved.display!r}. Supported suffixes: {supported}. "
                "Use grep to locate definitions instead."
            )
        stat = resolved.path.stat()
        check_size(resolved, stat.st_size, self._settings.max_file_bytes)
        fmt = require_text(resolved)
        text = read_text(resolved.path, fmt)
        try:
            symbols = provider.outline(text)
        except ParseError as exc:
            raise ParseError(
                f"Cannot parse {resolved.display} as {provider.language}: {exc}. "
                "Use read_lines or grep instead."
            ) from None
        return resolved, provider, split_lines(text), symbols, compute_etag(stat)


def _describe(symbols: list[Symbol]) -> str:
    first = symbols[0]
    label = f"{first.kind} {first.qualified_name}"
    if len(symbols) > 1:
        label += f" ({len(symbols)} definitions)"
    return label
