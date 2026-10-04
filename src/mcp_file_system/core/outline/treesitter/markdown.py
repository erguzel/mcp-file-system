"""Markdown outlines with the tree-sitter Markdown grammar.

Compared with line patterns, the grammar also recognizes setext headings (underlined
with ``===`` or ``---``) and does not mistake lines inside HTML blocks, indented code or
fenced code for headings. Headings inside block quotes and lists are not sections and
are skipped.
"""

import re

import tree_sitter_markdown
from tree_sitter import Node

from mcp_file_system.core.models import Symbol
from mcp_file_system.core.outline.markdown import Heading, symbols_from_headings
from mcp_file_system.core.outline.treesitter._base import (
    TreeSitterOutlineProvider,
    end_line,
    node_text,
    start_line,
)

_CLOSING_HASHES = re.compile(r"(?:^|[ \t]+)#+[ \t]*$")
_LEVELS = {
    **{f"atx_h{level}_marker": level for level in range(1, 7)},
    "setext_h1_underline": 1,
    "setext_h2_underline": 2,
}


class TreeSitterMarkdownOutlineProvider(TreeSitterOutlineProvider):
    """Lists ATX and setext headings and the sections they introduce."""

    language = "markdown"
    extensions = frozenset({".md", ".markdown", ".mdx"})

    def _grammar(self) -> object:
        return tree_sitter_markdown.language()

    def _extract(self, root: Node) -> list[Symbol]:
        headings: list[Heading] = []
        _collect(root, headings)
        total = end_line(root) if root.end_byte else 0
        return symbols_from_headings(headings, total)


def _collect(node: Node, headings: list[Heading]) -> None:
    for child in node.named_children:
        if child.type == "section":
            _collect(child, headings)
        elif child.type in {"atx_heading", "setext_heading"}:
            heading = _heading(child)
            if heading is not None:
                headings.append(heading)


def _heading(node: Node) -> Heading | None:
    level = next((_LEVELS[c.type] for c in node.children if c.type in _LEVELS), None)
    content = node.child_by_field_name("heading_content")
    if level is None or content is None:
        return None
    title = " ".join(node_text(content).split())
    if node.type == "atx_heading":
        title = _CLOSING_HASHES.sub("", title).strip()
    return Heading(level, title, start_line(node)) if title else None
