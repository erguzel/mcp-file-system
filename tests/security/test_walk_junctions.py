"""Walks must treat Windows junctions like symlinks (SECURITY.md, symlink/junction escapes)."""

import os
from pathlib import Path

from conftest import junction_or_skip, write
from mcp_file_system.context import AppContext


def test_junction_is_not_reported_as_a_symlink(root: Path, outside: Path) -> None:
    # The premise of the fix: os.DirEntry sees a junction as a plain directory unless
    # is_junction() is asked, so an is_symlink() check alone misses it.
    junction_or_skip(root / "j", outside)
    (entry,) = os.scandir(root)
    assert not entry.is_symlink()
    assert entry.is_dir(follow_symlinks=False)
    assert entry.is_junction()


def test_list_directory_drops_junction_leaving_the_roots(
    ctx: AppContext, root: Path, outside: Path
) -> None:
    junction_or_skip(root / "j", outside)
    assert ctx.finder.list_directory(".", depth=3).entries == []


def test_find_files_drops_junction_leaving_the_roots(
    ctx: AppContext, root: Path, outside: Path
) -> None:
    junction_or_skip(root / "j", outside)
    assert ctx.finder.find("*.txt").paths == []


def test_grep_does_not_read_through_junction_leaving_the_roots(
    ctx: AppContext, root: Path, outside: Path
) -> None:
    junction_or_skip(root / "j", outside)
    assert ctx.searcher.grep("top secret").hits == []


def test_file_info_does_not_count_junction_leaving_the_roots(
    ctx: AppContext, root: Path, outside: Path
) -> None:
    junction_or_skip(root / "j", outside)
    assert ctx.reader.info(".").entry_count == 0


def test_junction_to_the_root_is_not_descended(ctx: AppContext, root: Path) -> None:
    write(root / "real" / "data.txt", "d")
    junction_or_skip(root / "loop", root)
    paths = [e.path for e in ctx.finder.list_directory(".", depth=5).entries]
    assert not [p for p in paths if p.startswith("loop/")]
    assert paths.count("real/data.txt") == 1


def test_internal_junction_is_listed_by_target(ctx: AppContext, root: Path) -> None:
    write(root / "real" / "data.txt", "d")
    junction_or_skip(root / "j", root / "real")
    paths = [e.path for e in ctx.finder.list_directory(".", depth=3).entries]
    assert "j" not in paths
    assert sorted(paths) == ["real", "real", "real/data.txt"]
