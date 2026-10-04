"""Outlines for programming languages in the optional ``[code]`` extra.

JavaScript, TypeScript (incl. TSX), Go and Java. Each grammar ships as its own small,
pinned package (``pip install "mcp-file-system[code]"``); if one is missing, its
provider cannot be constructed and the registry skips it.

Like the Python provider, outlines list top-level definitions and class members, but
not functions nested inside function bodies, which keeps outlines short.
"""

import importlib
import re
from collections.abc import Iterable

from tree_sitter import Node

from mcp_file_system.core.models import Symbol
from mcp_file_system.core.outline.treesitter._base import (
    TreeSitterOutlineProvider,
    block_folds,
    end_line,
    node_text,
    start_line,
)

MAX_SIGNATURE_CHARS = 200
_ANNOTATIONS = re.compile(r"^(?:@[\w.]+(?:\([^)]*\))?\s*)+")


class CodeOutlineProvider(TreeSitterOutlineProvider):
    """Base for grammar packages that expose ``<module>.<function>()``."""

    module: str
    function = "language"
    function_types: frozenset[str] = frozenset()
    """Node types whose ``body`` is folded in skeleton views."""

    def _grammar(self) -> object:
        return getattr(importlib.import_module(self.module), self.function)()

    def fold_ranges(self, text: str) -> list[tuple[int, int]]:
        """Function and method bodies to fold in a skeleton view."""
        root, _ = self._parse(text)
        return block_folds(root, self.function_types)

    def _extract(self, root: Node, source: bytes) -> list[Symbol]:
        out: list[Symbol] = []
        self._visit(root, "", 0, source, out)
        return out

    def _visit(self, node: Node, prefix: str, depth: int, source: bytes, out: list[Symbol]) -> None:
        raise NotImplementedError

    @staticmethod
    def _emit(
        out: list[Symbol],
        *,
        name: str,
        prefix: str,
        kind: str,
        node: Node,
        span: Node,
        depth: int,
        source: bytes,
        body: Node | None = None,
        signature: str | None = None,
    ) -> str:
        qualified = f"{prefix}{name}"
        out.append(
            Symbol(
                name=name,
                qualified_name=qualified,
                kind=kind,
                start_line=start_line(span),
                end_line=end_line(span),
                depth=depth,
                signature=signature or _header(node, source, body),
            )
        )
        return qualified


def _header(node: Node, source: bytes, body: Node | None) -> str:
    """Declaration text up to its body, without annotations, on one line."""
    end = body.start_byte if body is not None else node.end_byte
    text = source[node.start_byte : end].decode("utf-8", errors="replace")
    text = _ANNOTATIONS.sub("", " ".join(text.split())).rstrip(" {=>;").strip()
    if len(text) > MAX_SIGNATURE_CHARS:
        text = text[: MAX_SIGNATURE_CHARS - 1] + "…"
    return text


def _name(node: Node, source: bytes) -> str | None:
    name = node.child_by_field_name("name")
    return node_text(name, source) if name is not None else None


# ------------------------------------------------------------- JavaScript / TypeScript

_JS_DECLARATIONS = {
    "function_declaration": "function",
    "generator_function_declaration": "function",
    "class_declaration": "class",
    "abstract_class_declaration": "class",
    "interface_declaration": "interface",
    "type_alias_declaration": "type",
    "enum_declaration": "enum",
}
_JS_CLASSES = {"class_declaration", "abstract_class_declaration", "interface_declaration"}
_JS_MEMBERS = {"method_definition", "abstract_method_signature", "method_signature"}
_JS_FUNCTION_VALUES = {"arrow_function", "function_expression", "generator_function", "function"}
_JS_NAMESPACES = {"internal_module", "module"}


class JavaScriptOutlineProvider(CodeOutlineProvider):
    """Functions, classes and their methods, and function-valued ``const``/``let``."""

    language = "javascript"
    extensions = frozenset({".js", ".mjs", ".cjs", ".jsx"})
    module = "tree_sitter_javascript"
    function_types = frozenset(
        {
            "function_declaration",
            "generator_function_declaration",
            "method_definition",
            "function_expression",
            "arrow_function",
            "generator_function",
            "function",
        }
    )

    def _visit(self, node: Node, prefix: str, depth: int, source: bytes, out: list[Symbol]) -> None:
        for child in node.named_children:
            target, span = child, child
            if child.type == "export_statement":
                declaration = child.child_by_field_name("declaration")
                if declaration is None:
                    continue
                target = declaration
            elif child.type == "expression_statement" and child.named_child_count:
                target = child.named_children[0]
            self._declaration(target, span, prefix, depth, source, out)

    def _declaration(
        self, target: Node, span: Node, prefix: str, depth: int, source: bytes, out: list[Symbol]
    ) -> None:
        if target.type in _JS_DECLARATIONS:
            name = _name(target, source)
            if name is None:
                return
            body = target.child_by_field_name("body")
            qualified = self._emit(
                out,
                name=name,
                prefix=prefix,
                kind=_JS_DECLARATIONS[target.type],
                node=target,
                span=span,
                depth=depth,
                source=source,
                body=body,
            )
            if target.type in _JS_CLASSES and body is not None:
                self._members(body.named_children, f"{qualified}.", depth + 1, source, out)
        elif target.type in {"lexical_declaration", "variable_declaration"}:
            declarators = [c for c in target.named_children if c.type == "variable_declarator"]
            for declarator in declarators:
                value = declarator.child_by_field_name("value")
                name = _name(declarator, source)
                if value is None or name is None:
                    continue
                if value.type in _JS_FUNCTION_VALUES:
                    kind, body = "function", value.child_by_field_name("body")
                elif value.type == "class":
                    kind, body = "class", value.child_by_field_name("body")
                else:
                    continue
                self._emit(
                    out,
                    name=name,
                    prefix=prefix,
                    kind=kind,
                    node=target if len(declarators) == 1 else declarator,
                    span=span if len(declarators) == 1 else declarator,
                    depth=depth,
                    source=source,
                    body=body,
                )
        elif target.type in _JS_NAMESPACES:
            name = _name(target, source)
            body = target.child_by_field_name("body")
            if name is None:
                return
            qualified = self._emit(
                out,
                name=name,
                prefix=prefix,
                kind="namespace",
                node=target,
                span=span,
                depth=depth,
                source=source,
                body=body,
            )
            if body is not None:
                self._visit(body, f"{qualified}.", depth + 1, source, out)

    def _members(
        self, members: Iterable[Node], prefix: str, depth: int, source: bytes, out: list[Symbol]
    ) -> None:
        for member in members:
            if member.type in _JS_MEMBERS and (name := _name(member, source)) is not None:
                self._emit(
                    out,
                    name=name,
                    prefix=prefix,
                    kind="method",
                    node=member,
                    span=member,
                    depth=depth,
                    source=source,
                    body=member.child_by_field_name("body"),
                )


