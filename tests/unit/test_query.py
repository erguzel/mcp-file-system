"""query: key-path lookups in JSON, YAML and TOML files, with depth and output limits."""

import json
from pathlib import Path

import pytest

from conftest import ContextFactory, write
from mcp_file_system.context import AppContext
from mcp_file_system.core.models import QueryResult
from mcp_file_system.core.query.keypath import format_key_path, parse_key_path
from mcp_file_system.core.query.nodes import block_scalar_value
from mcp_file_system.errors import (
    AccessDeniedError,
    BinaryFileError,
    FileTooLargeError,
    InvalidArgumentError,
    ParseError,
    PathOutsideRootError,
    SymbolNotFoundError,
)


def query(ctx: AppContext, path: Path, key_path: str = "", depth: int = 2) -> QueryResult:
    return ctx.data_query.query(str(path), key_path, depth=depth)


class TestKeyPath:
    @pytest.mark.parametrize(
        ("text", "segments"),
        [
            ("", []),
            ("a", ["a"]),
            ("tool.ruff.lint", ["tool", "ruff", "lint"]),
            ("steps[0].uses", ["steps", 0, "uses"]),
            ("[1].kind", [1, "kind"]),
            ("a[-1][2]", ["a", -1, 2]),
            ('"a.b".c', ["a.b", "c"]),
            ('x."[0]"', ["x", "[0]"]),
            ('"tab\\there"', ["tab\there"]),
            ("paths./users.get", ["paths", "/users", "get"]),
            ("@types/node", ["@types/node"]),
            ("with space", ["with space"]),
        ],
    )
    def test_parses(self, text: str, segments: list[str | int]) -> None:
        assert parse_key_path(text) == segments

    @pytest.mark.parametrize(
        "text",
        [
            ".a",
            "a.",
            "a..b",
            "a[",
            "a[x]",
            "a[1",
            "a[]",
            '"open',
            '"a"b',
            "[0]x",
            "a[" + "9" * 50 + "]",
        ],
    )
    def test_rejects_malformed_paths_with_a_hint(self, text: str) -> None:
        with pytest.raises(InvalidArgumentError, match=r"Invalid key path.*JSON quotes"):
            parse_key_path(text)

    def test_canonical_form_quotes_only_where_needed(self) -> None:
        assert format_key_path(["paths", "/users", 0, "a.b", "ok_1"]) == (
            'paths."/users"[0]."a.b".ok_1'
        )
        assert parse_key_path(format_key_path(["a.b", "[x]", 3])) == ["a.b", "[x]", 3]


