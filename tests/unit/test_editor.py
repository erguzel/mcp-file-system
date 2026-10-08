import codecs
import stat
import sys
from pathlib import Path

import pytest

from conftest import ContextFactory, write
from mcp_file_system.context import AppContext
from mcp_file_system.core import editor as editor_module
from mcp_file_system.core.editor import MAX_EDITS, atomic_write, normalize_newlines
from mcp_file_system.core.etag import compute_etag
from mcp_file_system.core.models import Replacement
from mcp_file_system.errors import (
    AccessDeniedError,
    AlreadyExistsError,
    BinaryFileError,
    FileTooLargeError,
    InvalidArgumentError,
    InvalidPatternError,
    MatchCountError,
    NoMatchError,
    PathOutsideRootError,
    PathTypeError,
    StaleFileError,
)


def etag(path: Path) -> str:
    return compute_etag(path.stat())


class TestReplace:
    def test_unique_literal_replacement(self, ctx: AppContext, root: Path) -> None:
        path = write(root / "a.py", "x = 1\ny = 2\n")
        result = ctx.editor.replace("a.py", "y = 2", "y = 3")
        assert path.read_text() == "x = 1\ny = 3\n"
        assert result.applied
        assert result.changes == 1
        assert result.etag == etag(path)
        assert "-y = 2\n+y = 3" in result.diff
        assert result.render().startswith("Applied 1 change(s) to a.py")

    def test_count_mismatch_is_rejected(self, ctx: AppContext, root: Path) -> None:
        path = write(root / "a.py", "v\nv\n")
        with pytest.raises(MatchCountError, match="2 time"):
            ctx.editor.replace("a.py", "v", "w")
        assert path.read_text() == "v\nv\n"

    def test_expected_count_and_any_count(self, ctx: AppContext, root: Path) -> None:
        path = write(root / "a.py", "v\nv\nv\n")
        ctx.editor.replace("a.py", "v", "w", expected_count=3)
        assert path.read_text() == "w\nw\nw\n"
        ctx.editor.replace("a.py", "w", "z", expected_count=None)
        assert path.read_text() == "z\nz\nz\n"

    def test_no_match(self, ctx: AppContext, root: Path) -> None:
        write(root / "a.py", "x\n")
        with pytest.raises(NoMatchError):
            ctx.editor.replace("a.py", "nope", "y")

    def test_regex_with_groups(self, ctx: AppContext, root: Path) -> None:
        path = write(root / "a.py", "foo(1)\nfoo(2)\n")
        ctx.editor.replace("a.py", r"foo\((\d)\)", r"bar(\1)", regex=True, expected_count=2)
        assert path.read_text() == "bar(1)\nbar(2)\n"

    def test_invalid_regex_template(self, ctx: AppContext, root: Path) -> None:
        write(root / "a.py", "foo\n")
        with pytest.raises(InvalidPatternError):
            ctx.editor.replace("a.py", "foo", r"\5", regex=True)

    def test_ignore_case_literal(self, ctx: AppContext, root: Path) -> None:
        path = write(root / "a.txt", "Hello (World)\n")
        ctx.editor.replace("a.txt", "hello (world)", "bye", ignore_case=True)
        assert path.read_text() == "bye\n"

    def test_literal_newlines_follow_file_style(self, ctx: AppContext, root: Path) -> None:
        path = write(root / "a.txt", b"one\r\ntwo\r\nthree\r\n")
        ctx.editor.replace("a.txt", "one\ntwo", "1\n2")
        assert path.read_bytes() == b"1\r\n2\r\nthree\r\n"

    def test_bom_and_encoding_are_preserved(self, ctx: AppContext, root: Path) -> None:
        path = write(root / "a.txt", codecs.BOM_UTF8 + "café\n".encode())
        ctx.editor.replace("a.txt", "café", "thé")
        assert path.read_bytes() == codecs.BOM_UTF8 + "thé\n".encode()

    def test_undecodable_bytes_round_trip(self, ctx: AppContext, root: Path) -> None:
        # Valid UTF-8 for the sniffed prefix, an invalid byte much later.
        original = b"a = 1\n" * 2000 + b"blob = '\xff'\nb = 2\n"
        path = write(root / "a.py", original)
        result = ctx.editor.replace("a.py", "b = 2", "b = 3")
        assert path.read_bytes() == original.replace(b"b = 2", b"b = 3")
        assert "�" not in result.diff or "b = 3" in result.diff

    def test_latin1_file(self, ctx: AppContext, root: Path) -> None:
        path = write(root / "a.txt", b"caf\xe9\n")
        ctx.editor.replace("a.txt", "café", "bar")
        assert path.read_bytes() == b"bar\n"

    def test_dry_run_does_not_write(self, ctx: AppContext, root: Path) -> None:
        path = write(root / "a.py", "x = 1\n")
        before = etag(path)
        result = ctx.editor.replace("a.py", "1", "2", dry_run=True)
        assert path.read_text() == "x = 1\n"
        assert not result.applied
        assert result.etag == before
        assert result.render().startswith("Dry run: 1 change(s)")

    def test_stale_etag_is_rejected(self, ctx: AppContext, root: Path) -> None:
        write(root / "a.py", "x = 1\n")
        with pytest.raises(StaleFileError, match="Re-read"):
            ctx.editor.replace("a.py", "1", "2", expected_etag="0-0")

    def test_matching_etag_is_accepted(self, ctx: AppContext, root: Path) -> None:
        path = write(root / "a.py", "x = 1\n")
        ctx.editor.replace("a.py", "1", "2", expected_etag=etag(path))
        assert path.read_text() == "x = 2\n"

    def test_invalid_arguments(self, ctx: AppContext, root: Path) -> None:
        write(root / "a.py", "x\n")
        with pytest.raises(InvalidPatternError):
            ctx.editor.replace("a.py", "", "y")
        with pytest.raises(InvalidArgumentError):
            ctx.editor.replace("a.py", "x", "y", expected_count=0)

    def test_binary_and_too_large_files(self, make_ctx: ContextFactory, root: Path) -> None:
        write(root / "b.bin", b"\x00x")
        write(root / "big.txt", "x" * 200)
        ctx = make_ctx(max_file_bytes=100)
        with pytest.raises(BinaryFileError):
            ctx.editor.replace("b.bin", "x", "y")
        with pytest.raises(FileTooLargeError):
            ctx.editor.replace("big.txt", "x", "y", expected_count=None)

    def test_result_growing_past_limit_is_rejected(
        self, make_ctx: ContextFactory, root: Path
    ) -> None:
        path = write(root / "a.txt", "x\n")
        with pytest.raises(FileTooLargeError):
            make_ctx(max_file_bytes=50).editor.replace("a.txt", "x", "y" * 100)
        assert path.read_text() == "x\n"

    def test_denied_and_outside_paths(self, ctx: AppContext, root: Path, outside: Path) -> None:
        write(root / ".env", "A=1\n")
        with pytest.raises(AccessDeniedError):
            ctx.editor.replace(".env", "1", "2")
        with pytest.raises(PathOutsideRootError):
            ctx.editor.replace(str(outside / "secret.txt"), "top", "x")


