"""Structure tools: outline, read_symbol."""

from typing import Annotated

from mcp.server import MCPServer
from mcp.types import CallToolResult
from pydantic import Field

from mcp_file_system.context import AppContext
from mcp_file_system.core.models import LinesResult, OutlineResult
from mcp_file_system.tools._common import READ_ONLY, PathArg, to_call_result, translate_errors


def register(server: MCPServer, ctx: AppContext) -> None:
    """Register the structure tools."""
    outliner = ctx.outliner

    @server.tool(annotations=READ_ONLY)
    @translate_errors
    def outline(path: PathArg) -> Annotated[CallToolResult, OutlineResult]:
        """Show a file's structure (classes, functions, methods, or Markdown headings).

        Each entry has its line span, so you can read just what you need with
        read_symbol or read_lines. Supports Python and Markdown files.
        """
        return to_call_result(outliner.outline(path))

    @server.tool(annotations=READ_ONLY)
    @translate_errors
    def read_symbol(
        path: PathArg,
        name: Annotated[
            str,
            Field(
                description="Symbol name as shown by outline, e.g. 'MyClass.method' or a "
                "heading like 'Usage > Install'. A plain name works if it is unique."
            ),
        ],
    ) -> Annotated[CallToolResult, LinesResult]:
        """Read the full source of one class, function, method or Markdown section."""
        return to_call_result(outliner.read_symbol(path, name))
