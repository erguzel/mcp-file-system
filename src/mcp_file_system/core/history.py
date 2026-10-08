"""Edit history for undo: the version of each file before this server's latest edit.

Undo is a cheap safety net for agents that edit badly, so it stays deliberately small:
one level per file (no redo), in memory only (a restart forgets it), and bounded in
both file count and bytes. A snapshot that alone exceeds the byte budget is not kept,
but its path is remembered so undo can say why it is unavailable.

Scope: one history per server process, like the read tracker; a future multi-session
HTTP transport must key histories by session.
"""

import os
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

MAX_UNDO_FILES = 100
MAX_UNDO_BYTES = 64 * 1024 * 1024
"""Total bytes of kept versions; without it, 100 files at max_file_bytes could take 1 GB."""


@dataclass(frozen=True, slots=True)
class Snapshot:
    """What undo needs to restore one file.

    Attributes:
        before: The exact bytes before the edit; ``None`` if the edit created the file.
        after_etag: The file's etag right after the edit; undo requires it unchanged.
        kept: False if the previous version was too large to keep (``before`` is then
            ``None`` as well).
    """

    before: bytes | None
    after_etag: str
    kept: bool = True

    @property
    def size(self) -> int:
        """Bytes this snapshot holds."""
        return len(self.before) if self.before is not None else 0


class EditHistory:
    """The latest :class:`Snapshot` per file, least recently edited dropped first.

    Thread-safe, because tools run in worker threads.
    """

    def __init__(self, max_files: int = MAX_UNDO_FILES, max_bytes: int = MAX_UNDO_BYTES) -> None:
        self._max_files = max_files
        self._max_bytes = max_bytes
        self._lock = threading.Lock()
        self._snapshots: OrderedDict[str, Snapshot] = OrderedDict()
        self._bytes = 0

    def record(self, path: Path, before: bytes | None, after_etag: str) -> None:
        """Remember the version before an edit, replacing the file's older snapshot."""
        snapshot = Snapshot(before=before, after_etag=after_etag)
        if snapshot.size > self._max_bytes:
            snapshot = Snapshot(before=None, after_etag=after_etag, kept=False)
        key = os.fspath(path)
        with self._lock:
            self._drop(key)
            self._snapshots[key] = snapshot
            self._bytes += snapshot.size
            while len(self._snapshots) > self._max_files or self._bytes > self._max_bytes:
                self._drop(next(iter(self._snapshots)))

    def get(self, path: Path) -> Snapshot | None:
        """The file's latest snapshot, if any."""
        with self._lock:
            return self._snapshots.get(os.fspath(path))

    def discard(self, path: Path) -> None:
        """Forget the file's snapshot (after it was undone)."""
        with self._lock:
            self._drop(os.fspath(path))

    def _drop(self, key: str) -> None:
        snapshot = self._snapshots.pop(key, None)
        if snapshot is not None:
            self._bytes -= snapshot.size
