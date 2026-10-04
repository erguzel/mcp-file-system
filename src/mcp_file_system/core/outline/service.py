"""Application service behind the ``outline`` and ``read_symbol`` tools."""

from collections.abc import Iterable

from mcp_file_system.config import Settings
from mcp_file_system.core.etag import compute_etag
from mcp_file_system.core.models import LinesResult, OutlineResult, SkeletonResult, Symbol
from mcp_file_system.core.outline.base import OutlineProvider
from mcp_file_system.core.outline.registry import OutlineRegistry
from mcp_file_system.core.render import number_lines
from mcp_file_system.core.skeleton import render_skeleton
from mcp_file_system.core.textio import check_size, read_text, require_text, split_lines
from mcp_file_system.core.tracking import (
    UNCHANGED,
    ReadTracker,
    join_notes,
    track_result,
    unchanged_result,
)
from mcp_file_system.errors import FsError, InvalidArgumentError, ParseError, SymbolNotFoundError
from mcp_file_system.security.paths import PathGuard, ResolvedPath

MAX_SYMBOLS = 2000
_MAX_LISTED_CANDIDATES = 15


class Outliner:
    """Builds file outlines and reads individual symbols by name."""

    def __init__(
        self,
        guard: PathGuard,
        registry: OutlineRegistry,
        settings: Settings,
        tracker: ReadTracker | None = None,
    ) -> None:
        self._guard = guard
        self._registry = registry
        self._settings = settings
        self._tracker = tracker or ReadTracker()

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

    def read_symbol(self, path: str, name: str, *, if_changed_since: str = "") -> LinesResult:
        """Return the source lines of one symbol.

        ``name`` is matched against qualified names first (``Class.method``,
        ``Usage > Install``), then against plain names if that is unambiguous. With
        ``if_changed_since=<etag>``, an unchanged symbol already returned in this
        session yields a content-free "unchanged" result.
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
        unchanged = unchanged_result(
            self._tracker,
            resolved.path,
            display=resolved.display,
            etag=etag,
            if_changed_since=if_changed_since,
            start=start,
            end=window_end,
            total=len(lines),
        )
        if unchanged is not None:
            return unchanged.model_copy(update={"note": join_notes(_describe(matches), UNCHANGED)})
        content, first, last = number_lines(
            ((n, lines[n - 1]) for n in range(start, window_end + 1)),
            width=len(str(len(lines))),
            max_line_chars=self._settings.max_line_chars,
            max_chars=self._settings.max_output_chars,
        )
        first = first or start
        last = last or start
        truncated = last < end
        result = LinesResult(
            path=resolved.display,
            start_line=first,
            end_line=last,
            total_lines=len(lines),
            etag=etag,
            content=content,
            truncated=truncated,
            next_start=last + 1 if truncated else None,
            note=_describe(matches),
        )
        return track_result(self._tracker, resolved.path, result, if_changed_since=if_changed_since)

    def skeleton(self, path: str) -> SkeletonResult:
        """Return the file with function bodies folded (code files only)."""
        resolved, provider, text, etag = self._load(path)
        fold_ranges = getattr(provider, "fold_ranges", None)
        if fold_ranges is None:
            raise InvalidArgumentError(
                f"No skeleton view for {provider.language} files like {resolved.display!r}; "
                "use outline, which lists their structure with line spans."
            )
        try:
            folds = fold_ranges(text)
        except ParseError as exc:
            raise ParseError(
                f"Cannot parse {resolved.display} as {provider.language}: {exc}. "
                "Use read_lines or grep instead."
            ) from None
        lines = split_lines(text)
        content, shown, folded, next_start = render_skeleton(
            lines,
            folds,
            max_line_chars=self._settings.max_line_chars,
            max_chars=self._settings.max_output_chars,
        )
        return SkeletonResult(
            path=resolved.display,
            language=provider.language,
            total_lines=len(lines),
            shown_lines=shown,
            folded_lines=folded,
            etag=etag,
            content=content,
            truncated=next_start is not None,
            next_start=next_start,
        )

    def enclosing_symbols(self, path: str, lines: Iterable[int]) -> dict[int, str]:
        """Map line numbers to the qualified name of their innermost enclosing symbol.

        Best effort for search results: files without an outline provider, too large,
        binary or unparsable files simply yield no entries.
        """
        wanted = sorted(set(lines))
        try:
            _, _, _, symbols, _ = self._analyze(path)
        except (FsError, OSError):
            return {}
        found: dict[int, str] = {}
        for line in wanted:
            containing = [s for s in symbols if s.start_line <= line <= s.end_line]
            if containing:
                innermost = max(containing, key=lambda s: (s.depth, -(s.end_line - s.start_line)))
                found[line] = innermost.qualified_name
        return found

    def _load(self, path: str) -> tuple[ResolvedPath, OutlineProvider, str, str]:
        """Resolve, select the provider, check size and decode: (path, provider, text, etag)."""
        resolved = self._guard.resolve_file(path)
        provider = self._registry.for_path(resolved.path) or self._registry.for_shebang(
            _first_line(resolved)
        )
        if provider is None:
            extra = self._registry.missing_extra(resolved.path)
            if extra is not None:
                raise InvalidArgumentError(
                    f"Outlines for {resolved.display!r} need the optional '{extra}' extra: "
                    f'pip install "mcp-file-system[{extra}]". Use grep meanwhile.'
                )
            suffixes = ", ".join(self._registry.supported_suffixes)
            interpreters = ", ".join(self._registry.supported_interpreters)
            raise InvalidArgumentError(
                f"No outline support for {resolved.display!r}. Supported suffixes: {suffixes}; "
                f"shebang interpreters: {interpreters}. Use grep to locate definitions instead."
            )
        stat = resolved.path.stat()
        check_size(resolved, stat.st_size, self._settings.max_file_bytes)
        fmt = require_text(resolved)
        return resolved, provider, read_text(resolved.path, fmt), compute_etag(stat)

    def _analyze(
        self, path: str
    ) -> tuple[ResolvedPath, OutlineProvider, list[str], list[Symbol], str]:
        resolved, provider, text, etag = self._load(path)
        try:
            symbols = provider.outline(text)
        except ParseError as exc:
            raise ParseError(
                f"Cannot parse {resolved.display} as {provider.language}: {exc}. "
                "Use read_lines or grep instead."
            ) from None
        return resolved, provider, split_lines(text), symbols, etag


def _first_line(resolved: ResolvedPath) -> str:
    """The first line of a file, from at most its first 256 bytes."""
    with resolved.path.open("rb") as handle:
        head = handle.read(256)
    return head.split(b"\n", 1)[0].decode("latin-1")


def _describe(symbols: list[Symbol]) -> str:
    first = symbols[0]
    label = f"{first.kind} {first.qualified_name}"
    if len(symbols) > 1:
        label += f" ({len(symbols)} definitions)"
    return label
