"""Regression tests for pdfdoctor.verify_safe / auto_fix content-loss check and
merge.py password-protected inputs.  Plain python, no pytest.

Bug #06: verify_safe FAILed on GOOD output whenever a sampled page was
legitimately blank (a spacer / blank middle sheet), so auto_fix silently
discarded valid fixes.  The guard must flag content-LOSS only -- a page that
went from non-blank to blank -- not absolute blankness.

Bug #35: merge_pdfs / split_pdf / rotate_pdf raised an unhandled backend
FileNotDecryptedError on password-protected input; they must now raise a clean
ValueError telling the user to unlock the file first.
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz
from rfi_stamper.minipdf.io import Reader as PdfReader

from rfi_stamper import pdfdoctor as doctor
from rfi_stamper.merge import (MergeItem, merge_pdfs, pdf_page_count,
                               rotate_pdf, split_pdf)

quiet = lambda *a, **k: None  # noqa: E731


def _make_with_blank_middle(path):
    """3-page set: inked / blank spacer / inked, plus dirty metadata."""
    doc = fitz.open()
    p0 = doc.new_page(width=612, height=792)
    p0.insert_text((72, 72), "sheet one", fontsize=14)
    p0.draw_rect(fitz.Rect(60, 60, 300, 120), color=(0, 0, 0), width=1)
    doc.new_page(width=612, height=792)                   # blank spacer page
    p2 = doc.new_page(width=612, height=792)
    p2.insert_text((72, 72), "sheet three", fontsize=14)
    p2.draw_rect(fitz.Rect(60, 60, 300, 120), color=(0, 0, 0), width=1)
    doc.set_metadata({"author": "Reviewer", "producer": "ToolX"})
    doc.save(path)
    doc.close()
    return path


def test_verify_safe_allows_blank_spacer(tmp):
    """A blank middle page that was blank in the ORIGINAL too must pass; the
    default sample includes the middle index, which used to trip the old
    absolute-blankness check."""
    src = _make_with_blank_middle(os.path.join(tmp, "spacer.pdf"))
    out = os.path.join(tmp, "spacer_clean.pdf")
    doctor.strip_metadata(src, out, log=quiet)
    ok, msg = doctor.verify_safe(src, out)               # default samples 0,1,2
    assert ok, f"blank spacer wrongly rejected: {msg}"
    print("  verify_safe allows blank spacer OK")


def test_verify_safe_still_catches_content_loss(tmp):
    """A page that carried ink in the original but is blank in the fixed copy
    must still FAIL -- the guard is content-loss, not no-op."""
    src = _make_with_blank_middle(os.path.join(tmp, "src2.pdf"))
    # a copy where every page is white -> page 0 and 2 lost their ink
    blanked = os.path.join(tmp, "blanked.pdf")
    b = fitz.open()
    for _ in range(3):
        b.new_page(width=612, height=792)
    b.save(blanked)
    b.close()
    ok, msg = doctor.verify_safe(src, blanked)
    assert not ok, "content loss on inked pages was not caught"
    print("  verify_safe catches content loss OK")


def test_auto_fix_strips_metadata_with_blank_page(tmp):
    """End-to-end: auto_fix on a set with a blank middle page must actually
    apply strip_metadata (previously rejected because the sampled blank page
    tripped verify_safe)."""
    src = _make_with_blank_middle(os.path.join(tmp, "dirty_blank.pdf"))
    assert (fitz.open(src).metadata or {}).get("author")
    out = os.path.join(tmp, "auto_out.pdf")
    res = doctor.auto_fix(src, out, do_compress=False, log=quiet)
    assert res["safe"] is True, res
    assert "strip_metadata" in res["actions"], res["actions"]
    m = fitz.open(out).metadata or {}
    assert not m.get("author") and not m.get("producer"), m
    assert len(fitz.open(out)) == 3
    print("  auto_fix strips metadata with blank page OK")


def _make_locked(path, user_pw="", owner_pw="secretowner"):
    doc = fitz.open()
    for i in range(2):
        page = doc.new_page(width=612, height=792)
        page.insert_text((72, 72), f"locked page {i}", fontsize=14)
    doc.save(path, encryption=fitz.PDF_ENCRYPT_AES_256,
             owner_pw=owner_pw, user_pw=user_pw,
             permissions=int(fitz.PDF_PERM_ACCESSIBILITY))
    doc.close()
    return path


def _expect_value_error(fn, *args, **kw):
    try:
        fn(*args, **kw)
    except ValueError as e:
        assert "password-protected" in str(e), str(e)
        return
    raise AssertionError(f"expected ValueError from {fn.__name__}")


def test_merge_owner_locked_ok(tmp):
    """Owner-locked with EMPTY user password: decrypt('') succeeds, so the
    ops behave exactly like a normal PDF."""
    src = _make_locked(os.path.join(tmp, "owner.pdf"))
    assert PdfReader(src).is_encrypted is True
    assert pdf_page_count(src) == 2
    out = os.path.join(tmp, "owner_merged.pdf")
    res = merge_pdfs([MergeItem(src)], out, log=quiet)
    assert res["pages"] == 2
    assert len(PdfReader(out).pages) == 2
    print("  merge owner-locked (empty user pw) OK")


def test_password_protected_raises_clean(tmp):
    """A real user password we don't have -> clean ValueError, not a raw
    decryption error, from all three entry points."""
    src = _make_locked(os.path.join(tmp, "userlock.pdf"), user_pw="realpass")
    out = os.path.join(tmp, "should_not_exist.pdf")
    _expect_value_error(merge_pdfs, [MergeItem(src)], out, log=quiet)
    _expect_value_error(split_pdf, src, os.path.join(tmp, "sp"),
                        every=1, log=quiet)
    _expect_value_error(rotate_pdf, src, out, 90, log=quiet)
    _expect_value_error(pdf_page_count, src)
    assert not os.path.exists(out), "wrote output for a locked input"
    print("  password-protected raises clean ValueError OK")


def main():
    with tempfile.TemporaryDirectory() as tmp:
        test_verify_safe_allows_blank_spacer(tmp)
        test_verify_safe_still_catches_content_loss(tmp)
        test_auto_fix_strips_metadata_with_blank_page(tmp)
        test_merge_owner_locked_ok(tmp)
        test_password_protected_raises_clean(tmp)
    print("REB PDFDOC TESTS PASSED")


if __name__ == "__main__":
    raise SystemExit(main())
