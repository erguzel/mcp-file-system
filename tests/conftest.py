"""Shared fixtures: an isolated workspace root and factories for settings and services."""

import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from mcp.client import Client
from mcp.types import Tool

from mcp_file_system.config import Settings
from mcp_file_system.context import AppContext
from mcp_file_system.server import create_server

SettingsFactory = Callable[..., Settings]
ContextFactory = Callable[..., AppContext]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """An empty workspace directory, resolved (tmp dirs are symlinked on macOS)."""
    path = tmp_path / "root"
    path.mkdir()
    return path.resolve()


@pytest.fixture
def outside(tmp_path: Path) -> Path:
    """A directory next to the root that must never be reachable."""
    path = tmp_path / "outside"
    path.mkdir()
    (path / "secret.txt").write_text("top secret\n")
    return path.resolve()


@pytest.fixture
def make_settings(root: Path) -> SettingsFactory:
    def factory(**overrides: Any) -> Settings:
        overrides.setdefault("roots", (root,))
        return Settings(**overrides)

    return factory


@pytest.fixture
def make_ctx(make_settings: SettingsFactory) -> ContextFactory:
    def factory(**overrides: Any) -> AppContext:
        return AppContext.from_settings(make_settings(**overrides))

    return factory


@pytest.fixture
def ctx(make_ctx: ContextFactory) -> AppContext:
    return make_ctx()


async def list_tools(make_settings: SettingsFactory, **overrides: Any) -> dict[str, Tool]:
    """The tools a server built with these settings lists, by name."""
    async with Client(create_server(make_settings(**overrides))) as client:
        return {tool.name: tool for tool in (await client.list_tools()).tools}


def write(path: Path, content: str | bytes) -> Path:
    """Create ``path`` (and parents) with exact bytes; str is encoded as UTF-8 verbatim."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content.encode() if isinstance(content, str) else content)
    return path


def symlink_or_skip(link: Path, target: Path) -> None:
    """Create a symlink, skipping the test where that is not permitted (e.g. Windows)."""
    try:
        link.symlink_to(target, target_is_directory=target.is_dir())
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")


def junction_or_skip(link: Path, target: Path) -> None:
    """Create a Windows directory junction, skipping the test on other platforms.

    Junctions need no privileges, so on Windows any failure is a real error and
    propagates instead of turning a security test into a silent skip.
    """
    if sys.platform == "win32":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        pytest.skip("directory junctions exist only on Windows")


def numbered(count: int, prefix: str = "line") -> str:
    """``count`` LF-terminated lines: 'line 1', 'line 2', ..."""
    return "".join(f"{prefix} {n}\n" for n in range(1, count + 1))
