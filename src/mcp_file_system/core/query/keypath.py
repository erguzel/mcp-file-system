"""Key paths such as ``jobs.test.steps[0].uses``, in the outline's qualified-name syntax.

Keys are separated by ``.``; element indexes are written ``[i]`` (``[-1]`` is the last
element). A bare key may contain any character except ``.``, ``[`` and ``"``, so
``paths./users.get`` works; other keys are written as JSON strings: ``"a.b"``.
"""

import json
import re

from mcp_file_system.core.outline.treesitter._base import join_index, join_key
from mcp_file_system.errors import InvalidArgumentError

Segment = str | int

_SYNTAX_HINT = (
    "Use keys separated by '.', indexes like [0] or [-1], and JSON quotes for keys "
    'containing dots or brackets, e.g. tool.ruff, steps[0].uses, "a.b".c'
)
_DECODER = json.JSONDecoder()
_INDEX = re.compile(r"\[(-?[0-9]{1,9})\]")


def parse_key_path(text: str) -> list[Segment]:
    """Split a key path into keys (``str``) and indexes (``int``); ``""`` is the root.

    Raises:
        InvalidArgumentError: The path is malformed.
    """
    segments: list[Segment] = []
    position, length = 0, len(text)
    while position < length:
        char = text[position]
        if char == "[":
            match = _INDEX.match(text, position)
            if match is None:
                raise _error(text, f"invalid index at position {position}")
            segments.append(int(match[1]))
            position = match.end()
        else:
            if segments:
                if char != ".":
                    raise _error(text, f"expected '.' or '[' at position {position}")
                position += 1
            key, position = _key(text, position)
            segments.append(key)
    return segments


def _key(text: str, position: int) -> tuple[str, int]:
    """Read one key starting at ``position``; return it and the position after it."""
    if position < len(text) and text[position] == '"':
        try:
            key, end = _DECODER.raw_decode(text, position)
        except ValueError:
            raise _error(text, f"unterminated or invalid quoted key at {position}") from None
        return str(key), end
    end = position
    while end < len(text) and text[end] not in '.["':
        end += 1
    if end == position:
        raise _error(text, f"empty key at position {position}")
    return text[position:end], end


def _error(text: str, problem: str) -> InvalidArgumentError:
    return InvalidArgumentError(f"Invalid key path {text!r}: {problem}. {_SYNTAX_HINT}.")


def format_key_path(segments: list[Segment]) -> str:
    """Render segments in canonical form (bare keys where possible, else quoted)."""
    path = ""
    for segment in segments:
        path = join_index(path, segment) if isinstance(segment, int) else join_key(path, segment)
    return path
