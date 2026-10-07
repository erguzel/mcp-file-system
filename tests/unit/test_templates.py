import pytest

from mcp_file_system.core.templates import TemplateTally, mask_line


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("at 2026-10-06T12:34:56Z", "at <TS>"),
        ("at 2026-10-06 12:34:56,123", "at <TS>"),
        ("at 2026-10-06T12:34:56.5+02:00 done", "at <TS> done"),
        ("at 12:34:56", "at <N>:<N>:<N>"),  # a time without date is only numbers
    ],
)
def test_timestamp_rule(line: str, expected: str) -> None:
    assert mask_line(line) == expected


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("id=123e4567-e89b-12d3-a456-426614174000 ok", "id=<UUID> ok"),
        ("ID=123E4567-E89B-12D3-A456-426614174000", "ID=<UUID>"),
        ("id=123e4567-e89b-12d3-a456", "id=<HEX>-e89b-<N>d3-a456"),  # too short for a UUID
    ],
)
def test_uuid_rule(line: str, expected: str) -> None:
    assert mask_line(line) == expected


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("to 10.0.0.4:8080", "to <IP>:<N>"),
        ("from 192.168.1.255.", "from <IP>."),
        ("from fe80::1", "from <IP>"),
        ("from ::1 and 2001:db8::ff00:42:8329", "from <IP> and <IP>"),
        ("from 2001:0db8:85a3:0000:0000:8a2e:0370:7334", "from <IP>"),
        ("std::vector and a::b", "std::vector and a::b"),  # no digit: not an address
        ("version 1.2.3", "version <N>.<N>.<N>"),
    ],
)
def test_ip_rule(line: str, expected: str) -> None:
    assert mask_line(line) == expected


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("key=0xFF00 flags=0x1", "key=<HEX> flags=<HEX>"),
        ("commit 3f9a2c1d7e", "commit <HEX>"),
        ("word deadbeef stays", "word deadbeef stays"),  # letters only
        ("epoch 1696590000", "epoch <N>"),  # digits only
        ("short abc1234", "short abc1234"),  # fewer than 8 hex digits
    ],
)
def test_hex_rule(line: str, expected: str) -> None:
    assert mask_line(line) == expected


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("took 312ms", "took <N>ms"),
        ("[worker-3] retry 2/5", "[worker-<N>] retry <N>/<N>"),
        ("pi=3.14 n=-7", "pi=<N>.<N> n=-<N>"),
        ("utf8 sha256 x86_64 abc123", "utf8 sha256 x86_64 abc123"),  # identifiers keep digits
        ("no numbers here", "no numbers here"),
    ],
)
def test_number_rule(line: str, expected: str) -> None:
    assert mask_line(line) == expected


def test_similar_lines_share_a_template() -> None:
    lines = [
        "2026-10-06T12:00:01Z Connection timeout after 312ms to 10.0.0.4",
        "2026-10-06T12:00:09Z Connection timeout after 98ms to 10.0.0.7",
    ]
    assert {mask_line(line) for line in lines} == {"<TS> Connection timeout after <N>ms to <IP>"}


def test_tally_counts_files_first_and_last() -> None:
    tally = TemplateTally()
    tally.add("retry 1", "a.log", 3)
    tally.add("retry 2", "a.log", 8)
    tally.add("other", "a.log", 9)
    tally.add("retry 3", "b.log", 1)
    (template, stats), (other, _) = tally.most_common()
    assert (template, other) == ("retry <N>", "other")
    assert (stats.count, stats.files) == (3, 2)
    assert (stats.first_path, stats.first_line) == ("a.log", 3)
    assert (stats.last_path, stats.last_line) == ("b.log", 1)


def test_tally_keeps_first_seen_order_for_ties() -> None:
    tally = TemplateTally()
    for text in ("b", "a", "c"):
        tally.add(text, "x.log", 1)
    assert [template for template, _ in tally.most_common()] == ["b", "a", "c"]


def test_tally_bounds_distinct_templates() -> None:
    tally = TemplateTally(max_templates=2)
    for text in ("one", "two", "three", "four", "one"):
        tally.add(text, "x.log", 1)
    assert len(tally) == 2
    assert tally.other == 2  # "three" and "four"
    assert {t: s.count for t, s in tally.most_common()} == {"one": 2, "two": 1}
