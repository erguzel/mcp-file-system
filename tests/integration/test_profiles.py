"""Tool profiles, the tool allowlist, output-schema control and schema slimming."""

import json
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from mcp.client import Client
from mcp.types import CallToolResult, TextContent, Tool

from conftest import SettingsFactory, numbered, write
from mcp_file_system.context import AppContext
from mcp_file_system.core.etag import compute_etag
from mcp_file_system.server import build_instructions, create_server

pytestmark = pytest.mark.anyio

FULL_READ = {
    "file_info",
    "read_lines",
    "read_ranges",
    "read_between",
    "grep",
    "list_directory",
    "find_files",
    "outline",
    "read_symbol",
    "read_skeleton",
    "peek",
    "query",
    "log_summary",
}
LITE_READ = FULL_READ - {
    "read_between",
    "read_ranges",
    "read_skeleton",
    "peek",
    "query",
    "log_summary",
}
LITE_WRITE = {"replace_in_file", "edit_lines", "write_file"}
FULL_WRITE = LITE_WRITE | {"multi_replace", "apply_patch", "undo_last_edit"}

LITE_PARAMETERS = {
    "file_info": {"path"},
    "list_directory": {"path", "depth"},
    "find_files": {"glob", "path"},
    "grep": {"pattern", "path", "glob", "ignore_case", "context", "output_mode"},
    "outline": {"path"},
    "read_symbol": {"path", "name", "if_changed_since"},
    "read_lines": {"path", "start", "end", "if_changed_since"},
    "replace_in_file": {"path", "pattern", "replacement"},
    "edit_lines": {"path", "start", "end", "new_text", "expected_etag"},
    "write_file": {"path", "content"},
}


async def list_tools(make_settings: SettingsFactory, **overrides: Any) -> dict[str, Tool]:
    async with Client(create_server(make_settings(**overrides))) as client:
        return {tool.name: tool for tool in (await client.list_tools()).tools}


@pytest.fixture
async def lite(make_settings: SettingsFactory) -> AsyncIterator[Client]:
    settings = make_settings(profile="lite", allow_write=True)
    async with Client(create_server(settings)) as connected:
        yield connected


@pytest.fixture
async def full(make_settings: SettingsFactory) -> AsyncIterator[Client]:
    async with Client(create_server(make_settings(allow_write=True))) as connected:
        yield connected


def text_of(result: CallToolResult) -> str:
    block = result.content[0]
    assert isinstance(block, TextContent)
    return block.text


async def call(client: Client, tool: str, /, **arguments: Any) -> CallToolResult:
    return await client.call_tool(tool, arguments)


@pytest.mark.parametrize("profile", ["full", "lite"])
@pytest.mark.parametrize("allow_write", [False, True])
async def test_profiles_expose_expected_tools(
    make_settings: SettingsFactory, profile: str, allow_write: bool
) -> None:
    tools = await list_tools(make_settings, profile=profile, allow_write=allow_write)
    expected = FULL_READ if profile == "full" else LITE_READ
    write_tools = FULL_WRITE if profile == "full" else LITE_WRITE
    assert set(tools) == (expected | write_tools if allow_write else expected)


@pytest.mark.parametrize("profile", ["full", "lite"])
async def test_input_schemas_have_no_nullable_unions(
    make_settings: SettingsFactory, profile: str
) -> None:
    # `anyOf` (from `X | None` parameters) is what small models handle worst.
    tools = await list_tools(make_settings, profile=profile, allow_write=True)
    for tool in tools.values():
        assert "anyOf" not in json.dumps(tool.input_schema), tool.name


async def test_lite_exposes_only_essential_parameters(make_settings: SettingsFactory) -> None:
    tools = await list_tools(make_settings, profile="lite", allow_write=True)
    for name, tool in tools.items():
        assert set(tool.input_schema["properties"]) == LITE_PARAMETERS[name], name


async def test_collapsed_grep_mode_stays_out_of_lite(make_settings: SettingsFactory) -> None:
    def modes(tools: dict[str, Tool]) -> list[str]:
        return list(tools["grep"].input_schema["properties"]["output_mode"]["enum"])

    assert modes(await list_tools(make_settings, profile="lite")) == [
        "content",
        "files_with_matches",
        "count",
    ]
    assert "collapsed" in modes(await list_tools(make_settings))


