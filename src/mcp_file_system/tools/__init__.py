"""MCP tool adapters.

Each module exposes ``register(server, ctx)``. Adapters are deliberately thin: they
declare the agent-facing schema and description, delegate to a service from
:class:`~mcp_file_system.context.AppContext`, and translate errors. To add a tool
group, write a module with a ``register`` function and add it to a tuple below.
"""

from collections.abc import Callable

from mcp.server import MCPServer

from mcp_file_system.context import AppContext
from mcp_file_system.tools import edit, navigate, outline, read, search

ToolRegistrar = Callable[[MCPServer, AppContext], None]

READ_ONLY_TOOLSETS: tuple[ToolRegistrar, ...] = (
    read.register,
    search.register,
    navigate.register,
    outline.register,
)
WRITE_TOOLSETS: tuple[ToolRegistrar, ...] = (edit.register,)


def register_all(server: MCPServer, ctx: AppContext) -> None:
    """Register every read-only tool, plus the editing tools if writes are enabled."""
    for registrar in READ_ONLY_TOOLSETS:
        registrar(server, ctx)
    if ctx.settings.allow_write:
        for registrar in WRITE_TOOLSETS:
            registrar(server, ctx)