class TestJson:
    def test_depth_collapses_deeper_containers(self, root: Path, ctx: AppContext) -> None:
        write(root / "p.json", '{"name": "app", "deps": {"react": "^18", "vue": {"v": 3}}}')
        shallow = query(ctx, root / "p.json", depth=1)
        assert shallow.content == 'name: "app"\ndeps: {2 keys}'
        assert shallow.first_collapsed == "deps"
        assert "e.g. 'deps'" in shallow.render()
        deep = query(ctx, root / "p.json", depth=3)
        assert deep.content == 'name: "app"\ndeps:\n  react: "^18"\n  vue:\n    v: 3'
        assert deep.first_collapsed is None
        assert "Collapsed" not in deep.render()

    def test_subtree_of_minified_file(self, root: Path, ctx: AppContext) -> None:
        data = {"packages": {f"pkg{i}": {"version": f"1.{i}.0"} for i in range(500)}}
        write(root / "lock.json", json.dumps(data, separators=(",", ":")))
        result = query(ctx, root / "lock.json", "packages.pkg42")
        assert result.content == 'version: "1.42.0"'
        assert result.kind == "object"
        assert (result.start_line, result.end_line, result.total_lines) == (1, 1, 1)
        assert "pkg42" in result.render().splitlines()[0]

    def test_comments_and_line_span(self, root: Path, ctx: AppContext) -> None:
        write(
            root / "tsconfig.json",
            '{\n  // compiler\n  "compilerOptions": {\n    "strict": true\n  }\n}\n',
        )
        result = query(ctx, root / "tsconfig.json", "compilerOptions")
        assert result.content == "strict: true"
        assert (result.start_line, result.end_line) == (3, 5)
        assert result.note is None
        assert "lines 3-5 of 6" in result.render()

    def test_array_indexes(self, root: Path, ctx: AppContext) -> None:
        write(root / "a.json", '{"items": [10, {"id": 2}, [true, null]]}')
        assert query(ctx, root / "a.json", "items[0]").content == "10"
        assert query(ctx, root / "a.json", "items[1].id").content == "2"
        last = query(ctx, root / "a.json", "items[-1]")
        assert last.key_path == "items[2]"  # canonical, non-negative
        assert last.content == "[0]: true\n[1]: null"
        assert query(ctx, root / "a.json", "items[-1][1]").content == "null"

    def test_keys_containing_dots_and_special_characters(self, root: Path, ctx: AppContext) -> None:
        write(root / "k.json", '{"a.b": {"c": 1}, "a": {"b": 2}, "@types/node": "20", "x[0]": 3}')
        assert query(ctx, root / "k.json", '"a.b".c').content == "1"
        assert query(ctx, root / "k.json", "a.b").content == "2"
        assert query(ctx, root / "k.json", "@types/node").content == '"20"'
        assert query(ctx, root / "k.json", '"x[0]"').content == "3"
        listing = query(ctx, root / "k.json").content
        assert '"a.b":' in listing
        assert '"@types/node": "20"' in listing

    def test_missing_key_lists_the_keys_that_exist(self, root: Path, ctx: AppContext) -> None:
        write(root / "m.json", '{"deps": {"react": "1", "a.b": "2"}}')
        with pytest.raises(SymbolNotFoundError) as info:
            query(ctx, root / "m.json", "deps.vue")
        message = str(info.value)
        assert "No key vue in deps of m.json" in message
        assert 'Keys there: react, "a.b"' in message
        with pytest.raises(SymbolNotFoundError, match="in the document root"):
            query(ctx, root / "m.json", "nope")

    def test_missing_key_listing_is_capped(self, root: Path, ctx: AppContext) -> None:
        write(root / "big.json", json.dumps({f"k{i}": i for i in range(40)}))
        with pytest.raises(SymbolNotFoundError, match=r"k14, \.\.\. \(\+25 more\)"):
            query(ctx, root / "big.json", "zzz")

    def test_index_errors(self, root: Path, ctx: AppContext) -> None:
        write(root / "i.json", '{"list": [1, 2, 3], "obj": {"a": 1}, "empty": [], "s": "x"}')
        with pytest.raises(
            SymbolNotFoundError, match=r"has 3 item\(s\) \(valid: 0\.\.2 or -3\.\.-1\)"
        ):
            query(ctx, root / "i.json", "list[3]")
        with pytest.raises(SymbolNotFoundError, match=r"-4\] is out of range"):
            query(ctx, root / "i.json", "list[-4]")
        with pytest.raises(SymbolNotFoundError, match=r"has 0 item\(s\)\.$"):
            query(ctx, root / "i.json", "empty[0]")
        with pytest.raises(InvalidArgumentError, match=r"obj in i.json is an object.*Use \.key"):
            query(ctx, root / "i.json", "obj[0]")
        with pytest.raises(InvalidArgumentError, match=r"is an array of 3.*Use an index"):
            query(ctx, root / "i.json", "list.first")
        with pytest.raises(InvalidArgumentError, match=r'is a scalar value \("x"\)'):
            query(ctx, root / "i.json", "s.deeper")

    def test_duplicate_keys_last_one_wins(self, root: Path, ctx: AppContext) -> None:
        write(root / "d.json", '{"a": 1, "a": 2}')
        assert query(ctx, root / "d.json", "a").content == "2"

    def test_scalar_and_empty_documents(self, root: Path, ctx: AppContext) -> None:
        write(root / "s.json", '"just text"\n')
        write(root / "e.json", "{}")
        write(root / "none.json", "// nothing here\n")
        scalar = query(ctx, root / "s.json")
        assert (scalar.kind, scalar.content) == ("value", '"just text"')
        assert query(ctx, root / "e.json").content == "{}"
        with pytest.raises(ParseError, match=r"Cannot parse none.json: no JSON value.*read_lines"):
            query(ctx, root / "none.json")

    def test_syntax_errors_are_reported_not_hidden(self, root: Path, ctx: AppContext) -> None:
        write(root / "bad.json", '{\n  "ok": {"a": 1},\n  "broken": {"b": ,},\n  "c": 3\n}\n')
        clean = query(ctx, root / "bad.json", "ok")
        assert clean.content == "a: 1"
        assert clean.note == "The file has syntax errors elsewhere; this value parsed cleanly."
        whole = query(ctx, root / "bad.json")
        assert whole.note is not None
        assert "contains syntax errors" in whole.note
        with pytest.raises(SymbolNotFoundError, match="syntax errors, which may hide keys"):
            query(ctx, root / "bad.json", "missing")