async def full_only_names(make_settings: SettingsFactory) -> dict[str, re.Pattern[str]]:
    """Per lite tool (and ``""`` for text not tied to one), the full-only names to avoid.

    Flags full tool and parameter names that lite lacks and that contain ``_`` (setting
    names such as max_matches count too, since a lite client cannot pass them), the tool's
    own full-only parameters, and one-word full-only parameters written as ``name=``.
    One-word names alone (``regex``, ``around``) are ordinary words in descriptions.
    """
    full = await list_tools(make_settings, allow_write=True)
    lite = await list_tools(make_settings, profile="lite", allow_write=True)

    def params(tool: Tool) -> set[str]:
        return set(tool.input_schema["properties"])

    def pattern(names: set[str]) -> re.Pattern[str]:
        terms = [re.escape(n) + r"(?!\w)" if "_" in n else re.escape(n) + "=" for n in names]
        return re.compile(r"\b(?:" + "|".join(sorted(terms)) + ")")

    full_params = set().union(*(params(tool) for tool in full.values()))
    lite_names = set(lite).union(*(params(tool) for tool in lite.values()))
    missing = (set(full) | full_params) - lite_names
    common = {n for n in missing if "_" in n} | (full_params - lite_names)
    patterns = {
        name: pattern(common | (params(full[name]) - params(tool))) for name, tool in lite.items()
    }
    patterns[""] = pattern(common)
    return patterns


def descriptions(schema: Any) -> list[str]:
    """Every ``description`` string in a JSON schema, at any depth."""
    if isinstance(schema, dict):
        own = [schema["description"]] if isinstance(schema.get("description"), str) else []
        return own + [text for value in schema.values() for text in descriptions(value)]
    if isinstance(schema, list):
        return [text for value in schema for text in descriptions(value)]
    return []


async def test_lite_listing_names_only_lite_tools(make_settings: SettingsFactory) -> None:
    forbidden = await full_only_names(make_settings)
    settings = make_settings(profile="lite", allow_write=True, output_schema=True)
    async with Client(create_server(settings)) as client:
        tools = (await client.list_tools()).tools
    for tool in tools:
        texts = [tool.description or "", *descriptions(tool.input_schema)]
        texts += descriptions(tool.output_schema)
        for text in texts:
            assert not forbidden[tool.name].search(text), (tool.name, text)
    assert not forbidden[""].search(build_instructions(settings))


async def test_lite_hints_name_only_lite_tools(make_settings: SettingsFactory, root: Path) -> None:
    forbidden = await full_only_names(make_settings)
    write(root / "big.py", "def f():\n    pass\n" * 20)
    write(root / "twice.txt", "x\nx\n")
    write(root / "other.txt", "x\n")
    settings = make_settings(profile="lite", allow_write=True, max_file_bytes=100, max_matches=1)
    calls = [
        ("outline", {"path": "big.py"}, "limited to 100 bytes"),
        ("write_file", {"path": "new.txt", "content": "y" * 200}, "Nothing was written"),
        ("replace_in_file", {"path": "twice.txt", "pattern": "x", "replacement": "y"}, "2 time"),
        ("write_file", {"path": "twice.txt", "content": "y"}, "already exists"),
        ("grep", {"pattern": "x"}, "match limit"),
        ("grep", {"pattern": "x", "output_mode": "count"}, "match limit"),
    ]
    async with Client(create_server(settings)) as client:
        for tool, arguments, expected in calls:
            text = text_of(await client.call_tool(tool, arguments))
            assert expected in text, (tool, text)
            assert not forbidden[tool].search(text), (tool, text)


async def test_lite_listing_is_much_smaller(make_settings: SettingsFactory) -> None:
    def size(tools: dict[str, Tool]) -> int:
        return len(
            json.dumps([t.model_dump(mode="json", exclude_none=True) for t in tools.values()])
        )

    full_tools = await list_tools(make_settings, allow_write=True)
    lite_tools = await list_tools(make_settings, profile="lite", allow_write=True)
    assert size(lite_tools) < size(full_tools) / 2


@pytest.mark.parametrize(
    ("overrides", "has_schema"),
    [
        ({}, True),
        ({"output_schema": False}, False),
        ({"profile": "lite"}, False),
        ({"profile": "lite", "output_schema": True}, True),
    ],
)
async def test_output_schema_control(
    make_settings: SettingsFactory, root: Path, overrides: dict[str, Any], has_schema: bool
) -> None:
    write(root / "f.txt", "hello\n")
    async with Client(create_server(make_settings(**overrides))) as client:
        tools = (await client.list_tools()).tools
        assert all((tool.output_schema is not None) == has_schema for tool in tools)
        result = await call(client, "read_lines", path="f.txt")
        # Structured data is still returned; only the schema advertisement changes.
        assert result.structured_content is not None
        assert result.structured_content["content"] == "1\thello"


async def test_allowlist_limits_tools(make_settings: SettingsFactory) -> None:
    tools = await list_tools(make_settings, tools=("grep", "read_lines"))
    assert set(tools) == {"grep", "read_lines"}


