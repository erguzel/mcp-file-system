"""Change-aware reads (session tracking, if_changed_since) and grep symbol hints."""

import threading
from pathlib import Path

import pytest

from conftest import ContextFactory, numbered, write
from mcp_file_system.context import AppContext
from mcp_file_system.core.etag import compute_etag
from mcp_file_system.core.tracking import (
    ALREADY_RETURNED,
    NOT_RETURNED_YET,
    UNCHANGED,
    ReadTracker,
)

FILE = Path("/virtual/file.txt")


class TestReadTracker:
    def test_records_and_merges_intervals(self) -> None:
        tracker = ReadTracker()
        tracker.record(FILE, "e1", 1, 10)
        tracker.record(FILE, "e1", 11, 20)  # adjacent: merged
        tracker.record(FILE, "e1", 30, 40)
        assert tracker.covers(FILE, "e1", 1, 20)
        assert not tracker.covers(FILE, "e1", 1, 30)
        assert tracker.overlap(FILE, "e1", 15, 35) == [(15, 20), (30, 35)]

    def test_new_etag_discards_old_version(self) -> None:
        tracker = ReadTracker()
        tracker.record(FILE, "e1", 1, 10)
        assert tracker.overlap(FILE, "e2", 1, 10) == []
        tracker.record(FILE, "e2", 5, 6)
        assert tracker.overlap(FILE, "e1", 1, 10) == []
        assert tracker.covers(FILE, "e2", 5, 6)

    def test_invalid_ranges_are_ignored(self) -> None:
        tracker = ReadTracker()
        tracker.record(FILE, "e1", 0, 5)
        tracker.record(FILE, "e1", 5, 4)
        assert tracker.overlap(FILE, "e1", 1, 100) == []

    def test_least_recently_used_files_are_dropped(self) -> None:
        tracker = ReadTracker(max_files=2)
        a, b, c = Path("/a"), Path("/b"), Path("/c")
        tracker.record(a, "e", 1, 1)
        tracker.record(b, "e", 1, 1)
        tracker.record(a, "e", 2, 2)  # a becomes most recent
        tracker.record(c, "e", 1, 1)  # evicts b
        assert tracker.covers(a, "e", 1, 2)
        assert not tracker.covers(b, "e", 1, 1)
        assert tracker.covers(c, "e", 1, 1)

    def test_concurrent_records(self) -> None:
        tracker = ReadTracker()

        def worker(offset: int) -> None:
            for n in range(200):
                tracker.record(FILE, "e", offset + n * 10 + 1, offset + n * 10 + 10)

        threads = [threading.Thread(target=worker, args=(k * 2000,)) for k in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert tracker.covers(FILE, "e", 1, 8000)


class TestReadLines:
    def test_repeated_read_gets_a_note_but_still_returns_content(
        self, ctx: AppContext, root: Path
    ) -> None:
        write(root / "f.txt", numbered(10))
        first = ctx.reader.read_lines("f.txt", 2, 5)
        assert first.note is None
        again = ctx.reader.read_lines("f.txt", 2, 5)
        assert again.note == ALREADY_RETURNED
        assert again.content == first.content

    def test_partial_overlap_names_the_lines(self, ctx: AppContext, root: Path) -> None:
        write(root / "f.txt", numbered(20))
        ctx.reader.read_lines("f.txt", 1, 5)
        ctx.reader.read_lines("f.txt", 8, 8)
        result = ctx.reader.read_lines("f.txt", 4, 10)
        assert result.note == (
            "Lines 4-5, 8 were already returned earlier in this session; file unchanged."
        )

    def test_modification_resets_tracking(self, ctx: AppContext, root: Path) -> None:
        path = write(root / "f.txt", numbered(5))
        ctx.reader.read_lines("f.txt")
        write(path, numbered(6))
        assert ctx.reader.read_lines("f.txt").note is None

    def test_if_changed_since_unchanged_omits_content(self, ctx: AppContext, root: Path) -> None:
        write(root / "f.txt", numbered(10))
        first = ctx.reader.read_lines("f.txt", 3, 6)
        result = ctx.reader.read_lines("f.txt", 3, 6, if_changed_since=first.etag)
        assert result.unchanged
        assert result.content == ""
        assert result.note == UNCHANGED
        assert (result.start_line, result.end_line, result.total_lines) == (3, 6, 10)
        rendered = result.render()
        assert "Unchanged since your earlier read" in rendered
        assert len(rendered) < 150

    def test_if_changed_since_for_lines_never_sent_returns_content(
        self, ctx: AppContext, root: Path
    ) -> None:
        # A model passing an etag for lines it never received must not be left without them.
        path = write(root / "f.txt", numbered(10))
        ctx.reader.read_lines("f.txt", 1, 3)
        result = ctx.reader.read_lines("f.txt", 1, 6, if_changed_since=compute_etag(path.stat()))
        assert not result.unchanged
        assert (result.start_line, result.end_line) == (1, 6)
        assert result.note is not None
        assert NOT_RETURNED_YET in result.note
        assert "Lines 1-3 were already returned" in result.note

    def test_if_changed_since_after_modification(self, ctx: AppContext, root: Path) -> None:
        path = write(root / "f.txt", numbered(10))
        old = ctx.reader.read_lines("f.txt", 1, 3).etag
        write(path, numbered(11))
        result = ctx.reader.read_lines("f.txt", 1, 3, if_changed_since=old)
        assert not result.unchanged
        assert result.content
        assert result.note == f"The file changed since etag {old}."

    def test_tail_window_is_tracked(self, ctx: AppContext, root: Path) -> None:
        write(root / "f.txt", numbered(10))
        first = ctx.reader.read_lines("f.txt", -3)
        assert ctx.reader.read_lines("f.txt", -3, if_changed_since=first.etag).unchanged

    def test_truncated_window_only_counts_what_was_sent(
        self, make_ctx: ContextFactory, root: Path
    ) -> None:
        write(root / "f.txt", numbered(50))
        ctx = make_ctx(max_read_lines=10)
        first = ctx.reader.read_lines("f.txt")
        assert first.end_line == 10
        # The default request window is 1-10, which is exactly what was sent.
        assert ctx.reader.read_lines("f.txt", if_changed_since=first.etag).unchanged
        assert not ctx.reader.read_lines("f.txt", 5, 15, if_changed_since=first.etag).unchanged


class TestReadBetweenAndSymbols:
    def test_read_between_is_tracked(self, ctx: AppContext, root: Path) -> None:
        write(root / "c.ini", "[a]\nx = 1\n[b]\ny = 2\n")
        ctx.reader.read_between("c.ini", r"^\[a\]", r"^x")
        assert ctx.reader.read_lines("c.ini", 1, 2).note == ALREADY_RETURNED

    def test_read_symbol_unchanged(self, ctx: AppContext, root: Path) -> None:
        write(root / "m.py", "def a():\n    return 1\n\n\ndef b():\n    return 2\n")
        first = ctx.outliner.read_symbol("m.py", "b")
        again = ctx.outliner.read_symbol("m.py", "b", if_changed_since=first.etag)
        assert again.unchanged
        assert again.note == f"function b | {UNCHANGED}"

    def test_read_lines_and_read_symbol_share_one_tracker(
        self, ctx: AppContext, root: Path
    ) -> None:
        write(root / "m.py", "def a():\n    return 1\n")
        ctx.reader.read_lines("m.py")
        result = ctx.outliner.read_symbol("m.py", "a")
        assert result.note is not None
        assert ALREADY_RETURNED in result.note


class TestGrepSymbols:
    def test_python_hits_name_their_function_or_method(self, ctx: AppContext, root: Path) -> None:
        write(
            root / "app.py",
            "import os\n\nclass Parser:\n    def parse(self):\n        return os.sep\n\n"
            "TOP = os.name\n",
        )
        result = ctx.searcher.grep(r"os\.")
        assert [(h.line, h.symbol) for h in result.hits] == [(5, "Parser.parse"), (7, "")]
        assert "[Parser.parse]\n5:        return os.sep" in result.render()

    def test_config_and_docs_hits(self, ctx: AppContext, root: Path) -> None:
        write(root / "ci.yml", "jobs:\n  test:\n    steps:\n      - run: pytest\n")
        write(root / "README.md", "# Guide\n## Testing\nRun pytest.\n")
        write(root / "Dockerfile", "FROM python AS build\nRUN pytest\n")
        symbols = {(h.path, h.symbol) for h in ctx.searcher.grep("pytest").hits}
        assert symbols == {
            ("ci.yml", "jobs.test.steps[0].run"),
            ("README.md", "Guide > Testing"),
            ("Dockerfile", "build.RUN[0]"),
        }

    def test_files_without_outline_support_have_no_symbol(
        self, ctx: AppContext, root: Path
    ) -> None:
        write(root / "notes.txt", "needle\n")
        assert [h.symbol for h in ctx.searcher.grep("needle").hits] == [""]

    def test_too_large_or_unparsable_files_are_skipped(
        self, make_ctx: ContextFactory, root: Path
    ) -> None:
        write(root / "big.py", "def f():\n    needle = 1\n" + "# pad\n" * 100)
        write(root / "bad.py", "def (:\n    needle\n")
        ctx = make_ctx(max_file_bytes=200)
        assert {h.symbol for h in ctx.searcher.grep("needle").hits} == {""}

    def test_other_modes_are_not_annotated(self, ctx: AppContext, root: Path) -> None:
        write(root / "m.py", "def f():\n    needle = 1\n")
        result = ctx.searcher.grep("needle", mode="count")
        assert result.hits == []
        assert result.files[0].count == 1

    @pytest.mark.parametrize("budget", [0.000001])
    def test_symbol_lookup_respects_the_time_budget(
        self, make_ctx: ContextFactory, root: Path, budget: float
    ) -> None:
        write(root / "m.py", "def f():\n    needle = 1\n")
        result = make_ctx(search_timeout_s=budget).searcher.grep("needle", fixed_string=True)
        assert all(h.symbol == "" for h in result.hits)
