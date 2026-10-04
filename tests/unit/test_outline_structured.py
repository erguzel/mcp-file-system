import json
import logging
from pathlib import Path

import pytest

from conftest import write
from mcp_file_system.context import AppContext
from mcp_file_system.core.models import Symbol
from mcp_file_system.core.outline import registry as registry_module
from mcp_file_system.core.outline.registry import default_registry
from mcp_file_system.core.outline.treesitter import (
    JsonOutlineProvider,
    TomlOutlineProvider,
    YamlOutlineProvider,
)
from mcp_file_system.core.outline.treesitter._base import MAX_DEPTH, MAX_ITEMS_PER_ARRAY


def table(symbols: list[Symbol]) -> list[tuple[str, int, int, int]]:
    return [(s.qualified_name, s.start_line, s.end_line, s.depth) for s in symbols]


def by_name(symbols: list[Symbol]) -> dict[str, Symbol]:
    return {s.qualified_name: s for s in symbols}


JSON_DOC = """\
{
  "name": "demo",
  "version": 3,
  "private": true,
  "license": null,
  "scripts": {
    "test": "pytest",
    "lint": "ruff check ."
  },
  "files": ["a", "b"],
  "contributors": [
    {"name": "Ada"},
    "plain string",
    {"name": "Linus", "roles": ["dev"]}
  ],
  "a.b": {"c d": 1}
}
"""


class TestJson:
    def test_keys_spans_and_depth(self) -> None:
        symbols = JsonOutlineProvider().outline(JSON_DOC)
        assert table(symbols) == [
            ("name", 2, 2, 0),
            ("version", 3, 3, 0),
            ("private", 4, 4, 0),
            ("license", 5, 5, 0),
            ("scripts", 6, 9, 0),
            ("scripts.test", 7, 7, 1),
            ("scripts.lint", 8, 8, 1),
            ("files", 10, 10, 0),
            ("contributors", 11, 15, 0),
            ("contributors[0]", 12, 12, 1),
            ("contributors[0].name", 12, 12, 2),
            ("contributors[2]", 14, 14, 1),
            ("contributors[2].name", 14, 14, 2),
            ("contributors[2].roles", 14, 14, 2),
            ('"a.b"', 16, 16, 0),
            ('"a.b"."c d"', 16, 16, 1),
        ]

    def test_signatures_and_kinds(self) -> None:
        symbols = by_name(JsonOutlineProvider().outline(JSON_DOC))
        assert symbols["name"].signature == 'name: "demo"'
        assert symbols["scripts"].signature == "scripts: {2 keys}"
        assert symbols["files"].signature == "files: [2 items]"
        assert symbols["contributors[2].roles"].signature == "roles: [1 item]"
        assert symbols['"a.b"'].signature == '"a.b": {1 key}'
        kinds = {name: s.kind for name, s in symbols.items()}
        assert (kinds["version"], kinds["private"], kinds["license"]) == (
            "number",
            "boolean",
            "null",
        )
        assert (kinds["scripts"], kinds["files"]) == ("object", "array")

    def test_comments_and_root_array(self) -> None:
        text = '[\n  // first\n  {"id": 1},\n  {"id": 2}\n]\n'
        assert table(JsonOutlineProvider().outline(text)) == [
            ("[0]", 3, 3, 0),
            ("[0].id", 3, 3, 1),
            ("[1]", 4, 4, 0),
            ("[1].id", 4, 4, 1),
        ]

    def test_escaped_keys_and_long_values(self) -> None:
        text = json.dumps({'q"uote': "x" * 200})
        symbol = JsonOutlineProvider().outline(text)[0]
        assert symbol.name == 'q"uote'
        assert symbol.qualified_name == '"q\\"uote"'
        assert symbol.signature is not None
        assert symbol.signature.endswith("…")
        assert len(symbol.signature) < 100

    def test_array_elements_are_capped(self) -> None:
        text = json.dumps({"items": [{"i": n} for n in range(MAX_ITEMS_PER_ARRAY + 50)]})
        names = [s.qualified_name for s in JsonOutlineProvider().outline(text)]
        assert f"items[{MAX_ITEMS_PER_ARRAY - 1}]" in names
        assert f"items[{MAX_ITEMS_PER_ARRAY}]" not in names

    def test_depth_is_capped(self) -> None:
        nested: dict[str, object] = {}
        current = nested
        for _ in range(MAX_DEPTH + 5):
            child: dict[str, object] = {}
            current["k"] = child
            current = child
        symbols = JsonOutlineProvider().outline(json.dumps(nested))
        assert max(s.depth for s in symbols) == MAX_DEPTH - 1

    @pytest.mark.parametrize("text", ['{"a": ', "{{{{", '{"a": [1, 2', "\x00\x01"])
    def test_malformed_input_never_crashes(self, text: str) -> None:
        JsonOutlineProvider().outline(text)


