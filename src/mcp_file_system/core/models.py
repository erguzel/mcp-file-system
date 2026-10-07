"""Tool result models.

Each model is both the structured output of a tool (its JSON schema is published as the
tool's output schema) and, via :meth:`ToolResult.render`, the compact plain-text view
that the agent reads. The text view is designed for token efficiency and mirrors
familiar CLI output (``cat -n``, ``grep -n``, ``tree``, unified diffs).
"""

from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from mcp_file_system.core.render import human_count, human_size

OutputMode = Literal["content", "files_with_matches", "count"]
"""Output modes of the ``lite`` profile's grep; kept small on purpose."""
GrepMode = Literal[OutputMode, "collapsed"]
"""Output modes of the ``full`` profile's grep."""


class ToolResult(BaseModel, ABC):
    """Base class for every tool result."""

    model_config = ConfigDict(frozen=True)

    @abstractmethod
    def render(self) -> str:
        """Return the agent-facing text representation."""


class FileInfo(ToolResult):
    """Metadata about a file or directory."""

    path: str
    kind: Literal["file", "directory"]
    size_bytes: int
    modified: datetime
    etag: str | None = Field(default=None, description="Version token required by edit tools.")
    is_binary: bool | None = None
    encoding: str | None = None
    newline: str | None = Field(default=None, description="LF, CRLF or CR.")
    line_count: int | None = None
    approx_tokens: int | None = Field(
        default=None, description="Rough token cost of reading the whole file (bytes / 4)."
    )
    entry_count: int | None = Field(default=None, description="Immediate children (dirs).")

    def render(self) -> str:  # noqa: D102
        rows = [f"path: {self.path}", f"kind: {self.kind}"]
        if self.kind == "directory":
            rows.append(f"entries: {self.entry_count}")
        else:
            size = f"size: {human_size(self.size_bytes)} ({self.size_bytes} bytes"
            if self.approx_tokens is not None:
                size += f", ~{human_count(self.approx_tokens)} tokens"
            rows.append(size + ")")
            if self.is_binary:
                rows.append("binary: yes")
            else:
                rows.append(f"lines: {self.line_count}")
                rows.append(f"encoding: {self.encoding}, newline: {self.newline}")
        rows.append(f"modified: {self.modified.isoformat()}")
        if self.etag:
            rows.append(f"etag: {self.etag}")
        return "\n".join(rows)


class LinesResult(ToolResult):
    """A contiguous, numbered block of lines from one file."""

    path: str
    start_line: int = Field(description="First line in content (0 if empty).")
    end_line: int = Field(description="Last line in content (0 if empty).")
    total_lines: int
    etag: str
    content: str = Field(description="Lines formatted as '<number>\\t<text>'.")
    truncated: bool = False
    next_start: int | None = Field(
        default=None, description="Pass as read_lines(start=...) to continue."
    )
    unchanged: bool = Field(
        default=False,
        description="True if content was omitted because if_changed_since matched.",
    )
    note: str | None = None

    def render(self) -> str:  # noqa: D102
        span = f"lines {self.start_line}-{self.end_line}" if self.end_line else "no lines"
        parts = [f"==> {self.path} ({span} of {self.total_lines}, etag {self.etag}) <=="]
        if self.content:
            parts.append(self.content)
        if self.note:
            parts.append(f"[{self.note}]")
        if self.truncated and self.next_start is not None:
            hint = f"read_lines(path={self.path!r}, start={self.next_start})"
            parts.append(f"[Truncated. Continue with {hint}]")
        return "\n".join(parts)


class GrepHit(BaseModel):
    """One matching line (or, with only_matching, one match) plus surrounding context."""

    model_config = ConfigDict(frozen=True)

    path: str
    line: int
    text: str
    symbol: str = Field(
        default="", description="Innermost enclosing symbol (e.g. 'Parser.parse'), if known."
    )
    before: list[str] = Field(default_factory=list, description="Lines line-N..line-1.")
    after: list[str] = Field(default_factory=list, description="Lines line+1..line+N.")


