import os
from pathlib import Path

import pytest

from conftest import ContextFactory, symlink_or_skip, write
from mcp_file_system.context import AppContext
from mcp_file_system.core.finder import GlobMatcher
from mcp_file_system.errors import InvalidArgumentError, PathTypeError


@pytest.fixture
def tree(root: Path) -> Path:
    write(root / "README.md", "# readme\n")
    write(root / "src" / "pkg" / "__init__.py", "")
    write(root / "src" / "pkg" / "core.py", "x = 1\n")
    write(root / "src" / "pkg" / "util.py", "y = 2\n")
    write(root / "src" / "main.py", "print()\n")
    write(root / "tests" / "test_core.py", "def test(): ...\n")
    write(root / ".hidden" / "conf.py", "z = 3\n")
    write(root / ".env", "SECRET=1\n")
    write(root / "build" / "out.py", "generated\n")
    write(root / "debug.log", "noise\n")
    write(root / ".gitignore", "build/\n*.log\n")
    return root


def listing_paths(ctx: AppContext, **kwargs: object) -> list[str]:
    return [e.path for e in ctx.finder.list_directory(**kwargs).entries]  # type: ignore[arg-type]


class TestGlobMatcher:
    def test_unanchored_glob_matches_any_depth(self) -> None:
        matcher = GlobMatcher("*.py")
        assert matcher.matches("a.py")
        assert matcher.matches("src/pkg/a.py")
        assert not matcher.matches("a.pyc")

    def test_anchored_glob(self) -> None:
        matcher = GlobMatcher("src/**/*.py")
        assert matcher.matches("src/pkg/a.py")
        assert not matcher.matches("other/src/a.py")

    def test_directory_matching(self) -> None:
        assert GlobMatcher("pkg").matches("src/pkg", is_dir=True)
        assert GlobMatcher("pkg/").matches("src/pkg", is_dir=True)

    @pytest.mark.parametrize("pattern", ["", "   ", "!x"])
    def test_invalid_globs(self, pattern: str) -> None:
        with pytest.raises(InvalidArgumentError):
            GlobMatcher(pattern)


class TestListDirectory:
    def test_depth_one_lists_visible_children_sorted(self, ctx: AppContext, tree: Path) -> None:
        assert listing_paths(ctx) == ["README.md", "src", "tests"]

    def test_depth_two_is_preorder(self, ctx: AppContext, tree: Path) -> None:
        assert listing_paths(ctx, depth=2) == [
            "README.md",
            "src",
            "src/main.py",
            "src/pkg",
            "tests",
            "tests/test_core.py",
        ]

    def test_include_hidden_still_applies_deny_and_gitignore(
        self, ctx: AppContext, tree: Path
    ) -> None:
        paths = listing_paths(ctx, include_hidden=True)
        assert ".hidden" in paths
        assert ".gitignore" in paths
        assert ".env" not in paths
        assert "build" not in paths

    def test_gitignore_can_be_disabled(self, make_ctx: ContextFactory, tree: Path) -> None:
        paths = listing_paths(make_ctx(respect_gitignore=False))
        assert "build" in paths
        assert "debug.log" in paths

    def test_subdirectory_and_sizes(self, ctx: AppContext, tree: Path) -> None:
        listing = ctx.finder.list_directory("src/pkg")
        assert listing.path == "src/pkg"
        sizes = {e.path: e.size_bytes for e in listing.entries}
        assert sizes["src/pkg/core.py"] == 6
        assert "core.py  (6 B)" in listing.render()

    def test_truncation(self, make_ctx: ContextFactory, tree: Path) -> None:
        listing = make_ctx(max_results=2).finder.list_directory(depth=3)
        assert len(listing.entries) == 2
        assert listing.truncated
        assert "Truncated" in listing.render()

    def test_invalid_arguments(self, ctx: AppContext, tree: Path) -> None:
        with pytest.raises(InvalidArgumentError):
            ctx.finder.list_directory(depth=0)
        with pytest.raises(PathTypeError):
            ctx.finder.list_directory("README.md")


