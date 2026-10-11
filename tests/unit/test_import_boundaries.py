"""Import boundaries: only adapters import ``mcp``, and layers depend downwards only.

The first rule is AGENTS.md hard rule 3. The second is the layer diagram in
``docs/ARCHITECTURE.md``: ``config``, ``errors`` and ``audit`` are leaves, ``security``
sits above ``errors``, ``core`` above those, ``context`` wires the services, and ``tools``,
``server`` and ``cli`` form the adapter layer on top.
"""

import ast
from pathlib import Path

import mcp_file_system

PACKAGE = Path(mcp_file_system.__file__).parent
PACKAGE_NAME = mcp_file_system.__name__
MCP_IMPORTERS_ALLOWED = ("tools/", "server.py", "cli.py")

# Top-level modules of the package each layer may import, besides itself. ``__init__``
# stands for the package root (``from mcp_file_system import __version__``).
LAYER_IMPORTS_ALLOWED: dict[str, frozenset[str]] = {
    "__init__": frozenset(),
    "config": frozenset(),
    "errors": frozenset(),
    "audit": frozenset(),
    "security": frozenset({"errors"}),
    "core": frozenset({"config", "errors", "security"}),
    "context": frozenset({"audit", "config", "core", "security"}),
    "tools": frozenset({"audit", "config", "context", "core", "errors"}),
    "server": frozenset({"__init__", "audit", "config", "context", "tools"}),
    "cli": frozenset({"__init__", "config", "server"}),
    "__main__": frozenset({"cli"}),
}


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


def _layer(dotted: str, names: list[str]) -> set[str]:
    """Layers that ``from <dotted> import <names>`` touches, given an absolute module name."""
    if dotted == PACKAGE_NAME:
        # ``from mcp_file_system import x``: x is a submodule or an attribute of the root.
        return {
            name if (PACKAGE / name).exists() or (PACKAGE / f"{name}.py").exists() else "__init__"
            for name in names
        }
    if dotted.startswith(PACKAGE_NAME + "."):
        return {dotted.split(".")[1]}
    return set()


def package_imports(source: str, module: str, is_package: bool) -> set[str]:
    """Layers that ``source`` imports from the package.

    ``module`` is the dotted name relative to the package (``""`` for its root) and
    ``is_package`` tells whether the file is an ``__init__.py``, which relative imports
    resolve against differently.
    """
    base = module.split(".") if module else []
    if not is_package:
        base = base[:-1]
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found |= _layer(alias.name, [])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                dotted = node.module or ""
            else:
                anchor = base[: len(base) - (node.level - 1)]
                dotted = ".".join([PACKAGE_NAME, *anchor, *([node.module] if node.module else [])])
            found |= _layer(dotted, [alias.name for alias in node.names])
    return found


def test_only_adapters_import_mcp() -> None:
    offenders = [
        relative
        for path in sorted(PACKAGE.rglob("*.py"))
        if not (relative := path.relative_to(PACKAGE).as_posix()).startswith(MCP_IMPORTERS_ALLOWED)
        and imports_mcp(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_layers_only_import_downwards() -> None:
    offenders: list[str] = []
    for path in sorted(PACKAGE.rglob("*.py")):
        parts = path.relative_to(PACKAGE).with_suffix("").parts
        is_package = parts[-1] == "__init__"
        module = ".".join(parts[:-1] if is_package else parts)
        layer = parts[0]
        allowed = LAYER_IMPORTS_ALLOWED[layer] | {layer}
        imported = package_imports(path.read_text(encoding="utf-8"), module, is_package)
        offenders.extend(f"{'/'.join(parts)} -> {name}" for name in sorted(imported - allowed))
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


def test_layer_detector_resolves_absolute_and_relative_imports() -> None:
    assert package_imports("import mcp_file_system.core.reader\n", "tools.read", False) == {"core"}
    assert package_imports("from mcp_file_system.security import PathGuard\n", "core.x", False) == {
        "security"
    }
    assert package_imports("from mcp_file_system import errors, __version__\n", "cli", False) == {
        "errors",
        "__init__",
    }
    assert package_imports("def f():\n    from mcp_file_system import audit\n", "cli", False) == {
        "audit"
    }
    # Relative imports resolve against the file's own package.
    assert package_imports("from . import base\n", "core.outline.service", False) == {"core"}
    assert package_imports("from .. import skeleton\n", "core.outline.service", False) == {"core"}
    assert package_imports("from ... import errors\n", "core.outline.service", False) == {"errors"}
    assert package_imports("from .security import paths\n", "", True) == {"security"}
    assert package_imports("from ...errors import FsError\n", "core.outline", True) == {"errors"}
    assert package_imports("import os\nfrom pathlib import Path\n", "core.x", False) == set()