class FileCount(BaseModel):
    """Number of matching lines (or matches) in one file."""

    model_config = ConfigDict(frozen=True)

    path: str
    count: int


class TemplateGroup(BaseModel):
    """Lines that differ only in masked tokens (numbers, hex, UUIDs, IPs, timestamps)."""

    model_config = ConfigDict(frozen=True)

    template: str = Field(description="The line with variable tokens replaced by <N>, <IP>, ...")
    count: int
    first_line: int
    last_line: int


class GrepGroup(TemplateGroup):
    """A template of matching lines, possibly spanning several files."""

    files: int = Field(description="Number of files containing the template.")
    first_path: str
    last_path: str

    def render(self) -> str:
        """Two rows: count and template, then where to read an example."""
        first = f"{self.first_path}:{self.first_line}"
        if self.count == 1:
            where = f"at {first}"
        else:
            where = f"first {first}, last {self.last_path}:{self.last_line}"
            if self.files > 1:
                where += f" ({self.files} files)"
        return f"[x{self.count}] {self.template}\n  {where}"


class GrepResult(ToolResult):
    """Search results across one or more files."""

    pattern: str
    mode: GrepMode
    hits: list[GrepHit] = Field(default_factory=list)
    files: list[FileCount] = Field(default_factory=list)
    groups: list[GrepGroup] = Field(
        default_factory=list, description="Collapsed mode: templates by descending count."
    )
    template_count: int = Field(
        default=0, description="Collapsed mode: distinct templates found, shown or not."
    )
    ungrouped: int = Field(
        default=0, description="Collapsed mode: matching lines beyond the template limit."
    )
    files_scanned: int = 0
    files_skipped: int = Field(default=0, description="Binary or unreadable files.")
    truncated: bool = False
    note: str | None = None

    def render(self) -> str:  # noqa: D102
        if self.mode == "content":
            body = self._render_content()
            matched = len({hit.path for hit in self.hits})
            summary = f"{len(self.hits)} hit(s) in {matched} file(s)"
        elif self.mode == "collapsed":
            body = "\n".join(group.render() for group in self.groups)
            lines = sum(group.count for group in self.groups)
            summary = f"{lines} matching line(s) in {len(self.groups)} template(s)"
            if self.template_count > len(self.groups):
                summary += f" (the most frequent of {self.template_count})"
            if self.ungrouped:
                summary += f", {self.ungrouped} more line(s) beyond the template limit"
        elif self.mode == "count":
            body = "\n".join(f"{f.path}:{f.count}" for f in self.files)
            summary = f"{sum(f.count for f in self.files)} hit(s) in {len(self.files)} file(s)"
        else:
            body = "\n".join(f.path for f in self.files)
            summary = f"{len(self.files)} matching file(s)"
        if not (self.hits or self.files or self.groups):
            body, summary = "", "No matches"
        footer = f"[{summary}; scanned {self.files_scanned} file(s)"
        if self.files_skipped:
            footer += f", skipped {self.files_skipped} binary/unreadable"
        footer += "]"
        if self.truncated:
            footer += (
                f"\n[Truncated: {self.note or 'limit reached'}. Narrow the path, glob or pattern.]"
            )
        elif self.note:
            footer += f"\n[{self.note}]"
        return f"{body}\n{footer}" if body else footer

    def _render_content(self) -> str:
        blocks: list[str] = []
        for path in dict.fromkeys(hit.path for hit in self.hits):
            rows: dict[int, tuple[str, bool]] = {}
            for hit in (h for h in self.hits if h.path == path):
                for offset, text in enumerate(hit.before):
                    rows.setdefault(hit.line - len(hit.before) + offset, (text, False))
                for offset, text in enumerate(hit.after, start=1):
                    rows.setdefault(hit.line + offset, (text, False))
            symbols: dict[int, str] = {}
            for hit in (h for h in self.hits if h.path == path):
                previous = rows.get(hit.line)
                # With only_matching, several matches share a line: show each one.
                text = f"{previous[0]} | {hit.text}" if previous and previous[1] else hit.text
                rows[hit.line] = (text, True)
                if hit.symbol:
                    symbols[hit.line] = hit.symbol
            lines = [path]
            last: int | None = None
            current_symbol = ""
            for number in sorted(rows):
                if last is not None and number > last + 1:
                    lines.append("--")
                text, is_match = rows[number]
                symbol = symbols.get(number, "")
                if is_match and symbol and symbol != current_symbol:
                    lines.append(f"[{symbol}]")  # read it with read_symbol
                    current_symbol = symbol
                lines.append(f"{number}{':' if is_match else '-'}{text}")
                last = number
            blocks.append("\n".join(lines))
        return "\n\n".join(blocks)


