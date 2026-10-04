"""Skeleton views: folding function bodies while keeping the API visible."""

import ast
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from conftest import ContextFactory, write
from mcp_file_system.context import AppContext
from mcp_file_system.core.outline.python import PythonOutlineProvider
from mcp_file_system.core.outline.treesitter import ShellOutlineProvider
from mcp_file_system.core.skeleton import normalize_folds, render_skeleton
from mcp_file_system.errors import InvalidArgumentError, ParseError

PY = '''\
"""Module docstring."""
import os


@decorator
def documented(a: int) -> int:
    """Return a doubled."""
    value = a * 2
    return value


def one_liner(): return 1


async def outer():
    def inner():
        return 1

    return inner()


class Service:
    """A service."""

    timeout = 30

    def run(self) -> None:
        os.getcwd()
        self.stop()

    def only_docstring(self):
        """Nothing else."""
'''


class TestPythonFolds:
    def test_fold_ranges(self) -> None:
        folds = normalize_folds(PythonOutlineProvider().fold_ranges(PY), PY.count("\n"))
        # documented: body after its docstring; outer: whole body incl. inner(); run: body.
        assert folds == [(8, 9), (16, 19), (28, 29)]

    def test_render(self, ctx: AppContext, root: Path) -> None:
        write(root / "svc.py", PY)
        result = ctx.outliner.skeleton("svc.py")
        assert result.content.splitlines() == [
            ' 1\t"""Module docstring."""',
            " 2\timport os",
            " 3\t",
            " 4\t",
            " 5\t@decorator",
            " 6\tdef documented(a: int) -> int:",
            ' 7\t    """Return a doubled."""',
            "8-9\t    ...",
            "10\t",
            "11\t",
            "12\tdef one_liner(): return 1",
            "13\t",
            "14\t",
            "15\tasync def outer():",
            "16-19\t    ...",
            "20\t",
            "21\t",
            "22\tclass Service:",
            '23\t    """A service."""',
            "24\t",
            "25\t    timeout = 30",
            "26\t",
            "27\t    def run(self) -> None:",
            "28-29\t        ...",  # the fold keeps the body's indentation
            "30\t",
            "31\t    def only_docstring(self):",
            '32\t        """Nothing else."""',
        ]
        assert (result.shown_lines, result.folded_lines, result.total_lines) == (24, 8, 32)
        assert result.language == "python"
        rendered = result.render()
        assert rendered.startswith("==> svc.py (python skeleton: 24 of 32 lines shown, 8 folded;")
        assert "read_lines(start, end)" in rendered

    def test_decorated_nested_function_after_docstring(self) -> None:
        text = 'def f():\n    """Doc."""\n    @cache\n    def g():\n        pass\n    return g\n'
        assert PythonOutlineProvider().fold_ranges(text) == [(3, 6), (5, 5)]
        assert normalize_folds(PythonOutlineProvider().fold_ranges(text), 6) == [(3, 6)]

    def test_syntax_error(self, ctx: AppContext, root: Path) -> None:
        write(root / "bad.py", "def (:\n")
        with pytest.raises(ParseError, match=r"bad\.py as python"):
            ctx.outliner.skeleton("bad.py")


SHELL = """\
#!/usr/bin/env bash
deploy() {
  kubectl apply -f k8s/
  kubectl rollout status deploy/app
}
quick() { echo hi; }
"""


def test_shell_folds() -> None:
    assert ShellOutlineProvider().fold_ranges(SHELL) == [(3, 4)]