class TestYaml:
    def test_multi_document_streams_are_indexed(self, root: Path, ctx: AppContext) -> None:
        write(root / "m.yaml", "kind: A\n---\nkind: B\nspec: {n: 1}\n")
        result = query(ctx, root / "m.yaml", "[1]")
        assert result.content == 'kind: "B"\nspec:\n  n: 1'
        assert (result.start_line, result.end_line) == (3, 4)
        assert query(ctx, root / "m.yaml", "[-1].spec.n").content == "1"
        assert query(ctx, root / "m.yaml").kind == "array"

    def test_single_document_is_the_root(self, root: Path, ctx: AppContext) -> None:
        write(root / "s.yml", "---\n# comment\nname: x\n")
        assert query(ctx, root / "s.yml").content == 'name: "x"'

    def test_scalar_types_and_quoting(self, root: Path, ctx: AppContext) -> None:
        write(
            root / "t.yaml",
            "int: 3\nfloat: 1.5\nbool: true\nnull1: ~\nnull2: null\nempty:\nword: yes\n"
            "dq: \"a\\tb\"\nsq: 'it''s'\nmulti: one\n  two\n",
        )
        assert query(ctx, root / "t.yaml").content == (
            "int: 3\nfloat: 1.5\nbool: true\nnull1: null\nnull2: null\nempty: null\n"
            'word: "yes"\ndq: "a\\tb"\nsq: "it\'s"\nmulti: "one two"'
        )

    def test_block_scalars(self, root: Path, ctx: AppContext) -> None:
        write(
            root / "b.yaml",
            "run: |\n  echo a\n    indented\n\n  echo b\nfold: >-\n  one\n  two\n\n  three\n",
        )
        assert query(ctx, root / "b.yaml", "run").content == '"echo a\\n  indented\\n\\necho b\\n"'
        assert query(ctx, root / "b.yaml", "fold").content == '"one two\\nthree"'

    def test_block_scalar_value_header_comment(self) -> None:
        assert block_scalar_value("| # note\n  a\n  b") == "a\nb\n"

    def test_anchors_are_shown_not_resolved(self, root: Path, ctx: AppContext) -> None:
        write(root / "a.yaml", "base: &b {x: 1}\nuse: *b\nlist:\n  - &item 1\n  - *item\n")
        assert query(ctx, root / "a.yaml", "base").content == "x: 1"
        assert query(ctx, root / "a.yaml", "use").content == "*b"
        assert query(ctx, root / "a.yaml", "list").content == "[0]: 1\n[1]: *item"

    def test_sequences_flow_collections_and_empty_items(self, root: Path, ctx: AppContext) -> None:
        write(
            root / "ci.yml",
            "jobs:\n  test:\n    steps:\n      - uses: actions/checkout@v4\n"
            "      -\n      - run: [a, {b: 2}]\n",
        )
        steps = query(ctx, root / "ci.yml", "jobs.test.steps", depth=3)
        assert steps.content == (
            '[0]:\n  uses: "actions/checkout@v4"\n[1]: null\n[2]:\n  run:\n'
            '    [0]: "a"\n    [1]: {1 key}'
        )
        assert query(ctx, root / "ci.yml", "jobs.test.steps[2].run[1].b").content == "2"

    def test_comments_do_not_shift_indexes(self, root: Path, ctx: AppContext) -> None:
        write(root / "c.yaml", "list:\n  # first\n  - a\n  # second\n  - b\nmap: {}\n")
        assert query(ctx, root / "c.yaml", "list[1]").content == '"b"'
        assert query(ctx, root / "c.yaml").content == 'list:\n  [0]: "a"\n  [1]: "b"\nmap: {}'

    def test_quoted_keys_with_dots(self, root: Path, ctx: AppContext) -> None:
        write(root / "q.yaml", '"app.kubernetes.io/name": web\nmeta: {"a.b": 1}\n')
        assert query(ctx, root / "q.yaml", '"app.kubernetes.io/name"').content == '"web"'
        assert query(ctx, root / "q.yaml", 'meta."a.b"').content == "1"

    def test_helm_templates(self, root: Path, ctx: AppContext) -> None:
        write(
            root / "deploy.yaml",
            "spec:\n  replicas: {{ .Values.replicas }}\n{{- if .Values.extra }}\n"
            "  extra: true\n{{- end }}\n  name: web\n",
        )
        result = query(ctx, root / "deploy.yaml", "spec")
        assert result.content == 'replicas: "{{ .Values.replicas }}"\nextra: true\nname: "web"'
        assert result.note is None

    def test_empty_file_is_null(self, root: Path, ctx: AppContext) -> None:
        write(root / "e.yaml", "# only a comment\n")
        result = query(ctx, root / "e.yaml")
        assert (result.kind, result.content) == ("value", "null")


