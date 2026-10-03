"""Reading tools: file_info, read_lines, read_between."""

from typing import Annotated

from mcp.server import MCPServer
from mcp.types import CallToolResult
from pydantic import Field

from mcp_file_system.context import AppContext
from mcp_file_system.core.models import FileInfo, LinesResult
from mcp_file_system.tools._common import READ_ONLY, PathArg, to_call_result, translate_errors


def register(server: MCPServer, ctx: AppContext) -> None:
    """Register the reading tools."""
    reader = ctx.reader

    @server.tool(annotations=READ_ONLY)
    @translate_errors
    def file_info(path: PathArg) -> Annotated[CallToolResult, FileInfo]:
        """Get metadata for a file or directory without reading its content.

        Returns size, line count, encoding, newline style, binary flag and the etag
        (required by edit tools). Call this first on unfamiliar files to decide how
        much to read.
        """
        return to_call_result(reader.info(path))

    @server.tool(annotations=READ_ONLY)
    @translate_errors
    def read_lines(
        path: PathArg,
        start: Annotated[
            int,
            Field(description="First line, 1-based. Negative counts from the end (-50 = tail)."),
        ] = 1,
        end: Annotated[
            int | None,
            Field(
                description="Last line, inclusive; negative counts from the end. Omit to read on."
            ),
        ] = None,
    ) -> Annotated[CallToolResult, LinesResult]:
        """Read a numbered range of lines from a text file.

        Prefer this over reading whole files: locate the region with grep, outline or
        read_between first, then read only the lines you need. Output is capped; if
        truncated, continue from `next_start`.
        """
        return to_call_result(reader.read_lines(path, start, end))

    @server.tool(annotations=READ_ONLY)
    @translate_errors
    def read_between(
        path: PathArg,
        start_pattern: Annotated[str, Field(description="Regex for the section's first line.")],
        end_pattern: Annotated[
            str | None,
            Field(description="Regex for the section's last line, searched after the start."),
        ] = None,
        occurrence: Annotated[
            int, Field(ge=1, description="Use the Nth line that matches start_pattern.")
        ] = 1,
        inclusive: Annotated[
            bool, Field(description="Include the start and end lines themselves.")
        ] = True,
        fixed_string: Annotated[bool, Field(description="Treat patterns as literal text.")] = False,
        ignore_case: Annotated[bool, Field(description="Case-insensitive matching.")] = False,
        max_lines: Annotated[
            int | None, Field(ge=1, description="Stop after this many lines.")
        ] = None,
    ) -> Annotated[CallToolResult, LinesResult]:
        """Read the block of lines between two patterns, like `sed -n '/start/,/end/p'`.

        Useful for config sections, log entries between markers, or a function whose
        start you can match. Without end_pattern, reads from the start match onward.
        """
        return to_call_result(
            reader.read_between(
                path,
                start_pattern,
                end_pattern,
                occurrence=occurrence,
                inclusive=inclusive,
                fixed_string=fixed_string,
                ignore_case=ignore_case,
                max_lines=max_lines,
            )
        )
