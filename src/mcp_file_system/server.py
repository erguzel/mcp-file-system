"""Server factory.

:func:`create_server` builds a fully configured :class:`MCPServer` independent of the
transport, so the same server can run over stdio today and be served over HTTP later
(``server.run("streamable-http", ...)``) without touching the tools. Serving several
sessions also needs per-session state, see ``docs/ARCHITECTURE.md``.
"""

import time
from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError
from mcp.types import CallToolResult, InputRequiredResult
from pydantic import ValidationError

from mcp_file_system import __version__
from mcp_file_system.audit import MAX_ARGUMENT_CHARS, AuditLog
from mcp_file_system.config import LogLevel, Settings
from mcp_file_system.context import AppContext
from mcp_file_system.tools import register_all
from mcp_file_system.tools._common import UNKNOWN_TOOL_ERROR, describe_rejection

SERVER_NAME = "mcp-file-system"


class FileSystemServer(MCPServer[Any]):
    """The MCP server, plus audit records for calls rejected before a tool runs.

    ``tools._common.audited`` records every call that reaches a tool function. The SDK
    rejects two kinds of calls before that point and only answers the client with an
    error: arguments that fail the input schema, and unknown tool names (including tools
    hidden by ``--allow-write`` or the ``tools`` allowlist). Both are recorded here.

    A validator that crashes with something other than a ``ValidationError`` would end
    in an ``UnexpectedToolError`` that neither place records; no tool argument model
    here has custom validators.
    """

    def __init__(
        self,
        *,
        name: str,
        version: str,
        instructions: str,
        log_level: LogLevel,
        audit: AuditLog | None,
    ) -> None:
        super().__init__(name=name, version=version, instructions=instructions, log_level=log_level)
        self._audit = audit

    async def call_tool(
        self, name: str, arguments: dict[str, Any], context: Context[Any, Any] | None = None
    ) -> CallToolResult | InputRequiredResult:
        """Call a tool; when the SDK rejects the call, record it before re-raising."""
        started = time.perf_counter()
        try:
            return await super().call_tool(name, arguments, context)
        except ToolError as exc:
            if self._audit is not None:
                await self._record_rejection(self._audit, name, arguments, exc, started)
            raise

    async def _record_rejection(
        self, audit: AuditLog, name: str, arguments: dict[str, Any], exc: ToolError, started: float
    ) -> None:
        """Record ``exc`` if the call never reached the tool function.

        Only a schema rejection or an unknown tool qualifies. ``Tool.run`` documents that
        every failure it raises has the original exception as ``__cause__``: a domain
        error's is the adapter's ``ToolError``, and a crash comes as
        ``UnexpectedToolError`` (even one caused by a ``ValidationError``). ``audited``
        already recorded those. Only the unknown-tool error, raised before any tool is
        found, has no cause.
        """
        cause = exc.__cause__
        if isinstance(cause, ValidationError) and not isinstance(exc, UnexpectedToolError):
            error, failed = describe_rejection(cause)
            schema = next(
                tool.input_schema for tool in await self.list_tools() if tool.name == name
            )
            plain = frozenset(schema.get("properties", {})) - failed
        elif cause is None:
            error, plain = UNKNOWN_TOOL_ERROR, frozenset[str]()
        else:
            return
        audit.record(
            tool=name[:MAX_ARGUMENT_CHARS],
            arguments=arguments,
            duration_ms=(time.perf_counter() - started) * 1000,
            result_chars=None,
            truncated=None,
            error=error,
            plain=plain,
        )


def build_instructions(settings: Settings) -> str:
    """Usage guidance sent to the client during initialization (profile-aware)."""
    roots = "\n".join(f"- {root}" for root in settings.roots)
    lite = settings.profile == "lite"
    read_tools = "read_lines or read_symbol" if lite else "read_lines, read_between or read_symbol"
    lines = [
        "Fine-grained filesystem access. Read only what you need instead of whole files.",
        f"Allowed roots (relative paths resolve against the first):\n{roots}",
        (
            "Workflow: list_directory/find_files to navigate; grep (output_mode='count' or "
            "'files_with_matches' first) to locate; file_info or outline to size up a file; "
            f"{read_tools} to read the relevant part."
        ),
        (
            "Every read returns an etag identifying the file version. To re-check lines "
            "you already have, pass that etag as if_changed_since instead of re-reading. "
            "grep hits show their enclosing symbol in [brackets]; read it with read_symbol."
        ),
    ]
    if not lite:
        lines.append(
            "For a line number from an error message or grep hit, use "
            "read_lines(around=N); for several known ranges, use read_ranges in one call."
        )
    if settings.allow_write:
        preview = "" if lite else "; use dry_run=true to preview a diff"
        lines.append(
            "Editing is enabled: prefer replace_in_file (unique match) or edit_lines (with "
            f"the etag of your latest read){preview}."
        )
        if not lite:
            lines.append(
                "For several changes to one file use multi_replace or apply_patch (unified "
                "diff); both apply all or nothing. undo_last_edit reverts this server's "
                "latest edit of a file."
            )
    else:
        lines.append("The server is read-only.")
    return "\n\n".join(lines)


def create_server(
    settings: Settings | None = None, *, context: AppContext | None = None
) -> MCPServer[Any]:
    """Create the MCP server.

    Args:
        settings: Configuration; read from the environment when omitted.
        context: Pre-built services (e.g. with a custom search backend or outline
            registry). Built from ``settings`` when omitted.
    """
    if context is None:
        context = AppContext.from_settings(settings or Settings())
    server = FileSystemServer(
        name=SERVER_NAME,
        version=__version__,
        instructions=build_instructions(context.settings),
        log_level=context.settings.log_level,
        audit=context.audit,
    )
    register_all(server, context)
    return server
