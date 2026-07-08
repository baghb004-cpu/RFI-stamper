"""Self-contained tests for rfi_stamper.draft — The Loft drafting engine.
Plain python, no pytest, no project data.  Exercises:

* feet-inches formatting/parsing round trips (fractions reduced, negatives)
* model CRUD, undo/redo depth + redo-cleared-on-new-edit, dirty flag
* doors/windows as host-parametric entities: move-with-host, t re-solve,
  wall openings break the host faces
* Plumbline snaps: priority (end > x > mid), true intersections,
  perpendicular from an anchor, ortho projection
* grid labeling: numeric + alpha sequences, I/O skipped, Z -> AA
* wall face offset geometry
* save/load round trip (all ten entity kinds), atomic writes
* stats / takeoff (+ price book attach), to_bim counts, grid_points labels
* render_ops: dim text, hidden-ply culling, paper-size conversion,
  per-entity include filter, stencil_ops tuple parity
* plate PDF (real PDF, sheet size, auto-fit scale drop), DXF R12, PNG

Run:  python3.12 tests/test_draft.py
"""
import json
import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rfi_stamper.draft import (                    # noqa: E402
    DraftModel, GRID_BUBBLE_IN, LINETYPES, SCALES, SHEET_SIZES, STENCILS,
    UNDO_LIMIT, WALL_TYPES, fmt_ftin, grid_points, offset_pair, parse_ftin,
    plate_pdf, render_ops, snap, stencil_ops, takeoff_lines, text_model_h,
    to_bim, to_dxf, to_png, wall_openings)

TMP = tempfile.mkdtemp(prefix="loft_test_")


# ------------------------------------------------------------ feet-inches --

def test_ftin():
    assert fmt_ftin(20.0) == "20'-0\"", fmt_ftin(20.0)
    assert fmt_ftin(12.375) == "12'-4 1/2\"", fmt_ftin(12.375)
    assert fmt_ftin(0.5) == "0'-6\"", fmt_ftin(0.5)
    # fraction reduces: 4/16 -> 1/4
    assert "1/4" in fmt_ftin(10.0 + 0.25 / 12.0)
    neg = fmt_ftin(-3.5)
    assert "3" in neg and "6" in neg
    assert abs(parse_ftin("12'-4 1/2\"") - 12.375) < 1e-9
    assert abs(parse_ftin('42"') - 3.5) < 1e-9
    assert abs(parse_ftin("3.5") - 3.5) < 1e-9
    assert abs(parse_ftin("12' 6") - 12.5) < 1e-9
    assert abs(parse_ftin("4 1/2\"") - 0.375) < 1e-9
    assert parse_ftin("garbage") is None
    # round trip at 1/16" precision
    for v in (0.0, 1.0, 7.3125, 100.0 + 11.0 / 12.0):
        assert abs(parse_ftin(fmt_ftin(v)) - v) < (1.0 / 16.0 / 12.0) + 1e-9


# ------------------------------------------------------------------ model --

def build_model():
    m = DraftModel()
    w1 = m.add("wall", [(0, 0), (20, 0)], wtype="stud4")
    w2 = m.add("wall", [(20, 0), (20, 12)], wtype="cmu8")
    d1 = m.add("door", [], host=w1.id, t=0.5, width_in=36.0,
               swing="in", hand="l")
    m.add("window", [], host=w2.id, t=0.5, width_in=48.0)
    m.add("fixture", [(5, 5)], stencil="wc", rot=0.0, flip=False)
    m.add("grid", [(2, -2), (2, 14)], label="1", bubble="both")
    m.add("grid", [(8, -2), (8, 14)], label="2", bubble="both")
    m.add("grid", [(-2, 6), (22, 6)], label="A", bubble="both")
    m.add("dim", [(0, 0), (20, 0), (10, -3)])
    m.add("room", [(10, 6)], name="LOBBY", number="101")
    m.add("text", [(3, 10)], text="TEST NOTE", size="body")
    m.add("callout", [(15, 10)], detail="2", sheet="A-501")
    m.add("line", [(0, 14), (6, 14), (6, 18)])
    return m, w1, w2, d1


