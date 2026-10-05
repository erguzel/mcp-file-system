"""Composition root: wires settings, security and services together."""

from dataclasses import dataclass

from mcp_file_system.audit import AuditLog
from mcp_file_system.config import Settings
from mcp_file_system.core.editor import FileEditor
from mcp_file_system.core.finder import FileFinder, Walker
from mcp_file_system.core.outline import Outliner, OutlineRegistry, default_registry
from mcp_file_system.core.peek import Peeker
from mcp_file_system.core.reader import FileReader
from mcp_file_system.core.search import PythonSearchBackend, SearchBackend, Searcher
from mcp_file_system.core.tracking import ReadTracker
from mcp_file_system.security import AccessPolicy, PathGuard


@dataclass(frozen=True, slots=True)
class AppContext:
    """The fully wired services that tool adapters delegate to."""

    settings: Settings
    guard: PathGuard
    reader: FileReader
    finder: FileFinder
    searcher: Searcher
    outliner: Outliner
    editor: FileEditor
    peeker: Peeker
    audit: AuditLog | None = None

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        search_backend: SearchBackend | None = None,
        outline_registry: OutlineRegistry | None = None,
    ) -> "AppContext":
        """Build every service from ``settings``; extension points can be overridden.

        Raises:
            OSError: The configured audit log cannot be created or written.
        """
        guard = PathGuard(settings.roots, AccessPolicy(settings.deny_globs))
        walker = Walker(guard, respect_gitignore=settings.respect_gitignore)
        finder = FileFinder(guard, walker, settings)
        tracker = ReadTracker()  # shared, so read_lines and read_symbol see each other's reads
        outliner = Outliner(guard, outline_registry or default_registry(), settings, tracker)
        return cls(
            settings=settings,
            guard=guard,
            reader=FileReader(guard, settings, tracker),
            finder=finder,
            searcher=Searcher(
                finder, search_backend or PythonSearchBackend(), settings, symbols=outliner
            ),
            outliner=outliner,
            editor=FileEditor(guard, settings),
            peeker=Peeker(guard, settings),
            audit=(
                AuditLog(settings.audit_log, profile=settings.profile)
                if settings.audit_log is not None
                else None
            ),
        )
