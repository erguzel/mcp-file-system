"""Optional audit log: one JSON line per tool call.

The log is for the operator, never for the model: nothing here changes tool results or
tool definitions, and the log file must live outside the allowed roots (enforced by
:class:`~mcp_file_system.config.Settings`), so an agent can neither read nor tamper
with it through this server.

Arguments that carry file content (replacement text, new lines, whole files) are reduced
to their length. Error messages are kept (cut to ``MAX_ARGUMENT_CHARS``): for domain
errors the agent-facing text, which can name keys or symbols of a file, and for
filesystem errors the full OS message, including paths the agent is not shown. A failing
range of ``read_ranges`` does not fail the call, so its record has no error.

Calls the SDK rejects before the tool runs (arguments that fail the input schema, unknown
tool names) are recorded too. Their arguments are raw, so only the declared scalar
arguments that passed validation are logged as is; everything else is a length.
"""

import json
import logging
import threading
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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
        plain: frozenset[str] | None = None,
    ) -> None:
        """Append one record. Never raises.

        ``plain`` is passed on to :func:`sanitize_arguments`; give it for arguments that
        were not validated.
        """
        entry = {
            "ts": self._clock().isoformat(timespec="seconds").replace("+00:00", "Z"),
            "tool": tool,
            "profile": self._profile,
            "args": sanitize_arguments(tool, arguments, plain=plain),
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


def sanitize_arguments(
    tool: str, arguments: Mapping[str, Any], *, plain: frozenset[str] | None = None
) -> dict[str, Any]:
    """Prepare arguments for the log: content as length only, long strings cut.

    Args:
        tool: Tool name, which selects the content arguments.
        arguments: Argument names and values.
        plain: For unvalidated arguments, the names whose values may be logged as is.
            Any other key, and any value that is not a scalar, is logged as its size:
            a misnamed or wrongly typed argument can hold file content.
    """
    content = CONTENT_ARGUMENTS.get(tool, frozenset())
    sanitized: dict[str, Any] = {}
    for key, value in arguments.items():
        if key in content or (plain is not None and not _is_plain(key, value, plain)):
            sanitized[key] = _content_size(value)
        elif isinstance(value, str) and len(value) > MAX_ARGUMENT_CHARS:
            sanitized[key] = value[:MAX_ARGUMENT_CHARS] + "…"
        else:
            sanitized[key] = value
    return sanitized


def _is_plain(key: str, value: Any, plain: frozenset[str]) -> bool:
    """Whether an unvalidated argument may be logged as is."""
    return key in plain and (value is None or isinstance(value, str | int | float))


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