class TestCodeFolds:
    @pytest.fixture(autouse=True)
    def _require_code_extra(self) -> None:
        pytest.importorskip("tree_sitter_go")
        pytest.importorskip("tree_sitter_java")
        pytest.importorskip("tree_sitter_typescript")

    def test_go(self, ctx: AppContext, root: Path) -> None:
        write(
            root / "main.go",
            "package main\n\nfunc main() {\n\tx := 1\n\t_ = x\n}\n\nfunc short() { return }\n",
        )
        assert ctx.outliner.skeleton("main.go").content.splitlines() == [
            "1\tpackage main",
            "2\t",
            "3\tfunc main() {",
            "4-5\t\t...",
            "6\t}",
            "7\t",
            "8\tfunc short() { return }",
        ]

    def test_java_methods_inside_classes(self, ctx: AppContext, root: Path) -> None:
        text = (
            "class A {\n  /** Doc. */\n  void m() {\n    int x = 1;\n    run(x);\n  }\n"
            "  A() {\n    super();\n    init();\n  }\n}\n"
        )
        write(root / "A.java", text)
        result = ctx.outliner.skeleton("A.java")
        assert "4-5\t    ..." in result.content
        assert "8-9\t    ..." in result.content
        assert "  /** Doc. */" in result.content

    def test_typescript_arrow_functions_and_methods(self, ctx: AppContext, root: Path) -> None:
        text = (
            "export const handler = async (req: Req) => {\n  const a = 1;\n  return a;\n};\n"
            "class S {\n  run(): void {\n    one();\n    two();\n  }\n}\n"
        )
        write(root / "h.ts", text)
        lines = ctx.outliner.skeleton("h.ts").content.splitlines()
        assert "2-3\t  ..." in lines
        assert "7-8\t    ..." in lines


def test_unsupported_formats_point_to_outline(ctx: AppContext, root: Path) -> None:
    write(root / "c.yaml", "a: 1\n")
    with pytest.raises(InvalidArgumentError, match="use outline"):
        ctx.outliner.skeleton("c.yaml")


def test_output_budget(make_ctx: ContextFactory, root: Path) -> None:
    write(root / "m.py", "".join(f"X{n} = {n}\n" for n in range(400)))
    result = make_ctx(max_output_chars=1000).outliner.skeleton("m.py")
    assert result.truncated
    assert result.next_start is not None
    assert "Truncated at line" in result.render()


class TestNormalizeAndRender:
    def test_normalize_clips_merges_and_drops_empty(self) -> None:
        assert normalize_folds([(5, 3), (0, 2), (4, 8), (6, 7), (8, 12), (20, 30)], 10) == [
            (1, 2),
            (4, 10),
        ]

    def test_render_without_folds_is_the_numbered_file(self) -> None:
        content, shown, folded, cut = render_skeleton(
            ["a", "b"], [], max_line_chars=100, max_chars=1000
        )
        assert (content, shown, folded, cut) == ("1\ta\n2\tb", 2, 0, None)

    @given(
        total=st.integers(1, 60),
        folds=st.lists(st.tuples(st.integers(-5, 70), st.integers(-5, 70)), max_size=12),
    )
    def test_every_line_is_shown_or_folded_exactly_once(
        self, total: int, folds: list[tuple[int, int]]
    ) -> None:
        lines = [f"line {n}" for n in range(1, total + 1)]
        content, shown, folded, cut = render_skeleton(
            lines, folds, max_line_chars=100, max_chars=10**6
        )
        assert cut is None
        assert shown + folded == total
        covered: list[int] = []
        for row in content.splitlines():
            label = row.split("\t", 1)[0].strip()
            if "-" in label:
                start, end = map(int, label.split("-"))
                covered.extend(range(start, end + 1))
            else:
                covered.append(int(label))
        assert covered == list(range(1, total + 1))


def test_every_module_of_this_repository_skeletons_to_valid_python(
    make_ctx: ContextFactory,
) -> None:
    # ROADMAP item 5 "done when": folding must never break the structure. A fold row
    # becomes "...", which is a valid statement, so stripped skeletons must still parse.
    src = Path(__file__).resolve().parents[2] / "src"
    ctx = make_ctx(roots=(src,), max_output_chars=10**7)
    modules = sorted(src.rglob("*.py"))
    assert len(modules) > 30
    for module in modules:
        result = ctx.outliner.skeleton(str(module))
        code = "\n".join(row.split("\t", 1)[1] for row in result.content.splitlines())
        ast.parse(code, filename=str(module))
