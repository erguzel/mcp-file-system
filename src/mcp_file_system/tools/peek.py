"""Data file tools: peek, query."""

from typing import Annotated

from mcp.types import CallToolResult
from pydantic import Field

from mcp_file_system.context import AppContext
from mcp_file_system.core.models import PeekResult, QueryResult
from mcp_file_system.core.query.service import MAX_DEPTH
from mcp_file_system.tools._common import READ_ONLY, PathArg, ToolRegistry, to_call_result


def register(registry: ToolRegistry, ctx: AppContext) -> None:
    """Register the data file tools."""
    peeker = ctx.peeker
    data_query = ctx.data_query

    @registry.tool(READ_ONLY)
    def peek(path: PathArg) -> Annotated[CallToolResult, PeekResult]:
        """Show the shape of a data file instead of its content.

        CSV/TSV: columns, types, row count, sample rows. JSON (comments allowed) and TOML:
        key tree with types. Logs (.log): time span and level counts. Manifests
        (pyproject.toml, package.json, Cargo.toml): name, version, dependencies. Markdown:
        front-matter keys and heading counts.
        """
        return to_call_result(peeker.peek(path))

    @registry.tool(READ_ONLY)
    def query(
        path: PathArg,
        key_path: Annotated[
            str,
            Field(
                description="Keys joined by '.', indexes as [0] or [-1], e.g. "
                "'dependencies.react', 'jobs.test.steps[0]', '[1].kind' (YAML document 1). "
                "Quote keys containing dots, brackets or double quotes: '\"a.b\".c'. "
                "Empty: the whole document."
            ),
        ] = "",
        depth: Annotated[
            int,
            Field(
                ge=1,
                le=MAX_DEPTH,
                description="Levels of nested values to show; deeper ones collapse to "
                "'{3 keys}' or '[40 items]'.",
            ),
        ] = 2,
    ) -> Annotated[CallToolResult, QueryResult]:
        """Return one value of a JSON, YAML or TOML file by key path.

        Use it on large structured files (package-lock.json, OpenAPI specs, Helm values)
        instead of grep or reading lines; works on minified JSON and JSON with comments.
        Rows use the same key syntax, so you can drill down from any of them.
        """
        return to_call_result(data_query.query(path, key_path, depth=depth))
