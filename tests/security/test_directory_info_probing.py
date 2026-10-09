"""file_info on a directory must not reveal denied entries (SECURITY.md, threat 3)."""

from pathlib import Path

import pytest

from conftest import symlink_or_skip, write
from mcp_file_system.context import AppContext


def denied_entries(directory: Path) -> None:
    write(directory / ".env", "SECRET=1\n")
    write(directory / "server.pem", "key\n")
    (directory / ".git").mkdir()


def test_denied_entries_are_not_counted(ctx: AppContext, root: Path) -> None:
    write(root / "d" / "visible.txt", "x")
    before = ctx.reader.info("d")
    denied_entries(root / "d")
    after = ctx.reader.info("d")
    assert before.entry_count == after.entry_count == 1
    assert before.render() == after.render()


def test_directory_with_only_denied_entries_looks_empty(ctx: AppContext, root: Path) -> None:
    denied_entries(root / "d")
    assert ctx.reader.info("d").entry_count == 0
    assert ctx.finder.list_directory("d", include_hidden=True).entries == []


def test_symlink_leaving_the_roots_is_not_counted(
    ctx: AppContext, root: Path, outside: Path
) -> None:
    write(outside / "secret.txt", "x")
    (root / "d").mkdir()
    symlink_or_skip(root / "d" / "link", outside / "secret.txt")
    assert ctx.reader.info("d").entry_count == 0


@pytest.mark.parametrize("path", ["d", "."])
def test_directory_metadata_is_withheld(ctx: AppContext, root: Path, path: str) -> None:
    # A directory's size grows with its entries on some filesystems (e.g. APFS), and its
    # modification time changes when a denied entry is created or removed.
    denied_entries(root / "d")
    info = ctx.reader.info(path)
    assert (info.size_bytes, info.modified) == (None, None)
    assert "modified" not in info.render()
