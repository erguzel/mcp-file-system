from pathlib import Path

import pytest

from conftest import ContextFactory, write
from mcp_file_system.context import AppContext
from mcp_file_system.core.models import Symbol
from mcp_file_system.core.outline import OutlineRegistry, default_registry
from mcp_file_system.core.outline.base import OutlineProvider
from mcp_file_system.core.outline.markdown import RegexMarkdownOutlineProvider
from mcp_file_system.core.outline.python import PythonOutlineProvider
from mcp_file_system.core.outline.treesitter import TreeSitterMarkdownOutlineProvider
from mcp_file_system.errors import (
    FileTooLargeError,
    InvalidArgumentError,
    ParseError,
    SymbolNotFoundError,
)

PY = '''\
"""Module."""
from typing import TYPE_CHECKING, overload

if TYPE_CHECKING:
    def typed_only() -> None: ...

try:
    import fast
except ImportError:
    def fallback() -> None: ...


@decorator
class Shape(Base, metaclass=Meta):
    """A shape."""

    def area(self) -> float:
        def helper():
            return 1
        return helper()

    @property
    def name(self) -> str:
        return "shape"

    @name.setter
    def name(self, value: str) -> None:
        pass

    class Inner:
        pass


async def fetch[T](url: str, *, retries: int = 3) -> T:
    return await get(url)


def area():
    return 0
'''


def by_name(symbols: list[Symbol]) -> dict[str, Symbol]:
    return {s.qualified_name: s for s in symbols}


class TestPythonProvider:
    def test_symbols_kinds_and_spans(self) -> None:
        symbols = PythonOutlineProvider().outline(PY)
        assert [s.qualified_name for s in symbols] == [
            "typed_only",
            "fallback",
            "Shape",
            "Shape.area",
            "Shape.name",
            "Shape.name",
            "Shape.Inner",
            "fetch",
            "area",
        ]
        table = by_name(symbols)
        shape = table["Shape"]
        assert (shape.kind, shape.start_line, shape.depth) == ("class", 13, 0)  # decorator line
        assert shape.signature == "class Shape(Base, metaclass=Meta)"
        assert table["Shape.area"].kind == "method"
        assert table["Shape.area"].depth == 1
        assert table["Shape.Inner"].kind == "class"
        assert table["fetch"].signature == ("async def fetch[T](url: str, *, retries: int=3) -> T")
        assert table["area"].kind == "function"

    def test_nested_functions_are_omitted(self) -> None:
        names = [s.name for s in PythonOutlineProvider().outline(PY)]
        assert "helper" not in names

    @pytest.mark.parametrize("keyword", ["except", "except*"])
    def test_try_blocks_are_listed_in_source_order(self, keyword: str) -> None:
        source = (
            "try:\n"
            "    def in_try(): ...\n"
            f"{keyword} ValueError:\n"
            "    def first_handler(): ...\n"
            f"{keyword} KeyError:\n"
            "    def second_handler(): ...\n"
            "else:\n"
            "    def in_else(): ...\n"
            "finally:\n"
            "    def in_finally(): ...\n"
        )
        symbols = PythonOutlineProvider().outline(source)
        assert [s.name for s in symbols] == [
            "in_try",
            "first_handler",
            "second_handler",
            "in_else",
            "in_finally",
        ]
        starts = [s.start_line for s in symbols]
        assert starts == sorted(starts)

    def test_blocks_in_a_class_keep_source_order_and_scope(self) -> None:
        source = (
            "class K:\n"
            "    if a:\n"
            "        def in_if(self): ...\n"
            "    else:\n"
            "        def in_else(self): ...\n"
            "    try:\n"
            "        def in_try(self): ...\n"
            "    except ValueError:\n"
            "        def in_except(self): ...\n"
        )
        symbols = PythonOutlineProvider().outline(source)
        assert [(s.qualified_name, s.kind, s.depth) for s in symbols] == [
            ("K", "class", 0),
            ("K.in_if", "method", 1),
            ("K.in_else", "method", 1),
            ("K.in_try", "method", 1),
            ("K.in_except", "method", 1),
        ]

    def test_long_signatures_are_clipped(self) -> None:
        params = ", ".join(f"p{n}: int" for n in range(60))
        symbol = PythonOutlineProvider().outline(f"def f({params}): pass\n")[0]
        assert symbol.signature is not None
        assert len(symbol.signature) == 200
        assert symbol.signature.endswith("…")

    def test_syntax_error(self) -> None:
        with pytest.raises(ParseError, match="line 1"):
            PythonOutlineProvider().outline("def broken(:\n")

    def test_nul_bytes(self) -> None:
        with pytest.raises(ParseError):
            PythonOutlineProvider().outline("x = 1\x00\n")


