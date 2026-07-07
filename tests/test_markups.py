"""Self-contained tests for rfi_stamper.markups (run: python3.12 tests/test_markups.py)."""
from __future__ import annotations

import csv
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz  # noqa: E402

from rfi_stamper.markups import (MARKUP_TYPES, STATUSES, Markup, MarkupStore,  # noqa: E402
                                 ScaleCal, Style, ToolChest, ToolPreset,
                                 apply_to_pdf, area, caption_for,
                                 cloud_path_points, compute, fmt_value, length,
                                 multiply, polylength)

TD = tempfile.mkdtemp(prefix="markups_test_")


def approx(a, b, tol=1e-6):
    return abs(a - b) <= tol


# ------------------------------------------------------- model / store ---

def test_model_store():
    m = Markup.new(1, "rect", [(10, 20), (110, 80)],
                   subject="café ✓ 日本語", comment="unicode ünïcode",
                   author="pm", style=Style(color="#00FF00", width=3.0))
    assert m.id and m.created and m.status == "none"
    assert m.bbox() == (10, 20, 110, 80)

    t = m.translated(5, -5)
    assert t.id != m.id
    assert t.points == [(15.0, 15.0), (115.0, 75.0)]
    assert m.points == [(10.0, 20.0), (110.0, 80.0)]  # original untouched

    # JSON round trip incl. unicode
    d = m.to_dict()
    m2 = Markup.from_dict(d)
    assert m2.to_dict() == d
    assert m2.subject == "café ✓ 日本語"
    assert m2.style.color == "#00FF00" and approx(m2.style.width, 3.0)

    try:
        Markup.new(1, "bogus", [(0, 0)])
        raise AssertionError("bad type accepted")
    except ValueError:
        pass

    pdf_path = os.path.join(TD, "doc.pdf")
    store = MarkupStore(pdf_path)  # sidecar does not exist yet
    assert store.markups == []
    store.add(m)
    store.add(Markup.new(2, "text", [(50, 50)], text="verify in field",
                         author="arch"))
    store.set_status(m.id, "accepted")
    store.set_status(m.id, "completed")
    assert store.get(m.id).status == "completed"
    hist = store.get(m.id).status_history
    assert [s for s, _ in hist] == ["accepted", "completed"]
    assert all(ts for _, ts in hist)
    try:
        store.set_status(m.id, "maybe")
        raise AssertionError("bad status accepted")
    except ValueError:
        pass

    # search: case-insensitive over subject/comment/text/type/status/author
    assert store.search("CAFÉ") == [m]
    assert store.search("VERIFY") and store.search("verify")[0].page == 2
    assert store.search("completed") == [m]
    assert store.search("arch") != [] and store.search("zzz-none") == []

    # save/load via sidecar + autoload
    store.save()
    assert os.path.exists(MarkupStore.sidecar_path(pdf_path))
    store2 = MarkupStore(pdf_path)  # autoload
    assert len(store2.markups) == 2
    r = store2.get(m.id)
    assert r.subject == "café ✓ 日本語"
    assert r.status == "completed" and len(r.status_history) == 2
    assert r.status_history[0][0] == "accepted"

    # CSV both modes
    csv1 = os.path.join(TD, "out1.csv")
    store.to_csv(csv1, latest_status_only=True)
    with open(csv1, encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    assert rows[0] == ["page", "type", "subject", "comment", "text", "status",
                       "author", "created", "measure_value", "measure_unit",
                       "caption"]
    assert rows[1][2] == "café ✓ 日本語" and rows[1][5] == "completed"
    assert "status_history" not in rows[0]

    csv2 = os.path.join(TD, "out2.csv")
    store.to_csv(csv2, latest_status_only=False)
    with open(csv2, encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    assert rows[0][-1] == "status_history"
    assert "accepted@" in rows[1][-1] and "; completed@" in rows[1][-1]

    store.remove(m.id)
    assert store.get(m.id) is None and len(store.markups) == 1
    assert store.for_page(2) == store.markups


# ------------------------------------------------------------- cloud ---

def test_cloud_path():
    pts = cloud_path_points(100, 100, 300, 200, r=10)
    assert pts[0] == pts[-1]                       # closed
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    assert min(xs) < 100 and max(xs) > 300         # bulges outward in x
    assert min(ys) < 100 and max(ys) > 200         # bulges outward in y
    assert len(pts) > 40                           # dense polyline
    # corner points of the rect lie on the path envelope, arcs never dip inside
    # farther than the rect edge midpoints do (spot check a top-edge sample)
    top = [p for p in pts if 140 < p[0] < 160 and p[1] < 100]
    assert top, "top edge scallop should rise above y=100"

    # regression: degenerate rects must not divide by zero
    z = cloud_path_points(50, 50, 50, 50)          # single point
    assert z and z[0] == z[-1]
    v = cloud_path_points(50, 10, 50, 200)         # zero-width (vertical line)
    assert len(v) > 2 and v[0] == v[-1]


# ---------------------------------------------------------- multiply ---

def test_multiply():
    m = Markup.new(1, "count", [(50, 60)], subject="fixture", text="A")
    m.status = "accepted"
    m.status_history = [("accepted", "2026-01-01T00:00:00")]
    m.measure_value = 1.0
    lin = multiply(m, copies=3, dx=10, dy=5)
    assert len(lin) == 3
    for i, c in enumerate(lin, start=1):
        assert c.points == [(50 + 10 * i, 60 + 5 * i)]
        assert c.id != m.id and c.status == "none" and c.status_history == []
        assert c.subject == "fixture" and c.text == "A"
        assert approx(c.measure_value, 1.0)   # copied, not recomputed
    assert len({c.id for c in lin}) == 3

    grid = multiply(m, copies=99, dx=10, dy=7, rows=2, cols=3)
    assert len(grid) == 5                       # 2x3 minus original cell
    offs = sorted((round(c.points[0][0] - 50), round(c.points[0][1] - 60))
                  for c in grid)
    assert offs == [(0, 7), (10, 0), (10, 7), (20, 0), (20, 7)]

    try:
        multiply(m, copies=0, dx=1, dy=1)
        raise AssertionError("copies=0 accepted")
    except ValueError:
        pass


# ------------------------------------------------------------ measure ---

def test_measure():
    # calibrate: 100 pt drawn segment measures 25 ft -> 0.25 ft/pt
    cal = ScaleCal.calibrate((0, 0), (100, 0), 25.0, "ft")
    assert approx(cal.real_per_pt, 0.25) and cal.unit == "ft"
    cal2 = ScaleCal.from_dict(cal.to_dict())
    assert approx(cal2.real_per_pt, 0.25) and cal2.unit == "ft"
    for bad in (((0, 0), (0, 0), 5.0), ((0, 0), (1, 1), 0.0)):
        try:
            ScaleCal.calibrate(bad[0], bad[1], bad[2], "ft")
            raise AssertionError("bad calibration accepted")
        except ValueError:
            pass

    assert approx(length([(0, 0), (30, 40)], cal), 50 * 0.25)
    # length is exactly first-to-last even with middle points
    assert approx(length([(0, 0), (999, 999), (30, 40)], cal), 12.5)
    assert approx(polylength([(0, 0), (30, 40), (30, 140)], cal), (50 + 100) * 0.25)

    # shoelace area of a rectangle: 100x50 pt at 0.1 ft/pt -> 50 sq ft
    calA = ScaleCal(real_per_pt=0.1, unit="ft")
    assert approx(area([(0, 0), (100, 0), (100, 50), (0, 50)], calA), 50.0)

    # ft-in formatting: 144 pt with 1/12 ft-per-pt -> 12'-0"
    calf = ScaleCal.calibrate((0, 0), (144, 0), 12.0, "ft-in")
    v = length([(0, 0), (144, 0)], calf)
    assert fmt_value(v, "ft-in") == "12'-0\""
    assert fmt_value(12 + 3.5 / 12, "ft-in") == "12'-3 1/2\""
    assert fmt_value(1234.53, "ft", "area") == "1,234.5 sf"
    assert fmt_value(1234.5, "ft-in", "area") == "1,234.5 sf"
    assert fmt_value(12.34, "m") == "12.34 m"
    assert fmt_value(12.34, "m", "area") == "12.34 m²"
    assert fmt_value(7.2, "", "count") == "7"

    ml = Markup.new(1, "measure_length", [(0, 0), (144, 0)])
    assert approx(compute(ml, calf), 12.0)
    ma = Markup.new(1, "measure_area", [(0, 0), (100, 0), (100, 50), (0, 50)])
    assert approx(compute(ma, calA), 50.0)
    mc = Markup.new(1, "count", [(5, 5)])
    assert approx(compute(mc, calA), 1.0)
    assert approx(compute(Markup.new(1, "rect", [(0, 0), (1, 1)]), calA), 0.0)

    # captions: defaults, placeholders, unknown placeholder untouched
    ml.subject = "Duct run"
    assert caption_for(ml, calf) == "12'-0\""       # default template {value}
    ml.caption_template = "{subject}: {value} ({raw}) {nope} {unit}"
    cap = caption_for(ml, calf)
    assert cap == "Duct run: 12'-0\" (12.0) {nope} ft-in"
    # no cal: falls back to stored measure_value/unit
    ml.measure_value, ml.measure_unit = 3.0, "m"
    ml.caption_template = "{value}|{page}|{type}|{status}"
    assert caption_for(ml) == "3.00 m|1|measure_length|none"
    mc.text = "Sprinkler"
    assert caption_for(mc) == "Sprinkler"           # count default {text}
    assert caption_for(Markup.new(1, "rect", [(0, 0), (1, 1)])) == ""


# ---------------------------------------------------------- toolchest ---

def test_toolchest():
    path = os.path.join(TD, "chest", "toolchest.json")
    tc = ToolChest(path)
    assert len(tc.presets) >= 8                    # seeded with defaults
    names = [p.name for p in tc.presets]
    assert "Revision Cloud" in names and "Area Takeoff" in names
    assert all(p.type in MARKUP_TYPES for p in tc.presets)

    tc.add(ToolPreset("Wet Wall Note", "text", Style(color="#0000AA"),
                      subject="Plumbing", text="WET WALL"))
    tc.save()
    tc2 = ToolChest(path)                          # loads from file
    assert len(tc2.presets) == len(tc.presets)
    assert "Wet Wall Note" in [p.name for p in tc2.presets]

    hits = tc2.search("cloud")
    assert hits and all("cloud" in (p.name + p.type).lower() for p in hits)
    assert tc2.search("PLUMB")[0].name == "Wet Wall Note"
    tc2.remove("Wet Wall Note")
    assert not tc2.search("plumb")

    preset = [p for p in tc2.presets if p.name == "Revision Cloud"][0]
    mk = tc2.make_markup(preset, 3, [(10, 10), (60, 40)])
    assert mk.type == "cloud" and mk.page == 3 and mk.id
    assert mk.style.color == preset.style.color
    mk.style.width = 99.0                          # instance style is a copy
    assert preset.style.width != 99.0


# ---------------------------------------------------------- PDF write ---

def _red_png(path):
    pm = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 12, 12))
    pm.set_rect(pm.irect, (220, 30, 30))
    pm.save(path)


