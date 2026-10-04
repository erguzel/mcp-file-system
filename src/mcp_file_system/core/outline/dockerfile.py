"""Dockerfile outlines (pure Python, no grammar dependency).

The format is line-based, so a small scanner is more portable than a C grammar (the
``tree-sitter-dockerfile`` package has no Windows wheels). It understands line
continuations (including a custom ``# escape=`` directive), comment lines inside
continued instructions, and BuildKit heredocs (``RUN <<EOF ... EOF``).
"""

import re

from mcp_file_system.core.models import Symbol
from mcp_file_system.core.textio import split_lines

MAX_SIGNATURE_CHARS = 200

_DIRECTIVE = re.compile(r"#\s*([A-Za-z]+)\s*=\s*(\S+)\s*$")
_KEYWORD = re.compile(r"\s*([A-Za-z]+)\b")
_HEREDOC = re.compile(r"<<-?\s*([\"']?)([A-Za-z_][\w.-]*)\1")
_STAGE_ALIAS = re.compile(r"\s+AS\s+(\S+)\s*$", re.IGNORECASE)


class DockerfileOutlineProvider:
    """Lists build stages (``FROM ... AS name``) and the instructions inside them.

    A stage's qualified name is its alias (``builder``), or ``[n]`` for unnamed
    stages; instructions are ``builder.RUN[0]``, ``builder.COPY[1]``, ... Instructions
    before the first ``FROM`` (global ``ARG``s) are top level: ``ARG[0]``.
    """

    language = "dockerfile"
    extensions = frozenset({".dockerfile"})
    filenames = ("dockerfile", "dockerfile.*", "*.dockerfile", "containerfile", "containerfile.*")

    def outline(self, text: str) -> list[Symbol]:
        """Return stages and instructions in source order."""
        lines = split_lines(text)
        escape = _escape_character(lines)
        symbols: list[Symbol] = []
        stage: str | None = None
        stage_index = -1
        counts: dict[str, int] = {}
        for keyword, first, last, head in _instructions(lines, escape):
            if keyword == "FROM":
                stage_index += 1
                alias = _STAGE_ALIAS.search(head)
                stage = alias.group(1) if alias else f"[{stage_index}]"
                counts = {}
                symbols.append(_symbol(stage, stage, "stage", first, last, 0, head))
                continue
            index = counts.get(keyword, 0)
            counts[keyword] = index + 1
            name = f"{keyword}[{index}]"
            qualified = f"{stage}.{name}" if stage is not None else name
            depth = 1 if stage is not None else 0
            symbols.append(_symbol(keyword, qualified, "instruction", first, last, depth, head))
        return _extend_stages(symbols)


def _symbol(
    name: str, qualified: str, kind: str, first: int, last: int, depth: int, head: str
) -> Symbol:
    if len(head) > MAX_SIGNATURE_CHARS:
        head = head[: MAX_SIGNATURE_CHARS - 1] + "…"
    return Symbol(
        name=name,
        qualified_name=qualified,
        kind=kind,
        start_line=first,
        end_line=last,
        depth=depth,
        signature=head,
    )


def _escape_character(lines: list[str]) -> str:
    """The escape character from a leading ``# escape=`` parser directive (default ``\\``)."""
    for line in lines:
        directive = _DIRECTIVE.match(line.strip())
        if directive is None:
            break  # parser directives are only recognized before anything else
        if directive.group(1).lower() == "escape" and directive.group(2) in {"\\", "`"}:
            return directive.group(2)
    return "\\"


def _is_comment_or_blank(line: str) -> bool:
    stripped = line.strip()
    return not stripped or stripped.startswith("#")


def _instructions(lines: list[str], escape: str) -> list[tuple[str, int, int, str]]:
    """Logical instructions as ``(KEYWORD, first_line, last_line, first_line_text)``."""
    found: list[tuple[str, int, int, str]] = []
    total = len(lines)
    index = 0
    while index < total:
        line = lines[index]
        keyword = _KEYWORD.match(line)
        if _is_comment_or_blank(line) or keyword is None:
            index += 1
            continue
        end = index
        delimiters = _HEREDOC.findall(line)
        while end + 1 < total and lines[end].rstrip().endswith(escape):
            end += 1
            # Comment and empty lines inside a continued instruction are skipped.
            while end + 1 < total and _is_comment_or_blank(lines[end]):
                end += 1
            delimiters += _HEREDOC.findall(lines[end])
        for _quote, delimiter in delimiters:
            closing = end + 1
            while closing < total and lines[closing].strip() != delimiter:
                closing += 1
            end = min(closing, total - 1)
        found.append((keyword.group(1).upper(), index + 1, end + 1, line.strip()))
        index = end + 1
    return found


def _extend_stages(symbols: list[Symbol]) -> list[Symbol]:
    """Let each stage span its ``FROM`` line through its last instruction."""
    result = list(symbols)
    stages = [i for i, s in enumerate(symbols) if s.kind == "stage"]
    for position, index in enumerate(stages):
        following = stages[position + 1] if position + 1 < len(stages) else len(symbols)
        members = symbols[index + 1 : following]
        end = max([symbols[index].end_line, *(m.end_line for m in members)])
        result[index] = symbols[index].model_copy(update={"end_line": end})
    return result
