"""The audit log end to end: configuration, recording through the MCP server, privacy."""

import json
from pathlib import Path
from typing import Any

import pytest
from mcp.client import Client
from pydantic import BaseModel, ValidationError

from conftest import SettingsFactory, symlink_or_skip, write
from mcp_file_system import cli
from mcp_file_system.audit import AuditLog
from mcp_file_system.context import AppContext
from mcp_file_system.core.etag import compute_etag
from mcp_file_system.server import FileSystemServer, create_server
from mcp_file_system.tools._common import READ_ONLY, ToolRegistry

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
    assert log[0]["args"] == {"path": "config.py", "start": 1, "end": -1, "if_changed_since": ""}
    assert log[0]["result_chars"] > 0
    assert log[0]["error"] is None
    assert log[1]["args"]["pattern"] == "api_token"
    assert log[2]["args"]["pattern"] == {"chars": len(SENSITIVE_LINE)}
    assert log[3]["error"]["type"] == "PathOutsideRootError"
    # The secret value appears in tool results, but never in the log.
    assert "do-not-log-me" not in log_path.read_text()


@pytest.mark.anyio
async def test_patches_are_recorded_without_content(
    make_settings: SettingsFactory, root: Path, tmp_path: Path
) -> None:
    log_path = tmp_path / "audit.jsonl"
    path = write(root / "config.py", f"{SENSITIVE_LINE}\n")
    etag = compute_etag(path.stat())
    failing = f"@@ -1,1 +1,1 @@ def load_secret():\n-{SENSITIVE_LINE}x\n+api_token = None\n"
    working = f"@@ -1,1 +1,1 @@ def load_secret():\n-{SENSITIVE_LINE}\n+api_token = None\n"
    settings = make_settings(audit_log=log_path, allow_write=True)
    async with Client(create_server(settings)) as client:
        failed = await client.call_tool(
            "apply_patch", {"path": "config.py", "diff": failing, "expected_etag": etag}
        )
        applied = await client.call_tool(
            "apply_patch", {"path": "config.py", "diff": working, "expected_etag": etag}
        )
    assert failed.is_error
    assert not applied.is_error
    log = read_log(log_path)
    assert [entry["args"]["diff"] for entry in log] == [
        {"chars": len(failing)},
        {"chars": len(working)},
    ]
    assert log[0]["error"]["type"] == "NoMatchError"
    text = log_path.read_text()
    assert "do-not-log-me" not in text
    assert "load_secret" not in text


@pytest.mark.anyio
async def test_multi_replace_is_recorded_without_content(
    make_settings: SettingsFactory, root: Path, tmp_path: Path
) -> None:
    log_path = tmp_path / "audit.jsonl"
    path = write(root / "config.py", f"{SENSITIVE_LINE}\n")
    edits = [{"pattern": SENSITIVE_LINE, "replacement": "api_token = None"}]
    settings = make_settings(audit_log=log_path, allow_write=True)
    async with Client(create_server(settings)) as client:
        result = await client.call_tool("multi_replace", {"path": "config.py", "edits": edits})
    assert not result.is_error
    assert path.read_text() == "api_token = None\n"
    (entry,) = read_log(log_path)
    assert entry["args"]["edits"] == {"items": 1, "chars": len(SENSITIVE_LINE) + 16}
    assert "do-not-log-me" not in log_path.read_text()


@pytest.mark.anyio
async def test_invalid_regex_pattern_is_not_logged(
    make_settings: SettingsFactory, root: Path, tmp_path: Path
) -> None:
    log_path = tmp_path / "audit.jsonl"
    write(root / "config.py", f"{SENSITIVE_LINE}\n")
    pattern = SENSITIVE_LINE + "["
    settings = make_settings(audit_log=log_path, allow_write=True)
    async with Client(create_server(settings)) as client:
        result = await client.call_tool(
            "replace_in_file",
            {"path": "config.py", "pattern": pattern, "replacement": "x", "regex": True},
        )
    assert result.is_error
    (entry,) = read_log(log_path)
    assert entry["args"]["pattern"] == {"chars": len(pattern)}
    assert entry["error"]["type"] == "InvalidPatternError"
    assert "do-not-log-me" not in log_path.read_text()


