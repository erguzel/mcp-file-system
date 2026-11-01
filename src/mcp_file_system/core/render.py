"""Helpers for compact, budget-aware text output."""

from collections.abc import Iterable


def clip_line(text: str, limit: int, focus: int | None = None) -> str:
    """Shorten a long line to about ``limit`` characters.

    Args:
        text: The line.
        limit: Maximum number of characters kept from ``text``.
        focus: Character offset that must stay visible (e.g. a match position); the
            kept window is centered on it. Without it, the line's head is kept.
    """
    if len(text) <= limit:
        return text
    if focus is None:
        return f"{text[:limit]} … [+{len(text) - limit} chars]"
    start = max(0, min(focus - limit // 2, len(text) - limit))
    end = start + limit
    prefix = f"[{start} chars] … " if start else ""
    suffix = f" … [+{len(text) - end} chars]" if end < len(text) else ""
    return f"{prefix}{text[start:end]}{suffix}"


def number_lines(
    lines: Iterable[tuple[int, str]],
    *,
    width: int,
    max_line_chars: int,
    max_chars: int,
    always_emit: bool = True,
) -> tuple[str, int | None, int | None]:
    """Render ``(line_number, text)`` pairs as ``"  12\\ttext"`` rows within a budget.

    Args:
        lines: The ``(line_number, text)`` pairs to render.
        width: Width the line numbers are right-aligned to.
        max_line_chars: Longer lines are clipped.
        max_chars: Budget for the rows, counting one newline per row.
        always_emit: Emit the first row even if it exceeds ``max_chars``, so that a caller
            continuing from the last line always moves forward. Batch reads turn this off
            once a row has been returned, since ``max_chars`` is then what is left over.

    Returns:
        The rendered block, the first and the last line number emitted (``None``
        when nothing was emitted). Rendering stops before the row that would exceed
        ``max_chars``.
    """
    rows: list[str] = []
    used = 0
    first: int | None = None
    last: int | None = None
    for number, text in lines:
        row = f"{number:>{width}}\t{clip_line(text, max_line_chars)}"
        if (rows or not always_emit) and used + len(row) + 1 > max_chars:
            break
        rows.append(row)
        used += len(row) + 1
        if first is None:
            first = number
        last = number
    return "\n".join(rows), first, last


def human_size(size: int) -> str:
    """Format a byte count, e.g. ``1536`` -> ``"1.5 KB"``."""
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{int(value)} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    raise AssertionError("unreachable")  # pragma: no cover


def human_count(value: int) -> str:
    """Format a count compactly, e.g. ``960`` -> ``"960"``, ``12400`` -> ``"12.4k"``."""
    if value < 1000:
        return str(value)
    if value < 1_000_000:
        return f"{value / 1000:.1f}k"
    return f"{value / 1_000_000:.1f}M"


def truncate_text(text: str, max_chars: int, hint: str) -> tuple[str, bool]:
    """Cut ``text`` at a line boundary to fit ``max_chars``; append ``hint`` if cut."""
    if len(text) <= max_chars:
        return text, False
    cut = text.rfind("\n", 0, max_chars)
    cut = cut if cut > 0 else max_chars
    return f"{text[:cut]}\n[{hint}]", True
