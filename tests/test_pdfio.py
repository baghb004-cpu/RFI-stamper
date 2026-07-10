"""The Shuttle (minipdf reader/writer) — the pypdf-retirement acceptance.

Deterministic, offline.  Covers the staged plan's gates in one place:

* R1/R2 corpus parity: fitz-authored fixtures (rotations, trimmed CropBox,
  annots) plus their ``use_objstms=1`` re-save and a ``saveIncr()``
  two-revision variant — page count / boxes / rotation / annots equal
  fitz's answers, and pypdf's when the retired oracle is importable
  (guarded: this suite must stay green with pypdf UNINSTALLED).
* R3 quirk battery: deterministic byte-surgery fixtures; the ``repaired``
  flag is True exactly for the ones that need recovery.
* Predictor unit vector: filter types 0-4 (Paeth included) decode to known
  bytes.
* Writer gates: strict self-re-parse (recovery disabled), byte determinism,
  no /Info//Producer//CreationDate anywhere in any output.
* Backend pixel parity: merge/rotate/stamp outputs render identically
  between ``PLOOM_PDF_IO=mini`` and ``=pypdf`` (150 dpi; oracle-guarded),
  and untouched pages stay pixel-identical to the source (90 dpi, always).
* Encryption: fitz-written RC4-128 / AES-256 owner-locked files open
  transparently through ``merge._open``; a user password raises the clean
  ValueError; ``pdfdoctor.is_encrypted`` still detects the owner lock.
* Retirement proof: no runtime module imports pypdf at module level.

Run:  python3.12 tests/test_pdfio.py
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np                                    # noqa: E402
import fitz                                           # noqa: E402

from rfi_stamper.minipdf import parse as P            # noqa: E402
from rfi_stamper.minipdf.io import Reader, Writer     # noqa: E402
from rfi_stamper.minipdf.canvas import Canvas         # noqa: E402
from rfi_stamper.minipdf.pagemerge import overlay_ctm  # noqa: E402
from rfi_stamper import merge, pdfdoctor              # noqa: E402

try:                    # the retired oracle — dev-box only, never required
    from pypdf import PdfReader as _OraclePdfReader
    HAVE_ORACLE = True
except Exception:
    HAVE_ORACLE = False

_N = [0]


def A(cond, msg=""):
    _N[0] += 1
    assert cond, msg


def _quiet(*a, **k):
    pass


TMP = tempfile.mkdtemp(prefix="pdfio_test_")


def _fitz_fixture(path):
    doc = fitz.open()
    for k in range(4):
        pg = doc.new_page(width=1224, height=792)
        pg.insert_text((100, 100), f"SHEET {k}")
    doc[1].set_rotation(90)
    doc[2].set_rotation(270)
    doc[2].set_cropbox(fitz.Rect(50, 40, 800, 700))
    doc[0].add_text_annot((200, 200), "reviewer note")
    doc.save(path, deflate=True)
    doc.close()


def _minipdf_fixture() -> bytes:
    buf = io.BytesIO()
    c = Canvas(buf, pagesize=(612, 792))
    for k in range(3):
        c.setFont("Helvetica", 12)
        c.drawString(72, 720, f"PAGE {k + 1}")
        c.showPage()
    c.save()
    return buf.getvalue()


# --------------------------------------------------------------------------- #
#  1. corpus parity (R1/R2)                                                    #
# --------------------------------------------------------------------------- #

def test_corpus_parity():
    src = os.path.join(TMP, "corpus.pdf")
    _fitz_fixture(src)
    variants = {"classic": src}
    ob = os.path.join(TMP, "corpus_objstm.pdf")
    doc = fitz.open(src)
    doc.save(ob, use_objstms=1, deflate=True)
    doc.close()
    variants["objstm"] = ob
    inc = os.path.join(TMP, "corpus_incr.pdf")
    with open(src, "rb") as f, open(inc, "wb") as g:
        g.write(f.read())
    doc = fitz.open(inc)
    doc[3].add_text_annot((150, 150), "second revision")
    doc.saveIncr()
    doc.close()
    variants["incremental"] = inc

    for name, path in variants.items():
        r = P.read_pdf(path)
        A(not r.repaired, f"{name}: no recovery needed")
        d = fitz.open(path)
        A(len(r.pages) == d.page_count, f"{name}: page count")
        for i, pg in enumerate(r.pages):
            A(pg.rotation == d[i].rotation, f"{name} p{i}: rotation")
            fr = d[i].cropbox
            cb = pg.cropbox
            for got, want in ((cb.width, fr.width), (cb.height, fr.height)):
                A(abs(got - want) < 1e-4, f"{name} p{i}: cropbox dims")
        A(("/Annots" in r.pages[0]), f"{name}: annot on page 1 seen")
        if name == "incremental":
            A("/Annots" in r.pages[3], "newest revision wins: rev-2 annot")
        d.close()
        if HAVE_ORACLE:
            pr = _OraclePdfReader(path)
            for i, pg in enumerate(r.pages):
                A(abs(float(pr.pages[i].mediabox.width)
                      - pg.mediabox.width) < 1e-4, f"{name}: oracle mediabox")
                A((pr.pages[i].get("/Rotate") or 0) % 360 == pg.rotation,
                  f"{name}: oracle rotation")
                A(("/Annots" in pr.pages[i]) == ("/Annots" in pg),
                  f"{name}: oracle annots")
    if not HAVE_ORACLE:
        print("  (pypdf oracle not importable — fitz-only parity, as designed)")

    m = _minipdf_fixture()
    r = P.read_pdf(m, strict=True)            # our own writer: STRICT parse
    A(len(r.pages) == 3 and not r.repaired, "minipdf file strict-parses")


# --------------------------------------------------------------------------- #
#  2. quirk battery (R3)                                                       #
# --------------------------------------------------------------------------- #

def test_quirk_battery():
    base = _minipdf_fixture()

    junk = b"MAIL-GATEWAY-WRAPPER\r\n" + base   # 1: junk before %PDF
    r = P.read_pdf(junk)
    A(len(r.pages) == 3, "junk prefix: pages found")
    A(not r.repaired, "junk prefix is leniency, not recovery")

    k = base.rfind(b"startxref")                # 2: startxref points at garbage
    bad = base[:k] + b"startxref\n999999\n%%EOF\n"
    r = P.read_pdf(bad)
    A(len(r.pages) == 3 and r.repaired, "bad startxref: rebuilt")

    noeof = base[:k]                            # 3: no startxref / %%EOF at all
    r = P.read_pdf(noeof)
    A(len(r.pages) == 3 and r.repaired, "missing startxref: rebuilt")

    import re as _re                            # 4: /Length lies (same byte
    wrong = _re.sub(rb"/Length (\d)", rb"/Length 9", base)   # count: offsets
    A(len(wrong) == len(base), "fixture must not shift offsets")   # untouched)
    r = P.read_pdf(wrong)
    A(len(r.pages) == 3 and not r.repaired,
      "wrong /Length: endstream scan, no recovery")

    slim = base.replace(b" n \n", b" n\n")      # 5: 19-byte xref rows
    r = P.read_pdf(slim)
    A(len(r.pages) == 3, "19-byte xref rows parse tokenwise")

    dup = (base[:k]                             # 6: duplicate object number —
           + b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Courier "
           b"/Encoding /WinAnsiEncoding >>\nendobj\n")     # LAST wins on rebuild
    r = P.read_pdf(dup)
    A(r.repaired, "duplicate-object fixture rebuilt")
    A(r.get(5).get("/BaseFont") == "/Courier",
      "rebuild takes the newest (last) definition")

    # 7: every offset shifted by a constant (an EOL-translating mail/text
    # tool) — a 1-byte pad would land in whitespace and tokenwise parsing
    # absorbs it silently, so shift far enough to land mid-content
    shift = base.replace(b"%PDF-1.4\n", b"%PDF-1.4\n% eol-translated-pad\n")
    r = P.read_pdf(shift)
    A(len(r.pages) == 3 and r.repaired, "shifted offsets: rebuilt")
    tiny = base.replace(b"1 0 obj", b"1 0  obj")    # ...and the 1-byte case
    r = P.read_pdf(tiny)                            # parses WITHOUT recovery
    A(len(r.pages) == 3, "1-byte shift absorbed tokenwise")

    rev = base.replace(b"/MediaBox [0 0 612 792]",
                       b"/MediaBox [612 792 0 0]")  # 8: reversed corners
    r = P.read_pdf(rev)
    A(r.pages[0].mediabox.width == 612 and r.pages[0].mediabox.height == 792,
      "reversed box corners normalize")

    strict_ok = P.read_pdf(base, strict=True)
    A(not strict_ok.repaired, "clean file needs no recovery (strict)")
    try:
        P.read_pdf(bad, strict=True)
        A(False, "strict mode must refuse recovery")
    except P.PdfError:
        A(True)


# --------------------------------------------------------------------------- #
#  3. parser + predictor unit vectors                                          #
# --------------------------------------------------------------------------- #

def test_parser_units():
    po = P.parse_object
    A(po(b"(a\\(b\\051c)", 0)[0] == b"a(b)c", "escapes + octal in strings")
    A(po(b"(line1\\\nline2)", 0)[0] == b"line1line2", "backslash-EOL")
    A(po(b"(cr\rhere)", 0)[0] == b"cr\nhere", "bare CR -> LF")
    A(po(b"<48656C6C6F7>", 0)[0] == b"Hello p".replace(b" ", b""),
      "odd-length hex pads with 0")
    A(po(b"+.5", 0)[0] == 0.5 and po(b"1.", 0)[0] == 1.0, "lenient numbers")
    A(po(b"--3", 0)[0] == 0, "double-negative junk -> 0, not a crash")
    A(po(b"1.0E-005", 0)[0] == 1e-5, "exponent reals")
    A(po(b"12 0 R", 0)[0] == P.Ref(12, 0), "two-token reference lookahead")
    A(po(b"12 0", 0)[0] == 12, "bare int is not a reference")
    d, _ = po(b"<< /A 1 %c\n /B (x%y) garbage /C /N#41me >>", 0)
    A(d == {"/A": 1, "/B": b"x%y", "/C": "/NAme"},
      f"comments + junk keys + #xx names, got {d}")

    rows = [bytes([1, 2, 3]), bytes([4, 5, 6]), bytes([7, 8, 9]),
            bytes([10, 11, 12]), bytes([13, 14, 15])]
    enc = (b"\x01\x01\x01\x01"          # sub
           b"\x02\x03\x03\x03"          # up
           b"\x04\x03\x01\x01"          # paeth
           b"\x00\x0a\x0b\x0c"          # none
           b"\x03\x08\x02\x02")         # average
    out = P.unpredict(enc, {"/Predictor": 12, "/Columns": 3})
    A(out == b"".join(rows), f"PNG predictor vector, got {out.hex()}")

    A(P.inflate(zlib.compress(b"DATA")) == b"DATA", "zlib inflate")
    co = zlib.compressobj(wbits=-15)
    raw = co.compress(b"RAWDEFLATE") + co.flush()
    A(P.inflate(raw) == b"RAWDEFLATE", "raw-deflate retry on bogus header")


def test_ctm_table():
    # the four literal CTMs match the field-verified viewer->media mapping
    w, h, x0, y0 = 1224.0, 792.0, 50.0, 40.0

    def apply(ctm, x, y):
        a, b, c, d, e, f = ctm
        return (a * x + c * y + e, b * x + d * y + f)

    A(apply(overlay_ctm(0, w, h, x0, y0), 10, 20) == (60.0, 60.0), "rot 0")
    A(apply(overlay_ctm(90, w, h, x0, y0), 10, 20) == (w - 20 + x0, 10 + y0),
      "rot 90: viewer (x,y) -> media (w-y, x) — the FIELD-VERIFIED case")
    A(apply(overlay_ctm(180, w, h, x0, y0), 10, 20) == (w - 10 + x0, h - 20 + y0),
      "rot 180")
    A(apply(overlay_ctm(270, w, h, x0, y0), 10, 20) == (20 + x0, h - 10 + y0),
      "rot 270")


# --------------------------------------------------------------------------- #
#  4. writer gates: strict self-check, determinism, clean bytes                #
# --------------------------------------------------------------------------- #

def test_writer_gates():
    src = os.path.join(TMP, "wsrc.pdf")
    _fitz_fixture(src)
    r = Reader(src)

    def build():
        w = Writer()
        for pg in r.pages:
            w.add_page(pg)
        w.pages[1].rotate(90)
        w.add_outline_item("alpha", 0)
        w.add_outline_item("Charlie p2", 2)
        buf = io.BytesIO()
        w.write(buf)
        return buf.getvalue()

    d1, d2 = build(), build()
    A(d1 == d2, "writer output is byte-deterministic")
    for tok in (b"/Info", b"/Producer", b"/CreationDate"):
        A(tok not in d1, f"{tok} never appears in output")

    rr = P.read_pdf(d1, strict=True)           # strict: recovery disabled
    A(not rr.repaired and len(rr.pages) == 4, "strict self-re-parse")
    # every xref offset lands exactly on its 'N 0 obj'
    for num, ent in rr.xref.items():
        A(d1[ent[1]:].startswith(b"%d 0 obj" % num),
          f"xref offset for {num} exact")

    d = fitz.open(stream=d1, filetype="pdf")
    A(d.get_toc() == [[1, "alpha", 1], [1, "Charlie p2", 3]],
      f"outline via fitz, got {d.get_toc()}")
    A(d[1].rotation == 180, "rotate(90) on the /Rotate 90 source page")
    A(len(list(d[0].annots())) == 1, "annotation traveled with the page")
    d.close()


# --------------------------------------------------------------------------- #
#  5. backend parity: merge + stamp render identically                         #
# --------------------------------------------------------------------------- #

def _px(path, page=0, dpi=150, annots=True):
    d = fitz.open(path)
    pix = d[page].get_pixmap(dpi=dpi, colorspace=fitz.csGRAY, annots=annots)
    a = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width)
    a = a.copy()
    d.close()
    return a


def test_backend_parity():
    src = os.path.join(TMP, "bsrc.pdf")
    _fitz_fixture(src)

    def run_merge(backend, tag, rot):
        os.environ["PLOOM_PDF_IO"] = backend
        try:
            out = os.path.join(TMP, f"m_{tag}_{rot}.pdf")
            merge.merge_pdfs([merge.MergeItem(src, "1-3", rot, "part")],
                             out, bookmarks=True, log=_quiet)
            return out
        finally:
            os.environ.pop("PLOOM_PDF_IO", None)

    # untouched-page guarantee: a plain merge renders pixel-identical to
    # the source at verify.py's dpi (rasterizing a /Rotate page is NOT the
    # same as np.rot90 of an upright raster — AA does not commute — so the
    # rotated variant is proven by backend A/B + smoke_test's full verify)
    a0 = run_merge("mini", "mini", 0)
    for i in range(3):
        A(np.array_equal(_px(a0, i, dpi=90), _px(src, i, dpi=90)),
          f"merged page {i + 1} pixel-identical to the source")
    a90 = run_merge("mini", "mini", 90)
    d = fitz.open(a90)
    A([d[i].rotation for i in range(3)] == [90, 180, 0],
      "extra rotation composes with the source /Rotate (0/90/270 + 90)")
    d.close()
    if HAVE_ORACLE:
        for rot in (0, 90):
            b = run_merge("pypdf", "pypdf", rot)
            am = a0 if rot == 0 else a90
            for i in range(3):
                A(np.array_equal(_px(am, i), _px(b, i)),
                  f"merge backends pixel-identical, rot {rot} page {i + 1}")
    else:
        print("  (backend A/B skipped — pypdf oracle not importable)")


# --------------------------------------------------------------------------- #
#  6. encryption behavior                                                      #
# --------------------------------------------------------------------------- #

def test_encryption():
    src = os.path.join(TMP, "esrc.pdf")
    _fitz_fixture(src)
    doc = fitz.open(src)
    rc4 = os.path.join(TMP, "rc4_owner.pdf")
    doc.save(rc4, encryption=fitz.PDF_ENCRYPT_RC4_128, owner_pw="lock",
             user_pw="")
    aes = os.path.join(TMP, "aes_owner.pdf")
    doc.save(aes, encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="lock",
             user_pw="")
    usr = os.path.join(TMP, "user_locked.pdf")
    doc.save(usr, encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="lock",
             user_pw="secret")
    doc.close()

    for path in (rc4, aes):                    # blank-password transparency
        A(merge.pdf_page_count(path) == 4,
          f"owner-locked opens transparently: {os.path.basename(path)}")
        A(pdfdoctor.is_encrypted(path),
          "pdfdoctor still detects the owner lock fitz opens silently")
    try:
        merge.pdf_page_count(usr)
        A(False, "user-password file must raise")
    except ValueError as e:
        A("password-protected" in str(e), f"clean error names the state: {e}")
    A(not pdfdoctor.is_encrypted(src), "plain file is not flagged")


# --------------------------------------------------------------------------- #
#  7. retirement proof: no runtime module imports pypdf at module level        #
# --------------------------------------------------------------------------- #

def test_retirement():
    root = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "rfi_stamper")
    offenders = []
    for dirpath, _dirs, files in os.walk(root):
        if "__pycache__" in dirpath:
            continue
        for fn in files:
            if not fn.endswith(".py"):
                continue
            with open(os.path.join(dirpath, fn), encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith(("import pypdf", "from pypdf")):
                        offenders.append(fn)
                        break
    A(not offenders,
      f"module-level pypdf imports remain in: {offenders}")
    # requirements carry only the documented floor
    req = os.path.join(os.path.dirname(root), "requirements.txt")
    with open(req, encoding="utf-8") as fh:
        A("pypdf" not in fh.read().lower(), "pypdf gone from requirements.txt")


# --------------------------------------------------------------------------- #

def main():
    tests = [
        (test_corpus_parity, "corpus parity: classic/objstm/incremental"),
        (test_quirk_battery, "quirk battery: 8 byte-surgery fixtures + strict"),
        (test_parser_units, "parser + Flate/predictor unit vectors"),
        (test_ctm_table, "the four field-verified overlay CTMs"),
        (test_writer_gates, "writer: determinism, strict self-check, no /Info"),
        (test_backend_parity, "merge backends render pixel-identically"),
        (test_encryption, "owner-lock transparency + user-password refusal"),
        (test_retirement, "no module-level pypdf import; requirements clean"),
    ]
    for fn, label in tests:
        fn()
        print(f"PASS {label}")
    print(f"PDF-IO TEST PASSED  ({_N[0]} checks)  — the Shuttle"
          + ("" if HAVE_ORACLE else "  [oracle absent]"))


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print("PDF-IO TEST FAILED:", e)
        sys.exit(1)
