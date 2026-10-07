"""Reading tools: file_info, read_lines, read_ranges, read_between."""

from typing import Annotated

from mcp.types import CallToolResult
from pydantic import Field

from mcp_file_system.context import AppContext
from mcp_file_system.core.models import BatchLinesResult, FileInfo, LineRange, LinesResult
from mcp_file_system.core.reader import DEFAULT_RADIUS, MAX_BATCH_RANGES
from mcp_file_system.tools._common import (
    READ_ONLY,
    IfChangedSinceArg,
    PathArg,
    ToolRegistry,
    to_call_result,
    unset_int,
    unset_str,
)


def register(registry: ToolRegistry, ctx: AppContext) -> None:
    """Register the reading tools."""
    reader = ctx.reader

    @registry.tool(READ_ONLY)
    def file_info(path: PathArg) -> Annotated[CallToolResult, FileInfo]:
        """Get metadata for a file or directory without reading its content.

        Returns size, approximate token count, line count, encoding, newline style,
        binary flag and the etag (required by edit tools). Call this first on
        unfamiliar files to decide how much to read.
        """
        return to_call_result(reader.info(path))

    @registry.tool(READ_ONLY)
    def read_lines(
        path: PathArg,
        start: Annotated[
            int,
            Field(description="First line, 1-based. Negative counts from the end (-50 = tail)."),
        ] = 1,
        end: Annotated[
            int,
            Field(description="Last line, inclusive; negative counts from the end (-1 = last)."),
        ] = -1,
        around: Annotated[
            int,
            Field(
                ge=0,
                description="Line to center on, e.g. from an error message or grep hit. "
                "Use instead of start/end. 0: use start/end.",
            ),
        ] = 0,
        radius: Annotated[
            int, Field(ge=0, description="Lines before and after `around` (0 = only that line).")
        ] = DEFAULT_RADIUS,
        if_changed_since: IfChangedSinceArg = "",
    ) -> Annotated[CallToolResult, LinesResult]:
        """Read a numbered range of lines from a text file.

        Prefer this over reading whole files: locate the region with grep, outline or
        read_between first, then read only the lines you need. For a line number from a
        stack trace or grep hit, pass around=N instead of computing start/end. Output is
        capped; if truncated, continue from `next_start`. To re-check lines you already
        have, pass their etag as if_changed_since instead of reading them again.
        """
        return to_call_result(
            reader.read_lines(
                path,
                start,
                end,
                around=unset_int(around),
                radius=radius,
                if_changed_since=if_changed_since,
            )
        )

    @registry.tool(READ_ONLY)
    def read_ranges(
        ranges: Annotated[
            list[LineRange],
            Field(
                min_length=1,
                max_length=MAX_BATCH_RANGES,
                description="Ranges to read in order, e.g. [{path: 'a.py', start: 10, end: 30}].",
            ),
        ],
    ) -> Annotated[CallToolResult, BatchLinesResult]:
        """Read several line ranges, from one or more files, in one call.

        Use it when you already know the lines you need, e.g. from several grep hits or
        stack frames. The ranges share one output budget; if it runs out, the result
        says which ranges are left and how to continue.
        """
        return to_call_result(reader.read_ranges(ranges))

    @registry.tool(READ_ONLY)
    def read_between(
        path: PathArg,
        start_pattern: Annotated[str, Field(description="Regex for the section's first line.")],
        end_pattern: Annotated[
            str,
            Field(
                description="Regex for the section's last line, searched after the start. "
                "Empty: read on from the start line."
            ),
        ] = "",
        occurrence: Annotated[
            int, Field(ge=1, description="Use the Nth line that matches start_pattern.")
        ] = 1,
        inclusive: Annotated[
            bool, Field(description="Include the start and end lines themselves.")
        ] = True,
        fixed_string: Annotated[bool, Field(description="Treat patterns as literal text.")] = False,
        ignore_case: Annotated[bool, Field(description="Case-insensitive matching.")] = False,
        max_lines: Annotated[
            int, Field(ge=0, description="Stop after this many lines (0 = server limit).")
        ] = 0,
    ) -> Annotated[CallToolResult, LinesResult]:
        """Read the block of lines between two patterns, like `sed -n '/start/,/end/p'`.

        Useful for config sections, log entries between markers, or a function whose
        start you can match. Without end_pattern, reads from the start match onward.
        """
        return to_call_result(
            reader.read_between(
                path,
                start_pattern,
                unset_str(end_pattern),
                occurrence=occurrence,
                inclusive=inclusive,
                fixed_string=fixed_string,
                ignore_case=ignore_case,
                max_lines=unset_int(max_lines),
            )
        )
