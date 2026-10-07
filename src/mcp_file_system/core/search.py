"""Content search (grep) with context, output modes and strict budgets.

The search *backend* is pluggable (:class:`SearchBackend`). Backends receive files that
have already been selected and vetted by the walker, so security policy stays in one
place; an alternative backend (e.g. one that shells out to ripgrep with an explicit
file list) only has to implement matching.
"""

from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Protocol

import regex

from mcp_file_system.config import Settings
from mcp_file_system.core.finder import FileFinder
from mcp_file_system.core.models import FileCount, GrepGroup, GrepHit, GrepMode, GrepResult
from mcp_file_system.core.render import clip_line
from mcp_file_system.core.templates import TemplateTally
from mcp_file_system.core.textio import detect_format, open_lines
from mcp_file_system.errors import InvalidArgumentError, PatternTimeoutError
from mcp_file_system.security.paths import ResolvedPath
from mcp_file_system.security.regex import Deadline, SafePattern, compile_pattern

MAX_CONTEXT_LINES = 20
BUDGET_EXHAUSTED = "time budget exhausted"
BUDGET_EXHAUSTED_SLOW_PATTERN = (
    "time budget exhausted while matching; if the pattern has nested quantifiers, simplify it"
)


@dataclass(frozen=True, slots=True)
class SearchRequest:
    """Everything a backend needs to know about one search."""

    pattern: SafePattern
    mode: GrepMode = "content"
    context_before: int = 0
    context_after: int = 0
    only_matching: bool = False
    max_matches: int = 100
    max_line_chars: int = 2000
    max_output_chars: int = 50_000


class SymbolLocator(Protocol):
    """Finds the innermost symbol enclosing given lines (implemented by the Outliner)."""

    def enclosing_symbols(self, path: str, lines: Iterable[int]) -> dict[int, str]:
        """Map line numbers of ``path`` to qualified symbol names."""
        ...


class SearchBackend(Protocol):
    """Strategy interface for matching a pattern against a set of vetted files."""

    def search(
        self, files: Iterable[ResolvedPath], request: SearchRequest, deadline: Deadline
    ) -> GrepResult:
        """Search ``files`` and return the results."""
        ...


@dataclass(slots=True)
class _PendingHit:
    path: str
    line: int
    text: str
    before: list[str]
    after: list[str] = field(default_factory=list)
    remaining_after: int = 0

    def freeze(self) -> GrepHit:
        return GrepHit(
            path=self.path, line=self.line, text=self.text, before=self.before, after=self.after
        )


@dataclass(slots=True)
class _Tally:
    hits: list[_PendingHit] = field(default_factory=list)
    files: list[FileCount] = field(default_factory=list)
    scanned: int = 0
    skipped: int = 0
    chars: int = 0
    stop_reason: str | None = None
    templates: TemplateTally = field(default_factory=TemplateTally)


