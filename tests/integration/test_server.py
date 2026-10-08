"""End-to-end tool behavior through an in-process MCP client."""

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from mcp.client import Client
from mcp.types import CallToolResult, TextContent

from conftest import SettingsFactory, numbered, write
from mcp_file_system.server import create_server

pytestmark = pytest.mark.anyio

READ_TOOLS = {
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
WRITE_TOOLS = {
    "replace_in_file",
    "multi_replace",
    "edit_lines",
    "apply_patch",
    "write_file",
    "undo_last_edit",
}


@pytest.fixture
async def client(make_settings: SettingsFactory) -> AsyncIterator[Client]:
    async with Client(create_server(make_settings())) as connected:
        yield connected


@pytest.fixture
async def writer(make_settings: SettingsFactory) -> AsyncIterator[Client]:
    async with Client(create_server(make_settings(allow_write=True))) as connected:
        yield connected


def text_of(result: CallToolResult) -> str:
    block = result.content[0]
    assert isinstance(block, TextContent)
    return block.text


async def call(client: Client, tool: str, /, **arguments: Any) -> CallToolResult:
    return await client.call_tool(tool, arguments)


async def test_read_only_server_hides_write_tools(client: Client) -> None:
    tools = {tool.name: tool for tool in (await client.list_tools()).tools}
    assert set(tools) == READ_TOOLS
    for tool in tools.values():
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is True
        assert tool.output_schema is not None
        assert tool.description


async def test_write_enabled_server_lists_write_tools(writer: Client) -> None:
    tools = {tool.name: tool for tool in (await writer.list_tools()).tools}
    assert set(tools) == READ_TOOLS | WRITE_TOOLS
    edit = tools["edit_lines"]
    assert edit.annotations is not None
    assert edit.annotations.destructive_hint is True
    assert "expected_etag" in edit.input_schema["required"]


async def test_peek_returns_shape_without_values(client: Client, root: Path) -> None:
    write(root / "d.json", '{"secret": "hunter2", "n": [1, 2]}')
    out = text_of(await call(client, "peek", path="d.json"))
    assert "n: array[2] of int" in out
    assert "hunter2" not in out


async def test_parameter_descriptions_reach_the_schema(client: Client) -> None:
    tools = {tool.name: tool for tool in (await client.list_tools()).tools}
    start = tools["read_lines"].input_schema["properties"]["start"]
    assert "Negative counts from the end" in start["description"]


async def test_instructions_describe_roots(make_settings: SettingsFactory, root: Path) -> None:
    server = create_server(make_settings())
    assert server.instructions is not None
    assert str(root) in server.instructions
    assert "read-only" in server.instructions


async def test_read_lines_returns_text_and_structured_content(client: Client, root: Path) -> None:
    write(root / "f.txt", numbered(5))
    result = await call(client, "read_lines", path="f.txt", start=2, end=3)
    assert not result.is_error
    assert text_of(result).splitlines()[1:] == ["2\tline 2", "3\tline 3"]
    assert result.structured_content is not None
    assert result.structured_content["start_line"] == 2
    assert result.structured_content["total_lines"] == 5


async def test_domain_errors_become_tool_errors(client: Client, root: Path) -> None:
    write(root / ".env", "SECRET=1\n")
    for arguments, message in [
        ({"path": "../x"}, "outside the allowed roots"),
        ({"path": ".env"}, "denied by policy"),
        ({"path": "missing.txt"}, "No such file"),
    ]:
        result = await call(client, "read_lines", **arguments)
        assert result.is_error
        assert message in text_of(result)
        assert "SECRET" not in text_of(result)


async def test_argument_validation_errors(client: Client) -> None:
    result = await call(client, "grep", pattern="x", context_before=99)
    assert result.is_error


async def test_os_errors_are_sanitized(
    client: Client, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write(root / "f.txt", "x\n")

    def boom(*_args: object, **_kwargs: object) -> None:
        raise PermissionError(13, "Permission denied", "/very/secret/location")

    monkeypatch.setattr("mcp_file_system.core.reader.FileReader.read_lines", boom)
    result = await call(client, "read_lines", path="f.txt")
    assert result.is_error
    assert text_of(result).endswith("Filesystem error: Permission denied")
    assert "/very/secret" not in text_of(result)


async def test_every_read_tool_works(client: Client, root: Path) -> None:
    write(root / "src" / "mod.py", "class A:\n    def run(self):\n        return 1\n")
    write(root / "README.md", "# Title\n## Part\ntext\n")
    write(root / "ci.yml", "jobs:\n  test:\n    steps:\n      - uses: actions/checkout@v4\n")
    write(root / "app.log", "retry 1 of 3\nretry 2 of 3\n")
    calls: list[tuple[str, dict[str, Any], str]] = [
        ("file_info", {"path": "src/mod.py"}, "lines: 3"),
        ("read_between", {"path": "README.md", "start_pattern": "## Part"}, "## Part"),
        ("grep", {"pattern": "return", "path": "src"}, "3:        return 1"),
        ("list_directory", {"depth": 2}, "mod.py"),
        ("find_files", {"glob": "*.py"}, "src/mod.py"),
        ("outline", {"path": "src/mod.py"}, "def run(self)"),
        ("read_symbol", {"path": "src/mod.py", "name": "A.run"}, "return 1"),
        ("query", {"path": "ci.yml", "key_path": "jobs.test.steps[0]"}, 'uses: "actions/'),
        ("log_summary", {"path": "app.log"}, "[x2] retry <N> of <N>"),
        ("grep", {"pattern": "retry", "output_mode": "collapsed"}, "[x2] retry <N> of <N>"),
    ]
    for name, arguments, expected in calls:
        result = await call(client, name, **arguments)
        assert not result.is_error, (name, text_of(result))
        assert expected in text_of(result), name


async def test_partial_read_then_guarded_edit_flow(writer: Client, root: Path) -> None:
    path = write(root / "app.py", "def main():\n    return 1\n")
    read = await call(writer, "read_lines", path="app.py", start=2, end=2)
    assert read.structured_content is not None
    etag = read.structured_content["etag"]

    preview = await call(
        writer,
        "edit_lines",
        path="app.py",
        start=2,
        end=2,
        new_text="    return 2",
        expected_etag=etag,
        dry_run=True,
    )
    assert "+    return 2" in text_of(preview)
    assert path.read_text() == "def main():\n    return 1\n"

    applied = await call(
        writer,
        "edit_lines",
        path="app.py",
        start=2,
        end=2,
        new_text="    return 2",
        expected_etag=etag,
    )
    assert not applied.is_error
    assert path.read_text() == "def main():\n    return 2\n"

    stale = await call(
        writer,
        "edit_lines",
        path="app.py",
        start=2,
        end=2,
        new_text="    return 3",
        expected_etag=etag,
    )
    assert stale.is_error
    assert "changed since it was read" in text_of(stale)

    replaced = await call(writer, "replace_in_file", path="app.py", pattern="2", replacement="3")
    assert not replaced.is_error
    created = await call(writer, "write_file", path="pkg/new.py", content="x = 1\n")
    assert not created.is_error
    assert (root / "pkg" / "new.py").read_text() == "x = 1\n"
