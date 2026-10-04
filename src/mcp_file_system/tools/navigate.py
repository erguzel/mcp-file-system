"""Navigation tools: list_directory, find_files."""

from typing import Annotated

from mcp.types import CallToolResult
from pydantic import Field

from mcp_file_system.context import AppContext
from mcp_file_system.core.finder import EntryKind
from mcp_file_system.core.models import DirectoryListing, FileList
from mcp_file_system.tools._common import (
    READ_ONLY,
    DirArg,
    IncludeHiddenArg,
    ToolRegistry,
    to_call_result,
    unset_int,
)

GlobArg = Annotated[
    str,
    Field(
        description="Gitignore-style glob: '*.py' matches at any depth, "
        "'src/**/test_*.py' is anchored to path."
    ),
]
DepthArg = Annotated[
    int, Field(ge=1, le=10, description="Levels to descend (1 = direct children).")
]


def register(registry: ToolRegistry, ctx: AppContext) -> None:
    """Register the navigation tools."""
    finder = ctx.finder

    @registry.tool(READ_ONLY)
    def list_directory(
        path: DirArg = ".",
        depth: DepthArg = 1,
        include_hidden: IncludeHiddenArg = False,
    ) -> Annotated[CallToolResult, DirectoryListing]:
        """List a directory as an indented tree with file sizes.

        Skips .gitignore'd and policy-denied entries. Start shallow and descend into
        the subdirectories you care about.
        """
        return to_call_result(
            finder.list_directory(path, depth=depth, include_hidden=include_hidden)
        )

    @registry.tool(READ_ONLY)
    def find_files(
        glob: GlobArg,
        path: DirArg = ".",
        kind: Annotated[
            EntryKind, Field(description="Return 'file's, 'directory's or 'any'.")
        ] = "file",
        max_depth: Annotated[
            int, Field(ge=0, description="Limit search depth (0 = unlimited).")
        ] = 0,
        max_results: Annotated[
            int, Field(ge=0, description="Limit result count (0 = server limit).")
        ] = 0,
        include_hidden: IncludeHiddenArg = False,
    ) -> Annotated[CallToolResult, FileList]:
        """Find files or directories by name pattern, like `find -name`."""
        return to_call_result(
            finder.find(
                glob,
                path,
                kind=kind,
                max_depth=unset_int(max_depth),
                include_hidden=include_hidden,
                max_results=unset_int(max_results),
            )
        )
