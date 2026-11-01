import pytest
from hypothesis import given
from hypothesis import strategies as st

from mcp_file_system.core.models import HunkReport
from mcp_file_system.core.patch import MAX_HUNKS, PatchOutcome, apply_hunks, parse_patch
from mcp_file_system.core.textio import split_lines_keepends
from mcp_file_system.errors import InvalidArgumentError

FILE = "".join(f"line {n}\n" for n in range(1, 11))
MARKER = "\\ No newline at end of file\n"


def patch(text: str, diff: str, newline: str = "\n") -> PatchOutcome:
    return apply_hunks(split_lines_keepends(text), parse_patch(diff), newline)


def failure(outcome: PatchOutcome) -> HunkReport:
    assert outcome.text is None
    failed = [report for report in outcome.reports if not report.applied]
    assert len(failed) == 1
    return failed[0]


class TestApply:
    def test_hunk_count_is_capped(self) -> None:
        hunk = "@@ -1,1 +1,1 @@\n-line 1\n+one\n"
        assert len(parse_patch(hunk * MAX_HUNKS)) == MAX_HUNKS
        with pytest.raises(InvalidArgumentError, match=f"at most {MAX_HUNKS} hunks"):
            parse_patch(hunk * (MAX_HUNKS + 1))

    def test_exact_position(self) -> None:
        outcome = patch(FILE, "@@ -2,2 +2,2 @@\n line 2\n-line 3\n+third\n")
        assert outcome.text == FILE.replace("line 3\n", "third\n")
        assert outcome.reports == [
            HunkReport(index=1, header="@@ -2,2 +2,2 @@", applied=True, line=2, offset=0)
        ]

    def test_offset_is_found_and_reported(self) -> None:
        outcome = patch(FILE, "@@ -2,2 +2,2 @@\n line 5\n-line 6\n+sixth\n")
        assert outcome.text == FILE.replace("line 6\n", "sixth\n")
        assert (outcome.reports[0].line, outcome.reports[0].offset) == (5, 3)

    def test_nearest_match_wins(self) -> None:
        text = "x\na\nx\nb\nc\nd\ne\nx\n"
        outcome = patch(text, "@@ -4,1 +4,1 @@\n-x\n+y\n")  # matches at 1, 3, 8; 3 is nearest
        assert outcome.text == "x\na\ny\nb\nc\nd\ne\nx\n"

    def test_equally_near_matches_are_ambiguous(self) -> None:
        reason = failure(patch("x\nmid\nx\n", "@@ -2,1 +2,1 @@\n-x\n+y\n")).reason
        assert reason == "ambiguous: matches at lines 1 and 3"

    def test_context_not_found(self) -> None:
        outcome = patch(FILE, "@@ -2,1 +2,1 @@\n-secret value\n+y\n")
        reason = failure(outcome).reason
        assert reason == "context not found"
        assert "secret" not in reason  # reasons never quote lines

    def test_one_failing_hunk_blocks_all(self) -> None:
        diff = (
            "@@ -1,1 +1,1 @@\n-line 1\n+one\n"
            "@@ -5,1 +5,1 @@\n-missing\n+five\n"
            "@@ -9,1 +9,1 @@\n-line 9\n+nine\n"
        )
        outcome = patch(FILE, diff)
        assert outcome.text is None
        assert [report.applied for report in outcome.reports] == [True, False, True]
        assert outcome.reports[1].index == 2

    def test_overlapping_hunks(self) -> None:
        diff = "@@ -2,2 +2,2 @@\n line 2\n-line 3\n+a\n@@ -3,1 +3,1 @@\n-line 3\n+b\n"
        assert failure(patch(FILE, diff)).reason == "overlaps or precedes hunk 1"

    def test_out_of_order_hunks(self) -> None:
        diff = "@@ -8,1 +8,1 @@\n-line 8\n+a\n@@ -2,1 +2,1 @@\n-line 2\n+b\n"
        report = failure(patch(FILE, diff))
        assert (report.index, report.reason) == (2, "overlaps or precedes hunk 1")

    def test_several_hunks_shift_lines(self) -> None:
        diff = "@@ -1,1 +1,3 @@\n line 1\n+a\n+b\n@@ -9,2 +11,1 @@\n line 9\n-line 10\n"
        outcome = patch(FILE, diff)
        expected = FILE.replace("line 1\n", "line 1\na\nb\n").replace("line 10\n", "")
        assert outcome.text == expected

    def test_insertion_at_start_and_after_last_line(self) -> None:
        diff = "@@ -0,0 +1,1 @@\n+first\n@@ -10,0 +12,1 @@\n+last\n"
        assert patch(FILE, diff).text == f"first\n{FILE}last\n"

    def test_insertion_past_end(self) -> None:
        reason = failure(patch(FILE, "@@ -11,0 +12,1 @@\n+x\n")).reason
        assert reason == "insertion point is past the end of the file (10 lines)"

    def test_removing_a_dash_comment(self) -> None:
        diff = "--- a/q.sql\n+++ b/q.sql\n@@ -1,2 +1,2 @@\n--- old\n+-- new\n select 1;\n"
        assert patch("-- old\nselect 1;\n", diff).text == "-- new\nselect 1;\n"

    def test_empty_line_inside_hunk_is_context(self) -> None:
        diff = "@@ -1,3 +1,3 @@\n a\n\n-b\n+c\n"
        assert patch("a\n\nb\n", diff).text == "a\n\nc\n"

    def test_trailing_blank_lines_are_not_context(self) -> None:
        assert patch(FILE, "@@ -3,1 +3,1 @@\n-line 3\n+x\n\n\n").text is not None

    def test_miscounted_header_is_accepted(self) -> None:
        outcome = patch(FILE, "@@ -3,7 +3,9 @@\n-line 3\n+x\n")
        assert outcome.text == FILE.replace("line 3\n", "x\n")
        assert outcome.reports[0].header == "@@ -3,1 +3,1 @@"

    def test_header_section_text_is_dropped(self) -> None:
        outcome = patch(FILE, "@@ -3,1 +3,1 @@ def load_secret():\n-line 3\n+x\n")
        assert outcome.reports[0].header == "@@ -3,1 +3,1 @@"