def test_allowlist_rejects_unknown_tools(make_settings: SettingsFactory) -> None:
    with pytest.raises(ValueError, match=r"Unknown tool\(s\) for profile 'lite'.*read_between"):
        create_server(make_settings(profile="lite", tools=("grep", "read_between")))


def test_allowlist_explains_missing_write_flag(make_settings: SettingsFactory) -> None:
    with pytest.raises(ValueError, match="need --allow-write"):
        create_server(make_settings(tools=("edit_lines",)))


def test_instructions_follow_the_profile(make_settings: SettingsFactory) -> None:
    lite_text = build_instructions(make_settings(profile="lite", allow_write=True))
    full_text = build_instructions(make_settings(allow_write=True))
    assert "read_between" not in lite_text
    assert "dry_run" not in lite_text
    assert "read_ranges" not in lite_text
    assert "around" not in lite_text
    assert "read_between" in full_text
    assert "dry_run" in full_text
    assert "read_ranges" in full_text
    assert "undo_last_edit" not in lite_text
    assert "undo_last_edit" in full_text


class TestLiteBehavior:
    async def test_grep_falls_back_to_literal_search(self, lite: Client, root: Path) -> None:
        write(root / "app.py", "value = compute(1)\n")
        result = await call(lite, "grep", pattern="compute(")
        assert not result.is_error
        assert "1:value = compute(1)" in text_of(result)
        assert "searched it as literal text" in text_of(result)

    async def test_grep_fallback_keeps_truncation_note(self, lite: Client, root: Path) -> None:
        write(root / "many.txt", "f(\n" * 150)
        result = await call(lite, "grep", pattern="f(")
        assert result.structured_content is not None
        note = result.structured_content["note"]
        assert note.startswith("pattern is not a valid regular expression")
        assert "match limit (100) reached" in note

    async def test_grep_context_applies_to_both_sides(self, lite: Client, root: Path) -> None:
        write(root / "f.txt", "a\nb\nhit\nc\nd\n")
        result = await call(lite, "grep", pattern="hit", context=1)
        assert result.structured_content is not None
        hit = result.structured_content["hits"][0]
        assert (hit["before"], hit["after"]) == (["b"], ["c"])

    async def test_replace_requires_a_unique_match(self, lite: Client, root: Path) -> None:
        write(root / "f.txt", "x\nx\n")
        result = await call(lite, "replace_in_file", path="f.txt", pattern="x", replacement="y")
        assert result.is_error
        assert "2 time(s)" in text_of(result)

    async def test_write_file_never_overwrites(self, lite: Client, root: Path) -> None:
        path = write(root / "f.txt", "keep\n")
        result = await call(lite, "write_file", path="f.txt", content="lost\n")
        assert result.is_error
        assert path.read_text() == "keep\n"
        created = await call(lite, "write_file", path="new/f.txt", content="x\n")
        assert not created.is_error

    async def test_edit_lines_still_requires_etag(self, lite: Client, root: Path) -> None:
        path = write(root / "f.txt", "a\n")
        stale = await call(
            lite, "edit_lines", path="f.txt", start=1, end=1, new_text="b", expected_etag="0-0"
        )
        assert stale.is_error
        fresh = await call(
            lite,
            "edit_lines",
            path="f.txt",
            start=1,
            end=1,
            new_text="b",
            expected_etag=compute_etag(path.stat()),
        )
        assert not fresh.is_error
        assert path.read_text() == "b\n"

    async def test_file_info_reports_token_estimate(self, lite: Client, root: Path) -> None:
        write(root / "f.txt", "x" * 4001)
        result = await call(lite, "file_info", path="f.txt")
        assert "~1.0k tokens" in text_of(result)

    async def test_every_lite_read_tool_works(self, lite: Client, root: Path) -> None:
        write(root / "src" / "mod.py", "class A:\n    def run(self):\n        return 1\n")
        calls: list[tuple[str, dict[str, Any], str]] = [
            ("list_directory", {"depth": 2}, "mod.py"),
            ("find_files", {"glob": "*.py"}, "src/mod.py"),
            ("outline", {"path": "src/mod.py"}, "def run(self)"),
            ("read_symbol", {"path": "src/mod.py", "name": "A.run"}, "return 1"),
            ("read_lines", {"path": "src/mod.py", "start": -1}, "return 1"),
        ]
        for tool, arguments, expected in calls:
            result = await call(lite, tool, **arguments)
            assert not result.is_error, (tool, text_of(result))
            assert expected in text_of(result), tool


class TestFullEditing:
    async def test_undo_last_edit(self, full: Client, root: Path) -> None:
        path = write(root / "a.txt", "x = 1\n")
        await call(full, "replace_in_file", path="a.txt", pattern="1", replacement="2")
        undone = await call(full, "undo_last_edit", path="a.txt")
        assert not undone.is_error, text_of(undone)
        assert path.read_text() == "x = 1\n"
        await call(full, "write_file", path="new.txt", content="fresh\n")
        renamed = await call(full, "undo_last_edit", path="new.txt")
        assert "renamed to new.txt.deleted-" in text_of(renamed)
        assert not (root / "new.txt").exists()