class PythonSearchBackend:
    """Pure-Python backend: streams each file line by line through a :class:`SafePattern`."""

    def search(
        self, files: Iterable[ResolvedPath], request: SearchRequest, deadline: Deadline
    ) -> GrepResult:
        """Search ``files`` sequentially until done or a limit is hit."""
        tally = _Tally()
        for file in files:
            if deadline.expired:
                tally.stop_reason = BUDGET_EXHAUSTED
                break
            try:
                fmt = detect_format(file.path)
            except OSError:
                tally.skipped += 1
                continue
            if fmt is None:
                tally.skipped += 1
                continue
            tally.scanned += 1
            try:
                with open_lines(file.path, fmt) as lines:
                    self._scan(file.display, lines, request, deadline, tally)
            except OSError:
                tally.skipped += 1
            except PatternTimeoutError:
                tally.stop_reason = BUDGET_EXHAUSTED_SLOW_PATTERN
            if tally.stop_reason:
                break
        result = GrepResult(
            pattern=request.pattern.source,
            mode=request.mode,
            hits=[hit.freeze() for hit in tally.hits],
            files=tally.files,
            files_scanned=tally.scanned,
            files_skipped=tally.skipped,
            truncated=tally.stop_reason is not None,
            note=tally.stop_reason,
        )
        if request.mode == "collapsed":
            return _with_groups(result, tally.templates, request)
        return result

    def _scan(
        self,
        display: str,
        lines: Iterable[str],
        request: SearchRequest,
        deadline: Deadline,
        tally: _Tally,
    ) -> None:
        if request.mode == "content":
            self._collect_hits(display, lines, request, deadline, tally)
        elif request.mode == "collapsed":
            self._collect_templates(display, lines, request, deadline, tally)
        else:
            self._count_matches(display, lines, request, deadline, tally)

    @staticmethod
    def _count_matches(
        display: str,
        lines: Iterable[str],
        request: SearchRequest,
        deadline: Deadline,
        tally: _Tally,
    ) -> None:
        count = 0
        for line in lines:
            if deadline.expired:
                tally.stop_reason = BUDGET_EXHAUSTED
                break
            matches = _matches(request, line)
            if matches:
                count += len(matches)
                if request.mode == "files_with_matches":
                    break
        if not count:
            return
        if len(tally.files) >= request.max_matches:
            tally.stop_reason = f"max_matches={request.max_matches} files reached"
            return
        tally.files.append(FileCount(path=display, count=count))

    @staticmethod
    def _collect_hits(
        display: str,
        lines: Iterable[str],
        request: SearchRequest,
        deadline: Deadline,
        tally: _Tally,
    ) -> None:
        width = request.max_line_chars
        before: deque[str] = deque(maxlen=request.context_before or None)
        pending: list[_PendingHit] = []
        for number, line in enumerate(lines, start=1):
            if deadline.expired:
                tally.stop_reason = BUDGET_EXHAUSTED
                return
            if pending:
                pending = _feed_after_context(pending, clip_line(line, width), tally)
            for match in _matches(request, line):
                if len(tally.hits) >= request.max_matches:
                    tally.stop_reason = f"max_matches={request.max_matches} reached"
                    return
                if tally.chars >= request.max_output_chars:
                    tally.stop_reason = "output size budget reached"
                    return
                text = (
                    clip_line(match.group(), width)
                    if request.only_matching
                    else clip_line(line, width, focus=match.start())
                )
                hit = _PendingHit(
                    display, number, text, list(before), remaining_after=request.context_after
                )
                tally.hits.append(hit)
                tally.chars += len(text) + len(display) + sum(len(b) + 8 for b in before) + 8
                if request.context_after:
                    pending.append(hit)
            if request.context_before:
                before.append(clip_line(line, width))

    @staticmethod
    def _collect_templates(
        display: str,
        lines: Iterable[str],
        request: SearchRequest,
        deadline: Deadline,
        tally: _Tally,
    ) -> None:
        """Count every matching line by template; max_matches only limits groups shown."""
        width = request.max_line_chars
        for number, line in enumerate(lines, start=1):
            if deadline.expired:
                tally.stop_reason = BUDGET_EXHAUSTED
                return
            for match in _matches(request, line):
                text = match.group() if request.only_matching else line
                tally.templates.add(text[:width], display, number)


def _with_groups(
    result: GrepResult, templates: TemplateTally, request: SearchRequest
) -> GrepResult:
    """Attach the most frequent templates, bounded by max_matches and the output budget."""
    groups: list[GrepGroup] = []
    chars = 0
    notes = [f"{result.note}; counts cover only the lines scanned"] if result.note else []
    cut = False
    for template, stats in templates.most_common():
        if len(groups) >= request.max_matches:
            notes.append(f"showing the top {len(groups)}; raise max_matches for more")
            cut = True
            break
        group = GrepGroup(
            template=template,
            count=stats.count,
            files=stats.files,
            first_path=stats.first_path,
            first_line=stats.first_line,
            last_path=stats.last_path,
            last_line=stats.last_line,
        )
        chars += len(template) + len(stats.first_path) + len(stats.last_path) + 40
        if groups and chars > request.max_output_chars:
            notes.append(f"output size budget reached after {len(groups)} template(s)")
            cut = True
            break
        groups.append(group)
    return result.model_copy(
        update={
            "groups": groups,
            "template_count": len(templates),
            "ungrouped": templates.other,
            "note": "; ".join(notes) or None,
            "truncated": result.truncated or cut,
        }
    )