class TestLineEndings:
    def test_untouched_lines_keep_their_terminators(self) -> None:
        outcome = patch("a\r\nb\nc\n", "@@ -2,1 +2,2 @@\n-b\n+x\n+y\n", newline="\r\n")
        assert outcome.text == "a\r\nx\r\ny\r\nc\n"

    def test_missing_final_newline_is_kept(self) -> None:
        assert patch("a\nb", "@@ -2,1 +2,1 @@\n-b\n+c\n").text == "a\nc"

    def test_append_after_unterminated_last_line(self) -> None:
        assert patch("a\nb", "@@ -2,0 +3,1 @@\n+c\n").text == "a\nb\nc"

    def test_deleting_every_line(self) -> None:
        assert patch("a\nb\n", "@@ -1,2 +0,0 @@\n-a\n-b\n").text == ""

    def test_marker_removes_final_newline(self) -> None:
        diff = "@@ -2,1 +2,1 @@\n-b\n+c\n\\ No newline at end of file\n"
        assert patch("a\nb\n", diff).text == "a\nc"

    def test_marker_adds_final_newline(self) -> None:
        diff = "@@ -2,1 +2,1 @@\n-b\n\\ No newline at end of file\n+c\n"
        assert patch("a\nb", diff).text == "a\nc\n"

    def test_context_marker_covers_both_sides(self) -> None:
        diff = "@@ -1,2 +1,2 @@\n-a\n+x\n b\n\\ No newline at end of file\n"
        assert patch("a\nb", diff).text == "x\nb"

    def test_marker_away_from_end_of_file_fails(self) -> None:
        diff = "@@ -2,1 +2,1 @@\n-b\n\\ No newline at end of file\n+c\n"
        assert failure(patch("a\nb\nz", diff)).reason == "context not found at the end of the file"

    def test_marker_that_contradicts_the_file_fails(self) -> None:
        diff = "@@ -2,1 +2,1 @@\n-b\n\\ No newline at end of file\n+c\n"
        reason = failure(patch("a\nb\n", diff)).reason
        assert reason == "the end-of-file newline does not match the file"

    @pytest.mark.parametrize(
        ("diff", "expected"),
        [
            ("@@ -0,0 +1,2 @@\n+a\n+b\n", "a\nb\n"),
            ("@@ -0,0 +1,2 @@\n+a\n+b\n\\ No newline at end of file\n", "a\nb"),
        ],
    )
    def test_empty_file(self, diff: str, expected: str) -> None:
        assert patch("", diff).text == expected


