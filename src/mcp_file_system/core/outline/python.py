"""Python outlines via the standard-library :mod:`ast` module."""

import ast
from collections.abc import Sequence

from mcp_file_system.core.models import Symbol
from mcp_file_system.errors import ParseError

MAX_SIGNATURE_CHARS = 200

_Definition = ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef


class PythonOutlineProvider:
    """Lists classes, functions and methods with signatures and line spans.

    Spans include decorators. Definitions nested inside functions are omitted to keep
    outlines short; definitions inside module- or class-level ``if``/``try`` blocks
    (e.g. ``if TYPE_CHECKING:``) are included.
    """

    language = "python"
    extensions = frozenset({".py", ".pyi", ".pyw"})

    def outline(self, text: str) -> list[Symbol]:
        """Parse ``text`` and return its definitions in source order."""
        try:
            tree = ast.parse(text)
        except SyntaxError as exc:
            raise ParseError(f"syntax error at line {exc.lineno}: {exc.msg}") from None
        except ValueError as exc:  # e.g. NUL bytes in a Latin-1 decoded file
            raise ParseError(str(exc)) from None
        symbols: list[Symbol] = []
        self._visit(tree.body, prefix="", depth=0, in_class=False, out=symbols)
        return symbols

    def _visit(
        self,
        body: Sequence[ast.stmt],
        *,
        prefix: str,
        depth: int,
        in_class: bool,
        out: list[Symbol],
    ) -> None:
        for node in body:
            if isinstance(node, _Definition):
                qualified = f"{prefix}{node.name}"
                out.append(self._symbol(node, qualified, depth, in_class))
                if isinstance(node, ast.ClassDef):
                    self._visit(
                        node.body, prefix=f"{qualified}.", depth=depth + 1, in_class=True, out=out
                    )
            elif isinstance(node, ast.If):
                for block in (node.body, node.orelse):
                    self._visit(block, prefix=prefix, depth=depth, in_class=in_class, out=out)
            elif isinstance(node, ast.Try | ast.TryStar):
                blocks = [node.body, node.orelse, node.finalbody]
                blocks.extend(handler.body for handler in node.handlers)
                for block in blocks:
                    self._visit(block, prefix=prefix, depth=depth, in_class=in_class, out=out)

    @staticmethod
    def _symbol(node: _Definition, qualified: str, depth: int, in_class: bool) -> Symbol:
        start = min([node.lineno, *(d.lineno for d in node.decorator_list)])
        if isinstance(node, ast.ClassDef):
            kind = "class"
        elif in_class:
            kind = "method"
        else:
            kind = "function"
        return Symbol(
            name=node.name,
            qualified_name=qualified,
            kind=kind,
            start_line=start,
            end_line=node.end_lineno or node.lineno,
            depth=depth,
            signature=_signature(node),
        )


def _signature(node: _Definition) -> str:
    type_params = getattr(node, "type_params", None) or []
    generics = f"[{', '.join(ast.unparse(p) for p in type_params)}]" if type_params else ""
    if isinstance(node, ast.ClassDef):
        bases = [ast.unparse(b) for b in node.bases]
        bases += [ast.unparse(k) for k in node.keywords]
        suffix = f"({', '.join(bases)})" if bases else ""
        signature = f"class {node.name}{generics}{suffix}"
    else:
        prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
        returns = f" -> {ast.unparse(node.returns)}" if node.returns else ""
        signature = f"{prefix} {node.name}{generics}({ast.unparse(node.args)}){returns}"
    if len(signature) > MAX_SIGNATURE_CHARS:
        signature = signature[: MAX_SIGNATURE_CHARS - 1] + "…"
    return signature