def edit(pattern: str, replacement: str, expected_count: int = 1) -> Replacement:
    return Replacement(pattern=pattern, replacement=replacement, expected_count=expected_count)


class TestMultiReplace:
    def test_edits_apply_in_order(self, ctx: AppContext, root: Path) -> None:
        path = write(root / "a.py", "x = a\ny = b\n")
        result = ctx.editor.multi_replace("a.py", [edit("a", "b"), edit("b", "c", 2)])
        assert path.read_text() == "x = c\ny = c\n"
        assert result.changes == 3
        assert result.etag == etag(path)
        assert "+x = c" in result.diff

    def test_failing_edit_leaves_file_unchanged(self, ctx: AppContext, root: Path) -> None:
        path = write(root / "a.py", "x = 1\ny = 2\n")
        before = etag(path)
        edits = [edit("1", "10"), edit("missing", "z"), edit("2", "20")]
        with pytest.raises(NoMatchError, match=r"^edit 2 of 3: Pattern not found in a\.py"):
            ctx.editor.multi_replace("a.py", edits)
        assert path.read_text() == "x = 1\ny = 2\n"
        assert etag(path) == before

    def test_count_mismatch_names_the_edit(self, ctx: AppContext, root: Path) -> None:
        write(root / "a.py", "a a\n")
        with pytest.raises(MatchCountError, match=r"^edit 1 of 1: Pattern matches 2 time"):
            ctx.editor.multi_replace("a.py", [edit("a", "b")])

    def test_zero_expected_count_means_any(self, ctx: AppContext, root: Path) -> None:
        path = write(root / "a.py", "a a a\n")
        ctx.editor.multi_replace("a.py", [edit("a", "b", 0)])
        assert path.read_text() == "b b b\n"

    def test_newlines_follow_file_style(self, ctx: AppContext, root: Path) -> None:
        path = write(root / "a.txt", "one\r\ntwo\r\n")
        ctx.editor.multi_replace("a.txt", [edit("one\ntwo", "1\n2")])
        assert path.read_bytes() == b"1\r\n2\r\n"

    def test_growth_is_checked_before_each_edit(self, make_ctx: ContextFactory, root: Path) -> None:
        path = write(root / "a.txt", "a\n")
        grow = edit("a", "a" * 10, 0)
        with pytest.raises(FileTooLargeError, match=r"^edit 2 of 3: the result would exceed"):
            make_ctx(max_file_bytes=100).editor.multi_replace("a.txt", [grow, grow, grow])
        assert path.read_text() == "a\n"

    def test_dry_run_and_etag(self, ctx: AppContext, root: Path) -> None:
        path = write(root / "a.py", "x = 1\n")
        result = ctx.editor.multi_replace(
            "a.py", [edit("1", "2")], expected_etag=etag(path), dry_run=True
        )
        assert not result.applied
        assert path.read_text() == "x = 1\n"
        with pytest.raises(StaleFileError):
            ctx.editor.multi_replace("a.py", [edit("1", "2")], expected_etag="0-0")

    def test_edit_count_limits(self, ctx: AppContext, root: Path) -> None:
        write(root / "a.py", "x\n")
        with pytest.raises(InvalidArgumentError, match="between 1 and"):
            ctx.editor.multi_replace("a.py", [])
        with pytest.raises(InvalidArgumentError, match="between 1 and"):
            ctx.editor.multi_replace("a.py", [edit("x", "x")] * (MAX_EDITS + 1))


