import os
import sys
from pathlib import Path

import pytest

from conftest import symlink_or_skip, write
from mcp_file_system.config import DEFAULT_DENY_GLOBS
from mcp_file_system.errors import (
    AccessDeniedError,
    InvalidArgumentError,
    NotFoundError,
    PathOutsideRootError,
    PathTypeError,
)
from mcp_file_system.security import AccessPolicy, PathGuard


@pytest.fixture
def guard(root: Path) -> PathGuard:
    return PathGuard([root], AccessPolicy(DEFAULT_DENY_GLOBS))


def test_relative_path_resolves_against_primary_root(guard: PathGuard, root: Path) -> None:
    write(root / "src" / "a.py", "x = 1\n")
    resolved = guard.resolve("src/a.py")
    assert resolved.path == root / "src" / "a.py"
    assert resolved.root == root
    assert resolved.display == "src/a.py"
    assert resolved.relative == "src/a.py"


def test_root_itself_displays_as_dot(guard: PathGuard, root: Path) -> None:
    resolved = guard.resolve(".")
    assert resolved.path == root
    assert resolved.display == "."
    assert resolved.relative == "."


def test_absolute_path_inside_root_is_allowed(guard: PathGuard, root: Path) -> None:
    write(root / "a.txt", "a")
    assert guard.resolve(str(root / "a.txt")).display == "a.txt"


@pytest.mark.parametrize("raw", ["..", "../outside/secret.txt", "src/../../outside", "./../x"])
def test_parent_traversal_is_rejected(
    guard: PathGuard, root: Path, outside: Path, raw: str
) -> None:
    (root / "src").mkdir()
    with pytest.raises(PathOutsideRootError):
        guard.resolve(raw)


@pytest.mark.parametrize("name", ["secret.txt", "does-not-exist.txt"])
def test_no_existence_oracle_outside_root(guard: PathGuard, outside: Path, name: str) -> None:
    # Existing and missing files outside the root must be indistinguishable.
    with pytest.raises(PathOutsideRootError):
        guard.resolve(f"../outside/{name}")
    with pytest.raises(PathOutsideRootError):
        guard.resolve(str(outside / name))


def test_traversal_to_existing_outside_file_is_outside_root(
    guard: PathGuard, outside: Path
) -> None:
    with pytest.raises(PathOutsideRootError) as exc_info:
        guard.resolve("../outside/secret.txt")
    # The message echoes the request but never where it would have led.
    assert "top secret" not in str(exc_info.value)


def test_absolute_path_outside_root_is_rejected(guard: PathGuard, outside: Path) -> None:
    with pytest.raises(PathOutsideRootError):
        guard.resolve(str(outside / "secret.txt"))


def test_traversal_that_returns_inside_root_is_allowed(guard: PathGuard, root: Path) -> None:
    write(root / "a.txt", "a")
    assert guard.resolve(f"../{root.name}/a.txt").path == root / "a.txt"


@pytest.mark.parametrize("raw", ["", "a\x00b"])
def test_empty_or_nul_paths_are_invalid(guard: PathGuard, raw: str) -> None:
    with pytest.raises(InvalidArgumentError):
        guard.resolve(raw)


def test_missing_path_raises_not_found(guard: PathGuard) -> None:
    with pytest.raises(NotFoundError):
        guard.resolve("nope.txt")


def test_must_exist_false_allows_new_nested_paths(guard: PathGuard, root: Path) -> None:
    resolved = guard.resolve("new/dir/file.txt", must_exist=False)
    assert resolved.path == root / "new" / "dir" / "file.txt"


def test_must_exist_false_still_confines(guard: PathGuard) -> None:
    with pytest.raises(PathOutsideRootError):
        guard.resolve("new/../../escape.txt", must_exist=False)


def test_symlink_to_outside_file_is_rejected(guard: PathGuard, root: Path, outside: Path) -> None:
    symlink_or_skip(root / "link.txt", outside / "secret.txt")
    with pytest.raises(PathOutsideRootError):
        guard.resolve("link.txt")


def test_symlinked_directory_escape_is_rejected(
    guard: PathGuard, root: Path, outside: Path
) -> None:
    symlink_or_skip(root / "linkdir", outside)
    with pytest.raises(PathOutsideRootError):
        guard.resolve("linkdir/secret.txt")


def test_new_file_under_escaping_symlink_dir_is_rejected(
    guard: PathGuard, root: Path, outside: Path
) -> None:
    symlink_or_skip(root / "linkdir", outside)
    with pytest.raises(PathOutsideRootError):
        guard.resolve("linkdir/new.txt", must_exist=False)


def test_symlink_inside_root_resolves_to_target(guard: PathGuard, root: Path) -> None:
    write(root / "real.txt", "x")
    symlink_or_skip(root / "alias.txt", root / "real.txt")
    resolved = guard.resolve("alias.txt")
    assert resolved.path == root / "real.txt"
    assert resolved.display == "real.txt"


