"""Lone surrogates must never reach the transport, whatever brought them into a result.

JSON-RPC cannot carry one: the SDK's stdio writer fails outside any tool, the client gets no
reply, and the server exits at its next message. A surrogate comes from a JSON or YAML
``"\\ud800"`` escape, from a file name that is not valid UTF-8, or from a path inside a
list argument, which the SDK decodes from a string.
"""

import asyncio
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from mcp.client import Client
from mcp.client.stdio import StdioServerParameters
from mcp.types import CallToolResult, TextContent

from conftest import (
    ContextFactory,
    SettingsFactory,
    symlink_or_skip,
    undecodable_name_or_skip,
    write,
)
from mcp_file_system.errors import AccessDeniedError, StaleFileError
from mcp_file_system.server import build_instructions, create_server

pytestmark = pytest.mark.anyio

# An unfixed server never answers, so this is how long a failing stdio test takes.
STDIO_TIMEOUT_S = 30
REPLACEMENT = "�"
KEY_JSON = '{"\\ud800": 1}\n'
RANGES_ARGUMENT = '[{"path": "caf\\udce9.txt", "start": 1, "end": 2}]'


def text_of(result: CallToolResult) -> str:
    block = result.content[0]
    assert isinstance(block, TextContent)
    return block.text


def assert_sendable(result: CallToolResult) -> None:
    """Serialize the result the way the SDK's stdio writer does, which raises on a surrogate.

    The in-memory transport never serializes, and ``json.dumps`` accepts a surrogate.
    """
    result.model_dump_json(by_alias=True, exclude_unset=True)


@pytest.fixture
async def client(make_settings: SettingsFactory) -> AsyncIterator[Client]:
    async with Client(create_server(make_settings(profile="full", allow_write=True))) as connected:
        yield connected


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("outline", {"path": "key.json"}),
        ("grep", {"pattern": "ud800", "path": "."}),
        ("read_ranges", {"ranges": RANGES_ARGUMENT}),
    ],
    ids=["file-content", "tree-wide-grep", "list-argument"],
)
async def test_server_answers_over_stdio_and_stays_up(
    root: Path, tool: str, arguments: dict[str, Any]
) -> None:
    write(root / "key.json", KEY_JSON)
    write(root / "ok.json", '{"a": 1}\n')
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_file_system", "--root", str(root), "--profile", "full"],
    )
    async with asyncio.timeout(STDIO_TIMEOUT_S):
        async with Client(params) as stdio:
            answer = await stdio.call_tool(tool, arguments)
            assert REPLACEMENT in text_of(answer)
            after = await stdio.call_tool("query", {"path": "ok.json"})
            assert not after.is_error


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("outline", {"path": "key.json"}),
        ("query", {"path": "key.json"}),
        ("peek", {"path": "key.json"}),
        ("peek", {"path": "package.json"}),
        ("outline", {"path": "key.yaml"}),
        ("query", {"path": "key.yaml"}),
        ("grep", {"pattern": "ud800", "path": "."}),
    ],
)
async def test_surrogate_from_file_content_is_replaced(
    root: Path, client: Client, tool: str, arguments: dict[str, Any]
) -> None:
    write(root / "key.json", KEY_JSON)
    write(root / "key.yaml", '"\\ud800": 1\n')
    write(root / "package.json", '{"name": "\\ud800", "version": "1"}\n')
    result = await client.call_tool(tool, arguments)
    assert_sendable(result)
    assert not result.is_error
    assert REPLACEMENT in text_of(result)


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("query", {"path": "key.json", "key_path": "missing"}),
        ("read_symbol", {"path": "key.json", "name": "missing"}),
    ],
)
async def test_surrogate_in_an_error_message_is_replaced(
    root: Path, client: Client, tool: str, arguments: dict[str, Any]
) -> None:
    write(root / "key.json", KEY_JSON)
    result = await client.call_tool(tool, arguments)
    assert_sendable(result)
    assert result.is_error
    assert REPLACEMENT in text_of(result)


async def test_structured_output_is_replaced_too(root: Path, client: Client) -> None:
    write(root / "key.json", KEY_JSON)
    result = await client.call_tool("query", {"path": "key.json"})
    assert result.structured_content is not None
    assert REPLACEMENT in result.structured_content["content"]


async def test_diff_shows_an_undecodable_byte_as_a_read_does(root: Path, client: Client) -> None:
    # A BOM keeps the file UTF-8, so the byte stays undecodable rather than Latin-1.
    write(root / "bom.txt", b"\xef\xbb\xbfcaf\xe9 line\nnext\n")
    arguments = {"path": "bom.txt", "pattern": "next", "replacement": "after", "dry_run": True}
    read = await client.call_tool("read_lines", {"path": "bom.txt"})
    diff = await client.call_tool("replace_in_file", arguments)
    assert_sendable(diff)
    assert f"caf{REPLACEMENT} line" in text_of(read)
    assert f"caf{REPLACEMENT} line" in text_of(diff)


