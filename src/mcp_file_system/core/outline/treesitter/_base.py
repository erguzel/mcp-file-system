"""Shared machinery for outline providers built on tree-sitter grammars."""

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from tree_sitter import Language, Node, Parser

from mcp_file_system.core.models import Symbol

MAX_DEPTH = 16
"""Deeper keys are not listed, which keeps outlines of pathological files readable."""

MAX_ITEMS_PER_ARRAY = 100
"""Container elements listed per array/sequence (``key[0]``, ``key[1]``, ...)."""

MAX_VALUE_CHARS = 60
"""Scalar values shown in signatures are clipped to this length."""

_BARE_KEY = re.compile(r"[A-Za-z0-9_-]+")
_LINE_BREAK = re.compile(r"\r\n?")


class TreeSitterOutlineProvider(ABC):
    """Base class: parses text with one tree-sitter grammar and extracts symbols.

    The :class:`Language` is shared, but a fresh :class:`Parser` is created per call
    because parsers are not thread-safe and tools run in worker threads.
    """

    language: str
    extensions: frozenset[str]

    def __init__(self) -> None:
        self._language = Language(self._grammar())

    @abstractmethod
    def _grammar(self) -> object:
        """Return the grammar handle (e.g. ``tree_sitter_json.language()``)."""

    @abstractmethod
    def _extract(self, root: Node) -> list[Symbol]:
        """Turn the syntax tree into symbols in source order."""

    def outline(self, text: str) -> list[Symbol]:
        """Parse ``text`` and return its symbols.

        Tree-sitter recovers from syntax errors, so malformed input yields a best-effort
        outline instead of an error.
        """
        # Tree-sitter counts rows at "\n" only; normalize so rows match our line numbers.
        source = _LINE_BREAK.sub("\n", text).encode("utf-8", errors="replace")
        tree = Parser(self._language).parse(source)
        return self._extract(tree.root_node)


def start_line(node: Node) -> int:
    """1-based first line of ``node``."""
    return node.start_point.row + 1


def end_line(node: Node) -> int:
    """1-based last line of ``node`` (a node ending at column 0 ends on the line before)."""
    end = node.end_point
    if end.column == 0 and end.row > node.start_point.row:
        return end.row
    return end.row + 1


def node_text(node: Node) -> str:
    """Source text of ``node``."""
    return (node.text or b"").decode("utf-8", errors="replace")


def format_key(key: str) -> str:
    """Render one key for a qualified name, quoting keys that are not bare words."""
    return key if _BARE_KEY.fullmatch(key) else json.dumps(key, ensure_ascii=False)


def clip_value(text: str) -> str:
    """Single-line, length-limited rendering of a scalar value."""
    flat = " ".join(text.split())
    return flat if len(flat) <= MAX_VALUE_CHARS else flat[: MAX_VALUE_CHARS - 1] + "…"


@dataclass(slots=True)
class SymbolCollector:
    """Accumulates symbols for key/value documents (JSON, TOML, YAML).

    Qualified names join keys with ``.`` and append element indexes as ``[i]``, e.g.
    ``jobs.test.steps[0].uses``.
    """

    symbols: list[Symbol] = field(default_factory=list)

    def add(
        self,
        *,
        path: str,
        name: str,
        kind: str,
        node: Node,
        depth: int,
        signature: str,
    ) -> None:
        """Record one symbol spanning ``node``."""
        self.symbols.append(
            Symbol(
                name=name,
                qualified_name=path,
                kind=kind,
                start_line=start_line(node),
                end_line=end_line(node),
                depth=depth,
                signature=signature,
            )
        )


def join_key(parent: str, key: str) -> str:
    """Append a key to a qualified name."""
    rendered = format_key(key)
    return f"{parent}.{rendered}" if parent else rendered


def join_index(parent: str, index: int) -> str:
    """Append an element index to a qualified name."""
    return f"{parent}[{index}]"
