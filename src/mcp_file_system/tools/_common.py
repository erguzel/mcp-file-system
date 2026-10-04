"""Shared plumbing for tool adapters."""

import functools
import logging
from collections.abc import Callable
from typing import Annotated, Any

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import Field

from mcp_file_system.audit import AuditLog
from mcp_file_system.config import Settings
from mcp_file_system.core.models import ToolResult
from mcp_file_system.errors import FsError

logger = logging.getLogger("mcp_file_system.tools")

READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)
"""Annotations for tools that never modify anything."""

WRITES = ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=False)
"""Annotations for tools that modify files."""

PathArg = Annotated[
    str,
    Field(description="Path relative to the primary root, or absolute inside an allowed root."),
]
DirArg = Annotated[
    str,
    Field(description="Directory (or file) relative to the primary root, or absolute."),
]
IncludeHiddenArg = Annotated[
    bool, Field(description="Also walk dot-files and dot-directories (deny rules still apply).")
]

# Tool parameters avoid `X | None = None`: nullable types become `anyOf` schemas, which
# small models handle poorly. "Unset" is expressed with an empty string or 0 instead,
# and adapters translate these sentinels back to None for the services.


def unset_str(value: str) -> str | None:
    """Map the empty-string sentinel to ``None``."""
    return value or None


def unset_int(value: int) -> int | None:
    """Map the zero sentinel to ``None``."""
    return value or None


def to_call_result(result: ToolResult) -> CallToolResult:
    """Wrap a result model: compact text for the model, structured data for the client."""
    return CallToolResult(
        content=[TextContent(type="text", text=result.render())],
        structured_content=result.model_dump(mode="json"),
    )


def translate_errors[**P, R](fn: Callable[P, R]) -> Callable[P, R]:
    """Turn anticipated failures into MCP tool errors the agent can act on.

    Domain errors carry agent-facing messages. Unexpected ``OSError``s are reduced to
    their description so no stack trace or unrelated path is exposed. Anything else
    propagates and is reported by the SDK as a generic failure.
    """

    @functools.wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return fn(*args, **kwargs)
        except FsError as exc:
            raise ToolError(str(exc)) from exc
        except OSError as exc:
            logger.info("filesystem error in %s: %r", fn.__name__, exc)
            raise ToolError(f"Filesystem error: {exc.strerror or type(exc).__name__}") from exc

    return wrapper


class ToolRegistry:
    """Registers tool functions on a server, applying the cross-cutting settings.

    It is the single place that decides, per tool, whether the tool is exposed
    (``Settings.tools`` allowlist), whether it publishes an output schema
    (``Settings.emit_output_schema``), that errors are translated, and whether calls
    are recorded in the audit log.
    """

    def __init__(
        self, server: MCPServer[Any], settings: Settings, audit: AuditLog | None = None
    ) -> None:
        self._server = server
        self._settings = settings
        self._audit = audit
        self.offered: list[str] = []
        """Every tool name offered by the registrars that ran, allowed or not."""
        self.registered: list[str] = []
        """Tool names actually exposed to clients."""

    def tool[F: Callable[..., Any]](self, annotations: ToolAnnotations) -> Callable[[F], F]:
        """Decorator registering ``fn`` as a tool named after the function."""

        def decorator(fn: F) -> F:
            name = fn.__name__
            self.offered.append(name)
            if self._settings.tools and name not in self._settings.tools:
                return fn
            tool_fn: Callable[..., Any] = translate_errors(fn)
            if self._audit is not None:
                tool_fn = self._audit.wrap(name, tool_fn)
            self._server.add_tool(
                tool_fn,
                annotations=annotations,
                # None lets the SDK derive the schema from the return annotation.
                structured_output=None if self._settings.emit_output_schema else False,
            )
            self.registered.append(name)
            return fn

        return decorator
