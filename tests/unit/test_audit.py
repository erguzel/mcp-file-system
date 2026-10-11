import json
import logging
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, TextContent

from conftest import write
from mcp_file_system.audit import MAX_ARGUMENT_CHARS, AuditLog, sanitize_arguments
from mcp_file_system.core.models import Replacement
from mcp_file_system.errors import StaleFileError
from mcp_file_system.tools._common import audited

FIXED = datetime(2026, 10, 4, 12, 0, 1, 123456, tzinfo=UTC)


def make_log(tmp_path: Path) -> AuditLog:
    return AuditLog(tmp_path / "logs" / "audit.jsonl", profile="lite", clock=lambda: FIXED)


def entries(log: AuditLog) -> list[dict[str, Any]]:
    return [json.loads(line) for line in log.path.read_text().splitlines()]


def test_creates_parent_directory_and_file(tmp_path: Path) -> None:
    log = make_log(tmp_path)
    assert log.path.exists()
    assert log.path.read_text() == ""


def test_example_record_is_about_200_bytes(tmp_path: Path) -> None:
    # README, Audit log: "about 200 bytes per call", shown with this example record.
    log = make_log(tmp_path)
    log.record(
        tool="read_lines",
        arguments={"path": "src/a.py", "start": 10, "end": 40, "if_changed_since": ""},
        duration_ms=2.5,
        result_chars=1240,
        truncated=False,
        error=None,
    )
    line = log.path.read_bytes()
    assert 150 <= len(line) <= 250, len(line)


def test_unwritable_location_fails_at_startup(tmp_path: Path) -> None:
    write(tmp_path / "blocker", "a file, not a directory")
    # The parent "directory" exists as a regular file.
    with pytest.raises(FileExistsError):
        AuditLog(tmp_path / "blocker" / "audit.jsonl", profile="full")


def test_record_format(tmp_path: Path) -> None:
    log = make_log(tmp_path)
    log.record(
        tool="read_lines",
        arguments={"path": "a.py", "start": 1, "end": -1},
        duration_ms=3.14159,
        result_chars=120,
        truncated=False,
        error=None,
    )
    assert entries(log) == [
        {
            "ts": "2026-10-04T12:00:01Z",
            "tool": "read_lines",
            "profile": "lite",
            "args": {"path": "a.py", "start": 1, "end": -1},
            "duration_ms": 3.1,
            "result_chars": 120,
            "truncated": False,
            "error": None,
        }
    ]


def test_content_arguments_are_logged_as_length_only() -> None:
    assert sanitize_arguments(
        "replace_in_file", {"path": "a", "pattern": "SECRET", "replacement": "xyz"}
    ) == {"path": "a", "pattern": {"chars": 6}, "replacement": {"chars": 3}}
    assert sanitize_arguments("edit_lines", {"new_text": "hello"}) == {"new_text": {"chars": 5}}
    assert sanitize_arguments("write_file", {"content": ""}) == {"content": {"chars": 0}}
    # grep patterns are search terms, not file content: kept.
    assert sanitize_arguments("grep", {"pattern": "TODO"}) == {"pattern": "TODO"}


@pytest.mark.parametrize(
    "edits",
    [
        [Replacement(pattern="secret", replacement="xy"), Replacement(pattern="a", replacement="")],
        [{"pattern": "secret", "replacement": "xy"}, {"pattern": "a", "replacement": ""}],
    ],
)
def test_structured_content_arguments_are_logged_as_sizes(edits: list[Any]) -> None:
    sanitized = sanitize_arguments("multi_replace", {"path": "a.py", "edits": edits})
    assert sanitized == {"path": "a.py", "edits": {"items": 2, "chars": 9}}


def test_only_plain_scalars_are_logged_as_is() -> None:
    sanitized = sanitize_arguments(
        "replace_in_file",
        {"path": "a.py", "count": 2, "pattern": "SECRET", "text": "SECRET", "end": ["SECRET"]},
        plain=frozenset({"path", "count", "pattern", "end"}),
    )
    assert sanitized == {
        "path": "a.py",
        "count": 2,
        "pattern": {"chars": 6},  # a content argument stays a length
        "text": {"chars": 6},  # not plain
        "end": {"items": 1, "chars": 6},  # plain, but not a scalar
    }


