"""Markdown outlines: headings and the sections they introduce.

This module holds the parser-independent section logic (:func:`symbols_from_headings`)
and a regex-based provider. The default registry prefers the tree-sitter provider, which
also understands setext headings and HTML blocks; the regex provider is the fallback
when the grammar is unavailable.
"""

import re
from dataclasses import dataclass

from mcp_file_system.core.models import Symbol
from mcp_file_system.core.textio import split_lines

QUALIFIED_SEPARATOR = " > "

_ATX = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_FRONT_MATTER_END = frozenset({"---", "..."})


@dataclass(frozen=True, slots=True)
class Heading:
    """A heading found by a Markdown parser."""

    level: int
    title: str
    line: int


def symbols_from_headings(headings: list[Heading], total_lines: int) -> list[Symbol]:
    """Turn headings (in document order) into section symbols.

    A heading's section ends right before the next heading of the same or a higher
    level. Qualified names join ancestor titles with ``" > "``, e.g.
    ``"Usage > Install"``.
    """
    symbols: list[Symbol] = []
    stack: list[Heading] = []
    for index, heading in enumerate(headings):
        while stack and stack[-1].level >= heading.level:
            stack.pop()
        end = next(
            (h.line - 1 for h in headings[index + 1 :] if h.level <= heading.level),
            total_lines,
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


class RegexMarkdownOutlineProvider:
    """Lists ATX headings (``#`` ... ``######``) using line patterns.

    Headings inside fenced code blocks and YAML front matter are ignored. Setext
    headings and HTML blocks are not understood; see the tree-sitter provider.
    """

    language = "markdown"
    extensions = frozenset({".md", ".markdown", ".mdx"})

    def outline(self, text: str) -> list[Symbol]:
        """Return the document's headings as symbols."""
        lines = split_lines(text)
        return symbols_from_headings(_scan_headings(lines), len(lines))


def _scan_headings(lines: list[str]) -> list[Heading]:
    headings: list[Heading] = []
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
            headings.append(Heading(len(heading.group(1)), title, number))
    return headings


def _front_matter_length(lines: list[str]) -> int:
    """Number of lines occupied by leading YAML front matter (0 if none)."""
    if not lines or lines[0].strip() != "---":
        return 0
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() in _FRONT_MATTER_END:
            return index + 1
    return 0