class TestSentinelDefaults:
    """Unset parameters (empty string / 0) must behave like the former `None` defaults."""

    async def test_read_lines_default_end_reads_to_the_window(
        self, full: Client, root: Path
    ) -> None:
        write(root / "f.txt", numbered(5))
        default = await call(full, "read_lines", path="f.txt")
        explicit = await call(full, "read_lines", path="f.txt", end=-1)
        assert default.structured_content is not None
        assert explicit.structured_content is not None
        # The second read only differs by the session note ("already returned").
        assert {**default.structured_content, "note": None} == {
            **explicit.structured_content,
            "note": None,
        }
        assert default.structured_content["end_line"] == 5

    async def test_read_lines_around(self, full: Client, root: Path) -> None:
        write(root / "f.txt", numbered(30))
        unset = await call(full, "read_lines", path="f.txt", start=3, end=4, around=0)
        centered = await call(full, "read_lines", path="f.txt", around=20, radius=2)
        both = await call(full, "read_lines", path="f.txt", start=3, around=20)
        assert unset.structured_content is not None
        assert centered.structured_content is not None
        assert (unset.structured_content["start_line"], unset.structured_content["end_line"]) == (
            3,
            4,
        )
        assert "18\tline 18" in text_of(centered)
        assert "22\tline 22" in text_of(centered)
        assert "line 23" not in text_of(centered)
        assert both.is_error
        assert "either around" in text_of(both)

    async def test_read_ranges(self, full: Client, root: Path) -> None:
        write(root / "a.txt", numbered(10))
        write(root / "b.txt", numbered(3, "row"))
        ranges = [{"path": "a.txt", "start": 4, "end": 5}, {"path": "b.txt", "start": -1}]
        result = await call(full, "read_ranges", ranges=ranges)
        assert not result.is_error, text_of(result)
        assert "4\tline 4" in text_of(result)
        assert "3\trow 3" in text_of(result)
        too_many = await call(full, "read_ranges", ranges=[{"path": "a.txt"}] * 21)
        assert too_many.is_error

    async def test_read_between_unset_end_pattern_and_max_lines(
        self, full: Client, root: Path
    ) -> None:
        write(root / "f.txt", "a\nSTART\nb\nc\n")
        result = await call(full, "read_between", path="f.txt", start_pattern="START")
        assert result.structured_content is not None
        assert (result.structured_content["start_line"], result.structured_content["end_line"]) == (
            2,
            4,
        )

    async def test_grep_empty_glob_searches_everything(self, full: Client, root: Path) -> None:
        write(root / "a.py", "needle\n")
        write(root / "b.md", "needle\n")
        result = await call(full, "grep", pattern="needle", output_mode="count")
        assert result.structured_content is not None
        assert len(result.structured_content["files"]) == 2

    async def test_find_files_zero_means_unlimited(self, full: Client, root: Path) -> None:
        write(root / "a" / "b" / "c" / "deep.txt", "x")
        result = await call(full, "find_files", glob="*.txt", max_depth=0, max_results=0)
        assert result.structured_content is not None
        assert result.structured_content["paths"] == ["a/b/c/deep.txt"]

    async def test_replace_zero_expected_count_means_any(self, full: Client, root: Path) -> None:
        path = write(root / "f.txt", "x\nx\nx\n")
        result = await call(
            full, "replace_in_file", path="f.txt", pattern="x", replacement="y", expected_count=0
        )
        assert not result.is_error
        assert path.read_text() == "y\ny\ny\n"

    async def test_empty_expected_etag_skips_the_check(self, full: Client, root: Path) -> None:
        path = write(root / "f.txt", "old\n")
        result = await call(
            full,
            "replace_in_file",
            path="f.txt",
            pattern="old",
            replacement="new",
            expected_etag="",
        )
        assert not result.is_error
        assert path.read_text() == "new\n"

    async def test_write_file_overwrite_still_needs_etag(self, full: Client, root: Path) -> None:
        write(root / "f.txt", "old\n")
        result = await call(full, "write_file", path="f.txt", content="new\n", overwrite=True)
        assert result.is_error
        assert "expected_etag is required" in text_of(result)


def test_register_all_reports_exposed_tools(make_settings: SettingsFactory) -> None:
    from mcp.server import MCPServer

    from mcp_file_system.tools import register_all

    settings = make_settings(profile="lite", tools=("grep", "outline"))
    names = register_all(MCPServer("t"), AppContext.from_settings(settings))
    assert names == ["grep", "outline"]
