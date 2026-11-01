import pytest

from mcp_file_system.core.render import (
    clip_line,
    human_count,
    human_size,
    number_lines,
    truncate_text,
)


def test_clip_line_keeps_short_lines() -> None:
    assert clip_line("short", 10) == "short"


def test_clip_line_keeps_head_without_focus() -> None:
    assert clip_line("abcdefghij", 4) == "abcd … [+6 chars]"


def test_clip_line_centers_on_focus() -> None:
    text = "x" * 50 + "MATCH" + "y" * 50
    clipped = clip_line(text, 20, focus=50)
    assert "MATCH" in clipped
    assert clipped.startswith("[40 chars] … ")
    assert clipped.endswith(" … [+45 chars]")


def test_clip_line_focus_near_edges() -> None:
    text = "MATCH" + "y" * 50
    assert clip_line(text, 10, focus=0) == "MATCHyyyyy … [+45 chars]"
    tail = "y" * 50 + "MATCH"
    assert clip_line(tail, 10, focus=50) == "[45 chars] … yyyyyMATCH"


def test_number_lines_formats_and_reports_span() -> None:
    content, first, last = number_lines(
        [(9, "a"), (10, "b")], width=2, max_line_chars=100, max_chars=1000
    )
    assert content == " 9\ta\n10\tb"
    assert (first, last) == (9, 10)


def test_number_lines_respects_budget_but_emits_one_row() -> None:
    rows = [(n, "x" * 30) for n in range(1, 10)]
    content, first, last = number_lines(rows, width=1, max_line_chars=100, max_chars=70)
    assert (first, last) == (1, 2)
    assert content.count("\n") == 1
    content, first, last = number_lines(
        [(1, "y" * 500)], width=1, max_line_chars=1000, max_chars=10
    )
    assert (first, last) == (1, 1)


def test_number_lines_without_always_emit_stays_within_budget() -> None:
    too_long = number_lines(
        [(1, "y" * 500)], width=1, max_line_chars=1000, max_chars=10, always_emit=False
    )
    assert too_long == ("", None, None)
    rows = [(n, "x" * 30) for n in range(1, 10)]
    content, first, last = number_lines(
        rows, width=1, max_line_chars=100, max_chars=70, always_emit=False
    )
    assert (first, last) == (1, 2)
    assert len(content) + 1 <= 70


def test_number_lines_clips_long_lines() -> None:
    content, _, _ = number_lines([(1, "z" * 100)], width=1, max_line_chars=10, max_chars=1000)
    assert content == "1\t" + "z" * 10 + " … [+90 chars]"


def test_number_lines_empty() -> None:
    assert number_lines([], width=1, max_line_chars=10, max_chars=10) == ("", None, None)


@pytest.mark.parametrize(
    ("size", "text"),
    [
        (0, "0 B"),
        (1023, "1023 B"),
        (1536, "1.5 KB"),
        (5 * 1024**2, "5.0 MB"),
        (3 * 1024**4, "3072.0 GB"),
    ],
)
def test_human_size(size: int, text: str) -> None:
    assert human_size(size) == text


def test_truncate_text() -> None:
    assert truncate_text("short", 10, "cut") == ("short", False)
    text, cut = truncate_text("line one\nline two\nline three", 20, "cut")
    assert cut
    assert text == "line one\nline two\n[cut]"
    text, cut = truncate_text("x" * 30, 10, "cut")
    assert text == "x" * 10 + "\n[cut]"


@pytest.mark.parametrize(
    ("value", "text"),
    [(0, "0"), (999, "999"), (1000, "1.0k"), (12_400, "12.4k"), (3_200_000, "3.2M")],
)
def test_human_count(value: int, text: str) -> None:
    assert human_count(value) == text
