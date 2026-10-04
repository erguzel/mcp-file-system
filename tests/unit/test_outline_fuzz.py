"""Property-based tests for the tree-sitter outline providers.

Tree-sitter is a C extension: a bug there crashes the whole server process instead of
raising an exception (tree-sitter 0.26.0 did exactly that). These tests push arbitrary
and generated structured input through every provider, so such a regression fails CI
instead of reaching users.
"""

import json
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from mcp_file_system.core.models import Symbol
from mcp_file_system.core.outline.base import OutlineProvider
from mcp_file_system.core.outline.treesitter import (
    JsonOutlineProvider,
    ShellOutlineProvider,
    TomlOutlineProvider,
    TreeSitterMarkdownOutlineProvider,
    YamlOutlineProvider,
)
from mcp_file_system.core.textio import split_lines

PROVIDERS = [
    TreeSitterMarkdownOutlineProvider(),
    JsonOutlineProvider(),
    TomlOutlineProvider(),
    YamlOutlineProvider(),
    ShellOutlineProvider(),
]
FUZZ = settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow])

# Text biased towards the syntax of the formats under test.
SYNTAX = st.sampled_from(
    [
        *"{}[]:,=\"'#-*>`~\n\r\t .",
        "---",
        "[[",
        "]]",
        "```",
        "# ",
        "key",
        "1",
        "true",
        "{{",
        "}}",
        "f() {",
        "fi",
    ]
)
SYNTAX_TEXT = st.lists(st.one_of(SYNTAX, st.text(max_size=5)), max_size=60).map("".join)

# Only valid JSON: json.dumps would write inf/nan as the non-standard Infinity/NaN.
JSON_VALUES = st.recursive(
    st.none()
    | st.booleans()
    | st.integers()
    | st.floats(allow_nan=False, allow_infinity=False)
    | st.text(max_size=8),
    lambda children: (
        st.lists(children, max_size=5) | st.dictionaries(st.text(max_size=6), children, max_size=5)
    ),
    max_leaves=40,
)


def assert_well_formed(symbols: list[Symbol], text: str) -> None:
    total = max(1, len(split_lines(text)))
    for symbol in symbols:
        assert 1 <= symbol.start_line <= symbol.end_line <= total, symbol
        assert symbol.depth >= 0
        assert symbol.qualified_name


@pytest.mark.parametrize("provider", PROVIDERS, ids=lambda p: p.language)
@FUZZ
@given(text=st.one_of(st.text(max_size=300), SYNTAX_TEXT))
def test_arbitrary_text_never_crashes(provider: OutlineProvider, text: str) -> None:
    assert_well_formed(provider.outline(text), text)


@FUZZ
@given(value=JSON_VALUES, indent=st.sampled_from([None, 0, 2, 4]))
def test_generated_json(value: Any, indent: int | None) -> None:
    text = json.dumps(value, indent=indent)
    symbols = JsonOutlineProvider().outline(text)
    assert_well_formed(symbols, text)
    if isinstance(value, dict):
        top_level = {s.name for s in symbols if s.depth == 0}
        assert top_level == set(value)


@FUZZ
@given(value=JSON_VALUES, indent=st.sampled_from([None, 2]))
def test_generated_json_is_also_valid_yaml(value: Any, indent: int | None) -> None:
    # JSON is a subset of YAML 1.2, which gives the YAML provider structured input.
    text = json.dumps(value, indent=indent)
    assert_well_formed(YamlOutlineProvider().outline(text), text)


TOML_KEYS = st.from_regex(r"[a-z][a-z0-9_-]{0,7}", fullmatch=True)
TOML_SCALARS = st.one_of(
    st.integers(-1000, 1000).map(str),
    st.booleans().map(lambda b: str(b).lower()),
    st.text(alphabet="abc xyz", max_size=8).map(json.dumps),
)


@FUZZ
@given(
    tables=st.dictionaries(
        TOML_KEYS, st.dictionaries(TOML_KEYS, TOML_SCALARS, max_size=4), max_size=5
    ),
    top=st.dictionaries(TOML_KEYS, TOML_SCALARS, max_size=3),
)
def test_generated_toml(tables: dict[str, dict[str, str]], top: dict[str, str]) -> None:
    lines = [f"{key} = {value}" for key, value in top.items()]
    for name, pairs in tables.items():
        lines.append(f"[{name}]")
        lines.extend(f"{key} = {value}" for key, value in pairs.items())
    text = "\n".join(lines) + "\n"
    symbols = TomlOutlineProvider().outline(text)
    assert_well_formed(symbols, text)
    names = {s.qualified_name for s in symbols}
    assert set(tables) - set(top) <= names
