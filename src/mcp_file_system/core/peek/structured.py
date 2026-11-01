"""Shape summaries for JSON and TOML data and for package manifests (no values)."""

import json
import tomllib
from typing import Any

from tree_sitter import Node

from mcp_file_system.core.outline.treesitter._base import node_text, start_line
from mcp_file_system.core.outline.treesitter.structured import JsonOutlineProvider, unquote
from mcp_file_system.errors import ParseError

MAX_DEPTH = 4
MAX_KEYS = 30
MAX_SAMPLED_ITEMS = 200
MAX_NAMES = 40

_TYPE_NAMES = {bool: "bool", int: "int", float: "float", str: "str", type(None): "null"}


_JSON_TREE = JsonOutlineProvider()
_JSON_LITERALS = {"true": True, "false": False, "null": None}


def parse_json(text: str) -> Any:
    """Parse JSON, mapping every failure (including too deep nesting) to :class:`ParseError`.

    Strict JSON goes through :func:`json.loads`. Text it rejects is parsed once more with
    the tree-sitter grammar that ``outline`` and ``query`` use, so comments (``//`` and
    ``/* */``, as in ``.jsonc`` or ``tsconfig.json``) are accepted; anything the grammar
    flags as an error (for example a trailing comma) is still rejected.
    """
    try:
        return json.loads(text)
    except (ValueError, RecursionError) as exc:
        strict_error = f"invalid JSON ({type(exc).__name__}: {str(exc)[:120]})"
    try:
        return _parse_with_comments(text)
    except RecursionError:
        raise ParseError(strict_error) from None


def _parse_with_comments(text: str) -> Any:
    """Build Python values from the tree-sitter JSON tree, skipping comment nodes."""
    root, source = _JSON_TREE.parse(text)
    values = [child for child in root.named_children if child.type != "comment"]
    if root.has_error or not values:
        raise ParseError("invalid JSON (comments are allowed, syntax errors are not)")
    return _to_value(values[0], source)


def _to_value(node: Node, source: bytes) -> Any:
    """Turn a node of an error-free tree into a Python value.

    The grammar's strings and numbers are wider than JSON's (a raw control character, a
    bare ``\\u``, ``1.``), and an integer can be too long for Python to convert, so a token
    the grammar accepted can still fail here.

    Raises:
        ParseError: A string or number value cannot be decoded.
        RecursionError: The nesting is too deep for this walk; ``parse_json`` handles it.
    """
    if node.type == "object":
        pairs = (
            (pair.child_by_field_name("key"), pair.child_by_field_name("value"))
            for pair in node.named_children
            if pair.type == "pair"
        )
        return {
            unquote(node_text(key, source)): _to_value(value, source)
            for key, value in pairs
            if key is not None and value is not None
        }
    if node.type == "array":
        return [_to_value(c, source) for c in node.named_children if c.type != "comment"]
    if node.type in ("string", "number"):
        try:
            return json.loads(node_text(node, source))
        except ValueError:
            # The decoder's own position is relative to the token, not to the file.
            raise ParseError(
                f"the JSON {node.type} at line {start_line(node)} cannot be decoded"
            ) from None
    # In an error-free tree the only other value nodes are the three literals.
    return _JSON_LITERALS[node.type]


def parse_toml(text: str) -> dict[str, Any]:
    """Parse TOML, mapping failures to :class:`ParseError`."""
    try:
        return tomllib.loads(text)
    except (tomllib.TOMLDecodeError, RecursionError) as exc:
        raise ParseError(f"invalid TOML ({str(exc)[:120]})") from None


def describe(value: Any, depth: int = 0) -> str:
    """Describe the shape of ``value`` on one line, without any of its scalar values."""
    scalar = _TYPE_NAMES.get(type(value))
    if scalar is not None:
        return scalar
    if isinstance(value, dict):
        if not value:
            return "{}"
        if depth >= MAX_DEPTH:
            return f"{{...{len(value)} keys}}"
        return "{" + _fields(value, depth) + "}"
    if isinstance(value, list):
        return _describe_list(value, depth)
    return type(value).__name__  # TOML datetimes and similar


def _fields(value: dict[str, Any], depth: int) -> str:
    shown = [f"{key}: {describe(item, depth + 1)}" for key, item in list(value.items())[:MAX_KEYS]]
    if len(value) > MAX_KEYS:
        shown.append(f"...+{len(value) - MAX_KEYS} keys")
    return ", ".join(shown)


