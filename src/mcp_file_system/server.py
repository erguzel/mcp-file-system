"""Server factory.

:func:`create_server` builds a fully configured :class:`MCPServer` independent of the
transport, so the same server can run over stdio today and be served over HTTP later
(``server.run("streamable-http", ...)``) without touching tools or services.
"""

from typing import Any

from mcp.server import MCPServer

from mcp_file_system import __version__
from mcp_file_system.config import Settings
from mcp_file_system.context import AppContext
from mcp_file_system.tools import register_all

SERVER_NAME = "mcp-file-system"


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
    if settings.allow_write:
        preview = "" if lite else "; use dry_run=true to preview a diff"
        lines.append(
            "Editing is enabled: prefer replace_in_file (unique match) or edit_lines (with "
            f"the etag of your latest read){preview}."
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
    server: MCPServer[Any] = MCPServer(
        name=SERVER_NAME,
        version=__version__,
        instructions=build_instructions(context.settings),
        log_level=context.settings.log_level,
    )
    register_all(server, context)
    return server
