"""The audit log end to end: configuration, recording through the MCP server, privacy."""

import json
from pathlib import Path
from typing import Any

import pytest
from mcp.client import Client
from pydantic import ValidationError

from conftest import SettingsFactory, symlink_or_skip, write
from mcp_file_system import cli
from mcp_file_system.context import AppContext
from mcp_file_system.server import create_server

SENSITIVE_LINE = "api_token = 'do-not-log-me'"


def read_log(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_disabled_by_default(make_settings: SettingsFactory) -> None:
    assert AppContext.from_settings(make_settings()).audit is None


def test_log_inside_a_root_is_rejected(make_settings: SettingsFactory, root: Path) -> None:
    with pytest.raises(ValidationError, match="must be outside the allowed roots"):
        make_settings(audit_log=root / "logs" / "audit.jsonl")


def test_log_reached_through_symlink_into_root_is_rejected(
    make_settings: SettingsFactory, root: Path, tmp_path: Path
) -> None:
    symlink_or_skip(tmp_path / "logs", root)
    with pytest.raises(ValidationError, match="must be outside the allowed roots"):
        make_settings(audit_log=tmp_path / "logs" / "audit.jsonl")


def test_log_path_is_resolved(make_settings: SettingsFactory, tmp_path: Path) -> None:
    settings = make_settings(audit_log=tmp_path / "a" / ".." / "audit.jsonl")
    assert settings.audit_log == (tmp_path / "audit.jsonl").resolve()


def test_cli_flag_and_unwritable_location(
    root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = cli.build_parser().parse_args(
        ["--root", str(root), "--audit-log", str(tmp_path / "a.log")]
    )
    assert cli.settings_from_args(args).audit_log == (tmp_path / "a.log").resolve()

    (tmp_path / "blocker").write_text("file")
    code = cli.main(["--root", str(root), "--audit-log", str(tmp_path / "blocker" / "a.log")])
    assert code == cli.EXIT_CONFIG_ERROR
    assert "invalid configuration" in capsys.readouterr().err


@pytest.mark.anyio
async def test_calls_are_recorded_without_file_contents(
    make_settings: SettingsFactory, root: Path, tmp_path: Path
) -> None:
    log_path = tmp_path / "audit" / "audit.jsonl"
    write(root / "config.py", f"{SENSITIVE_LINE}\n")
    settings = make_settings(audit_log=log_path, allow_write=True, profile="lite")
    async with Client(create_server(settings)) as client:
        await client.call_tool("read_lines", {"path": "config.py"})
        await client.call_tool("grep", {"pattern": "api_token"})
        await client.call_tool(
            "replace_in_file",
            {"path": "config.py", "pattern": SENSITIVE_LINE, "replacement": "api_token = None"},
        )
        await client.call_tool("read_lines", {"path": "../escape.txt"})

    log = read_log(log_path)
    assert [entry["tool"] for entry in log] == [
        "read_lines",
        "grep",
        "replace_in_file",
        "read_lines",
    ]
    assert all(entry["profile"] == "lite" for entry in log)
    assert log[0]["args"] == {"path": "config.py", "start": 1, "end": -1}
    assert log[0]["result_chars"] > 0
    assert log[0]["error"] is None
    assert log[1]["args"]["pattern"] == "api_token"
    assert log[2]["args"]["pattern"] == {"chars": len(SENSITIVE_LINE)}
    assert log[3]["error"]["type"] == "PathOutsideRootError"
    # The secret value appears in tool results, but never in the log.
    assert "do-not-log-me" not in log_path.read_text()


@pytest.mark.anyio
async def test_disallowed_tools_are_not_wrapped(
    make_settings: SettingsFactory, root: Path, tmp_path: Path
) -> None:
    log_path = tmp_path / "audit.jsonl"
    write(root / "a.txt", "x\n")
    settings = make_settings(audit_log=log_path, tools=("file_info",))
    async with Client(create_server(settings)) as client:
        await client.call_tool("file_info", {"path": "a.txt"})
    assert [entry["tool"] for entry in read_log(log_path)] == ["file_info"]