MD = """\
---
title: "# not a heading"
---
# Guide

Intro.

## Install
```bash
# not a heading either
```
### From source ###
Steps.

~~~
## fenced
~~~
## Usage
Text.
#NoSpace is not a heading
#
# Appendix
"""


MARKDOWN_PROVIDERS = [
    pytest.param(RegexMarkdownOutlineProvider, id="regex"),
    pytest.param(TreeSitterMarkdownOutlineProvider, id="tree-sitter"),
]


@pytest.mark.parametrize("provider_class", MARKDOWN_PROVIDERS)
class TestMarkdownProviders:
    """Behavior both Markdown providers must share."""

    def test_headings_sections_and_qualified_names(
        self, provider_class: type[OutlineProvider]
    ) -> None:
        symbols = provider_class().outline(MD)
        assert [(s.qualified_name, s.start_line, s.end_line, s.depth) for s in symbols] == [
            ("Guide", 4, 21, 0),
            ("Guide > Install", 8, 17, 1),
            ("Guide > Install > From source", 12, 17, 2),
            ("Guide > Usage", 18, 21, 1),
            ("Appendix", 22, 22, 0),
        ]
        assert symbols[2].signature == "### From source"
        assert symbols[2].kind == "h3"

    def test_unterminated_front_matter_is_content(
        self, provider_class: type[OutlineProvider]
    ) -> None:
        symbols = provider_class().outline("---\n# Title\n")
        assert [s.name for s in symbols] == ["Title"]

    def test_empty_document(self, provider_class: type[OutlineProvider]) -> None:
        assert provider_class().outline("") == []

    def test_crlf_line_numbers(self, provider_class: type[OutlineProvider]) -> None:
        symbols = provider_class().outline("# A\r\ntext\r\n## B\r\nmore\r\n")
        assert [(s.name, s.start_line, s.end_line) for s in symbols] == [
            ("A", 1, 4),
            ("B", 3, 4),
        ]


class TestTreeSitterMarkdown:
    """Cases only the grammar-based provider gets right."""

    def test_setext_headings(self) -> None:
        text = "Title\n=====\n\nIntro.\n\nPart\n----\nBody.\n"
        symbols = TreeSitterMarkdownOutlineProvider().outline(text)
        assert [(s.qualified_name, s.kind, s.start_line, s.end_line) for s in symbols] == [
            ("Title", "h1", 1, 8),
            ("Title > Part", "h2", 6, 8),
        ]

    def test_html_blocks_and_indented_code_are_not_headings(self) -> None:
        text = "# Real\n<div>\n# inside html\n</div>\n\n    # indented code\n"
        assert [s.name for s in TreeSitterMarkdownOutlineProvider().outline(text)] == ["Real"]

    def test_headings_in_quotes_and_lists_are_not_sections(self) -> None:
        text = "# Top\n> # quoted\n\n- # in list\n"
        assert [s.name for s in TreeSitterMarkdownOutlineProvider().outline(text)] == ["Top"]

    def test_closing_hashes_and_inline_markup(self) -> None:
        text = "## Use `grep` here ##\n"
        symbols = TreeSitterMarkdownOutlineProvider().outline(text)
        assert symbols[0].name == "Use `grep` here"


