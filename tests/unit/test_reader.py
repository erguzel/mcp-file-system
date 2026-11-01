import os
import sys
from pathlib import Path

import pytest

from conftest import ContextFactory, numbered, write
from mcp_file_system.context import AppContext
from mcp_file_system.core.etag import compute_etag
from mcp_file_system.core.models import LineRange
from mcp_file_system.core.reader import MAX_BATCH_RANGES
from mcp_file_system.core.tracking import ALREADY_RETURNED
from mcp_file_system.errors import (
    BinaryFileError,
    InvalidArgumentError,
    InvalidPatternError,
    NoMatchError,
    PathTypeError,
)


def body(content: str) -> list[str]:
    """Strip line numbers from a LinesResult.content block."""
    return [row.split("\t", 1)[1] for row in content.splitlines()]


class TestInfo:
    def test_text_file(self, ctx: AppContext, root: Path) -> None:
        path = write(root / "a.txt", "one\r\ntwo\r\n")
        info = ctx.reader.info("a.txt")
        assert info.kind == "file"
        assert info.line_count == 2
        assert info.encoding == "utf-8"
        assert info.newline == "CRLF"
        assert info.is_binary is False
        assert info.etag == compute_etag(path.stat())
        assert info.approx_tokens == 3  # 10 bytes / 4, rounded up
        assert "lines: 2" in info.render()
        assert "~3 tokens" in info.render()

    def test_binary_file(self, ctx: AppContext, root: Path) -> None:
        write(root / "b.bin", b"\x00\x01")
        info = ctx.reader.info("b.bin")
        assert info.is_binary is True
        assert info.line_count is None
        assert info.approx_tokens is None
        assert "binary: yes" in info.render()

    def test_directory(self, ctx: AppContext, root: Path) -> None:
        write(root / "d" / "x", "1")
        write(root / "d" / "y", "2")
        info = ctx.reader.info("d")
        assert info.kind == "directory"
        assert info.entry_count == 2
        assert info.etag is None
        assert "entries: 2" in info.render()

    def test_directory_count_matches_hidden_listing(self, ctx: AppContext, root: Path) -> None:
        write(root / ".gitignore", "build/\n")
        write(root / "d" / "a.txt", "a")
        write(root / "d" / ".github" / "ci.yml", "on: push\n")
        write(root / "d" / "build" / "out.bin", "o")
        listing = ctx.finder.list_directory("d", include_hidden=True)
        assert ctx.reader.info("d").entry_count == len(listing.entries) == 2

    @pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFOs are POSIX-only")
    def test_directory_count_skips_special_files(self, ctx: AppContext, root: Path) -> None:
        write(root / "d" / "a.txt", "a")
        os.mkfifo(root / "d" / "pipe")
        assert ctx.reader.info("d").entry_count == 1

    @pytest.mark.skipif(
        sys.platform == "win32" or os.geteuid() == 0,
        reason="POSIX permissions; root can read anything",
    )
    def test_unreadable_directory_is_an_error(self, ctx: AppContext, root: Path) -> None:
        directory = root / "locked"
        directory.mkdir()
        directory.chmod(0)
        try:
            with pytest.raises(PermissionError):
                ctx.reader.info("locked")
        finally:
            directory.chmod(0o700)

    @pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFOs are POSIX-only")
    def test_special_file_is_rejected(self, ctx: AppContext, root: Path) -> None:
        os.mkfifo(root / "pipe")
        with pytest.raises(PathTypeError):
            ctx.reader.info("pipe")