def test_symlink_pointing_at_secret_is_denied(guard: PathGuard, root: Path) -> None:
    write(root / ".env", "TOKEN=1")
    symlink_or_skip(root / "notes.txt", root / ".env")
    with pytest.raises(AccessDeniedError):
        guard.resolve("notes.txt")


def test_symlink_named_like_secret_is_denied(guard: PathGuard, root: Path) -> None:
    write(root / "harmless.txt", "x")
    symlink_or_skip(root / ".env", root / "harmless.txt")
    with pytest.raises(AccessDeniedError):
        guard.resolve(".env")


def test_symlink_loop_is_denied(guard: PathGuard, root: Path) -> None:
    symlink_or_skip(root / "a", root / "b")
    symlink_or_skip(root / "b", root / "a")
    with pytest.raises((AccessDeniedError, NotFoundError)):
        guard.resolve("a")


@pytest.mark.parametrize(
    "relative",
    [
        ".env",
        "sub/.env",
        ".env.local",
        ".git/config",
        "server.pem",
        "deploy/id_rsa",
        ".ssh/known_hosts",
        ".aws/credentials",
        ".npmrc",
    ],
)
def test_default_deny_list_blocks_secrets(guard: PathGuard, root: Path, relative: str) -> None:
    write(root / relative, "secret")
    with pytest.raises(AccessDeniedError):
        guard.resolve(relative)


@pytest.mark.parametrize("variant", [".ENV", ".Env", "SERVER.PEM", ".GIT/config", "Id_Rsa"])
def test_deny_list_is_case_insensitive(guard: PathGuard, root: Path, variant: str) -> None:
    # On case-insensitive filesystems these open the real secret; resolution keeps the case.
    write(root / variant, "secret")
    with pytest.raises(AccessDeniedError):
        guard.resolve(variant)


def test_denied_directory_itself_is_blocked(guard: PathGuard, root: Path) -> None:
    (root / ".git").mkdir()
    with pytest.raises(AccessDeniedError):
        guard.resolve(".git")


@pytest.mark.parametrize("relative", [".env.example", ".env.sample", ".gitignore", "envfile"])
def test_templates_and_lookalikes_are_allowed(guard: PathGuard, root: Path, relative: str) -> None:
    write(root / relative, "ok")
    assert guard.resolve(relative).display == relative


def test_new_file_matching_deny_list_is_blocked(guard: PathGuard) -> None:
    with pytest.raises(AccessDeniedError):
        guard.resolve("config/.env", must_exist=False)


def test_resolve_file_and_dir_check_types(guard: PathGuard, root: Path) -> None:
    write(root / "f.txt", "x")
    (root / "d").mkdir()
    assert guard.resolve_file("f.txt").display == "f.txt"
    assert guard.resolve_dir("d").display == "d"
    with pytest.raises(PathTypeError):
        guard.resolve_file("d")
    with pytest.raises(PathTypeError):
        guard.resolve_dir("f.txt")


def test_multiple_roots_and_display(root: Path, outside: Path) -> None:
    guard = PathGuard([root, outside], AccessPolicy(()))
    resolved = guard.resolve(str(outside / "secret.txt"))
    assert resolved.root == outside
    # Paths outside the primary root are shown absolute (they are inside an allowed root).
    assert resolved.display == os.fspath(outside / "secret.txt")
    assert guard.roots == (root, outside)


def test_nested_roots_attribute_to_most_specific(root: Path) -> None:
    inner = root / "inner"
    write(inner / "x.txt", "x")
    guard = PathGuard([root, inner], AccessPolicy(()))
    assert guard.resolve("inner/x.txt").root == inner


def test_guard_requires_a_root() -> None:
    with pytest.raises(ValueError, match="at least one root"):
        PathGuard([], AccessPolicy(()))


def test_check_entry_applies_policy(guard: PathGuard, root: Path) -> None:
    assert guard.check_entry(root / "a.py", root, is_dir=False)
    assert not guard.check_entry(root / ".git", root, is_dir=True)


def test_stream_suffix_detection() -> None:
    from mcp_file_system.security.paths import _has_stream_suffix

    assert _has_stream_suffix(".env::$DATA")
    assert _has_stream_suffix("C:\\repo\\file.txt:hidden")
    assert not _has_stream_suffix("C:\\repo\\file.txt")
    assert not _has_stream_suffix("src/file.txt")


@pytest.mark.skipif(sys.platform != "win32", reason="NTFS alternate data streams")
def test_alternate_data_streams_are_rejected(
    guard: PathGuard, root: Path
) -> None:  # pragma: no cover
    write(root / ".env", "SECRET=1")
    for raw in [".env::$DATA", "notes.txt:hidden", f"{root}\\.env::$DATA"]:
        with pytest.raises(InvalidArgumentError):
            guard.resolve(raw)


@pytest.mark.skipif(sys.platform != "win32", reason="drive semantics are Windows-only")
def test_other_drive_is_rejected_on_windows(
    guard: PathGuard, root: Path
) -> None:  # pragma: no cover
    other = "D:\\" if root.drive.upper() != "D:" else "E:\\"
    with pytest.raises((PathOutsideRootError, NotFoundError, AccessDeniedError)):
        guard.resolve(other)
