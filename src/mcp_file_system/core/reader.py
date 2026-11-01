"""Partial reading: file metadata, line ranges and pattern-delimited sections."""

import os
from collections.abc import Iterable, Iterator, Sequence
from datetime import UTC, datetime
from itertools import islice

from mcp_file_system.config import Settings
from mcp_file_system.core.etag import compute_etag
from mcp_file_system.core.finder import Walker
from mcp_file_system.core.models import (
    BatchLinesResult,
    FileInfo,
    LineRange,
    LinesResult,
    RangeError,
)
from mcp_file_system.core.render import number_lines
from mcp_file_system.core.textio import count_lines, detect_format, open_lines, require_text
from mcp_file_system.core.tracking import ReadTracker, track_result, unchanged_result
from mcp_file_system.errors import (
    FsError,
    InvalidArgumentError,
    NoMatchError,
    PathTypeError,
    describe_os_error,
)
from mcp_file_system.security.paths import PathGuard, ResolvedPath
from mcp_file_system.security.regex import Deadline, SafePattern, compile_pattern

BYTES_PER_TOKEN = 4
"""Common rule of thumb for English text and code with current tokenizers."""

DEFAULT_RADIUS = 10
"""Lines shown before and after the ``around`` line of ``read_lines``."""

MAX_BATCH_RANGES = 20
"""Most ranges per ``read_ranges`` call; each one resolves and opens a file."""


