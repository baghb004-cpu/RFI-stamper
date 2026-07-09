"""Tests for the from-scratch PDF writer (rfi_stamper.minipdf) — Phase P1 core.

The gating invariant of the whole reportlab-retirement effort is **text-metric
parity**: layout.py measures header width to decide truncation, so the writer's
string_width MUST equal reportlab's to sub-point accuracy or box geometry drifts
and verify.py FAILs.  This suite proves that against the reportlab oracle (kept
as a dev/test-only dependency — the SHIPPED engine imports no reportlab), plus
intrinsic checks (known Adobe widths, WinAnsi byte mapping, string escaping,
data checksum) that stand on their own once reportlab is gone.

Run:  python3.12 tests/test_minipdf.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rfi_stamper.minipdf import encoding, metrics          # noqa: E402
from rfi_stamper.minipdf._metrics_data import WIDTHS, WINANSI, CHECKSUM  # noqa: E402

_N = [0]


def A(cond, msg=""):
    _N[0] += 1
    assert cond, msg


# --------------------------------------------------------------------------- #
#  1. metric parity with the reportlab oracle (the gate)                      #
# --------------------------------------------------------------------------- #

def _corpus():
    """Strings that exercise the whole encoding + the app's real lettering."""
    # every printable WinAnsi character on its own (0x7F is DEL — a control
    # char core._normalize_text strips and never real lettering, and reportlab
    # is self-inconsistent at that slot; genuine text never carries it)
    chars = []
    for b in range(0x20, 0x100):
        if b == 0x7F:
            continue
        try:
            chars.append(bytes([b]).decode("cp1252"))
        except UnicodeDecodeError:
            continue
    singles = ["".join(chars)]
    app = [
        "RFI 014 — DUCT CONFLICT",
        "RFI 132 — SLAB EDGE · ANSWERED",
        "A-101 · ANSWERED",
        "E-1.10 · IN WORK",
        "GENERAL NOTES REFER TO SHEET P-101 FOR PLUMBING RISER DIAGRAM.",
        "ALL DIMENSIONS IN FEET AND INCHES. VERIFY IN FIELD.",
        "8'-6\"  ±1/4\"  90°  ½ typ.",
        "MAXIMUM (TYP.) UNLESS NOTED — SEE 3/A5.1",
        "The quick brown fox jumps over the lazy dog 0123456789",
        "«guillemets» “curly” ‘quotes’ • bullet … ellipsis",
    ]
    return singles + app


def test_metric_parity():
    try:
        from reportlab.pdfbase.pdfmetrics import stringWidth as rl_width
    except Exception:
        print("  (reportlab oracle absent — skipping parity, intrinsic checks still run)")
        return
    fonts = ["Helvetica", "Helvetica-Bold", "Helvetica-Oblique",
             "Times-Roman", "Courier"]
    sizes = [7.7, 9.2, 12.0, 13.0, 42.0]
    worst = 0.0
    n = 0
    for text in _corpus():
        for font in fonts:
            for size in sizes:
                mine = metrics.string_width(text, font, size)
                theirs = rl_width(text, font, size)
                worst = max(worst, abs(mine - theirs))
                n += 1
    A(worst < 1e-6,
      f"string_width matches reportlab across {n} cases (worst |Δ|={worst:.2e})")
    print(f"  metric parity: {n} (text,font,size) cases, worst |Δ| = {worst:.2e}")


# --------------------------------------------------------------------------- #
#  2. intrinsic width checks (no oracle) — canonical Adobe values             #
# --------------------------------------------------------------------------- #

def test_known_widths():
    H = WIDTHS["Helvetica"]
    A(H["space"] == 278, "space is 278")
    A(H["emdash"] == 1000, "em dash is 1000")
    A(H["periodcentered"] == 278, "middle dot is 278")
    A(H["ellipsis"] == 1000, "ellipsis is 1000")
    A(H["degree"] == 400, "degree is 400")
    A(WIDTHS["Courier"]["i"] == WIDTHS["Courier"]["W"] == 600,
      "Courier is monospaced (every glyph 600)")
    A(metrics.char_width("—", "Helvetica-Bold") == 1000, "char_width em dash")
    A(abs(metrics.string_width("AV", "Helvetica", 1000)
          - (WIDTHS["Helvetica"]["A"] + WIDTHS["Helvetica"]["V"])) < 1e-9,
      "no kerning: AV width is the plain sum (reportlab default)")
    A(metrics.string_width("", "Helvetica", 12) == 0.0, "empty string is width 0")