def test_model_crud_undo():
    m, w1, w2, d1 = build_model()
    n = len(m.ents)
    assert n == 13, n
    assert m.dirty
    # update
    assert m.update(w1.id, wtype="cmu12",
                    thick_in=WALL_TYPES["cmu12"]["thick_in"])
    assert m.entity(w1.id).props["wtype"] == "cmu12"
    # removing a wall cascades to its hosted window (no orphan doors/windows)
    assert m.remove([w2.id]) == 2
    assert m.entity(w2.id) is None
    assert not [e for e in m.ents if e.kind == "window"]
    assert m.undo()
    assert m.entity(w2.id) is not None
    assert [e for e in m.ents if e.kind == "window"]
    # redo removes again; a fresh edit clears the redo lane
    assert m.redo()
    assert m.entity(w2.id) is None
    assert m.undo()
    m.add("text", [(0, 0)], text="X", size="body")
    assert not m.redo()
    # deep depth: 40 adds, 40 undos, 40 redos
    base = len(m.ents)
    for i in range(40):
        m.add("text", [(i, 0)], text=str(i), size="body")
    for _ in range(40):
        assert m.undo()
    assert len(m.ents) == base
    for _ in range(40):
        assert m.redo()
    assert len(m.ents) == base + 40
    assert UNDO_LIMIT >= 1000


def test_doors_ride_hosts():
    m, w1, w2, d1 = build_model()

    def hinge():
        host = m.entity(m.entity(d1.id).props["host"])
        t = float(m.entity(d1.id).props["t"])
        (ax, ay), (bx, by) = host.pts
        return (ax + t * (bx - ax), ay + t * (by - ay))

    hx, hy = hinge()
    assert abs(hx - 10.0) < 1e-9 and abs(hy) < 1e-9
    # moving the WALL carries the door (t unchanged)
    m.move([w1.id], 5.0, 0.0)
    assert abs(float(m.entity(d1.id).props["t"]) - 0.5) < 1e-9
    hx, hy = hinge()
    assert abs(hx - 15.0) < 1e-9, hx
    # moving the DOOR alone re-solves t against the same host
    m.move([d1.id], 2.0, 0.0)
    t = float(m.entity(d1.id).props["t"])
    assert abs(t - 0.6) < 1e-6, t
    # the host wall faces break around the leaf
    spans = wall_openings(m, w1.id)
    assert len(spans) == 1, spans
    t0, t1 = spans[0]
    assert 0.0 < t0 < t1 < 1.0
    assert abs((t1 - t0) - 3.0 / 20.0) < 0.02, spans


def test_snaps():
    m = DraftModel()
    m.add("wall", [(0, 0), (20, 0)], wtype="stud4")
    m.add("wall", [(10, -5), (10, 5)], wtype="stud4")
    # endpoint wins
    h = snap(m, 0.05, -0.03, 0.5)
    assert h and h.kind == "end" and abs(h.x) < 1e-9 and abs(h.y) < 1e-9
    # true intersection at (10,0) outranks wall-1's midpoint there
    h = snap(m, 10.1, 0.05, 0.5)
    assert h and h.kind == "x", h
    assert abs(h.x - 10.0) < 1e-9 and abs(h.y) < 1e-9
    # midpoint of the vertical wall at (10, 0)?  that IS the intersection —
    # use wall 2's quarter point region to grab a plain 'near-free' midpoint
    h = snap(m, 0.02, 0.04, 0.5, enabled={"mid", "end"})
    assert h.kind == "end"
    h = snap(m, 10.03, 4.96, 0.5, enabled={"end"})
    assert h and h.kind == "end" and abs(h.y - 5.0) < 1e-9
    # perpendicular foot from the anchor
    h = snap(m, 6.0, 0.2, 0.5, anchor=(6.0, -4.0), enabled={"perp"})
    assert h and h.kind == "perp" and abs(h.x - 6.0) < 1e-9 \
        and abs(h.y) < 1e-9, h
    # ortho projection when nothing else bites
    h = snap(m, 14.7, 8.0, 0.2, anchor=(14.5, 0.0), ortho=True,
             enabled=set())
    assert h and h.kind == "ortho" and abs(h.x - 14.5) < 1e-9, h
    # snapped coordinates are analytic, not the raw cursor
    h = snap(m, 19.96, 0.04, 0.5)
    assert (h.x, h.y) == (20.0, 0.0)


