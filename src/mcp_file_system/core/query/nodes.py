"""Uniform, read-only views of JSON, YAML and TOML values for key-path queries.

JSON and YAML are viewed lazily over their tree-sitter syntax trees, so a query only
touches the part of the document it walks through and every value knows its line
span. TOML is viewed over the ``tomllib`` result instead, because a TOML table can be
spread over several sections (``[tool.ruff]``, ``[tool.ruff.lint]``) that only its
parser merges correctly.
"""

import json
from abc import ABC, abstractmethod
from datetime import date, datetime, time
from typing import Any, Literal

from tree_sitter import Node

from mcp_file_system.core.outline.treesitter._base import end_line, node_text, start_line
from mcp_file_system.core.outline.treesitter.structured import unquote, yaml_content, yaml_key

Kind = Literal["object", "array", "value"]


class DataNode(ABC):
    """One value of a structured document: an object, an array or a scalar."""

    @property
    @abstractmethod
    def kind(self) -> Kind:
        """Whether this is an ``object``, an ``array`` or a scalar ``value``."""

    @abstractmethod
    def entries(self) -> list[tuple[str, "DataNode"]]:
        """Key/value pairs of an object in document order (empty for other kinds)."""

    @abstractmethod
    def elements(self) -> list["DataNode"]:
        """Elements of an array (empty for other kinds)."""

    @abstractmethod
    def scalar(self) -> str:
        """The scalar as one line: strings JSON-quoted, other values as written."""

    def lines(self) -> tuple[int, int] | None:
        """1-based first and last line of the value, if the format tracks them."""
        return None

    @property
    def has_error(self) -> bool:
        """Whether the parser had to recover from syntax errors inside this value."""
        return False

    def size(self) -> int:
        """Number of keys or elements (0 for scalars)."""
        if self.kind == "object":
            return len(self.entries())
        if self.kind == "array":
            return len(self.elements())
        return 0

    def child(self, key: str) -> "DataNode | None":
        """The value of ``key`` in an object; the last one wins if a key repeats."""
        found = None
        for name, value in self.entries():
            if name == key:
                found = value
        return found


def _quote(text: str) -> str:
    return json.dumps(text, ensure_ascii=False)


# ---------------------------------------------------------------------------- JSON


class JsonNode(DataNode):
    """A JSON (or JSONC) value backed by a tree-sitter-json node."""

    def __init__(self, node: Node, source: bytes) -> None:
        self._node = node
        self._source = source

    @property
    def kind(self) -> Kind:  # noqa: D102
        if self._node.type == "object":
            return "object"
        if self._node.type == "array":
            return "array"
        return "value"

    def entries(self) -> list[tuple[str, DataNode]]:  # noqa: D102
        if self._node.type != "object":
            return []
        found: list[tuple[str, DataNode]] = []
        for pair in self._node.named_children:
            key, value = pair.child_by_field_name("key"), pair.child_by_field_name("value")
            if pair.type == "pair" and key is not None and value is not None:
                found.append((unquote(node_text(key, self._source)), JsonNode(value, self._source)))
        return found

    def elements(self) -> list[DataNode]:  # noqa: D102
        if self._node.type != "array":
            return []
        return [
            JsonNode(child, self._source)
            for child in self._node.named_children
            if child.type != "comment"
        ]

    def scalar(self) -> str:  # noqa: D102
        return node_text(self._node, self._source)

    def lines(self) -> tuple[int, int]:  # noqa: D102
        return start_line(self._node), end_line(self._node)

    @property
    def has_error(self) -> bool:  # noqa: D102
        return self._node.has_error


# ---------------------------------------------------------------------------- YAML

_MAPPINGS = {"block_mapping", "flow_mapping"}
_SEQUENCES = {"block_sequence", "flow_sequence"}
_PAIRS = {"block_mapping_pair", "flow_pair"}
_RAW_SCALARS = {"integer_scalar", "float_scalar", "boolean_scalar"}