# --------------------------------------------------------------------------- #
#  3. WinAnsi encoding + PDF token escaping                                    #
# --------------------------------------------------------------------------- #

def test_encoding():
    A(encoding.to_byte("—") == 0x97, "em dash -> 0x97 (NOT its Latin-1 slot)")
    A(encoding.to_byte("–") == 0x96, "en dash -> 0x96")
    A(encoding.to_byte("·") == 0xB7, "middle dot -> 0xB7")
    A(encoding.to_byte("…") == 0x85, "ellipsis -> 0x85")
    A(encoding.to_byte("°") == 0xB0, "degree -> 0xB0")
    A(encoding.to_byte("A") == 0x41, "ASCII passes through")
    # an out-of-WinAnsi char (arrow) falls back to '?', and the width charged
    # is the '?' width so measurement and ink agree.
    A(encoding.to_byte("→") == ord("?"), "out-of-WinAnsi char -> '?'")
    A(metrics.char_width("→", "Helvetica") == WIDTHS["Helvetica"]["question"],
      "fallback char is measured at the '?' width it is drawn as")
    A(encoding.encode_winansi("A—B") == b"A\x97B", "encode em dash mid-string")

    # PDF literal string escaping of ( ) \ and control bytes
    A(encoding.pdf_string("a(b)c\\d") == b"(a\\(b\\)c\\\\d)", "escape ( ) and backslash")
    A(encoding.pdf_string("x\ry") == b"(x\\ry)", "escape carriage return")
    A(encoding.pdf_string("RFI — 1") == b"(RFI \x97 1)", "em dash byte in a literal")
    A(encoding.pdf_hexstring("A—") == b"<4197>", "hex string is winansi hex")
    A(encoding.pdf_name("F1") == b"/F1", "simple name")
    A(encoding.pdf_name("a b") == b"/a#20b", "name escapes the space")


# --------------------------------------------------------------------------- #
#  4. vendored data integrity                                                 #
# --------------------------------------------------------------------------- #

def test_data_integrity():
    import hashlib
    A(len(WINANSI) == 256, "WinAnsi table is 256 entries")
    A(len(WIDTHS) == 12, "twelve Latin standard-14 faces carried")
    A(all("question" in t for t in WIDTHS.values()),
      "every font has the fallback '?' glyph")
    h = hashlib.sha256()
    h.update(repr(list(WINANSI)).encode())
    for f in WIDTHS:
        h.update(repr(sorted(WIDTHS[f].items())).encode())
    A(h.hexdigest() == CHECKSUM, "metrics data matches its checksum (untampered)")


# --------------------------------------------------------------------------- #
#  5. emit a real PDF — valid, renderable, deterministic, metadata-free        #
# --------------------------------------------------------------------------- #

def _sample_pdf():
    from rfi_stamper.minipdf import Document
    doc = Document()
    pg = doc.add_page(612, 792)
    c = pg.content
    # a thin red-outlined white-filled rectangle (the note-box style)
    c.save()
    c.fill_rgb(1, 1, 1).stroke_rgb(0.84, 0.06, 0.06).line_width(1.2)
    c.rect(72, 680, 320, 40).fill_stroke()
    # bold red header + body inside it
    c.fill_rgb(0.84, 0.06, 0.06)
    c.text(78, 705, "RFI 014 — DUCT CONFLICT", "Helvetica-Bold", 9.2)
    c.text(78, 690, "Reroute below joist · ANSWERED", "Helvetica", 7.7)
    c.restore()
    # a second page (exercises the page tree + shared font dict)
    doc.add_page(612, 792).content.text(80, 700, "SHEET 2", "Helvetica", 12)
    return doc.to_bytes()


