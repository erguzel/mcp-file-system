"""Unified diff parsing and all-or-nothing hunk application for one file.

Agents produce unified diffs fluently, but their line numbers and hunk counts are often
off. The rules here follow ``git apply`` without fuzz: context and removed lines must
match the file exactly, a hunk whose position is off is placed at the nearest exact
match, and an ambiguous or missing match fails that hunk. Header counts are recomputed
from the hunk body; only the start line is used, as a position hint. Unlike
``git apply --unidiff-zero``, which joins the next line onto a marked added line, a line
with a ``\\ No newline`` marker on the new side must end up last: a marked insertion away
from the end of the file fails, and so does any hunk after one whose new side is marked.

Reports and errors never quote lines of the file or the patch, because they reach the
audit log. Hunk headers are rebuilt without the section text git appends (often a
function name).
"""

import re
from dataclasses import dataclass

from mcp_file_system.core.models import HunkReport
from mcp_file_system.core.textio import split_lines, strip_eol
from mcp_file_system.errors import InvalidArgumentError

MAX_HUNKS = 500
_HUNK_HEADER = re.compile(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")
_NO_NEWLINE = "\\"

Op = tuple[str, str]
"""One body line: its tag (``" "``, ``"-"`` or ``"+"``) and its text."""


@dataclass(frozen=True, slots=True)
class Hunk:
    """One parsed hunk.

    Attributes:
        index: 1-based position in the patch, used in reports.
        old_start: Old start line from the header (the position hint).
        new_start: New start line from the header (only used to rebuild the header).
        ops: Body lines in order.
        old_no_newline: The old side ends without a final newline.
        new_no_newline: The new side ends without a final newline.
    """

    index: int
    old_start: int
    new_start: int
    ops: tuple[Op, ...]
    old_no_newline: bool
    new_no_newline: bool

    @property
    def old_lines(self) -> list[str]:
        """Lines the hunk expects in the file (context and removed lines)."""
        return [text for tag, text in self.ops if tag != "+"]

    @property
    def new_count(self) -> int:
        """Number of lines on the new side."""
        return sum(1 for tag, _ in self.ops if tag != "-")

    @property
    def at_eof(self) -> bool:
        """Whether a ``\\ No newline`` marker ties the hunk to the end of the file."""
        return self.old_no_newline or self.new_no_newline

    @property
    def header(self) -> str:
        """The header with counts recomputed from the body and no section text."""
        return f"@@ -{self.old_start},{len(self.old_lines)} +{self.new_start},{self.new_count} @@"


@dataclass(frozen=True, slots=True)
class PatchOutcome:
    """Result of applying a patch: the new text (if every hunk applied) and the reports."""

    text: str | None
    reports: list[HunkReport]


def parse_patch(diff: str) -> list[Hunk]:
    """Parse a unified diff for one file into hunks.

    Lines before the first hunk (``diff --git``, ``index``, ``---``/``+++``) are
    accepted and their paths ignored: the target file is given separately.

    Raises:
        InvalidArgumentError: The text holds no hunk, more than :data:`MAX_HUNKS` hunks,
            more than one file, a file creation, or a line that does not belong to a hunk.
    """
    rows = split_lines(diff)
    hunks: list[Hunk] = []
    seen_file_header = False
    i = 0
    while i < len(rows):
        row = rows[i]
        if _is_file_header(rows, i):
            if seen_file_header or hunks:
                raise InvalidArgumentError(
                    "The patch changes more than one file; apply_patch takes one file per call."
                )
            if row[4:].split("\t")[0].strip() == "/dev/null":
                raise InvalidArgumentError(
                    "The patch creates a new file; use write_file to create files."
                )
            seen_file_header = True
            i += 2
            continue
        if row.startswith("diff ") and hunks:
            raise InvalidArgumentError(
                "The patch changes more than one file; apply_patch takes one file per call."
            )
        if row.startswith("@@"):
            header = _HUNK_HEADER.match(row)
            if header is None:
                raise InvalidArgumentError(
                    f"Line {i + 1} of the patch is not a valid hunk header; expected "
                    "'@@ -<old start>,<count> +<new start>,<count> @@'."
                )
            end = _body_end(rows, i + 1)
            hunks.append(_parse_hunk(len(hunks) + 1, header, rows, i + 1, end))
            i = end
            continue
        i += 1  # preamble such as "diff --git" or "index", or a blank separator
    if not hunks:
        raise InvalidArgumentError("The patch contains no hunk (a line starting with '@@ -').")
    if len(hunks) > MAX_HUNKS:
        raise InvalidArgumentError(
            f"The patch has {len(hunks)} hunks; at most {MAX_HUNKS} hunks per call. Split it."
        )
    return hunks


def apply_hunks(lines: list[str], hunks: list[Hunk], newline: str) -> PatchOutcome:
    """Apply ``hunks`` to ``lines`` (with terminators), all or nothing.

    Every hunk is located in the original lines; located hunks must be in ascending
    order and must not overlap. Unchanged lines keep their own terminators; added lines
    get ``newline``. The file's final-newline state is kept unless a hunk carries a
    ``\\ No newline`` marker, which ties it to the end of the file; no hunk may follow
    one whose new side carries the marker, since that line must stay last.
    """
    stripped = [strip_eol(line) for line in lines]
    has_final_newline = not lines or strip_eol(lines[-1]) != lines[-1]
    reports: list[HunkReport] = []
    placed: list[tuple[Hunk, int]] = []
    previous_end, previous_index = 0, 0
    for hunk in hunks:
        start, reason = _locate(stripped, hunk, has_final_newline)
        if start is not None and start < previous_end:
            start, reason = None, f"overlaps or precedes hunk {previous_index}"
        elif start is not None and placed and placed[-1][0].new_no_newline:
            start = None
            reason = (
                f"comes after hunk {previous_index}, whose '\\ No newline' marker ends the "
                "file; put the marker on the last hunk"
            )
        if start is None:
            reports.append(_report(hunk, reason=reason))
            continue
        hint = _hint(hunk)
        reports.append(_report(hunk, line=start + 1, offset=start - hint))
        placed.append((hunk, start))
        previous_end, previous_index = start + len(hunk.old_lines), hunk.index
    if len(placed) < len(hunks):
        return PatchOutcome(text=None, reports=reports)

    result = list(lines)
    for hunk, start in reversed(placed):
        result[start : start + len(hunk.old_lines)] = _splice(hunk, lines, start, newline)
    final_newline = has_final_newline
    for hunk in hunks:
        if hunk.at_eof:
            final_newline = not hunk.new_no_newline
    text = "".join(_terminate(result, final_newline, newline))
    return PatchOutcome(text=text, reports=reports)


def _is_file_header(rows: list[str], i: int) -> bool:
    """A ``---`` row starts a file header only when ``+++`` and a hunk follow.

    Without this check, removing a line such as ``-- comment`` (body row
    ``--- comment``) would read as a second file.
    """
    return (
        rows[i].startswith("--- ")
        and i + 2 < len(rows)
        and rows[i + 1].startswith("+++ ")
        and rows[i + 2].startswith("@@ -")
    )


def _body_end(rows: list[str], start: int) -> int:
    """Index of the first row after the hunk body that begins at ``start``."""
    end = start
    while end < len(rows):
        row = rows[end]
        if row.startswith(("@@", "diff ")) or _is_file_header(rows, end):
            break
        end += 1
    while end > start and rows[end - 1] == "":
        end -= 1  # trailing blank lines are separators, not empty context
    return end


def _parse_hunk(index: int, header: re.Match[str], rows: list[str], start: int, end: int) -> Hunk:
    ops: list[Op] = []
    no_newline = {"-": False, "+": False}
    for number in range(start, end):
        row = rows[number]
        tag, text = (" ", "") if row == "" else (row[0], row[1:])
        if tag == _NO_NEWLINE:
            if not ops:
                raise InvalidArgumentError(
                    f"Line {number + 1} of the patch: '\\ No newline' needs a line before it."
                )
            previous = ops[-1][0]
            for side in ("-", "+") if previous == " " else (previous,):
                no_newline[side] = True
            continue
        if tag not in " -+":
            raise InvalidArgumentError(
                f"Line {number + 1} of the patch (hunk {index}) starts with {tag!r}; hunk "
                "lines start with ' ', '-', '+' or '\\'."
            )
        sides = ("-", "+") if tag == " " else (tag,)
        if any(no_newline[side] for side in sides):
            raise InvalidArgumentError(
                f"Hunk {index}: '\\ No newline at end of file' must follow the last line."
            )
        ops.append((tag, text))
    if not ops:
        raise InvalidArgumentError(f"Hunk {index} is empty.")
    return Hunk(
        index=index,
        old_start=int(header.group(1)),
        new_start=int(header.group(2)),
        ops=tuple(ops),
        old_no_newline=no_newline["-"],
        new_no_newline=no_newline["+"],
    )


def _hint(hunk: Hunk) -> int:
    """0-based index where the hunk's old block is expected to start.

    For a pure insertion (``@@ -N,0``) the header names the line *after which* the
    new lines go, so the block starts at index ``N``.
    """
    if not hunk.old_lines:
        return hunk.old_start
    return max(hunk.old_start - 1, 0)


def _locate(stripped: list[str], hunk: Hunk, has_final_newline: bool) -> tuple[int | None, str]:
    """Find where the hunk applies: ``(start, "")`` or ``(None, reason)``."""
    old = hunk.old_lines
    if not old:
        return _locate_insertion(len(stripped), hunk)
    hint = _hint(hunk)
    if hunk.at_eof and hunk.old_no_newline == has_final_newline:
        return None, "the end-of-file newline does not match the file"
    width = len(old)
    candidates = {
        position
        for position in range(len(stripped) - width + 1)
        if stripped[position] == old[0] and stripped[position : position + width] == old
    }
    if hunk.at_eof:
        candidates = {p for p in candidates if p + width == len(stripped)}
    if not candidates:
        where = " at the end of the file" if hunk.at_eof else ""
        return None, f"context not found{where}"
    nearest = min(candidates, key=lambda p: (abs(p - hint), p))
    twin = hint - (nearest - hint)
    if twin != nearest and twin in candidates:
        first, second = sorted((nearest, twin))
        return None, f"ambiguous: matches at lines {first + 1} and {second + 1}"
    return nearest, ""


def _locate_insertion(line_count: int, hunk: Hunk) -> tuple[int | None, str]:
    """Place a pure insertion at its header's line; it has no context to search for.

    A marker ties the insertion to the end of the file. It is not moved there, because
    the header and the marker contradict each other and either could be the mistake.
    """
    hint = _hint(hunk)
    if hint > line_count:
        return None, f"insertion point is past the end of the file ({line_count} lines)"
    if hunk.at_eof and hint < line_count:
        return None, (
            "a '\\ No newline' marker ties an insertion to the end of the file; "
            f"use '@@ -{line_count},0' to append, or drop the marker"
        )
    return hint, ""


def _splice(hunk: Hunk, lines: list[str], start: int, newline: str) -> list[str]:
    """The hunk's new lines.

    Context lines keep the file's own terminator; added lines get ``newline``.
    """
    out: list[str] = []
    position = start
    for tag, text in hunk.ops:
        if tag == " ":
            out.append(lines[position])
            position += 1
        elif tag == "-":
            position += 1
        else:
            out.append(text + newline)
    return out


def _terminate(lines: list[str], final_newline: bool, newline: str) -> list[str]:
    """Give every line but the last a terminator, and set the last one's as required.

    Only the file's original last line can lack a terminator; once lines follow it (an
    insertion at the end), it needs one. The last line ends with a terminator whenever
    ``final_newline`` holds: it is then either an original line that had one or an
    added line.
    """
    for number, line in enumerate(lines[:-1]):
        if strip_eol(line) == line:
            lines[number] = line + newline
    if lines and not final_newline:
        lines[-1] = strip_eol(lines[-1])
    return lines


def _report(hunk: Hunk, *, line: int = 0, offset: int = 0, reason: str = "") -> HunkReport:
    return HunkReport(
        index=hunk.index,
        header=hunk.header,
        applied=not reason,
        line=line,
        offset=offset,
        reason=reason,
    )
