"""Optional audit log: one JSON line per tool call.

The log is for the operator, never for the model: nothing here changes tool results or
tool definitions, and the log file must live outside the allowed roots (enforced by
:class:`~mcp_file_system.config.Settings`), so an agent can neither read nor tamper
with it through this server.

File contents are never written. Arguments that carry file content (replacement text,
new lines, whole files) are reduced to their length.
"""

import functools
import inspect
import json
import logging
import threading
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, TextContent
from pydantic import BaseModel

logger = logging.getLogger("mcp_file_system.audit")

CONTENT_ARGUMENTS: Mapping[str, frozenset[str]] = {
    "replace_in_file": frozenset({"pattern", "replacement"}),
    "multi_replace": frozenset({"edits"}),
    "edit_lines": frozenset({"new_text"}),
    "apply_patch": frozenset({"diff"}),
    "write_file": frozenset({"content"}),
}
"""Per tool, the arguments that contain file content and are logged as a length only."""

MAX_ARGUMENT_CHARS = 500
"""Longer string arguments (e.g. huge patterns) are cut in the log."""


class AuditLog:
    """Appends audit records to a JSON Lines file.

    The file is opened per record, which keeps no handle open, survives external log
    rotation, and costs microseconds next to the file I/O of a tool call. Writes are
    serialized with a lock because tools run in worker threads. A failing write never
    fails the tool call; it is reported on stderr (once, until writes succeed again).
    """

    def __init__(
        self,
        path: Path,
        *,
        profile: str,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """Create the log, verifying up front that ``path`` is writable.

        Raises:
            OSError: The file or its parent directory cannot be created or appended to.
        """
        self.path = path
        self._profile = profile
        self._clock = clock
        self._lock = threading.Lock()
        self._failing = False
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8"):
            pass

    def record(
        self,
        *,
        tool: str,
        arguments: Mapping[str, Any],
        duration_ms: float,
        result_chars: int | None,
        truncated: bool | None,
        error: Mapping[str, str] | None,
    ) -> None:
        """Append one record. Never raises."""
        entry = {
            "ts": self._clock().isoformat(timespec="seconds").replace("+00:00", "Z"),
            "tool": tool,
            "profile": self._profile,
            "args": sanitize_arguments(tool, arguments),
            "duration_ms": round(duration_ms, 1),
            "result_chars": result_chars,
            "truncated": truncated,
            "error": dict(error) if error else None,
        }
        line = json.dumps(entry, ensure_ascii=False, default=str) + "\n"
        with self._lock:
            try:
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(line)
            except OSError as exc:
                if not self._failing:
                    logger.warning("cannot write audit log %s: %s", self.path, exc)
                self._failing = True
            else:
                self._failing = False

    def wrap[F: Callable[..., Any]](self, name: str, fn: F) -> F:
        """Return ``fn`` wrapped so that every call is recorded under ``name``.

        ``fn`` is a tool function after error translation, so anticipated failures
        arrive as :class:`ToolError` whose cause is the domain error.
        """
        signature = inspect.signature(fn)

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            bound = signature.bind_partial(*args, **kwargs)
            bound.apply_defaults()
            started = time.perf_counter()
            result: Any = None
            error: dict[str, str] | None = None
            try:
                result = fn(*args, **kwargs)
            except ToolError as exc:
                cause = exc.__cause__
                error = {
                    "type": type(cause).__name__ if cause else type(exc).__name__,
                    "message": str(cause or exc)[:MAX_ARGUMENT_CHARS],
                }
                raise
            except Exception as exc:
                error = {"type": type(exc).__name__, "message": "unexpected error"}
                raise
            finally:
                chars, truncated = _measure(result)
                self.record(
                    tool=name,
                    arguments=bound.arguments,
                    duration_ms=(time.perf_counter() - started) * 1000,
                    result_chars=chars,
                    truncated=truncated,
                    error=error,
                )
            return result

        return wrapper  # type: ignore[return-value]


def sanitize_arguments(tool: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Prepare arguments for the log: content as length only, long strings cut."""
    content = CONTENT_ARGUMENTS.get(tool, frozenset())
    sanitized: dict[str, Any] = {}
    for key, value in arguments.items():
        if key in content:
            sanitized[key] = _content_size(value)
        elif isinstance(value, str) and len(value) > MAX_ARGUMENT_CHARS:
            sanitized[key] = value[:MAX_ARGUMENT_CHARS] + "…"
        else:
            sanitized[key] = value
    return sanitized


def _content_size(value: Any) -> dict[str, int]:
    """The size of a content-carrying argument, never the content itself.

    Lists (e.g. multi_replace's ``edits``) also report their length. The SDK hands
    validated arguments over as pydantic objects, whose ``str()`` would print every
    field, so text is only ever counted.
    """
    if isinstance(value, list | tuple):
        return {"items": len(value), "chars": _text_chars(value)}
    return {"chars": _text_chars(value)}


def _text_chars(value: Any) -> int:
    """Total length of the strings inside ``value`` (models, mappings and sequences)."""
    if isinstance(value, str):
        return len(value)
    if isinstance(value, BaseModel):
        value = value.model_dump()
    if isinstance(value, Mapping):
        return sum(_text_chars(item) for item in value.values())
    if isinstance(value, list | tuple):
        return sum(_text_chars(item) for item in value)
    return 0


def _measure(result: Any) -> tuple[int | None, bool | None]:
    if not isinstance(result, CallToolResult):
        return None, None
    chars = sum(len(block.text) for block in result.content if isinstance(block, TextContent))
    structured = result.structured_content or {}
    truncated = structured.get("truncated")
    return chars, truncated if isinstance(truncated, bool) else None
