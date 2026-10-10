"""Server factory.

:func:`create_server` builds a fully configured :class:`MCPServer` independent of the
transport, so the same server can run over stdio today and be served over HTTP later
(``server.run("streamable-http", ...)``) without touching the tools. Serving several
sessions also needs per-session state, see ``docs/ARCHITECTURE.md``.
"""

import time
from collections.abc import Callable
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


LITE_WRITE_TOOLS = ("replace_in_file", "edit_lines", "write_file")
FULL_WRITE_TOOLS = (*LITE_WRITE_TOOLS, "multi_replace", "apply_patch", "undo_last_edit")


def build_instructions(settings: Settings) -> str:
    """Usage guidance sent to the client during initialization.

    Follows the profile and the ``tools`` allowlist: a sentence names only tools the client
    is offered, and is left out when it would name none. Naming a hidden tool makes small
    models call it and fail.
    """
    lite = settings.profile == "lite"

    def offered(*names: str) -> list[str]:
        return [name for name in names if not settings.tools or name in settings.tools]

    roots = "\n".join(f"- {root}" for root in settings.roots)
    lines = [
        "Fine-grained filesystem access. Read only what you need instead of whole files.",
        f"Allowed roots (relative paths resolve against the first):\n{roots}",
    ]
    lines += _reading_lines(offered, lite)
    writes = offered(*(LITE_WRITE_TOOLS if lite else FULL_WRITE_TOOLS))
    if settings.allow_write and writes:
        lines += _editing_lines(offered, lite)
    else:
        lines.append("The server is read-only.")
    return "\n\n".join(lines)


def _reading_lines(offered: Callable[..., list[str]], lite: bool) -> list[str]:
    """Instruction paragraphs for navigating, locating and reading."""
    steps = []
    if navigate := offered("list_directory", "find_files"):
        steps.append(f"{'/'.join(navigate)} to navigate")
    if offered("grep"):
        steps.append("grep (output_mode='count' or 'files_with_matches' first) to locate")
    if size := offered("file_info", "outline"):
        steps.append(f"{' or '.join(size)} to size up a file")
    if read := offered("read_lines", *(() if lite else ("read_between",)), "read_symbol"):
        steps.append(f"{_either(read)} to read the relevant part")
    lines = [f"Workflow: {'; '.join(steps)}."] if steps else []
    if offered("read_lines", "read_symbol"):
        etag = (
            "Every read returns an etag identifying the file version. To re-check lines "
            "you already have, pass that etag as if_changed_since instead of re-reading."
        )
        if offered("grep") and offered("read_symbol"):
            etag += (
                " grep hits show their enclosing symbol in [brackets]; read it with read_symbol."
            )
        lines.append(etag)
    if not lite:
        tips = []
        if offered("read_lines"):
            tips.append(
                "for a line number from an error message or a search hit, use read_lines(around=N)"
            )
        if offered("read_ranges"):
            tips.append("for several known ranges, use read_ranges in one call")
        if tips:
            text = "; ".join(tips)
            lines.append(f"{text[0].upper()}{text[1:]}.")
    return lines


def _editing_lines(offered: Callable[..., list[str]], lite: bool) -> list[str]:
    """Instruction paragraphs for the editing tools (at least one of them is offered)."""
    prefer = [
        label
        for name, label in (
            ("replace_in_file", "replace_in_file (unique match)"),
            ("edit_lines", "edit_lines (with the etag of your latest read)"),
        )
        if offered(name)
    ]
    editing = "Editing is enabled" + (f": prefer {' or '.join(prefer)}" if prefer else "")
    lines = [editing + ("" if lite else "; use dry_run=true to preview a diff") + "."]
    if lite:
        return lines
    batch = [
        label
        for name, label in (
            ("multi_replace", "multi_replace"),
            ("apply_patch", "apply_patch (unified diff)"),
        )
        if offered(name)
    ]
    extra = []
    if batch:
        verb = "both apply" if len(batch) == 2 else "it applies"
        extra.append(
            f"For several changes to one file use {' or '.join(batch)}; {verb} all or nothing."
        )
    if offered("undo_last_edit"):
        extra.append("undo_last_edit reverts this server's latest edit of a file.")
    if extra:
        lines.append(" ".join(extra))
    return lines


def _either(names: list[str]) -> str:
    """``"a"``, ``"a or b"``, ``"a, b or c"``."""
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} or {names[-1]}"


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