class TestEditLines:
    def test_replace_range(self, ctx: AppContext, root: Path) -> None:
        path = write(root / "f.txt", "a\nb\nc\nd\n")
        result = ctx.editor.edit_lines("f.txt", 2, 3, "B\nC", expected_etag=etag(path))
        assert path.read_text() == "a\nB\nC\nd\n"
        assert result.changes == 2

    def test_insert_before_line(self, ctx: AppContext, root: Path) -> None:
        path = write(root / "f.txt", "a\nb\n")
        ctx.editor.edit_lines("f.txt", 2, 1, "inserted", expected_etag=etag(path))
        assert path.read_text() == "a\ninserted\nb\n"

    def test_append_at_end(self, ctx: AppContext, root: Path) -> None:
        path = write(root / "f.txt", "a\n")
        ctx.editor.edit_lines("f.txt", 2, 1, "b\n", expected_etag=etag(path))
        assert path.read_text() == "a\nb\n"

    def test_append_keeps_missing_final_newline_missing(self, ctx: AppContext, root: Path) -> None:
        path = write(root / "f.txt", "a")
        ctx.editor.edit_lines("f.txt", 2, 1, "b", expected_etag=etag(path))
        assert path.read_text() == "a\nb"

    def test_delete_range(self, ctx: AppContext, root: Path) -> None:
        path = write(root / "f.txt", "a\nb\nc\n")
        ctx.editor.edit_lines("f.txt", 2, 2, "", expected_etag=etag(path))
        assert path.read_text() == "a\nc\n"

    def test_replacing_unterminated_last_line_keeps_it_unterminated(
        self, ctx: AppContext, root: Path
    ) -> None:
        path = write(root / "f.txt", "a\nb")
        ctx.editor.edit_lines("f.txt", 2, 2, "B", expected_etag=etag(path))
        assert path.read_text() == "a\nB"

    def test_crlf_is_preserved(self, ctx: AppContext, root: Path) -> None:
        path = write(root / "f.txt", b"a\r\nb\r\n")
        ctx.editor.edit_lines("f.txt", 1, 1, "x\ny", expected_etag=etag(path))
        assert path.read_bytes() == b"x\r\ny\r\nb\r\n"

    def test_empty_file(self, ctx: AppContext, root: Path) -> None:
        path = write(root / "f.txt", "")
        ctx.editor.edit_lines("f.txt", 1, 0, "first", expected_etag=etag(path))
        assert path.read_text() == "first\n"

    @pytest.mark.parametrize(("start", "end"), [(0, 0), (5, 5), (2, 0), (1, 4)])
    def test_invalid_ranges(self, ctx: AppContext, root: Path, start: int, end: int) -> None:
        path = write(root / "f.txt", "a\nb\nc\n")
        with pytest.raises(InvalidArgumentError):
            ctx.editor.edit_lines("f.txt", start, end, "x", expected_etag=etag(path))

    def test_stale_etag(self, ctx: AppContext, root: Path) -> None:
        path = write(root / "f.txt", "a\n")
        old = etag(path)
        write(path, "changed underneath\n")
        with pytest.raises(StaleFileError):
            ctx.editor.edit_lines("f.txt", 1, 1, "x", expected_etag=old)
        assert path.read_text() == "changed underneath\n"

    def test_dry_run(self, ctx: AppContext, root: Path) -> None:
        path = write(root / "f.txt", "a\n")
        result = ctx.editor.edit_lines("f.txt", 1, 1, "b", expected_etag=etag(path), dry_run=True)
        assert path.read_text() == "a\n"
        assert "-a\n+b" in result.diff

    def test_concurrent_change_between_load_and_write(
        self, ctx: AppContext, root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = write(root / "f.txt", "a\n")
        original_diff = editor_module.FileEditor._diff

        def diff_then_modify(self: object, *args: object, **kwargs: object) -> str:
            write(path, "someone else wrote this\n")
            return original_diff(self, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(editor_module.FileEditor, "_diff", diff_then_modify)
        with pytest.raises(StaleFileError):
            ctx.editor.edit_lines("f.txt", 1, 1, "b", expected_etag=etag(path))
        assert path.read_text() == "someone else wrote this\n"


class TestApplyPatch:
    DIFF = "@@ -2,1 +2,1 @@\n-b\n+B\n"

    def test_applies_and_returns_diff(self, ctx: AppContext, root: Path) -> None:
        path = write(root / "f.txt", "a\nb\nc\n")
        result = ctx.editor.apply_patch("f.txt", self.DIFF, expected_etag=etag(path))
        assert path.read_text() == "a\nB\nc\n"
        assert result.applied
        assert result.etag == etag(path)
        assert "-b\n+B" in result.diff
        assert result.render().startswith("Applied 1 change(s) to f.txt")

    def test_moved_hunk_is_reported(self, ctx: AppContext, root: Path) -> None:
        path = write(root / "f.txt", "x\ny\na\nb\nc\n")
        result = ctx.editor.apply_patch("f.txt", self.DIFF, expected_etag=etag(path))
        assert "[hunk 1 applied at line 4 (offset +2)]" in result.render()

    def test_failing_hunk_leaves_file_unchanged(self, ctx: AppContext, root: Path) -> None:
        path = write(root / "f.txt", "a\nb\nc\nd\n")
        before = etag(path)
        diff = (
            "@@ -1,1 +1,1 @@\n-a\n+A\n"
            "@@ -2,1 +2,1 @@ def hidden():\n-zzz\n+Z\n"
            "@@ -4,1 +4,1 @@\n-d\n+D\n"
        )
        with pytest.raises(NoMatchError) as error:
            ctx.editor.apply_patch("f.txt", diff, expected_etag=before)
        message = str(error.value)
        assert "hunk 2 (@@ -2,1 +2,1 @@): context not found" in message
        assert "hunk 1" not in message
        assert "hidden" not in message
        assert "zzz" not in message
        assert path.read_text() == "a\nb\nc\nd\n"
        assert etag(path) == before

    def test_dry_run_reports_failing_hunks(self, ctx: AppContext, root: Path) -> None:
        path = write(root / "f.txt", "a\nb\n")
        diff = f"{self.DIFF}@@ -9,1 +9,1 @@\n-q\n+Q\n"
        result = ctx.editor.apply_patch("f.txt", diff, expected_etag=etag(path), dry_run=True)
        assert not result.applied
        assert [hunk.applied for hunk in result.hunks] == [True, False]
        assert result.render() == (
            "Dry run: 1 of 2 hunk(s) would fail on f.txt; nothing would be applied.\n"
            "[hunk 2 @@ -9,1 +9,1 @@: context not found]"
        )
        assert path.read_text() == "a\nb\n"

    def test_dry_run_success(self, ctx: AppContext, root: Path) -> None:
        path = write(root / "f.txt", "a\nb\n")
        result = ctx.editor.apply_patch("f.txt", self.DIFF, expected_etag=etag(path), dry_run=True)
        assert result.render().startswith("Dry run: 1 change(s) would be applied")
        assert path.read_text() == "a\nb\n"

    def test_crlf_file_gets_crlf_lines(self, ctx: AppContext, root: Path) -> None:
        path = write(root / "f.txt", "a\r\nb\r\n")
        ctx.editor.apply_patch("f.txt", "@@ -1,1 +1,2 @@\n a\n+new\n", expected_etag=etag(path))
        assert path.read_bytes() == b"a\r\nnew\r\nb\r\n"

    def test_guards(self, make_ctx: ContextFactory, root: Path, outside: Path) -> None:
        path = write(root / "f.txt", "a\nb\n")
        write(root / ".env", "b\n")
        ctx = make_ctx(max_file_bytes=50)
        with pytest.raises(StaleFileError):
            ctx.editor.apply_patch("f.txt", self.DIFF, expected_etag="0-0")
        with pytest.raises(AccessDeniedError):
            ctx.editor.apply_patch(".env", self.DIFF, expected_etag="0-0")
        with pytest.raises(PathOutsideRootError):
            ctx.editor.apply_patch(str(outside / "secret.txt"), self.DIFF, expected_etag="0-0")
        with pytest.raises(InvalidArgumentError, match="larger than max_file_bytes"):
            ctx.editor.apply_patch("f.txt", self.DIFF + " x\n" * 30, expected_etag=etag(path))


class TestWrite:
    def test_create_with_parents(self, ctx: AppContext, root: Path) -> None:
        result = ctx.editor.write("new/dir/file.py", "x = 1\n")
        path = root / "new" / "dir" / "file.py"
        assert path.read_text() == "x = 1\n"
        assert result.created
        assert "--- /dev/null" in result.diff
        assert result.render().startswith("Created new/dir/file.py")

    def test_existing_file_requires_overwrite_and_etag(self, ctx: AppContext, root: Path) -> None:
        path = write(root / "a.txt", "old\n")
        with pytest.raises(AlreadyExistsError):
            ctx.editor.write("a.txt", "new\n")
        with pytest.raises(InvalidArgumentError, match="expected_etag"):
            ctx.editor.write("a.txt", "new\n", overwrite=True)
        with pytest.raises(StaleFileError):
            ctx.editor.write("a.txt", "new\n", overwrite=True, expected_etag="0-0")
        ctx.editor.write("a.txt", "new\n", overwrite=True, expected_etag=etag(path))
        assert path.read_text() == "new\n"

    def test_overwrite_keeps_newline_style(self, ctx: AppContext, root: Path) -> None:
        path = write(root / "a.txt", b"old\r\n")
        ctx.editor.write("a.txt", "new\nfile\n", overwrite=True, expected_etag=etag(path))
        assert path.read_bytes() == b"new\r\nfile\r\n"

    def test_directory_target(self, ctx: AppContext, root: Path) -> None:
        (root / "d").mkdir()
        with pytest.raises(PathTypeError):
            ctx.editor.write("d", "x")

    def test_dry_run_create(self, ctx: AppContext, root: Path) -> None:
        result = ctx.editor.write("n.txt", "x\n", dry_run=True)
        assert not (root / "n.txt").exists()
        assert result.dry_run
        assert result.etag is None

    def test_race_where_file_appears(
        self, ctx: AppContext, root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        original_diff = editor_module.FileEditor._diff

        def diff_then_create(self: object, *args: object, **kwargs: object) -> str:
            write(root / "n.txt", "raced\n")
            return original_diff(self, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(editor_module.FileEditor, "_diff", diff_then_create)
        with pytest.raises(AlreadyExistsError):
            ctx.editor.write("n.txt", "mine\n")
        assert (root / "n.txt").read_text() == "raced\n"

    def test_denied_and_outside_targets(self, ctx: AppContext, outside: Path) -> None:
        with pytest.raises(AccessDeniedError):
            ctx.editor.write("keys/server.pem", "x")
        with pytest.raises(PathOutsideRootError):
            ctx.editor.write("../outside/evil.txt", "x")
        assert not (outside / "evil.txt").exists()

    def test_diff_is_truncated(self, make_ctx: ContextFactory, root: Path) -> None:
        result = make_ctx(max_output_chars=1000).editor.write("big.txt", "line\n" * 1000)
        assert "[diff truncated]" in result.diff


class TestAtomicWrite:
    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
    def test_permissions_are_preserved(self, tmp_path: Path) -> None:  # pragma: no cover
        path = write(tmp_path / "script.sh", "#!/bin/sh\n")
        path.chmod(0o750)
        atomic_write(path, b"#!/bin/sh\necho hi\n")
        assert stat.S_IMODE(path.stat().st_mode) == 0o750
        assert path.read_bytes() == b"#!/bin/sh\necho hi\n"

    def test_new_file_and_no_leftovers(self, tmp_path: Path) -> None:
        path = tmp_path / "new.txt"
        atomic_write(path, b"data")
        assert path.read_bytes() == b"data"
        assert sorted(p.name for p in tmp_path.iterdir()) == ["new.txt"]

    def test_temp_file_is_removed_on_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = write(tmp_path / "a.txt", "old")

        def fail(self: Path, target: Path) -> Path:
            raise OSError("disk full")

        monkeypatch.setattr(Path, "replace", fail)
        with pytest.raises(OSError, match="disk full"):
            atomic_write(path, b"new")
        assert path.read_text() == "old"
        assert sorted(p.name for p in tmp_path.iterdir()) == ["a.txt"]


@pytest.mark.parametrize(
    ("text", "newline", "expected"),
    [("a\nb\r\nc\rd", "\r\n", "a\r\nb\r\nc\r\nd"), ("a\r\nb", "\n", "a\nb")],
)
def test_normalize_newlines(text: str, newline: str, expected: str) -> None:
    assert normalize_newlines(text, newline) == expected
