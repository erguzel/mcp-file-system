"""Editing tools, registered only when writes are enabled.

Tools: replace_in_file, edit_lines, write_file.
"""

from typing import Annotated

from mcp.types import CallToolResult
from pydantic import Field

from mcp_file_system.context import AppContext
from mcp_file_system.core.models import EditResult
from mcp_file_system.tools._common import (
    WRITES,
    PathArg,
    ToolRegistry,
    to_call_result,
    unset_int,
    unset_str,
)

DryRunArg = Annotated[bool, Field(description="Preview the diff without writing.")]
EditLinesStartArg = Annotated[int, Field(ge=1, description="First line to replace (1-based).")]
EditLinesEndArg = Annotated[
    int,
    Field(ge=0, description="Last line to replace, inclusive; start-1 inserts before start."),
]
NewTextArg = Annotated[str, Field(description="Replacement lines; empty deletes the range.")]
RequiredEtagArg = Annotated[
    str, Field(description="Etag from read_lines/file_info; the edit fails if stale.")
]


def register(registry: ToolRegistry, ctx: AppContext) -> None:
    """Register the editing tools."""
    editor = ctx.editor

    @registry.tool(WRITES)
    def replace_in_file(
        path: PathArg,
        pattern: Annotated[str, Field(description="Text to find (a regex if regex=true).")],
        replacement: Annotated[
            str, Field(description="Replacement text; with regex=true may use \\1 or \\g<name>.")
        ],
        regex: Annotated[bool, Field(description="Interpret pattern as a regex.")] = False,
        ignore_case: Annotated[bool, Field(description="Case-insensitive matching.")] = False,
        expected_count: Annotated[
            int, Field(ge=0, description="Fail unless exactly this many matches (0 = any).")
        ] = 1,
        expected_etag: Annotated[
            str, Field(description="Fail if the file changed since this etag (empty: no check).")
        ] = "",
        dry_run: DryRunArg = False,
    ) -> Annotated[CallToolResult, EditResult]:
        """Find and replace text in a file, like `sed -i 's/old/new/'` but guarded.

        By default the pattern must match exactly once; include enough surrounding
        context to make it unique. Returns a unified diff and the new etag.
        """
        return to_call_result(
            editor.replace(
                path,
                pattern,
                replacement,
                regex=regex,
                ignore_case=ignore_case,
                expected_count=unset_int(expected_count),
                expected_etag=unset_str(expected_etag),
                dry_run=dry_run,
            )
        )

    @registry.tool(WRITES)
    def edit_lines(
        path: PathArg,
        start: EditLinesStartArg,
        end: EditLinesEndArg,
        new_text: NewTextArg,
        expected_etag: RequiredEtagArg,
        dry_run: DryRunArg = False,
    ) -> Annotated[CallToolResult, EditResult]:
        """Replace, insert or delete a range of lines.

        Requires the etag from the read that showed you these line numbers, so edits
        based on an outdated view are rejected. Returns a diff and the new etag.
        """
        return to_call_result(
            editor.edit_lines(
                path, start, end, new_text, expected_etag=expected_etag, dry_run=dry_run
            )
        )

    @registry.tool(WRITES)
    def write_file(
        path: PathArg,
        content: Annotated[str, Field(description="Full file content.")],
        overwrite: Annotated[
            bool, Field(description="Allow replacing an existing file (needs expected_etag).")
        ] = False,
        expected_etag: Annotated[
            str, Field(description="Current etag of the file being overwritten.")
        ] = "",
        dry_run: DryRunArg = False,
    ) -> Annotated[CallToolResult, EditResult]:
        """Create a new file (with parent directories), or overwrite one given its etag.

        Prefer edit_lines or replace_in_file for changes to existing files.
        """
        return to_call_result(
            editor.write(
                path,
                content,
                overwrite=overwrite,
                expected_etag=unset_str(expected_etag),
                dry_run=dry_run,
            )
        )
