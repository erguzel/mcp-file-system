"""Structure tools: outline, read_symbol."""

from typing import Annotated

from mcp.types import CallToolResult
from pydantic import Field

from mcp_file_system.context import AppContext
from mcp_file_system.core.models import LinesResult, OutlineResult
from mcp_file_system.tools._common import (
    READ_ONLY,
    IfChangedSinceArg,
    PathArg,
    ToolRegistry,
    to_call_result,
)

SymbolNameArg = Annotated[
    str,
    Field(
        description="Symbol name as shown by outline, e.g. 'MyClass.method', a heading "
        "like 'Usage > Install', or a key path like 'tool.ruff.lint'. A plain name works "
        "if it is unique."
    ),
]


def register(registry: ToolRegistry, ctx: AppContext) -> None:
    """Register the structure tools."""
    outliner = ctx.outliner

    @registry.tool(READ_ONLY)
    def outline(path: PathArg) -> Annotated[CallToolResult, OutlineResult]:
        """Show a file's structure: code symbols, Markdown headings, or config keys.

        Each entry has its line span, so you can read just what you need with
        read_symbol or read_lines. Supports Python, Markdown, JSON, TOML and YAML; for
        config files, scalar values are shown inline (e.g. `version = "1.2"`).
        """
        return to_call_result(outliner.outline(path))

    @registry.tool(READ_ONLY)
    def read_symbol(
        path: PathArg, name: SymbolNameArg, if_changed_since: IfChangedSinceArg = ""
    ) -> Annotated[CallToolResult, LinesResult]:
        """Read the full source of one class, function, method, section or config key."""
        return to_call_result(outliner.read_symbol(path, name, if_changed_since=if_changed_since))
