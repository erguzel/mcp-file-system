"""Session read tracking: which lines of which file version were already returned.

Agents often re-read content that is still in their context. The server cannot see that
context, but it knows what it sent. Reads use this to add an "already returned" note,
and ``if_changed_since`` uses it to answer "unchanged" without resending content, but
only for lines that really were sent, so a misused parameter never leaves a model
without content.

Scope: one tracker per server process. Over stdio a process serves exactly one client
session; a future multi-session HTTP transport must key trackers by session.
"""

import os
import threading
from collections import OrderedDict
from pathlib import Path

from mcp_file_system.core.models import LinesResult

MAX_TRACKED_FILES = 1000

Interval = tuple[int, int]

ALREADY_RETURNED = "These lines were already returned earlier in this session; file unchanged."
NOT_RETURNED_YET = (
    "The etag matches, but these lines were not returned earlier in this session, so they "
    "are included."
)
UNCHANGED = "Unchanged since your earlier read of these lines; reuse them."


class ReadTracker:
    """Remembers the served line intervals per file, for the latest seen version only.

    Thread-safe; bounded to :data:`MAX_TRACKED_FILES` files (least recently used are
    dropped). A new etag for a file discards the intervals of the old version.
    """

    def __init__(self, max_files: int = MAX_TRACKED_FILES) -> None:
        self._max_files = max_files
        self._lock = threading.Lock()
        self._served: OrderedDict[str, tuple[str, list[Interval]]] = OrderedDict()

    def record(self, path: Path, etag: str, start: int, end: int) -> None:
        """Remember that lines ``start..end`` of version ``etag`` were returned."""
        if start < 1 or end < start:
            return
        key = os.fspath(path)
        with self._lock:
            known_etag, intervals = self._served.get(key, (etag, []))
            if known_etag != etag:
                intervals = []
            self._served[key] = (etag, _merge([*intervals, (start, end)]))
            self._served.move_to_end(key)
            while len(self._served) > self._max_files:
                self._served.popitem(last=False)

    def overlap(self, path: Path, etag: str, start: int, end: int) -> list[Interval]:
        """Parts of ``start..end`` already returned for version ``etag``."""
        with self._lock:
            known_etag, intervals = self._served.get(os.fspath(path), ("", []))
        if known_etag != etag:
            return []
        return [
            (max(start, low), min(end, high))
            for low, high in intervals
            if low <= end and high >= start
        ]

    def covers(self, path: Path, etag: str, start: int, end: int) -> bool:
        """Whether every line in ``start..end`` was returned for version ``etag``."""
        return self.overlap(path, etag, start, end) == [(start, end)]


def _merge(intervals: list[Interval]) -> list[Interval]:
    """Merge overlapping and adjacent intervals."""
    merged: list[Interval] = []
    for low, high in sorted(intervals):
        if merged and low <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], high))
        else:
            merged.append((low, high))
    return merged


def join_notes(*notes: str | None) -> str | None:
    """Combine optional notes into one."""
    present = [note for note in notes if note]
    return " | ".join(present) if present else None


def track_result(
    tracker: ReadTracker,
    path: Path,
    result: LinesResult,
    *,
    if_changed_since: str = "",
) -> LinesResult:
    """Annotate a freshly built read with what was already sent, then record it."""
    note: str | None = None
    if result.end_line:
        overlap = tracker.overlap(path, result.etag, result.start_line, result.end_line)
        if overlap == [(result.start_line, result.end_line)]:
            note = ALREADY_RETURNED
        elif overlap:
            ranges = ", ".join(
                f"{low}-{high}" if low != high else str(low) for low, high in overlap
            )
            note = f"Lines {ranges} were already returned earlier in this session; file unchanged."
        tracker.record(path, result.etag, result.start_line, result.end_line)
    if if_changed_since:
        if if_changed_since != result.etag:
            note = join_notes(f"The file changed since etag {if_changed_since}.", note)
        elif note != ALREADY_RETURNED:
            note = join_notes(NOT_RETURNED_YET, note)
    if note is None:
        return result
    return result.model_copy(update={"note": join_notes(result.note, note)})


def unchanged_result(
    tracker: ReadTracker,
    path: Path,
    *,
    display: str,
    etag: str,
    if_changed_since: str,
    start: int,
    end: int,
    total: int,
) -> LinesResult | None:
    """A content-free answer if ``start..end`` was already sent at ``if_changed_since``."""
    if not if_changed_since or if_changed_since != etag:
        return None
    if not tracker.covers(path, etag, start, end):
        return None
    return LinesResult(
        path=display,
        start_line=start,
        end_line=end,
        total_lines=total,
        etag=etag,
        content="",
        unchanged=True,
        note=UNCHANGED,
    )
