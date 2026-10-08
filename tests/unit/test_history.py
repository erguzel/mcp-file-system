from pathlib import Path

from mcp_file_system.core.history import EditHistory, Snapshot


def test_latest_snapshot_per_file_wins() -> None:
    history = EditHistory()
    history.record(Path("/a"), b"one", "e1")
    history.record(Path("/a"), b"two", "e2")
    assert history.get(Path("/a")) == Snapshot(before=b"two", after_etag="e2")
    history.discard(Path("/a"))
    assert history.get(Path("/a")) is None


def test_created_files_have_no_previous_bytes() -> None:
    history = EditHistory()
    history.record(Path("/new"), None, "e1")
    snapshot = history.get(Path("/new"))
    assert snapshot is not None
    assert snapshot.before is None
    assert snapshot.kept


def test_least_recently_edited_file_is_dropped_by_count() -> None:
    history = EditHistory(max_files=2)
    for name in ("a", "b", "c"):
        history.record(Path(f"/{name}"), b"x", "e")
    assert history.get(Path("/a")) is None
    assert history.get(Path("/b")) is not None
    assert history.get(Path("/c")) is not None


def test_least_recently_edited_file_is_dropped_by_bytes() -> None:
    history = EditHistory(max_bytes=10)
    history.record(Path("/a"), b"123456", "e")
    history.record(Path("/b"), b"123456", "e")
    assert history.get(Path("/a")) is None
    assert history.get(Path("/b")) is not None
    history.record(Path("/b"), b"1234567890", "e")  # replacing frees the old bytes first
    assert history.get(Path("/b")) == Snapshot(before=b"1234567890", after_etag="e")


def test_oversized_snapshot_is_not_kept() -> None:
    history = EditHistory(max_bytes=10)
    history.record(Path("/a"), b"small", "e1")
    history.record(Path("/b"), b"kept", "e")
    history.record(Path("/a"), b"x" * 11, "e2")
    assert history.get(Path("/a")) == Snapshot(before=None, after_etag="e2", kept=False)
    assert history.get(Path("/b")) is not None  # nothing else was evicted for it