class TestReadLines:
    def test_default_reads_from_start(self, ctx: AppContext, root: Path) -> None:
        write(root / "f.txt", numbered(5))
        result = ctx.reader.read_lines("f.txt")
        assert (result.start_line, result.end_line, result.total_lines) == (1, 5, 5)
        assert body(result.content) == [f"line {n}" for n in range(1, 6)]
        assert not result.truncated
        assert result.next_start is None

    def test_explicit_range(self, ctx: AppContext, root: Path) -> None:
        write(root / "f.txt", numbered(10))
        result = ctx.reader.read_lines("f.txt", 3, 5)
        assert body(result.content) == ["line 3", "line 4", "line 5"]
        assert result.content.splitlines()[0] == " 3\tline 3"  # padded to the total's width

    def test_negative_start_reads_tail(self, ctx: AppContext, root: Path) -> None:
        write(root / "f.txt", numbered(10))
        result = ctx.reader.read_lines("f.txt", -3)
        assert (result.start_line, result.end_line) == (8, 10)

    def test_negative_start_larger_than_file_clamps(self, ctx: AppContext, root: Path) -> None:
        write(root / "f.txt", numbered(3))
        assert ctx.reader.read_lines("f.txt", -50).start_line == 1

    def test_negative_end(self, ctx: AppContext, root: Path) -> None:
        write(root / "f.txt", numbered(10))
        result = ctx.reader.read_lines("f.txt", 8, -2)
        assert (result.start_line, result.end_line) == (8, 9)

    def test_end_beyond_file_is_clamped(self, ctx: AppContext, root: Path) -> None:
        write(root / "f.txt", numbered(3))
        result = ctx.reader.read_lines("f.txt", 2, 99)
        assert (result.start_line, result.end_line) == (2, 3)
        assert not result.truncated

    @pytest.mark.parametrize(("start", "end"), [(0, None), (1, 0), (5, 2)])
    def test_invalid_ranges(self, ctx: AppContext, root: Path, start: int, end: int | None) -> None:
        write(root / "f.txt", numbered(10))
        with pytest.raises(InvalidArgumentError):
            ctx.reader.read_lines("f.txt", start, end)

    def test_start_past_end(self, ctx: AppContext, root: Path) -> None:
        write(root / "f.txt", numbered(3))
        with pytest.raises(InvalidArgumentError, match="past the end"):
            ctx.reader.read_lines("f.txt", 4)

    def test_empty_file(self, ctx: AppContext, root: Path) -> None:
        write(root / "empty.txt", "")
        result = ctx.reader.read_lines("empty.txt")
        assert result.total_lines == 0
        assert result.content == ""
        assert result.note == "File is empty."
        assert "no lines" in result.render()

    def test_line_window_is_capped(self, make_ctx: ContextFactory, root: Path) -> None:
        write(root / "f.txt", numbered(50))
        ctx = make_ctx(max_read_lines=10)
        result = ctx.reader.read_lines("f.txt", 5)
        assert (result.start_line, result.end_line) == (5, 14)
        assert result.truncated
        assert (result.next_start, result.next_end) == (15, 50)
        assert "read_lines(path='f.txt', start=15, end=50)" in result.render()

    def test_requested_end_survives_truncation(self, make_ctx: ContextFactory, root: Path) -> None:
        # Following the hint must not read past the end the caller asked for.
        write(root / "f.txt", numbered(50))
        result = make_ctx(max_read_lines=10).reader.read_lines("f.txt", 1, 30)
        assert (result.next_start, result.next_end) == (11, 30)
        assert "start=11, end=30)" in result.render()

    def test_output_budget_truncates(self, make_ctx: ContextFactory, root: Path) -> None:
        write(root / "f.txt", "".join("x" * 400 + "\n" for _ in range(20)))
        ctx = make_ctx(max_output_chars=1000)
        result = ctx.reader.read_lines("f.txt")
        assert result.end_line == 2
        assert result.next_start == 3

    def test_crlf_and_cr_lines(self, ctx: AppContext, root: Path) -> None:
        write(root / "f.txt", b"a\r\nb\rc\n")
        assert body(ctx.reader.read_lines("f.txt").content) == ["a", "b", "c"]

    def test_binary_is_rejected(self, ctx: AppContext, root: Path) -> None:
        write(root / "b.bin", b"\x00abc")
        with pytest.raises(BinaryFileError):
            ctx.reader.read_lines("b.bin")

    def test_directory_is_rejected(self, ctx: AppContext, root: Path) -> None:
        (root / "d").mkdir()
        with pytest.raises(PathTypeError):
            ctx.reader.read_lines("d")

    def test_etag_matches_file(self, ctx: AppContext, root: Path) -> None:
        path = write(root / "f.txt", numbered(2))
        assert ctx.reader.read_lines("f.txt").etag == compute_etag(path.stat())


