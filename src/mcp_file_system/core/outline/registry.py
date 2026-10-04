"""Maps file suffixes to outline providers."""

import logging
from collections.abc import Callable, Iterable
from pathlib import Path

from mcp_file_system.core.outline.base import OutlineProvider
from mcp_file_system.core.outline.markdown import RegexMarkdownOutlineProvider
from mcp_file_system.core.outline.python import PythonOutlineProvider

logger = logging.getLogger("mcp_file_system.outline")


class OutlineRegistry:
    """Selects the outline provider for a path by its suffix (later registrations win)."""

    def __init__(self, providers: Iterable[OutlineProvider] = ()) -> None:
        self._by_suffix: dict[str, OutlineProvider] = {}
        for provider in providers:
            self.register(provider)

    def register(self, provider: OutlineProvider) -> None:
        """Register ``provider`` for each of its extensions."""
        for suffix in provider.extensions:
            self._by_suffix[suffix.lower()] = provider

    def for_path(self, path: Path) -> OutlineProvider | None:
        """Return the provider for ``path``, or ``None`` if unsupported."""
        return self._by_suffix.get(path.suffix.lower())

    @property
    def supported_suffixes(self) -> tuple[str, ...]:
        """All registered suffixes, sorted."""
        return tuple(sorted(self._by_suffix))


def _tree_sitter_factories() -> list[Callable[[], OutlineProvider]]:
    from mcp_file_system.core.outline.treesitter import (  # noqa: PLC0415 - optional import
        JsonOutlineProvider,
        TomlOutlineProvider,
        TreeSitterMarkdownOutlineProvider,
        YamlOutlineProvider,
    )

    return [
        TreeSitterMarkdownOutlineProvider,
        JsonOutlineProvider,
        TomlOutlineProvider,
        YamlOutlineProvider,
    ]


def default_registry() -> OutlineRegistry:
    """Registry with the built-in providers.

    Python uses the standard library. Markdown, JSON, TOML and YAML use tree-sitter
    grammars; if a grammar cannot be loaded (e.g. no wheel for the platform), that
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
