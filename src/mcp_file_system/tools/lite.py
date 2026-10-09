"""The ``lite`` profile: a small but complete tool set for small local models.

Design rules:

* **Narrow but complete.** One tool per step of the agent loop (navigate, locate, size
  up, read part, edit), so a model never has to work around a missing tool with more
  calls and more tokens.
* **Essential parameters only.** Hidden parameters keep the full tools' defaults.
* **Short descriptions.** Tool and parameter text is sent with every request.
* **Same services.** Behavior matches the full tools called with the same arguments,
  except that grep retries an invalid regex as literal text instead of failing.
"""

from typing import Annotated

from mcp.types import CallToolResult
from pydantic import Field

from mcp_file_system.context import AppContext
from mcp_file_system.core.models import (
    DirectoryListing,
    EditResult,
    FileInfo,
    FileList,
    GrepResult,
    LinesResult,
    OutlineResult,
    OutputMode,
)
from mcp_file_system.core.search import MAX_CONTEXT_LINES
from mcp_file_system.errors import InvalidPatternError
from mcp_file_system.tools._common import (
    READ_ONLY,
    WRITES,
    DirArg,
    IfChangedSinceArg,
    PathArg,
    ToolRegistry,
    to_call_result,
    unset_str,
)
from mcp_file_system.tools.edit import (
    EditLinesEndArg,
    EditLinesStartArg,
    NewTextArg,
    RequiredEtagArg,
)
from mcp_file_system.tools.navigate import DepthArg, GlobArg
from mcp_file_system.tools.outline import SymbolNameArg

LITERAL_FALLBACK_NOTE = "pattern is not a valid regular expression; searched it as literal text"


def register(registry: ToolRegistry, ctx: AppContext) -> None:
    """Register the lite read-only tools."""
    reader, finder, searcher, outliner = ctx.reader, ctx.finder, ctx.searcher, ctx.outliner

    @registry.tool(READ_ONLY)
    def file_info(path: PathArg) -> Annotated[CallToolResult, FileInfo]:
        """Size, token estimate, line count and etag of a file. Use before reading."""
        return to_call_result(reader.info(path))

    @registry.tool(READ_ONLY)
    def list_directory(
        path: DirArg = ".", depth: DepthArg = 1
    ) -> Annotated[CallToolResult, DirectoryListing]:
        """List a directory as a tree with file sizes."""
        return to_call_result(finder.list_directory(path, depth=depth))

    @registry.tool(READ_ONLY)
    def find_files(glob: GlobArg, path: DirArg = ".") -> Annotated[CallToolResult, FileList]:
        """Find files by name pattern."""
        return to_call_result(finder.find(glob, path))

    @registry.tool(READ_ONLY)
    def grep(
        pattern: Annotated[str, Field(description="Regex (invalid regex is searched literally).")],
        path: DirArg = ".",
        glob: Annotated[str, Field(description="Only files matching this glob, e.g. '*.py'.")] = "",
        ignore_case: bool = False,
        context: Annotated[
            int, Field(ge=0, le=MAX_CONTEXT_LINES, description="Context lines around each hit.")
        ] = 0,
        output_mode: Annotated[
            OutputMode, Field(description="'content', 'files_with_matches' or 'count'.")
        ] = "content",
    ) -> Annotated[CallToolResult, GrepResult]:
        """Search file contents. Use output_mode='count' first to see where matches are."""

        def run(*, fixed_string: bool) -> GrepResult:
            return searcher.grep(
                pattern,
                path,
                glob=unset_str(glob),
                fixed_string=fixed_string,
                ignore_case=ignore_case,
                context_before=context,
                context_after=context,
                mode=output_mode,
            )

        try:
            result = run(fixed_string=False)
        except InvalidPatternError:
            # Without a fixed_string parameter, a small model searching for "foo(" would
            # otherwise be stuck on a regex error.
            result = run(fixed_string=True)
            note = (
                f"{LITERAL_FALLBACK_NOTE}; {result.note}" if result.note else LITERAL_FALLBACK_NOTE
            )
            result = result.model_copy(update={"note": note})
        return to_call_result(result)

    @registry.tool(READ_ONLY)
    def outline(path: PathArg) -> Annotated[CallToolResult, OutlineResult]:
        """Structure of a code, Markdown, JSON, TOML or YAML file, with line spans."""
        return to_call_result(outliner.outline(path))

    @registry.tool(READ_ONLY)
    def read_symbol(
        path: PathArg, name: SymbolNameArg, if_changed_since: IfChangedSinceArg = ""
    ) -> Annotated[CallToolResult, LinesResult]:
        """Read one class, function, section or config key by name (see outline)."""
        return to_call_result(outliner.read_symbol(path, name, if_changed_since=if_changed_since))

    @registry.tool(READ_ONLY)
    def read_lines(
        path: PathArg,
        start: Annotated[int, Field(description="First line (1-based; negative = from end).")] = 1,
        end: Annotated[int, Field(description="Last line, inclusive (-1 = last line).")] = -1,
        if_changed_since: IfChangedSinceArg = "",
    ) -> Annotated[CallToolResult, LinesResult]:
        """Read numbered lines. If truncated, continue from next_start."""
        return to_call_result(
            reader.read_lines(path, start, end, if_changed_since=if_changed_since)
        )


def register_edit(registry: ToolRegistry, ctx: AppContext) -> None:
    """Register the lite editing tools."""
    editor = ctx.editor

    @registry.tool(WRITES)
    def replace_in_file(
        path: PathArg,
        pattern: Annotated[str, Field(description="Exact text to find; must occur once.")],
        replacement: Annotated[str, Field(description="Replacement text.")],
    ) -> Annotated[CallToolResult, EditResult]:
        """Replace one exact, unique occurrence of text in a file. Returns a diff."""
        return to_call_result(editor.replace(path, pattern, replacement))

    @registry.tool(WRITES)
    def edit_lines(
        path: PathArg,
        start: EditLinesStartArg,
        end: EditLinesEndArg,
        new_text: NewTextArg,
        expected_etag: RequiredEtagArg,
    ) -> Annotated[CallToolResult, EditResult]:
        """Replace, insert or delete lines. Needs the etag from your latest read."""
        return to_call_result(
            editor.edit_lines(path, start, end, new_text, expected_etag=expected_etag)
        )

    @registry.tool(WRITES)
    def write_file(
        path: PathArg, content: Annotated[str, Field(description="Full file content.")]
    ) -> Annotated[CallToolResult, EditResult]:
        """Create a new file. Existing files are never overwritten."""
        return to_call_result(editor.write(path, content))
