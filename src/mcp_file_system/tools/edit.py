"""Editing tools, registered only when writes are enabled.

Tools: replace_in_file, multi_replace, edit_lines, apply_patch, write_file, undo_last_edit.
"""

from typing import Annotated

from mcp.types import CallToolResult
from pydantic import Field

from mcp_file_system.context import AppContext
from mcp_file_system.core.editor import MAX_EDITS
from mcp_file_system.core.models import EditResult, PatchResult, Replacement, UndoResult
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
    def multi_replace(
        path: PathArg,
        edits: Annotated[
            list[Replacement],
            Field(
                min_length=1,
                max_length=MAX_EDITS,
                description="Exact-text replacements, applied in order; each one sees the "
                "result of the previous ones.",
            ),
        ],
        expected_etag: Annotated[
            str, Field(description="Fail if the file changed since this etag (empty: no check).")
        ] = "",
        dry_run: DryRunArg = False,
    ) -> Annotated[CallToolResult, EditResult]:
        """Apply several exact text replacements to one file: all or none.

        Each pattern must match exactly expected_count times (default 1) when its turn
        comes. If any edit fails, the file is left unchanged and the error names the
        edit. Returns one diff and the new etag.
        """
        return to_call_result(
            editor.multi_replace(
                path, edits, expected_etag=unset_str(expected_etag), dry_run=dry_run
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
    def apply_patch(
        path: PathArg,
        diff: Annotated[
            str,
            Field(
                description="Unified diff for this one file, hunks starting with "
                "'@@ -a,b +c,d @@'. Context and removed lines must match the file exactly."
            ),
        ],
        expected_etag: RequiredEtagArg,
        dry_run: DryRunArg = False,
    ) -> Annotated[CallToolResult, PatchResult]:
        """Apply a unified diff to one file: all hunks or none.

        A hunk whose line numbers are off is placed at the nearest exact match of its
        context; missing or ambiguous context fails the whole patch and names the hunk.
        Use dry_run=true to see which hunks apply. Returns a diff and the new etag.
        """
        return to_call_result(
            editor.apply_patch(path, diff, expected_etag=expected_etag, dry_run=dry_run)
        )

    @registry.tool(WRITES)
    def write_file(
        path: PathArg,
        content: Annotated[str, Field(description="Full file content.")],
        overwrite: Annotated[
            bool, Field(description="Allow replacing an existing file (needs expected_etag).")
        ] = False,
        expected_etag: Annotated[
            str,
            Field(
                description="Current etag of the file being overwritten; if that file no "
                "longer exists the call fails as stale instead of creating a new one."
            ),
        ] = "",
        dry_run: DryRunArg = False,
    ) -> Annotated[CallToolResult, EditResult]:
        """Create a file (with parent directories), or replace one with overwrite=true and its etag.

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

    @registry.tool(WRITES)
    def undo_last_edit(
        path: PathArg, dry_run: DryRunArg = False
    ) -> Annotated[CallToolResult, UndoResult]:
        """Undo this server's latest edit of a file, if nothing changed the file since.

        Restores the exact previous version: one level per file, no redo, kept only for
        this server session. A file this server created is renamed to
        '<name>.deleted-<UTC time>' instead of being deleted.
        """
        return to_call_result(editor.undo_last_edit(path, dry_run=dry_run))
