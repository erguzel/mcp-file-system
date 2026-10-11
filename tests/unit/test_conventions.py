"""Conventions from AGENTS.md and docs/ARCHITECTURE.md that can be checked statically.

Each test names the rule it enforces. They read source files with ``ast`` or the docs as
text, so a violation fails here instead of surfacing in a later documentation audit.
"""

import ast
import importlib
import re
from pathlib import Path

import mcp_file_system

PACKAGE = Path(mcp_file_system.__file__).parent
REPO = PACKAGE.parent.parent
TESTS = REPO / "tests"

# Tool parameters whose ``0`` default is a real value, not "unset" (AGENTS.md, nullable rule).
ZERO_IS_A_VALUE = {"context", "context_before", "context_after"}
# Tool parameters forwarded as ``""`` because the service itself treats ``""`` as unset.
EMPTY_PASSES_THROUGH = {"if_changed_since", "key_path"}
# Filesystem helpers tests must not call directly (AGENTS.md, test conventions).
DIRECT_FS_CALLS = {"write_text", "write_bytes", "symlink_to", "symlink", "CreateJunction"}


def _modules(root: Path) -> dict[str, ast.Module]:
    return {
        path.relative_to(root).as_posix(): ast.parse(path.read_text(encoding="utf-8"))
        for path in sorted(root.rglob("*.py"))
    }


def test_src_never_uses_str_splitlines() -> None:
    """AGENTS.md "Lines": use core/textio helpers; ``str.splitlines`` splits on more."""
    offenders = [
        name
        for name, tree in _modules(PACKAGE).items()
        if any(
            isinstance(node, ast.Attribute) and node.attr == "splitlines" for node in ast.walk(tree)
        )
    ]
    assert offenders == []


def test_tests_create_files_and_links_only_through_conftest() -> None:
    """AGENTS.md test conventions: ``write``, ``symlink_or_skip``, ``junction_or_skip``."""
    offenders = [
        f"{name}:{node.lineno} .{node.attr}"
        for name, tree in _modules(TESTS).items()
        if name != "conftest.py"
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr in DIRECT_FS_CALLS
    ]
    assert offenders == []


def _sentinel_parameters(function: ast.FunctionDef) -> list[tuple[str, str]]:
    """Parameters defaulting to ``""`` or ``0`` and the helper that must map them."""
    args = function.args
    first_default = len(args.args) - len(args.defaults)
    positional = list(zip(args.args[first_default:], args.defaults, strict=True))
    keyword = [(a, d) for a, d in zip(args.kwonlyargs, args.kw_defaults, strict=True) if d]
    found = []
    for arg, default in positional + keyword:
        if not isinstance(default, ast.Constant):
            continue
        if default.value == "" and isinstance(default.value, str):
            found.append((arg.arg, "unset_str"))
        elif type(default.value) is int and default.value == 0:
            found.append((arg.arg, "unset_int"))
    return found


def _mapped_with(function: ast.FunctionDef, helper: str) -> set[str]:
    return {
        node.args[0].id
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == helper
        and node.args
        and isinstance(node.args[0], ast.Name)
    }


def test_sentinel_defaults_are_mapped_with_unset_helpers() -> None:
    """AGENTS.md nullable rule: ``""`` and ``0`` mean unset and go through ``unset_*``."""
    offenders = []
    for name, tree in _modules(PACKAGE / "tools").items():
        for function in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)):
            for parameter, helper in _sentinel_parameters(function):
                if helper == "unset_int" and parameter in ZERO_IS_A_VALUE:
                    continue
                if helper == "unset_str" and parameter in EMPTY_PASSES_THROUGH:
                    continue
                if parameter not in _mapped_with(function, helper):
                    offenders.append(f"{name}:{function.name}({parameter}) needs {helper}")
    assert offenders == []


def _source_modules() -> set[str]:
    """Dotted names of the package's modules, without packages, ``__main__`` and private ones."""
    found = set()
    for path in PACKAGE.rglob("*.py"):
        parts = path.relative_to(PACKAGE).with_suffix("").parts
        if parts[-1].startswith("_") or any(p.startswith("_") for p in parts[:-1]):
            continue
        found.add(".".join(parts))
    return found


def _resolve_row(key: str) -> str:
    """Module a docs/ARCHITECTURE.md row describes: a module path or a symbol inside one."""
    dotted = key.removesuffix("/*").replace("/", ".")
    try:
        return importlib.import_module(f"{mcp_file_system.__name__}.{dotted}").__name__
    except ImportError:
        parent, _, symbol = dotted.rpartition(".")
        module = importlib.import_module(f"{mcp_file_system.__name__}.{parent}")
        return str(getattr(module, symbol).__module__)


def test_architecture_table_covers_every_module() -> None:
    """AGENTS.md definition of done: every module has a row in the ARCHITECTURE table."""
    text = (REPO / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")
    prefix = mcp_file_system.__name__ + "."
    described = {
        _resolve_row(key).removeprefix(prefix) for key in re.findall(r"^\| `([^`]+)`", text, re.M)
    }
    uncovered = sorted(
        module
        for module in _source_modules()
        if not any(module == row or module.startswith(row + ".") for row in described)
    )
    assert uncovered == []


def test_agents_project_map_names_every_top_level_entry() -> None:
    """AGENTS.md definition of done: the project map names every top-level file and package."""
    text = (REPO / "AGENTS.md").read_text(encoding="utf-8")
    entries = [
        path.name
        for path in PACKAGE.iterdir()
        if path.name not in ("__init__.py", "py.typed", "__pycache__")
    ]
    assert [name for name in entries if name not in text] == []
