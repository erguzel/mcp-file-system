"""CSV/TSV preview: header, row count, sample rows and guessed column types."""

import csv
import re
from pathlib import Path

from mcp_file_system.core.textio import TextFormat
from mcp_file_system.errors import ParseError

MAX_SCAN_ROWS = 50_000
MAX_COLUMNS = 40
SAMPLE_ROWS = 3
_CELL_CHARS = 40
_INT = re.compile(r"[+-]?\d+")
_FLOAT = re.compile(r"[+-]?(\d+\.\d*|\.\d+|\d+)([eE][+-]?\d+)?")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2})?.*)?")
_BOOLS = frozenset({"true", "false"})


def _kind(cell: str) -> str:
    text = cell.strip()
    if _INT.fullmatch(text):
        return "int"
    if _FLOAT.fullmatch(text):
        return "float"
    if text.lower() in _BOOLS:
        return "bool"
    if _DATE.fullmatch(text):
        return "date"
    return "str"


def _combine(kinds: set[str]) -> str:
    if not kinds:
        return "empty"
    if kinds == {"int", "float"}:
        return "float"
    return next(iter(kinds)) if len(kinds) == 1 else "mixed"


def _column_label(kinds: set[str], empty: int) -> str:
    return _combine(kinds) + (f", {empty} empty" if empty else "")


def _delimiter(path: Path, fmt: TextFormat) -> str:
    if path.suffix.lower() == ".tsv":
        return "\t"
    with path.open(encoding=fmt.encoding, errors="replace", newline="") as handle:
        sample = handle.read(4096)
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


def summarize_csv(path: Path, fmt: TextFormat) -> list[str]:
    """Summarize a delimited file, scanning at most :data:`MAX_SCAN_ROWS` data rows."""
    delimiter = _delimiter(path, fmt)
    kinds: list[set[str]] = []
    empties: list[int] = []
    samples: list[list[str]] = []
    header: list[str] = []
    rows = 0
    complete = True
    try:
        with path.open(encoding=fmt.encoding, errors="replace", newline="") as handle:
            reader = csv.reader(handle, delimiter=delimiter)
            header = next(reader, [])
            kinds = [set() for _ in header]
            empties = [0] * len(header)
            for record in reader:
                if rows >= MAX_SCAN_ROWS:
                    complete = False
                    break
                if not record:
                    continue
                rows += 1
                if len(samples) < SAMPLE_ROWS:
                    samples.append(record)
                for index, cell in enumerate(record[: len(header)]):
                    if cell.strip():
                        kinds[index].add(_kind(cell))
                    else:
                        empties[index] += 1
    except csv.Error as exc:
        raise ParseError(f"invalid delimited text ({str(exc)[:100]})") from None
    if not header:
        return ["(empty file)"]
    count = f"{rows}" if complete else f"more than {rows} (scanned the first {rows})"
    shown = header[:MAX_COLUMNS]
    out = [
        f"delimiter: {delimiter!r}; columns: {len(header)}; data rows: {count}",
        "columns: "
        + ", ".join(
            f"{name[:_CELL_CHARS]} ({_column_label(kinds[i], empties[i])})"
            for i, name in enumerate(shown)
        )
        + (f", ...+{len(header) - MAX_COLUMNS} more" if len(header) > MAX_COLUMNS else ""),
    ]
    for number, record in enumerate(samples, start=1):
        cells = [cell[:_CELL_CHARS] for cell in record[:MAX_COLUMNS]]
        out.append(f"sample {number}: " + " | ".join(cells))
    return out