class YamlNode(DataNode):
    """A YAML value backed by a tree-sitter-yaml node.

    ``node`` may be a ``block_node``/``flow_node`` wrapper, a ``document``, a
    ``stream`` with several documents (viewed as an array of documents, matching the
    outline's ``[0].kind`` names) or ``None`` for an empty value (``key:``), which is
    ``null``. Aliases (``*base``) are shown as written, not resolved, and merge keys
    (``<<``) are ordinary keys.
    """

    def __init__(self, node: Node | None, source: bytes) -> None:
        self._node = node
        self._source = source
        self._content = _yaml_unwrap(node)

    @property
    def kind(self) -> Kind:  # noqa: D102
        content = self._content
        if content is None:
            return "value"
        if content.type in _MAPPINGS:
            return "object"
        if content.type in _SEQUENCES or content.type == "stream":
            return "array"
        return "value"

    def entries(self) -> list[tuple[str, DataNode]]:  # noqa: D102
        if self.kind != "object" or self._content is None:
            return []
        found: list[tuple[str, DataNode]] = []
        for pair in self._content.named_children:
            key = pair.child_by_field_name("key")
            if pair.type not in _PAIRS or key is None:
                continue
            value = pair.child_by_field_name("value")
            found.append((yaml_key(key, self._source), YamlNode(value, self._source)))
        return found

    def elements(self) -> list[DataNode]:  # noqa: D102
        if self.kind != "array" or self._content is None:
            return []
        if self._content.type == "stream":
            documents = [c for c in self._content.named_children if c.type == "document"]
            return [YamlNode(document, self._source) for document in documents]
        found: list[DataNode] = []
        for item in self._content.named_children:
            if item.type == "comment":
                continue
            if item.type == "block_sequence_item":
                values = [c for c in item.named_children if c.type != "comment"]
                found.append(YamlNode(values[0] if values else None, self._source))
            else:
                found.append(YamlNode(item, self._source))
        return found

    def scalar(self) -> str:  # noqa: D102
        if self._content is None:
            return "null"
        return _yaml_scalar(self._content, node_text(self._content, self._source))

    def lines(self) -> tuple[int, int] | None:  # noqa: D102
        node = self._content or self._node
        return (start_line(node), end_line(node)) if node is not None else None

    @property
    def has_error(self) -> bool:  # noqa: D102
        return self._node is not None and self._node.has_error


def _yaml_unwrap(node: Node | None) -> Node | None:
    """Skip a ``document`` wrapper and block/flow wrappers down to the content node."""
    if node is not None and node.type == "stream":
        documents = [c for c in node.named_children if c.type == "document"]
        if len(documents) > 1:
            return node
        node = documents[0] if documents else None
    if node is not None and node.type == "document":
        node = next((c for c in node.named_children if c.type != "comment"), None)
    return yaml_content(node)


def _yaml_scalar(content: Node, text: str) -> str:
    """Render a YAML scalar node whose source text is ``text``."""
    kind = content.type
    if kind == "plain_scalar":
        typed = content.named_children[0].type if content.named_child_count else ""
        if typed == "null_scalar":
            return "null"
        # Numbers and booleans stay as written; multi-line plain strings fold to one line.
        return text if typed in _RAW_SCALARS else _quote(" ".join(text.split()))
    if kind in {"double_quote_scalar", "single_quote_scalar"}:
        return _quote(unquote(text))
    if kind == "block_scalar":
        return _quote(block_scalar_value(text))
    return text if kind == "alias" else _quote(text)


def block_scalar_value(text: str) -> str:
    """Approximate the value of a ``|`` or ``>`` block scalar from its source text.

    The body is dedented; literal (``|``) scalars keep their line breaks, folded
    (``>``) ones join lines with spaces and keep blank lines as breaks. Chomping
    ``-`` drops the final line break; keep (``+``) chomping and explicit indentation
    indicators are not interpreted.
    """
    header, _, body = text.partition("\n")
    indicator = header.split("#", 1)[0].strip()
    rows = body.rstrip().split("\n")  # trailing blank lines are chomped below
    indent = min((len(r) - len(r.lstrip(" ")) for r in rows if r.strip()), default=0)
    rows = [r[indent:] for r in rows]
    if indicator.startswith(">"):
        value = ""
        for row in rows:
            if not row:
                value += "\n"
            elif value and not value.endswith("\n"):
                value += " " + row
            else:
                value += row
    else:
        value = "\n".join(rows)
    return value if "-" in indicator else value + "\n"


# ---------------------------------------------------------------------------- TOML


class ValueNode(DataNode):
    """A parsed Python value (from ``tomllib``); no line information."""

    def __init__(self, value: Any) -> None:
        self._value = value

    @property
    def kind(self) -> Kind:  # noqa: D102
        if isinstance(self._value, dict):
            return "object"
        if isinstance(self._value, list):
            return "array"
        return "value"

    def entries(self) -> list[tuple[str, DataNode]]:  # noqa: D102
        if not isinstance(self._value, dict):
            return []
        return [(str(key), ValueNode(value)) for key, value in self._value.items()]

    def elements(self) -> list[DataNode]:  # noqa: D102
        if not isinstance(self._value, list):
            return []
        return [ValueNode(value) for value in self._value]

    def scalar(self) -> str:  # noqa: D102
        value = self._value
        if isinstance(value, str):
            return _quote(value)
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, datetime | date | time):
            return value.isoformat()
        return str(value)