def test_grid_labels():
    m = DraftModel()
    for i in range(8):
        m.add("grid", [(i, 0), (i, 10)],
              label=m.next_grid_label("alpha"), bubble="both")
    labels = [e.props["label"] for e in m.ents]
    assert labels == list("ABCDEFGH"), labels
    assert m.next_grid_label("alpha") == "J"      # I skipped
    m.add("grid", [(30, 0), (30, 10)], label="Z", bubble="both")
    assert m.next_grid_label("alpha") == "AA"
    assert m.next_grid_label("num") == "1"
    m.add("grid", [(0, 5), (30, 5)], label="1", bubble="both")
    assert m.next_grid_label("num") == "2"


def test_offset_geometry():
    (a1, b1), (a2, b2) = offset_pair((0, 0), (10, 0), 0.5)
    ys = sorted([a1[1], a2[1]])
    assert abs(ys[0] + 0.5) < 1e-9 and abs(ys[1] - 0.5) < 1e-9


def test_save_load_atomic():
    m, *_ = build_model()
    p = os.path.join(TMP, "round.loft.json")
    m.save(p)
    assert os.path.exists(p) and not os.path.exists(p + ".part")
    assert not m.dirty
    raw = json.load(open(p, encoding="utf-8"))
    assert raw.get("planloom_loft") == 1
    m2 = DraftModel.load(p)
    assert len(m2.ents) == len(m.ents)
    assert {e.kind for e in m2.ents} == {e.kind for e in m.ents}
    assert [pl.name for pl in m2.plies] == [pl.name for pl in m.plies]
    assert m2.scale_ratio == m.scale_ratio


def test_stats_takeoff_bridges():
    m, *_ = build_model()
    s = m.stats()
    assert abs(s["wall_lf"] - 32.0) < 1e-9, s
    assert s["doors"] == 1 and s["windows"] == 1 and s["rooms"] == 1
    assert s["fixtures"] == {"wc": 1}
    # takeoff lines + price book attach
    from rfi_stamper.reckoner import PriceBook
    book_csv = os.path.join(TMP, "book.csv")
    with open(book_csv, "w", encoding="utf-8") as f:
        f.write("code,description,unit,cost\n"
                "P-WC,\"Water closet, tank type\",ea,850\n")
    book = PriceBook(book_csv)
    lines = takeoff_lines(m, book)
    assert lines
    wc = [ln for ln in lines if "closet" in ln.subject.lower()]
    assert wc and abs(wc[0].unit_cost - 850.0) < 1e-9, wc
    lf = [ln for ln in lines if ln.kind == "length"]
    assert abs(sum(ln.qty for ln in lf) - 32.0) < 1e-9
    # 3D: 2 walls x 2 edges x 2 floors + columns (3 unique corners x 2)
    b3 = to_bim(m, wall_height=9.0, floors=2)
    assert len(b3.segments) == 2 * 2 * 2 + 3 * 2, len(b3.segments)
    # grid intersections labeled alpha/num
    gp = grid_points(m)
    assert sorted(g[2] for g in gp) == ["A/1", "A/2"], gp


