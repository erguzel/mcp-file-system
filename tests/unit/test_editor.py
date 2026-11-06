import codecs
import stat
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from conftest import ContextFactory, write
from mcp_file_system.context import AppContext
from mcp_file_system.core import editor as editor_module
from mcp_file_system.core.editor import MAX_EDITS, FileEditor, atomic_write, normalize_newlines
from mcp_file_system.core.etag import compute_etag
from mcp_file_system.core.history import EditHistory
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

    @pytest.mark.parametrize(
        ("bom", "codec"),
        [
            (codecs.BOM_UTF16_BE, "utf-16-be"),
            (codecs.BOM_UTF16_LE, "utf-16-le"),
            (codecs.BOM_UTF32_BE, "utf-32-be"),
            (codecs.BOM_UTF32_LE, "utf-32-le"),
        ],
    )
    def test_utf16_and_utf32_keep_their_byte_order(
        self, ctx: AppContext, root: Path, bom: bytes, codec: str
    ) -> None:
        path = write(root / "a.txt", bom + "café\nx\n".encode(codec))
        ctx.editor.replace("a.txt", "café", "thé")
        assert path.read_bytes() == bom + "thé\nx\n".encode(codec)

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

    def test_unencodable_text_is_an_actionable_error(self, ctx: AppContext, root: Path) -> None:
        original, replacement = b"caf\xe9\n", "\u015feker"
        path = write(root / "a.txt", original)
        with pytest.raises(InvalidArgumentError, match="latin-1 cannot store") as error:
            ctx.editor.replace("a.txt", "caf", replacement, dry_run=True)
        assert replacement not in str(error.value)
        with pytest.raises(InvalidArgumentError, match="nothing was changed"):
            ctx.editor.multi_replace("a.txt", [edit("caf", replacement)])
        assert path.read_bytes() == original

    def test_huge_diff_is_cut_and_says_so(self, make_ctx: ContextFactory, root: Path) -> None:
        path = write(root / "big.txt", "".join(f"line {n}\n" for n in range(400)))
        ctx = make_ctx(max_output_chars=1000)
        result = ctx.editor.replace("big.txt", "line", "LINE", expected_count=None)
        assert result.applied
        assert result.truncated
        assert len(result.diff) <= 1000 + len("\n[diff truncated]")  # cut, then marked
        assert "[Diff truncated at the output limit; re-read the changed lines" in result.render()
        assert path.read_text().startswith("LINE 0\n")
        small = ctx.editor.replace("big.txt", "LINE 0", "line 0")
        assert not small.truncated

    def test_undo_diff_is_cut_and_says_so(self, make_ctx: ContextFactory, root: Path) -> None:
        write(root / "big.txt", "".join(f"line {n}\n" for n in range(400)))
        ctx = make_ctx(allow_write=True, max_output_chars=1000)
        ctx.editor.replace("big.txt", "line", "LINE", expected_count=None)
        result = ctx.editor.undo_last_edit("big.txt")
        assert result.truncated
        assert "[Diff truncated at the output limit" in result.render()

    def test_cut_preview_says_nothing_was_written(
        self, make_ctx: ContextFactory, root: Path
    ) -> None:
        write(root / "big.txt", "".join(f"line {n}\n" for n in range(400)))
        ctx = make_ctx(max_output_chars=1000)
        result = ctx.editor.replace("big.txt", "line", "LINE", expected_count=None, dry_run=True)
        assert result.truncated
        assert result.render().endswith(
            "[Diff preview truncated at the output limit; nothing was written.]"
        )
        assert "re-read" not in result.render()

    def test_creation_undo_cut_has_no_lines_to_re_read(
        self, make_ctx: ContextFactory, root: Path
    ) -> None:
        ctx = make_ctx(allow_write=True, max_output_chars=1000)
        ctx.editor.write("new.txt", "".join(f"line {n}\n" for n in range(400)))
        result = ctx.editor.undo_last_edit("new.txt")
        assert result.renamed_to is not None
        assert result.truncated
        assert result.render().endswith("[Diff truncated at the output limit.]")

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

        def diff_then_modify(self: object, *args: object, **kwargs: object) -> tuple[str, bool]:
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

    def test_marked_insertion_away_from_end_changes_nothing(
        self, ctx: AppContext, root: Path
    ) -> None:
        path = write(root / "f.txt", "a\nb\nc\n")
        before = etag(path)
        diff = "@@ -1,0 +2,1 @@\n+added-line\n\\ No newline at end of file\n"
        report = ctx.editor.apply_patch("f.txt", diff, expected_etag=before, dry_run=True)
        assert not report.applied
        assert [hunk.applied for hunk in report.hunks] == [False]
        with pytest.raises(NoMatchError) as error:
            ctx.editor.apply_patch("f.txt", diff, expected_etag=before)
        assert "added-line" not in str(error.value)
        assert path.read_bytes() == b"a\nb\nc\n"
        assert etag(path) == before

    def test_many_failing_hunks_are_listed_in_part(self, ctx: AppContext, root: Path) -> None:
        # The error and the dry-run report must not grow with the number of failed hunks.
        path = write(root / "f.txt", "a\n")
        diff = "".join(f"@@ -9,1 +9,1 @@\n-q{i}\n+Q\n" for i in range(50))
        with pytest.raises(NoMatchError) as error:
            ctx.editor.apply_patch("f.txt", diff, expected_etag=etag(path))
        assert str(error.value).count("hunk ") == 15
        assert "(+35 more)" in str(error.value)
        report = ctx.editor.apply_patch("f.txt", diff, expected_etag=etag(path), dry_run=True)
        rows = report.render().splitlines()
        assert rows[0].startswith("Dry run: 50 of 50 hunk(s) would fail")
        assert len(rows) == 17
        assert rows[-1] == "[+35 more failing hunks]"

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