def _describe_list(value: list[Any], depth: int) -> str:
    if not value:
        return "array[0]"
    sample = value[:MAX_SAMPLED_ITEMS]
    if depth >= MAX_DEPTH:
        return f"array[{len(value)}]"
    if all(isinstance(item, dict) for item in sample):
        return f"array[{len(value)}] of {{{_merged_fields(sample, depth)}}}"
    kinds = list(dict.fromkeys(describe(item, depth + 1) for item in sample))
    return f"array[{len(value)}] of {' | '.join(kinds[:5])}{' | ...' if len(kinds) > 5 else ''}"


def _merged_fields(items: list[dict[str, Any]], depth: int) -> str:
    """Union of the keys of several objects; keys missing in some are marked ``?``."""
    types: dict[str, dict[str, None]] = {}
    seen: dict[str, int] = {}
    for item in items:
        for key, child in item.items():
            types.setdefault(key, {})[describe(child, depth + 2)] = None
            seen[key] = seen.get(key, 0) + 1
    shown = []
    for key in list(types)[:MAX_KEYS]:
        optional = "?" if seen[key] < len(items) else ""
        shown.append(f"{key}{optional}: {' | '.join(list(types[key])[:4])}")
    if len(types) > MAX_KEYS:
        shown.append(f"...+{len(types) - MAX_KEYS} keys")
    return ", ".join(shown)


def key_tree(value: Any) -> list[str]:
    """One row per top-level key (or a single row for a non-object document)."""
    if not isinstance(value, dict):
        return [describe(value)]
    rows = [f"{key}: {describe(item, 1)}" for key, item in list(value.items())[:MAX_KEYS]]
    if len(value) > MAX_KEYS:
        rows.append(f"... +{len(value) - MAX_KEYS} more keys")
    return rows or ["{} (empty object)"]


def _names(mapping: Any) -> str:
    if isinstance(mapping, dict):
        items = [str(key) if val in ("", "*") else f"{key} {val}" for key, val in mapping.items()]
    elif isinstance(mapping, list):
        items = [str(item) for item in mapping]
    else:
        return "(unrecognized)"
    shown = ", ".join(items[:MAX_NAMES])
    more = f", ...+{len(items) - MAX_NAMES}" if len(items) > MAX_NAMES else ""
    return f"{len(items)}: {shown}{more}" if items else "0"


def _scalar(value: Any) -> str:
    return str(value)[:80] if isinstance(value, str | int | float) else "-"


def _get(data: Any, *keys: str) -> Any:
    for key in keys:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def manifest_rows(name: str, data: Any) -> list[str]:
    """Summarize ``pyproject.toml``, ``package.json`` or ``Cargo.toml`` content."""
    if name == "pyproject.toml":
        rows = [
            f"name: {_scalar(_get(data, 'project', 'name'))}",
            f"version: {_scalar(_get(data, 'project', 'version'))}",
            f"requires-python: {_scalar(_get(data, 'project', 'requires-python'))}",
            f"dependencies ({_names(_get(data, 'project', 'dependencies') or [])})",
        ]
        extras = _get(data, "project", "optional-dependencies")
        if isinstance(extras, dict):
            for extra, deps in list(extras.items())[:10]:
                rows.append(f"extra {extra} ({_names(deps)})")
        tools = _get(data, "tool")
        if isinstance(tools, dict):
            rows.append(f"[tool.*]: {', '.join(list(tools)[:15])}")
        return rows
    if name == "package.json":
        rows = [
            f"name: {_scalar(_get(data, 'name'))}",
            f"version: {_scalar(_get(data, 'version'))}",
        ]
        for section in ("dependencies", "devDependencies", "peerDependencies"):
            if _get(data, section):
                rows.append(f"{section} ({_names(_get(data, section))})")
        scripts = _get(data, "scripts")
        if isinstance(scripts, dict):
            rows.append(f"scripts ({_names(list(scripts))})")
        return rows
    rows = [  # Cargo.toml
        f"name: {_scalar(_get(data, 'package', 'name'))}",
        f"version: {_scalar(_get(data, 'package', 'version'))}",
    ]
    for section in ("dependencies", "dev-dependencies", "build-dependencies"):
        if _get(data, section):
            rows.append(f"{section} ({_names(_get(data, section))})")
    return rows
