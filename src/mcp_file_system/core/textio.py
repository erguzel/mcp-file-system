"""Low-level text file handling: format detection, streaming lines, counting and splitting.

Line semantics are identical everywhere in the package: a line ends at ``\\n``, ``\\r\\n``
or a lone ``\\r`` (the same rule as :class:`io.TextIOWrapper` with ``newline=""``), and a
trailing line terminator does not start an extra empty line.
"""

import codecs
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache, partial
from pathlib import Path

from mcp_file_system.errors import BinaryFileError, FileTooLargeError
from mcp_file_system.security.paths import ResolvedPath

SNIFF_BYTES = 8192
_CHUNK_BYTES = 1 << 20

# UTF-32 BOMs must be tested before UTF-16: BOM_UTF32_LE starts with BOM_UTF16_LE.
_BOMS: tuple[tuple[bytes, str], ...] = (
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF32_LE, "utf-32"),
    (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)
# "utf-16"/"utf-32" read either byte order from the BOM but always write the machine's,
# so text is written back with the codec of the file's own order.
_BOM_ENCODINGS: dict[bytes, str] = {
    codecs.BOM_UTF32_LE: "utf-32-le",
    codecs.BOM_UTF32_BE: "utf-32-be",
    codecs.BOM_UTF16_LE: "utf-16-le",
    codecs.BOM_UTF16_BE: "utf-16-be",
}
# Encodings in which every newline is the single ASCII byte, so bytes can be counted.
_BYTE_COUNTABLE = frozenset({"utf-8", "utf-8-sig", "latin-1"})
NEWLINE_NAMES = {"\n": "LF", "\r\n": "CRLF", "\r": "CR"}

_EOL = re.compile(r"\r\n|\r|\n")
_LINE_WITH_END = re.compile(r"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+")


@dataclass(frozen=True, slots=True)
class TextFormat:
    """How a text file is encoded."""

    encoding: str
    """Python codec name used to decode the file."""
    newline: str
    """The first line terminator found (``"\\n"`` when the file has none)."""
    bom: bytes = b""
    """The UTF-16/UTF-32 byte-order mark, which fixes the byte order for writing.

    Empty otherwise, also for UTF-8 with BOM, whose ``utf-8-sig`` codec writes its own.
    """

    @property
    def newline_name(self) -> str:
        """``"LF"``, ``"CRLF"`` or ``"CR"``."""
        return NEWLINE_NAMES[self.newline]


def detect_format(path: Path) -> TextFormat | None:
    """Sniff the start of a file; return ``None`` if it looks binary."""
    with path.open("rb") as handle:
        sample = handle.read(SNIFF_BYTES)
    return detect_format_bytes(sample)


def detect_format_bytes(sample: bytes) -> TextFormat | None:
    """Detect the text format of a byte sample; return ``None`` if it looks binary.

    A byte-order mark wins; otherwise any NUL byte means binary, valid UTF-8 means
    UTF-8, and anything else falls back to Latin-1 (which decodes every byte).
    """
    for bom, encoding in _BOMS:
        if sample.startswith(bom):
            newline = _detect_newline(sample.decode(encoding, errors="replace"))
            return TextFormat(encoding, newline, bom if bom in _BOM_ENCODINGS else b"")
    if b"\x00" in sample:
        return None
    decoder = codecs.getincrementaldecoder("utf-8")()
    try:
        # final=False tolerates a multi-byte character cut off at the end of the sample.
        decoder.decode(sample, final=False)
        encoding = "utf-8"
    except UnicodeDecodeError:
        encoding = "latin-1"
    return TextFormat(encoding, _detect_newline(sample.decode(encoding, errors="replace")))


def _detect_newline(text: str) -> str:
    match = _EOL.search(text)
    return match.group() if match else "\n"


def require_text(resolved: ResolvedPath) -> TextFormat:
    """Return the text format of a file, or raise :class:`BinaryFileError`."""
    fmt = detect_format(resolved.path)
    if fmt is None:
        raise BinaryFileError(
            f"{resolved.display} looks like a binary file; text tools cannot read it."
        )
    return fmt


def check_size(resolved: ResolvedPath, size: int, max_bytes: int) -> None:
    """Raise :class:`FileTooLargeError` if ``size`` exceeds ``max_bytes``."""
    if size > max_bytes:
        raise FileTooLargeError(
            f"{resolved.display} is {size} bytes; operations that load the whole file are "
            f"limited to {max_bytes} bytes. Use read_lines, read_between or grep instead."
        )


def strip_eol(line: str) -> str:
    """Remove one trailing line terminator."""
    if line.endswith("\r\n"):
        return line[:-2]
    if line.endswith(("\n", "\r")):
        return line[:-1]
    return line


@contextmanager
def open_lines(path: Path, fmt: TextFormat) -> Iterator[Iterator[str]]:
    """Stream a file's lines (without terminators); undecodable bytes become U+FFFD."""
    with path.open(encoding=fmt.encoding, errors="replace", newline="") as handle:
        yield (strip_eol(line) for line in handle)


def read_text(path: Path, fmt: TextFormat, *, errors: str = "replace") -> str:
    """Decode a whole file without translating line terminators."""
    return path.read_bytes().decode(fmt.encoding, errors=errors)


def encode_text(text: str, fmt: TextFormat, *, errors: str) -> bytes:
    """Encode ``text`` as a file of format ``fmt``, keeping its BOM and byte order.

    Raises:
        UnicodeEncodeError: ``text`` holds characters the encoding cannot store.
    """
    if fmt.bom:
        return fmt.bom + text.encode(_BOM_ENCODINGS[fmt.bom], errors=errors)
    return text.encode(fmt.encoding, errors=errors)


def split_lines(text: str) -> list[str]:
    """Split text into lines without terminators (no phantom trailing empty line)."""
    if not text:
        return []
    parts = _EOL.split(text)
    if parts[-1] == "":
        parts.pop()
    return parts


def split_lines_keepends(text: str) -> list[str]:
    """Split text into lines, keeping each line's terminator."""
    return _LINE_WITH_END.findall(text)


def count_lines(path: Path, fmt: TextFormat, stat: os.stat_result) -> int:
    """Count lines, memoized on the file's identity, mtime and size."""
    return _count_lines_cached(os.fspath(path), stat.st_mtime_ns, stat.st_size, fmt)


@lru_cache(maxsize=512)
def _count_lines_cached(path: str, _mtime_ns: int, _size: int, fmt: TextFormat) -> int:
    if fmt.encoding in _BYTE_COUNTABLE:
        return _count_lines_bytes(Path(path))
    with open_lines(Path(path), fmt) as lines:
        return sum(1 for _ in lines)


def _count_lines_bytes(path: Path) -> int:
    lines = 0
    previous_ended_with_cr = False
    last_byte = b""
    with path.open("rb") as handle:
        for chunk in iter(partial(handle.read, _CHUNK_BYTES), b""):
            lines += chunk.count(b"\n") + chunk.count(b"\r") - chunk.count(b"\r\n")
            if previous_ended_with_cr and chunk.startswith(b"\n"):
                lines -= 1  # a "\r\n" split across two chunks was counted twice
            previous_ended_with_cr = chunk.endswith(b"\r")
            last_byte = chunk[-1:]
    if last_byte and last_byte not in {b"\n", b"\r"}:
        lines += 1
    return lines
