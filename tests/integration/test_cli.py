import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from mcp.client import Client
from mcp.client.stdio import StdioServerParameters

from conftest import write
from mcp_file_system import __version__, cli
from mcp_file_system.config import DEFAULT_DENY_GLOBS
from mcp_file_system.security import AccessPolicy


def parse(*argv: str) -> Any:
    return cli.build_parser().parse_args(list(argv))


def test_unset_flags_defer_to_environment(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    monkeypatch.chdir(root)
    monkeypatch.setenv("MCP_FS_ALLOW_WRITE", "1")
    settings = cli.settings_from_args(parse())
    assert settings.allow_write is True
    assert settings.roots == (root,)


def test_flags_override_environment(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    monkeypatch.setenv("MCP_FS_ALLOW_WRITE", "1")
    settings = cli.settings_from_args(
        parse("--root", str(root), "--no-allow-write", "--no-gitignore", "--max-read-lines", "9")
    )
    assert settings.allow_write is False
    assert settings.respect_gitignore is False
    assert settings.max_read_lines == 9


def test_multiple_roots_and_extra_deny(root: Path, outside: Path) -> None:
    settings = cli.settings_from_args(
        parse("-r", str(root), "-r", str(outside), "--deny", "*.sqlite", "--deny", "dumps/")
    )
    assert settings.roots == (root, outside)
    assert settings.deny_globs == (*DEFAULT_DENY_GLOBS, "*.sqlite", "dumps/")


def test_deny_negation_re_allows_a_default(root: Path) -> None:
    # --deny patterns follow the defaults and the last matching pattern wins.
    policy = AccessPolicy(
        cli.settings_from_args(parse("-r", str(root), "--deny", "!.env")).deny_globs
    )
    assert not policy.is_denied(".env", is_dir=False)
    assert policy.is_denied(".env.local", is_dir=False)
    again = cli.settings_from_args(parse("-r", str(root), "--deny", "!.env", "--deny", ".env"))
    assert AccessPolicy(again.deny_globs).is_denied(".env", is_dir=False)


def test_invalid_configuration_exits_with_code_2(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["--root", str(root / "missing")]) == cli.EXIT_CONFIG_ERROR
    assert "invalid configuration" in capsys.readouterr().err


def test_main_runs_server_over_stdio(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr("mcp.server.MCPServer.run", lambda self, transport: calls.append(transport))
    assert cli.main(["--root", str(root), "--log-level", "ERROR"]) == 0
    assert calls == ["stdio"]


def test_keyboard_interrupt_exits_cleanly(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def interrupt(self: object, transport: str) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("mcp.server.MCPServer.run", interrupt)
    assert cli.main(["--root", str(root)]) == cli.EXIT_INTERRUPTED


def test_version_flag() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "mcp_file_system", "--version"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert completed.stdout.strip() == f"mcp-file-system {__version__}"


@pytest.mark.anyio
async def test_real_stdio_subprocess(root: Path) -> None:
    write(root / "hello.txt", "hello\nworld\n")
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_file_system", "--root", str(root)],
    )
    async with Client(params) as client:
        names = {tool.name for tool in (await client.list_tools()).tools}
        assert "read_lines" in names
        assert "edit_lines" not in names
        result = await client.call_tool("read_lines", {"path": "hello.txt", "start": -1})
        assert not result.is_error
        assert result.structured_content is not None
        assert result.structured_content["content"] == "2\tworld"


def test_profile_tools_and_output_schema_flags(root: Path) -> None:
    settings = cli.settings_from_args(
        parse(
            "--root",
            str(root),
            "--profile",
            "lite",
            "--tools",
            "grep, read_lines",
            "--output-schema",
        )
    )
    assert settings.profile == "lite"
    assert settings.tools == ("grep", "read_lines")
    assert settings.emit_output_schema is True


def test_unknown_tool_exits_with_code_2(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--root", str(root), "--tools", "nope"]) == cli.EXIT_CONFIG_ERROR
    assert "Unknown tool(s)" in capsys.readouterr().err


def test_every_option_has_help_text() -> None:
    """Operators read --help first; an option without text hides its default and meaning."""
    parser = cli.build_parser()
    missing = [action.option_strings[0] for action in parser._actions if not action.help]
    assert missing == []


def test_help_explains_deny_precedence() -> None:
    # CHANGELOG: the --deny help says that the last matching pattern wins.
    assert "matching pattern wins" in cli.build_parser().format_help()


def test_help_names_the_real_environment_variables() -> None:
    # Env names follow the settings, not the flags (--root -> MCP_FS_ROOTS).
    text = cli.build_parser().format_help()
    assert "MCP_FS_ROOTS" in text
    assert "MCP_FS_DENY_GLOBS" in text
    assert "MCP_FS_<NAME>" not in text
