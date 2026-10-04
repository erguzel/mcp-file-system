"""Outlines for the optional [code] extra: JavaScript, TypeScript, TSX, Go and Java."""

import logging
from pathlib import Path

import pytest

from conftest import write
from mcp_file_system.context import AppContext
from mcp_file_system.core.models import Symbol
from mcp_file_system.core.outline import registry as registry_module
from mcp_file_system.core.outline.registry import default_registry
from mcp_file_system.errors import InvalidArgumentError

pytest.importorskip("tree_sitter_javascript")
pytest.importorskip("tree_sitter_typescript")
pytest.importorskip("tree_sitter_go")
pytest.importorskip("tree_sitter_java")

from mcp_file_system.core.outline.treesitter.code import (
    GoOutlineProvider,
    JavaOutlineProvider,
    JavaScriptOutlineProvider,
    TsxOutlineProvider,
    TypeScriptOutlineProvider,
)


def rows(symbols: list[Symbol]) -> list[tuple[str, str, int, int, int, str | None]]:
    return [
        (s.qualified_name, s.kind, s.start_line, s.end_line, s.depth, s.signature) for s in symbols
    ]


JS = """\
export function a(x) {}
export default class B extends C {
  m() {}
  static async *gen() {}
  #priv() {}
}
const f = () => 1, g = function() {};
const h = async (req, res) => {
  return 1;
};
function* gen2() {}
export default function () {}
const notAFunction = 42;
"""

TS = """\
@dec
export abstract class A<T> implements I {
  constructor(private x: T) {}
  abstract m(): void;
  get y(): number { return 1 }
}
export interface I { m(): void; n(a: string): number }
type T = string | number;
export enum E { A, B }
namespace N { export function f(): void {} }
"""

GO = """\
package m

// F does things.
func F(a int) (string, error) { return "", nil }

func (s *Server[T]) Handle(w http.ResponseWriter) error { return nil }

type Server[T any] struct{ x int }

type (
	Handler interface{ Handle() error }
	ID = string
)
"""

JAVA = """\
@Service
public class A {
  public A() {}
  @Override public void m(int x) {}
  static class Inner { void n() {} }
  enum Color { RED; void paint() {} }
}
interface I { void m(); }
record P(int x) { int twice() { return 2 * x; } }
"""


def test_javascript() -> None:
    assert rows(JavaScriptOutlineProvider().outline(JS)) == [
        ("a", "function", 1, 1, 0, "function a(x)"),
        ("B", "class", 2, 6, 0, "class B extends C"),
        ("B.m", "method", 3, 3, 1, "m()"),
        ("B.gen", "method", 4, 4, 1, "static async *gen()"),
        ("B.#priv", "method", 5, 5, 1, "#priv()"),
        ("f", "function", 7, 7, 0, "f = ()"),
        ("g", "function", 7, 7, 0, "g = function()"),
        ("h", "function", 8, 10, 0, "const h = async (req, res)"),
        ("gen2", "function", 11, 11, 0, "function* gen2()"),
    ]


def test_typescript_includes_decorators_in_the_span() -> None:
    assert rows(TypeScriptOutlineProvider().outline(TS)) == [
        ("A", "class", 1, 6, 0, "abstract class A<T> implements I"),
        ("A.constructor", "method", 3, 3, 1, "constructor(private x: T)"),
        ("A.m", "method", 4, 4, 1, "abstract m(): void"),
        ("A.y", "method", 5, 5, 1, "get y(): number"),
        ("I", "interface", 7, 7, 0, "interface I"),
        ("I.m", "method", 7, 7, 1, "m(): void"),
        ("I.n", "method", 7, 7, 1, "n(a: string): number"),
        ("T", "type", 8, 8, 0, "type T = string | number"),
        ("E", "enum", 9, 9, 0, "enum E"),
        ("N", "namespace", 10, 10, 0, "namespace N"),
        ("N.f", "function", 10, 10, 1, "function f(): void"),
    ]


def test_tsx() -> None:
    text = "export const Button = ({label}: Props) => <button>{label}</button>;\n"
    assert rows(TsxOutlineProvider().outline(text)) == [
        ("Button", "function", 1, 1, 0, "const Button = ({label}: Props)"),
    ]


def test_go_methods_are_qualified_by_receiver() -> None:
    assert rows(GoOutlineProvider().outline(GO)) == [
        ("F", "function", 4, 4, 0, "func F(a int) (string, error)"),
        (
            "Server.Handle",
            "method",
            6,
            6,
            0,
            "func (s *Server[T]) Handle(w http.ResponseWriter) error",
        ),
        ("Server", "struct", 8, 8, 0, "type Server[T any] struct"),
        ("Handler", "interface", 11, 11, 0, "type Handler interface"),
        ("ID", "type", 12, 12, 0, "type ID = string"),
    ]


def test_java_nested_types_and_annotations() -> None:
    assert rows(JavaOutlineProvider().outline(JAVA)) == [
        ("A", "class", 1, 7, 0, "public class A"),
        ("A.A", "constructor", 3, 3, 1, "public A()"),
        ("A.m", "method", 4, 4, 1, "public void m(int x)"),
        ("A.Inner", "class", 5, 5, 1, "static class Inner"),
        ("A.Inner.n", "method", 5, 5, 2, "void n()"),
        ("A.Color", "enum", 6, 6, 1, "enum Color"),
        ("A.Color.paint", "method", 6, 6, 2, "void paint()"),
        ("I", "interface", 8, 8, 0, "interface I"),
        ("I.m", "method", 8, 8, 1, "void m()"),
        ("P", "record", 9, 9, 0, "record P(int x)"),
        ("P.twice", "method", 9, 9, 1, "int twice()"),
    ]


@pytest.mark.parametrize(
    "provider",
    [JavaScriptOutlineProvider(), GoOutlineProvider(), JavaOutlineProvider()],
    ids=lambda p: p.language,
)
@pytest.mark.parametrize("text", ["", "}{", "class {", "func (", "@@@", "export default"])
def test_malformed_input_never_crashes(provider: JavaOutlineProvider, text: str) -> None:
    provider.outline(text)


def test_read_symbol_through_the_outliner(ctx: AppContext, root: Path) -> None:
    write(root / "server.go", GO)
    write(root / "A.java", JAVA)
    assert ctx.outliner.read_symbol("server.go", "Server.Handle").start_line == 6
    assert ctx.outliner.read_symbol("A.java", "A.Inner.n").start_line == 5


def test_registry_registers_code_languages() -> None:
    suffixes = set(default_registry().supported_suffixes)
    assert {".js", ".mjs", ".jsx", ".ts", ".tsx", ".go", ".java"} <= suffixes


def test_missing_extra_is_reported_with_install_hint(
    ctx: AppContext,
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def not_installed() -> object:
        raise ModuleNotFoundError("No module named 'tree_sitter_go'")

    monkeypatch.setattr(registry_module, "_code_factories", lambda: [not_installed])
    with caplog.at_level(logging.WARNING, logger="mcp_file_system.outline"):
        registry = default_registry()
    assert caplog.text == ""  # an optional extra being absent is not a warning
    assert registry.missing_extra(Path("main.go")) == "code"
    assert registry.missing_extra(Path("notes.md")) is None

    from mcp_file_system.core.outline import Outliner

    outliner = Outliner(ctx.guard, registry, ctx.settings)
    write(root / "main.go", GO)
    with pytest.raises(InvalidArgumentError, match=r'pip install "mcp-file-system\[code\]"'):
        outliner.outline("main.go")
