"""Guarded file editing.

Every edit:

* is confined by :class:`PathGuard` like any read,
* refuses to run against a stale view of the file (etag check, re-verified right
  before writing),
* preserves the file's encoding, BOM, newline style and permissions, and round-trips
  undecodable bytes unchanged (``surrogateescape``),
* is written atomically (temporary file in the same directory + :func:`os.replace`),
* returns a unified diff, and can be previewed with ``dry_run``.
"""

import difflib
import os
import re
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

from mcp_file_system.config import Settings
from mcp_file_system.core.etag import compute_etag, ensure_etag
from mcp_file_system.core.models import EditResult
from mcp_file_system.core.render import truncate_text
from mcp_file_system.core.textio import (
    TextFormat,
    check_size,
    require_text,
    split_lines,
    split_lines_keepends,
)
from mcp_file_system.errors import (
    AlreadyExistsError,
    InvalidArgumentError,
    InvalidPatternError,
    MatchCountError,
    NoMatchError,
    PathTypeError,
    StaleFileError,
)
from mcp_file_system.security.paths import PathGuard, ResolvedPath
from mcp_file_system.security.regex import Deadline, compile_pattern

NEW_FILE_MODE = 0o644
_EOL = re.compile(r"\r\n|\r|\n")
_LINE_ENDINGS = ("\n", "\r")


@dataclass(frozen=True, slots=True)
class _Document:
    text: str
    fmt: TextFormat
    etag: str


def normalize_newlines(text: str, newline: str) -> str:
    """Convert every line terminator in ``text`` to ``newline``."""
    return _EOL.sub(newline, text)


def atomic_write(path: Path, data: bytes) -> None:
    """Replace ``path`` with ``data`` atomically, keeping the existing permission bits."""
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        mode = NEW_FILE_MODE
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        tmp.chmod(mode)
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