@pytest.mark.anyio
async def test_schema_rejections_are_recorded_without_values(
    make_settings: SettingsFactory, root: Path, tmp_path: Path
) -> None:
    log_path = tmp_path / "audit.jsonl"
    write(root / "config.py", f"{SENSITIVE_LINE}\n")
    settings = make_settings(audit_log=log_path, allow_write=True)
    calls: list[dict[str, Any]] = [
        # A wrong type for a plain argument.
        {"path": "config.py", "pattern": SENSITIVE_LINE, "replacement": "x", "expected_count": "n"},
        # The secret itself as the failing value.
        {"path": "config.py", "pattern": "a", "replacement": "b", "expected_count": SENSITIVE_LINE},
        # A list where a string belongs.
        {"path": [SENSITIVE_LINE], "pattern": "a", "replacement": "b"},
    ]
    async with Client(create_server(settings)) as client:
        results = [await client.call_tool("replace_in_file", call) for call in calls]
        # Misnamed content argument: "content" is missing, "text" is undeclared.
        results.append(
            await client.call_tool("write_file", {"path": "new.py", "text": SENSITIVE_LINE})
        )
    assert all(result.is_error for result in results)
    log = read_log(log_path)
    assert [entry["tool"] for entry in log] == ["replace_in_file"] * 3 + ["write_file"]
    assert {entry["error"]["type"] for entry in log} == {"ValidationError"}
    assert log[0]["error"]["message"].startswith("expected_count: ")
    assert log[0]["args"] == {
        "path": "config.py",
        "pattern": {"chars": len(SENSITIVE_LINE)},
        "replacement": {"chars": 1},
        "expected_count": {"chars": 1},
    }
    assert log[1]["args"]["expected_count"] == {"chars": len(SENSITIVE_LINE)}
    assert log[2]["args"]["path"] == {"items": 1, "chars": len(SENSITIVE_LINE)}
    assert log[3]["args"] == {"path": "new.py", "text": {"chars": len(SENSITIVE_LINE)}}
    assert all(entry["result_chars"] is None for entry in log)
    assert "do-not-log-me" not in log_path.read_text()


@pytest.mark.anyio
async def test_unknown_tools_are_recorded_without_values(
    make_settings: SettingsFactory, tmp_path: Path
) -> None:
    log_path = tmp_path / "audit.jsonl"
    settings = make_settings(audit_log=log_path)  # read-only: write_file is not exposed
    async with Client(create_server(settings)) as client:
        hidden = await client.call_tool("write_file", {"path": "a.py", "content": SENSITIVE_LINE})
        made_up = await client.call_tool("save_file", {"path": "a.py", "data": SENSITIVE_LINE})
    assert hidden.is_error
    assert made_up.is_error
    log = read_log(log_path)
    assert [entry["tool"] for entry in log] == ["write_file", "save_file"]
    assert {entry["error"]["type"] for entry in log} == {"UnknownToolError"}
    assert log[1]["args"] == {
        "path": {"chars": 4},
        "data": {"chars": len(SENSITIVE_LINE)},
    }
    assert "do-not-log-me" not in log_path.read_text()


@pytest.mark.anyio
async def test_validation_error_inside_a_tool_is_recorded_once(
    make_settings: SettingsFactory, tmp_path: Path
) -> None:
    # A crash with a ValidationError cause must not pass for a schema rejection.
    log_path = tmp_path / "audit.jsonl"
    settings = make_settings(audit_log=log_path)
    audit = AuditLog(log_path, profile="full")
    server = FileSystemServer(
        name="test", version="0", instructions="", log_level="WARNING", audit=audit
    )
    registry = ToolRegistry(server, settings, audit)

    class Point(BaseModel):
        x: int

    @registry.tool(READ_ONLY)
    def broken(path: str) -> str:
        """Fails inside its body."""
        return str(Point.model_validate({"x": path}))

    async with Client(server) as client:
        result = await client.call_tool("broken", {"path": "a.py"})
    assert result.is_error
    (entry,) = read_log(log_path)
    assert entry["error"] == {"type": "ValidationError", "message": "unexpected error"}


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
