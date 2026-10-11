"""Policy-aware directory walking, globbing and listings."""

import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import Literal

from pathspec import GitIgnoreSpec

from mcp_file_system.config import Settings
from mcp_file_system.core.ignore import IgnoreRules
from mcp_file_system.core.models import DirectoryListing, DirEntry, FileList
from mcp_file_system.errors import FsError, InvalidArgumentError
from mcp_file_system.security.paths import PathGuard, ResolvedPath

EntryKind = Literal["file", "directory", "any"]

MAX_WALK_DEPTH = 64
"""Hard cap on walk depth, bounding recursion and runaway traversals."""


@dataclass(frozen=True, slots=True)
class WalkEntry:
    """A visible entry produced by :class:`Walker`."""

    target: ResolvedPath
    """The entry as a fully checked path (symlinks and junctions resolve to their target)."""
    relative: str
    """POSIX path relative to the walk's start directory."""
    is_dir: bool
    depth: int
    """1 for immediate children of the start directory."""
    is_symlink: bool = False
    """A symlink or Windows junction; such directories are listed but never walked."""


class GlobMatcher:
    """Matches start-relative paths against one gitignore-style glob.

    A glob without a slash matches at any depth (``*.py``); a glob with a slash is
    anchored to the start directory (``src/**/*.py``). The glob is agent input that
    pathspec compiles into a stdlib regex, so its length is capped like a regex pattern
    (``max_length``, the ``max_pattern_length`` setting); there is no deadline on matching.
    """

    def __init__(self, pattern: str, *, max_length: int) -> None:
        if not pattern.strip():
            raise InvalidArgumentError("glob must not be empty.")
        if pattern.startswith("!"):
            raise InvalidArgumentError("glob must not start with '!'.")
        if len(pattern) > max_length:
            raise InvalidArgumentError(
                f"glob is {len(pattern)} characters; at most {max_length} characters are "
                "accepted (max_pattern_length)."
            )
        self._spec = GitIgnoreSpec.from_lines([pattern])

    def matches(self, relative: str, *, is_dir: bool = False) -> bool:
        """Return whether ``relative`` matches."""
        if is_dir:
            return self._spec.match_file(relative + "/") or self._spec.match_file(relative)
        return self._spec.match_file(relative)


class Walker:
    """Depth-first, sorted walk that honors the deny policy, hidden files and .gitignore.

    Symlinks and Windows junctions are followed only if their target passes
    :class:`PathGuard`; linked directories are listed but never descended into (no loops,
    no escapes). Junctions need their own check: ``os.DirEntry`` reports them as plain
    directories, not as symlinks.
    """

    def __init__(self, guard: PathGuard, *, respect_gitignore: bool) -> None:
        self._guard = guard
        self._respect_gitignore = respect_gitignore

    def walk(
        self,
        start: ResolvedPath,
        *,
        max_depth: int | None = None,
        include_hidden: bool = False,
    ) -> Iterator[WalkEntry]:
        """Yield visible entries below ``start`` in pre-order (parents before children)."""
        ignore = IgnoreRules(start.root) if self._respect_gitignore else None
        limit = min(max_depth or MAX_WALK_DEPTH, MAX_WALK_DEPTH)
        yield from self._walk_dir(start.path, start, 1, limit, include_hidden, ignore)

    def _walk_dir(
        self,
        directory: Path,
        start: ResolvedPath,
        depth: int,
        limit: int,
        include_hidden: bool,
        ignore: IgnoreRules | None,
    ) -> Iterator[WalkEntry]:
        for entry in self._visible_children(directory, start, depth, include_hidden, ignore):
            yield entry
            if entry.is_dir and not entry.is_symlink and depth < limit:
                yield from self._walk_dir(
                    entry.target.path, start, depth + 1, limit, include_hidden, ignore
                )

    def _visible_children(
        self,
        directory: Path,
        start: ResolvedPath,
        depth: int,
        include_hidden: bool,
        ignore: IgnoreRules | None,
    ) -> list[WalkEntry]:
        try:
            with os.scandir(directory) as iterator:
                raw = sorted(iterator, key=lambda e: e.name)
        except OSError:
            return []
        visible: list[WalkEntry] = []
        for item in raw:
            if not include_hidden and item.name.startswith("."):
                continue
            entry = self._make_entry(item, start, depth)
            if entry is None:
                continue
            if ignore is not None and ignore.is_ignored(Path(item.path), is_dir=entry.is_dir):
                continue
            visible.append(entry)
        return visible

    def _make_entry(
        self, item: os.DirEntry[str], start: ResolvedPath, depth: int
    ) -> WalkEntry | None:
        path = Path(item.path)
        relative = PurePath(os.path.relpath(path, start.path)).as_posix()
        try:
            if item.is_symlink() or item.is_junction():
                target = self._guard.resolve(os.fspath(path))
                target_is_dir = target.path.is_dir()
                if not target_is_dir and not target.path.is_file():
                    return None  # links to sockets, FIFOs, devices
                return WalkEntry(target, relative, target_is_dir, depth, is_symlink=True)
            is_dir = item.is_dir(follow_symlinks=False)
            if not is_dir and not item.is_file(follow_symlinks=False):
                return None  # sockets, FIFOs, devices
        except (FsError, OSError):
            return None
        if not self._guard.check_entry(path, start.root, is_dir=is_dir):
            return None
        resolved = ResolvedPath(path=path, root=start.root, display=self._guard.display(path))
        return WalkEntry(resolved, relative, is_dir, depth)


