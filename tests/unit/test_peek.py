"""peek: shape summaries of data files, including huge and malformed inputs."""

import json
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from conftest import ContextFactory, write
from mcp_file_system.core.peek import structured, tabular, text
from mcp_file_system.errors import (
    AccessDeniedError,
    BinaryFileError,
    FileTooLargeError,
    InvalidArgumentError,
    ParseError,
)

JSON_SYNTAX = ["{", "}", "[", "]", ":", ",", " ", "\n", "\r", "// c\n", "/*c*/"]
JSON_TOKENS = ['"a"', '"\t"', '"\\u"', '"\\q"', '"\x01"', "1", "1.", "-", "1e", "null", "true"]
JSON_PIECES = [*JSON_SYNTAX, *JSON_TOKENS]


def peek(ctx_factory: ContextFactory, path: Path, **settings: int) -> str:
    return ctx_factory(**settings).peeker.peek(str(path)).render()


class TestCsv:
    def test_columns_types_and_samples(self, root: Path, make_ctx: ContextFactory) -> None:
        write(
            root / "d.csv",
            "id,name,score,when,ok,note\n"
            "1,Ann,1.5,2024-01-02,true,\n"
            "2,Bob,2,2024-01-03,false,x\n"
            "3,Cy,3.25,2024-01-04T10:00:00Z,true,\n"
            "4,Di,4,2024-01-05,true,y\n",
        )
        out = peek(make_ctx, root / "d.csv")
        assert "data rows: 4" in out
        assert "id (int)" in out
        assert "name (str)" in out
        assert "score (float)" in out
        assert "when (date)" in out
        assert "ok (bool)" in out
        assert "note (str, 2 empty)" in out
        assert "sample 3: 3 | Cy" in out
        assert "sample 4" not in out

    def test_tsv_and_semicolon(self, root: Path, make_ctx: ContextFactory) -> None:
        write(root / "d.tsv", "a\tb\n1\t2\n")
        write(root / "e.csv", "a;b\n1;2\n3;4\n")
        assert "'\\t'" in peek(make_ctx, root / "d.tsv")
        assert "';'" in peek(make_ctx, root / "e.csv")

    def test_mixed_and_empty(self, root: Path, make_ctx: ContextFactory) -> None:
        write(root / "m.csv", "a\n1\nx\n")
        write(root / "e.csv", "")
        assert "a (mixed)" in peek(make_ctx, root / "m.csv")
        assert "(empty file)" in peek(make_ctx, root / "e.csv")

    def test_quoted_newlines_stay_one_row(self, root: Path, make_ctx: ContextFactory) -> None:
        write(root / "q.csv", 'a,b\n1,"x\ny"\n2,z\n')
        assert "data rows: 2" in peek(make_ctx, root / "q.csv")

    def test_huge_file_is_scanned_only_in_part(
        self, root: Path, make_ctx: ContextFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(tabular, "MAX_SCAN_ROWS", 10)
        write(root / "big.csv", "a\n" + "1\n" * 100)
        assert "more than 10 (scanned the first 10)" in peek(make_ctx, root / "big.csv")

    def test_many_columns_are_capped(self, root: Path, make_ctx: ContextFactory) -> None:
        header = ",".join(f"c{i}" for i in range(100))
        write(root / "w.csv", header + "\n" + ",".join("1" for _ in range(100)) + "\n")
        out = peek(make_ctx, root / "w.csv")
        assert "columns: 100" in out
        assert "...+60 more" in out

    def test_oversized_field_is_a_parse_error(self, root: Path, make_ctx: ContextFactory) -> None:
        write(root / "f.csv", 'a\n"' + "x" * 200_000 + '"\n')
        with pytest.raises(ParseError, match="Cannot parse"):
            peek(make_ctx, root / "f.csv")


class TestJson:
    def test_shape_without_values(self, root: Path, make_ctx: ContextFactory) -> None:
        data = {
            "name": "SECRET-VALUE",
            "items": [{"id": i, "name": f"n{i}"} for i in range(1532)],
            "meta": {"ok": True, "tags": ["a", "b"], "none": None},
        }
        write(root / "d.json", json.dumps(data))
        out = peek(make_ctx, root / "d.json")
        assert "items: array[1532] of {id: int, name: str}" in out
        assert "meta: {ok: bool, tags: array[2] of str, none: null}" in out
        assert "name: str" in out
        assert "SECRET-VALUE" not in out

    def test_optional_and_mixed_keys(self, root: Path, make_ctx: ContextFactory) -> None:
        write(root / "d.json", '{"x": [{"a": 1}, {"a": "s", "b": 2.5}]}')
        assert "x: array[2] of {a: int | str, b?: float}" in peek(make_ctx, root / "d.json")

    def test_jsonc_with_comments(self, root: Path, make_ctx: ContextFactory) -> None:
        write(
            root / "settings.jsonc",
            '{\n  // editor\n  "tabSize": 2, /* spaces */\n  "rulers": [80, /* soft */ 100]\n}\n',
        )
        out = peek(make_ctx, root / "settings.jsonc")
        assert "tabSize: int" in out
        assert "rulers: array[2] of int" in out
        assert "editor" not in out

    def test_json_with_comments_like_tsconfig(self, root: Path, make_ctx: ContextFactory) -> None:
        write(
            root / "tsconfig.json",
            '{\n  // compiler\n  "compilerOptions": {\n    "strict": true\n  }\n}\n',
        )
        assert "compilerOptions: {strict: bool}" in peek(make_ctx, root / "tsconfig.json")

    def test_comment_parse_keeps_scalar_types(self, root: Path, make_ctx: ContextFactory) -> None:
        write(root / "t.jsonc", '// types\n{"a": 1, "b": 1.5, "c": "x", "d": null, "e": false}')
        out = peek(make_ctx, root / "t.jsonc")
        for expected in ("a: int", "b: float", "c: str", "d: null", "e: bool"):
            assert expected in out

    @pytest.mark.parametrize("body", ["{", "// only a comment\n", "[1,] // trailing", "{'a': 1}"])
    def test_malformed_jsonc(self, root: Path, make_ctx: ContextFactory, body: str) -> None:
        write(root / "bad.jsonc", body)
        with pytest.raises(ParseError, match="invalid JSON"):
            peek(make_ctx, root / "bad.jsonc")

    def test_top_level_array_and_scalar(self, root: Path, make_ctx: ContextFactory) -> None:
        write(root / "a.json", "[1, 2, 3]")
        write(root / "s.json", "42")
        assert "array[3] of int" in peek(make_ctx, root / "a.json")
        assert "int" in peek(make_ctx, root / "s.json")

    def test_many_keys_are_capped(self) -> None:
        rows = structured.key_tree({f"k{i}": i for i in range(100)})
        assert len(rows) == structured.MAX_KEYS + 1
        assert rows[-1] == "... +70 more keys"

    @pytest.mark.parametrize("body", ["{", "", "{'a': 1}", "[1,]"])
    def test_malformed(self, root: Path, make_ctx: ContextFactory, body: str) -> None:
        write(root / "bad.json", body)
        with pytest.raises(ParseError, match="invalid JSON"):
            peek(make_ctx, root / "bad.json")

    @pytest.mark.parametrize(
        ("body", "count"),
        [
            ('{"a": 1}\n{"b": 2}\n', 2),
            ('{"a": 1}\r\n{"b": 2}\r\n{"c": 3}\r\n', 3),
            ('{"a": 1}{"b": 2}', 2),
            ('// comment\n{"a": 1}\n{"b": 2}\n', 2),
            ("1\n2\n", 2),
        ],
        ids=["newline-delimited", "crlf", "side-by-side", "comment-first", "scalars"],
    )
    def test_several_top_level_values_are_rejected(
        self, root: Path, make_ctx: ContextFactory, body: str, count: int
    ) -> None:
        write(root / "several.json", body)
        with pytest.raises(ParseError, match="Cannot parse") as error:
            peek(make_ctx, root / "several.json")
        assert str(count) in str(error.value)

    def test_file_with_a_bad_token_in_a_later_value_is_rejected(
        self, root: Path, make_ctx: ContextFactory
    ) -> None:
        write(root / "several.json", '{"a": 1}\n{"b": "SECRET\tVALUE"}\n')
        with pytest.raises(ParseError) as error:
            peek(make_ctx, root / "several.json")
        assert "SECRET" not in str(error.value)

    def test_manifest_with_several_values_is_rejected(
        self, root: Path, make_ctx: ContextFactory
    ) -> None:
        write(root / "package.json", '{"name": "first"}\n{"name": "second"}\n')
        with pytest.raises(ParseError, match="Cannot parse"):
            peek(make_ctx, root / "package.json")

    @pytest.mark.parametrize("body", ['{"a": 1} // trailing\n', '/* before */ {"a": 1}\n'])
    def test_comments_do_not_count_as_values(
        self, root: Path, make_ctx: ContextFactory, body: str
    ) -> None:
        write(root / "one.jsonc", body)
        assert "a: int" in peek(make_ctx, root / "one.jsonc")

    @pytest.mark.parametrize(
        "token",
        [
            '"SECRET\tVALUE"',  # raw control character
            '"\\u"',  # bare \u
            '"\\u12"',  # short \u
            "1.",  # the grammar's number is wider than JSON's
            "9" * 5000,  # longer than Python converts to an int
        ],
        ids=["control-char", "bare-u", "short-u", "trailing-dot", "long-int"],
    )
    @pytest.mark.parametrize("wrap", ['{{"a": {}}}', '// comment\n{{"a": {}}}'])
    def test_token_the_grammar_accepts_but_json_rejects(
        self, root: Path, make_ctx: ContextFactory, token: str, wrap: str
    ) -> None:
        write(root / "bad.jsonc", wrap.format(token))
        with pytest.raises(ParseError, match="Cannot parse") as error:
            peek(make_ctx, root / "bad.jsonc")
        assert "SECRET" not in str(error.value)

    def test_long_integer_is_not_called_invalid(self, root: Path, make_ctx: ContextFactory) -> None:
        # Valid JSON that Python refuses to convert: the file is not malformed.
        write(root / "long.json", '{"a": ' + "9" * 5000 + "}")
        with pytest.raises(ParseError) as error:
            peek(make_ctx, root / "long.json")
        assert "invalid" not in str(error.value).lower()

    @pytest.mark.parametrize(
        ("content", "line"),
        [
            ('// c\n{\n  "a": "x\ty"\n}', 3),
            ('// c\r\n{\r\n  "a": "x\ty"\r\n}', 3),
            ('// c\r{\r  "a": "x\ty"\r}', 3),
        ],
    )
    def test_bad_token_error_names_its_line(self, content: str, line: int) -> None:
        with pytest.raises(ParseError, match=rf"\bline {line}\b"):
            structured.parse_json(content)

    @given(
        st.lists(
            st.sampled_from(JSON_PIECES),
            max_size=12,
        ).map("".join)
    )
    def test_every_failure_is_a_parse_error(self, content: str) -> None:
        try:
            structured.parse_json(content)
        except ParseError:
            return  # refusing is allowed; any other exception fails the test

    def test_deep_nesting_with_comment_is_rejected(
        self, root: Path, make_ctx: ContextFactory
    ) -> None:
        # Too deep for json.loads and for the tree walk of the comment-tolerant parser.
        write(root / "deep.jsonc", "// deep\n" + "[" * 10_000 + "]" * 10_000)
        with pytest.raises(ParseError, match="invalid JSON"):
            peek(make_ctx, root / "deep.jsonc")

    def test_deep_nesting_is_bounded(self, root: Path, make_ctx: ContextFactory) -> None:
        write(root / "deep.json", "[" * 100_000 + "]" * 100_000)
        try:
            out = peek(make_ctx, root / "deep.json")
        except ParseError:
            return  # the parser refused: also acceptable, and bounded
        assert len(out) < 2000

    def test_file_over_size_limit(self, root: Path, make_ctx: ContextFactory) -> None:
        write(root / "big.json", json.dumps(["x" * 50] * 100))
        with pytest.raises(FileTooLargeError):
            peek(make_ctx, root / "big.json", max_file_bytes=1000)

    def test_output_is_bounded(self, root: Path, make_ctx: ContextFactory) -> None:
        write(root / "d.json", json.dumps({f"key{i}": {"a": [1]} for i in range(30)}))
        result = make_ctx(max_output_chars=1000).peeker.peek(str(root / "d.json"))
        assert len(result.summary) <= 1100


class TestToml:
    def test_generic_toml_key_tree(self, root: Path, make_ctx: ContextFactory) -> None:
        write(root / "c.toml", 'title = "x"\n[server]\nport = 80\nhosts = ["a"]\n')
        out = peek(make_ctx, root / "c.toml")
        assert "title: str" in out
        assert "server: {port: int, hosts: array[1] of str}" in out

    def test_malformed(self, root: Path, make_ctx: ContextFactory) -> None:
        write(root / "c.toml", "a = \n")
        with pytest.raises(ParseError, match="invalid TOML"):
            peek(make_ctx, root / "c.toml")


class TestManifests:
    def test_pyproject(self, root: Path, make_ctx: ContextFactory) -> None:
        write(
            root / "pyproject.toml",
            '[project]\nname = "demo"\nversion = "1.2.3"\nrequires-python = ">=3.12"\n'
            'dependencies = ["mcp>=1", "pydantic"]\n'
            '[project.optional-dependencies]\ncode = ["tree-sitter"]\n'
            "[tool.ruff]\nline-length = 100\n",
        )
        out = peek(make_ctx, root / "pyproject.toml")
        assert "name: demo" in out
        assert "version: 1.2.3" in out
        assert "dependencies (2: mcp>=1, pydantic)" in out
        assert "extra code (1: tree-sitter)" in out
        assert "[tool.*]: ruff" in out

    def test_package_json(self, root: Path, make_ctx: ContextFactory) -> None:
        write(
            root / "package.json",
            '{"name": "w", "version": "0.1.0", "dependencies": {"left-pad": "^1.0.0"},'
            ' "devDependencies": {"jest": "*"}, "scripts": {"test": "jest", "build": "tsc"}}',
        )
        out = peek(make_ctx, root / "package.json")
        assert "name: w" in out
        assert "dependencies (1: left-pad ^1.0.0)" in out
        assert "devDependencies (1: jest)" in out
        assert "scripts (2: test, build)" in out

    def test_cargo(self, root: Path, make_ctx: ContextFactory) -> None:
        write(
            root / "Cargo.toml",
            '[package]\nname = "c"\nversion = "0.3.0"\n[dependencies]\nserde = "1"\n'
            '[dev-dependencies]\ntokio = { version = "1" }\n',
        )
        out = peek(make_ctx, root / "Cargo.toml")
        assert "name: c" in out
        assert "dependencies (1: serde 1)" in out
        assert "dev-dependencies (1:" in out

    def test_manifest_with_odd_types_does_not_crash(
        self, root: Path, make_ctx: ContextFactory
    ) -> None:
        write(root / "package.json", '{"name": 5, "dependencies": "oops", "scripts": []}')
        write(root / "pyproject.toml", "project = 3\n")
        assert "name: 5" in peek(make_ctx, root / "package.json")
        assert "name: -" in peek(make_ctx, root / "pyproject.toml")

    def test_many_dependencies_are_capped(self, root: Path, make_ctx: ContextFactory) -> None:
        deps = ", ".join(f'"dep{i}"' for i in range(100))
        write(root / "pyproject.toml", f"[project]\ndependencies = [{deps}]\n")
        assert "...+60" in peek(make_ctx, root / "pyproject.toml")


class TestLogs:
    LOG = (
        "2024-05-01 10:00:00,123 INFO start\n"
        "2024-05-01 10:00:01 WARNING slow\n"
        "continuation line without timestamp\n"
        "2024-05-01T10:05:00Z ERROR boom\n"
        "2024-05-01T10:06:00Z error again\n"
    )

    def test_span_and_levels(self, root: Path, make_ctx: ContextFactory) -> None:
        write(root / "app.log", self.LOG)
        out = peek(make_ctx, root / "app.log")
        assert "time span: 2024-05-01 10:00:00,123 .. 2024-05-01T10:06:00Z" in out
        assert "ERROR: 2, WARN: 1, INFO: 1" in out
        assert "levels (5 lines)" in out

    def test_no_timestamps_or_levels(self, root: Path, make_ctx: ContextFactory) -> None:
        write(root / "x.log", "hello\nworld\n")
        out = peek(make_ctx, root / "x.log")
        assert "no timestamps found" in out
        assert "none recognized" in out

    def test_scan_is_capped_but_last_timestamp_comes_from_the_tail(
        self, root: Path, make_ctx: ContextFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(text, "MAX_SCAN_LINES", 5)
        lines = [f"2024-01-01 00:00:{i % 60:02d} INFO row {i}" for i in range(500)]
        lines[-1] = "2025-01-01 00:00:00 ERROR end"
        write(root / "big.log", "\n".join(lines) + "\n")
        out = peek(make_ctx, root / "big.log")
        assert "levels (first 5 lines): INFO: 5" in out
        assert ".. 2025-01-01 00:00:00" in out

    def test_very_long_lines_are_bounded(self, root: Path, make_ctx: ContextFactory) -> None:
        write(root / "long.log", "x" * 5_000_000 + " ERROR\n")
        assert "none recognized" in peek(make_ctx, root / "long.log", max_file_bytes=10_000_000)


class TestMarkdown:
    def test_front_matter_and_headings(self, root: Path, make_ctx: ContextFactory) -> None:
        write(
            root / "d.md",
            "---\ntitle: T\ntags: [a]\n---\n# Title\n## A\ntext\n```\n# not a heading\n```\n"
            "## B\n### C\n",
        )
        out = peek(make_ctx, root / "d.md")
        assert "front matter keys: title, tags" in out
        assert "headings: 4 (h1: 1, h2: 2, h3: 1)" in out
        assert "title: Title" in out

    def test_plain(self, root: Path, make_ctx: ContextFactory) -> None:
        write(root / "d.md", "just text\n")
        out = peek(make_ctx, root / "d.md")
        assert "front matter keys: none" in out
        assert "headings: 0" in out


class TestErrorsAndSecurity:
    def test_unsupported_type(self, root: Path, make_ctx: ContextFactory) -> None:
        write(root / "a.py", "x = 1\n")
        with pytest.raises(InvalidArgumentError, match="Supported: CSV/TSV"):
            peek(make_ctx, root / "a.py")

    def test_binary_file(self, root: Path, make_ctx: ContextFactory) -> None:
        write(root / "b.csv", b"a\x00b\n1\x002\n")
        with pytest.raises(BinaryFileError):
            peek(make_ctx, root / "b.csv")

    def test_denied_path(self, root: Path, make_ctx: ContextFactory) -> None:
        write(root / ".env.json", "{}")
        with pytest.raises(AccessDeniedError):
            peek(make_ctx, root / ".env.json")

    def test_outside_root(self, root: Path, outside: Path, make_ctx: ContextFactory) -> None:
        write(outside / "o.json", "{}")
        with pytest.raises(Exception, match="outside"):
            peek(make_ctx, outside / "o.json")

    def test_result_metadata(self, root: Path, make_ctx: ContextFactory) -> None:
        write(root / "d.json", '{"a": 1}\n')
        result = make_ctx().peeker.peek("d.json")
        assert result.kind == "json"
        assert result.total_lines == 1
        assert result.size_bytes == 9
        assert "etag" in result.render()