def _escape(char: str) -> str:
    """The ASCII ``\\uXXXX`` escape for ``char``, as a regex replacement template takes it."""
    return f"\\u{ord(char):04x}"


# Each writer puts "caf" + the character in place of line 1, "alpha", of a.txt.
WRITERS: dict[str, Callable[[FileEditor, Path, str, bool], object]] = {
    "replace": lambda e, p, c, d: e.replace("a.txt", "alpha", f"caf{c}", dry_run=d),
    "replace-regex-escape": lambda e, p, c, d: e.replace(
        "a.txt", "alpha", f"caf{_escape(c)}", regex=True, dry_run=d
    ),
    "multi_replace": lambda e, p, c, d: e.multi_replace(
        "a.txt", [edit("alpha", f"caf{c}")], dry_run=d
    ),
    "edit_lines": lambda e, p, c, d: e.edit_lines(
        "a.txt", 1, 1, f"caf{c}", expected_etag=etag(p), dry_run=d
    ),
    "apply_patch": lambda e, p, c, d: e.apply_patch(
        "a.txt", f"@@ -1,1 +1,1 @@\n-alpha\n+caf{c}\n", expected_etag=etag(p), dry_run=d
    ),
    "write-overwrite": lambda e, p, c, d: e.write(
        "a.txt", f"caf{c}\nbeta\n", overwrite=True, expected_etag=etag(p), dry_run=d
    ),
}


# U+DCE9 stands for the byte 0xE9 under surrogateescape; U+D800 stands for no byte at all.
SURROGATES = [chr(0xDCE9), chr(0xD800)]
SURROGATE_IDS = ["escaped-byte", "high-surrogate"]


