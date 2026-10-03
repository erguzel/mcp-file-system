"""Markdown outlines: ATX headings and the sections they introduce."""

import re
from dataclasses import dataclass

from mcp_file_system.core.models import Symbol
from mcp_file_system.core.textio import split_lines

QUALIFIED_SEPARATOR = " > "

_ATX = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_FRONT_MATTER_END = frozenset({"---", "..."})


@dataclass(frozen=True, slots=True)
class _Heading:
    level: int
    title: str
    line: int


class MarkdownOutlineProvider:
    """Lists ATX headings (``#`` ... ``######``).

    A heading's section ends right before the next heading of the same or a higher
    level. Headings inside fenced code blocks and YAML front matter are ignored.
    Qualified names join ancestor titles with ``" > "``, e.g. ``"Usage > Install"``.
    """

    language = "markdown"
    extensions = frozenset({".md", ".markdown", ".mdx"})

    def outline(self, text: str) -> list[Symbol]:
        """Return the document's headings as symbols."""
        lines = split_lines(text)
        headings = _scan_headings(lines)
        symbols: list[Symbol] = []
        stack: list[_Heading] = []
        for index, heading in enumerate(headings):
            while stack and stack[-1].level >= heading.level:
                stack.pop()
            end = next(
                (h.line - 1 for h in headings[index + 1 :] if h.level <= heading.level),
                len(lines),
            )
            qualified = QUALIFIED_SEPARATOR.join([*(h.title for h in stack), heading.title])
            symbols.append(
                Symbol(
                    name=heading.title,
                    qualified_name=qualified,
                    kind=f"h{heading.level}",
                    start_line=heading.line,
                    end_line=end,
                    depth=len(stack),
                    signature=f"{'#' * heading.level} {heading.title}",
                )
            )
            stack.append(heading)
        return symbols


def _scan_headings(lines: list[str]) -> list[_Heading]:
    headings: list[_Heading] = []
    fence: str | None = None
    start = _front_matter_length(lines)
    for number, line in enumerate(lines[start:], start=start + 1):
        fence_match = _FENCE.match(line)
        if fence is not None:
            if (
                fence_match
                and fence_match.group(1)[0] == fence[0]
                and len(fence_match.group(1)) >= len(fence)
            ):
                fence = None
            continue
        if fence_match:
            fence = fence_match.group(1)
            continue
        heading = _ATX.match(line)
        if heading and (title := (heading.group(2) or "").strip()):
            headings.append(_Heading(len(heading.group(1)), title, number))
    return headings


def _front_matter_length(lines: list[str]) -> int:
    """Number of lines occupied by leading YAML front matter (0 if none)."""
    if not lines or lines[0].strip() != "---":
        return 0
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() in _FRONT_MATTER_END:
            return index + 1
    return 0
