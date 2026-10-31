"""README.md's tool, limit and settings tables against the code, so they cannot drift apart.

The tests read only the facts out of the tables (names, numbers, flags), not the prose around
them. A failure means the README or the code changed without the other.
"""

import re
from pathlib import Path
from typing import Any

import pytest
from mcp.client import Client
from mcp.types import Tool

import mcp_file_system
from conftest import SettingsFactory
from mcp_file_system import cli
from mcp_file_system.config import Settings
from mcp_file_system.core.editor import MAX_EDITS
from mcp_file_system.core.finder import MAX_WALK_DEPTH
from mcp_file_system.core.history import MAX_UNDO_BYTES, MAX_UNDO_FILES
from mcp_file_system.core.patch import MAX_HUNKS
from mcp_file_system.core.peek.tabular import MAX_COLUMNS, MAX_SCAN_ROWS, SAMPLE_ROWS
from mcp_file_system.core.peek.text import MAX_SCAN_LINES
from mcp_file_system.core.reader import DEFAULT_RADIUS, MAX_BATCH_RANGES
from mcp_file_system.server import create_server

pytestmark = pytest.mark.anyio

README = (Path(mcp_file_system.__file__).parents[2] / "README.md").read_text(encoding="utf-8")
MIB = 1024 * 1024

# Settings whose default the table describes in words instead of giving the value.
DESCRIBED_DEFAULTS = {
    "roots": "CWD",
    "deny_globs": "secrets list",
    "tools": "all",
    "output_schema": "by profile",
    "audit_log": "off",
}
# Options the settings table leaves out on purpose: no setting behind them, or the negative
# form of a read-only default that needs no documenting.
OPTIONS_NOT_IN_TABLE = {"--help", "--version", "--no-allow-write"}


def section(heading: str) -> str:
    match = re.search(rf"^## {heading}\n(.*?)(?=^## |\Z)", README, re.MULTILINE | re.DOTALL)
    assert match is not None, f"README has no '## {heading}' section"
    return match.group(1)


def tool_rows() -> dict[str, tuple[bool, str]]:
    """Each row of the Tools table as ``name -> (marked as a write tool, description)``."""
    rows = re.findall(r"^\| `([a-z_]+)`(\*?) \| (.+) \|$", section("Tools"), re.MULTILINE)
    return {name: (bool(star), text) for name, star, text in rows}


def settings_rows() -> dict[str, tuple[str, str]]:
    """Each row of the settings table as ``name -> (CLI flag cell, default cell)``."""
    rows = re.findall(r"^\| `([a-z_]+)` \|([^|]*)\|([^|]*)\|", section("Configuration"), re.M)
    return {name: (flag.strip(), default.strip()) for name, flag, default in rows}


def number_in(text: str, pattern: str) -> int:
    match = re.search(pattern, text)
    assert match is not None, f"{pattern!r} not found in: {text[:200]}"
    return int(match.group(1).replace(",", ""))


async def list_tools(make_settings: SettingsFactory, **overrides: Any) -> dict[str, Tool]:
    async with Client(create_server(make_settings(**overrides))) as client:
        return {tool.name: tool for tool in (await client.list_tools()).tools}


async def test_tool_table_matches_the_listing(make_settings: SettingsFactory) -> None:
    tools = await list_tools(make_settings, allow_write=True)
    rows = tool_rows()
    assert set(rows) == set(tools)
    for name, (marked_write, _) in rows.items():
        annotations = tools[name].annotations
        assert annotations is not None
        assert marked_write is (annotations.read_only_hint is False), name


async def test_profiles_row_matches_the_listings(make_settings: SettingsFactory) -> None:
    full = await list_tools(make_settings, allow_write=True)
    lite = await list_tools(make_settings, profile="lite", allow_write=True)

    def counts(tools: dict[str, Tool]) -> tuple[int, int]:
        read = [t for t in tools.values() if t.annotations and t.annotations.read_only_hint]
        return len(read), len(tools) - len(read)

    match = re.search(
        r"^\| Tools \| all (\d+) \(\+(\d+) editing\) \| (\d+) \(\+(\d+) editing\)(.*) \|$",
        README,
        re.MULTILINE,
    )
    assert match is not None
    full_read, full_write, lite_read, lite_write, missing = match.groups()
    assert (int(full_read), int(full_write)) == counts(full)
    assert (int(lite_read), int(lite_write)) == counts(lite)
    assert set(re.findall(r"`([a-z_]+)`", missing)) == set(full) - set(lite)


@pytest.mark.parametrize(
    ("tool", "pattern", "expected"),
    [
        ("read_ranges", r"at most (\d+)\)", MAX_BATCH_RANGES),
        ("read_lines", r"`radius` defaults to (\d+)", DEFAULT_RADIUS),
        ("find_files", r"`max_depth` up to (\d+)", MAX_WALK_DEPTH),
        ("multi_replace", r"at most (\d+)\)", MAX_EDITS),
        ("apply_patch", r"at most (\d+) hunks", MAX_HUNKS),
        ("undo_last_edit", r"at most (\d+) files", MAX_UNDO_FILES),
        ("undo_last_edit", r"and (\d+) MiB", MAX_UNDO_BYTES // MIB),
        ("peek", r"(\d+) sample rows", SAMPLE_ROWS),
        ("peek", r"after ([\d,]+) rows", MAX_SCAN_ROWS),
        ("peek", r"show (\d+) columns", MAX_COLUMNS),
        ("peek", r"logs after ([\d,]+) lines", MAX_SCAN_LINES),
    ],
)
def test_tool_table_limits_match_the_constants(tool: str, pattern: str, expected: int) -> None:
    assert number_in(tool_rows()[tool][1], pattern) == expected


async def test_tool_table_limits_match_the_schemas(make_settings: SettingsFactory) -> None:
    tools = await list_tools(make_settings)
    rows = tool_rows()

    depth = tools["query"].input_schema["properties"]["depth"]
    assert number_in(rows["query"][1], r"default (\d+), at most") == depth["default"]
    assert number_in(rows["query"][1], r"at most (\d+)\)") == depth["maximum"]

    depth = tools["list_directory"].input_schema["properties"]["depth"]
    assert number_in(rows["list_directory"][1], r"`depth` (\d+)\D") == depth["minimum"]
    assert number_in(rows["list_directory"][1], r"`depth` \d+\D(\d+)") == depth["maximum"]


def test_settings_table_covers_every_setting_and_default() -> None:
    rows = settings_rows()
    assert set(rows) == set(Settings.model_fields)
    for name, (_, cell) in rows.items():
        if name in DESCRIBED_DEFAULTS:
            assert cell == DESCRIBED_DEFAULTS[name], name
            continue
        text = cell.strip("`")
        mebibytes = re.fullmatch(r"(\d+) MiB", text)
        documented: object = text
        if mebibytes:
            documented = int(mebibytes.group(1)) * MIB
        elif text in ("true", "false"):
            documented = text == "true"
        elif text.isdigit():
            documented = int(text)
        assert documented == Settings.model_fields[name].default, name


def test_settings_table_flags_match_the_parser() -> None:
    documented: set[str] = set()
    for flag_cell, _ in settings_rows().values():
        for negatable, flag in re.findall(r"--(\[no-\])?([a-z-]+)", flag_cell):
            documented.add(f"--{flag}")
            if negatable:
                documented.add(f"--no-{flag}")
    options = {
        option
        for action in cli.build_parser()._actions
        for option in action.option_strings
        if option.startswith("--")
    }
    assert documented == options - OPTIONS_NOT_IN_TABLE