class TestNewTextCannotAddAByte:
    """A lone surrogate in new text would be written as a raw byte (``surrogateescape``)."""

    @pytest.mark.parametrize("dry_run", [False, True])
    @pytest.mark.parametrize("char", SURROGATES, ids=SURROGATE_IDS)
    @pytest.mark.parametrize(
        "original",
        [
            b"alpha\nbeta\n",
            codecs.BOM_UTF8 + b"alpha\nbeta\n",
            b"alpha\nbeta \xe9\n",
            # Broken, so the UTF-16 escape writer is in play (E-9).
            codecs.BOM_UTF16_LE + "alpha\n".encode("utf-16-le") + b"\x00\xd8",
        ],
        ids=["utf-8", "utf-8-sig", "latin-1", "utf-16-broken"],
    )
    @pytest.mark.parametrize("writer", list(WRITERS), ids=list(WRITERS))
    def test_is_refused_and_changes_nothing(
        self, ctx: AppContext, root: Path, writer: str, original: bytes, char: str, dry_run: bool
    ) -> None:
        path = write(root / "a.txt", original)
        with pytest.raises(InvalidArgumentError, match="nothing was changed") as error:
            WRITERS[writer](ctx.editor, path, char, dry_run)
        assert char not in str(error.value)
        assert _escape(char) not in str(error.value)
        assert path.read_bytes() == original

    @pytest.mark.parametrize("char", SURROGATES, ids=SURROGATE_IDS)
    def test_creating_is_refused(self, ctx: AppContext, root: Path, char: str) -> None:
        with pytest.raises(InvalidArgumentError, match="nothing was changed"):
            ctx.editor.write("new.txt", f"caf{char}\n")
        assert not (root / "new.txt").exists()

    # Valid UTF-8 for the sniffed prefix, so the late byte is read as a surrogate escape.
    UNDECODABLE = b"a = 1\n" * 2000 + b"blob = '\xff'\nb = 2\n"

    def test_the_files_own_byte_can_move(self, ctx: AppContext, root: Path) -> None:
        path = write(root / "a.py", self.UNDECODABLE)
        ctx.editor.replace("a.py", r"blob = '(.)'\nb = 2", r"b = 2\nblob = '\1'", regex=True)
        assert path.read_bytes() == b"a = 1\n" * 2000 + b"b = 2\nblob = '\xff'\n"

    def test_the_files_own_byte_cannot_be_copied(self, ctx: AppContext, root: Path) -> None:
        path = write(root / "a.py", self.UNDECODABLE)
        with pytest.raises(InvalidArgumentError, match="nothing was changed"):
            ctx.editor.replace("a.py", r"'(.)'", r"'\1\1'", regex=True)
        assert path.read_bytes() == self.UNDECODABLE


# Bytes that are not valid in their UTF-16 or UTF-32 encoding: a lone unit sits between two
# lines, a cut-off last unit (tail) ends the file.
BROKEN_UNITS = {
    "utf-16-le-lone-high": (codecs.BOM_UTF16_LE, "utf-16-le", b"\x00\xd8", False),
    "utf-16-le-lone-low": (codecs.BOM_UTF16_LE, "utf-16-le", b"\x80\xdc", False),
    "utf-16-be-lone-high": (codecs.BOM_UTF16_BE, "utf-16-be", b"\xd8\x00", False),
    "utf-16-le-tail": (codecs.BOM_UTF16_LE, "utf-16-le", b"\x41", True),
    "utf-32-le-out-of-range": (codecs.BOM_UTF32_LE, "utf-32-le", b"\x00\x00\x11\x00", False),
    "utf-32-be-surrogate": (codecs.BOM_UTF32_BE, "utf-32-be", b"\x00\x00\xd8\x00", False),
    "utf-32-le-tail": (codecs.BOM_UTF32_LE, "utf-32-le", b"\x41\x00", True),
}


def broken_file(name: str, first: str = "A", second: str = "B\n") -> bytes:
    """The file of :data:`BROKEN_UNITS` ``name`` with the given first and second line."""
    bom, codec, bad, tail = BROKEN_UNITS[name]
    head = f"{first}\n".encode(codec)
    if tail:
        return bom + head + second.encode(codec) + bad
    return bom + head + bad + second.encode(codec)


# Each tool puts "Z" in place of line 1, "A", of a.txt.
BROKEN_EDITS: dict[str, Callable[[FileEditor, Path], object]] = {
    "replace": lambda e, p: e.replace("a.txt", "A", "Z"),
    "replace-regex": lambda e, p: e.replace("a.txt", "[A]", "Z", regex=True),
    "multi_replace": lambda e, p: e.multi_replace("a.txt", [edit("A", "Z")]),
    "edit_lines": lambda e, p: e.edit_lines("a.txt", 1, 1, "Z", expected_etag=etag(p)),
    "apply_patch": lambda e, p: e.apply_patch(
        "a.txt", "@@ -1,1 +1,1 @@\n-A\n+Z\n", expected_etag=etag(p)
    ),
}

