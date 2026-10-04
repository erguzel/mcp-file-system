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
# UTF-8 sequences of code points above U+00FF (lead bytes 0xC4 and up).
_WIDE_CHAR = re.compile(
    rb"[\xc4-\xdf][\x80-\xbf]|[\xe0-\xef][\x80-\xbf]{2}|[\xf0-\xf4][\x80-\xbf]{3}"
)


class TreeSitterOutlineProvider(ABC):
    """Base class: parses text with one tree-sitter grammar and extracts symbols.

    The :class:`Language` is shared, but a fresh :class:`Parser` is created per call
    because parsers are not thread-safe and tools run in worker threads.
    """

    language: str
    extensions: frozenset[str]
    masks_wide_chars: bool = False
    """Hide code points above U+00FF from the grammar (see :func:`mask_wide_chars`).

    Set this for grammars whose external scanner passes the lookahead character to the
    C ``<ctype.h>`` classifiers; symbol names are still read from the original source.
    """

    def __init__(self) -> None:
        self._language = Language(self._grammar())

    @abstractmethod
    def _grammar(self) -> object:
        """Return the grammar handle (e.g. ``tree_sitter_json.language()``)."""

    @abstractmethod
    def _extract(self, root: Node, source: bytes) -> list[Symbol]:
        """Turn the syntax tree into symbols in source order.

        ``source`` is the original text (before :meth:`_prepare`); read node text from it
        with ``node_text(node, source)``. Byte offsets are identical in both.
        """

    def _prepare(self, source: bytes) -> bytes:
        """Transform the source before parsing; must keep every byte offset unchanged."""
        return source

    def outline(self, text: str) -> list[Symbol]:
        """Parse ``text`` and return its symbols.

        Tree-sitter recovers from syntax errors, so malformed input yields a best-effort
        outline instead of an error.
        """
        root, source = self._parse(text)
        return self._extract(root, source)

    def _parse(self, text: str) -> tuple[Node, bytes]:
        """Parse ``text``; return the root node and the original (unprepared) source."""
        # Tree-sitter counts rows at "\n" only; normalize so rows match our line numbers.
        source = _LINE_BREAK.sub("\n", text).encode("utf-8", errors="replace")
        prepared = self._prepare(source)
        if self.masks_wide_chars:
            prepared = mask_wide_chars(prepared)
        if len(prepared) != len(source):  # pragma: no cover - programming error guard
            raise ValueError("_prepare must preserve byte offsets")
        tree = Parser(self._language).parse(prepared)
        return tree.root_node, source


def mask_wide_chars(source: bytes) -> bytes:
    """Replace each code point above U+00FF with ``x`` bytes of the same UTF-8 length.

    Some grammar scanners call ``isdigit()`` and friends on the raw lookahead code point.
    glibc implements these as a table lookup that is only defined for -128..255, so a
    wider character reads far outside the table and can crash the process (seen with
    tree-sitter-markdown 0.5.1 and tree-sitter-bash 0.25.1 on Linux). Byte offsets and
    line breaks are unchanged.
    """
    return _WIDE_CHAR.sub(lambda match: b"x" * len(match[0]), source)


BLOCK_BODIES = frozenset({"statement_block", "block", "constructor_body", "compound_statement"})


def block_folds(root: Node, function_types: frozenset[str]) -> list[tuple[int, int]]:
    """Interior lines of multi-line ``{ ... }`` bodies of the given function node types.

    The lines holding the opening and closing brace stay visible. Bodies nested inside
    a folded body are not visited.
    """
    folds: list[tuple[int, int]] = []
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type in function_types:
            body = node.child_by_field_name("body")
            if body is not None and body.type in BLOCK_BODIES:
                first_row, last_row = body.start_point.row, body.end_point.row
                if last_row - first_row >= 2:
                    folds.append((first_row + 2, last_row))  # 1-based interior lines
                    continue
        stack.extend(node.named_children)
    return folds


def start_line(node: Node) -> int:
    """1-based first line of ``node``."""
    return node.start_point.row + 1


def end_line(node: Node) -> int:
    """1-based last line of ``node`` (a node ending at column 0 ends on the line before)."""
    end = node.end_point
    if end.column == 0 and end.row > node.start_point.row:
        return end.row
    return end.row + 1


def node_text(node: Node, source: bytes | None = None) -> str:
    """Text of ``node``, taken from ``source`` when given (else from the parsed text)."""
    raw = source[node.start_byte : node.end_byte] if source is not None else node.text
    return (raw or b"").decode("utf-8", errors="replace")


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
