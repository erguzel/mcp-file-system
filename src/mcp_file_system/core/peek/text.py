"""Log and Markdown previews, streamed with fixed scan budgets."""

import re
from collections import Counter
from collections.abc import Iterator
from pathlib import Path

from mcp_file_system.core.textio import TextFormat, split_lines

MAX_SCAN_LINES = 200_000
TAIL_BYTES = 64 * 1024
_SCAN_CHARS = 300  # only the start of a line is searched, which bounds regex work
_TIMESTAMP = re.compile(
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?"
)
_LEVEL = re.compile(
    r"\b(TRACE|DEBUG|INFO|NOTICE|WARN|WARNING|ERROR|CRITICAL|FATAL)\b", re.IGNORECASE
)
_LEVEL_ORDER = ("FATAL", "CRITICAL", "ERROR", "WARN", "WARNING", "NOTICE", "INFO", "DEBUG", "TRACE")
_HEADING = re.compile(r"(#{1,6})\s+\S")
_FENCE = re.compile(r"\s{0,3}(```|~~~)")


def _timestamp(line: str) -> str | None:
    match = _TIMESTAMP.search(line[:_SCAN_CHARS])
    return match.group() if match else None


def _tail_lines(path: Path, fmt: TextFormat) -> list[str]:
    size = path.stat().st_size
    with path.open("rb") as handle:
        handle.seek(max(0, size - TAIL_BYTES))
        chunk = handle.read().decode(fmt.encoding, errors="replace")
    return split_lines(chunk)[1:] if size > TAIL_BYTES else split_lines(chunk)


def summarize_log(lines: Iterator[str], path: Path, fmt: TextFormat) -> list[str]:
    """Summarize a log: time span and level counts over the first scanned lines."""
    counts: Counter[str] = Counter()
    first: str | None = None
    scanned = 0
    exhausted = True
    for line in lines:
        if scanned >= MAX_SCAN_LINES:
            exhausted = False
            break
        scanned += 1
        head = line[:_SCAN_CHARS]
        first = first or _timestamp(head)
        level = _LEVEL.search(head)
        if level:
            name = level.group(1).upper()
            counts["WARN" if name == "WARNING" else name] += 1
    last = first
    for line in reversed(_tail_lines(path, fmt)):
        found = _timestamp(line)
        if found:
            last = found
            break
    scope = f"{scanned} lines" if exhausted else f"first {scanned} lines"
    out = [f"time span: {first or 'no timestamps found'}" + (f" .. {last}" if first else "")]
    ordered = [name for name in _LEVEL_ORDER if counts[name]]
    levels = ", ".join(f"{name}: {counts[name]}" for name in ordered)
    out.append(f"levels ({scope}): {levels or 'none recognized'}")
    return out


def summarize_markdown(lines: Iterator[str]) -> list[str]:
    """Summarize a Markdown file: front matter keys and heading counts per level."""
    front: list[str] = []
    levels: Counter[int] = Counter()
    title = ""
    fenced = False
    in_front = False
    for number, line in enumerate(lines, start=1):
        if number > MAX_SCAN_LINES:
            break
        if number == 1 and line.strip() == "---":
            in_front = True
            continue
        if in_front:
            if line.strip() in ("---", "..."):
                in_front = False
            elif re.match(r"[A-Za-z_][\w-]*\s*:", line):
                front.append(line.split(":", 1)[0].strip())
            continue
        if _FENCE.match(line):
            fenced = not fenced
        elif not fenced and (match := _HEADING.match(line)):
            levels[len(match.group(1))] += 1
            if not title and len(match.group(1)) == 1:
                title = line.lstrip("# ").strip()[:100]
    out = [f"front matter keys: {', '.join(front[:20]) if front else 'none'}"]
    total = sum(levels.values())
    per_level = ", ".join(f"h{level}: {levels[level]}" for level in sorted(levels))
    out.append(f"headings: {total}" + (f" ({per_level})" if total else ""))
    if title:
        out.append(f"title: {title}")
    return out