def test_emit_valid_pdf():
    import fitz
    data = _sample_pdf()
    A(data.startswith(b"%PDF-1.4\n%"), "header + binary marker")
    A(data.rstrip().endswith(b"%%EOF"), "ends at %%EOF")
    A(b"/Producer" not in data and b"/CreationDate" not in data,
      "no metadata leak (no Producer/CreationDate)")

    doc = fitz.open(stream=data, filetype="pdf")
    A(doc.page_count == 2, "two pages")
    # mutool/fitz round-trip: the text is really there and readable
    words = [w[4] for w in doc[0].get_text("words")]
    joined = " ".join(words)
    A("DUCT" in joined and "CONFLICT" in joined, f"header text round-trips, got {joined!r}")
    A("ANSWERED" in joined, "body text round-trips")
    A("SHEET" in " ".join(w[4] for w in doc[1].get_text("words")), "page 2 text round-trips")

    # the em dash survived as a real dash, not mojibake or a dropped glyph
    txt = doc[0].get_text("text")
    A("—" in txt, f"em dash round-trips through WinAnsi, got {txt!r}")

    # the red box actually rendered (a red pixel exists where the border is)
    pix = doc[0].get_pixmap(dpi=72)
    found_red = any(
        pix.pixel(x, y)[0] > 150 and pix.pixel(x, y)[1] < 90 and pix.pixel(x, y)[2] < 90
        for y in range(70, 115) for x in range(72, 393)
    )
    A(found_red, "the red note-box border rendered")
    doc.close()


def test_deterministic():
    A(_sample_pdf() == _sample_pdf(), "identical input -> byte-identical output")
    # the content hash /ID reflects content: a different string changes bytes
    from rfi_stamper.minipdf import Document
    d = Document(); d.add_page(612, 792).content.text(80, 700, "OTHER", "Helvetica", 12)
    A(d.to_bytes() != _sample_pdf(), "different content -> different bytes")


def test_xref_offsets():
    """The single most common hand-writer bug: byte-exact xref offsets."""
    import re
    data = _sample_pdf()
    m = re.search(rb"startxref\n(\d+)\n%%EOF", data)
    A(m is not None, "startxref present")
    A(data[int(m.group(1)):int(m.group(1)) + 4] == b"xref", "startxref points at 'xref'")
    # every 'N 0 obj' sits exactly at the offset its xref record claims
    xref = data[int(m.group(1)):]
    rows = re.findall(rb"(\d{10}) 00000 n ", xref)
    A(len(rows) >= 5, f"xref lists the in-use objects, got {len(rows)}")
    for i, off in enumerate(rows, start=1):
        at = int(off)
        A(data[at:at + len(b"%d 0 obj" % i)] == b"%d 0 obj" % i,
          f"object {i} really begins at its xref byte offset {at}")


def test_external_conformance():
    """If the qpdf validator is on PATH (dev/CI), the file must check clean.

    Skipped when absent so the suite never hard-depends on an external binary
    (the shipped engine has none); qpdf is a dev-only cross-check per the plan.
    """
    import shutil
    import subprocess
    import tempfile
    qpdf = shutil.which("qpdf")
    if not qpdf:
        print("  (qpdf absent — external conformance check skipped)")
        return
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(_sample_pdf())
        path = f.name
    try:
        r = subprocess.run([qpdf, "--check", path], capture_output=True, text=True)
        out = r.stdout + r.stderr
        A("No syntax or stream encoding errors found" in out,
          f"qpdf --check reports the file clean:\n{out}")
        print("  qpdf --check: clean")
    finally:
        os.unlink(path)


# --------------------------------------------------------------------------- #
#  6. flow-engine pagination hardening (audit round)                           #
# --------------------------------------------------------------------------- #