class TestNestedGitignore:
    def test_deeper_rules_and_negation(self, ctx: AppContext, root: Path) -> None:
        write(root / ".gitignore", "*.tmp\n")
        write(root / "a" / ".gitignore", "!keep.tmp\nlocal/\n")
        write(root / "a" / "keep.tmp", "k")
        write(root / "a" / "drop.tmp", "d")
        write(root / "a" / "local" / "x.txt", "x")
        write(root / "b" / "other.tmp", "o")
        found = ctx.finder.find("*", kind="any").paths
        assert "a/keep.tmp" in found
        assert "a/drop.tmp" not in found
        assert "a/local/" not in found
        assert "a/local/x.txt" not in found
        assert "b/other.tmp" not in found

    def test_rules_above_walk_start_apply(self, ctx: AppContext, root: Path) -> None:
        write(root / ".gitignore", "*.gen\n")
        write(root / "sub" / "a.gen", "x")
        write(root / "sub" / "a.src", "x")
        assert ctx.finder.find("*", "sub").paths == ["sub/a.src"]

    def test_only_line_terminators_split_patterns(self, ctx: AppContext, root: Path) -> None:
        # A form feed is part of the pattern "x\fy", not a line break that would yield "x".
        write(root / ".gitignore", b"x\x0cy\n*.tmp\n")
        write(root / "x", "x")
        write(root / "a.tmp", "t")
        assert ctx.finder.find("*").paths == ["x"]


class TestFind:
    def test_find_files_by_glob(self, ctx: AppContext, tree: Path) -> None:
        result = ctx.finder.find("*.py")
        assert result.paths == [
            "src/main.py",
            "src/pkg/__init__.py",
            "src/pkg/core.py",
            "src/pkg/util.py",
            "tests/test_core.py",
        ]
        assert "5 path(s)" in result.render()

    def test_find_in_subdirectory_with_anchored_glob(self, ctx: AppContext, tree: Path) -> None:
        assert ctx.finder.find("pkg/*.py", "src").paths == [
            "src/pkg/__init__.py",
            "src/pkg/core.py",
            "src/pkg/util.py",
        ]

    def test_find_directories(self, ctx: AppContext, tree: Path) -> None:
        assert ctx.finder.find("pkg", kind="directory").paths == ["src/pkg/"]

    def test_max_depth(self, ctx: AppContext, tree: Path) -> None:
        assert ctx.finder.find("*.py", max_depth=2).paths == ["src/main.py", "tests/test_core.py"]

    def test_max_results(self, ctx: AppContext, tree: Path) -> None:
        result = ctx.finder.find("*.py", max_results=2)
        assert len(result.paths) == 2
        assert result.truncated
        assert "Truncated" in result.render()

    def test_no_matches(self, ctx: AppContext, tree: Path) -> None:
        result = ctx.finder.find("*.rs")
        assert result.paths == []
        assert "No paths" in result.render()

    def test_invalid_depth(self, ctx: AppContext, tree: Path) -> None:
        with pytest.raises(InvalidArgumentError):
            ctx.finder.find("*", max_depth=0)


class TestSymlinksInWalks:
    def test_escaping_symlinks_are_skipped(
        self, ctx: AppContext, root: Path, outside: Path
    ) -> None:
        write(root / "a.txt", "a")
        symlink_or_skip(root / "out_dir", outside)
        symlink_or_skip(root / "out_file", outside / "secret.txt")
        assert listing_paths(ctx, depth=3) == ["a.txt"]

    def test_internal_file_symlink_is_listed_by_target(self, ctx: AppContext, root: Path) -> None:
        write(root / "real" / "data.txt", "d")
        symlink_or_skip(root / "alias.txt", root / "real" / "data.txt")
        files = list(ctx.finder.iter_files("."))
        assert [f.display for f in files] == ["real/data.txt", "real/data.txt"]

    def test_internal_directory_symlink_is_not_descended(self, ctx: AppContext, root: Path) -> None:
        write(root / "real" / "data.txt", "d")
        symlink_or_skip(root / "loop", root)
        paths = listing_paths(ctx, depth=5)
        assert paths.count("real/data.txt") == 1


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFOs are POSIX-only")
def test_special_files_and_links_to_them_are_skipped(ctx: AppContext, root: Path) -> None:
    os.mkfifo(root / "pipe")
    symlink_or_skip(root / "pipe_link", root / "pipe")
    write(root / "a.txt", "needle\n")
    assert listing_paths(ctx) == ["a.txt"]
    # Opening a FIFO would block the search forever.
    assert len(ctx.searcher.grep("needle").hits) == 1


def test_iter_files_accepts_a_single_file(ctx: AppContext, tree: Path) -> None:
    assert [f.display for f in ctx.finder.iter_files("src/main.py")] == ["src/main.py"]


def test_iter_files_with_glob(ctx: AppContext, tree: Path) -> None:
    files = [f.display for f in ctx.finder.iter_files("src", glob="core.py")]
    assert files == ["src/pkg/core.py"]