class DirEntry(BaseModel):
    """One entry of a directory listing."""

    model_config = ConfigDict(frozen=True)

    path: str
    kind: Literal["file", "directory"]
    depth: int = Field(description="1 for immediate children.")
    size_bytes: int | None = None


class DirectoryListing(ToolResult):
    """A depth-limited directory tree."""

    path: str
    depth: int
    entries: list[DirEntry]
    truncated: bool = False

    def render(self) -> str:  # noqa: D102
        lines = [f"==> {self.path} (depth {self.depth}, {len(self.entries)} entries) <=="]
        for entry in self.entries:
            name = PurePosixPath(entry.path).name
            indent = "  " * (entry.depth - 1)
            if entry.kind == "directory":
                lines.append(f"{indent}{name}/")
            else:
                size = f"  ({human_size(entry.size_bytes)})" if entry.size_bytes is not None else ""
                lines.append(f"{indent}{name}{size}")
        if self.truncated:
            lines.append("[Truncated: entry limit reached. List a subdirectory or lower depth.]")
        return "\n".join(lines)


class FileList(ToolResult):
    """Paths matching a glob."""

    pattern: str
    base: str
    paths: list[str]
    truncated: bool = False

    def render(self) -> str:  # noqa: D102
        if not self.paths:
            return f"[No paths under {self.base} match {self.pattern!r}]"
        footer = f"[{len(self.paths)} path(s) under {self.base} match {self.pattern!r}]"
        if self.truncated:
            footer += "\n[Truncated: result limit reached. Use a more specific glob or path.]"
        return "\n".join([*self.paths, footer])


class Symbol(BaseModel):
    """A structural element of a file (class, function, heading, ...)."""

    model_config = ConfigDict(frozen=True)

    name: str
    qualified_name: str = Field(description="Accepted by read_symbol, e.g. 'Class.method'.")
    kind: str
    start_line: int
    end_line: int
    depth: int = Field(description="Nesting depth, 0 for top level.")
    signature: str | None = None


class OutlineResult(ToolResult):
    """The structural skeleton of a file."""

    path: str
    language: str
    total_lines: int
    symbols: list[Symbol]
    truncated: bool = False

    def render(self) -> str:  # noqa: D102
        header = f"==> {self.path} ({self.language}, {self.total_lines} lines) <=="
        if not self.symbols:
            return f"{header}\n[No symbols found]"
        width = len(str(self.total_lines))
        rows = [header]
        for symbol in self.symbols:
            span = f"{symbol.start_line:>{width}}-{symbol.end_line:<{width}}"
            label = symbol.signature or f"{symbol.kind} {symbol.name}"
            rows.append(f"{span}  {'  ' * symbol.depth}{label}")
        if self.truncated:
            rows.append("[Truncated: too many symbols for one response.]")
        return "\n".join(rows)