# Each edit of a.txt would leave bytes that read back differently than the diff shows.
BROKEN_SHIFTS: dict[str, tuple[bytes, Callable[[FileEditor, Path, bool], object]]] = {
    "text-after-tail": (
        broken_file("utf-16-le-tail", second="B"),
        lambda e, p, d: e.edit_lines("a.txt", 3, 2, "C", expected_etag=etag(p), dry_run=d),
    ),
    "moved-escape": (
        broken_file("utf-16-le-tail", second="B"),
        lambda e, p, d: e.replace("a.txt", "B(.)", r"\1B", regex=True, dry_run=d),
    ),
    "joined-units": (
        codecs.BOM_UTF16_LE + b"\x00\xd8" + "X".encode("utf-16-le") + b"\x00\xdc",
        lambda e, p, d: e.replace("a.txt", "X", "", dry_run=d),
    ),
}


class TestBrokenUtf16And32:
    """A UTF-16 or UTF-32 file keeps its broken units through an edit, like UTF-8 (E-9)."""

    @pytest.mark.parametrize("name", list(BROKEN_UNITS))
    def test_edit_keeps_the_broken_unit(self, ctx: AppContext, root: Path, name: str) -> None:
        path = write(root / "a.txt", broken_file(name))
        result = ctx.editor.replace("a.txt", "A", "Z")
        assert result.applied
        assert path.read_bytes() == broken_file(name, first="Z")

    @pytest.mark.parametrize(
        "name", ["utf-16-le-lone-high", "utf-16-le-lone-low", "utf-16-le-tail"]
    )
    @pytest.mark.parametrize("tool", list(BROKEN_EDITS))
    def test_every_editing_tool_edits_it(
        self, ctx: AppContext, root: Path, tool: str, name: str
    ) -> None:
        path = write(root / "a.txt", broken_file(name))
        BROKEN_EDITS[tool](ctx.editor, path)
        assert path.read_bytes() == broken_file(name, first="Z")

    @pytest.mark.parametrize("name", ["utf-16-le-lone-high", "utf-16-le-tail"])
    def test_overwrite_and_dry_run(self, ctx: AppContext, root: Path, name: str) -> None:
        original = broken_file(name)
        path = write(root / "a.txt", original)
        preview = ctx.editor.replace("a.txt", "A", "Z", dry_run=True)
        assert not preview.applied
        assert "+Z" in preview.diff
        assert path.read_bytes() == original
        ctx.editor.write("a.txt", "Z\nB\n", overwrite=True, expected_etag=etag(path))
        assert path.read_bytes() == codecs.BOM_UTF16_LE + "Z\nB\n".encode("utf-16-le")

    def test_whole_line_with_the_tail_can_be_replaced(self, ctx: AppContext, root: Path) -> None:
        # The way out the refusal names: edit the whole line that holds the U+FFFD.
        path = write(root / "a.txt", broken_file("utf-16-le-tail", second="B"))
        ctx.editor.edit_lines("a.txt", 2, 2, "B\nC", expected_etag=etag(path))
        assert path.read_bytes() == codecs.BOM_UTF16_LE + "A\nB\nC".encode("utf-16-le")

    @pytest.mark.parametrize("dry_run", [False, True])
    @pytest.mark.parametrize("case", list(BROKEN_SHIFTS))
    def test_shifting_the_broken_bytes_is_refused(
        self, ctx: AppContext, root: Path, case: str, dry_run: bool
    ) -> None:
        original, change = BROKEN_SHIFTS[case]
        path = write(root / "a.txt", original)
        with pytest.raises(InvalidArgumentError, match="nothing was changed") as error:
            change(ctx.editor, path, dry_run)
        assert "edit_lines" in str(error.value)
        assert path.read_bytes() == original

    @pytest.mark.parametrize("dry_run", [False, True])
    def test_a_start_that_reads_as_utf32_is_refused(
        self, ctx: AppContext, root: Path, dry_run: bool
    ) -> None:
        # FF FE 00 00 is the UTF-32-LE BOM, so the file would read as UTF-32 afterwards.
        original = codecs.BOM_UTF16_LE + "A\n".encode("utf-16-le")
        path = write(root / "a.txt", original)
        with pytest.raises(InvalidArgumentError, match="nothing was changed"):
            ctx.editor.replace("a.txt", "A", "\x00A", dry_run=dry_run)
        assert path.read_bytes() == original


FILLER = b"x" * 9000 + b"\n"  # pushes what follows past the 8 KiB the format is sniffed from

