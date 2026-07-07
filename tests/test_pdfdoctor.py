"""Tests for rfi_stamper.pdfdoctor (offline PDF repair / optimize).

Plain python, no pytest.  Builds its own synthetic PDFs with fitz / reportlab
into a tempdir, exercises every fix, and asserts the input is never mutated
and every output is a valid, page-count-preserving PDF.
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz
import numpy as np
from pypdf import PdfReader

from rfi_stamper import pdfdoctor as doctor

quiet = lambda *a, **k: None  # noqa: E731


# ---------------------------------------------------------- pdf builders ---

def make_text_pdf(path, n_pages=3, tag="doc"):
    doc = fitz.open()
    for i in range(1, n_pages + 1):
        page = doc.new_page(width=612, height=792)
        page.insert_text((72, 72), f"{tag} page {i}", fontsize=14)
        page.draw_rect(fitz.Rect(60, 60, 300, 120), color=(0, 0, 0), width=1)
    doc.save(path, garbage=4, deflate=True)
    doc.close()
    return path


def _noise_pixmap(px):
    y, x = np.mgrid[0:px, 0:px]
    r = ((np.sin(x / 6.0) * 0.5 + 0.5) * 255).astype(np.uint8)
    g = (x % 256).astype(np.uint8)
    b = (y % 256).astype(np.uint8)
    rgb = np.ascontiguousarray(np.stack([r, g, b], axis=2))
    return fitz.Pixmap(fitz.csRGB, px, px, rgb.tobytes(), False)


def make_image_pdf(path, n_pages=2, page_pt=300, px=1200):
    """Image-heavy PDF: a ~page_pt-point page filled by a px*px photo, i.e.
    well above 150 dpi as placed, and no extractable text."""
    doc = fitz.open()
    for _ in range(n_pages):
        page = doc.new_page(width=page_pt, height=page_pt)
        page.insert_image(page.rect, pixmap=_noise_pixmap(px))
    doc.save(path, deflate=True)
    doc.close()
    return path


def make_encrypted_pdf(path, user_pw="", owner_pw="ownersecret"):
    """Owner-locked with (by default) an EMPTY user password -- opens without a
    prompt yet is genuinely encrypted."""
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 72), "confidential body text", fontsize=14)
    page.draw_rect(fitz.Rect(60, 60, 300, 120), color=(0, 0, 0), width=1)
    perm = int(fitz.PDF_PERM_ACCESSIBILITY)
    doc.save(path, encryption=fitz.PDF_ENCRYPT_AES_256,
             owner_pw=owner_pw, user_pw=user_pw, permissions=perm)
    doc.close()
    return path


def make_rotated_pdf(path, rotation=90):
    doc = fitz.open()
    page = doc.new_page(width=400, height=300)
    page.insert_text((25, 35), "TOP-LEFT MARK", fontsize=22)
    page.draw_rect(fitz.Rect(10, 10, 140, 70), color=(1, 0, 0), width=3)
    page.draw_line(fitz.Point(10, 10), fitz.Point(390, 290), color=(0, 0, 1),
                   width=2)
    page.set_rotation(rotation)
    doc.save(path)
    doc.close()
    return path


def digest(path):
    with open(path, "rb") as f:
        import hashlib
        return hashlib.sha256(f.read()).hexdigest()


def gray_arr(page, dpi=40):
    pix = page.get_pixmap(dpi=dpi, colorspace=fitz.csGRAY, alpha=False)
    return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height,
                                                              pix.width)


def page_has_ink(path, pno=0, dpi=50):
    d = fitz.open(path)
    if d.needs_pass:
        d.authenticate("")
    a = gray_arr(d[pno], dpi)
    d.close()
    return int(a.min()) < 250


# ----------------------------------------------------------------- tests ---

def test_diagnose(tmp):
    enc = make_encrypted_pdf(os.path.join(tmp, "enc.pdf"))
    keys = {i.key: i for i in doctor.diagnose(enc, log=quiet)}
    assert "encrypted" in keys, keys
    assert keys["encrypted"].severity == "high"
    assert keys["encrypted"].fix == "unlock" and keys["encrypted"].fixable

    heavy = make_image_pdf(os.path.join(tmp, "heavy.pdf"))
    hk = {i.key: i for i in doctor.diagnose(heavy, log=quiet)}
    assert "oversize" in hk, hk
    assert hk["oversize"].fix == "compress"
    # image-only pages flagged for OCR
    assert "no_text" in hk, hk
    assert hk["no_text"].fix == "ocr"

    # results are sorted high -> low
    order = {"high": 0, "medium": 1, "low": 2}
    sev = [order[i.severity] for i in doctor.diagnose(heavy, log=quiet)]
    assert sev == sorted(sev), sev

    # diagnose never raises on a non-PDF
    junk = os.path.join(tmp, "junk.pdf")
    with open(junk, "wb") as f:
        f.write(b"this is definitely not a pdf")
    res = doctor.diagnose(junk, log=quiet)
    assert res and res[0].key == "broken", res
    print("  diagnose OK")


def test_is_encrypted(tmp):
    enc = make_encrypted_pdf(os.path.join(tmp, "e2.pdf"))
    plain = make_text_pdf(os.path.join(tmp, "plain.pdf"))
    assert doctor.is_encrypted(enc) is True
    assert doctor.is_encrypted(plain) is False
    print("  is_encrypted OK")


def test_unlock(tmp):
    enc = make_encrypted_pdf(os.path.join(tmp, "locked.pdf"))
    before = digest(enc)
    assert PdfReader(enc).is_encrypted is True

    out = os.path.join(tmp, "unlocked.pdf")
    assert doctor.unlock(enc, out, log=quiet) is True

    # result opens without any password and is no longer encrypted
    assert PdfReader(out).is_encrypted is False
    d = fitz.open(out)
    assert d.needs_pass == 0 and d.is_encrypted is False
    assert len(d) == 1
    assert "confidential" in d[0].get_text()
    d.close()
    # input untouched
    assert digest(enc) == before
    # unlock with wrong password on a user-locked file returns False
    ulock = make_encrypted_pdf(os.path.join(tmp, "userlock.pdf"),
                               user_pw="realpass")
    assert doctor.unlock(ulock, os.path.join(tmp, "no.pdf"),
                         password="wrong", log=quiet) is False
    # ...and True with the right password
    good = os.path.join(tmp, "userunlocked.pdf")
    assert doctor.unlock(ulock, good, password="realpass", log=quiet) is True
    assert PdfReader(good).is_encrypted is False
    print("  unlock OK")


def test_repair(tmp):
    src = make_text_pdf(os.path.join(tmp, "good.pdf"), n_pages=3, tag="rep")
    before = digest(src)
    out = os.path.join(tmp, "repaired.pdf")
    res = doctor.repair(src, out, log=quiet)
    assert res["out_path"] == out and res["note"]
    d = fitz.open(out)
    assert len(d) == 3
    assert "rep page 1" in d[0].get_text()
    d.close()
    assert page_has_ink(out, 0)
    assert digest(src) == before                       # input untouched
    print("  repair OK")


def test_compress(tmp):
    heavy = make_image_pdf(os.path.join(tmp, "big.pdf"), n_pages=2)
    before_bytes = os.path.getsize(heavy)
    before = digest(heavy)
    out = os.path.join(tmp, "small.pdf")
    res = doctor.compress(heavy, out, image_dpi=100, log=quiet)
    assert res["out_path"] == out
    assert res["before"] == before_bytes
    assert res["after"] < res["before"], (res["before"], res["after"])
    assert res["ratio"] < 1.0
    # page count preserved and a sampled page still renders non-blank
    assert len(fitz.open(out)) == 2
    assert page_has_ink(out, 0)
    assert page_has_ink(out, 1)
    assert digest(heavy) == before                     # input untouched
    print("  compress OK")


def test_strip_metadata(tmp):
    src = os.path.join(tmp, "meta.pdf")
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "m")
    doc.set_metadata({"author": "Field Reviewer", "producer": "SomeTool 9",
                      "keywords": "k1 k2", "title": "Draft", "creator": "Ed"})
    doc.save(src)
    doc.close()
    before = digest(src)
    assert (fitz.open(src).metadata or {}).get("author")

    out = os.path.join(tmp, "clean.pdf")
    res = doctor.strip_metadata(src, out, log=quiet)
    assert "author" in res["removed"] and "producer" in res["removed"]
    m = fitz.open(out).metadata or {}
    assert not m.get("author") and not m.get("producer")
    assert not m.get("keywords") and not m.get("title")
    assert digest(src) == before                       # input untouched
    print("  strip_metadata OK")


def test_rasterize(tmp):
    src = make_text_pdf(os.path.join(tmp, "vec.pdf"), n_pages=2, tag="ras")
    before = digest(src)
    # original has extractable text
    assert fitz.open(src)[0].get_text().strip()

    out = os.path.join(tmp, "ras.pdf")
    res = doctor.rasterize(src, out, dpi=120, log=quiet)
    assert res["pages"] == 2 and res["dpi"] == 120

    o = fitz.open(src)
    r = fitz.open(out)
    assert len(r) == 2
    for pno in range(2):
        # same page point size
        assert abs(r[pno].rect.width - o[pno].rect.width) < 0.5
        assert abs(r[pno].rect.height - o[pno].rect.height) < 0.5
        # non-blank
        assert int(gray_arr(r[pno], 50).min()) < 250
        # no extractable text left
        assert r[pno].get_text().strip() == "", r[pno].get_text()
    o.close()
    r.close()
    assert digest(src) == before                       # input untouched
    print("  rasterize OK")


def test_upscale(tmp):
    src = make_text_pdf(os.path.join(tmp, "u.pdf"), n_pages=1, tag="up")
    out = os.path.join(tmp, "up.pdf")
    res = doctor.upscale(src, out, dpi=200, log=quiet)
    assert res["pages"] == 1 and res["dpi"] == 200
    assert len(fitz.open(out)) == 1
    assert page_has_ink(out, 0)
    print("  upscale OK")


def test_normalize_rotation(tmp):
    src = make_rotated_pdf(os.path.join(tmp, "rot90.pdf"), rotation=90)
    before = digest(src)
    s = fitz.open(src)
    assert s[0].rotation == 90
    ref = gray_arr(s[0], 40)                            # displayed appearance
    s.close()

    out = os.path.join(tmp, "norm.pdf")
    res = doctor.normalize_rotation(src, out, log=quiet)
    assert res["normalized"] == 1

    n = fitz.open(out)
    assert n[0].rotation == 0
    # viewer size preserved
    assert abs(n[0].rect.width - 300) < 0.5
    assert abs(n[0].rect.height - 400) < 0.5
    got = gray_arr(n[0], 40)
    n.close()
    assert got.shape == ref.shape, (got.shape, ref.shape)
    # visually similar (only anti-aliasing differences)
    assert float(np.abs(got.astype(int) - ref.astype(int)).mean()) < 12.0
    assert digest(src) == before                       # input untouched
    print("  normalize_rotation OK")


def test_linearize(tmp):
    src = make_text_pdf(os.path.join(tmp, "lin_in.pdf"), n_pages=2, tag="lin")
    out = os.path.join(tmp, "lin_out.pdf")
    res = doctor.linearize(src, out, log=quiet)
    assert res["out_path"] == out and res["note"]
    d = fitz.open(out)
    assert len(d) == 2                                  # round-trips
    assert "lin page 1" in d[0].get_text()
    d.close()
    print("  linearize OK")


def test_javascript_and_embedded(tmp):
    # build a PDF carrying doc-level JavaScript and an attachment
    src = os.path.join(tmp, "js.pdf")
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "js host")
    cat = doc.pdf_catalog()
    js = doc.get_new_xref()
    doc.update_object(js, "<</S/JavaScript/JS(app.alert\\(1\\);)>>")
    names = doc.get_new_xref()
    doc.update_object(names, "<</JavaScript<</Names[(EJS) %d 0 R]>>>>" % js)
    doc.xref_set_key(cat, "Names", "%d 0 R" % names)
    doc.embfile_add("attach.txt", b"secret attachment payload")
    doc.save(src)
    doc.close()

    keys = {i.key for i in doctor.diagnose(src, log=quiet)}
    assert "javascript" in keys and "embedded_files" in keys, keys

    js_out = os.path.join(tmp, "nojs.pdf")
    res = doctor.remove_javascript(src, js_out, log=quiet)
    assert res["removed"] is True
    assert not doctor._has_javascript(fitz.open(js_out))

    emb_out = os.path.join(tmp, "noemb.pdf")
    res = doctor.remove_embedded_files(src, emb_out, log=quiet)
    assert "attach.txt" in res["removed"]
    assert fitz.open(emb_out).embfile_count() == 0
    print("  javascript / embedded-files OK")


def test_flatten_annotations(tmp):
    src = os.path.join(tmp, "annot.pdf")
    doc = fitz.open()
    page = doc.new_page(width=400, height=300)
    page.insert_text((40, 40), "base")
    page.add_freetext_annot(fitz.Rect(50, 100, 250, 140), "REVIEW STAMP",
                            fontsize=12, text_color=(1, 0, 0))
    doc.save(src)
    doc.close()
    assert doctor._annot_count(fitz.open(src)) >= 1

    out = os.path.join(tmp, "flat.pdf")
    res = doctor.flatten_annotations(src, out, log=quiet)
    assert res["out_path"] == out
    d = fitz.open(out)
    assert len(d) == 1
    # after baking, no live annotation objects remain
    assert doctor._annot_count(d) == 0
    d.close()
    assert page_has_ink(out, 0)
    print("  flatten_annotations OK")


def test_verify_safe(tmp):
    src = make_text_pdf(os.path.join(tmp, "vs.pdf"), n_pages=3, tag="vs")
    good = os.path.join(tmp, "vs_good.pdf")
    doctor.repair(src, good, log=quiet)
    ok, msg = doctor.verify_safe(src, good)
    assert ok, msg

    # blank copy: same page count, all-white pages -> not safe
    blank = os.path.join(tmp, "blank.pdf")
    bdoc = fitz.open()
    for _ in range(3):
        bdoc.new_page(width=612, height=792)           # empty white pages
    bdoc.save(blank)
    bdoc.close()
    ok, msg = doctor.verify_safe(src, blank)
    assert not ok, msg

    # truncated copy: unreadable -> not safe
    trunc = os.path.join(tmp, "trunc.pdf")
    with open(good, "rb") as f:
        head = f.read(120)
    with open(trunc, "wb") as f:
        f.write(head)
    ok, msg = doctor.verify_safe(src, trunc)
    assert not ok, msg
    print("  verify_safe OK")


def test_auto_fix(tmp):
    # encrypted + dirty metadata + heavy images in one file
    src = os.path.join(tmp, "dirty.pdf")
    doc = fitz.open()
    for _ in range(2):
        page = doc.new_page(width=300, height=300)
        page.insert_image(page.rect, pixmap=_noise_pixmap(1000))
    doc.set_metadata({"author": "Someone", "producer": "ToolZ"})
    doc.save(src, encryption=fitz.PDF_ENCRYPT_AES_256,
             owner_pw="own", user_pw="", permissions=int(fitz.PDF_PERM_PRINT))
    doc.close()
    before = digest(src)
    assert doctor.is_encrypted(src)

    out = os.path.join(tmp, "fixed.pdf")
    res = doctor.auto_fix(src, out, do_compress=True, log=quiet)
    assert res["safe"] is True, res
    assert "unlock" in res["actions"], res
    assert "strip_metadata" in res["actions"], res
    assert "compress" in res["actions"], res
    assert res["out_path"] == out
    assert res["issues_after"] <= res["issues_before"]

    # output: decrypted, clean metadata, page count preserved, renders
    assert PdfReader(out).is_encrypted is False
    d = fitz.open(out)
    assert len(d) == 2
    m = d.metadata or {}
    assert not m.get("author") and not m.get("producer")
    d.close()
    assert page_has_ink(out, 0)
    # no stray .stage temp files left behind
    leftovers = [f for f in os.listdir(tmp) if f.endswith(".stage")
                 or f.endswith(".part")]
    assert not leftovers, leftovers
    assert digest(src) == before                       # input untouched
    print("  auto_fix OK")


def main():
    with tempfile.TemporaryDirectory() as tmp:
        test_diagnose(tmp)
        test_is_encrypted(tmp)
        test_unlock(tmp)
        test_repair(tmp)
        test_compress(tmp)
        test_strip_metadata(tmp)
        test_rasterize(tmp)
        test_upscale(tmp)
        test_normalize_rotation(tmp)
        test_linearize(tmp)
        test_javascript_and_embedded(tmp)
        test_flatten_annotations(tmp)
        test_verify_safe(tmp)
        test_auto_fix(tmp)
    print("PDFDOCTOR TESTS PASSED")


if __name__ == "__main__":
    main()