class FileEditor:
    """Implements the opt-in editing tools."""

    def __init__(self, guard: PathGuard, settings: Settings) -> None:
        self._guard = guard
        self._settings = settings

    def replace(
        self,
        path: str,
        pattern: str,
        replacement: str,
        *,
        regex: bool = False,
        ignore_case: bool = False,
        expected_count: int | None = 1,
        expected_etag: str | None = None,
        dry_run: bool = False,
    ) -> EditResult:
        """Replace occurrences of ``pattern`` and fail unless the match count is as expected.

        In literal mode (the default) line terminators in ``pattern`` and
        ``replacement`` are adapted to the file's newline style. In regex mode the
        replacement may reference groups (``\\1``, ``\\g<name>``).
        """
        if not pattern:
            raise InvalidPatternError("Pattern must not be empty.")
        if expected_count is not None and expected_count < 1:
            raise InvalidArgumentError("expected_count must be >= 1 (or null for any count).")
        resolved = self._guard.resolve_file(path)
        document = self._load(resolved, expected_etag)
        text = document.text
        if regex:
            compiled = compile_pattern(
                pattern,
                deadline=Deadline(self._settings.search_timeout_s),
                max_length=self._settings.max_pattern_length,
                ignore_case=ignore_case,
            )
            new_text, count = compiled.subn(replacement, text)
        else:
            literal = normalize_newlines(pattern, document.fmt.newline)
            substitute = normalize_newlines(replacement, document.fmt.newline)
            if ignore_case:
                compiled = compile_pattern(
                    literal,
                    deadline=Deadline(self._settings.search_timeout_s),
                    max_length=max(len(literal), self._settings.max_pattern_length),
                    fixed_string=True,
                    ignore_case=True,
                )
                new_text, count = compiled.subn(lambda _match: substitute, text)
            else:
                count = text.count(literal)
                new_text = text.replace(literal, substitute)
        if count == 0:
            raise NoMatchError(
                f"Pattern not found in {resolved.display}. Re-read the target lines; whitespace "
                "and indentation must match exactly."
            )
        if expected_count is not None and count != expected_count:
            raise MatchCountError(
                f"Pattern matches {count} time(s) in {resolved.display}, expected "
                f"{expected_count}. Add surrounding context to make it unique, or set "
                "expected_count to the intended number."
            )
        return self._commit(resolved, document, new_text, changes=count, dry_run=dry_run)

    def edit_lines(
        self,
        path: str,
        start: int,
        end: int,
        new_text: str,
        *,
        expected_etag: str,
        dry_run: bool = False,
    ) -> EditResult:
        """Replace lines ``start..end`` (1-based, inclusive) with ``new_text``.

        ``end = start - 1`` inserts before ``start`` without removing anything (use
        ``start = total_lines + 1`` to append); an empty ``new_text`` deletes the range.
        """
        resolved = self._guard.resolve_file(path)
        document = self._load(resolved, expected_etag)
        lines = split_lines_keepends(document.text)
        total = len(lines)
        if not 1 <= start <= total + 1:
            raise InvalidArgumentError(f"start must be between 1 and {total + 1}.")
        if not start - 1 <= end <= total:
            raise InvalidArgumentError(
                f"end must be between {start - 1} (pure insertion) and {total}."
            )
        newline = document.fmt.newline
        replacement = normalize_newlines(new_text, newline)
        last_line_open = bool(lines) and not lines[-1].endswith(_LINE_ENDINGS)
        # Terminate the new text, except at the end of a file that has no final newline:
        # the edit keeps the file's final-newline convention either way.
        if (
            replacement
            and not replacement.endswith(_LINE_ENDINGS)
            and not (end == total and last_line_open)
        ):
            replacement += newline
        if start == total + 1 and last_line_open and replacement:
            lines[-1] += newline
        removed = end - start + 1
        added = len(split_lines(replacement))
        updated = "".join([*lines[: start - 1], replacement, *lines[end:]])
        return self._commit(
            resolved, document, updated, changes=max(removed, added), dry_run=dry_run
        )

    def write(
        self,
        path: str,
        content: str,
        *,
        overwrite: bool = False,
        expected_etag: str | None = None,
        dry_run: bool = False,
    ) -> EditResult:
        """Create a file (and missing parent directories), or overwrite one with its etag."""
        resolved = self._guard.resolve(path, must_exist=False)
        if resolved.path.exists():
            if not resolved.path.is_file():
                raise PathTypeError(f"Not a regular file: {path}")
            if not overwrite:
                raise AlreadyExistsError(
                    f"{resolved.display} already exists. Edit it with edit_lines or "
                    "replace_in_file, or pass overwrite=true together with expected_etag."
                )
            if expected_etag is None:
                raise InvalidArgumentError(
                    "expected_etag is required to overwrite a file; get it from file_info."
                )
            document = self._load(resolved, expected_etag)
            new_text = normalize_newlines(content, document.fmt.newline)
            return self._commit(resolved, document, new_text, changes=1, dry_run=dry_run)

        empty = _Document(text="", fmt=TextFormat("utf-8", "\n"), etag="")
        return self._commit(resolved, empty, content, changes=1, dry_run=dry_run, create=True)

    def _load(self, resolved: ResolvedPath, expected_etag: str | None) -> _Document:
        file_stat = resolved.path.stat()
        etag = compute_etag(file_stat)
        if expected_etag is not None and expected_etag != etag:
            raise StaleFileError(
                f"{resolved.display} changed since it was read (etag {expected_etag} != {etag}). "
                "Re-read the relevant lines and retry with the new etag."
            )
        check_size(resolved, file_stat.st_size, self._settings.max_file_bytes)
        fmt = require_text(resolved)
        text = resolved.path.read_bytes().decode(fmt.encoding, errors="surrogateescape")
        return _Document(text=text, fmt=fmt, etag=etag)

    def _commit(
        self,
        resolved: ResolvedPath,
        document: _Document,
        new_text: str,
        *,
        changes: int,
        dry_run: bool,
        create: bool = False,
    ) -> EditResult:
        diff = self._diff(resolved.display, document.text, new_text, create=create)
        data = new_text.encode(document.fmt.encoding, errors="surrogateescape")
        check_size(resolved, len(data), self._settings.max_file_bytes)
        if dry_run:
            return EditResult(
                path=resolved.display,
                applied=False,
                dry_run=True,
                changes=changes,
                etag=document.etag or None,
                diff=diff,
                created=False,
            )
        if create:
            if resolved.path.exists():  # appeared since the existence check
                raise AlreadyExistsError(f"{resolved.display} already exists.")
            resolved.path.parent.mkdir(parents=True, exist_ok=True)
        else:
            ensure_etag(resolved, document.etag)  # narrow the read-modify-write race
        atomic_write(resolved.path, data)
        return EditResult(
            path=resolved.display,
            applied=True,
            dry_run=False,
            changes=changes,
            etag=compute_etag(resolved.path.stat()),
            diff=diff,
            created=create,
        )

    def _diff(self, display: str, old: str, new: str, *, create: bool) -> str:
        diff = "\n".join(
            difflib.unified_diff(
                split_lines(old),
                split_lines(new),
                fromfile="/dev/null" if create else f"a/{display}",
                tofile=f"b/{display}",
                lineterm="",
            )
        )
        # Undecodable bytes are carried as lone surrogates, which cannot be serialized.
        diff = diff.encode("utf-8", errors="replace").decode("utf-8")
        text, _ = truncate_text(diff, self._settings.max_output_chars, "diff truncated")
        return text