# Edits after which the file would look binary or start with another byte-order mark.
SNIFF_BREAKERS: dict[str, tuple[bytes | None, Callable[[FileEditor, bool], object]]] = {
    "utf-8-nul": (b"abc\n", lambda e, d: e.replace("a.txt", "b", "\x00", dry_run=d)),
    "create-nul": (None, lambda e, d: e.write("a.txt", "a\x00b\n", dry_run=d)),
    "utf-8-feff": (b"abc\n", lambda e, d: e.replace("a.txt", "abc", "﻿abc", dry_run=d)),
    "create-feff": (None, lambda e, d: e.write("a.txt", "﻿abc\n", dry_run=d)),
    "latin-1-utf-16-bom": (
        b"\xe9 line\n",
        lambda e, d: e.replace("a.txt", "é", "ÿþ", dry_run=d),
    ),
    "latin-1-utf-8-bom": (
        b"\xe9 line\n",
        lambda e, d: e.replace("a.txt", "é", "ï»¿", dry_run=d),
    ),
    "utf-8-own-bytes-first": (
        b"abc\n" + FILLER + b"\xff\xfe\n",
        lambda e, d: e.replace("a.txt", "(?s)^(.*)(\udcff\udcfe)", r"\2\1", regex=True, dry_run=d),
    ),
}


class TestSniffedEncodingStays:
    """An edit cannot leave a file that the next read takes as binary or another encoding (E-10)."""

    @pytest.mark.parametrize("dry_run", [False, True])
    @pytest.mark.parametrize("case", list(SNIFF_BREAKERS))
    def test_binary_or_new_bom_is_refused(
        self, ctx: AppContext, root: Path, case: str, dry_run: bool
    ) -> None:
        original, edit = SNIFF_BREAKERS[case]
        path = root / "a.txt"
        if original is not None:
            write(path, original)
        with pytest.raises(InvalidArgumentError, match="nothing was changed"):
            edit(ctx.editor, dry_run)
        if original is None:
            assert not path.exists()
        else:
            assert path.read_bytes() == original

    @pytest.mark.parametrize("dry_run", [False, True])
    def test_latin1_read_as_utf8_is_noted(self, ctx: AppContext, root: Path, dry_run: bool) -> None:
        # The first 8 KiB lose their only byte that is not valid UTF-8.
        original = b"\xe9\n" + FILLER + b"\xe9t\xe9\n"
        path = write(root / "a.txt", original)
        result = ctx.editor.edit_lines(
            "a.txt", 1, 1, "e\n", expected_etag=etag(path), dry_run=dry_run
        )
        assert result.note is not None
        assert "UTF-8" in result.note
        assert "Latin-1" in result.note
        assert result.note in result.render()
        expected = original if dry_run else b"e\n" + FILLER + b"\xe9t\xe9\n"
        assert path.read_bytes() == expected

    def test_utf8_read_as_latin1_is_noted(self, ctx: AppContext, root: Path) -> None:
        # Deleting the filler brings a stray byte into the first 8 KiB.
        path = write(root / "a.txt", "café\n".encode() + FILLER + b"\xff\n")
        result = ctx.editor.edit_lines("a.txt", 2, 2, "", expected_etag=etag(path))
        assert result.note is not None
        assert "UTF-8" in result.note
        assert "Latin-1" in result.note
        assert path.read_bytes() == "café\n".encode() + b"\xff\n"

    @pytest.mark.parametrize(
        ("original", "old", "new"),
        [
            pytest.param(b"\xe9t\xe9\n", "été", "ete", id="latin-1-now-ascii"),
            pytest.param("café\n".encode(), "caf", "th", id="utf-8"),
            pytest.param(b"\xe9t\xe9\n", "t", "s", id="latin-1"),
            pytest.param(codecs.BOM_UTF8 + b"abc\n", "abc", "xyz", id="utf-8-sig"),
        ],
    )
    def test_no_note_when_the_text_reads_the_same(
        self, ctx: AppContext, root: Path, original: bytes, old: str, new: str
    ) -> None:
        write(root / "a.txt", original)
        result = ctx.editor.replace("a.txt", old, new)
        assert result.applied
        assert result.note is None


