"""Property-based checks: no agent-supplied path string may escape the root."""

from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

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


def _build_tree(tmp: Path) -> tuple[Path, PathGuard]:
    root = (tmp / "root").resolve()
    for relative in ("a/b", "src", "outside_link_target"):
        (root / relative).mkdir(parents=True, exist_ok=True)
    (root / "a" / "file.txt").write_text("x")
    (root / ".env").write_text("SECRET=1")
    out = tmp / "outside"
    out.mkdir(exist_ok=True)
    (out / "secret.txt").write_text("secret")
    try:
        link = root / "outside"
        if not link.exists():
            link.symlink_to(out, target_is_directory=True)
    except (OSError, NotImplementedError):  # pragma: no cover - Windows without privilege
        pass
    return root, PathGuard([root], AccessPolicy(DEFAULT_DENY_GLOBS))


@settings(
    max_examples=400, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(raw=path_strings(), must_exist=st.booleans())
def test_resolved_paths_never_escape_root(tmp_path: Path, raw: str, must_exist: bool) -> None:
    root, guard = _build_tree(tmp_path)
    try:
        resolved = guard.resolve(raw, must_exist=must_exist)
    except FsError:
        return
    assert _is_within(resolved.path, root)
    assert resolved.path.resolve() == resolved.path or not resolved.path.exists()
    assert resolved.relative.split("/")[0] != ".."


@settings(
    max_examples=200, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(raw=st.text(min_size=1, max_size=40))
def test_arbitrary_text_never_crashes_or_escapes(tmp_path: Path, raw: str) -> None:
    root, guard = _build_tree(tmp_path)
    try:
        resolved = guard.resolve(raw, must_exist=False)
    except FsError:
        return
    assert _is_within(resolved.path, root)