class TestReadAround:
    @pytest.mark.parametrize(
        ("around", "radius", "window"),
        [
            (50, 3, (47, 53)),  # middle
            (2, 5, (1, 7)),  # near the start
            (99, 5, (94, 100)),  # near the end
            (1, 0, (1, 1)),  # first line only
            (100, 0, (100, 100)),  # last line only
            (40, 0, (40, 40)),
        ],
    )
    def test_window_is_clamped_to_the_file(
        self, ctx: AppContext, root: Path, around: int, radius: int, window: tuple[int, int]
    ) -> None:
        write(root / "f.txt", numbered(100))
        result = ctx.reader.read_lines("f.txt", around=around, radius=radius)
        assert (result.start_line, result.end_line) == window
        assert body(result.content) == [f"line {n}" for n in range(window[0], window[1] + 1)]
        assert not result.truncated
        assert result.note is None

    def test_default_radius(self, ctx: AppContext, root: Path) -> None:
        write(root / "f.txt", numbered(100))
        result = ctx.reader.read_lines("f.txt", around=50)
        assert (result.start_line, result.end_line) == (40, 60)

    def test_around_past_end(self, ctx: AppContext, root: Path) -> None:
        write(root / "f.txt", numbered(10))
        with pytest.raises(InvalidArgumentError, match=r"around=11 is past the end.*10 lines"):
            ctx.reader.read_lines("f.txt", around=11)

    @pytest.mark.parametrize(
        ("start", "end", "around", "radius"),
        [(5, None, 3, 1), (1, 8, 3, 1), (1, None, 0, 1), (1, None, 3, -1)],
    )
    def test_invalid_arguments(
        self, ctx: AppContext, root: Path, start: int, end: int | None, around: int, radius: int
    ) -> None:
        write(root / "f.txt", numbered(10))
        with pytest.raises(InvalidArgumentError):
            ctx.reader.read_lines("f.txt", start, end, around=around, radius=radius)

    def test_tool_default_end_counts_as_unset(self, ctx: AppContext, root: Path) -> None:
        write(root / "f.txt", numbered(10))
        result = ctx.reader.read_lines("f.txt", 1, -1, around=5, radius=1)
        assert (result.start_line, result.end_line) == (4, 6)

    def test_radius_shrinks_to_the_line_cap(self, make_ctx: ContextFactory, root: Path) -> None:
        write(root / "f.txt", numbered(100))
        ctx = make_ctx(max_read_lines=5)
        result = ctx.reader.read_lines("f.txt", around=50, radius=10)
        assert (result.start_line, result.end_line) == (48, 52)
        assert not result.truncated
        assert result.note == "radius reduced from 10 to 2 to fit max_read_lines."

    def test_empty_file(self, ctx: AppContext, root: Path) -> None:
        write(root / "empty.txt", "")
        assert ctx.reader.read_lines("empty.txt", around=1).note == "File is empty."

    def test_if_changed_since(self, ctx: AppContext, root: Path) -> None:
        write(root / "f.txt", numbered(30))
        first = ctx.reader.read_lines("f.txt", around=10, radius=2)
        again = ctx.reader.read_lines("f.txt", around=10, radius=2, if_changed_since=first.etag)
        assert again.unchanged
        assert (again.start_line, again.end_line) == (8, 12)
        assert again.content == ""


