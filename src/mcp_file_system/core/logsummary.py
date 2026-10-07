"""Application service behind the ``log_summary`` tool."""

from mcp_file_system.config import Settings
from mcp_file_system.core.etag import compute_etag
from mcp_file_system.core.models import LogSummaryResult, TemplateGroup
from mcp_file_system.core.templates import TemplateTally
from mcp_file_system.core.textio import count_lines, open_lines, require_text
from mcp_file_system.errors import InvalidArgumentError
from mcp_file_system.security.paths import PathGuard
from mcp_file_system.security.regex import Deadline


class LogSummarizer:
    """Reduces a log file to its most frequent line templates.

    The file is streamed once, from ``start`` until its end or until the search time
    budget is used up; a partial summary says where to continue.
    """

    def __init__(self, guard: PathGuard, settings: Settings) -> None:
        self._guard = guard
        self._settings = settings

    def summarize(self, path: str, *, limit: int = 20, start: int = 1) -> LogSummaryResult:
        """Count the lines of ``path`` per template and return the ``limit`` most frequent.

        Raises:
            InvalidArgumentError: ``limit`` or ``start`` is out of range.
        """
        if limit < 1:
            raise InvalidArgumentError("limit must be >= 1.")
        if start < 1:
            raise InvalidArgumentError("start must be >= 1.")
        resolved = self._guard.resolve_file(path)
        stat = resolved.path.stat()
        fmt = require_text(resolved)
        total = count_lines(resolved.path, fmt, stat)
        if start > max(total, 1):
            raise InvalidArgumentError(f"start={start} is past the end ({total} lines).")
        tally = TemplateTally()
        deadline = Deadline(self._settings.search_timeout_s)
        width = self._settings.max_line_chars
        last = start - 1
        next_start: int | None = None
        with open_lines(resolved.path, fmt) as lines:
            for number, line in enumerate(lines, start=1):
                if number < start:
                    continue
                if deadline.expired:
                    next_start = number
                    break
                last = number
                if line.strip():
                    tally.add(line[:width], resolved.display, number)
        groups, note = self._top_groups(tally, min(limit, self._settings.max_results))
        return LogSummaryResult(
            path=resolved.display,
            total_lines=total,
            etag=compute_etag(stat),
            scanned_from=start,
            scanned_to=last,
            groups=groups,
            template_count=len(tally),
            ungrouped=tally.other,
            next_start=next_start,
            truncated=next_start is not None or len(groups) < len(tally),
            note=note,
        )

    def _top_groups(self, tally: TemplateTally, limit: int) -> tuple[list[TemplateGroup], str]:
        """The most frequent templates, bounded by ``limit`` and the output budget."""
        groups: list[TemplateGroup] = []
        chars = 0
        for template, stats in tally.most_common():
            if len(groups) >= limit:
                return groups, "raise limit for more"
            chars += len(template) + 40
            if groups and chars > self._settings.max_output_chars:
                return groups, "output size budget reached"
            groups.append(
                TemplateGroup(
                    template=template,
                    count=stats.count,
                    first_line=stats.first_line,
                    last_line=stats.last_line,
                )
            )
        return groups, ""
