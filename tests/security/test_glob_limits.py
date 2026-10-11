"""Globs are agent input too: their length is capped like regex patterns."""

from pathlib import Path

import pytest

from conftest import ContextFactory, write
from mcp_file_system.errors import InvalidArgumentError


def test_find_files_rejects_a_glob_over_max_pattern_length(
    make_ctx: ContextFactory, root: Path
) -> None:
    write(root / "a.py", "x = 1\n")
    ctx = make_ctx(max_pattern_length=40)
    assert ctx.finder.find("*" * 37 + ".py").paths == ["a.py"]
    with pytest.raises(InvalidArgumentError, match="at most 40 characters"):
        ctx.finder.find("*" * 38 + ".py")


def test_grep_rejects_a_glob_over_max_pattern_length(make_ctx: ContextFactory, root: Path) -> None:
    write(root / "a.py", "x = 1\n")
    ctx = make_ctx(max_pattern_length=40)
    assert ctx.searcher.grep("x", glob="*" * 37 + ".py").hits
    with pytest.raises(InvalidArgumentError, match="at most 40 characters"):
        ctx.searcher.grep("x", glob="*" * 38 + ".py")
