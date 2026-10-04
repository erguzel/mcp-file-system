from pathlib import Path

import pytest

from conftest import write
from mcp_file_system.context import AppContext
from mcp_file_system.core.models import Symbol
from mcp_file_system.core.outline.dockerfile import DockerfileOutlineProvider
from mcp_file_system.core.outline.registry import default_registry


def rows(symbols: list[Symbol]) -> list[tuple[str, int, int, int]]:
    return [(s.qualified_name, s.start_line, s.end_line, s.depth) for s in symbols]


DOCKERFILE = """\
# syntax=docker/dockerfile:1.7
ARG PYTHON=3.12

FROM python:${PYTHON}-slim AS builder
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN pip install uv \\
    # resolve and install
    && uv sync --locked
RUN <<EOF
set -e
echo building
EOF

FROM gcr.io/distroless/python3
COPY --from=builder /app /app
ENTRYPOINT ["python", "-m", "app"]
"""


def test_stages_and_instructions() -> None:
    symbols = DockerfileOutlineProvider().outline(DOCKERFILE)
    assert rows(symbols) == [
        ("ARG[0]", 2, 2, 0),
        ("builder", 4, 13, 0),
        ("builder.WORKDIR[0]", 5, 5, 1),
        ("builder.COPY[0]", 6, 6, 1),
        ("builder.RUN[0]", 7, 9, 1),
        ("builder.RUN[1]", 10, 13, 1),
        ("[1]", 15, 17, 0),
        ("[1].COPY[0]", 16, 16, 1),
        ("[1].ENTRYPOINT[0]", 17, 17, 1),
    ]
    by_name = {s.qualified_name: s for s in symbols}
    assert by_name["builder"].kind == "stage"
    assert by_name["builder"].signature == "FROM python:${PYTHON}-slim AS builder"
    assert by_name["builder.RUN[0]"].kind == "instruction"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # <<- strips leading tabs from the delimiter line; quoted delimiters.
        (
            "FROM a\nRUN <<-EOT\n\techo\n\tEOT\nRUN x\n",
            [("[0]", 1, 5, 0), ("[0].RUN[0]", 2, 4, 1), ("[0].RUN[1]", 5, 5, 1)],
        ),
        (
            "FROM a\nCOPY <<'A' /a <<\"B\" /b\none\nA\ntwo\nB\nRUN x\n",
            [("[0]", 1, 7, 0), ("[0].COPY[0]", 2, 6, 1), ("[0].RUN[0]", 7, 7, 1)],
        ),
        # An unterminated heredoc runs to the end of the file.
        ("FROM a\nRUN <<EOF\necho\n", [("[0]", 1, 3, 0), ("[0].RUN[0]", 2, 3, 1)]),
        # A trailing escape at end of file does not run past it.
        ("FROM a\nRUN echo \\\n", [("[0]", 1, 2, 0), ("[0].RUN[0]", 2, 2, 1)]),
        # Empty lines inside a continuation are allowed.
        (
            "FROM a\nRUN a \\\n\n  b\nRUN c\n",
            [("[0]", 1, 5, 0), ("[0].RUN[0]", 2, 4, 1), ("[0].RUN[1]", 5, 5, 1)],
        ),
        # Lower-case instructions and an alias in lower case.
        ("from a as base\nrun x\n", [("base", 1, 2, 0), ("base.RUN[0]", 2, 2, 1)]),
        # No FROM at all: instructions are top level.
        ("ARG A\nARG B\n", [("ARG[0]", 1, 1, 0), ("ARG[1]", 2, 2, 0)]),
        ("", []),
        ("# only a comment\n", []),
    ],
)
def test_scanner_edge_cases(text: str, expected: list[tuple[str, int, int, int]]) -> None:
    assert rows(DockerfileOutlineProvider().outline(text)) == expected


def test_escape_directive_backtick() -> None:
    text = "# escape=`\nFROM mcr.microsoft.com/windows\nRUN dir `\n  C:\\\nRUN echo\n"
    assert rows(DockerfileOutlineProvider().outline(text)) == [
        ("[0]", 2, 5, 0),
        ("[0].RUN[0]", 3, 4, 1),
        ("[0].RUN[1]", 5, 5, 1),
    ]


def test_long_signatures_are_clipped() -> None:
    symbol = DockerfileOutlineProvider().outline("RUN " + "x" * 400 + "\n")[0]
    assert symbol.signature is not None
    assert len(symbol.signature) == 200


@pytest.mark.parametrize(
    "name",
    [
        "Dockerfile",
        "dockerfile",
        "Dockerfile.dev",
        "api.Dockerfile",
        "build.dockerfile",
        "Containerfile",
    ],
)
def test_file_names_are_recognized(name: str) -> None:
    provider = default_registry().for_path(Path(name))
    assert provider is not None
    assert provider.language == "dockerfile"


@pytest.mark.parametrize(
    ("name", "language"), [("Dockerfile.md", "markdown"), ("dockerfile.yaml", "yaml")]
)
def test_known_suffixes_win_over_file_name_patterns(name: str, language: str) -> None:
    provider = default_registry().for_path(Path(name))
    assert provider is not None
    assert provider.language == language


def test_read_symbol_returns_one_stage(ctx: AppContext, root: Path) -> None:
    write(root / "Dockerfile", DOCKERFILE)
    stage = ctx.outliner.read_symbol("Dockerfile", "builder")
    assert (stage.start_line, stage.end_line) == (4, 13)
    run = ctx.outliner.read_symbol("Dockerfile", "builder.RUN[1]")
    assert (run.start_line, run.end_line) == (10, 13)
    assert ctx.outliner.outline("Dockerfile").language == "dockerfile"
