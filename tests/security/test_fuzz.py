"""Property-based checks: no agent-supplied path string may escape the root."""

from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from conftest import symlink_or_skip, write
from mcp_file_system.config import DEFAULT_DENY_GLOBS
from mcp_file_system.errors import FsError
from mcp_file_system.security import AccessPolicy, PathGuard
from mcp_file_system.security.paths import _is_within

SEGMENTS = st.sampled_from(
    [
        "..",
        ".",
        "",
        "a",
        "b",
        "src",
        ".env",
        ".git",
        "~",
        "outside",
        "secret.txt",
        "x" * 3,
        "%2e%2e",
    ]
)
SEPARATORS = st.sampled_from(["/", "\\", "//"])


@st.composite
def path_strings(draw: st.DrawFn) -> str:
    parts = draw(st.lists(SEGMENTS, min_size=1, max_size=8))
    separator = draw(SEPARATORS)
    prefix = draw(st.sampled_from(["", "/", "./", "../"]))
    return prefix + separator.join(parts)


@pytest.fixture
def fuzz_tree(tmp_path: Path) -> tuple[Path, PathGuard]:
    """A small workspace next to an ``outside`` directory that must stay unreachable."""
    root = (tmp_path / "root").resolve()
    for relative in ("a/b", "src", "outside_link_target"):
        (root / relative).mkdir(parents=True)
    write(root / "a" / "file.txt", "x")
    write(root / ".env", "SECRET=1")
    write(tmp_path / "outside" / "secret.txt", "secret")
    return root, PathGuard([root], AccessPolicy(DEFAULT_DENY_GLOBS))


@pytest.fixture
def linked_tree(fuzz_tree: tuple[Path, PathGuard]) -> tuple[Path, PathGuard]:
    """The same workspace with ``root/outside`` symlinked to the outside directory."""
    root, _ = fuzz_tree
    symlink_or_skip(root / "outside", root.parent / "outside")
    return fuzz_tree


def _assert_confined(tree: tuple[Path, PathGuard], raw: str, must_exist: bool) -> None:
    """Resolving ``raw`` either fails with a domain error or stays inside the root."""
    root, guard = tree
    try:
        resolved = guard.resolve(raw, must_exist=must_exist)
    except FsError:
        return
    assert _is_within(resolved.path, root)
    assert resolved.path.resolve() == resolved.path or not resolved.path.exists()
    assert resolved.relative.split("/")[0] != ".."


@settings(
    max_examples=400, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(raw=path_strings(), must_exist=st.booleans())
def test_resolved_paths_never_escape_root(
    fuzz_tree: tuple[Path, PathGuard], raw: str, must_exist: bool
) -> None:
    _assert_confined(fuzz_tree, raw, must_exist)


@settings(
    max_examples=400, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(raw=path_strings(), must_exist=st.booleans())
def test_symlink_to_outside_never_escapes_root(
    linked_tree: tuple[Path, PathGuard], raw: str, must_exist: bool
) -> None:
    _assert_confined(linked_tree, raw, must_exist)


@settings(
    max_examples=200, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(raw=st.text(min_size=1, max_size=40))
def test_arbitrary_text_never_crashes_or_escapes(
    fuzz_tree: tuple[Path, PathGuard], raw: str
) -> None:
    _assert_confined(fuzz_tree, raw, must_exist=False)
