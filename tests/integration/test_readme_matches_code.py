"""The facts README.md states about tools, limits and settings, against the code.

Covered: tool names and write marks, the profiles row's tool counts, the limits listed below,
and setting names, defaults and flags. Descriptions are not checked. A failure means a fact
changed on one side only, or the wording a pattern anchors on (``at most ... hunks``) changed.
"""

import re
from pathlib import Path

import pytest
from mcp.types import Tool

from conftest import SettingsFactory, list_tools
from mcp_file_system import cli
from mcp_file_system.config import DEFAULT_DENY_GLOBS, MIB, Settings
from mcp_file_system.core.editor import MAX_EDITS
from mcp_file_system.core.finder import MAX_WALK_DEPTH
from mcp_file_system.core.history import MAX_UNDO_BYTES, MAX_UNDO_FILES
from mcp_file_system.core.patch import MAX_HUNKS
from mcp_file_system.core.peek.tabular import MAX_COLUMNS, MAX_SCAN_ROWS, SAMPLE_ROWS
from mcp_file_system.core.peek.text import MAX_SCAN_LINES
from mcp_file_system.core.reader import DEFAULT_RADIUS, MAX_BATCH_RANGES

pytestmark = pytest.mark.anyio

README = (Path(__file__).parents[2] / "README.md").read_text(encoding="utf-8")

# Parser destinations that are not named after their setting.
SETTING_OF_DEST = {"deny": "deny_globs"}
# Options with no setting behind them, and the negative form of a read-only default.
OPTIONS_NOT_IN_TABLE = {"--help", "--version", "--no-allow-write"}


def section(heading: str) -> str:
    match = re.search(rf"^## {heading}\n(.*?)(?=^## |\Z)", README, re.MULTILINE | re.DOTALL)
    assert match is not None, f"README has no '## {heading}' section"
    return match.group(1)


def tool_rows() -> dict[str, tuple[bool, str]]:
    """Each row of the Tools table as ``name -> (marked as a write tool, description)``."""
    rows = re.findall(r"^\| `([a-z_]+)`(\*?) \| (.+) \|$", section("Tools"), re.MULTILINE)
    assert len({name for name, _, _ in rows}) == len(rows), "duplicate row in the Tools table"
    return {name: (bool(star), text) for name, star, text in rows}


def settings_rows() -> dict[str, tuple[str, str]]:
    """Each row of the settings table as ``name -> (CLI flag cell, default cell)``."""
    rows = re.findall(r"^\| `([a-z_]+)` \|([^|]*)\|([^|]*)\|", section("Configuration"), re.M)
    assert len({name for name, _, _ in rows}) == len(rows), "duplicate row in the settings table"
    return {name: (flag.strip(), default.strip()) for name, flag, default in rows}


def number_in(text: str, pattern: str) -> int:
    match = re.search(pattern, text)
    assert match is not None, f"{pattern!r} not found in: {text[:200]}"
    return int(match.group(1).replace(",", ""))


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
        ("undo_last_edit", r"and (\d+) MiB", MAX_UNDO_BYTES / MIB),
        ("peek", r"(\d+) sample rows", SAMPLE_ROWS),
        ("peek", r"after ([\d,]+) rows", MAX_SCAN_ROWS),
        ("peek", r"show (\d+) columns", MAX_COLUMNS),
        ("peek", r"logs after ([\d,]+) lines", MAX_SCAN_LINES),
    ],
)
def test_tool_table_limits_match_the_constants(tool: str, pattern: str, expected: float) -> None:
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
    # Defaults the table gives in words: the word and the value it stands for.
    described: dict[str, tuple[str, object]] = {
        "roots": ("CWD", (Path.cwd(),)),
        "deny_globs": ("secrets list", DEFAULT_DENY_GLOBS),
        "tools": ("all", ()),
        "output_schema": ("by profile", None),
        "audit_log": ("off", None),
    }
    rows = settings_rows()
    assert set(rows) == set(Settings.model_fields)
    for name, (_, cell) in rows.items():
        field = Settings.model_fields[name]
        if name in described:
            word, value = described[name]
            assert cell == word, name
            assert field.get_default(call_default_factory=True) == value, name
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
        assert documented == field.default, name
        # 0 == False and 1 == True, so a number must not pass for a boolean.
        assert isinstance(documented, bool) == isinstance(field.default, bool), name


def test_settings_table_flags_match_the_parser() -> None:
    options: dict[str, set[str]] = {}
    for action in cli.build_parser()._actions:
        long_options = {o for o in action.option_strings if o.startswith("--")}
        long_options -= OPTIONS_NOT_IN_TABLE
        if long_options:
            options[SETTING_OF_DEST.get(action.dest, action.dest)] = long_options
    rows = settings_rows()
    assert set(options) <= set(rows)
    for name, (flag_cell, _) in rows.items():
        documented: set[str] = set()
        for negatable, flag in re.findall(r"--(\[no-\])?([a-z-]+)", flag_cell):
            documented.add(f"--{flag}")
            if negatable:
                documented.add(f"--no-{flag}")
        assert documented == options.get(name, set()), name
