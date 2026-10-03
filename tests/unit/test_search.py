from pathlib import Path

import pytest

from conftest import ContextFactory, write
from mcp_file_system.context import AppContext
from mcp_file_system.core.search import PythonSearchBackend, SearchRequest
from mcp_file_system.errors import InvalidArgumentError, InvalidPatternError
from mcp_file_system.security import Deadline, compile_pattern

APP = """\
import os

def alpha():
    return os.getcwd()

def beta():
    return alpha()
"""


@pytest.fixture
def project(root: Path) -> Path:
    write(root / "app.py", APP)
    write(root / "lib" / "helper.py", "def alpha_helper():\n    pass\n")
    write(root / "notes.md", "alpha and alpha again\n")
    write(root / "image.png", b"\x89PNG\x00\x00alpha")
    write(root / ".env", "alpha=secret\n")
    return root


def test_content_mode_lists_hits_grouped_by_file(ctx: AppContext, project: Path) -> None:
    result = ctx.searcher.grep(r"def alpha")
    assert [(h.path, h.line) for h in result.hits] == [("app.py", 3), ("lib/helper.py", 1)]
    assert result.files_scanned == 3  # app.py, lib/helper.py, notes.md
    assert result.files_skipped == 1  # image.png
    rendered = result.render()
    assert "app.py\n3:def alpha():" in rendered
    assert "[2 hit(s) in 2 file(s); scanned 3 file(s), skipped 1 binary/unreadable]" in rendered


def test_denied_files_are_never_searched(ctx: AppContext, project: Path) -> None:
    result = ctx.searcher.grep("secret")
    assert result.hits == []
    assert "No matches" in result.render()


def test_context_lines_are_merged_in_render(ctx: AppContext, project: Path) -> None:
    result = ctx.searcher.grep("alpha", "app.py", context_before=1, context_after=1)
    hits = result.hits
    assert [h.line for h in hits] == [3, 7]
    assert hits[0].before == [""]
    assert hits[0].after == ["    return os.getcwd()"]
    assert result.render().splitlines()[:8] == [
        "app.py",
        "2-",
        "3:def alpha():",
        "4-    return os.getcwd()",
        "--",
        "6-def beta():",
        "7:    return alpha()",
        "[2 hit(s) in 1 file(s); scanned 1 file(s)]",
    ]


def test_overlapping_context_is_not_duplicated(ctx: AppContext, root: Path) -> None:
    write(root / "f.txt", "a\nhit\nb\nhit\nc\n")
    rendered = ctx.searcher.grep("hit", "f.txt", context_before=2, context_after=2).render()
    assert rendered.splitlines()[:6] == ["f.txt", "1-a", "2:hit", "3-b", "4:hit", "5-c"]


def test_only_matching_returns_each_match(ctx: AppContext, project: Path) -> None:
    result = ctx.searcher.grep(r"alpha\w*", "notes.md", only_matching=True)
    assert [h.text for h in result.hits] == ["alpha", "alpha"]
    assert "1:alpha | alpha" in result.render()


def test_count_mode(ctx: AppContext, project: Path) -> None:
    result = ctx.searcher.grep("alpha", mode="count")
    assert [(f.path, f.count) for f in result.files] == [
        ("app.py", 2),
        ("lib/helper.py", 1),
        ("notes.md", 1),
    ]
    assert "app.py:2" in result.render()


def test_count_mode_with_only_matching_counts_matches(ctx: AppContext, project: Path) -> None:
    result = ctx.searcher.grep("alpha", "notes.md", mode="count", only_matching=True)
    assert result.files[0].count == 2


def test_files_with_matches_mode(ctx: AppContext, project: Path) -> None:
    result = ctx.searcher.grep("alpha", mode="files_with_matches", glob="*.py")
    assert [f.path for f in result.files] == ["app.py", "lib/helper.py"]
    assert result.render().startswith("app.py\nlib/helper.py\n[2 matching file(s)")