async def test_replacing_keeps_the_length_output_limits_count(root: Path, client: Client) -> None:
    write(root / "odd.json", '{"' + "\\ud800" * 200 + '": 1}\n')
    write(root / "wide.json", '{"' + "é" * 200 + '": 1}\n')
    odd = await client.call_tool("query", {"path": "odd.json"})
    wide = await client.call_tool("query", {"path": "wide.json"})
    assert odd.structured_content is not None
    assert wide.structured_content is not None
    assert len(odd.structured_content["content"]) == len(wide.structured_content["content"])


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("multi_replace", {"edits": '[{"pattern": "alpha", "replacement": "caf\\udce9"}]'}),
        ("replace_in_file", {"pattern": "alpha", "replacement": "caf\\udce9", "regex": True}),
    ],
    ids=["list-argument", "regex-escape"],
)
async def test_new_text_cannot_add_an_undecodable_byte(
    root: Path, client: Client, tool: str, arguments: dict[str, Any]
) -> None:
    # Both arrive as plain ASCII escapes, the way the wire carries them.
    path = write(root / "a.txt", b"alpha\n")
    result = await client.call_tool(tool, {"path": "a.txt", **arguments})
    assert_sendable(result)
    assert result.is_error
    assert path.read_bytes() == b"alpha\n"


def test_instructions_replace_a_surrogate_in_a_root(
    root: Path, make_settings: SettingsFactory
) -> None:
    settings = make_settings().model_copy(update={"roots": (root, Path("/caf\udce9"))})
    text = build_instructions(settings)
    assert f"caf{REPLACEMENT}" in text
    text.encode()


class TestWriteFileToADisplayedName:
    """A listed name stands for bytes it cannot show, so writing to it must not make a sibling."""

    @pytest.mark.parametrize("dry_run", [False, True])
    @pytest.mark.parametrize("name", [f"caf{REPLACEMENT}.txt", f"d{REPLACEMENT}/new.txt"])
    async def test_creating_is_refused(
        self, root: Path, client: Client, name: str, dry_run: bool
    ) -> None:
        result = await client.call_tool(
            "write_file", {"path": name, "content": "x\n", "dry_run": dry_run}
        )
        assert result.is_error
        assert list(root.iterdir()) == []

    async def test_a_dangling_link_to_such_a_name_is_refused(
        self, root: Path, client: Client
    ) -> None:
        symlink_or_skip(root / "link", root / f"target{REPLACEMENT}.txt")
        result = await client.call_tool("write_file", {"path": "link", "content": "x\n"})
        assert result.is_error
        assert not (root / f"target{REPLACEMENT}.txt").exists()

    async def test_a_clean_new_name_in_a_real_directory_is_created(
        self, root: Path, client: Client
    ) -> None:
        write(root / f"real{REPLACEMENT}" / "other.txt", "old\n")
        path = f"real{REPLACEMENT}/new.txt"
        result = await client.call_tool("write_file", {"path": path, "content": "x\n"})
        assert not result.is_error
        assert (root / path).read_bytes() == b"x\n"

    def test_an_etag_on_a_missing_path_stays_stale(self, make_ctx: ContextFactory) -> None:
        editor = make_ctx(allow_write=True).editor
        with pytest.raises(StaleFileError):
            editor.write(f"caf{REPLACEMENT}.txt", "x\n", overwrite=True, expected_etag="1-1")

    def test_the_deny_list_comes_first(self, make_ctx: ContextFactory) -> None:
        editor = make_ctx(allow_write=True).editor
        with pytest.raises(AccessDeniedError):
            editor.write(f"secret{REPLACEMENT}.pem", "x\n")

    async def test_a_real_name_with_the_character_can_be_overwritten(
        self, root: Path, client: Client
    ) -> None:
        target = write(root / f"caf{REPLACEMENT}.txt", "old\n")
        info = await client.call_tool("file_info", {"path": target.name})
        assert info.structured_content is not None
        result = await client.call_tool(
            "write_file",
            {
                "path": target.name,
                "content": "new\n",
                "overwrite": True,
                "expected_etag": info.structured_content["etag"],
            },
        )
        assert not result.is_error
        assert target.read_bytes() == b"new\n"


class TestRealFileNames:
    """Names that are not UTF-8; skipped where the filesystem refuses them (APFS)."""

    @pytest.mark.parametrize(
        ("tool", "arguments"),
        [
            ("list_directory", {"path": "."}),
            ("find_files", {"glob": "*.txt"}),
            ("grep", {"pattern": "data", "path": "."}),
            ("file_info", {"path": "caf\udce9.txt"}),
            ("read_lines", {"path": "caf\udce9.txt"}),
        ],
    )
    async def test_name_is_shown_replaced(
        self, root: Path, client: Client, tool: str, arguments: dict[str, Any]
    ) -> None:
        undecodable_name_or_skip(root / "caf\udce9.txt")
        result = await client.call_tool(tool, arguments)
        assert_sendable(result)
        assert not result.is_error
        assert f"caf{REPLACEMENT}.txt" in text_of(result)

    async def test_listed_name_over_stdio(self, root: Path) -> None:
        undecodable_name_or_skip(root / "caf\udce9.txt")
        params = StdioServerParameters(
            command=sys.executable, args=["-m", "mcp_file_system", "--root", str(root)]
        )
        async with asyncio.timeout(STDIO_TIMEOUT_S):
            async with Client(params) as stdio:
                listing = await stdio.call_tool("list_directory", {"path": "."})
                assert f"caf{REPLACEMENT}.txt" in text_of(listing)

    async def test_undecodable_root_over_stdio(self, root: Path) -> None:
        second = undecodable_name_or_skip(root.parent / "caf\udce9" / "f.txt").parent
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "mcp_file_system", "--root", str(root), "--root", str(second)],
        )
        async with asyncio.timeout(STDIO_TIMEOUT_S):
            async with Client(params) as stdio:
                tools = await stdio.list_tools()
                assert tools.tools

    async def test_denied_name_stays_hidden(self, root: Path, client: Client) -> None:
        undecodable_name_or_skip(root / "secret\udce9.pem")
        result = await client.call_tool("list_directory", {"path": "."})
        assert "secret" not in text_of(result)
