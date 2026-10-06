"""Grammars whose scanners misuse <ctype.h> never see code points above U+00FF."""

# ruff: noqa: RUF001  (the Turkish dotless i in the test data is intentional)

import pytest
from hypothesis import given
from hypothesis import strategies as st

from mcp_file_system.core.outline.treesitter import (
    ShellOutlineProvider,
    TreeSitterMarkdownOutlineProvider,
)
from mcp_file_system.core.outline.treesitter._base import (
    TreeSitterOutlineProvider,
    mask_wide_chars,
)

# Inputs that crashed the Linux CI: a digit or "{" followed by a wide character makes the
# scanner call isdigit() on that code point.
CRASHERS = ["1\U0010ffff. item\n", "{\U000f6c2c", "{1ş}", "12中) x\n"]
MASKED = [TreeSitterMarkdownOutlineProvider, ShellOutlineProvider]


class TestMaskWideChars:
    def test_keeps_ascii_and_latin1(self) -> None:
        source = "café üÿ \t\n#1".encode()
        assert mask_wide_chars(source) == source

    def test_replaces_wider_code_points_byte_for_byte(self) -> None:
        source = "aşb中c\U0001f389d\n".encode()
        assert mask_wide_chars(source) == b"axxbxxxcxxxxd\n"

    @given(st.text())
    def test_preserves_length_and_line_breaks(self, text: str) -> None:
        source = text.encode("utf-8", errors="replace")
        masked = mask_wide_chars(source)
        assert len(masked) == len(source)
        assert [i for i, b in enumerate(masked) if b == 0x0A] == [
            i for i, b in enumerate(source) if b == 0x0A
        ]
        assert max(masked.decode(), default="\x00") <= "\xff"


@pytest.mark.parametrize("factory", MASKED)
@pytest.mark.parametrize("text", CRASHERS)
def test_parser_never_sees_wide_chars(factory: type[TreeSitterOutlineProvider], text: str) -> None:
    provider = factory()
    root, source = provider.parse(text)
    assert max((root.text or b"").decode(), default="\x00") <= "\xff"
    assert source == text.encode()  # extractors still read the original text
    provider.outline(text)


def test_markdown_titles_keep_their_characters() -> None:
    text = "# Çalışma 日本\n\nmetin \U0001f389\n\nİkinci\n===\n"
    symbols = TreeSitterMarkdownOutlineProvider().outline(text)
    assert [(s.name, s.start_line) for s in symbols] == [
        ("Çalışma 日本", 1),
        ("İkinci", 5),
    ]


def test_shell_function_names_keep_their_characters() -> None:
    text = "# çözüm \U0001f389\nçalış() {\n  echo 中\n}\n"
    symbols = ShellOutlineProvider().outline(text)
    assert [(s.name, s.start_line, s.end_line) for s in symbols] == [("çalış", 2, 4)]
