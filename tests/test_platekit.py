"""The Batterboard plate kit: plotted look, invariant 3, exact manifest.

Checks the drawing kit the plates are built on: the owner's table colors,
the one accent color, the FOR REVIEW stamp, on-plan note blocks that
never cover linework (verified against an independent render of the base
plate, the way verify.py checks note boxes), a manifest that lists every
string on the page, the stored page scale, table overflow, deterministic
metadata-free bytes, the phone booklet and the near-anchor finder.

Run:  python tests/test_platekit.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz                                                   # noqa: E402
import numpy as np                                            # noqa: E402

from rfi_stamper import layout, papertrail                    # noqa: E402
from rfi_stamper.batterboard import model as M                # noqa: E402
from rfi_stamper.batterboard import platekit as pk            # noqa: E402
from rfi_stamper.batterboard import sampleset as ss           # noqa: E402

TMP = tempfile.mkdtemp(prefix="platekit_")
PROJECT = {"project": "SAMPLE BUILDING", "drawn_by": "LAYOUT", "date": "2026-01-15"}


def grid():
    g = M.Grid()
    for name, e in ss.GRID_X:
        g.lines.append(M.GridLine(name=name, axis="x", offset_ft=e,
                                  world_a=(e, -8), world_b=(e, 48)))
    for name, n in ss.GRID_Y:
        g.lines.append(M.GridLine(name=name, axis="y", offset_ft=n,
                                  world_a=(-8, n), world_b=(80, n)))
    return g


def build(path, *, notes=True, issue=False, rows=12):
    ps = pk.PlateSet(sheet="ANSI B", project=PROJECT, issue=issue)
    pl = ps.plate("BB-201", "LEVEL 2 WALL PLAN")
    pl.set_plan((-1, -1, 73, 41))
    pl.grid(grid())
    for w in ss.WALLS_L2:
        pl.poly(w["poly"], fill=pk.CONCRETE_GRAY, weight="light", role="concrete")
    pl.poly([(-1, -1), (73, -1), (73, 41), (-1, 41)], weight="heavy", role="slab_edge")
    pl.box((46, 23, 50, 35), pk.data("3", src="item:L2-3"))
    asked = []
    if notes:
        for i, w in enumerate(ss.WALLS_L2):
            cx = sum(p[0] for p in w["poly"]) / len(w["poly"])
            cy = sum(p[1] for p in w["poly"]) / len(w["poly"])
            asked.append(pl.note(cx, cy, [pk.data(f"L2-W-{i + 1:03d}", src=f"element:L2-W-{i + 1:03d}"),
                                          pk.label("thickness as drawn")], accent=(i == 2)))
            asked.append(pl.tag(w["poly"][0][0], w["poly"][0][1],
                                pk.data(f"L2-10{i:02d}", src=f"point:L2-10{i:02d}")))
    drawn = pl.table([(pk.label("ID"), 1.2, "l"), (pk.label("MARK"), 0.8, "c"),
                      (pk.label("NOTE"), 3, "l")],
                     [[pk.data(f"L2-W-{i:03d}", src=f"element:L2-W-{i:03d}"), pk.data("SW1"),
                       pk.label("single source")] for i in range(1, rows + 1)],
                     title=pk.label("WALLS"))
    pl.furniture()
    pl.statements([pk.T("Local grid frame: origin at grid A/1.", "statement", proof="P-0001")])
    man = ps.save(path)
    return man, asked, drawn, pl


def render(path, dpi):
    doc = fitz.open(path)
    pix = doc[0].get_pixmap(dpi=dpi, colorspace=fitz.csRGB, alpha=False)
    a = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3).copy()
    doc.close()
    return a


def px_at(img, page_h, x, y, dpi):
    z = dpi / 72.0
    return tuple(int(v) for v in img[int((page_h - y) * z), int(x * z)])


def look():
    path = os.path.join(TMP, "plate.pdf")
    man, asked, drawn, pl = build(path)
    pg = man["pages"][0]
    img = render(path, 150)
    h = pg["page_h"]
    cells = [t for t in pg["texts"] if t["where"] == "table"]

    def modal(bb):
        z = 150 / 72.0
        r0, r1 = int((h - bb[3]) * z), int((h - bb[1]) * z) + 1
        c0, c1 = int(bb[0] * z), int(bb[2] * z) + 1
        px = img[r0:r1, c0:c1].reshape(-1, 3)
        vals, counts = np.unique(px, axis=0, return_counts=True)
        return tuple(int(v) for v in vals[counts.argmax()])

    def close(a, b):
        # the renderer rounds a color channel by at most one level
        return all(abs(x - y) <= 1 for x, y in zip(a, b))

    head = next(t for t in cells if t["s"] == "MARK")
    assert close(modal(head["bbox"]), (0x44, 0x72, 0xC4)), modal(head["bbox"])
    body = sorted((t for t in cells if t["s"] == "SW1"), key=lambda t: -t["bbox"][1])
    cols = [modal(t["bbox"]) for t in body[:4]]
    assert any(close(c, (0xD9, 0xE1, 0xF2)) for c in cols), cols
    assert any(c == (255, 255, 255) for c in cols), cols
    assert all(close(c, (0xD9, 0xE1, 0xF2)) or c == (255, 255, 255) for c in cols), cols
    w1 = ss.WALLS_L2[0]["poly"]
    cx = sum(p[0] for p in w1) / 4
    cy = sum(p[1] for p in w1) / 4
    X, Y = pl.W(cx, cy)
    g = int(round(pk.CONCRETE_GRAY * 255))
    assert px_at(img, h, X, Y + 20.0, 150) == (g, g, g), px_at(img, h, X, Y + 20.0, 150)
    acc = np.array([int(round(v * 255)) for v in pk.ACCENT])
    n_acc = int((np.abs(img.astype(int) - acc).max(axis=2) <= 1).sum())
    assert n_acc > 200, f"the open item box and its note are the accent color ({n_acc} px)"
    doc = fitz.open(path)
    text = doc[0].get_text()
    assert pk.FOR_REVIEW in text and "PLANLOOM" in text and "SAMPLE BUILDING" in text
    doc.close()
    man2, *_ = build(os.path.join(TMP, "issued.pdf"), issue=True)
    doc = fitz.open(os.path.join(TMP, "issued.pdf"))
    t2 = doc[0].get_text()
    assert pk.FOR_REVIEW not in t2 and "ISSUED" in t2
    doc.close()
    print("PASS look: header band 4472C4, banded rows D9E1F2, concrete gray, accent "
          "box, FOR REVIEW until issued")
    return man, asked


def invariant3(man, asked):
    pg = man["pages"][0]
    assert all(bool(a) for a in asked), [bool(a) for a in asked]
    blocks = pg["blocks"]
    assert len(blocks) == 12 and pg["notes"] == {"asked": 6, "placed": 6, "list_only": 0}
    base_path = os.path.join(TMP, "base.pdf")
    build(base_path, notes=False)
    gray = render(base_path, 90).mean(axis=2)
    z = 90 / 72.0
    h = pg["page_h"]
    for b in blocks:
        x0, y0, x1, y1 = b["bbox"]
        r0, r1 = int((h - y1) * z), int(np.ceil((h - y0) * z))
        c0, c1 = int(x0 * z), int(np.ceil(x1 * z))
        assert (gray[r0:r1, c0:c1] >= layout.DARK_THRESH).all(), \
            f"{b['what']} block covers content at {b['bbox']}"
    for i, a in enumerate(blocks):
        for b in blocks[i + 1:]:
            ax0, ay0, ax1, ay1 = a["bbox"]
            bx0, by0, bx1, by1 = b["bbox"]
            assert ax1 <= bx0 or bx1 <= ax0 or ay1 <= by0 or by1 <= ay0, "blocks overlap"
    vp = pg["viewport"]
    assert all(vp[0] <= b["bbox"][0] and b["bbox"][2] <= vp[2] for b in blocks)
    # no free spot within reach: the row stays in the list only, and is counted
    ps = pk.PlateSet(sheet="ANSI B", project=PROJECT)
    pl = ps.plate("BB-900", "CROWDED")
    pl.set_plan((0, 0, 40, 40))
    for i in range(0, 81):
        pl.line((i * 0.5, 0), (i * 0.5, 40), weight="light")
        pl.line((0, i * 0.5), (40, i * 0.5), weight="light")
    got = pl.note(20, 20, [pk.label("buried")], reach_in=0.3)
    m = ps.save(os.path.join(TMP, "crowded.pdf"))
    assert not got and m["pages"][0]["notes"] == {"asked": 1, "placed": 0, "list_only": 1}
    print("PASS invariant 3: every placed block sits on empty base pixels (independent "
          "render), none overlap; a buried note stays list only and is counted")


def manifest(man):
    pg = man["pages"][0]
    path = os.path.join(TMP, "plate.pdf")
    doc = fitz.open(path)
    words = [w[4] for w in doc[0].get_text("words")]
    obj = doc.xref_object(doc[0].xref)
    doc.close()
    known = set()
    for t in pg["texts"]:
        known.update(t["s"].split())
        for ln in t.get("lines", []):
            known.update(ln.split())
    missing = [w for w in words if w not in known]
    assert not missing, missing[:10]
    kinds = {t["kind"] for t in pg["texts"]}
    assert {"label", "data", "statement", "setting"} <= kinds, kinds
    st = next(t for t in pg["texts"] if t["kind"] == "statement")
    assert st["proof"] == "P-0001" and st["where"] == "band"
    tag = next(t for t in pg["texts"] if t["where"] == "tag")
    assert tag["src"].startswith("point:")
    roles = {g["role"] for g in pg["geometry"]}
    assert {"concrete", "slab_edge"} <= roles <= {"concrete", "slab_edge", "item_box", "grid"}, roles
    conc = [g for g in pg["geometry"] if g["role"] == "concrete"]
    assert len(conc) == len(ss.WALLS_L2)
    assert [tuple(p) for p in conc[0]["pts"]] == [tuple(p) for p in ss.WALLS_L2[0]["poly"]]
    tr = pg["transform"]
    assert abs(tr["k"] - pg["pt_per_ft"]) < 1e-9
    # the stored page scale for viewer measuring tools
    assert "/VP" in obj and "/Measure" in obj and "/RL" in obj, obj[:200]
    import re
    c = float(re.search(r"/X\s*\[\s*<<[^>]*?/C\s+([0-9.]+)", obj).group(1))
    assert abs(c - 1.0 / pg["pt_per_ft"]) < 1e-6, (c, pg["pt_per_ft"])
    ps = pk.PlateSet(sheet="ANSI B", project=PROJECT)
    pl = ps.plate("BB-000", "INDEX")
    pl.table([(pk.label("PLATE"), 1, "l")], [[pk.data("BB-201")]], where="full")
    ps.save(os.path.join(TMP, "index.pdf"))
    doc = fitz.open(os.path.join(TMP, "index.pdf"))
    assert "/VP" not in doc.xref_object(doc[0].xref)
    doc.close()
    print("PASS manifest: every word on the page is listed with its kind, proof and "
          "source; geometry roles in world feet; page scale stored (/VP /Measure)")


def overflow_and_bytes():
    _m, _a, drawn, _pl = build(os.path.join(TMP, "big.pdf"), rows=200)
    assert 20 < drawn < 200, drawn
    doc = fitz.open(os.path.join(TMP, "big.pdf"))
    t = doc[0].get_text()
    assert f"L2-W-{drawn:03d}" in t and f"L2-W-{drawn + 1:03d}" not in t
    doc.close()
    a, b = os.path.join(TMP, "a.pdf"), os.path.join(TMP, "b.pdf")
    build(a)
    build(b)
    assert open(a, "rb").read() == open(b, "rb").read(), "deterministic bytes"
    hits = papertrail.lint_pdf(a, PROJECT)
    assert not hits, hits
    ps = pk.PlateSet(sheet="ANSI B", project=PROJECT)
    pl = ps.plate("BB-001", "DASH")
    pl.statements([pk.label("first — second")])
    m = ps.save(os.path.join(TMP, "dash.pdf"))
    assert not papertrail.lint_pdf(os.path.join(TMP, "dash.pdf"), PROJECT)
    assert "—" not in " ".join(t["s"] for t in m["pages"][0]["texts"])
    print(f"PASS table overflow ({drawn} of 200 rows drawn, the caller continues), "
          "deterministic bytes, metadata-free, long dashes made plain")


def booklet():
    bk = pk.Booklet(project=PROJECT)
    for i, w in enumerate(ss.WALLS_L2[:3]):
        xs = [p[0] for p in w["poly"]]
        ys = [p[1] for p in w["poly"]]

        def draw(view, w=w):
            view.poly(w["poly"], fill=pk.CONCRETE_GRAY, role="concrete")

        bk.item(pk.data(f"L2-{i + 1}", src=f"item:L2-{i + 1}"), pk.label("OPEN ITEM"),
                (min(xs) - 4, min(ys) - 4, max(xs) + 4, max(ys) + 4), draw,
                [pk.label("What is wrong"), pk.label("What is needed")])
    m = bk.save(os.path.join(TMP, "booklet.pdf"))
    doc = fitz.open(os.path.join(TMP, "booklet.pdf"))
    assert doc.page_count == 3 and len(m["pages"]) == 3
    assert abs(doc[0].rect.width - 612) < 1 and abs(doc[0].rect.height - 792) < 1
    assert "L2-2" in doc[1].get_text() and pk.FOR_REVIEW in doc[1].get_text()
    doc.close()
    print("PASS booklet: one item per letter page, crop + text, stamped")


def finder():
    gray = np.full((200, 300), 255, dtype=np.uint8)
    gray[90:110, :] = 0                                  # a band of linework
    ii = layout.integral(gray)
    got = layout.find_spot_near(ii, 300, 200, 20.0, 8.0, 1.0, [], (150, 100), 80)
    assert got is not None
    x0, y0, x1, y1 = got[2]
    assert y1 <= 90 or y0 >= 110, "the window misses the band"
    assert layout._rect_sum(ii, x0, y0, x1, y1) == 0
    occ = [got[2]]
    again = layout.find_spot_near(ii, 300, 200, 20.0, 8.0, 1.0, occ, (150, 100), 80)
    ax0, ay0, ax1, ay1 = again[2]
    assert ax1 <= x0 or ax0 >= x1 or ay1 <= y0 or ay0 >= y1, "occupied is honored"
    assert layout.find_spot_near(ii, 300, 200, 20.0, 8.0, 1.0, [], (150, 100), 3) is None
    ins = layout.find_spot_near(ii, 300, 200, 20.0, 8.0, 1.0, [], (150, 100), 120,
                                inside=(0, 120, 300, 200))
    assert ins is not None and ins[2][1] >= 120
    print("PASS find_spot_near: nearest clear window, zero content pixels, honors "
          "occupied, inside and reach")


def main():
    man, asked = look()
    invariant3(man, asked)
    manifest(man)
    overflow_and_bytes()
    booklet()
    finder()
    print("PLATEKIT TEST PASSED")
    print("outputs in", TMP)


if __name__ == "__main__":
    main()
