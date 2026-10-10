import codecs
from pathlib import Path

import pytest

from conftest import write
from mcp_file_system.core import textio
from mcp_file_system.core.textio import (
    TextFormat,
    check_size,
    count_lines,
    detect_format,
    detect_format_bytes,
    encode_text,
    open_lines,
    read_text,
    require_text,
    split_lines,
    split_lines_keepends,
    strip_eol,
)
from mcp_file_system.errors import BinaryFileError, FileTooLargeError
from mcp_file_system.security import ResolvedPath


def _resolved(path: Path) -> ResolvedPath:
    return ResolvedPath(path=path, root=path.parent, display=path.name)


@pytest.mark.parametrize(
    ("sample", "encoding", "newline", "bom"),
    [
        (b"plain\n", "utf-8", "\n", b""),
        (b"crlf\r\nline\r\n", "utf-8", "\r\n", b""),
        (b"old mac\rline\r", "utf-8", "\r", b""),
        (b"no newline", "utf-8", "\n", b""),
        ("café\n".encode(), "utf-8", "\n", b""),
        # utf-8-sig writes its own BOM, so none is recorded.
        (codecs.BOM_UTF8 + b"bom\r\n", "utf-8-sig", "\r\n", b""),
        (codecs.BOM_UTF16_LE + "wide\n".encode("utf-16-le"), "utf-16", "\n", codecs.BOM_UTF16_LE),
        (codecs.BOM_UTF16_BE + "wide\n".encode("utf-16-be"), "utf-16", "\n", codecs.BOM_UTF16_BE),
        (
            codecs.BOM_UTF32_LE + "wider\r\n".encode("utf-32-le"),
            "utf-32",
            "\r\n",
            codecs.BOM_UTF32_LE,
        ),
        (
            codecs.BOM_UTF32_BE + "wider\r\n".encode("utf-32-be"),
            "utf-32",
            "\r\n",
            codecs.BOM_UTF32_BE,
        ),
        (b"caf\xe9\n", "latin-1", "\n", b""),
    ],
)
def test_detect_format(sample: bytes, encoding: str, newline: str, bom: bytes) -> None:
    assert detect_format_bytes(sample) == TextFormat(encoding, newline, bom)


@pytest.mark.parametrize(
    ("bom", "codec"),
    [
        (codecs.BOM_UTF16_LE, "utf-16-le"),
        (codecs.BOM_UTF16_BE, "utf-16-be"),
        (codecs.BOM_UTF32_LE, "utf-32-le"),
        (codecs.BOM_UTF32_BE, "utf-32-be"),
        (codecs.BOM_UTF8, "utf-8"),
        (b"", "utf-8"),
        (b"", "latin-1"),
    ],
)
def test_encode_text_round_trips_the_detected_format(bom: bytes, codec: str) -> None:
    raw = bom + "x = 'é'\r\n".encode(codec)
    fmt = detect_format_bytes(raw)
    assert fmt is not None
    text = raw.decode(fmt.encoding)
    assert encode_text(text, fmt, errors="strict") == raw


def test_truncated_multibyte_at_sample_end_is_still_utf8() -> None:
    sample = "aé".encode()[:-1]  # cut in the middle of a 2-byte character
    fmt = detect_format_bytes(sample)
    assert fmt is not None
    assert fmt.encoding == "utf-8"


def test_nul_bytes_mean_binary() -> None:
    assert detect_format_bytes(b"\x89PNG\r\n\x1a\n\x00\x00") is None


def test_newline_names() -> None:
    assert TextFormat("utf-8", "\r\n").newline_name == "CRLF"


def test_detect_format_from_file_and_require_text(tmp_path: Path) -> None:
    text = write(tmp_path / "a.txt", "hello\n")
    binary = write(tmp_path / "b.bin", b"\x00\x01\x02")
    assert detect_format(text) == TextFormat("utf-8", "\n")
    assert require_text(_resolved(text)).encoding == "utf-8"
    with pytest.raises(BinaryFileError, match=r"b\.bin"):
        require_text(_resolved(binary))