def test_file_limit_in_count_mode(ctx: AppContext, project: Path) -> None:
    result = ctx.searcher.grep("alpha", mode="count", max_matches=1)
    assert len(result.files) == 1
    assert result.truncated


def test_max_matches_truncates(ctx: AppContext, project: Path) -> None:
    result = ctx.searcher.grep("alpha", max_matches=1)
    assert len(result.hits) == 1
    assert result.truncated
    assert "max_matches=1" in result.render()


def test_max_matches_is_capped_by_settings(make_ctx: ContextFactory, project: Path) -> None:
    result = make_ctx(max_matches=2).searcher.grep("alpha", max_matches=50)
    assert len(result.hits) == 2


def test_output_budget_truncates(make_ctx: ContextFactory, root: Path) -> None:
    write(root / "big.txt", "".join(f"hit {'x' * 300}\n" for _ in range(50)))
    result = make_ctx(max_output_chars=1000).searcher.grep("hit")
    assert result.truncated
    assert result.note == "output size budget reached"
    assert len(result.hits) < 50


def test_ignore_case_and_fixed_string(ctx: AppContext, root: Path) -> None:
    write(root / "f.txt", "Value (x)\nvalue [y]\n")
    assert len(ctx.searcher.grep("VALUE", ignore_case=True).hits) == 2
    assert [h.line for h in ctx.searcher.grep("(x)", fixed_string=True).hits] == [1]


def test_long_lines_are_clipped_around_the_match(make_ctx: ContextFactory, root: Path) -> None:
    write(root / "min.js", "a" * 5000 + "NEEDLE" + "b" * 5000 + "\n")
    hit = make_ctx(max_line_chars=100).searcher.grep("NEEDLE").hits[0]
    assert "NEEDLE" in hit.text
    assert len(hit.text) < 160


def test_hidden_files_are_opt_in(ctx: AppContext, root: Path) -> None:
    write(root / ".config" / "settings.txt", "needle\n")
    assert ctx.searcher.grep("needle").hits == []
    assert len(ctx.searcher.grep("needle", include_hidden=True).hits) == 1


@pytest.mark.parametrize(
    "kwargs",
    [{"context_before": -1}, {"context_after": 21}, {"max_matches": 0}],
)
def test_invalid_arguments(ctx: AppContext, project: Path, kwargs: dict[str, int]) -> None:
    with pytest.raises(InvalidArgumentError):
        ctx.searcher.grep("alpha", **kwargs)  # type: ignore[arg-type]


def test_invalid_pattern(ctx: AppContext, project: Path) -> None:
    with pytest.raises(InvalidPatternError):
        ctx.searcher.grep("[unclosed")


class ExpiringClock:
    """Advances by one second per call, so a short deadline expires mid-search."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        self.now += 1.0
        return self.now


def test_backend_stops_when_time_budget_is_exhausted(ctx: AppContext, project: Path) -> None:
    deadline = Deadline(3.0, clock=ExpiringClock())
    request = SearchRequest(pattern=compile_pattern("alpha", deadline=deadline, max_length=10))
    files = list(ctx.finder.iter_files("."))
    result = PythonSearchBackend().search(files, request, deadline)
    assert result.truncated
    assert result.note is not None
    assert result.note.startswith("time budget exhausted")


def test_catastrophic_pattern_returns_partial_results(make_ctx: ContextFactory, root: Path) -> None:
    write(root / "a.txt", "aaaa\n")
    write(root / "b.txt", "a" * 64 + "b\n")
    result = make_ctx(search_timeout_s=0.3).searcher.grep(r"(a|aa)+$")
    assert [h.path for h in result.hits] == ["a.txt"]
    assert result.truncated
    assert "simplify" in result.render()


def test_backend_skips_vanished_files(ctx: AppContext, project: Path) -> None:
    files = list(ctx.finder.iter_files("."))
    (project / "app.py").unlink()
    deadline = Deadline(10)
    request = SearchRequest(pattern=compile_pattern("alpha", deadline=deadline, max_length=10))
    result = PythonSearchBackend().search(files, request, deadline)
    assert result.files_skipped == 2  # the vanished file and the binary image
