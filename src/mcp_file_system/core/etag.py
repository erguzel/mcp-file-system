"""Cheap change detection for optimistic concurrency.

An etag identifies one observed version of a file. Readers return it; editors require
it back and refuse to write if the file changed in between, which protects edits that
were planned from a partial (and possibly stale) view of the file.
"""

import os

from mcp_file_system.errors import StaleFileError
from mcp_file_system.security.paths import ResolvedPath


def compute_etag(stat: os.stat_result) -> str:
    """Derive an etag from modification time (ns) and size; no file content is read."""
    return f"{stat.st_mtime_ns:x}-{stat.st_size:x}"


def ensure_etag(resolved: ResolvedPath, expected: str) -> None:
    """Raise :class:`StaleFileError` unless the file's current etag equals ``expected``."""
    current = compute_etag(resolved.path.stat())
    if current != expected:
        raise StaleFileError(
            f"{resolved.display} changed since it was read (etag {expected} != {current}). "
            "Re-read the relevant lines and retry with the new etag."
        )