TOML_DOC = """\
title = "demo"
owner.name = "Ada"

[tool.ruff]
line-length = 100
point = { x = 1, y = 2 }

[tool.ruff.lint]
select = ["E", "F"]

[tool.mypy]
strict = true

[[bin]]
name = "one"

[[bin]]
name = "two"
servers = [{ host = "a" }, { host = "b" }]

["quoted key"]
'literal' = 1
"""


class TestToml:
    def test_tables_pairs_and_array_tables(self) -> None:
        assert table(TomlOutlineProvider().outline(TOML_DOC)) == [
            ("title", 1, 1, 0),
            ("owner.name", 2, 2, 0),
            ("tool.ruff", 4, 10, 1),
            ("tool.ruff.line-length", 5, 5, 2),
            ("tool.ruff.point", 6, 6, 2),
            ("tool.ruff.point.x", 6, 6, 3),
            ("tool.ruff.point.y", 6, 6, 3),
            ("tool.ruff.lint", 8, 10, 2),
            ("tool.ruff.lint.select", 9, 9, 3),
            ("tool.mypy", 11, 13, 1),
            ("tool.mypy.strict", 12, 12, 2),
            ("bin[0]", 14, 16, 0),
            ("bin[0].name", 15, 15, 1),
            ("bin[1]", 17, 20, 0),
            ("bin[1].name", 18, 18, 1),
            ("bin[1].servers", 19, 19, 1),
            ("bin[1].servers[0]", 19, 19, 2),
            ("bin[1].servers[0].host", 19, 19, 3),
            ("bin[1].servers[1]", 19, 19, 2),
            ("bin[1].servers[1].host", 19, 19, 3),
            ('"quoted key"', 21, 22, 0),
            ('"quoted key".literal', 22, 22, 1),
        ]

    def test_signatures(self) -> None:
        symbols = by_name(TomlOutlineProvider().outline(TOML_DOC))
        assert symbols["tool.ruff"].signature == "[tool.ruff]"
        assert symbols["bin[1]"].signature == "[[bin]]"
        assert symbols["bin[1]"].kind == "array-table"
        assert symbols["owner.name"].signature == 'owner.name = "Ada"'
        assert symbols["tool.ruff.point"].signature == "point = {2 keys}"
        assert symbols["tool.ruff.lint.select"].signature == 'select = ["E", "F"]'.replace(
            '["E", "F"]', "[2 items]"
        )

    def test_parent_span_stops_at_unrelated_table(self) -> None:
        text = "[a]\nx = 1\n[b]\ny = 2\n[a.c]\nz = 3\n"
        spans = {
            s.qualified_name: (s.start_line, s.end_line)
            for s in TomlOutlineProvider().outline(text)
        }
        assert spans["a"] == (1, 2)  # [a.c] is not contiguous with [a]

    def test_nested_arrays(self) -> None:
        names = [
            s.qualified_name for s in TomlOutlineProvider().outline("m = [[1, 2], [{ k = 1 }]]\n")
        ]
        assert names == ["m", "m[0]", "m[1]", "m[1][0]", "m[1][0].k"]

    @pytest.mark.parametrize("text", ["[unclosed", "a = ", "= 1", "[[x]\ny"])
    def test_malformed_input_never_crashes(self, text: str) -> None:
        TomlOutlineProvider().outline(text)


YAML_DOC = """\
name: ci
on:
  push:
    branches: [main]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout
      - run: echo hi
        env:
          A: "1"
      - plain item
"quoted key": 1
'single ''q''': 2
empty:
flow: {a: 1, b: [x, y]}
anchored: &base
  x: 1
"""


