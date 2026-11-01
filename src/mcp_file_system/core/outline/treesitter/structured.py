"""Key-tree outlines for structured data files: JSON, TOML and YAML.

Every key becomes a symbol whose line span covers its value, so ``read_symbol`` can
return just one section (``tool.ruff.lint``, ``jobs.test.steps[0]``). Scalar values
are shown in signatures, which often answers a question without any further read.
"""

import json
import re

import tree_sitter_json
import tree_sitter_toml
import tree_sitter_yaml
from tree_sitter import Node

from mcp_file_system.core.models import Symbol
from mcp_file_system.core.outline.treesitter._base import (
    MAX_DEPTH,
    MAX_ITEMS_PER_ARRAY,
    SymbolCollector,
    TreeSitterOutlineProvider,
    clip_value,
    format_key,
    join_index,
    join_key,
    node_text,
)


def _describe(kind: str, count: int, text: str) -> str:
    if kind in {"object", "table"}:
        return f"{{{count} key{'' if count == 1 else 's'}}}"
    if kind == "array":
        return f"[{count} item{'' if count == 1 else 's'}]"
    return clip_value(text)


def unquote(text: str) -> str:
    """Decode a quoted key: JSON-style escapes for "...", literal for '...'."""
    if len(text) >= 2 and text[0] == text[-1] == "'":
        return text[1:-1].replace("''", "'")  # YAML single quotes double the quote
    try:
        decoded = json.loads(text)
    except ValueError:
        return text.strip("\"'")
    return decoded if isinstance(decoded, str) else text


# ---------------------------------------------------------------------------- JSON

_JSON_SCALARS = {
    "string": "string",
    "number": "number",
    "true": "boolean",
    "false": "boolean",
    "null": "null",
}
_JSON_PLUS_EXPONENT = re.compile(rb"(?<=[0-9][eE])\+(?=[0-9])")


class JsonOutlineProvider(TreeSitterOutlineProvider):
    """Outlines JSON (comments tolerated) as a tree of keys and container elements.

    The grammar has no ``+`` in a number's exponent: it reads ``1e+5`` as ``1`` and an error.
    Such a ``+`` is shown to it as ``-``, which keeps the token whole and every byte offset, so
    node text must be read from the original source. Inside a string or comment the swap changes
    nothing the grammar cares about, so it need not know where it is.
    """

    language = "json"
    extensions = frozenset({".json", ".jsonc"})

    def _grammar(self) -> object:
        return tree_sitter_json.language()

    def _prepare(self, source: bytes) -> bytes:
        return _JSON_PLUS_EXPONENT.sub(b"-", source)

    def _extract(self, root: Node, source: bytes) -> list[Symbol]:
        out = SymbolCollector()
        for value in root.named_children:
            self._children(value, source, "", 0, out)
        return out.symbols

    def _children(
        self, node: Node, source: bytes, path: str, depth: int, out: SymbolCollector
    ) -> None:
        if depth >= MAX_DEPTH:
            return
        if node.type == "object":
            for pair in node.named_children:
                key, value = pair.child_by_field_name("key"), pair.child_by_field_name("value")
                if pair.type != "pair" or key is None or value is None:
                    continue
                name = unquote(node_text(key, source))
                child_path = join_key(path, name)
                out.add(
                    path=child_path,
                    name=name,
                    kind=_json_kind(value),
                    node=pair,
                    depth=depth,
                    signature=f"{format_key(name)}: {_json_describe(value, source)}",
                )
                self._children(value, source, child_path, depth + 1, out)
        elif node.type == "array":
            elements = [c for c in node.named_children if c.type != "comment"]
            listed = 0
            for index, element in enumerate(elements):
                if element.type not in {"object", "array"}:
                    continue
                if listed >= MAX_ITEMS_PER_ARRAY:
                    break
                listed += 1
                child_path = join_index(path, index)
                out.add(
                    path=child_path,
                    name=f"[{index}]",
                    kind=element.type,
                    node=element,
                    depth=depth,
                    signature=f"[{index}]: {_json_describe(element, source)}",
                )
                self._children(element, source, child_path, depth + 1, out)


def _json_kind(value: Node) -> str:
    return (
        value.type if value.type in {"object", "array"} else _JSON_SCALARS.get(value.type, "value")
    )


def _json_describe(value: Node, source: bytes) -> str:
    entries = [c for c in value.named_children if c.type != "comment"]
    return _describe(_json_kind(value), len(entries), node_text(value, source))


# ---------------------------------------------------------------------------- TOML

_TOML_KEYS = {"bare_key", "quoted_key", "dotted_key"}


