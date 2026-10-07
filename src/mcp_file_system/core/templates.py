"""Line templates: mask variable tokens so near-identical lines collapse into one.

``Connection timeout after 312ms to 10.0.0.4`` and ``... 98ms to 10.0.0.7`` both become
``Connection timeout after <N>ms to <IP>``. Counting templates instead of lines turns
thousands of log lines into a handful of rows, which is the point of ``grep``'s
``collapsed`` mode and of ``log_summary``.

The masking patterns are fixed (never agent-supplied) and free of nested quantifiers, so
they run in linear time on the bounded text they are given.
"""

import re
from dataclasses import dataclass

MAX_TEMPLATES = 10_000
"""Distinct templates kept per operation; further ones are only counted (bounds memory)."""

_HEX = "[0-9a-fA-F]"
_IPV6 = re.compile(
    rf"(?<![\w:])(?=[\w:]*\d)"  # starts a token and contains a digit ("std::map" does not)
    rf"(?:(?:{_HEX}{{1,4}}:){{7}}{_HEX}{{1,4}}"  # full form
    rf"|(?:{_HEX}{{1,4}}(?::{_HEX}{{1,4}})*)?::(?:{_HEX}{{1,4}}(?::{_HEX}{{1,4}})*)?)"  # with ::
    rf"(?![\w:])"
)

MASK_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "<TS>",
        re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?"),
    ),
    ("<UUID>", re.compile(rf"\b{_HEX}{{8}}-{_HEX}{{4}}-{_HEX}{{4}}-{_HEX}{{4}}-{_HEX}{{12}}\b")),
    ("<IP>", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
    # 0x-prefixed, or 8+ hex digits mixing letters and digits (hashes, ids); plain decimal
    # numbers fall through to <N>, words such as "deadbeef" stay as they are.
    ("<HEX>", re.compile(rf"\b0[xX]{_HEX}+\b|\b(?={_HEX}*[a-fA-F])(?={_HEX}*\d){_HEX}{{8,}}\b")),
    # Digits not glued to a preceding letter: "312ms" and "worker-3" are masked, while
    # identifiers such as "utf8", "sha256" or "x86_64" keep their meaning.
    ("<N>", re.compile(r"(?<![A-Za-z_\d])\d+")),
)
"""Masks applied in order; earlier rules claim their tokens before ``<N>`` sees digits."""


def mask_line(text: str) -> str:
    """Replace timestamps, UUIDs, IPs, hex values and numbers with placeholders."""
    # IPv6 first, and only when the line can hold one: it is the costliest pattern and
    # its digits must not be claimed by the other rules.
    if "::" in text or text.count(":") >= 7:
        text = _IPV6.sub("<IP>", text)
    for placeholder, pattern in MASK_RULES:
        text = pattern.sub(placeholder, text)
    return text


@dataclass(slots=True)
class TemplateStats:
    """Occurrences of one template: how many, in how many files, and where first and last."""

    count: int
    files: int
    first_path: str
    first_line: int
    last_path: str
    last_line: int


class TemplateTally:
    """Counts lines per template, keeping at most ``max_templates`` distinct ones.

    Lines are expected file by file (as the search walker yields them), so a template's
    file count grows only when its last occurrence was in another file.
    """

    def __init__(self, max_templates: int = MAX_TEMPLATES) -> None:
        self._max_templates = max_templates
        self._stats: dict[str, TemplateStats] = {}
        self.other = 0
        """Lines whose template did not fit under the limit."""

    def add(self, text: str, path: str, line: int) -> None:
        """Count ``text`` (already clipped by the caller) found at ``path:line``."""
        template = mask_line(text)
        stats = self._stats.get(template)
        if stats is None:
            if len(self._stats) >= self._max_templates:
                self.other += 1
                return
            self._stats[template] = TemplateStats(1, 1, path, line, path, line)
            return
        stats.count += 1
        if stats.last_path != path:
            stats.files += 1
            stats.last_path = path
        stats.last_line = line

    def __len__(self) -> int:
        return len(self._stats)

    def most_common(self) -> list[tuple[str, TemplateStats]]:
        """All templates by descending count; ties keep first-seen order."""
        return sorted(self._stats.items(), key=lambda item: -item[1].count)