class TestYaml:
    def test_mappings_and_sequences(self) -> None:
        assert table(YamlOutlineProvider().outline(YAML_DOC)) == [
            ("name", 1, 1, 0),
            ("on", 2, 4, 0),
            ("on.push", 3, 4, 1),
            ("on.push.branches", 4, 4, 2),
            ("jobs", 5, 13, 0),
            ("jobs.test", 6, 13, 1),
            ('jobs.test."runs-on"'.replace('"runs-on"', "runs-on"), 7, 7, 2),
            ("jobs.test.steps", 8, 13, 2),
            ("jobs.test.steps[0]", 9, 9, 3),
            ("jobs.test.steps[0].uses", 9, 9, 4),
            ("jobs.test.steps[1]", 10, 12, 3),
            ("jobs.test.steps[1].run", 10, 10, 4),
            ("jobs.test.steps[1].env", 11, 12, 4),
            ("jobs.test.steps[1].env.A", 12, 12, 5),
            ('"quoted key"', 14, 14, 0),
            ("\"single 'q'\"", 15, 15, 0),
            ("empty", 16, 16, 0),
            ("flow", 17, 17, 0),
            ("flow.a", 17, 17, 1),
            ("flow.b", 17, 17, 1),
            ("anchored", 18, 19, 0),
            ("anchored.x", 19, 19, 1),
        ]

    def test_signatures(self) -> None:
        symbols = by_name(YamlOutlineProvider().outline(YAML_DOC))
        assert symbols["name"].signature == "name: ci"
        assert symbols["on.push.branches"].signature == "branches: [1 item]"
        assert symbols["jobs.test.steps"].signature == "steps: [3 items]"
        assert symbols["jobs.test.steps[1]"].signature == "[1]: {2 keys}"
        assert symbols["empty"].signature == "empty:"
        assert symbols["anchored"].kind == "object"

    def test_multiple_documents_are_indexed(self) -> None:
        text = "kind: A\n---\nkind: B\nspec:\n  x: 1\n"
        assert table(YamlOutlineProvider().outline(text)) == [
            ("[0]", 1, 1, 0),
            ("[0].kind", 1, 1, 1),
            ("[1]", 2, 5, 0),
            ("[1].kind", 3, 3, 1),
            ("[1].spec", 4, 5, 1),
            ("[1].spec.x", 5, 5, 2),
        ]

    @pytest.mark.parametrize("text", ["a: [", "- - -", ": :", "key: 'open"])
    def test_malformed_input_never_crashes(self, text: str) -> None:
        YamlOutlineProvider().outline(text)


class TestThroughOutliner:
    def test_read_symbol_returns_one_toml_table(self, ctx: AppContext, root: Path) -> None:
        write(root / "pyproject.toml", TOML_DOC)
        result = ctx.outliner.read_symbol("pyproject.toml", "tool.ruff.lint")
        assert (result.start_line, result.end_line) == (8, 10)
        assert result.content.splitlines()[0].endswith("[tool.ruff.lint]")
        assert result.note == "table tool.ruff.lint"

    def test_read_symbol_for_yaml_sequence_element(self, ctx: AppContext, root: Path) -> None:
        write(root / "ci.yml", YAML_DOC)
        result = ctx.outliner.read_symbol("ci.yml", "jobs.test.steps[1]")
        assert (result.start_line, result.end_line) == (10, 12)

    def test_plain_name_lookup_works_when_unique(self, ctx: AppContext, root: Path) -> None:
        write(root / "package.json", JSON_DOC)
        assert ctx.outliner.read_symbol("package.json", "scripts").start_line == 6

    def test_outline_render(self, ctx: AppContext, root: Path) -> None:
        write(root / "package.json", JSON_DOC)
        rendered = ctx.outliner.outline("package.json").render()
        assert rendered.startswith("==> package.json (json, 17 lines) <==")
        assert "scripts: {2 keys}" in rendered


class TestRegistryFallback:
    def test_default_registry_covers_structured_formats(self) -> None:
        suffixes = default_registry().supported_suffixes
        assert {".json", ".jsonc", ".toml", ".yaml", ".yml", ".md", ".py"} <= set(suffixes)

    def test_missing_tree_sitter_falls_back_to_regex_markdown(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        def unavailable() -> list[object]:
            raise ImportError("no tree_sitter")

        monkeypatch.setattr(registry_module, "_tree_sitter_factories", unavailable)
        with caplog.at_level(logging.WARNING, logger="mcp_file_system.outline"):
            registry = default_registry()
        markdown = registry.for_path(Path("README.md"))
        assert markdown is not None
        assert type(markdown).__name__ == "RegexMarkdownOutlineProvider"
        assert registry.for_path(Path("a.json")) is None
        assert "tree-sitter outlines unavailable" in caplog.text

    def test_one_broken_grammar_does_not_disable_the_others(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        def broken() -> JsonOutlineProvider:
            raise OSError("cannot load grammar")

        original = registry_module._tree_sitter_factories

        def factories() -> list[object]:
            return [broken, *original()[2:]]  # drop Markdown and JSON, add a broken one

        monkeypatch.setattr(registry_module, "_tree_sitter_factories", factories)
        with caplog.at_level(logging.WARNING, logger="mcp_file_system.outline"):
            registry = default_registry()
        assert registry.for_path(Path("a.toml")) is not None
        assert registry.for_path(Path("a.json")) is None
        assert "broken unavailable" in caplog.text