def _red_bbox(pix, r_min=150, g_max=120, b_max=120, step=2):
    found = []
    for y in range(0, pix.height, step):
        for x in range(0, pix.width, step):
            r, g, b = pix.pixel(x, y)
            if r > r_min and g < g_max and b < b_max:
                found.append((x, y))
    if not found:
        return None
    xs = [p[0] for p in found]
    ys = [p[1] for p in found]
    return (min(xs), min(ys), max(xs), max(ys))


def test_apply_to_pdf():
    src = os.path.join(TD, "two_page.pdf")
    doc = fitz.open()
    doc.new_page(width=612, height=792)
    p2 = doc.new_page(width=612, height=792)
    p2.set_rotation(90)                            # /Rotate 90
    doc.save(src)
    doc.close()

    img = os.path.join(TD, "red.png")
    _red_png(img)

    red = Style(color="#D01414", width=3.0)
    mks = [
        Markup.new(1, "pen", [(20, 20), (40, 35), (60, 20)], style=red),
        Markup.new(1, "highlighter", [(20, 60), (120, 60)],
                   style=Style(color="#F5D400", width=4.0)),
        Markup.new(1, "line", [(20, 90), (120, 90)], style=red),
        Markup.new(1, "arrow", [(20, 120), (120, 120)], style=red),
        Markup.new(1, "ellipse", [(150, 20), (220, 60)], style=red),
        Markup.new(1, "cloud", [(150, 90), (260, 150)], style=red,
                   subject="revision"),
        Markup.new(1, "text", [(300, 30)], text="NOTE HERE", style=red,
                   author="pm", subject="note", comment="check this"),
        Markup.new(1, "text", [(300, 60)], text="Größe ✓ 日本語 <&>",
                   subject="unicode",
                   style=Style(color="not-hex", fill="#ZZZZZZ")),  # bad colors ok
        Markup.new(1, "callout", [(300, 90), (380, 150)], text="SEE DETAIL",
                   style=red),
        Markup.new(1, "image", [(400, 400), (460, 460)], image_path=img),
        Markup.new(1, "measure_length", [(20, 200), (164, 200)],
                   measure_value=12.0, measure_unit="ft-in", style=red),
        Markup.new(1, "measure_area",
                   [(20, 260), (120, 260), (120, 320), (20, 320)],
                   measure_value=50.0, measure_unit="ft", style=red),
        Markup.new(1, "count", [(500, 200)], text="7", style=red),
        # page 2 (rotated): rect at a known viewer location
        Markup.new(2, "rect", [(100, 50), (200, 120)], style=red),
    ]
    out = os.path.join(TD, "annotated.pdf")
    res = apply_to_pdf(src, out, mks, log=lambda *a: None)
    assert res["out_path"] == out and res["annots"] >= 15

    doc = fitz.open(out)
    page1, page2 = doc[0], doc[1]
    a1 = list(page1.annots())
    types1 = sorted(a.type[1] for a in a1)
    assert types1.count("Ink") == 2                # pen + highlighter
    assert types1.count("Line") == 4               # line, arrow, callout, measure
    assert types1.count("Circle") == 2             # ellipse + count dot
    assert types1.count("Polygon") == 2            # cloud + area
    assert types1.count("FreeText") == 6   # 2 texts, callout, 2 labels, count
    assert len(a1) == 16

    # info + colors
    # regression: a FreeText annot DISPLAYS its /Contents, so the comment must
    # NOT clobber the visible text of a 'text' markup that carries a comment
    ft = [a for a in a1 if a.type[1] == "FreeText" and "NOTE HERE" in a.get_text()]
    assert ft, "text markup lost its displayed text"
    note = [a for a in a1 if a.info.get("subject") == "note"]
    assert note and note[0].info["title"] == "pm"
    assert "check this" not in note[0].get_text()  # comment stays out of display
    assert any("Größe" in a.get_text() and "<&>" in a.get_text()
               for a in a1 if a.type[1] == "FreeText")  # unicode survives
    inks = [a for a in a1 if a.type[1] == "Ink"]
    ink_cols = [tuple(round(c, 2) for c in a.colors["stroke"]) for a in inks]
    assert (0.82, 0.08, 0.08) in ink_cols          # #D01414 pen
    assert (0.96, 0.83, 0.0) in ink_cols           # #F5D400 highlighter
    hl = [a for a in inks if round(a.colors["stroke"][0], 2) == 0.96][0]
    assert approx(hl.opacity, 0.35, 0.01) and hl.border["width"] > 8  # 3x width
    # measure caption text landed in a label + info
    labels = [a.get_text() for a in a1 if a.type[1] == "FreeText"]
    assert any("12'-0\"" in t for t in labels)
    assert any("50.0 sf" in t for t in labels)

    # rotated page: exactly one Square, red, and it RENDERS at the viewer spot
    a2 = list(page2.annots())
    assert [a.type[1] for a in a2] == ["Square"]
    sq = a2[0]
    assert tuple(round(c, 2) for c in sq.colors["stroke"]) == (0.82, 0.08, 0.08)
    pix2 = page2.get_pixmap()
    assert (pix2.width, pix2.height) == (792, 612)  # viewer orientation
    bb = _red_bbox(pix2)
    assert bb is not None, "rect on rotated page did not render"
    x0, y0, x1, y1 = bb
    assert 90 <= x0 <= 110 and 190 <= x1 <= 210, f"rect x off: {bb}"
    assert 40 <= y0 <= 60 and 110 <= y1 <= 130, f"rect y off: {bb}"

    # image embedded as page content: non-white where placed
    pix1 = page1.get_pixmap()
    r, g, b = pix1.pixel(430, 430)
    assert r > 150 and g < 120 and b < 120, f"image pixel {(r, g, b)}"
    doc.close()

    # flatten path must not crash and output must reopen
    out_flat = os.path.join(TD, "flat.pdf")
    apply_to_pdf(src, out_flat, mks, flatten=True, log=lambda *a: None)
    doc = fitz.open(out_flat)
    assert doc.page_count == 2
    doc.close()


if __name__ == "__main__":
    test_model_store()
    test_cloud_path()
    test_multiply()
    test_measure()
    test_toolchest()
    test_apply_to_pdf()
    print("MARKUPS TESTS PASSED")