class TypeScriptOutlineProvider(JavaScriptOutlineProvider):
    """JavaScript symbols plus interfaces, type aliases, enums and namespaces."""

    language = "typescript"
    extensions = frozenset({".ts", ".mts", ".cts"})
    module = "tree_sitter_typescript"
    function = "language_typescript"


class TsxOutlineProvider(JavaScriptOutlineProvider):
    """TypeScript with JSX."""

    language = "tsx"
    extensions = frozenset({".tsx"})
    module = "tree_sitter_typescript"
    function = "language_tsx"


# ------------------------------------------------------------------------------ Go

_GO_TYPE_KINDS = {"struct_type": "struct", "interface_type": "interface"}


class GoOutlineProvider(CodeOutlineProvider):
    """Functions, methods (qualified by receiver type: ``Server.Handle``) and types."""

    language = "go"
    extensions = frozenset({".go"})
    module = "tree_sitter_go"
    function_types = frozenset({"function_declaration", "method_declaration", "func_literal"})

    def _visit(self, node: Node, prefix: str, depth: int, source: bytes, out: list[Symbol]) -> None:
        for child in node.named_children:
            if child.type == "function_declaration" and (name := _name(child, source)):
                self._emit(
                    out,
                    name=name,
                    prefix="",
                    kind="function",
                    node=child,
                    span=child,
                    depth=0,
                    source=source,
                    body=child.child_by_field_name("body"),
                )
            elif child.type == "method_declaration" and (name := _name(child, source)):
                receiver = _go_receiver_type(child, source)
                self._emit(
                    out,
                    name=name,
                    prefix=f"{receiver}." if receiver else "",
                    kind="method",
                    node=child,
                    span=child,
                    depth=0,
                    source=source,
                    body=child.child_by_field_name("body"),
                )
            elif child.type == "type_declaration":
                for spec in child.named_children:
                    if spec.type not in {"type_spec", "type_alias"}:
                        continue
                    name = _name(spec, source)
                    type_node = spec.child_by_field_name("type")
                    if name is None:
                        continue
                    kind = _GO_TYPE_KINDS.get(type_node.type if type_node else "", "type")
                    single = len([s for s in child.named_children if s.type != "comment"]) == 1
                    is_body = kind in {"struct", "interface"}
                    header = _header(spec, source, type_node if is_body else None)
                    self._emit(
                        out,
                        name=name,
                        prefix="",
                        kind=kind,
                        node=spec,
                        span=child if single else spec,
                        depth=0,
                        source=source,
                        signature=f"type {header} {kind}" if is_body else f"type {header}",
                    )


def _go_receiver_type(method: Node, source: bytes) -> str | None:
    receiver = method.child_by_field_name("receiver")
    if receiver is None:
        return None
    stack = [receiver]
    while stack:
        node = stack.pop(0)
        if node.type == "type_identifier":
            return node_text(node, source)
        stack.extend(node.named_children)
    return None


# ---------------------------------------------------------------------------- Java

_JAVA_TYPES = {
    "class_declaration": "class",
    "interface_declaration": "interface",
    "enum_declaration": "enum",
    "record_declaration": "record",
    "annotation_type_declaration": "annotation",
}
_JAVA_MEMBERS = {"method_declaration": "method", "constructor_declaration": "constructor"}
_JAVA_BODIES = {"class_body", "interface_body", "enum_body", "enum_body_declarations"}


class JavaOutlineProvider(CodeOutlineProvider):
    """Types (incl. nested), methods and constructors: ``Outer.Inner.method``."""

    language = "java"
    extensions = frozenset({".java"})
    module = "tree_sitter_java"
    function_types = frozenset(
        {"method_declaration", "constructor_declaration", "lambda_expression"}
    )

    def _visit(self, node: Node, prefix: str, depth: int, source: bytes, out: list[Symbol]) -> None:
        for child in node.named_children:
            if child.type in _JAVA_BODIES:
                self._visit(child, prefix, depth, source, out)
                continue
            kind = _JAVA_TYPES.get(child.type) or _JAVA_MEMBERS.get(child.type)
            name = _name(child, source) if kind else None
            if kind is None or name is None:
                continue
            body = child.child_by_field_name("body")
            qualified = self._emit(
                out,
                name=name,
                prefix=prefix,
                kind=kind,
                node=child,
                span=child,
                depth=depth,
                source=source,
                body=body,
            )
            if child.type in _JAVA_TYPES and body is not None:
                self._visit(body, f"{qualified}.", depth + 1, source, out)
