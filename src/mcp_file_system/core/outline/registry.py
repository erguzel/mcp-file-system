"""Maps file suffixes to outline providers."""

import logging
import re
from collections.abc import Callable, Iterable
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath

from mcp_file_system.core.outline.base import OutlineProvider
from mcp_file_system.core.outline.dockerfile import DockerfileOutlineProvider
from mcp_file_system.core.outline.markdown import RegexMarkdownOutlineProvider
from mcp_file_system.core.outline.python import PythonOutlineProvider

logger = logging.getLogger("mcp_file_system.outline")

_CODE_SUFFIXES = (".js", ".mjs", ".cjs", ".jsx", ".ts", ".mts", ".cts", ".tsx", ".go", ".java")
OPTIONAL_SUFFIXES: dict[str, str] = dict.fromkeys(_CODE_SUFFIXES, "code")
"""Suffixes served by providers from an optional extra (suffix -> extra name)."""

_SHEBANG = re.compile(r"#!\s*(\S+)((?:\s+\S+)*)")
_VERSION_SUFFIX = re.compile(r"[\d.]+$")


def shebang_interpreter(first_line: str) -> str | None:
    """Interpreter named by a shebang line, without version digits.

    ``#!/bin/bash -e`` -> ``bash``; ``#!/usr/bin/env python3.12`` -> ``python``;
    ``#!/usr/bin/env -S bash -x`` -> ``bash``. Returns ``None`` without a shebang.
    """
    match = _SHEBANG.match(first_line)
    if match is None:
        return None
    program = PurePosixPath(match.group(1)).name
    if program == "env":
        arguments = [a for a in match.group(2).split() if not a.startswith("-")]
        if not arguments:
            return None
        program = PurePosixPath(arguments[0]).name
    return _VERSION_SUFFIX.sub("", program) or None


class OutlineRegistry:
    """Selects the outline provider for a file (later registrations win).

    Files are matched by suffix first, so ``Dockerfile.md`` stays Markdown. A file
    whose suffix is unknown is then matched by file-name pattern (optional
    ``filenames`` attribute: lower-case globs such as ``"dockerfile.*"``, which covers
    ``Dockerfile`` and ``Dockerfile.dev``), and finally by the interpreter in its
    shebang line (optional ``interpreters`` attribute, e.g. ``{"bash", "sh"}``).
    """

    def __init__(self, providers: Iterable[OutlineProvider] = ()) -> None:
        self._by_suffix: dict[str, OutlineProvider] = {}
        self._by_interpreter: dict[str, OutlineProvider] = {}
        self._by_filename: dict[str, OutlineProvider] = {}
        for provider in providers:
            self.register(provider)

    def register(self, provider: OutlineProvider) -> None:
        """Register ``provider`` for its file-name patterns, extensions and interpreters."""
        for suffix in provider.extensions:
            self._by_suffix[suffix.lower()] = provider
        for interpreter in getattr(provider, "interpreters", frozenset()):
            self._by_interpreter[interpreter] = provider
        for pattern in getattr(provider, "filenames", ()):
            self._by_filename[pattern.lower()] = provider

    def providers(self) -> tuple[OutlineProvider, ...]:
        """Every provider that some suffix, interpreter or file name currently selects, once.

        Fallbacks shadowed by a later registration for all their keys (the regex Markdown
        provider once tree-sitter Markdown loads) are not included. Lets tests and tooling
        enumerate what the registry serves instead of keeping a hand-maintained list.
        """
        seen: dict[int, OutlineProvider] = {}
        for provider in (
            *self._by_suffix.values(),
            *self._by_interpreter.values(),
            *self._by_filename.values(),
        ):
            seen.setdefault(id(provider), provider)
        return tuple(seen.values())

    def for_path(self, path: Path) -> OutlineProvider | None:
        """Return the provider for ``path``'s name or suffix, or ``None`` if unsupported."""
        by_suffix = self._by_suffix.get(path.suffix.lower())
        if by_suffix is not None:
            return by_suffix
        name = path.name.lower()
        return next(
            (p for pattern, p in self._by_filename.items() if fnmatchcase(name, pattern)), None
        )

    def missing_extra(self, path: Path) -> str | None:
        """Name of the extra that would add support for ``path``, if it is not installed."""
        suffix = path.suffix.lower()
        if suffix in self._by_suffix:
            return None
        return OPTIONAL_SUFFIXES.get(suffix)

    def for_shebang(self, first_line: str) -> OutlineProvider | None:
        """Return the provider for the interpreter in a shebang line, or ``None``."""
        interpreter = shebang_interpreter(first_line)
        return self._by_interpreter.get(interpreter) if interpreter else None

    @property
    def supported_suffixes(self) -> tuple[str, ...]:
        """All registered suffixes, sorted."""
        return tuple(sorted(self._by_suffix))

    @property
    def supported_interpreters(self) -> tuple[str, ...]:
        """All interpreters recognized in shebang lines, sorted."""
        return tuple(sorted(self._by_interpreter))


def _tree_sitter_factories() -> list[Callable[[], OutlineProvider]]:
    from mcp_file_system.core.outline.treesitter import (  # noqa: PLC0415 - optional import
        JsonOutlineProvider,
        ShellOutlineProvider,
        TomlOutlineProvider,
        TreeSitterMarkdownOutlineProvider,
        YamlOutlineProvider,
    )

    return [
        TreeSitterMarkdownOutlineProvider,
        JsonOutlineProvider,
        TomlOutlineProvider,
        YamlOutlineProvider,
        ShellOutlineProvider,
    ]


def _code_factories() -> list[Callable[[], OutlineProvider]]:
    from mcp_file_system.core.outline.treesitter.code import (  # noqa: PLC0415 - optional
        GoOutlineProvider,
        JavaOutlineProvider,
        JavaScriptOutlineProvider,
        TsxOutlineProvider,
        TypeScriptOutlineProvider,
    )

    return [
        JavaScriptOutlineProvider,
        TypeScriptOutlineProvider,
        TsxOutlineProvider,
        GoOutlineProvider,
        JavaOutlineProvider,
    ]


def default_registry() -> OutlineRegistry:
    """Registry with the built-in providers.

    Python and Dockerfiles need no grammar. Markdown, JSON, TOML, YAML and shell use
    tree-sitter grammars (core dependencies); JavaScript/TypeScript, Go and Java use
    grammars from the optional ``[code]`` extra and are silently skipped without it.
    If a core grammar cannot be loaded (e.g. no wheel for the platform), that format
    is skipped with a warning and Markdown falls back to the regex provider.
    """
    registry = OutlineRegistry(
        [PythonOutlineProvider(), RegexMarkdownOutlineProvider(), DockerfileOutlineProvider()]
    )
    try:
        factories = _tree_sitter_factories()
    except (ImportError, OSError) as exc:
        logger.warning("tree-sitter outlines unavailable: %s", exc)
        return registry
    for factory in factories:
        try:
            registry.register(factory())
        except (ImportError, OSError, ValueError) as exc:
            logger.warning("outline provider %s unavailable: %s", factory.__name__, exc)
    # The [code] extra is optional: a missing grammar is expected, not a warning.
    for factory in _code_factories():
        try:
            registry.register(factory())
        except (ImportError, OSError, ValueError) as exc:
            logger.debug("optional outline provider %s unavailable: %s", factory.__name__, exc)
    return registry
