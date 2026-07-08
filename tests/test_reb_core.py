"""Regression tests for rfi_stamper.core parser fixes.

Covers the rebuild findings:
  #02  distinct un-numbered RFIs must NOT collapse into one record
  #03  6+ digit numbers must not be truncated to 5 digits
  #04  single-digit RFI numbers (RFI #5) must parse, not fall to "???"
  #19  cross-file answer-backfill must also upgrade a stale/short question
  #20  answer restatement leak: a long/labelled question restatement placed
       under Answer: must be rejected even past the 90-char head window

Plain-python, no project data.  Run:  python tests/test_reb_core.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rfi_stamper import core                                   # noqa: E402
from rfi_stamper.core import parse_fields, split_records, parse_paths  # noqa: E402


# --------------------------------------------------------------- #03, #04 --

def test_number_parsing():
    # single digit (#04): must parse, not fall to the "???" collision path
    r = parse_fields("Request for Information\nRFI #5\nQuestion: what\n", "x.txt")
    assert r.number == "005" and r.numbered, r.number

    # 6+ digit (#03): not truncated to 5 digits
    r = parse_fields("Request for Information\nDocument: 123456\n", "x.txt")
    assert r.number == "123456", r.number

    r = parse_fields("Request for Information\nDistrict RFI #: 7654321\n", "x.txt")
    assert r.number == "7654321", r.number

    # short numbers still zero-filled to 3
    r = parse_fields("Request for Information\nDocument: 42\n", "x.txt")
    assert r.number == "042", r.number

    # word boundary: a trailing letter must not swallow the token oddly, and
    # the number should be exactly the run of digits
    r = parse_fields("Request for Information\nRFI #10 revised\n", "x.txt")
    assert r.number == "010", r.number

    # sentinel when nothing anywhere
    r = parse_fields("Request for Information\nno digits here\n", "nofn.txt")
    assert r.number == "???" and not r.numbered, r.number
    print("  number parsing (#03/#04) OK")


# ------------------------------------------------------------------- #02 --

def test_unnumbered_do_not_collapse():
    # two records, neither with a parseable number, distinct refs
    text = ("Request for Information\nno number\nQuestion: first q\n"
            "Plan Ref: P-101\n"
            "Request for Information\nno number\nQuestion: second q\n"
            "Plan Ref: P-202\n")
    recs = split_records(text, "nofn.txt")
    assert len(recs) == 2, f"un-numbered RFIs collapsed: {len(recs)}"
    # refs must NOT be cross-contaminated
    refsets = [{t for t, _ in r.refs} for r in recs]
    assert {"P-101"} in refsets and {"P-202"} in refsets, refsets
    for r in recs:
        assert not r.numbered
    print("  un-numbered records stay distinct (#02, split) OK")


def test_unnumbered_do_not_collapse_across_files():
    tmp = tempfile.mkdtemp(prefix="rfi_core_")
    a = os.path.join(tmp, "a.txt")
    b = os.path.join(tmp, "b.txt")
    with open(a, "w", encoding="utf-8") as f:
        f.write("Request for Information\nno number\nQuestion: alpha\n"
                "Plan Ref: P-101\n")
    with open(b, "w", encoding="utf-8") as f:
        f.write("Request for Information\nno number\nQuestion: bravo\n"
                "Plan Ref: P-202\n")
    recs = parse_paths([a, b], log=lambda m: None)
    assert len(recs) == 2, f"un-numbered RFIs collapsed across files: {len(recs)}"
    refsets = [{t for t, _ in r.refs} for r in recs]
    assert {"P-101"} in refsets and {"P-202"} in refsets, refsets
    print("  un-numbered records stay distinct (#02, cross-file) OK")


def test_numbered_still_merge():
    # a genuine duplicate (same content number) still merges, answer backfills
    tmp = tempfile.mkdtemp(prefix="rfi_core_")
    a = os.path.join(tmp, "a.txt")
    b = os.path.join(tmp, "b.txt")
    with open(a, "w", encoding="utf-8") as f:
        f.write("Request for Information\nDocument: 12\nQuestion: q\n"
                "Answer:\n")
    with open(b, "w", encoding="utf-8") as f:
        f.write("Request for Information\nDocument: 12\nQuestion: q\n"
                "Answer: Route it per detail five and coordinate with the "
                "architect before rough-in of the assembly.\n")
    recs = parse_paths([a, b], log=lambda m: None)
    assert len(recs) == 1, f"numbered duplicates did not merge: {len(recs)}"
    assert recs[0].has_answer, "answer did not backfill across files"
    print("  numbered duplicates still merge + backfill OK")


# ------------------------------------------------------------------- #19 --

def test_cross_file_question_upgrade():
    # file A: number 30, short question, no answer
    # file B: number 30, long question, real answer -> merge must keep the
    #         LONG question next to the backfilled answer
    tmp = tempfile.mkdtemp(prefix="rfi_core_")
    a = os.path.join(tmp, "a.txt")
    b = os.path.join(tmp, "b.txt")
    long_q = ("Where exactly should the fake pipe route around the three "
              "fixtures shown in the plan and at what invert elevation?")
    with open(a, "w", encoding="utf-8") as f:
        f.write("Request for Information\nDocument: 30\nQuestion: short q\n"
                "Answer:\n")
    with open(b, "w", encoding="utf-8") as f:
        f.write("Request for Information\nDocument: 30\n"
                f"Question: {long_q}\n"
                "Answer: Route the fake pipe per detail five on this sheet "
                "and coordinate with the architect before rough-in.\n")
    recs = parse_paths([a, b], log=lambda m: None)
    assert len(recs) == 1, len(recs)
    assert recs[0].has_answer
    assert "three fixtures" in recs[0].question, \
        f"stale short question kept next to answer: {recs[0].question!r}"
    print("  cross-file question upgrade (#19) OK")


# ------------------------------------------------------------------- #20 --

def test_answer_restatement_leak():
    # a long, labelled restatement of the question placed under Answer: must
    # be rejected even though it exceeds the 90-char head window.
    q = ("Please confirm the required fire rating and the exact assembly type "
         "for the demising partition between the two tenant suites on level "
         "three near the mechanical shaft as shown on the reflected plan.")
    chunk = ("Request for Information\nDocument: 77\n"
             f"Question: {q}\n"
             "Answer: Original Question - " + q + "\n"
             "Attachments:\n")
    r = parse_fields(chunk, "x.txt")
    assert not r.has_answer, \
        f"question restatement leaked as an answer: {r.answer!r}"

    # a genuine answer is still accepted
    chunk2 = ("Request for Information\nDocument: 78\n"
              f"Question: {q}\n"
              "Answer: Provide a one hour rated assembly using type X gypsum "
              "board both sides on metal studs per the wall type schedule.\n")
    r2 = parse_fields(chunk2, "x.txt")
    assert r2.has_answer, "genuine answer was wrongly rejected"
    print("  answer restatement leak guard (#20) OK")


def main():
    test_number_parsing()
    test_unnumbered_do_not_collapse()
    test_unnumbered_do_not_collapse_across_files()
    test_numbered_still_merge()
    test_cross_file_question_upgrade()
    test_answer_restatement_leak()
    print("REB CORE TESTS PASSED  (number parsing #03/#04, un-numbered "
          "no-collapse #02, question upgrade #19, restatement leak #20)")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as e:
        print("REB CORE TEST FAILED:", e)
        sys.exit(1)