def estimate_tokens(size_bytes: int) -> int:
    """Rough number of tokens needed to read ``size_bytes`` of text."""
    return -(-size_bytes // BYTES_PER_TOKEN)  # ceiling division


class FileReader:
    """Reads metadata and selected parts of text files without loading them whole."""

    def __init__(
        self,
        guard: PathGuard,
        walker: Walker,
        settings: Settings,
        tracker: ReadTracker | None = None,
    ) -> None:
        self._guard = guard
        self._walker = walker
        self._settings = settings
        self._tracker = tracker or ReadTracker()

    def info(self, path: str) -> FileInfo:
        """Describe a file or directory so the caller can choose a reading strategy.

        A directory reports only what a walk may show: denied entries are not counted,
        and its size and modification time are withheld because both change with
        entries the agent must not learn about (e.g. creating ``.env``).
        """
        resolved = self._guard.resolve(path)
        stat = resolved.path.stat()
        if resolved.path.is_dir():
            return FileInfo(
                path=resolved.display,
                kind="directory",
                size_bytes=None,
                modified=None,
                entry_count=self._count_entries(resolved),
            )
        if not resolved.path.is_file():
            raise PathTypeError(f"Not a regular file or directory: {path}")
        fmt = detect_format(resolved.path)
        common = {
            "path": resolved.display,
            "kind": "file",
            "size_bytes": stat.st_size,
            "modified": datetime.fromtimestamp(stat.st_mtime, tz=UTC),
            "etag": compute_etag(stat),
        }
        if fmt is None:
            return FileInfo(**common, is_binary=True)  # type: ignore[arg-type]
        return FileInfo(
            **common,  # type: ignore[arg-type]
            is_binary=False,
            encoding=fmt.encoding,
            newline=fmt.newline_name,
            line_count=count_lines(resolved.path, fmt, stat),
            approx_tokens=estimate_tokens(stat.st_size),
        )

    def _count_entries(self, directory: ResolvedPath) -> int:
        """Count the immediate children ``list_directory(include_hidden=True)`` would show.

        The walk silently skips unreadable directories, so the directory is opened once
        first: a permission error must surface instead of looking like an empty directory.
        """
        with os.scandir(directory.path):
            pass
        return sum(1 for _ in self._walker.walk(directory, max_depth=1, include_hidden=True))

    def read_lines(
        self,
        path: str,
        start: int = 1,
        end: int | None = None,
        *,
        around: int | None = None,
        radius: int = DEFAULT_RADIUS,
        if_changed_since: str = "",
    ) -> LinesResult:
        """Return numbered lines ``start..end`` (1-based, inclusive).

        Negative values count from the end of the file: ``start=-20`` returns the last
        twenty lines. Output is capped by ``max_read_lines`` and ``max_output_chars``;
        when capped, ``next_start`` tells the caller where to continue.

        ``around=N`` replaces ``start``/``end`` with the window ``N-radius..N+radius``,
        clamped to the file, because line numbers from stack traces and grep hits are
        easier to pass on than a computed range.

        With ``if_changed_since=<etag>``, an unchanged file whose requested lines were
        already returned in this session yields a content-free "unchanged" result.
        """
        return self._read_lines(
            path,
            start,
            end,
            around=around,
            radius=radius,
            if_changed_since=if_changed_since,
            max_lines=self._settings.max_read_lines,
            max_chars=self._settings.max_output_chars,
            always_emit=True,
        )

    def _read_lines(
        self,
        path: str,
        start: int,
        end: int | None,
        *,
        around: int | None,
        radius: int,
        if_changed_since: str,
        max_lines: int,
        max_chars: int,
        always_emit: bool,
    ) -> LinesResult:
        """Implement :meth:`read_lines` within an explicit line and character budget.

        The budget is a parameter so that batch reads can share one budget across
        several ranges. Without ``always_emit``, a first row that does not fit gives a
        result with no lines instead of overrunning the budget.
        """
        if around is not None:
            _check_around(around, radius, start, end)
        if start == 0:
            raise InvalidArgumentError(
                "start is 1-based: use 1 for the first line or a negative number to count "
                "from the end (-1 is the last line)."
            )
        if end == 0:
            raise InvalidArgumentError("end is 1-based; use -1 for the last line.")
        resolved = self._guard.resolve_file(path)
        fmt = require_text(resolved)
        stat = resolved.path.stat()
        total = count_lines(resolved.path, fmt, stat)
        etag = compute_etag(stat)
        if total == 0:
            return self._empty(resolved, etag, "File is empty.")

        note = None
        if around is not None:
            first, last, note = _around_window(around, radius, total, max_lines, resolved.display)
        else:
            first = start if start > 0 else max(1, total + start + 1)
            if first > total:
                raise InvalidArgumentError(
                    f"start={start} is past the end of {resolved.display} ({total} lines)."
                )
            last = total if end is None else (end if end > 0 else total + end + 1)
            if last < first:
                raise InvalidArgumentError(f"end ({end}) resolves to before start ({start}).")
            last = min(last, total)
        window_last = min(last, first + max_lines - 1)
        unchanged = unchanged_result(
            self._tracker,
            resolved.path,
            display=resolved.display,
            etag=etag,
            if_changed_since=if_changed_since,
            start=first,
            end=window_last,
            total=total,
        )
        if unchanged is not None:
            return unchanged

        with open_lines(resolved.path, fmt) as lines:
            selected = enumerate(islice(lines, first - 1, window_last), start=first)
            result = self._build(
                resolved,
                etag,
                total,
                selected,
                requested_last=last,
                max_chars=max_chars,
                note=note,
                always_emit=always_emit,
            )
        return track_result(self._tracker, resolved.path, result, if_changed_since=if_changed_since)

    def read_ranges(self, ranges: Sequence[LineRange]) -> BatchLinesResult:
        """Read several line ranges, from one or more files, within one shared budget.

        Ranges are read in the given order and together spend ``max_read_lines`` and
        ``max_output_chars``. The character budget covers the numbered lines, not headers,
        notes, errors or the continuation, and only the first row returned may exceed it,
        so that a continuation always moves forward. A range cut by the budget, or whose
        first row does not fit, is listed in ``cut`` with the lines still to read, and so
        is every range after it, unread and as requested. A range that fails (missing
        file, denied path, invalid range, unreadable file) is reported in ``errors`` and
        does not stop the others; a range waiting in ``cut`` is not tried, so its failure
        is reported by the call that continues. An exception other than ``FsError`` or
        ``OSError`` still ends the call.
        """
        if len(ranges) > MAX_BATCH_RANGES:
            raise InvalidArgumentError(
                f"At most {MAX_BATCH_RANGES} ranges per call; split them across calls."
            )
        lines_left = self._settings.max_read_lines
        chars_left = self._settings.max_output_chars
        reads: list[LinesResult] = []
        errors: list[RangeError] = []
        cut: list[LineRange] = []
        for spec in ranges:
            if lines_left <= 0 or chars_left <= 0:
                cut.append(spec)
                continue
            # Nothing returned yet: the first row may overrun, so continuations move forward.
            always_emit = lines_left == self._settings.max_read_lines
            try:
                result = self._read_lines(
                    spec.path,
                    spec.start,
                    spec.end,
                    around=None,
                    radius=DEFAULT_RADIUS,
                    if_changed_since="",
                    max_lines=lines_left,
                    max_chars=chars_left,
                    always_emit=always_emit,
                )
            except FsError as exc:
                errors.append(RangeError(**spec.model_dump(), message=str(exc)))
                continue
            except OSError as exc:
                errors.append(RangeError(**spec.model_dump(), message=describe_os_error(exc)))
                continue
            if not always_emit and result.total_lines and not result.end_line:
                cut.append(spec)
                chars_left = 0  # spent in order: later ranges wait for the continuation
                continue
            reads.append(result)
            if result.end_line:
                lines_left -= result.end_line - result.start_line + 1
                chars_left -= len(result.content) + 1
            if result.next_start is not None:
                total = result.total_lines
                end = spec.end if spec.end > 0 else total + spec.end + 1
                cut.append(LineRange(path=spec.path, start=result.next_start, end=min(end, total)))
                chars_left = 0  # spent in order: later ranges wait for the continuation
        return BatchLinesResult(reads=reads, errors=errors, cut=cut)

    def read_between(
        self,
        path: str,
        start_pattern: str,
        end_pattern: str | None = None,
        *,
        occurrence: int = 1,
        inclusive: bool = True,
        fixed_string: bool = False,
        ignore_case: bool = False,
        max_lines: int | None = None,
    ) -> LinesResult:
        """Return the block from the Nth ``start_pattern`` match to the next ``end_pattern``.

        This is the equivalent of ``sed -n '/start/,/end/p'``. The end pattern is
        searched from the line after the start line. Without an end pattern (or if it
        never matches) the block runs to the end of the file, subject to the limits.
        """
        if occurrence < 1:
            raise InvalidArgumentError("occurrence must be >= 1.")
        limit = min(max_lines or self._settings.max_read_lines, self._settings.max_read_lines)
        if limit < 1:
            raise InvalidArgumentError("max_lines must be >= 1.")
        deadline = Deadline(self._settings.search_timeout_s)
        start_re = self._compile(start_pattern, deadline, fixed_string, ignore_case)
        end_re = (
            self._compile(end_pattern, deadline, fixed_string, ignore_case) if end_pattern else None
        )
        resolved = self._guard.resolve_file(path)
        fmt = require_text(resolved)
        stat = resolved.path.stat()
        total = count_lines(resolved.path, fmt, stat)
        etag = compute_etag(stat)

        with open_lines(resolved.path, fmt) as lines:
            numbered = enumerate(lines, start=1)
            start = _find_occurrence(numbered, start_re, occurrence)
            if start is None:
                raise NoMatchError(
                    f"start_pattern {start_pattern!r} does not match {occurrence} time(s) "
                    f"in {resolved.display}."
                )
            block, end_found, cut = _collect_block(numbered, start, end_re, inclusive, limit)

        note = None
        if end_re is not None and not end_found:
            note = (
                f"end_pattern {end_pattern!r} not reached within {limit} lines."
                if cut
                else f"end_pattern {end_pattern!r} not found; block runs to end of file."
            )
        if not block:
            return self._empty(resolved, etag, "The block between the patterns is empty.", total)
        requested_last = block[-1][0] + 1 if cut else block[-1][0]
        result = self._build(
            resolved,
            etag,
            total,
            block,
            requested_last=requested_last,
            max_chars=self._settings.max_output_chars,
            note=note,
            known_end=not cut,
        )
        return track_result(self._tracker, resolved.path, result)

    def _compile(
        self, pattern: str, deadline: Deadline, fixed_string: bool, ignore_case: bool
    ) -> SafePattern:
        return compile_pattern(
            pattern,
            deadline=deadline,
            max_length=self._settings.max_pattern_length,
            fixed_string=fixed_string,
            ignore_case=ignore_case,
        )

    def _build(
        self,
        resolved: ResolvedPath,
        etag: str,
        total: int,
        lines: Iterable[tuple[int, str]],
        *,
        requested_last: int,
        max_chars: int,
        note: str | None = None,
        known_end: bool = True,
        always_emit: bool = True,
    ) -> LinesResult:
        content, first, last = number_lines(
            lines,
            width=len(str(total)),
            max_line_chars=self._settings.max_line_chars,
            max_chars=max_chars,
            always_emit=always_emit,
        )
        if first is None or last is None:
            return self._empty(resolved, etag, note or "No lines selected.", total)
        truncated = last < requested_last
        return LinesResult(
            path=resolved.display,
            start_line=first,
            end_line=last,
            total_lines=total,
            etag=etag,
            content=content,
            truncated=truncated,
            next_start=last + 1 if truncated else None,
            next_end=min(requested_last, total) if truncated and known_end else None,
            note=note,
        )

    @staticmethod
    def _empty(resolved: ResolvedPath, etag: str, note: str, total: int = 0) -> LinesResult:
        return LinesResult(
            path=resolved.display,
            start_line=0,
            end_line=0,
            total_lines=total,
            etag=etag,
            content="",
            note=note,
        )


def _check_around(around: int, radius: int, start: int, end: int | None) -> None:
    """Reject ``around`` arguments that cannot describe a window."""
    if start != 1 or end not in (None, -1):
        raise InvalidArgumentError(
            "Pass either around (with radius) or start/end, not both. Use around for a "
            "line from an error message or grep hit."
        )
    if around < 1:
        raise InvalidArgumentError("around is a 1-based line number (1 is the first line).")
    if radius < 0:
        raise InvalidArgumentError("radius must be >= 0.")


def _around_window(
    around: int, radius: int, total: int, max_lines: int, display: str
) -> tuple[int, int, str | None]:
    """Resolve ``around``/``radius`` to a ``(first, last, note)`` window inside the file.

    A radius whose window exceeds ``max_lines`` is shrunk on both sides, so the window
    stays centered and the ``around`` line is never cut off by the line cap.
    """
    if around > total:
        raise InvalidArgumentError(f"around={around} is past the end of {display} ({total} lines).")
    note = None
    max_radius = (max_lines - 1) // 2
    if radius > max_radius:
        note = f"radius reduced from {radius} to {max_radius} to fit max_read_lines."
        radius = max_radius
    return max(1, around - radius), min(total, around + radius), note


def _find_occurrence(
    numbered: Iterator[tuple[int, str]], pattern: SafePattern, occurrence: int
) -> tuple[int, str] | None:
    seen = 0
    for number, line in numbered:
        if pattern.search(line):
            seen += 1
            if seen == occurrence:
                return number, line
    return None


def _collect_block(
    numbered: Iterator[tuple[int, str]],
    start: tuple[int, str],
    end_pattern: SafePattern | None,
    inclusive: bool,
    limit: int,
) -> tuple[list[tuple[int, str]], bool, bool]:
    """Collect the lines of a section that begins at ``start``.

    Returns:
        The collected ``(number, text)`` pairs (at most ``limit``), whether the end
        pattern was found, and whether collection stopped at ``limit`` with more lines
        remaining.
    """
    block: list[tuple[int, str]] = [start] if inclusive else []
    for number, line in numbered:
        is_end = end_pattern is not None and end_pattern.search(line) is not None
        if is_end and not inclusive:
            return block, True, False
        if len(block) >= limit:
            return block, False, True
        block.append((number, line))
        if is_end:
            return block, True, False
    return block, False, False
