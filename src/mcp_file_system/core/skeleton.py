"""Skeleton views: a source file with function bodies folded away.

Providers report *what* to fold (``fold_ranges``: 1-based inclusive line ranges, usually
function bodies); this module decides *how* to show it, independent of the language.
Kept lines keep their original numbers, and each fold becomes one row labelled with its
line range, so the agent can open any part with ``read_lines``.
"""

from collections.abc import Iterable

from mcp_file_system.core.render import clip_line

FOLD_MARKER = "..."

Fold = tuple[int, int]


def normalize_folds(folds: Iterable[Fold], total_lines: int) -> list[Fold]:
    """Clip folds to the file, drop empty ones, and keep only outermost (merged) ranges."""
    clipped = sorted(
        (max(1, start), min(end, total_lines))
        for start, end in folds
        if min(end, total_lines) >= max(1, start)
    )
    merged: list[Fold] = []
    for start, end in clipped:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def render_skeleton(
    lines: list[str],
    folds: Iterable[Fold],
    *,
    max_line_chars: int,
    max_chars: int,
) -> tuple[str, int, int, int | None]:
    """Render the skeleton within a character budget.

    Returns:
        The content, the number of kept source lines shown, the number of folded lines,
        and the first line not shown if the budget cut the output (else ``None``).
    """
    total = len(lines)
    width = len(str(total))
    rows: list[str] = []
    used = kept = folded = 0
    line = 1
    pending = iter(normalize_folds(folds, total))
    fold = next(pending, None)
    while line <= total:
        is_fold = fold is not None and line == fold[0]
        if fold is not None and is_fold:
            start, end = fold
            source = lines[start - 1]
            indent = source[: len(source) - len(source.lstrip())]
            row = f"{start}-{end}\t{indent}{FOLD_MARKER}"
            advance = end - start + 1
            fold = next(pending, None)
        else:
            row = f"{line:>{width}}\t{clip_line(lines[line - 1], max_line_chars)}"
            advance = 1
        if rows and used + len(row) + 1 > max_chars:
            return "\n".join(rows), kept, folded, line
        rows.append(row)
        used += len(row) + 1
        if is_fold:
            folded += advance
        else:
            kept += 1
        line += advance
    return "\n".join(rows), kept, folded, None
