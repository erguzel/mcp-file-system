"""Application service behind the ``query`` tool."""

from mcp_file_system.config import Settings
from mcp_file_system.core.etag import compute_etag
from mcp_file_system.core.models import QueryResult
from mcp_file_system.core.outline.treesitter._base import format_key
from mcp_file_system.core.outline.treesitter.structured import (
    JsonOutlineProvider,
    YamlOutlineProvider,
)
from mcp_file_system.core.peek.structured import parse_toml
from mcp_file_system.core.query.keypath import Segment, format_key_path, parse_key_path
from mcp_file_system.core.query.nodes import DataNode, JsonNode, ValueNode, YamlNode
from mcp_file_system.core.query.render import render_value
from mcp_file_system.core.textio import check_size, read_text, require_text, split_lines
from mcp_file_system.errors import InvalidArgumentError, ParseError, SymbolNotFoundError
from mcp_file_system.security.paths import PathGuard

MAX_DEPTH = 10
"""Deepest ``depth`` a caller may ask for; deeper values are queried by key path."""

_LANGUAGES = {
    ".json": "json",
    ".jsonc": "json",
    ".toml": "toml",
    ".yaml": "yaml",
    ".yml": "yaml",
}
_MAX_LISTED_KEYS = 15


class DataQuery:
    """Returns one value of a structured file by key path, bounded in depth and size."""

    def __init__(self, guard: PathGuard, settings: Settings) -> None:
        self._guard = guard
        self._settings = settings
        self._json = JsonOutlineProvider()
        self._yaml = YamlOutlineProvider()

    def query(self, path: str, key_path: str = "", *, depth: int = 2) -> QueryResult:
        """Return the value at ``key_path`` (the whole document if empty).

        Raises:
            InvalidArgumentError: Unsupported file type, malformed key path, bad depth,
                or a key path that steps into the wrong kind of value.
            SymbolNotFoundError: A key or index along the path does not exist.
            ParseError: A TOML file is malformed, or a JSON file holds no value or several
                top-level values.
        """
        if not 1 <= depth <= MAX_DEPTH:
            raise InvalidArgumentError(f"depth must be between 1 and {MAX_DEPTH}.")
        segments = parse_key_path(key_path)
        resolved = self._guard.resolve_file(path)
        language = _LANGUAGES.get(resolved.path.suffix.lower())
        if language is None:
            raise InvalidArgumentError(
                f"query supports JSON, YAML and TOML files, not {resolved.display!r}. "
                "Use outline and read_symbol, or grep, instead."
            )
        stat = resolved.path.stat()
        check_size(resolved, stat.st_size, self._settings.max_file_bytes)
        text = read_text(resolved.path, require_text(resolved))
        try:
            root, damaged = self._parse(language, text)
        except ParseError as exc:
            raise ParseError(
                f"Cannot parse {resolved.display}: {exc}. Use read_lines or grep instead."
            ) from None
        node, walked = _walk(root, segments, resolved.display, damaged=damaged)
        canonical = format_key_path(walked)
        rendered = render_value(
            node,
            path=canonical,
            depth=depth,
            max_chars=self._settings.max_output_chars,
            max_line_chars=self._settings.max_line_chars,
        )
        start, end = node.lines() or (0, 0)
        return QueryResult(
            path=resolved.display,
            key_path=canonical,
            language=language,
            kind=node.kind,
            size=node.size(),
            start_line=start,
            end_line=end,
            total_lines=len(split_lines(text)),
            etag=compute_etag(stat),
            content=rendered.text,
            truncated=rendered.truncated,
            first_collapsed=rendered.first_collapsed,
            note=_damage_note(node, damaged=damaged),
        )

    def _parse(self, language: str, text: str) -> tuple[DataNode, bool]:
        """Parse ``text``; return the document's root value and whether it has syntax errors.

        Tree-sitter recovers from errors, so JSON and YAML files with errors still yield
        values; TOML files with errors are rejected by ``tomllib``. A JSON file without syntax
        errors but with several top-level values is a :class:`ParseError`, because returning
        only the first would pass it off as the whole document. A file with syntax errors is
        not counted: error recovery may split one broken value into several (``true+5``), so
        it yields its first value, which the caller marks as damaged.
        """
        if language == "toml":
            return ValueNode(parse_toml(text)), False
        if language == "yaml":
            root, source = self._yaml.parse(text)
            return YamlNode(root, source), root.has_error
        root, source = self._json.parse(text)
        values = [child for child in root.named_children if child.type != "comment"]
        if not values:
            raise ParseError("no JSON value found")
        if len(values) > 1 and not root.has_error:
            raise ParseError(
                f"it holds {len(values)} top-level JSON values (newline-delimited JSON, for "
                "example), and query reads one document"
            )
        return JsonNode(values[0], source), root.has_error


def _walk(
    root: DataNode, segments: list[Segment], display: str, *, damaged: bool
) -> tuple[DataNode, list[Segment]]:
    """Follow ``segments`` from ``root``; return the value and its canonical segments."""
    node = root
    walked: list[Segment] = []
    for segment in segments:
        here = format_key_path(walked) or "the document root"
        if isinstance(segment, str):
            child = node.child(segment) if node.kind == "object" else None
            if child is None:
                raise _missing_key(node, segment, here, display, damaged=damaged)
            node = child
            walked.append(segment)
            continue
        if node.kind != "array":
            hint = " Use .key to step into an object." if node.kind == "object" else ""
            raise InvalidArgumentError(
                f"{here} in {display} is {_describe(node)}; [{segment}] needs an array.{hint}"
            )
        elements = node.elements()
        index = segment + len(elements) if segment < 0 else segment
        if not 0 <= index < len(elements):
            count = len(elements)
            valid = f" (valid: 0..{count - 1} or -{count}..-1)" if count else ""
            raise SymbolNotFoundError(
                f"Index [{segment}] is out of range: {here} in {display} has "
                f"{count} item(s){valid}."
            )
        node = elements[index]
        walked.append(index)
    return node, walked


def _missing_key(node: DataNode, key: str, here: str, display: str, *, damaged: bool) -> Exception:
    """The error for a key that ``node`` does not have, listing what it does have."""
    if node.kind != "object":
        hint = " Use an index like [0]." if node.kind == "array" else ""
        return InvalidArgumentError(
            f"{here} in {display} is {_describe(node)}, so it has no key {format_key(key)}.{hint}"
        )
    keys = [format_key(name) for name, _ in node.entries()]
    listed = ", ".join(keys[:_MAX_LISTED_KEYS]) or "(none)"
    if len(keys) > _MAX_LISTED_KEYS:
        listed += f", ... (+{len(keys) - _MAX_LISTED_KEYS} more)"
    syntax = " The file has syntax errors, which may hide keys." if damaged else ""
    return SymbolNotFoundError(
        f"No key {format_key(key)} in {here} of {display}. Keys there: {listed}. "
        f'Write keys containing dots, brackets or double quotes in quotes, e.g. "a.b".{syntax}'
    )


def _describe(node: DataNode) -> str:
    if node.kind == "object":
        return "an object"
    if node.kind == "array":
        return f"an array of {node.size()} item(s)"
    return f"a scalar value ({node.scalar()[:40]})"


def _damage_note(node: DataNode, *, damaged: bool) -> str | None:
    if node.has_error:
        return "This value contains syntax errors and may be incomplete; check it with read_lines."
    if damaged:
        return "The file has syntax errors elsewhere; this value parsed cleanly."
    return None