class TestMarkedInsertion:
    def test_away_from_end_of_file_fails(self) -> None:
        report = failure(patch("a\nb\nc\n", f"@@ -1,0 +2,1 @@\n+x\n{MARKER}"))
        assert report.index == 1
        assert report.line == 0
        assert "-3,0" in report.reason  # names the header that would apply

    def test_the_header_the_reason_names_applies(self) -> None:
        text = "a\nb\nc\n"
        assert patch(text, f"@@ -3,0 +4,1 @@\n+x\n{MARKER}").text == "a\nb\nc\nx"

    def test_past_the_end_keeps_its_own_reason(self) -> None:
        reason = failure(patch("a\n", f"@@ -2,0 +3,1 @@\n+x\n{MARKER}")).reason
        assert "past the end" in reason

    @pytest.mark.parametrize(
        "first",
        [
            f"@@ -3,0 +4,1 @@\n+x\n{MARKER}",
            f"@@ -3,1 +3,1 @@\n-c\n+x\n{MARKER}",
        ],
    )
    @pytest.mark.parametrize("later_marker", ["", MARKER])
    def test_no_hunk_may_follow_a_marked_line(self, first: str, later_marker: str) -> None:
        report = failure(patch("a\nb\nc\n", f"{first}@@ -3,0 +5,1 @@\n+y\n{later_marker}"))
        assert report.index == 2
        assert "hunk 1" in report.reason

    def test_context_marker_also_ends_the_file(self) -> None:
        diff = f"@@ -1,2 +1,2 @@\n-a\n+x\n b\n{MARKER}@@ -2,0 +3,1 @@\n+y\n"
        assert failure(patch("a\nb", diff)).index == 2

    def test_marker_on_the_last_hunk_applies(self) -> None:
        diff = f"@@ -3,0 +4,1 @@\n+x\n@@ -3,0 +5,1 @@\n+y\n{MARKER}"
        assert patch("a\nb\nc\n", diff).text == "a\nb\nc\nx\ny"

    def test_old_side_marker_does_not_end_the_file(self) -> None:
        diff = f"@@ -2,1 +2,1 @@\n-b\n{MARKER}+c\n@@ -2,0 +3,1 @@\n+d\n"
        assert patch("a\nb", diff).text == "a\nc\nd\n"

    @given(
        hunks=st.lists(
            st.sampled_from(
                [
                    (f"@@ -3,0 +4,1 @@\n+x\n{MARKER}", "x"),
                    (f"@@ -3,1 +3,1 @@\n-c\n+z\n{MARKER}", "z"),
                    (f"@@ -1,0 +2,1 @@\n+w\n{MARKER}", "w"),
                    ("@@ -3,0 +4,1 @@\n+y\n", ""),
                ]
            ),
            min_size=1,
            max_size=3,
        ),
        text=st.sampled_from(["a\nb\nc\n", "a\nb\nc"]),
    )
    def test_a_marked_line_stays_last(self, hunks: list[tuple[str, str]], text: str) -> None:
        outcome = patch(text, "".join(diff for diff, _ in hunks))

        if outcome.text is not None:
            for _, marked in hunks:
                assert not marked or outcome.text.endswith(f"\n{marked}")

    @given(
        count=st.integers(min_value=0, max_value=6),
        point=st.integers(min_value=0, max_value=8),
        final_newline=st.booleans(),
        newline=st.sampled_from(["\n", "\r\n"]),
        marked=st.booleans(),
    )
    def test_every_insertion_point(
        self, count: int, point: int, final_newline: bool, newline: str, marked: bool
    ) -> None:
        names = [f"line {n}" for n in range(count)]
        text = newline.join(names) + (newline if names and final_newline else "")
        diff = f"@@ -{point},0 +{point + 1},1 @@\n+new\n{MARKER if marked else ''}"

        outcome = patch(text, diff, newline)

        if point > count or (marked and point != count):
            assert failure(outcome).reason
            return
        rows = [*names[:point], "new", *names[point:]]
        ends_with_newline = not marked and (final_newline or not names)
        assert outcome.text == newline.join(rows) + (newline if ends_with_newline else "")


class TestParse:
    def test_git_headers_are_accepted(self) -> None:
        diff = (
            "diff --git a/f.txt b/f.txt\nindex 1234..5678 100644\n"
            "--- a/f.txt\n+++ b/f.txt\n@@ -3,1 +3,1 @@\n-line 3\n+x\n"
        )
        assert len(parse_patch(diff)) == 1

    @pytest.mark.parametrize(
        ("diff", "message"),
        [
            ("just text\n", "contains no hunk"),
            ("--- /dev/null\n+++ b/new.txt\n@@ -0,0 +1 @@\n+x\n", "use write_file"),
            (
                "--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n--- a/y\n+++ b/y\n@@ -1 +1 @@\n-a\n+b\n",
                "more than one file",
            ),
            ("@@ -1 +1 @@\n-a\n+b\ndiff --git a/y b/y\n", "more than one file"),
            ("@@ -1 +1 @@\n-a\n*b\n", r"Line 3 of the patch \(hunk 1\) starts with '\*'"),
            ("@@ -1 +1 @@\n", "Hunk 1 is empty"),
            ("@@ bad @@\n-a\n", "Line 1 of the patch is not a valid hunk header"),
            ("@@ -1 +1 @@\n\\ No newline at end of file\n-a\n", "needs a line before it"),
            ("@@ -1,2 +1 @@\n-a\n\\ No newline at end of file\n-b\n", "must follow the last"),
        ],
    )
    def test_invalid_patches(self, diff: str, message: str) -> None:
        with pytest.raises(InvalidArgumentError, match=message):
            parse_patch(diff)

    def test_parse_errors_do_not_quote_lines(self) -> None:
        with pytest.raises(InvalidArgumentError) as error:
            parse_patch("@@ -1 +1 @@\n-a\n*secret value\n")
        assert "secret" not in str(error.value)