class TestReadRanges:
    def test_several_files_in_one_call(self, ctx: AppContext, root: Path) -> None:
        write(root / "a.txt", numbered(10))
        write(root / "b.txt", numbered(5, "row"))
        result = ctx.reader.read_ranges(
            [LineRange(path="a.txt", start=2, end=3), LineRange(path="b.txt", start=-2)]
        )
        assert [(r.path, r.start_line, r.end_line) for r in result.reads] == [
            ("a.txt", 2, 3),
            ("b.txt", 4, 5),
        ]
        assert body(result.reads[1].content) == ["row 4", "row 5"]
        assert result.errors == []
        assert result.cut == []
        assert "Output budget" not in result.render()

    def test_char_budget_cuts_a_range(self, make_ctx: ContextFactory, root: Path) -> None:
        write(root / "a.txt", "x" * 400 + "\n")
        write(root / "b.txt", "".join("y" * 400 + "\n" for _ in range(5)))
        ctx = make_ctx(max_output_chars=1000)
        result = ctx.reader.read_ranges(
            [LineRange(path="a.txt"), LineRange(path="b.txt", start=1, end=-2)]
        )
        assert [(r.start_line, r.end_line) for r in result.reads] == [(1, 1), (1, 1)]
        # The negative end is resolved, so the continuation stops where the request did.
        assert result.cut == [LineRange(path="b.txt", start=2, end=4)]
        rendered = result.render()
        assert "1 range(s) not fully returned" in rendered
        assert '{"path": "b.txt", "start": 2, "end": 4}' in rendered
        assert "read_lines(" not in rendered  # no per-read hint that drops the end line

    def test_line_budget_skips_later_ranges(self, make_ctx: ContextFactory, root: Path) -> None:
        write(root / "a.txt", numbered(10))
        write(root / "b.txt", numbered(10))
        write(root / "c.txt", numbered(10))
        ctx = make_ctx(max_read_lines=5)
        result = ctx.reader.read_ranges(
            [
                LineRange(path="a.txt", start=1, end=3),
                LineRange(path="b.txt"),
                LineRange(path="c.txt", start=-4),
            ]
        )
        assert [(r.path, r.start_line, r.end_line) for r in result.reads] == [
            ("a.txt", 1, 3),
            ("b.txt", 1, 2),
        ]
        assert result.cut == [
            LineRange(path="b.txt", start=3, end=10),
            LineRange(path="c.txt", start=-4, end=-1),  # never read: kept as requested
        ]

    def test_range_after_a_char_cut_waits(self, make_ctx: ContextFactory, root: Path) -> None:
        write(root / "a.txt", ("y" * 400 + "\n") * 5)
        write(root / "b.txt", "short\n")
        ctx = make_ctx(max_output_chars=1000)
        result = ctx.reader.read_ranges([LineRange(path="a.txt"), LineRange(path="b.txt")])
        assert [(r.path, r.start_line, r.end_line) for r in result.reads] == [("a.txt", 1, 2)]
        assert result.cut == [LineRange(path="a.txt", start=3, end=5), LineRange(path="b.txt")]
        assert sum(len(r.content) + 1 for r in result.reads) <= 1000

    def test_row_that_does_not_fit_waits(self, make_ctx: ContextFactory, root: Path) -> None:
        write(root / "a.txt", ("y" * 400 + "\n") * 2)
        write(root / "b.txt", "z" * 300 + "\n")
        write(root / "c.txt", "short\n")
        ctx = make_ctx(max_output_chars=1000)
        result = ctx.reader.read_ranges(
            [LineRange(path="a.txt"), LineRange(path="b.txt", start=-1), LineRange(path="c.txt")]
        )
        assert [(r.path, r.start_line, r.end_line) for r in result.reads] == [("a.txt", 1, 2)]
        # Never read, so kept as requested; the short range behind it keeps its turn too.
        assert result.cut == [LineRange(path="b.txt", start=-1), LineRange(path="c.txt")]
        assert sum(len(r.content) + 1 for r in result.reads) <= 1000
        assert ctx.reader.read_lines("b.txt").note is None  # not recorded as returned

    def test_failure_after_a_cut_is_reported_by_the_continuation(
        self, make_ctx: ContextFactory, root: Path
    ) -> None:
        write(root / "a.txt", ("y" * 400 + "\n") * 3)
        ctx = make_ctx(max_output_chars=1000)
        result = ctx.reader.read_ranges([LineRange(path="a.txt"), LineRange(path="missing.txt")])
        assert result.errors == []
        assert result.cut[-1] == LineRange(path="missing.txt")
        follow_up = ctx.reader.read_ranges(result.cut)
        assert [e.path for e in follow_up.errors] == ["missing.txt"]
        assert follow_up.cut == []

    def test_empty_file_with_budget_left_is_a_read(
        self, make_ctx: ContextFactory, root: Path
    ) -> None:
        write(root / "a.txt", "short\n")
        write(root / "empty.txt", "")
        ctx = make_ctx(max_output_chars=1000)
        result = ctx.reader.read_ranges([LineRange(path="a.txt"), LineRange(path="empty.txt")])
        assert result.reads[1].note == "File is empty."
        assert result.cut == []

    def test_continuations_finish_when_a_row_exceeds_the_budget(
        self, make_ctx: ContextFactory, root: Path
    ) -> None:
        write(root / "a.txt", ("y" * 1500 + "\n") * 3)
        write(root / "b.txt", "short\n")
        ctx = make_ctx(max_line_chars=2000, max_output_chars=1000)
        pending = [LineRange(path="a.txt"), LineRange(path="b.txt")]
        returned: list[tuple[str, int]] = []
        for _ in range(10):  # more calls than lines: a stuck continuation fails below
            result = ctx.reader.read_ranges(pending)
            returned += [
                (r.path, line) for r in result.reads for line in range(r.start_line, r.end_line + 1)
            ]
            pending = result.cut
            if not pending:
                break
        assert pending == []
        assert returned == [("a.txt", 1), ("a.txt", 2), ("a.txt", 3), ("b.txt", 1)]

    def test_failing_range_does_not_stop_the_others(
        self, ctx: AppContext, root: Path, outside: Path
    ) -> None:
        write(root / "a.txt", numbered(3))
        result = ctx.reader.read_ranges(
            [
                LineRange(path="missing.txt"),
                LineRange(path="../outside/secret.txt"),
                LineRange(path="a.txt", start=9),
                LineRange(path="a.txt", start=2, end=2),
            ]
        )
        assert [(r.path, r.start_line) for r in result.reads] == [("a.txt", 2)]
        assert [(e.path, e.start) for e in result.errors] == [
            ("missing.txt", 1),
            ("../outside/secret.txt", 1),
            ("a.txt", 9),
        ]
        assert "past the end" in result.errors[2].message
        rendered = result.render()
        assert "[missing.txt lines 1..-1: " in rendered
        assert "top secret" not in rendered
        assert result.cut == []

    def test_empty_file(self, ctx: AppContext, root: Path) -> None:
        write(root / "empty.txt", "")
        result = ctx.reader.read_ranges([LineRange(path="empty.txt")])
        assert result.reads[0].note == "File is empty."
        assert result.cut == []

    def test_repeated_range_is_noted(self, ctx: AppContext, root: Path) -> None:
        write(root / "a.txt", numbered(5))
        spec = LineRange(path="a.txt", start=1, end=2)
        result = ctx.reader.read_ranges([spec, spec])
        assert result.reads[0].note is None
        assert result.reads[1].note == ALREADY_RETURNED

    def test_too_many_ranges(self, ctx: AppContext, root: Path) -> None:
        write(root / "a.txt", numbered(5))
        with pytest.raises(InvalidArgumentError, match="split them across calls"):
            ctx.reader.read_ranges([LineRange(path="a.txt")] * (MAX_BATCH_RANGES + 1))


