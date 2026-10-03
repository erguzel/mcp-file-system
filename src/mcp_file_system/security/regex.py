"""Safe evaluation of agent-supplied regular expressions.

Python's built-in :mod:`re` has no timeout, so a pattern with catastrophic backtracking
can pin a CPU indefinitely (ReDoS). Patterns are compiled with the third-party
:mod:`regex` module instead, and every evaluation draws from a shared wall-clock
:class:`Deadline`.
"""

import time
from collections.abc import Callable

import regex

from mcp_file_system.errors import InvalidPatternError, PatternTimeoutError

_TIMEOUT_HINT = (
    "Pattern evaluation exceeded the time budget. Simplify the pattern (avoid nested "
    "quantifiers such as '(a+)+'), use fixed_string=true, or narrow the search path."
)


class Deadline:
    """A monotonic wall-clock budget shared by the steps of one operation."""

    __slots__ = ("_clock", "_end")

    def __init__(self, seconds: float, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._end = clock() + seconds

    def remaining(self) -> float:
        """Seconds left, never negative."""
        return max(0.0, self._end - self._clock())

    @property
    def expired(self) -> bool:
        """Whether the budget is used up."""
        return self._clock() >= self._end


class SafePattern:
    """A compiled pattern whose every evaluation is bounded by a :class:`Deadline`."""

    __slots__ = ("_compiled", "_deadline", "source")

    def __init__(self, compiled: regex.Pattern[str], deadline: Deadline, source: str) -> None:
        self._compiled = compiled
        self._deadline = deadline
        self.source = source
        """The pattern as supplied by the caller."""

    def search(self, text: str) -> regex.Match[str] | None:
        """Return the first match in ``text``, or ``None``."""
        try:
            return self._compiled.search(text, timeout=self._budget())
        except TimeoutError:
            raise PatternTimeoutError(_TIMEOUT_HINT) from None

    def finditer(self, text: str) -> list[regex.Match[str]]:
        """Return all non-overlapping matches in ``text``."""
        try:
            return list(self._compiled.finditer(text, timeout=self._budget()))
        except TimeoutError:
            raise PatternTimeoutError(_TIMEOUT_HINT) from None

    def subn(
        self, replacement: str | Callable[[regex.Match[str]], str], text: str
    ) -> tuple[str, int]:
        """Replace every match; a string replacement may use ``\\1`` / ``\\g<name>``."""
        try:
            return self._compiled.subn(replacement, text, timeout=self._budget())
        except TimeoutError:
            raise PatternTimeoutError(_TIMEOUT_HINT) from None
        except (regex.error, IndexError) as exc:
            raise InvalidPatternError(f"Invalid replacement template: {exc}") from None

    def _budget(self) -> float:
        remaining = self._deadline.remaining()
        if remaining <= 0:
            raise PatternTimeoutError(_TIMEOUT_HINT)
        return remaining


def compile_pattern(
    pattern: str,
    *,
    deadline: Deadline,
    max_length: int,
    fixed_string: bool = False,
    ignore_case: bool = False,
) -> SafePattern:
    """Validate and compile an agent-supplied pattern.

    Args:
        pattern: Regular expression (Python syntax), or a literal if ``fixed_string``.
        deadline: Budget shared by all evaluations of the returned pattern.
        max_length: Longest accepted pattern.
        fixed_string: Treat ``pattern`` as literal text.
        ignore_case: Match case-insensitively.

    Raises:
        InvalidPatternError: The pattern is empty, too long, or does not compile.
    """
    if not pattern:
        raise InvalidPatternError("Pattern must not be empty.")
    if len(pattern) > max_length:
        raise InvalidPatternError(
            f"Pattern is {len(pattern)} characters long; the limit is {max_length}."
        )
    source = regex.escape(pattern) if fixed_string else pattern
    flags = regex.VERSION0 | (regex.IGNORECASE if ignore_case else 0)
    try:
        compiled = regex.compile(source, flags)
    except regex.error as exc:
        raise InvalidPatternError(f"Invalid regular expression {pattern!r}: {exc}") from None
    return SafePattern(compiled, deadline, pattern)
