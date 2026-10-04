"""Search tool: grep."""

from typing import Annotated

from mcp.types import CallToolResult
from pydantic import Field

from mcp_file_system.context import AppContext
from mcp_file_system.core.models import GrepResult, OutputMode
from mcp_file_system.core.search import MAX_CONTEXT_LINES
from mcp_file_system.tools._common import (
    READ_ONLY,
    DirArg,
    IncludeHiddenArg,
    ToolRegistry,
    to_call_result,
    unset_str,
)

DEFAULT_MAX_MATCHES = 100


def register(registry: ToolRegistry, ctx: AppContext) -> None:
    """Register the search tool."""
    searcher = ctx.searcher

    @registry.tool(READ_ONLY)
    def grep(
        pattern: Annotated[str, Field(description="Regular expression (Python syntax).")],
        path: DirArg = ".",
        glob: Annotated[
            str,
            Field(
                description="Only search files matching this glob, e.g. '*.py' or "
                "'src/**/*.ts'. Empty: all files."
            ),
        ] = "",
        fixed_string: Annotated[bool, Field(description="Treat pattern as literal text.")] = False,
        ignore_case: Annotated[bool, Field(description="Case-insensitive matching.")] = False,
        context_before: Annotated[
            int, Field(ge=0, le=MAX_CONTEXT_LINES, description="Lines of context before each hit.")
        ] = 0,
        context_after: Annotated[
            int, Field(ge=0, le=MAX_CONTEXT_LINES, description="Lines of context after each hit.")
        ] = 0,
        only_matching: Annotated[
            bool, Field(description="Return only the matched text, one hit per match.")
        ] = False,
        output_mode: Annotated[
            OutputMode,
            Field(
                description="'content': matching lines; 'files_with_matches': paths only; "
                "'count': matches per file."
            ),
        ] = "content",
        max_matches: Annotated[
            int, Field(ge=1, description="Maximum hits (or files, in the other modes).")
        ] = DEFAULT_MAX_MATCHES,
        include_hidden: IncludeHiddenArg = False,
    ) -> Annotated[CallToolResult, GrepResult]:
        """Search file contents with a regular expression, like `grep -rn`.

        Searches a single file or a directory recursively (skipping binary,
        .gitignore'd and policy-denied files). Results show `path`, then
        `line:text` for hits and `line-text` for context. Use output_mode
        'files_with_matches' or 'count' to survey before reading content.
        """
        return to_call_result(
            searcher.grep(
                pattern,
                path,
                glob=unset_str(glob),
                fixed_string=fixed_string,
                ignore_case=ignore_case,
                context_before=context_before,
                context_after=context_after,
                only_matching=only_matching,
                mode=output_mode,
                max_matches=max_matches,
                include_hidden=include_hidden,
            )
        )
