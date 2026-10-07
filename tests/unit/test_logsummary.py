from pathlib import Path

import pytest

from conftest import ContextFactory, write
from mcp_file_system.context import AppContext
from mcp_file_system.errors import AccessDeniedError, BinaryFileError, InvalidArgumentError
from mcp_file_system.security import Deadline

LOG = """\
2026-10-06T12:00:01Z ERROR timeout after 312ms to 10.0.0.4
2026-10-06T12:00:02Z INFO request 7 ok

2026-10-06T12:00:03Z ERROR timeout after 98ms to 10.0.0.7
2026-10-06T12:00:04Z INFO request 8 ok
2026-10-06T12:00:05Z ERROR timeout after 7ms to 10.0.0.9
2026-10-06T12:00:06Z WARN disk 91% full
"""


class ExpiringClock:
    """Advances by one second per call, so a short deadline expires mid-file."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        self.now += 1.0
        return self.now


def test_templates_are_ordered_by_count(ctx: AppContext, root: Path) -> None:
    write(root / "app.log", LOG)
    result = ctx.log_summarizer.summarize("app.log")
    assert [(g.template, g.count, g.first_line, g.last_line) for g in result.groups] == [
        ("<TS> ERROR timeout after <N>ms to <IP>", 3, 1, 6),
        ("<TS> INFO request <N> ok", 2, 2, 5),
        ("<TS> WARN disk <N>% full", 1, 7, 7),
    ]
    assert (result.scanned_from, result.scanned_to, result.total_lines) == (1, 7, 7)
    assert result.next_start is None
    assert not result.truncated
    rendered = result.render()
    assert "(lines 1-7 of 7: 3 template(s);" in rendered
    assert "[x3] <TS> ERROR timeout after <N>ms to <IP>\n  lines 1..6" in rendered
    assert "[x1] <TS> WARN disk <N>% full\n  line 7" in rendered


def test_blank_lines_are_not_a_template(ctx: AppContext, root: Path) -> None:
    write(root / "app.log", "\n\n  \nx 1\n")
    assert [g.template for g in ctx.log_summarizer.summarize("app.log").groups] == ["x <N>"]


def test_limit_keeps_the_most_frequent(ctx: AppContext, root: Path) -> None:
    write(root / "app.log", LOG)
    result = ctx.log_summarizer.summarize("app.log", limit=1)
    assert [g.count for g in result.groups] == [3]
    assert result.template_count == 3
    assert result.truncated
    assert "[Top 1 of 3 templates: raise limit for more.]" in result.render()


def test_limit_is_capped_by_max_results(make_ctx: ContextFactory, root: Path) -> None:
    write(root / "app.log", "".join(f"event{chr(97 + i)}\n" for i in range(5)))
    result = make_ctx(max_results=2).log_summarizer.summarize("app.log", limit=50)
    assert len(result.groups) == 2


def test_output_budget_bounds_the_groups(make_ctx: ContextFactory, root: Path) -> None:
    write(
        root / "app.log", "".join(f"{'x' * 900} {chr(97 + i % 26) * (i + 1)}\n" for i in range(40))
    )
    result = make_ctx(max_output_chars=5000).log_summarizer.summarize("app.log", limit=40)
    assert 1 <= len(result.groups) < 40
    assert "output size budget reached" in result.render()


def test_start_skips_earlier_lines(ctx: AppContext, root: Path) -> None:
    write(root / "app.log", LOG)
    result = ctx.log_summarizer.summarize("app.log", start=5)
    assert [(g.count, g.first_line) for g in result.groups] == [(1, 5), (1, 6), (1, 7)]
    assert (result.scanned_from, result.scanned_to) == (5, 7)


def test_time_budget_reports_where_to_continue(
    ctx: AppContext, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write(root / "app.log", LOG)
    monkeypatch.setattr(
        "mcp_file_system.core.logsummary.Deadline",
        lambda seconds: Deadline(3.0, clock=ExpiringClock()),
    )
    partial = ctx.log_summarizer.summarize("app.log")
    assert (partial.scanned_to, partial.next_start) == (2, 3)
    assert partial.truncated
    rendered = partial.render()
    assert "counts cover lines 1-2 only" in rendered
    assert "Continue with log_summary(path='app.log', start=3)" in rendered

    monkeypatch.undo()
    rest = ctx.log_summarizer.summarize("app.log", start=3)
    assert rest.next_start is None
    counted = sum(g.count for g in partial.groups) + sum(g.count for g in rest.groups)
    assert counted == 6  # every non-blank line, counted exactly once


def test_lines_beyond_the_template_limit_are_reported(ctx: AppContext, root: Path) -> None:
    write(root / "app.log", LOG)
    result = ctx.log_summarizer.summarize("app.log")
    assert result.ungrouped == 0
    rendered = result.model_copy(update={"ungrouped": 4}).render()
    assert "[4 more line(s) beyond the template limit.]" in rendered


@pytest.mark.parametrize("kwargs", [{"limit": 0}, {"start": 0}, {"start": 9}])
def test_invalid_arguments(ctx: AppContext, root: Path, kwargs: dict[str, int]) -> None:
    write(root / "app.log", LOG)
    with pytest.raises(InvalidArgumentError):
        ctx.log_summarizer.summarize("app.log", **kwargs)


def test_empty_file(ctx: AppContext, root: Path) -> None:
    write(root / "empty.log", "")
    result = ctx.log_summarizer.summarize("empty.log")
    assert (result.groups, result.template_count, result.total_lines) == ([], 0, 0)


def test_binary_file_is_rejected(ctx: AppContext, root: Path) -> None:
    write(root / "app.log", b"\x00\x01binary")
    with pytest.raises(BinaryFileError):
        ctx.log_summarizer.summarize("app.log")


def test_denied_file_is_rejected(ctx: AppContext, root: Path) -> None:
    write(root / ".env", "SECRET=1\n")
    with pytest.raises(AccessDeniedError):
        ctx.log_summarizer.summarize(".env")
