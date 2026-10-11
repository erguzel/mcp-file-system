"""Guarded file editing.

Every edit:

* is confined by :class:`PathGuard` like any read,
* refuses to run against a stale view of the file (etag check, re-verified right
  before writing),
* preserves the file's encoding, BOM, newline style and permissions, and round-trips
  undecodable bytes unchanged (``surrogateescape``),
* is written atomically (temporary file in the same directory + :func:`os.replace`),
* returns a unified diff, and can be previewed with ``dry_run``,
* is recorded for ``undo_last_edit`` when an edit history is configured.
"""

import difflib
import os
import re
import stat
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from mcp_file_system.config import Settings
from mcp_file_system.core.etag import compute_etag, ensure_etag
from mcp_file_system.core.history import EditHistory
from mcp_file_system.core.models import (
    MAX_LISTED_FAILURES,
    EditResult,
    PatchResult,
    Replacement,
    UndoResult,
)
from mcp_file_system.core.patch import apply_hunks, parse_patch
from mcp_file_system.core.render import truncate_text
from mcp_file_system.core.textio import (
    TextFormat,
    check_size,
    encode_text,
    require_text,
    split_lines,
    split_lines_keepends,
)
from mcp_file_system.errors import (
    AccessDeniedError,
    AlreadyExistsError,
    FileTooLargeError,
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
MAX_EDITS = 50
"""Most replacements per multi_replace call."""
MAX_RENAME_ATTEMPTS = 100
"""Candidate names tried when undo renames a created file."""
_EOL = re.compile(r"\r\n|\r|\n")
_LINE_ENDINGS = ("\n", "\r")


@dataclass(frozen=True, slots=True)
class _Document:
    """A loaded file: decoded text, format, etag and the exact bytes it was read from."""

    text: str
    fmt: TextFormat
    etag: str
    raw: bytes


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


def _utc_stamp() -> str:
    """Current UTC time for soft-deleted file names, e.g. ``20261009T120000Z``."""
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _unencodable_message(display: str, encoding: str) -> str:
    """Explain why new text cannot be written, without quoting it (errors reach the audit log).

    Legacy files are read as Latin-1 when they are not valid UTF-8, so characters such as
    'ş' or '€' cannot be stored in them. UTF encodings store every character except
    unpaired surrogates, which can arrive through JSON escapes.
    """
    if encoding.startswith("utf"):
        return (
            f"The new text contains an unpaired surrogate, which {encoding} cannot store; "
            f"nothing was changed in {display}. Remove or replace that character."
        )
    return (
        f"{display} is read as {encoding}, and {encoding} cannot store some characters of "
        "the new text; nothing was changed. Use only characters this encoding supports, or "
        "convert the file to UTF-8 first."
    )


def _check_count(count: int, expected: int | None, display: str, label: str = "") -> None:
    """Reject a replacement whose match count is zero or differs from ``expected``.

    Args:
        count: Matches found.
        expected: Required number of matches; ``None`` accepts any positive count.
        display: The file as shown to the agent.
        label: Names the edit within a batch (e.g. ``"edit 3 of 5: "``).
    """
    if count == 0:
        raise NoMatchError(
            f"{label}Pattern not found in {display}. Re-read the target lines; whitespace "
            "and indentation must match exactly."
        )
    if expected is not None and count != expected:
        # A unique match is all a lite client can ask for; only full tools take a count.
        advice = (
            "Add surrounding context to make it unique."
            if expected == 1
            else "Adjust the pattern, or set expected_count to the intended number."
        )
        raise MatchCountError(
            f"{label}Pattern matches {count} time(s) in {display}, expected {expected}. {advice}"
        )


class FileEditor:
    """Implements the opt-in editing tools."""

    def __init__(
        self, guard: PathGuard, settings: Settings, history: EditHistory | None = None
    ) -> None:
        self._guard = guard
        self._settings = settings
        self._history = history

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
            raise InvalidArgumentError("expected_count must be >= 1 (or None for any count).")
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
        _check_count(count, expected_count, resolved.display)
        return self._commit(resolved, document, new_text, changes=count, dry_run=dry_run)

    def multi_replace(
        self,
        path: str,
        edits: Sequence[Replacement],
        *,
        expected_etag: str | None = None,
        dry_run: bool = False,
    ) -> EditResult:
        """Apply literal replacements in order and write once: all of them or none.

        Each edit works on the result of the previous ones and must match exactly its
        ``expected_count`` (0 = any positive count). Line terminators in patterns and
        replacements follow the file's newline style, as in :meth:`replace`. The
        growing text is checked against ``max_file_bytes`` before every edit, so a
        chain of expanding edits cannot exhaust memory before the final size check.
        """
        if not 1 <= len(edits) <= MAX_EDITS:
            raise InvalidArgumentError(f"Pass between 1 and {MAX_EDITS} edits.")
        resolved = self._guard.resolve_file(path)
        document = self._load(resolved, expected_etag)
        newline = document.fmt.newline
        limit = self._settings.max_file_bytes
        text = document.text
        changes = 0
        for number, edit in enumerate(edits, start=1):
            label = f"edit {number} of {len(edits)}: "
            literal = normalize_newlines(edit.pattern, newline)
            substitute = normalize_newlines(edit.replacement, newline)
            count = text.count(literal)
            _check_count(count, edit.expected_count or None, resolved.display, label)
            if len(text) + count * (len(substitute) - len(literal)) > limit:
                raise FileTooLargeError(
                    f"{label}the result would exceed max_file_bytes ({limit}); nothing was changed."
                )
            text = text.replace(literal, substitute)
            changes += count
        return self._commit(resolved, document, text, changes=changes, dry_run=dry_run)

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

    def apply_patch(
        self, path: str, diff: str, *, expected_etag: str, dry_run: bool = False
    ) -> PatchResult:
        """Apply a unified diff for one file, all hunks or none.

        Hunks are placed as described in :mod:`~mcp_file_system.core.patch`. If any hunk
        fails, a dry run returns the per-hunk report and a real run raises without
        writing, naming the first failed hunks.
        """
        if len(diff) > self._settings.max_file_bytes:
            raise InvalidArgumentError(
                f"The patch is larger than max_file_bytes ({self._settings.max_file_bytes})."
            )
        hunks = parse_patch(diff)
        resolved = self._guard.resolve_file(path)
        document = self._load(resolved, expected_etag)
        outcome = apply_hunks(split_lines_keepends(document.text), hunks, document.fmt.newline)
        if outcome.text is None:
            if dry_run:
                return PatchResult(
                    path=resolved.display,
                    applied=False,
                    dry_run=True,
                    changes=0,
                    etag=document.etag,
                    diff="",
                    hunks=outcome.reports,
                )
            failed = [report for report in outcome.reports if not report.applied]
            shown = failed[:MAX_LISTED_FAILURES]
            failures = "; ".join(
                f"hunk {report.index} ({report.header}): {report.reason}" for report in shown
            )
            if len(failed) > len(shown):
                failures += f" (+{len(failed) - len(shown)} more)"
            raise NoMatchError(
                f"Patch not applied to {resolved.display}; nothing was changed. {failures}. "
                "Re-read the target lines and fix those hunks, or check with dry_run=true."
            )
        result = self._commit(resolved, document, outcome.text, changes=len(hunks), dry_run=dry_run)
        return PatchResult(**result.model_dump(), hunks=outcome.reports)

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
                    "replace_in_file."
                )
            if expected_etag is None:
                raise InvalidArgumentError(
                    "expected_etag is required to overwrite a file; get it from file_info."
                )
            document = self._load(resolved, expected_etag)
            new_text = normalize_newlines(content, document.fmt.newline)
            return self._commit(resolved, document, new_text, changes=1, dry_run=dry_run)

        empty = _Document(text="", fmt=TextFormat("utf-8", "\n"), etag="", raw=b"")
        return self._commit(resolved, empty, content, changes=1, dry_run=dry_run, create=True)

    def undo_last_edit(self, path: str, *, dry_run: bool = False) -> UndoResult:
        """Restore the version before this server's latest edit of ``path``.

        Refused when the file changed since that edit (the etag recorded right after it
        no longer matches), so undo never discards someone else's work. A file the edit
        created is renamed to ``<name>.deleted-<UTC time>`` instead of being deleted.
        One level per file: the snapshot is dropped once undone.
        """
        if self._history is None:
            raise InvalidArgumentError("Undo is not enabled in this server configuration.")
        resolved = self._guard.resolve_file(path)
        snapshot = self._history.get(resolved.path)
        if snapshot is None:
            raise InvalidArgumentError(
                f"No edit of {resolved.display} by this server to undo. Undo covers the "
                "latest edit per file in this server session, one level deep."
            )
        if not snapshot.kept:
            raise InvalidArgumentError(
                f"The version of {resolved.display} before the last edit was too large to "
                "keep for undo."
            )
        current = compute_etag(resolved.path.stat())
        if current != snapshot.after_etag:
            raise StaleFileError(
                f"{resolved.display} changed since this server's last edit (etag "
                f"{snapshot.after_etag} != {current}); undo refused. Edit the file directly."
            )
        document = self._load(resolved, snapshot.after_etag)
        if snapshot.before is None:
            result = self._undo_create(resolved, document, dry_run=dry_run)
            if result.applied:
                self._history.discard(resolved.path)
            return result
        before = snapshot.before.decode(document.fmt.encoding, errors="surrogateescape")
        diff = self._diff(resolved.display, document.text, before, create=False)
        if dry_run:
            return UndoResult(
                path=resolved.display,
                applied=False,
                dry_run=True,
                changes=1,
                etag=document.etag,
                diff=diff,
            )
        ensure_etag(resolved, document.etag)
        atomic_write(resolved.path, snapshot.before)
        self._history.discard(resolved.path)
        return UndoResult(
            path=resolved.display,
            applied=True,
            dry_run=False,
            changes=1,
            etag=compute_etag(resolved.path.stat()),
            diff=diff,
        )

    def _undo_create(
        self, resolved: ResolvedPath, document: _Document, *, dry_run: bool
    ) -> UndoResult:
        """Undo a file creation by renaming the file aside (soft delete).

        The new name is checked by :class:`PathGuard` like any path, and reserved with an
        exclusive create before the rename, so an existing file is never overwritten.
        """
        diff = self._diff(resolved.display, document.text, "", create=False)
        target = self._soft_delete_target(resolved, reserve=not dry_run)
        if dry_run:
            return UndoResult(
                path=resolved.display,
                applied=False,
                dry_run=True,
                changes=1,
                etag=document.etag,
                diff=diff,
                renamed_to=target.display,
            )
        try:
            ensure_etag(resolved, document.etag)
            resolved.path.replace(target.path)
        except BaseException:
            target.path.unlink(missing_ok=True)
            raise
        return UndoResult(
            path=resolved.display,
            applied=True,
            dry_run=False,
            changes=1,
            diff=diff,
            renamed_to=target.display,
        )

    def _soft_delete_target(self, resolved: ResolvedPath, *, reserve: bool) -> ResolvedPath:
        """Pick a free ``<name>.deleted-<stamp>[-N]`` next to the file.

        With ``reserve``, the name is claimed by creating an empty placeholder
        exclusively, which the rename then replaces.
        """
        base = f"{resolved.path.name}.deleted-{_utc_stamp()}"
        for attempt in range(1, MAX_RENAME_ATTEMPTS + 1):
            name = base if attempt == 1 else f"{base}-{attempt}"
            try:
                target = self._guard.resolve(
                    os.fspath(resolved.path.with_name(name)), must_exist=False
                )
            except AccessDeniedError as exc:
                raise AccessDeniedError(
                    f"Undo would rename {resolved.display} to {name}, which the deny list "
                    "blocks; nothing was changed."
                ) from exc
            if not reserve:
                if not target.path.exists():
                    return target
                continue
            try:
                os.close(os.open(target.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
            except FileExistsError:
                continue
            return target
        raise AlreadyExistsError(
            f"Could not find a free name to rename {resolved.display} aside; nothing was changed."
        )

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
        raw = resolved.path.read_bytes()
        text = raw.decode(fmt.encoding, errors="surrogateescape")
        return _Document(text=text, fmt=fmt, etag=etag, raw=raw)

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
        try:
            data = encode_text(new_text, document.fmt, errors="surrogateescape")
        except UnicodeEncodeError as exc:
            raise InvalidArgumentError(
                _unencodable_message(resolved.display, document.fmt.encoding)
            ) from exc
        if len(data) > self._settings.max_file_bytes:
            raise FileTooLargeError(
                f"The new content of {resolved.display} would be {len(data)} bytes; the limit "
                f"is {self._settings.max_file_bytes}. Nothing was written."
            )
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
        etag = compute_etag(resolved.path.stat())
        if self._history is not None:
            self._history.record(resolved.path, None if create else document.raw, etag)
        return EditResult(
            path=resolved.display,
            applied=True,
            dry_run=False,
            changes=changes,
            etag=etag,
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
