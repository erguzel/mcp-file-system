"""MCP tool adapters and tool profiles.

Each module exposes ``register(registry, ctx)``. Adapters are deliberately thin: they
declare the agent-facing schema and description and delegate to a service from
:class:`~mcp_file_system.context.AppContext`. :class:`~._common.ToolRegistry` applies the
cross-cutting settings (allowlist, output schemas, error translation).

To add a tool group, write a module with a ``register`` function and add it to the
profile tables below. New tools normally go into the ``full`` profile only; see
``tools/lite.py`` for what belongs in ``lite``.
"""

from collections.abc import Callable
from typing import Any

from mcp.server import MCPServer

from mcp_file_system.config import Profile
from mcp_file_system.context import AppContext
from mcp_file_system.tools import edit, lite, navigate, outline, read, search
from mcp_file_system.tools._common import ToolRegistry

Registrar = Callable[[ToolRegistry, AppContext], None]

READ_TOOLSETS: dict[Profile, tuple[Registrar, ...]] = {
    "full": (read.register, search.register, navigate.register, outline.register),
    "lite": (lite.register,),
}
WRITE_TOOLSETS: dict[Profile, tuple[Registrar, ...]] = {
    "full": (edit.register,),
    "lite": (lite.register_edit,),
}


def register_all(server: MCPServer[Any], ctx: AppContext) -> list[str]:
    """Register the profile's tools (plus editing tools if writes are enabled).

    Returns:
        The names of the tools exposed to clients.

    Raises:
        ValueError: The ``tools`` allowlist names a tool the profile does not offer.
    """
    settings = ctx.settings
    registry = ToolRegistry(server, settings)
    registrars = READ_TOOLSETS[settings.profile]
    if settings.allow_write:
        registrars += WRITE_TOOLSETS[settings.profile]
    for registrar in registrars:
        registrar(registry, ctx)

    unknown = [name for name in settings.tools if name not in registry.offered]
    if unknown:
        hint = "" if settings.allow_write else " (editing tools also need --allow-write)"
        raise ValueError(
            f"Unknown tool(s) for profile {settings.profile!r}{hint}: {', '.join(unknown)}. "
            f"Available: {', '.join(registry.offered)}"
        )
    return registry.registered
