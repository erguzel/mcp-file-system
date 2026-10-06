"""Compact, depth-limited text view of a queried value.

Each row is ``<key>: <value>`` with keys written exactly as in key paths (``[i]`` for
elements), so any row can be turned into a follow-up query by joining the keys above
it. Containers beyond the depth limit collapse to ``{3 keys}`` or ``[40 items]``.
"""

from collections.abc import Iterator
from dataclasses import dataclass

from mcp_file_system.core.outline.treesitter._base import format_key, join_index, join_key
from mcp_file_system.core.query.nodes import DataNode
from mcp_file_system.core.render import clip_line


@dataclass(slots=True)
class RenderedValue:
    """The text view of a value and what was left out of it."""

    text: str
    truncated: bool
    """The output budget ran out before every row was written."""
    first_collapsed: str | None
    """Key path of the first container collapsed by the depth limit, if any."""


def collapsed(node: DataNode) -> str:
    """One-line stand-in for a container: ``{2 keys}``, ``[1 item]``, ``{}`` or ``[]``."""
    size = node.size()
    if node.kind == "object":
        return f"{{{size} key{'' if size == 1 else 's'}}}" if size else "{}"
    return f"[{size} item{'' if size == 1 else 's'}]" if size else "[]"


def render_value(
    node: DataNode, *, path: str, depth: int, max_chars: int, max_line_chars: int
) -> RenderedValue:
    """Render ``node`` (found at key path ``path``) down to ``depth`` levels of children.

    Rows are added until ``max_chars`` would be exceeded; single rows are clipped to
    ``max_line_chars``.
    """
    collapsed_paths: list[str] = []
    if node.kind == "value":
        rows: Iterator[str] = iter([node.scalar()])
    elif node.size() == 0:
        rows = iter([collapsed(node)])
    else:
        rows = _rows(node, path, depth, 0, collapsed_paths)
    kept: list[str] = []
    used = 0
    truncated = False
    for row in rows:
        line = clip_line(row, max_line_chars)
        if used + len(line) + 1 > max_chars:
            truncated = True
            break
        kept.append(line)
        used += len(line) + 1
    return RenderedValue(
        text="\n".join(kept),
        truncated=truncated,
        first_collapsed=collapsed_paths[0] if collapsed_paths else None,
    )


def _rows(
    node: DataNode, path: str, levels: int, indent: int, collapsed_paths: list[str]
) -> Iterator[str]:
    """Rows for the children of a non-empty container, recursing ``levels - 1`` deeper."""
    if node.kind == "object":
        children = [(format_key(k), join_key(path, k), v) for k, v in node.entries()]
    else:
        children = [(f"[{i}]", join_index(path, i), v) for i, v in enumerate(node.elements())]
    pad = "  " * indent
    for label, child_path, child in children:
        if child.kind == "value":
            yield f"{pad}{label}: {child.scalar()}"
        elif levels <= 1 or child.size() == 0:
            if child.size():
                collapsed_paths.append(child_path)
            yield f"{pad}{label}: {collapsed(child)}"
        else:
            yield f"{pad}{label}:"
            yield from _rows(child, child_path, levels - 1, indent + 1, collapsed_paths)