def test_render_ops():
    m, w1, *_ = build_model()
    ops = render_ops(m)
    kinds = {op[0] for op in ops}
    assert {"line", "text", "circle", "arc"} <= kinds, kinds
    # dim text carries the feet-inches readout
    assert any(op[0] == "text" and "20'-0" in str(op[3]) for op in ops)
    # hidden ply culls its ops
    m.ply("S-GRID").visible = False
    assert len(render_ops(m)) < len(ops)
    m.ply("S-GRID").visible = True
    # paper-size conversion: grid bubble radius = dia/2 * ratio / 12
    want_r = GRID_BUBBLE_IN / 2.0 * m.scale_ratio / 12.0
    grid_circles = [op for op in render_ops(m)
                    if op[0] == "circle" and op[4] == "S-GRID"]
    assert grid_circles and any(abs(op[3] - want_r) < 1e-6
                                for op in grid_circles), grid_circles[:2]
    # per-entity include filter
    only = render_ops(m, include=("ent:" + w1.id,))
    assert 0 < len(only) < len(ops)
    # stencil_ops emits render_ops-shaped tuples
    shape = {"line": 8, "circle": 7, "arc": 9, "ellipse": 8, "text": 8}
    for key in STENCILS:
        for op in stencil_ops(key, 3.0, 4.0, 90.0, False):
            assert op[0] in shape and len(op) == shape[op[0]], (key, op)
    # rotation actually moves geometry
    a = stencil_ops("wc", 0, 0, 0, False)
    b = stencil_ops("wc", 0, 0, 90, False)
    assert a != b
    # text sizes convert paper->model
    assert abs(text_model_h("body", 96) - (3.0 / 32.0) * 96 / 12.0) < 1e-9
    # linetype tables sane
    assert LINETYPES["solid"] == () and len(LINETYPES["center"]) == 4


def test_plate_dxf_png():
    m, *_ = build_model()
    out = os.path.join(TMP, "plate.pdf")
    res = plate_pdf(m, out, sheet="ARCH D",
                    meta={"project": "TEST", "drawn_by": "QA"})
    assert os.path.exists(out) and not os.path.exists(out + ".part")
    from pypdf import PdfReader
    rd = PdfReader(out)
    assert len(rd.pages) == 1
    w_in, h_in = SHEET_SIZES["ARCH D"]
    assert abs(float(rd.pages[0].mediabox.width) - w_in * 72) < 1.0
    assert abs(float(rd.pages[0].mediabox.height) - h_in * 72) < 1.0
    assert res.get("fit") is True and res.get("ops")
    # auto-fit: 400 ft of wall cannot plot at 1/8" on ARCH D -> smaller scale
    big = DraftModel()
    big.add("wall", [(0, 0), (400, 0)], wtype="stud4")
    big.scale_ratio = 96
    res2 = plate_pdf(big, os.path.join(TMP, "big.pdf"))
    assert res2["scale"] != SCALES[2][0], res2
    # DXF: layers + entities + terminator
    dxf = os.path.join(TMP, "draft.dxf")
    n = to_dxf(m, dxf)
    assert n > 20
    text = open(dxf, encoding="ascii").read()
    for token in ("LAYER", "A-WALL", "S-GRID", "ENTITIES", "LINE", "TEXT",
                  "EOF"):
        assert token in text, token
    # PNG via the plate rasterizer
    png = os.path.join(TMP, "draft.png")
    try:
        to_png(m, png, dpi=72)
    except ImportError:
        print("  (fitz unavailable: to_png skipped)")
    else:
        assert os.path.exists(png) and os.path.getsize(png) > 1000


def main():
    test_ftin()
    print("PASS feet-inches format/parse")
    test_model_crud_undo()
    print("PASS model CRUD + undo/redo depth")
    test_doors_ride_hosts()
    print("PASS doors ride hosts, t re-solve, wall openings")
    test_snaps()
    print("PASS Plumbline snap priority/intersection/perp/ortho")
    test_grid_labels()
    print("PASS grid labels (I/O skipped, Z->AA)")
    test_offset_geometry()
    print("PASS wall face offsets")
    test_save_load_atomic()
    print("PASS save/load round trip, atomic")
    test_stats_takeoff_bridges()
    print("PASS stats, takeoff + price book, to_bim, grid_points")
    test_render_ops()
    print("PASS render_ops + stencil parity + paper conversion")
    test_plate_dxf_png()
    print("PASS plate PDF (auto-fit), DXF R12, PNG")
    print("DRAFT ENGINE TEST PASSED  (The Loft)")
    print("outputs in", TMP)


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print("DRAFT TEST FAILED:", e)
        sys.exit(1)
