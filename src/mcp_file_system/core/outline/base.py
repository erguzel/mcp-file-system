"""The outline provider contract."""

from typing import Protocol

from mcp_file_system.core.models import Symbol


class OutlineProvider(Protocol):
    """Extracts the structural skeleton of one kind of text file.

    To add a language, implement this protocol and register an instance with
    :class:`~mcp_file_system.core.outline.registry.OutlineRegistry`.
    """

    @property
    def language(self) -> str:
        """Human-readable language name, e.g. ``"python"``."""
        ...

    @property
    def extensions(self) -> frozenset[str]:
        """Lower-case file suffixes handled, including the dot, e.g. ``{".py"}``."""
        ...

    def outline(self, text: str) -> list[Symbol]:
        """Return the symbols of ``text`` in source order.

        Raises:
            ParseError: The text cannot be parsed. The message should describe the
                problem without referring to a file path; callers add it.
        """
        ...