class TomlOutlineProvider(TreeSitterOutlineProvider):
    """Outlines TOML tables, array-of-tables elements and keys.

    A table's span also covers the sub-tables that directly follow it, so
    ``read_symbol("tool.ruff")`` includes ``[tool.ruff.lint]``. Array-of-tables
    elements are indexed: ``bin[0]``, ``bin[1]``.
    """

    language = "toml"
    extensions = frozenset({".toml"})

    def _grammar(self) -> object:
        return tree_sitter_toml.language()

    def _extract(self, root: Node, source: bytes) -> list[Symbol]:
        out = SymbolCollector()
        array_counts: dict[str, int] = {}
        for child in root.named_children:
            if child.type == "pair":
                self._pair(child, "", 0, out)
            elif child.type in {"table", "table_array_element"}:
                self._table(child, array_counts, out)
        return _extend_parent_tables(out.symbols)

    def _table(self, node: Node, array_counts: dict[str, int], out: SymbolCollector) -> None:
        key = next((c for c in node.named_children if c.type in _TOML_KEYS), None)
        if key is None:
            return
        parts = _toml_key_parts(key)
        header = ".".join(format_key(part) for part in parts)
        depth = len(parts) - 1
        if node.type == "table_array_element":
            index = array_counts.get(header, 0)
            array_counts[header] = index + 1
            path, name, kind = join_index(header, index), f"{parts[-1]}[{index}]", "array-table"
            signature = f"[[{node_text(key)}]]"
        else:
            path, name, kind, signature = header, parts[-1], "table", f"[{node_text(key)}]"
        out.add(path=path, name=name, kind=kind, node=node, depth=depth, signature=signature)
        for pair in node.named_children:
            if pair.type == "pair":
                self._pair(pair, path, depth + 1, out)

    def _pair(self, node: Node, path: str, depth: int, out: SymbolCollector) -> None:
        if depth >= MAX_DEPTH or node.named_child_count < 2:
            return
        key, value = node.named_children[0], node.named_children[-1]
        parts = _toml_key_parts(key)
        child_path = path
        for part in parts:
            child_path = join_key(child_path, part)
        kind = _toml_kind(value)
        out.add(
            path=child_path,
            name=parts[-1],
            kind=kind,
            node=node,
            depth=depth,
            signature=f"{node_text(key)} = {_toml_describe(value)}",
        )
        if value.type == "inline_table":
            for pair in value.named_children:
                if pair.type == "pair":
                    self._pair(pair, child_path, depth + 1, out)
        elif value.type == "array":
            self._array(value, child_path, depth + 1, out)

    def _array(self, node: Node, path: str, depth: int, out: SymbolCollector) -> None:
        elements = [c for c in node.named_children if c.type != "comment"]
        listed = 0
        for index, element in enumerate(elements):
            if element.type not in {"inline_table", "array"} or listed >= MAX_ITEMS_PER_ARRAY:
                continue
            listed += 1
            child_path = join_index(path, index)
            out.add(
                path=child_path,
                name=f"[{index}]",
                kind=_toml_kind(element),
                node=element,
                depth=depth,
                signature=f"[{index}]: {_toml_describe(element)}",
            )
            if element.type == "inline_table":
                for pair in element.named_children:
                    if pair.type == "pair":
                        self._pair(pair, child_path, depth + 1, out)
            else:
                self._array(element, child_path, depth + 1, out)


def _toml_key_parts(key: Node) -> list[str]:
    if key.type == "dotted_key":
        parts: list[str] = []
        for child in key.named_children:
            parts.extend(_toml_key_parts(child))
        return parts
    text = node_text(key)
    return [unquote(text) if key.type == "quoted_key" else text]


def _toml_kind(value: Node) -> str:
    if value.type == "inline_table":
        return "object"
    if value.type == "array":
        return "array"
    return "value"


def _toml_describe(value: Node) -> str:
    entries = [c for c in value.named_children if c.type != "comment"]
    return _describe(_toml_kind(value), len(entries), node_text(value))


def _extend_parent_tables(symbols: list[Symbol]) -> list[Symbol]:
    """Let each table's span include the sub-tables that directly follow it."""
    tables = [i for i, s in enumerate(symbols) if s.kind in {"table", "array-table"}]
    result = list(symbols)
    for position, index in enumerate(tables):
        prefix = symbols[index].qualified_name
        end = symbols[index].end_line
        for later in tables[position + 1 :]:
            name = symbols[later].qualified_name
            if not name.startswith((prefix + ".", prefix + "[")):
                break
            end = max(end, symbols[later].end_line)
        if end != symbols[index].end_line:
            result[index] = symbols[index].model_copy(update={"end_line": end})
    return result


# ---------------------------------------------------------------------------- YAML

_YAML_MAPPINGS = {"block_mapping", "flow_mapping"}
_YAML_SEQUENCES = {"block_sequence", "flow_sequence"}
_YAML_PAIRS = {"block_mapping_pair", "flow_pair"}


