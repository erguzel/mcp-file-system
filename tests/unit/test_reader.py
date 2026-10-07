import os
import sys
from pathlib import Path

import pytest

from conftest import ContextFactory, numbered, write
from mcp_file_system.context import AppContext
from mcp_file_system.core.etag import compute_etag
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
        assert result.next_start == 15
        assert "start=15" in result.render()

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
        assert result.note is not None
        assert "not reached within 5 lines" in result.note

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