class FileFinder:
    """Glob search and tree listings built on :class:`Walker`."""

    def __init__(self, guard: PathGuard, walker: Walker, settings: Settings) -> None:
        self._guard = guard
        self._walker = walker
        self._settings = settings

    def _matcher(self, glob: str) -> GlobMatcher:
        return GlobMatcher(glob, max_length=self._settings.max_pattern_length)

    def iter_files(
        self, path: str, *, glob: str | None = None, include_hidden: bool = False
    ) -> Iterator[ResolvedPath]:
        """Yield the files to search: ``path`` itself if it is a file, else a filtered walk."""
        start = self._guard.resolve(path)
        if start.path.is_file():
            yield start
            return
        start = self._guard.resolve_dir(path)
        matcher = self._matcher(glob) if glob else None
        for entry in self._walker.walk(start, include_hidden=include_hidden):
            if entry.is_dir:
                continue
            if matcher is None or matcher.matches(entry.relative):
                yield entry.target

    def find(
        self,
        glob: str,
        path: str = ".",
        *,
        kind: EntryKind = "file",
        max_depth: int | None = None,
        include_hidden: bool = False,
        max_results: int | None = None,
    ) -> FileList:
        """Return paths below ``path`` that match ``glob``."""
        if max_depth is not None and max_depth < 1:
            raise InvalidArgumentError("max_depth must be >= 1.")
        limit = min(max_results or self._settings.max_results, self._settings.max_results)
        start = self._guard.resolve_dir(path)
        matcher = self._matcher(glob)
        found: list[str] = []
        truncated = False
        for entry in self._walker.walk(start, max_depth=max_depth, include_hidden=include_hidden):
            if (kind == "file" and entry.is_dir) or (kind == "directory" and not entry.is_dir):
                continue
            if not matcher.matches(entry.relative, is_dir=entry.is_dir):
                continue
            if len(found) >= limit:
                truncated = True
                break
            found.append(entry.target.display + ("/" if entry.is_dir else ""))
        return FileList(pattern=glob, base=start.display, paths=found, truncated=truncated)

    def list_directory(
        self, path: str = ".", *, depth: int = 1, include_hidden: bool = False
    ) -> DirectoryListing:
        """Return a depth-limited tree of ``path``."""
        if depth < 1:
            raise InvalidArgumentError("depth must be >= 1.")
        start = self._guard.resolve_dir(path)
        entries: list[DirEntry] = []
        truncated = False
        for entry in self._walker.walk(start, max_depth=depth, include_hidden=include_hidden):
            if len(entries) >= self._settings.max_results:
                truncated = True
                break
            size = None
            if not entry.is_dir:
                try:
                    size = entry.target.path.stat().st_size
                except OSError:
                    size = None
            entries.append(
                DirEntry(
                    path=entry.target.display,
                    kind="directory" if entry.is_dir else "file",
                    depth=entry.depth,
                    size_bytes=size,
                )
            )
        return DirectoryListing(
            path=start.display, depth=depth, entries=entries, truncated=truncated
        )