class SkeletonResult(ToolResult):
    """A source file with function bodies folded; kept lines keep their numbers."""

    path: str
    language: str
    total_lines: int
    shown_lines: int = Field(description="Source lines shown verbatim.")
    folded_lines: int = Field(description="Lines hidden inside folds.")
    etag: str
    content: str = Field(
        description="'<number>\\t<text>' rows; folds appear as '<start>-<end>\\t...'."
    )
    truncated: bool = False
    next_start: int | None = Field(default=None, description="First line not shown.")

    def render(self) -> str:  # noqa: D102
        header = (
            f"==> {self.path} ({self.language} skeleton: {self.shown_lines} of "
            f"{self.total_lines} lines shown, {self.folded_lines} folded; etag {self.etag}) <=="
        )
        parts = [header, self.content] if self.content else [header]
        if self.truncated and self.next_start is not None:
            parts.append(
                f"[Truncated at line {self.next_start}. Use outline, or read_lines for a range.]"
            )
        parts.append("[Open a folded range with read_lines(start, end) or read_symbol.]")
        return "\n".join(parts)


class PeekResult(ToolResult):
    """The shape of a data file: structure and statistics, not content."""

    path: str
    kind: str = Field(description="csv, json, toml, log, markdown or manifest.")
    size_bytes: int
    total_lines: int
    etag: str
    summary: str = Field(description="Format-specific shape summary, one fact per row.")
    truncated: bool = False

    def render(self) -> str:  # noqa: D102
        header = (
            f"==> {self.path} ({self.kind}, {human_size(self.size_bytes)}, "
            f"{self.total_lines} lines; etag {self.etag}) <=="
        )
        return f"{header}\n{self.summary}\n[Shape only. Use read_lines or grep for content.]"


class QueryResult(ToolResult):
    """One value of a JSON, YAML or TOML file, shown to a limited depth."""

    path: str
    key_path: str = Field(description="Canonical key path of the value ('' for the document).")
    language: str = Field(description="json, yaml or toml.")
    kind: Literal["object", "array", "value"]
    size: int = Field(description="Keys or elements of the value (0 for scalars).")
    start_line: int = Field(default=0, description="First line of the value (0 if unknown).")
    end_line: int = Field(default=0, description="Last line of the value (0 if unknown).")
    total_lines: int
    etag: str
    content: str = Field(description="'<key>: <value>' rows; deeper containers collapsed.")
    truncated: bool = False
    first_collapsed: str | None = Field(
        default=None, description="Key path of the first collapsed container, to query next."
    )
    note: str | None = None

    def render(self) -> str:  # noqa: D102
        facts = [f"{self.language} {self.kind}"]
        if self.kind != "value":
            unit = "key" if self.kind == "object" else "item"
            facts[0] += f", {self.size} {unit}{'' if self.size == 1 else 's'}"
        if self.start_line:
            facts.append(f"lines {self.start_line}-{self.end_line} of {self.total_lines}")
        facts.append(f"etag {self.etag}")
        target = f" :: {self.key_path}" if self.key_path else ""
        parts = [f"==> {self.path}{target} ({'; '.join(facts)}) <==", self.content]
        if self.note:
            parts.append(f"[{self.note}]")
        if self.truncated:
            parts.append("[Truncated: output limit. Query a deeper key path or lower depth.]")
        if self.first_collapsed is not None:
            parts.append(
                f"[Collapsed values: query one by key path, e.g. {self.first_collapsed!r}, "
                "or raise depth.]"
            )
        return "\n".join(parts)


class EditResult(ToolResult):
    """Outcome of an edit tool."""

    path: str
    applied: bool
    dry_run: bool
    changes: int = Field(description="Replacements made or lines changed.")
    etag: str | None = Field(default=None, description="The file's etag after the edit.")
    diff: str
    created: bool = False

    def render(self) -> str:  # noqa: D102
        if self.dry_run:
            head = f"Dry run: {self.changes} change(s) would be applied to {self.path}"
        elif self.created:
            head = f"Created {self.path} (etag {self.etag})"
        else:
            head = f"Applied {self.changes} change(s) to {self.path} (new etag {self.etag})"
        return f"{head}\n{self.diff}" if self.diff else f"{head}\n[No textual changes]"
