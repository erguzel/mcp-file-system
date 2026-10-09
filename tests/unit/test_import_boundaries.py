"""Only the MCP adapter layer may import the ``mcp`` SDK (AGENTS.md, hard rule 3)."""

import ast
from pathlib import Path

import mcp_file_system

PACKAGE = Path(mcp_file_system.__file__).parent
MCP_IMPORTERS_ALLOWED = ("tools/", "server.py", "cli.py")


def imports_mcp(source: str) -> bool:
    """Whether ``source`` imports ``mcp`` anywhere, including in functions and type blocks."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module]
        else:
            continue
        if any(name == "mcp" or name.startswith("mcp.") for name in names):
            return True
    return False


def test_only_adapters_import_mcp() -> None:
    offenders = [
        relative
        for path in sorted(PACKAGE.rglob("*.py"))
        if not (relative := path.relative_to(PACKAGE).as_posix()).startswith(MCP_IMPORTERS_ALLOWED)
        and imports_mcp(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_detector_finds_every_import_form() -> None:
    assert imports_mcp("import mcp\n")
    assert imports_mcp("from mcp.types import CallToolResult\n")
    assert imports_mcp("def f():\n    from mcp.server import MCPServer\n")
    assert imports_mcp("if TYPE_CHECKING:\n    import mcp.types\n")


def test_detector_ignores_lookalikes() -> None:
    assert not imports_mcp("from mcp_file_system import errors\n")
    assert not imports_mcp("import mcpx\n")
    assert not imports_mcp("from . import mcp\n")