class TestRegistry:
    def test_lookup_by_suffix_case_insensitive(self) -> None:
        registry = default_registry()
        provider = registry.for_path(Path("README.MD"))
        assert provider is not None
        assert provider.language == "markdown"
        assert registry.for_path(Path("x.rs")) is None
        assert ".py" in registry.supported_suffixes

    def test_later_registration_wins(self) -> None:
        class Custom:
            language = "custom"
            extensions = frozenset({".py"})

            def outline(self, text: str) -> list[Symbol]:
                return []

        registry = OutlineRegistry([PythonOutlineProvider(), Custom()])
        provider = registry.for_path(Path("a.py"))
        assert provider is not None
        assert provider.language == "custom"


class TestOutliner:
    def test_outline_result(self, ctx: AppContext, root: Path) -> None:
        write(root / "shapes.py", PY)
        result = ctx.outliner.outline("shapes.py")
        assert result.language == "python"
        assert result.total_lines == PY.count("\n")
        rendered = result.render()
        assert "class Shape(Base, metaclass=Meta)" in rendered
        assert "    def area(self) -> float" in rendered

    def test_unsupported_suffix(self, ctx: AppContext, root: Path) -> None:
        write(root / "main.rs", "fn main() {}\n")
        with pytest.raises(InvalidArgumentError, match="Supported suffixes"):
            ctx.outliner.outline("main.rs")

    def test_parse_error_names_file(self, ctx: AppContext, root: Path) -> None:
        write(root / "bad.py", "def (:\n")
        with pytest.raises(ParseError, match=r"bad\.py as python"):
            ctx.outliner.outline("bad.py")

    def test_size_limit(self, make_ctx: ContextFactory, root: Path) -> None:
        write(root / "big.py", "x = 1\n" * 500)
        with pytest.raises(FileTooLargeError):
            make_ctx(max_file_bytes=100).outliner.outline("big.py")

    def test_empty_file_outline(self, ctx: AppContext, root: Path) -> None:
        write(root / "empty.py", "")
        assert "[No symbols found]" in ctx.outliner.outline("empty.py").render()

    def test_symbol_truncation(self, ctx: AppContext, root: Path) -> None:
        write(root / "many.py", "".join(f"def f{n}(): pass\n" for n in range(2005)))
        result = ctx.outliner.outline("many.py")
        assert len(result.symbols) == 2000
        assert result.truncated
        assert result.next_line == 2001
        assert result.render().endswith(
            "[Truncated at 2000 symbols; the next one starts at line 2001. "
            "Continue with read_lines(start=2001).]"
        )

    def test_truncation_continues_inside_the_last_listed_symbol(
        self, ctx: AppContext, root: Path
    ) -> None:
        # The 2000th symbol is a class whose methods were not listed: continue at its first
        # method, not after the class (which here would also be past the end of the file).
        source = "".join(f"def f{n}(): pass\n" for n in range(1999))
        source += "class B:\n" + "".join(f"    def m{n}(self): pass\n" for n in range(10))
        write(root / "nested.py", source)
        result = ctx.outliner.outline("nested.py")
        assert result.symbols[-1].qualified_name == "B"
        assert result.next_line == 2001

    def test_truncation_inside_a_try_block_skips_no_symbol(
        self, ctx: AppContext, root: Path
    ) -> None:
        source = "".join(f"def f{n}(): pass\n" for n in range(1999))
        source += (
            "try:\n"  # line 2000
            "    def in_try(): pass\n"
            "except ValueError:\n"
            "    def in_except(): pass\n"  # line 2003
            "else:\n"
            "    def in_else(): pass\n"
        )
        write(root / "cut.py", source)
        result = ctx.outliner.outline("cut.py")
        assert result.symbols[-1].qualified_name == "in_try"
        assert result.next_line == 2003


