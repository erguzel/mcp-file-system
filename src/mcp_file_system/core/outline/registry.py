"""Maps file suffixes to outline providers."""

import logging
import re
from collections.abc import Callable, Iterable
from pathlib import Path, PurePosixPath

from mcp_file_system.core.outline.base import OutlineProvider
from mcp_file_system.core.outline.markdown import RegexMarkdownOutlineProvider
from mcp_file_system.core.outline.python import PythonOutlineProvider

logger = logging.getLogger("mcp_file_system.outline")

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

    Files are matched by suffix. Files without a known suffix (e.g. ``bin/deploy``) can
    be matched by the interpreter in their shebang line, for providers that declare an
    optional ``interpreters`` attribute (a frozenset such as ``{"bash", "sh"}``).
    """

    def __init__(self, providers: Iterable[OutlineProvider] = ()) -> None:
        self._by_suffix: dict[str, OutlineProvider] = {}
        self._by_interpreter: dict[str, OutlineProvider] = {}
        for provider in providers:
            self.register(provider)

    def register(self, provider: OutlineProvider) -> None:
        """Register ``provider`` for each of its extensions and interpreters."""
        for suffix in provider.extensions:
            self._by_suffix[suffix.lower()] = provider
        for interpreter in getattr(provider, "interpreters", frozenset()):
            self._by_interpreter[interpreter] = provider

    def for_path(self, path: Path) -> OutlineProvider | None:
        """Return the provider for ``path``'s suffix, or ``None`` if unsupported."""
        return self._by_suffix.get(path.suffix.lower())

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


def default_registry() -> OutlineRegistry:
    """Registry with the built-in providers.

    Python uses the standard library. Markdown, JSON, TOML, YAML and shell use
    tree-sitter grammars; if a grammar cannot be loaded (e.g. no wheel for the platform), that
    format is skipped with a warning and Markdown falls back to the regex provider.
    """
    registry = OutlineRegistry([PythonOutlineProvider(), RegexMarkdownOutlineProvider()])
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
    return registry