def test_long_strings_are_cut() -> None:
    sanitized = sanitize_arguments("grep", {"pattern": "x" * 2000})
    assert len(sanitized["pattern"]) == MAX_ARGUMENT_CHARS + 1
    assert sanitized["pattern"].endswith("…")


def test_write_failures_never_raise_and_warn_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    log = make_log(tmp_path)
    original_open = Path.open

    def broken_open(self: Path, *args: Any, **kwargs: Any) -> Any:
        if self == log.path:
            raise OSError(28, "No space left on device")
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", broken_open)
    with caplog.at_level(logging.WARNING, logger="mcp_file_system.audit"):
        for _ in range(3):
            log.record(
                tool="grep", arguments={}, duration_ms=1, result_chars=0, truncated=None, error=None
            )
    assert len(caplog.records) == 1
    assert "No space left on device" in caplog.records[0].getMessage()

    # After recovery the next failure is reported again.
    monkeypatch.setattr(Path, "open", original_open)
    log.record(tool="grep", arguments={}, duration_ms=1, result_chars=0, truncated=None, error=None)
    monkeypatch.setattr(Path, "open", broken_open)
    with caplog.at_level(logging.WARNING, logger="mcp_file_system.audit"):
        log.record(
            tool="grep", arguments={}, duration_ms=1, result_chars=0, truncated=None, error=None
        )
    assert len(caplog.records) == 2


def test_concurrent_writes_produce_whole_lines(tmp_path: Path) -> None:
    log = make_log(tmp_path)

    def worker(index: int) -> None:
        for n in range(100):
            log.record(
                tool="grep",
                arguments={"pattern": f"t{index}-{n}" * 20},
                duration_ms=1,
                result_chars=n,
                truncated=None,
                error=None,
            )

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(entries(log)) == 800  # every line parses as JSON


class TestAudited:
    def test_success_records_arguments_with_defaults_and_result_size(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)

        def tool(path: str, start: int = 1) -> CallToolResult:
            return CallToolResult(
                content=[TextContent(type="text", text="12345")],
                structured_content={"truncated": True},
            )

        wrapped = audited(log, "read_lines", tool)
        assert wrapped(path="a.py").content[0].text == "12345"  # type: ignore[union-attr]
        entry = entries(log)[0]
        assert entry["args"] == {"path": "a.py", "start": 1}
        assert (entry["result_chars"], entry["truncated"], entry["error"]) == (5, True, None)

    def test_domain_errors_are_recorded_with_type_and_message(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)

        def tool(path: str) -> CallToolResult:
            raise ToolError("stale") from StaleFileError("a.py changed since it was read")

        with pytest.raises(ToolError):
            audited(log, "edit_lines", tool)(path="a.py")
        entry = entries(log)[0]
        assert entry["error"] == {
            "type": "StaleFileError",
            "message": "a.py changed since it was read",
        }
        assert entry["result_chars"] is None

    def test_unexpected_errors_hide_their_message(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)

        def tool() -> CallToolResult:
            raise RuntimeError("internal detail")

        with pytest.raises(RuntimeError):
            audited(log, "grep", tool)()
        assert entries(log)[0]["error"] == {"type": "RuntimeError", "message": "unexpected error"}

    def test_tool_error_without_cause(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)

        def tool() -> CallToolResult:
            raise ToolError("plain")

        with pytest.raises(ToolError):
            audited(log, "grep", tool)()
        assert entries(log)[0]["error"] == {"type": "ToolError", "message": "plain"}

    def test_non_tool_result_is_not_measured(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        audited(log, "x", lambda: "plain")()
        entry = entries(log)[0]
        assert (entry["result_chars"], entry["truncated"]) == (None, None)
