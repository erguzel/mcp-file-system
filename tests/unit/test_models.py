"""Rendering edge cases not covered through the services."""

from datetime import UTC, datetime

from mcp_file_system.core.models import (
    BatchLinesResult,
    DirectoryListing,
    DirEntry,
    EditResult,
    FileCount,
    GrepHit,
    GrepResult,
    LineRange,
    LinesResult,
    RangeError,
)


def test_lines_result_note_and_empty() -> None:
    result = LinesResult(
        path="a.txt",
        start_line=0,
        end_line=0,
        total_lines=0,
        etag="1-0",
        content="",
        note="File is empty.",
    )
    assert result.render() == "==> a.txt (no lines of 0, etag 1-0) <==\n[File is empty.]"


def test_batch_lines_result_render() -> None:
    read = LinesResult(
        path="a.txt",
        start_line=1,
        end_line=1,
        total_lines=9,
        etag="1-0",
        content="1\tx",
        truncated=True,
        next_start=2,
        note="fyi",
    )
    result = BatchLinesResult(
        reads=[read],
        errors=[RangeError(path="b.txt", start=1, end=-1, message="Not found.")],
        cut=[LineRange(path="a.txt", start=2, end=5), LineRange(path="c.txt")],
    )
    assert result.render() == (
        "==> a.txt (lines 1-1 of 9, etag 1-0) <==\n1\tx\n[fyi]\n"
        "[b.txt lines 1..-1: Not found.]\n"
        "[Output budget reached; 2 range(s) not fully returned. Continue with read_ranges("
        'ranges=[{"path": "a.txt", "start": 2, "end": 5}, {"path": "c.txt", "start": 1, '
        '"end": -1}])]'
    )


def test_grep_result_notes_without_truncation() -> None:
    result = GrepResult(pattern="x", mode="content", files_scanned=3, note="fyi")
    assert result.render() == "[No matches; scanned 3 file(s)]\n[fyi]"


def test_grep_result_truncated_without_note() -> None:
    result = GrepResult(
        pattern="x",
        mode="files_with_matches",
        files=[FileCount(path="a", count=1)],
        files_scanned=1,
        truncated=True,
    )
    assert "[Truncated: limit reached." in result.render()


def test_grep_content_spanning_files_keeps_order() -> None:
    hits = [
        GrepHit(path="b.py", line=2, text="hit b"),
        GrepHit(path="a.py", line=9, text="hit a", before=["ctx"]),
    ]
    rendered = GrepResult(pattern="hit", mode="content", hits=hits, files_scanned=2).render()
    assert rendered.split("\n\n")[0] == "b.py\n2:hit b"
    assert rendered.split("\n\n")[1].startswith("a.py\n8-ctx\n9:hit a")


def test_directory_listing_without_size() -> None:
    listing = DirectoryListing(
        path=".",
        depth=1,
        entries=[DirEntry(path="x", kind="file", depth=1, size_bytes=None)],
    )
    assert listing.render().splitlines()[1] == "x"


def test_edit_result_without_diff() -> None:
    result = EditResult(path="a", applied=True, dry_run=False, changes=0, etag="1-1", diff="")
    assert result.render().endswith("[No textual changes]")


def test_file_info_serializes_datetime() -> None:
    from mcp_file_system.core.models import FileInfo

    info = FileInfo(path="a", kind="file", size_bytes=1, modified=datetime(2026, 1, 1, tzinfo=UTC))
    assert info.model_dump(mode="json")["modified"] == "2026-01-01T00:00:00Z"
