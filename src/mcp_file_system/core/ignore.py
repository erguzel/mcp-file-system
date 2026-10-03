"""Hierarchical ``.gitignore`` evaluation for directory walks.

Unlike the deny policy, ignore rules only hide paths from walks and searches; a
gitignored file can still be read directly by path.
"""

import os
from pathlib import Path, PurePath

from pathspec import GitIgnoreSpec

IGNORE_FILE = ".gitignore"


class IgnoreRules:
    """Evaluates the ``.gitignore`` files that apply to paths below a root.

    Rules from deeper directories override shallower ones, and a later matching
    pattern (including ``!negations``) wins, as in git.
    """

    def __init__(self, root: Path) -> None:
        self._root = root
        self._cache: dict[Path, GitIgnoreSpec | None] = {}

    def is_ignored(self, path: Path, *, is_dir: bool) -> bool:
        """Return whether ``path`` (inside the root) is ignored."""
        decision: bool | None = None
        for directory in self._ancestors(path):
            spec = self._spec_for(directory)
            if spec is None:
                continue
            relative = PurePath(os.path.relpath(path, directory)).as_posix()
            if is_dir:
                relative += "/"
            result = spec.check_file(relative)
            if result.include is not None:
                decision = result.include
        return bool(decision)

    def _ancestors(self, path: Path) -> list[Path]:
        """Directories from the root down to ``path``'s parent."""
        chain: list[Path] = []
        current = path.parent
        while True:
            chain.append(current)
            if current in {self._root, current.parent}:
                break
            current = current.parent
        chain.reverse()
        return chain

    def _spec_for(self, directory: Path) -> GitIgnoreSpec | None:
        if directory not in self._cache:
            ignore_file = directory / IGNORE_FILE
            spec: GitIgnoreSpec | None = None
            try:
                if ignore_file.is_file():
                    lines = ignore_file.read_text(encoding="utf-8", errors="replace").splitlines()
                    spec = GitIgnoreSpec.from_lines(lines)
            except OSError:
                spec = None
            self._cache[directory] = spec
        return self._cache[directory]