def test_flow_pagination():
    import io
    import fitz
    from rfi_stamper.minipdf.colors import black
    from rfi_stamper.minipdf.flow import (Paragraph, ParagraphStyle,
                                          SimpleDocTemplate, Spacer, Table,
                                          TableStyle)
    body = ParagraphStyle("b", fontName="Helvetica", fontSize=10, leading=12,
                          textColor=black)

    # (a) a table whose header+first row can't fit the page remainder is
    # DEFERRED to a fresh page — never forced into the bottom margin
    tall_cell = Paragraph("<br/>".join(f"line {i}" for i in range(12)), body)
    table = Table([[Paragraph("HDR", body)], [tall_cell]],
                  colWidths=[300.0], repeatRows=1,
                  style=TableStyle([("TOPPADDING", (0, 0), (-1, -1), 2),
                                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=(400, 300), leftMargin=40,
                            rightMargin=40, topMargin=40, bottomMargin=60)
    total = doc.build([Spacer(0, 150), table])   # slot left: ~50pt, row ~150pt
    A(total == 2, f"tall first row defers the table to page 2, got {total}")
    d = fitz.open(stream=buf.getvalue(), filetype="pdf")
    A("HDR" not in d[0].get_text("text"), "nothing of the table on page 1")
    p2 = d[1].get_text("text")
    A("HDR" in p2 and "line 11" in p2, "header + full row on page 2")
    # nothing rendered below the bottom margin on page 2 (y > page_h - margin)
    max_y = max(w[3] for w in d[1].get_text("words"))
    A(max_y <= 300 - 60 + 12, f"no text into the bottom margin (max y {max_y:.0f})")
    d.close()

    # (b) a paragraph taller than a whole page SPLITS across pages
    long_para = Paragraph("<br/>".join(f"row {i}" for i in range(40)), body)
    buf2 = io.BytesIO()
    doc2 = SimpleDocTemplate(buf2, pagesize=(400, 300), leftMargin=40,
                             rightMargin=40, topMargin=40, bottomMargin=60)
    total2 = doc2.build([long_para])             # 40*12=480pt > 200pt frame
    A(total2 >= 2, f"page-taller paragraph paginates, got {total2}")
    d2 = fitz.open(stream=buf2.getvalue(), filetype="pdf")
    alltext = "".join(d2[i].get_text("text") for i in range(d2.page_count))
    missing = [i for i in range(40) if f"row {i}" not in alltext]
    A(not missing, f"no paragraph line lost across the split (missing {missing[:4]})")
    A("row 0" in d2[0].get_text("text") and "row 39" not in d2[0].get_text("text"),
      "the split really spans pages")
    d2.close()


def test_canvas_guards():
    import io
    from rfi_stamper.minipdf import Canvas, fmt_num
    A(fmt_num(True) == "1" and fmt_num(False) == "0",
      "bool coerces to a PDF numeric, never the token 'True'")
    c = Canvas(io.BytesIO(), pagesize=(200, 200))
    c.saveState()
    try:
        c.showPage()
        A(False, "showPage with an open saveState must raise")
    except ValueError:
        pass
    c.restoreState()
    try:
        c.restoreState()
        A(False, "restoreState underflow must raise")
    except ValueError:
        pass


def main():
    for fn, label in [
        (test_metric_parity, "text-metric parity with the reportlab oracle"),
        (test_known_widths, "canonical Adobe widths + no-kerning"),
        (test_encoding, "WinAnsi encoding + PDF token escaping"),
        (test_data_integrity, "vendored metrics data integrity"),
        (test_emit_valid_pdf, "emit a valid, renderable PDF (fitz round-trip)"),
        (test_deterministic, "deterministic byte-reproducible output"),
        (test_xref_offsets, "byte-exact cross-reference offsets"),
        (test_external_conformance, "qpdf --check conformance (advisory)"),
        (test_flow_pagination, "flow pagination: table defer + paragraph split"),
        (test_canvas_guards, "canvas state guards + bool numeric coercion"),
    ]:
        fn()
        print(f"PASS {label}")
    print(f"MINIPDF P1 METRICS TEST PASSED  ({_N[0]} checks)")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print("MINIPDF TEST FAILED:", e)
        sys.exit(1)
