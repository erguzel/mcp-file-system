"""Shell script outlines (bash and POSIX sh) with the tree-sitter Bash grammar."""

import tree_sitter_bash
from tree_sitter import Node

from mcp_file_system.core.models import Symbol
from mcp_file_system.core.outline.treesitter._base import (
    TreeSitterOutlineProvider,
    block_folds,
    end_line,
    node_text,
    start_line,
)


class ShellOutlineProvider(TreeSitterOutlineProvider):
    """Lists shell functions (``name() {...}`` and ``function name {...}``).

    Functions defined inside other functions or inside ``if``/``case`` blocks are
    included; ``depth`` reflects nesting inside other functions. Shell functions share
    one global namespace, so qualified names are the plain function names; conditional
    redefinitions share a name and ``read_symbol`` returns them together.
    """

    language = "shell"
    extensions = frozenset({".sh", ".bash", ".zsh", ".ksh"})
    interpreters = frozenset({"sh", "bash", "zsh", "ksh", "dash"})
    masks_wide_chars = True

    def _grammar(self) -> object:
        return tree_sitter_bash.language()

    def _extract(self, root: Node, source: bytes) -> list[Symbol]:
        symbols: list[Symbol] = []
        _collect(root, source, 0, symbols)
        return symbols

    def fold_ranges(self, text: str) -> list[tuple[int, int]]:
        """Function bodies to fold in a skeleton view."""
        root, _ = self._parse(text)
        return block_folds(root, frozenset({"function_definition"}))


def _collect(node: Node, source: bytes, depth: int, out: list[Symbol]) -> None:
    for child in node.named_children:
        if child.type == "function_definition":
            name_node = child.child_by_field_name("name")
            if name_node is not None:
                name = node_text(name_node, source)
                out.append(
                    Symbol(
                        name=name,
                        qualified_name=name,
                        kind="function",
                        start_line=start_line(child),
                        end_line=end_line(child),
                        depth=depth,
                        signature=f"{name}()",
                    )
                )
            _collect(child, source, depth + 1, out)
        else:
            _collect(child, source, depth, out)
