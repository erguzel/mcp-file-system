"""Path confinement.

:class:`PathGuard` is the single choke point between agent-supplied path strings and the
filesystem. Every tool resolves its paths through it, which guarantees that:

* the fully resolved (symlink-free) path lies inside one of the configured roots,
* neither the requested nor the resolved path matches the deny policy,
* error messages never reveal where an escaping path would have led.

Known limitation: checks happen before I/O, so a concurrent process that swaps a path
component for a symlink between the check and the access (TOCTOU) is out of scope.
"""

import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePath, PureWindowsPath

from mcp_file_system.errors import (
    AccessDeniedError,
    InvalidArgumentError,
    NotFoundError,
    PathOutsideRootError,
    PathTypeError,
)
from mcp_file_system.security.policy import AccessPolicy

_IS_WINDOWS = os.name == "nt"


@dataclass(frozen=True, slots=True)
class ResolvedPath:
    """A path that passed every confinement check."""

    path: Path
    """Absolute, symlink-free filesystem path."""
    root: Path
    """The allowed root that contains :attr:`path`."""
    display: str
    """How the path is shown to (and accepted back from) the agent."""

    @property
    def relative(self) -> str:
        """POSIX path relative to :attr:`root` (``"."`` for the root itself)."""
        return _relative_posix(self.path, self.root)


def _has_stream_suffix(raw: str) -> bool:
    """Whether a Windows path has a colon outside its drive (an alternate data stream)."""
    pure = PureWindowsPath(raw)
    return ":" in raw[len(pure.drive) :]


def _normalize(path: Path | str) -> str:
    return os.path.normcase(os.fspath(path))


def _is_within(path: Path, root: Path) -> bool:
    candidate, base = _normalize(path), _normalize(root)
    return candidate == base or candidate.startswith(base.rstrip(os.sep) + os.sep)


def _relative_posix(path: Path, root: Path) -> str:
    if _normalize(path) == _normalize(root):
        return "."
    return PurePath(os.path.relpath(path, root)).as_posix()


class PathGuard:
    """Resolves agent-supplied paths and enforces root confinement and the deny policy."""

    def __init__(self, roots: Sequence[Path], policy: AccessPolicy) -> None:
        """Create a guard.

        Args:
            roots: Absolute, already-resolved root directories. Relative paths resolve
                against the first one.
            policy: Deny policy applied to every resolved path.
        """
        if not roots:
            raise ValueError("at least one root is required")
        # Most specific root first, so nested roots attribute paths correctly.
        self._roots = tuple(roots)
        self._roots_by_depth = sorted(self._roots, key=lambda r: len(r.parts), reverse=True)
        self._policy = policy

    @property
    def roots(self) -> tuple[Path, ...]:
        """The allowed roots, primary root first."""
        return self._roots

    @property
    def primary_root(self) -> Path:
        """The root that relative paths resolve against."""
        return self._roots[0]

    def resolve(self, raw: str, *, must_exist: bool = True) -> ResolvedPath:
        """Resolve and validate an agent-supplied path.

        Args:
            raw: Absolute path, or path relative to the primary root.
            must_exist: When false, the final components may be missing (for creating
                files); existing components are still resolved and checked.

        Raises:
            InvalidArgumentError: The path is empty or contains NUL bytes.
            NotFoundError: ``must_exist`` is set and the path does not exist.
            PathOutsideRootError: The resolved path escapes every root.
            AccessDeniedError: The path is blocked by policy or cannot be resolved.
        """
        if not raw or "\x00" in raw:
            raise InvalidArgumentError("Path must be a non-empty string without NUL bytes.")
        if _IS_WINDOWS and _has_stream_suffix(raw):
            # "file:stream" addresses an NTFS alternate data stream; "secret::$DATA"
            # would read a denied file under a name the policy does not recognize.
            raise InvalidArgumentError("Colons are only allowed in a drive prefix.")
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = self.primary_root / candidate
        try:
            real = candidate.resolve(strict=must_exist)
        except FileNotFoundError:
            # Report confinement before existence: otherwise "not found" vs "outside"
            # would reveal which paths exist outside the roots.
            if self._containing_root(self._resolve_loosely(candidate, raw)) is None:
                raise self._outside_error(raw) from None
            raise NotFoundError(f"No such file or directory: {raw}") from None
        except (OSError, RuntimeError):
            # Symlink loops, permission errors on a component, invalid names, ...
            raise AccessDeniedError(f"Cannot resolve path: {raw}") from None

        root = self._containing_root(real)
        if root is None:
            raise self._outside_error(raw)

        try:
            is_dir = real.is_dir()
        except (OSError, ValueError):
            raise AccessDeniedError(f"Cannot resolve path: {raw}") from None
        if self._policy.is_denied(_relative_posix(real, root), is_dir=is_dir):
            raise AccessDeniedError(f"Access to this path is denied by policy: {raw}")
        # Also honor the path as requested, so a symlink named like a secret stays blocked.
        lexical = Path(os.path.normpath(candidate))
        lexical_root = self._containing_root(lexical)
        if lexical_root is not None and self._policy.is_denied(
            _relative_posix(lexical, lexical_root), is_dir=is_dir
        ):
            raise AccessDeniedError(f"Access to this path is denied by policy: {raw}")

        return ResolvedPath(path=real, root=root, display=self.display(real))

    def resolve_file(self, raw: str) -> ResolvedPath:
        """Resolve a path that must be an existing regular file."""
        resolved = self.resolve(raw)
        if not resolved.path.is_file():
            raise PathTypeError(f"Not a regular file: {raw}")
        return resolved

    def resolve_dir(self, raw: str) -> ResolvedPath:
        """Resolve a path that must be an existing directory."""
        resolved = self.resolve(raw)
        if not resolved.path.is_dir():
            raise PathTypeError(f"Not a directory: {raw}")
        return resolved

    def check_entry(self, path: Path, root: Path, *, is_dir: bool) -> bool:
        """Return whether a directory-walk entry under ``root`` is visible.

        Walks pass the already-resolved parent plus an entry name, so only the deny
        policy needs checking; symlinked entries must go through :meth:`resolve`.
        """
        return not self._policy.is_denied(_relative_posix(path, root), is_dir=is_dir)

    def display(self, path: Path) -> str:
        """Render an in-root path for the agent: relative to the primary root if possible."""
        if _is_within(path, self.primary_root):
            return _relative_posix(path, self.primary_root)
        return os.fspath(path)

    def _outside_error(self, raw: str) -> PathOutsideRootError:
        return PathOutsideRootError(
            f"Path is outside the allowed roots: {raw}. "
            f"Allowed roots: {', '.join(str(r) for r in self._roots)}"
        )

    @staticmethod
    def _resolve_loosely(candidate: Path, raw: str) -> Path:
        try:
            return candidate.resolve(strict=False)
        except (OSError, RuntimeError):
            raise AccessDeniedError(f"Cannot resolve path: {raw}") from None

    def _containing_root(self, path: Path) -> Path | None:
        for root in self._roots_by_depth:
            if _is_within(path, root):
                return root
        return None