class TestToml:
    def test_tables_are_merged_and_have_no_lines(self, root: Path, ctx: AppContext) -> None:
        write(
            root / "pyproject.toml",
            '[tool.ruff]\nline-length = 100\n\n[tool.ruff.lint]\nselect = ["E", "F"]\n'
            '\n[project]\nname = "x"\n',
        )
        result = query(ctx, root / "pyproject.toml", "tool.ruff", depth=3)
        assert result.content == 'line-length: 100\nlint:\n  select:\n    [0]: "E"\n    [1]: "F"'
        assert (result.start_line, result.end_line) == (0, 0)
        assert "lines" not in result.render().splitlines()[0]

    def test_arrays_of_tables_dates_and_quoted_keys(self, root: Path, ctx: AppContext) -> None:
        write(
            root / "c.toml",
            '[[bin]]\nname = "a"\n[[bin]]\nname = "b"\nwhen = 2024-01-02T03:04:05Z\n'
            'day = 2024-01-02\nok = false\npi = 3.5\ntext = """\nline1\nline2"""\n'
            '[site."example.com"]\nport = 80\n',
        )
        assert query(ctx, root / "c.toml", "bin[1]").content == (
            'name: "b"\nwhen: 2024-01-02T03:04:05+00:00\nday: 2024-01-02\nok: false\n'
            'pi: 3.5\ntext: "line1\\nline2"'
        )
        assert query(ctx, root / "c.toml", 'site."example.com".port').content == "80"

    def test_invalid_toml(self, root: Path, ctx: AppContext) -> None:
        write(root / "bad.toml", "a = \n")
        with pytest.raises(ParseError, match=r"Cannot parse bad.toml: invalid TOML.*grep"):
            query(ctx, root / "bad.toml")


class TestLimits:
    def test_output_budget_truncates_with_a_hint(
        self, root: Path, make_ctx: ContextFactory
    ) -> None:
        write(root / "big.json", json.dumps({f"key{i}": "v" * 40 for i in range(200)}))
        result = query(make_ctx(max_output_chars=1000), root / "big.json")
        assert result.truncated
        assert len(result.content) <= 1000
        assert result.content.startswith('key0: "vvvv')
        assert "[Truncated: output limit. Query a deeper key path or lower depth.]" in (
            result.render()
        )

    def test_long_scalars_are_clipped(self, root: Path, make_ctx: ContextFactory) -> None:
        write(root / "long.json", json.dumps({"s": "x" * 500}))
        content = query(make_ctx(max_line_chars=100), root / "long.json").content
        assert content.startswith('s: "xxx')
        assert content.endswith("[+405 chars]")

    @pytest.mark.parametrize("depth", [0, 11])
    def test_depth_is_bounded(self, root: Path, ctx: AppContext, depth: int) -> None:
        write(root / "d.json", "{}")
        with pytest.raises(InvalidArgumentError, match="depth must be between 1 and 10"):
            query(ctx, root / "d.json", depth=depth)

    def test_file_size_limit(self, root: Path, make_ctx: ContextFactory) -> None:
        write(root / "huge.json", "[" + ",".join(["1"] * 1000) + "]")
        with pytest.raises(FileTooLargeError):
            query(make_ctx(max_file_bytes=100), root / "huge.json")

    def test_unsupported_and_binary_files(self, root: Path, ctx: AppContext) -> None:
        write(root / "notes.txt", "a: 1\n")
        write(root / "blob.json", b"\x00\x01\x02{}")
        with pytest.raises(InvalidArgumentError, match=r"JSON, YAML and TOML.*outline"):
            query(ctx, root / "notes.txt")
        with pytest.raises(BinaryFileError):
            query(ctx, root / "blob.json")

    def test_paths_go_through_the_guard(self, root: Path, outside: Path, ctx: AppContext) -> None:
        write(root / ".env.json", '{"token": "secret"}')
        write(outside / "o.json", '{"a": 1}')
        with pytest.raises(AccessDeniedError):
            query(ctx, root / ".env.json")
        with pytest.raises(PathOutsideRootError):
            query(ctx, outside / "o.json")