class TestUndo:
    @pytest.fixture
    def writer(self, make_ctx: ContextFactory) -> AppContext:
        return make_ctx(allow_write=True)

    @pytest.mark.parametrize(
        "original",
        [
            b"x = 1\ny = 2\n",
            b"x = 1\r\ny = 2\r\n",
            codecs.BOM_UTF8 + b"x = 1\ny = 2\n",
            b"x = 1\n\xff\xfe junk\ny = 2\n",
            "x = 1 é\ny = 2\n".encode("latin-1"),
            codecs.BOM_UTF16_LE + "x = 1\n".encode("utf-16-le") + b"\x00\xd8\x41",
        ],
    )
    def test_restores_exact_bytes(self, writer: AppContext, root: Path, original: bytes) -> None:
        path = write(root / "a.py", original)
        writer.editor.replace("a.py", "1", "100")
        assert path.read_bytes() != original
        result = writer.editor.undo_last_edit("a.py")
        assert path.read_bytes() == original
        assert result.applied
        assert result.etag == etag(path)
        assert result.render().startswith("Restored a.py to the version before the last edit")

    def test_every_edit_tool_is_undoable(self, writer: AppContext, root: Path) -> None:
        path = write(root / "a.txt", "a\nb\n")
        steps: list[Callable[[], object]] = [
            lambda: writer.editor.edit_lines("a.txt", 1, 1, "z", expected_etag=etag(path)),
            lambda: writer.editor.multi_replace("a.txt", [edit("b", "c")]),
            lambda: writer.editor.apply_patch(
                "a.txt", "@@ -1,1 +1,1 @@\n-a\n+q\n", expected_etag=etag(path)
            ),
            lambda: writer.editor.write("a.txt", "new\n", overwrite=True, expected_etag=etag(path)),
        ]
        for step in steps:
            step()
            assert path.read_text() != "a\nb\n"
            writer.editor.undo_last_edit("a.txt")
            assert path.read_text() == "a\nb\n"

    def test_only_the_latest_edit_and_only_once(self, writer: AppContext, root: Path) -> None:
        path = write(root / "a.py", "x = 1\n")
        writer.editor.replace("a.py", "1", "2")
        writer.editor.replace("a.py", "2", "3")
        writer.editor.undo_last_edit("a.py")
        assert path.read_text() == "x = 2\n"
        with pytest.raises(InvalidArgumentError, match=r"No edit of a\.py by this server"):
            writer.editor.undo_last_edit("a.py")

    def test_external_change_is_refused(self, writer: AppContext, root: Path) -> None:
        path = write(root / "a.py", "x = 1\n")
        writer.editor.replace("a.py", "1", "2")
        write(path, "x = 2  # edited by hand\n")
        with pytest.raises(StaleFileError, match="undo refused"):
            writer.editor.undo_last_edit("a.py")
        assert path.read_text() == "x = 2  # edited by hand\n"

    def test_dry_run(self, writer: AppContext, root: Path) -> None:
        path = write(root / "a.py", "x = 1\n")
        writer.editor.replace("a.py", "1", "2")
        result = writer.editor.undo_last_edit("a.py", dry_run=True)
        assert path.read_text() == "x = 2\n"
        assert "-x = 2\n+x = 1" in result.diff
        assert result.render().startswith("Dry run: a.py would be restored")
        writer.editor.undo_last_edit("a.py")  # the dry run kept the snapshot
        assert path.read_text() == "x = 1\n"

    def test_dry_runs_are_not_recorded(self, writer: AppContext, root: Path) -> None:
        write(root / "a.py", "x = 1\n")
        writer.editor.replace("a.py", "1", "2", dry_run=True)
        with pytest.raises(InvalidArgumentError, match="No edit"):
            writer.editor.undo_last_edit("a.py")

    def test_created_file_is_renamed_aside(
        self, writer: AppContext, root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(editor_module, "_utc_stamp", lambda: "20261009T120000Z")
        write(root / "new.txt.deleted-20261009T120000Z", "older\n")
        writer.editor.write("new.txt", "fresh\n")
        preview = writer.editor.undo_last_edit("new.txt", dry_run=True)
        assert preview.renamed_to == "new.txt.deleted-20261009T120000Z-2"
        assert (root / "new.txt").exists()
        result = writer.editor.undo_last_edit("new.txt")
        assert result.renamed_to == "new.txt.deleted-20261009T120000Z-2"
        assert not (root / "new.txt").exists()
        assert (root / "new.txt.deleted-20261009T120000Z-2").read_text() == "fresh\n"
        assert (root / "new.txt.deleted-20261009T120000Z").read_text() == "older\n"
        assert "renamed to new.txt.deleted-20261009T120000Z-2" in result.render()

    def test_race_before_rename_removes_placeholder(
        self, writer: AppContext, root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        writer.editor.write("new.txt", "fresh\n")

        def changed(*_args: object) -> None:
            raise StaleFileError("changed")

        monkeypatch.setattr(editor_module, "ensure_etag", changed)
        with pytest.raises(StaleFileError):
            writer.editor.undo_last_edit("new.txt")
        assert [p.name for p in root.iterdir()] == ["new.txt"]

    def test_no_free_name(
        self, writer: AppContext, root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(editor_module, "_utc_stamp", lambda: "T")
        monkeypatch.setattr(editor_module, "MAX_RENAME_ATTEMPTS", 1)
        write(root / "new.txt.deleted-T", "taken\n")
        writer.editor.write("new.txt", "fresh\n")
        for dry_run in (True, False):
            with pytest.raises(AlreadyExistsError, match="free name"):
                writer.editor.undo_last_edit("new.txt", dry_run=dry_run)
        assert (root / "new.txt").read_text() == "fresh\n"

    def test_denied_rename_target_changes_nothing(self, writer: AppContext, root: Path) -> None:
        writer.editor.write(".env.example", "A=\n")
        for _ in range(2):  # the snapshot survives the refusal
            with pytest.raises(AccessDeniedError, match="deny list blocks; nothing was changed"):
                writer.editor.undo_last_edit(".env.example")
        assert (root / ".env.example").read_text() == "A=\n"
        assert [p.name for p in root.iterdir()] == [".env.example"]

    def test_too_large_to_keep(self, writer: AppContext, root: Path) -> None:
        write(root / "a.py", "x = 1\n")
        history = EditHistory(max_bytes=3)
        editor = FileEditor(writer.guard, writer.settings, history)
        editor.replace("a.py", "1", "2")
        with pytest.raises(InvalidArgumentError, match="too large to keep"):
            editor.undo_last_edit("a.py")

    @pytest.mark.parametrize(
        "overrides",
        [
            {"allow_write": False},
            {"allow_write": True, "profile": "lite"},
            {"allow_write": True, "tools": ("replace_in_file",)},
        ],
    )
    def test_no_history_without_the_tool(
        self, make_ctx: ContextFactory, root: Path, overrides: dict[str, object]
    ) -> None:
        write(root / "a.py", "x = 1\n")
        ctx = make_ctx(**overrides)
        ctx.editor.replace("a.py", "1", "2")
        with pytest.raises(InvalidArgumentError, match="not enabled"):
            ctx.editor.undo_last_edit("a.py")

    def test_guards(self, writer: AppContext, root: Path, outside: Path) -> None:
        with pytest.raises(PathOutsideRootError):
            writer.editor.undo_last_edit(str(outside / "secret.txt"))


class TestWrite:
    def test_create_with_parents(self, ctx: AppContext, root: Path) -> None:
        result = ctx.editor.write("new/dir/file.py", "x = 1\n")
        path = root / "new" / "dir" / "file.py"
        assert path.read_text() == "x = 1\n"
        assert result.created
        assert "--- /dev/null" in result.diff
        assert result.render().startswith("Created new/dir/file.py")

    def test_vanished_file_with_etag_is_stale(self, ctx: AppContext, root: Path) -> None:
        # An etag says "replace what I read"; if that file is gone, creating a new one would
        # hide the loss. Without an etag the call is a plain create and still succeeds.
        path = write(root / "gone.py", "x = 1\n")
        before = etag(path)
        path.unlink()
        with pytest.raises(StaleFileError, match="no longer exists"):
            ctx.editor.write("gone.py", "y = 2\n", overwrite=True, expected_etag=before)
        assert not path.exists()
        assert ctx.editor.write("gone.py", "y = 2\n").created

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

        def diff_then_create(self: object, *args: object, **kwargs: object) -> tuple[str, bool]:
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
