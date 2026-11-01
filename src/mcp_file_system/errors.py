"""Domain errors, and the agent-facing text for unexpected filesystem errors.

Every error raised deliberately by this package derives from :class:`FsError`. The
tool layer converts these into MCP tool errors, so their messages are shown to the
agent verbatim: they must be actionable and must never leak paths outside the
configured roots.
"""


def describe_os_error(exc: OSError) -> str:
    """Reduce an ``OSError`` to agent-facing text: its description, never its path.

    ``str(exc)`` would name the absolute path, which the agent must not be shown.
    """
    return f"Filesystem error: {exc.strerror or type(exc).__name__}"


class FsError(Exception):
    """Base class for anticipated, agent-facing errors."""


class InvalidArgumentError(FsError):
    """An argument is syntactically valid but semantically wrong (e.g. ``start > end``)."""


class PathOutsideRootError(FsError):
    """A path resolves to a location outside every allowed root."""


class AccessDeniedError(FsError):
    """A path is inside a root but blocked by the access policy (e.g. a secret file)."""


class NotFoundError(FsError):
    """A path does not exist."""


class PathTypeError(FsError):
    """A path exists but has the wrong type (e.g. a directory where a file is expected)."""


class AlreadyExistsError(FsError):
    """A file that must not exist already exists."""


class BinaryFileError(FsError):
    """A text operation was requested on a binary file."""


class FileTooLargeError(FsError):
    """A file exceeds the configured size limit for an operation that loads it fully."""


class InvalidPatternError(FsError):
    """A regular expression (or replacement template) is invalid or too long."""


class PatternTimeoutError(FsError):
    """Evaluating a regular expression exceeded its time budget (likely backtracking)."""


class NoMatchError(FsError):
    """A pattern that must match (e.g. a section start) did not match often enough."""


class ParseError(FsError):
    """A file could not be parsed for structural analysis (e.g. a Python syntax error)."""


class SymbolNotFoundError(FsError):
    """A requested symbol does not exist, or its name is ambiguous."""


class StaleFileError(FsError):
    """A file changed since the caller last read it (etag mismatch)."""


class MatchCountError(FsError):
    """A replacement matched a different number of times than expected."""