def test_check_size(tmp_path: Path) -> None:
    resolved = _resolved(tmp_path / "x")
    check_size(resolved, 10, 10)
    with pytest.raises(FileTooLargeError, match="limited to 10 bytes"):
        check_size(resolved, 11, 10)


@pytest.mark.parametrize(
    ("line", "expected"),
    [("a\r\n", "a"), ("a\n", "a"), ("a\r", "a"), ("a", "a"), ("", ""), ("\r\n", "")],
)
def test_strip_eol(line: str, expected: str) -> None:
    assert strip_eol(line) == expected


@pytest.mark.parametrize(
    ("text", "lines"),
    [
        ("", []),
        ("a", ["a"]),
        ("a\n", ["a"]),
        ("a\nb", ["a", "b"]),
        ("a\r\nb\rc\n", ["a", "b", "c"]),
        ("\n\n", ["", ""]),
        ("a\x0cb\u2028c\n", ["a\x0cb\u2028c"]),  # only CR/LF terminate lines
    ],
)
def test_split_lines(text: str, lines: list[str]) -> None:
    assert split_lines(text) == lines


@pytest.mark.parametrize(
    ("text", "lines"),
    [
        ("", []),
        ("a", ["a"]),
        ("a\nb", ["a\n", "b"]),
        ("a\r\nb\r\n", ["a\r\n", "b\r\n"]),
        ("\n\r", ["\n", "\r"]),
    ],
)
def test_split_lines_keepends(text: str, lines: list[str]) -> None:
    assert split_lines_keepends(text) == lines
    assert "".join(lines) == text


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (b"", 0),
        (b"one", 1),
        (b"one\n", 1),
        (b"one\ntwo", 2),
        (b"a\r\nb\r\n", 2),
        (b"a\rb\rc", 3),
        (b"a\r\nb\nc\rd", 4),
        (b"\n\n\n", 3),
    ],
)
def test_count_lines_matches_streaming_semantics(
    tmp_path: Path, content: bytes, expected: int
) -> None:
    path = write(tmp_path / "f.txt", content)
    fmt = detect_format(path)
    assert fmt is not None
    assert count_lines(path, fmt, path.stat()) == expected
    with open_lines(path, fmt) as lines:
        assert sum(1 for _ in lines) == expected


def test_count_lines_handles_crlf_split_across_chunks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(textio, "_CHUNK_BYTES", 4)
    path = write(tmp_path / "f.txt", b"abc\r\ndef\r\nxy")  # "\r" ends the first chunk
    assert textio._count_lines_bytes(path) == 3


def test_count_lines_utf16_counts_text_lines(tmp_path: Path) -> None:
    path = write(tmp_path / "w.txt", "a\nb\nc".encode("utf-16"))
    fmt = detect_format(path)
    assert fmt is not None
    assert count_lines(path, fmt, path.stat()) == 3


def test_count_lines_cache_invalidates_on_change(tmp_path: Path) -> None:
    path = write(tmp_path / "f.txt", "a\n")
    fmt = TextFormat("utf-8", "\n")
    assert count_lines(path, fmt, path.stat()) == 1
    write(path, "a\nb\nc\n")
    assert count_lines(path, fmt, path.stat()) == 3


def test_open_lines_replaces_undecodable_bytes(tmp_path: Path) -> None:
    path = write(tmp_path / "f.txt", b"ok\nbad \xff\n")
    with open_lines(path, TextFormat("utf-8", "\n")) as lines:
        assert list(lines) == ["ok", "bad �"]


def test_read_text_keeps_line_terminators(tmp_path: Path) -> None:
    path = write(tmp_path / "f.txt", b"a\r\nb\r\n")
    assert read_text(path, TextFormat("utf-8", "\r\n")) == "a\r\nb\r\n"