class TestReadSymbol:
    def test_by_qualified_name(self, ctx: AppContext, root: Path) -> None:
        write(root / "shapes.py", PY)
        result = ctx.outliner.read_symbol("shapes.py", "Shape.area")
        assert (result.start_line, result.end_line) == (17, 20)
        assert result.note == "method Shape.area"
        assert result.content.splitlines()[0].endswith("def area(self) -> float:")

    def test_plain_name_when_unique(self, ctx: AppContext, root: Path) -> None:
        write(root / "shapes.py", PY)
        assert ctx.outliner.read_symbol("shapes.py", "fetch").start_line == 34

    def test_ambiguous_plain_name(self, ctx: AppContext, root: Path) -> None:
        write(
            root / "two.py",
            "class A:\n    def run(self): pass\nclass B:\n    def run(self): pass\n",
        )
        with pytest.raises(SymbolNotFoundError, match=r"ambiguous.*A\.run.*B\.run"):
            ctx.outliner.read_symbol("two.py", "run")

    def test_ambiguous_name_lists_a_bounded_number_of_candidates(
        self, ctx: AppContext, root: Path
    ) -> None:
        # 40 classes each define run(); the error must not grow with the file.
        source = "".join(f"class C{i}:\n    def run(self): pass\n" for i in range(40))
        write(root / "many.py", source)
        with pytest.raises(SymbolNotFoundError) as info:
            ctx.outliner.read_symbol("many.py", "run")
        message = str(info.value)
        assert message.count("(line ") == 15
        assert "(+25 more)" in message
        assert "Pass a qualified name" in message
        assert len(message) < 1500

    def test_ambiguous_json_key_is_bounded_too(self, ctx: AppContext, root: Path) -> None:
        # Objects have no item cap in the tree-sitter view, so this path must cap itself.
        body = ",".join(f'"p{i}": {{"version": {i}}}' for i in range(40))
        write(root / "lock.json", "{" + body + "}")
        with pytest.raises(SymbolNotFoundError) as info:
            ctx.outliner.read_symbol("lock.json", "version")
        assert str(info.value).count("(line ") == 15
        assert "(+25 more)" in str(info.value)

    def test_exact_qualified_name_wins_over_plain_matches(
        self, ctx: AppContext, root: Path
    ) -> None:
        # "area" is both a top-level function and the method Shape.area.
        write(root / "shapes.py", PY)
        assert ctx.outliner.read_symbol("shapes.py", "area").start_line == 38

    def test_multiple_definitions_are_spanned(self, ctx: AppContext, root: Path) -> None:
        write(root / "shapes.py", PY)
        result = ctx.outliner.read_symbol("shapes.py", "Shape.name")
        assert (result.start_line, result.end_line) == (22, 28)
        assert result.note == "method Shape.name (2 definitions)"

    def test_missing_symbol_lists_candidates(self, ctx: AppContext, root: Path) -> None:
        write(root / "shapes.py", PY)
        with pytest.raises(SymbolNotFoundError, match="Available: typed_only"):
            ctx.outliner.read_symbol("shapes.py", "Nope")

    def test_empty_name(self, ctx: AppContext, root: Path) -> None:
        write(root / "shapes.py", PY)
        with pytest.raises(InvalidArgumentError):
            ctx.outliner.read_symbol("shapes.py", " ")

    def test_markdown_section(self, ctx: AppContext, root: Path) -> None:
        write(root / "guide.md", MD)
        result = ctx.outliner.read_symbol("guide.md", "Guide > Usage")
        assert (result.start_line, result.end_line) == (18, 21)

    def test_long_symbol_is_truncated(self, make_ctx: ContextFactory, root: Path) -> None:
        write(root / "long.py", "def f():\n" + "    x = 1\n" * 50)
        result = make_ctx(max_read_lines=10).outliner.read_symbol("long.py", "f")
        assert (result.start_line, result.end_line) == (1, 10)
        assert result.truncated
        assert (result.next_start, result.next_end) == (11, 51)
        assert "start=11, end=51)" in result.render()
