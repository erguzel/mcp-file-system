"""Maps file suffixes to outline providers."""

from collections.abc import Iterable
from pathlib import Path

from mcp_file_system.core.outline.base import OutlineProvider
from mcp_file_system.core.outline.markdown import MarkdownOutlineProvider
from mcp_file_system.core.outline.python import PythonOutlineProvider


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


def default_registry() -> OutlineRegistry:
    """Registry with the built-in providers (Python and Markdown)."""
    return OutlineRegistry([PythonOutlineProvider(), MarkdownOutlineProvider()])