class YamlOutlineProvider(TreeSitterOutlineProvider):
    """Outlines YAML mappings and sequence elements.

    Multi-document streams are indexed by document: ``[0].name``, ``[1].kind``.
    Go/Helm template directives (``{{ ... }}``) are masked before parsing, so templated
    manifests outline like plain YAML; signatures still show the original directives.
    """

    language = "yaml"
    extensions = frozenset({".yaml", ".yml"})

    def _grammar(self) -> object:
        return tree_sitter_yaml.language()

    def _prepare(self, source: bytes) -> bytes:
        return mask_templates(source)

    def _extract(self, root: Node, source: bytes) -> list[Symbol]:
        out = SymbolCollector()
        documents = [d for d in root.named_children if d.type == "document"]
        multiple = len(documents) > 1
        for index, document in enumerate(documents):
            path, depth = "", 0
            if multiple:
                path, depth = join_index("", index), 1
                out.add(
                    path=path,
                    name=f"[{index}]",
                    kind="document",
                    node=document,
                    depth=0,
                    signature=f"--- document {index}",
                )
            for child in document.named_children:
                self._children(child, path, depth, source, out)
        return out.symbols

    def _children(
        self, node: Node, path: str, depth: int, source: bytes, out: SymbolCollector
    ) -> None:
        if depth >= MAX_DEPTH:
            return
        content = yaml_content(node)
        if content is None:
            return
        if content.type in _YAML_MAPPINGS:
            for pair in content.named_children:
                key = pair.child_by_field_name("key")
                if pair.type not in _YAML_PAIRS or key is None:
                    continue
                value = pair.child_by_field_name("value")
                name = yaml_key(key, source)
                child_path = join_key(path, name)
                described = _yaml_describe(value, source)
                out.add(
                    path=child_path,
                    name=name,
                    kind=_yaml_kind(value),
                    node=pair,
                    depth=depth,
                    signature=f"{format_key(name)}: {described}".rstrip(),
                )
                if value is not None:
                    self._children(value, child_path, depth + 1, source, out)
        elif content.type in _YAML_SEQUENCES:
            self._sequence(content, path, depth, source, out)

    def _sequence(
        self, node: Node, path: str, depth: int, source: bytes, out: SymbolCollector
    ) -> None:
        listed = 0
        for index, item in enumerate(c for c in node.named_children if c.type != "comment"):
            if item.type == "block_sequence_item" and item.named_child_count == 0:
                continue  # "- " with no value
            value = item.named_children[0] if item.type == "block_sequence_item" else item
            kind = _yaml_kind(value)
            if kind not in {"object", "array"} or listed >= MAX_ITEMS_PER_ARRAY:
                continue
            listed += 1
            child_path = join_index(path, index)
            out.add(
                path=child_path,
                name=f"[{index}]",
                kind=kind,
                node=item,
                depth=depth,
                signature=f"[{index}]: {_yaml_describe(value, source)}",
            )
            self._children(value, child_path, depth + 1, source, out)


_TEMPLATE = re.compile(rb"\{\{.*?\}\}", re.DOTALL)
_FILLER, _BLANK, _NEWLINE = ord("x"), ord(" "), ord("\n")


def mask_templates(source: bytes) -> bytes:
    """Hide Go/Helm template directives from the YAML parser, keeping byte offsets.

    Inline directives (``replicas: {{ .Values.n }}``) become ``x`` filler, which parses
    as a plain scalar. Lines made only of directives (``{{- if .Values.on }}``,
    ``{{- end }}``) become blank, so they do not break the indentation structure.
    Newlines are never touched, so line numbers stay exact.
    """
    if b"{{" not in source:
        return source
    masked = bytearray(source)
    hidden = bytearray(len(source))
    for match in _TEMPLATE.finditer(source):
        for index in range(match.start(), match.end()):
            if source[index] != _NEWLINE:
                masked[index] = _FILLER
                hidden[index] = 1
    start = 0
    while start <= len(source):
        end = source.find(b"\n", start)
        end = len(source) if end == -1 else end
        line = range(start, end)
        if any(hidden[i] for i in line) and all(hidden[i] or source[i] in b" \t" for i in line):
            for index in line:
                if hidden[index]:
                    masked[index] = _BLANK
        start = end + 1
    return bytes(masked)


def yaml_content(node: Node | None) -> Node | None:
    """Unwrap block/flow nodes (skipping anchors and tags) to the actual content node."""
    while node is not None and node.type in {"block_node", "flow_node"}:
        node = next((c for c in node.named_children if c.type not in {"anchor", "tag"}), None)
    return node


def _yaml_kind(value: Node | None) -> str:
    content = yaml_content(value)
    if content is None:
        return "value"
    if content.type in _YAML_MAPPINGS:
        return "object"
    if content.type in _YAML_SEQUENCES:
        return "array"
    return "value"


def _yaml_describe(value: Node | None, source: bytes) -> str:
    content = yaml_content(value)
    if content is None:
        return ""
    entries = [c for c in content.named_children if c.type != "comment"]
    return _describe(_yaml_kind(value), len(entries), node_text(content, source))


def yaml_key(key: Node, source: bytes) -> str:
    """The text of a mapping key, with quoted keys decoded (``"a.b"`` -> ``a.b``)."""
    content = yaml_content(key)
    text = node_text(content, source) if content is not None else node_text(key, source)
    if content is not None and content.type in {"double_quote_scalar", "single_quote_scalar"}:
        return unquote(text)
    return text


__all__ = [
    "JsonOutlineProvider",
    "TomlOutlineProvider",
    "YamlOutlineProvider",
    "mask_templates",
    "unquote",
    "yaml_content",
    "yaml_key",
]
