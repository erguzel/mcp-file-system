"""Data preview tool: peek."""

from typing import Annotated

from mcp.types import CallToolResult

from mcp_file_system.context import AppContext
from mcp_file_system.core.models import PeekResult
from mcp_file_system.tools._common import READ_ONLY, PathArg, ToolRegistry, to_call_result


def register(registry: ToolRegistry, ctx: AppContext) -> None:
    """Register the data preview tool."""
    peeker = ctx.peeker

    @registry.tool(READ_ONLY)
    def peek(path: PathArg) -> Annotated[CallToolResult, PeekResult]:
        """Show the shape of a data file instead of its content.

        CSV/TSV: columns, types, row count, sample rows. JSON/TOML: key tree with types.
        Logs: time span and level counts. Manifests (pyproject.toml, package.json,
        Cargo.toml): name, version, dependencies. Markdown: front matter and headings.
        """
        return to_call_result(peeker.peek(path))
