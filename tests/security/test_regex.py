import time

import pytest

from mcp_file_system.errors import InvalidPatternError, PatternTimeoutError
from mcp_file_system.security import Deadline, compile_pattern


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def test_deadline_counts_down_and_expires() -> None:
    clock = FakeClock()
    deadline = Deadline(5.0, clock=clock)
    assert deadline.remaining() == 5.0
    assert not deadline.expired
    clock.now += 6
    assert deadline.remaining() == 0.0
    assert deadline.expired


def test_catastrophic_backtracking_times_out_quickly() -> None:
    pattern = compile_pattern(r"(a|aa)+$", deadline=Deadline(0.3), max_length=100)
    started = time.monotonic()
    with pytest.raises(PatternTimeoutError, match="time budget") as excinfo:
        pattern.search("a" * 64 + "b")
    assert time.monotonic() - started < 5
    # Callers differ in how they search literally (fixed_string, regex=false).
    assert "Simplify the pattern" in str(excinfo.value)
    assert "fixed_string" not in str(excinfo.value)


def test_expired_deadline_refuses_new_work() -> None:
    clock = FakeClock()
    deadline = Deadline(1.0, clock=clock)
    pattern = compile_pattern("a", deadline=deadline, max_length=10)
    clock.now += 2
    with pytest.raises(PatternTimeoutError):
        pattern.search("a")
    with pytest.raises(PatternTimeoutError):
        pattern.finditer("a")
    with pytest.raises(PatternTimeoutError):
        pattern.subn("b", "a")


def test_finditer_and_subn_timeouts_are_translated() -> None:
    pattern = compile_pattern(r"(a|aa)+$", deadline=Deadline(0.2), max_length=100)
    with pytest.raises(PatternTimeoutError):
        pattern.finditer("a" * 64 + "b")
    pattern = compile_pattern(r"(a|aa)+$", deadline=Deadline(0.2), max_length=100)
    with pytest.raises(PatternTimeoutError):
        pattern.subn("x", "a" * 64 + "b")


def test_pattern_length_limit() -> None:
    with pytest.raises(InvalidPatternError, match="limit is 5"):
        compile_pattern("abcdef", deadline=Deadline(1), max_length=5)


def test_empty_pattern_rejected() -> None:
    with pytest.raises(InvalidPatternError, match="empty"):
        compile_pattern("", deadline=Deadline(1), max_length=5)


def test_invalid_regex_rejected() -> None:
    with pytest.raises(InvalidPatternError, match="Invalid regular expression"):
        compile_pattern("(", deadline=Deadline(1), max_length=5)


def test_invalid_regex_message_does_not_echo_the_pattern() -> None:
    # Error messages reach the audit log, where replace_in_file's pattern must stay hidden.
    with pytest.raises(InvalidPatternError) as excinfo:
        compile_pattern("secret[", deadline=Deadline(1), max_length=50)
    assert "secret" not in str(excinfo.value)
    assert "position" in str(excinfo.value)


def test_fixed_string_escapes_metacharacters() -> None:
    pattern = compile_pattern("a.b(", deadline=Deadline(1), max_length=10, fixed_string=True)
    assert pattern.search("xa.b(y") is not None
    assert pattern.search("axb(") is None
    assert pattern.source == "a.b("


def test_ignore_case() -> None:
    pattern = compile_pattern("hello", deadline=Deadline(1), max_length=10, ignore_case=True)
    assert pattern.search("HeLLo") is not None


def test_subn_supports_group_references() -> None:
    pattern = compile_pattern(r"(\w+)@(\w+)", deadline=Deadline(1), max_length=50)
    assert pattern.subn(r"\2 at \1", "me@host and you@there") == ("host at me and there at you", 2)


@pytest.mark.parametrize("template", [r"\9", r"\g<missing>"])
def test_subn_invalid_template(template: str) -> None:
    pattern = compile_pattern(r"(a)", deadline=Deadline(1), max_length=50)
    with pytest.raises(InvalidPatternError, match="replacement template"):
        pattern.subn(template, "a")