CONFIG = """\
[core]
name = a
[server]
host = localhost
port = 8080

[client]
retries = 3
[server]
host = second
"""


class TestReadBetween:
    def test_inclusive_block(self, ctx: AppContext, root: Path) -> None:
        write(root / "c.ini", CONFIG)
        result = ctx.reader.read_between("c.ini", r"^\[server\]", r"^$")
        assert body(result.content) == ["[server]", "host = localhost", "port = 8080", ""]
        assert (result.start_line, result.end_line) == (3, 6)
        assert result.note is None

    def test_exclusive_block(self, ctx: AppContext, root: Path) -> None:
        write(root / "c.ini", CONFIG)
        result = ctx.reader.read_between("c.ini", r"^\[server\]", r"^\[", inclusive=False)
        assert body(result.content) == ["host = localhost", "port = 8080", ""]

    def test_occurrence(self, ctx: AppContext, root: Path) -> None:
        write(root / "c.ini", CONFIG)
        result = ctx.reader.read_between("c.ini", "[server]", fixed_string=True, occurrence=2)
        assert body(result.content) == ["[server]", "host = second"]
        assert result.note is None  # no end pattern requested

    def test_missing_end_reads_to_eof_with_note(self, ctx: AppContext, root: Path) -> None:
        write(root / "c.ini", CONFIG)
        result = ctx.reader.read_between("c.ini", r"^\[client\]", "NEVER")
        assert result.end_line == 10
        assert result.note is not None
        assert "not found" in result.note

    def test_start_not_found(self, ctx: AppContext, root: Path) -> None:
        write(root / "c.ini", CONFIG)
        with pytest.raises(NoMatchError, match="2 time"):
            ctx.reader.read_between("c.ini", "[core]", fixed_string=True, occurrence=2)

    def test_ignore_case(self, ctx: AppContext, root: Path) -> None:
        write(root / "c.ini", CONFIG)
        result = ctx.reader.read_between("c.ini", "CLIENT", "retries", ignore_case=True)
        assert body(result.content) == ["[client]", "retries = 3"]

    def test_max_lines_cuts_and_points_to_continuation(self, ctx: AppContext, root: Path) -> None:
        write(root / "f.txt", "BEGIN\n" + numbered(20) + "END\n")
        result = ctx.reader.read_between("f.txt", "BEGIN", "END", max_lines=5)
        assert (result.start_line, result.end_line) == (1, 5)
        assert result.truncated
        assert result.next_start == 6
        assert result.next_end is None  # the block's end is unknown: no end in the hint
        assert "start=6)" in result.render()
        assert result.note is not None
        assert "not reached within 5 lines" in result.note

    def test_budget_cut_keeps_the_block_end(self, make_ctx: ContextFactory, root: Path) -> None:
        write(root / "f.txt", "BEGIN\n" + "".join("x" * 300 + "\n" for _ in range(20)) + "END\n")
        result = make_ctx(max_output_chars=1000).reader.read_between("f.txt", "BEGIN", "END")
        assert result.truncated
        assert result.next_end == 22
        assert f"start={result.next_start}, end=22)" in result.render()

    def test_empty_exclusive_block(self, ctx: AppContext, root: Path) -> None:
        write(root / "f.txt", "BEGIN\nEND\n")
        result = ctx.reader.read_between("f.txt", "BEGIN", "END", inclusive=False)
        assert result.content == ""
        assert result.total_lines == 2

    def test_invalid_arguments(self, ctx: AppContext, root: Path) -> None:
        write(root / "f.txt", "x\n")
        with pytest.raises(InvalidArgumentError):
            ctx.reader.read_between("f.txt", "x", occurrence=0)
        with pytest.raises(InvalidArgumentError):
            ctx.reader.read_between("f.txt", "x", max_lines=-1)
        with pytest.raises(InvalidPatternError):
            ctx.reader.read_between("f.txt", "(")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission semantics")
def test_unreadable_file_surfaces_os_error(ctx: AppContext, root: Path) -> None:  # pragma: no cover
    path = write(root / "locked.txt", "x\n")
    path.chmod(0)
    try:
        if os.access(path, os.R_OK):
            pytest.skip("running with elevated privileges")
        with pytest.raises(PermissionError):
            ctx.reader.read_lines("locked.txt")
    finally:
        path.chmod(0o644)
