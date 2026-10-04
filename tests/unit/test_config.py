from pathlib import Path

import pytest
from pydantic import ValidationError

from conftest import write
from mcp_file_system.config import DEFAULT_DENY_GLOBS, Settings


def test_defaults(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    monkeypatch.chdir(root)
    settings = Settings()
    assert settings.roots == (root,)
    assert settings.primary_root == root
    assert settings.allow_write is False
    assert settings.respect_gitignore is True
    assert settings.deny_globs == DEFAULT_DENY_GLOBS


def test_roots_are_resolved_and_deduplicated(root: Path) -> None:
    (root / "sub").mkdir()
    settings = Settings(roots=(root / "sub" / "..", root, root / "sub"))
    assert settings.roots == (root, root / "sub")


def test_missing_root_is_rejected(root: Path) -> None:
    with pytest.raises(ValidationError, match="root does not exist"):
        Settings(roots=(root / "missing",))


def test_file_root_is_rejected(root: Path) -> None:
    write(root / "f.txt", "x")
    with pytest.raises(ValidationError, match="not a directory"):
        Settings(roots=(root / "f.txt",))


def test_empty_roots_are_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(roots=())


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_read_lines", 0),
        ("max_output_chars", 10),
        ("search_timeout_s", 0),
        ("log_level", "LOUD"),
    ],
)
def test_invalid_limits(root: Path, field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({"roots": (root,), field: value})


def test_environment_variables(monkeypatch: pytest.MonkeyPatch, root: Path, outside: Path) -> None:
    monkeypatch.setenv("MCP_FS_ROOTS", f'["{root.as_posix()}", "{outside.as_posix()}"]')
    monkeypatch.setenv("MCP_FS_ALLOW_WRITE", "true")
    monkeypatch.setenv("MCP_FS_MAX_READ_LINES", "42")
    settings = Settings()
    assert settings.roots == (root, outside)
    assert settings.allow_write is True
    assert settings.max_read_lines == 42


def test_explicit_arguments_override_environment(
    monkeypatch: pytest.MonkeyPatch, root: Path
) -> None:
    monkeypatch.setenv("MCP_FS_MAX_READ_LINES", "42")
    assert Settings(roots=(root,), max_read_lines=7).max_read_lines == 7


def test_settings_are_immutable_and_strict(root: Path) -> None:
    settings = Settings(roots=(root,))
    with pytest.raises(ValidationError):
        settings.allow_write = True  # type: ignore[misc]
    with pytest.raises(ValidationError):
        Settings(roots=(root,), unknown_option=1)  # type: ignore[call-arg]


def test_tools_accept_comma_separated_environment_value(
    monkeypatch: pytest.MonkeyPatch, root: Path
) -> None:
    monkeypatch.setenv("MCP_FS_TOOLS", "grep, read_lines,,outline")
    assert Settings(roots=(root,)).tools == ("grep", "read_lines", "outline")


def test_tools_accept_json_list_and_sequences(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    assert Settings.model_validate({"roots": (root,), "tools": ["grep"]}).tools == ("grep",)
    monkeypatch.setenv("MCP_FS_PROFILE", "lite")
    assert Settings(roots=(root,)).profile == "lite"


@pytest.mark.parametrize(
    ("profile", "output_schema", "expected"),
    [
        ("full", None, True),
        ("lite", None, False),
        ("full", False, False),
        ("lite", True, True),
    ],
)
def test_output_schema_defaults_follow_the_profile(
    root: Path, profile: str, output_schema: bool | None, expected: bool
) -> None:
    settings = Settings.model_validate(
        {"roots": (root,), "profile": profile, "output_schema": output_schema}
    )
    assert settings.emit_output_schema is expected


def test_unknown_profile_is_rejected(root: Path) -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({"roots": (root,), "profile": "tiny"})