def _matches(request: SearchRequest, line: str) -> list[regex.Match[str]]:
    """All matches with only_matching, otherwise at most the first one."""
    if request.only_matching:
        return request.pattern.finditer(line)
    match = request.pattern.search(line)
    return [match] if match else []


def _feed_after_context(
    pending: list[_PendingHit], clipped: str, tally: _Tally
) -> list[_PendingHit]:
    """Append a line to every hit still waiting for after-context; drop completed ones."""
    for hit in pending:
        hit.after.append(clipped)
        hit.remaining_after -= 1
        tally.chars += len(clipped) + 8
    return [hit for hit in pending if hit.remaining_after > 0]


class Searcher:
    """Application service behind the ``grep`` tool: selects files, then delegates matching."""

    def __init__(
        self,
        finder: FileFinder,
        backend: SearchBackend,
        settings: Settings,
        symbols: SymbolLocator | None = None,
    ) -> None:
        self._finder = finder
        self._backend = backend
        self._settings = settings
        self._symbols = symbols

    def grep(
        self,
        pattern: str,
        path: str = ".",
        *,
        glob: str | None = None,
        fixed_string: bool = False,
        ignore_case: bool = False,
        context_before: int = 0,
        context_after: int = 0,
        only_matching: bool = False,
        mode: GrepMode = "content",
        max_matches: int | None = None,
        include_hidden: bool = False,
    ) -> GrepResult:
        """Search for ``pattern`` in ``path`` (a file, or a directory searched recursively)."""
        for name, value in (("context_before", context_before), ("context_after", context_after)):
            if not 0 <= value <= MAX_CONTEXT_LINES:
                raise InvalidArgumentError(f"{name} must be between 0 and {MAX_CONTEXT_LINES}.")
        if max_matches is not None and max_matches < 1:
            raise InvalidArgumentError("max_matches must be >= 1.")
        if mode == "collapsed" and (context_before or context_after):
            raise InvalidArgumentError(
                "Context lines are not available with output_mode='collapsed'. Read an "
                "example with read_lines, or use output_mode='content'."
            )
        deadline = Deadline(self._settings.search_timeout_s)
        compiled = compile_pattern(
            pattern,
            deadline=deadline,
            max_length=self._settings.max_pattern_length,
            fixed_string=fixed_string,
            ignore_case=ignore_case,
        )
        request = SearchRequest(
            pattern=compiled,
            mode=mode,
            context_before=context_before,
            context_after=context_after,
            only_matching=only_matching,
            max_matches=min(max_matches or 100, self._settings.max_matches),
            max_line_chars=self._settings.max_line_chars,
            max_output_chars=self._settings.max_output_chars,
        )
        files = self._finder.iter_files(path, glob=glob, include_hidden=include_hidden)
        result = self._backend.search(files, request, deadline)
        if mode == "content" and result.hits and self._symbols is not None:
            result = self._annotate_symbols(result, deadline)
        return result

    def _annotate_symbols(self, result: GrepResult, deadline: Deadline) -> GrepResult:
        """Add each hit's enclosing symbol, so the agent can go straight to read_symbol."""
        assert self._symbols is not None  # noqa: S101 - checked by the caller
        lines_by_path: dict[str, list[int]] = {}
        for hit in result.hits:
            lines_by_path.setdefault(hit.path, []).append(hit.line)
        names: dict[tuple[str, int], str] = {}
        for path, lines in lines_by_path.items():
            if deadline.expired:
                break  # symbols are a convenience; never exceed the search budget for them
            for line, name in self._symbols.enclosing_symbols(path, lines).items():
                names[(path, line)] = name
        if not names:
            return result
        hits = [
            hit.model_copy(update={"symbol": names.get((hit.path, hit.line), "")})
            for hit in result.hits
        ]
        return result.model_copy(update={"hits": hits})
