"""Access policy: which paths inside a root are off-limits regardless of how they are reached."""

from collections.abc import Iterable

from pathspec import GitIgnoreSpec


class AccessPolicy:
    """Blocks paths matching gitignore-style deny patterns.

    Patterns follow ``.gitignore`` semantics: a pattern without a slash matches at any
    depth, a trailing slash matches a directory and everything below it, and ``!``
    re-allows a previously denied path.

    Matching is case-insensitive. On case-insensitive filesystems (the macOS and
    Windows defaults) ``.ENV`` opens the same file as ``.env``, and path resolution
    does not canonicalize case, so case-sensitive matching would be bypassable.
    Over-blocking a differently-cased name on a case-sensitive filesystem is the
    safe trade-off.
    """

    def __init__(self, deny_globs: Iterable[str]) -> None:
        self._spec = GitIgnoreSpec.from_lines([glob.lower() for glob in deny_globs])

    def is_denied(self, relative: str, *, is_dir: bool) -> bool:
        """Return whether ``relative`` (a POSIX path relative to its root) is blocked.

        Args:
            relative: Root-relative POSIX path. The root itself (``"."``) is never denied.
            is_dir: Whether the path is a directory, so directory-only patterns apply.
        """
        if relative in {"", "."}:
            return False
        folded = relative.lower()
        if self._spec.match_file(folded):
            return True
        return is_dir and self._spec.match_file(folded + "/")
