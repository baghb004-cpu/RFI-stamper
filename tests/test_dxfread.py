"""Self-contained tests for rfi_stamper.dxfread -- the in-house ASCII DXF
background reader and its small R12 background writer.  Plain python, no
pytest, no project data: every fixture is written here, as group pairs or
through the writer under test.

Exercises:

* sniff by content, never by extension (ASCII DXF, binary DXF, a native
  CAD drawing file, anything else) and the plain refusal message
* writer -> reader round trip of every shape kind, on several layers, in
  feet, inches and with no declared units; an independent reparse with
  the lenient pair reader in selvage (CRLF pairs, header, LAYER table)
* LWPOLYLINE (bulge, closed flag) and MTEXT (chunks + inline codes) from
  hand-written group pairs; bulge_arc; extents that follow the arcs
* INSERT expansion: base point, scale -> rotate -> translate with exact
  coordinates, layer-0 inheritance, nested blocks and the block path,
  the depth cap, a block that inserts itself, a missing block, arrays,
  unequal scale on a circle, a mirrored arc, attribute values as TEXT
* entity planes: a flipped plane (what mirroring leaves) is brought back
  to plan coordinates, a tilted one is counted; BLOCKS after ENTITIES;
  run terminators
* the coverage-honest skipped tally: every record is read or counted
* layer roles from layer NAMES only, the single precedence rule
* TEXT anchors and control codes
* the declared-units table and the refusal to guess units
* tolerance (LF, doubled CR, blank lines, lowercase tags, no ENDSEC or
  EOF), a current-format file layout walked without tripping, and
  structural damage reported with its line number
* writer refusals (layer-name rule enforced at creation, nothing written)

Run:  python3 tests/test_dxfread.py
"""
import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rfi_stamper import dxfread as dx              # noqa: E402
from rfi_stamper import selvage                    # noqa: E402
from rfi_stamper.dxfread import DxfError           # noqa: E402


def expect(exc, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except exc as e:
        return e
    raise AssertionError(f"expected {exc.__name__} from {fn!r}{args!r}")


def raw(pairs, eol="\r\n") -> bytes:
    return "".join(f"{c}{eol}{v}{eol}" for c, v in pairs).encode("ascii")


def put(tmp, name, data: bytes) -> str:
    path = os.path.join(tmp, name)
    with open(path, "wb") as f:
        f.write(data)
    return path


def slurp(path) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def doc_pairs(entities, blocks=(), header=(), layers=()):
    """A whole ASCII DXF as group pairs from its ENTITIES (and optional
    BLOCKS / HEADER / LAYER-table) pairs."""
    pairs = [(0, "SECTION"), (2, "HEADER"), (9, "$ACADVER"), (1, "AC1015")]
    pairs += list(header) + [(0, "ENDSEC")]
    if layers:
        pairs += [(0, "SECTION"), (2, "TABLES"),
                  (0, "TABLE"), (2, "LAYER"), (70, str(len(layers)))]
        for name in layers:
            pairs += [(0, "LAYER"), (2, name), (70, "0"), (62, "7"),
                      (6, "CONTINUOUS")]
        pairs += [(0, "ENDTAB"), (0, "ENDSEC")]
    if blocks:
        pairs += [(0, "SECTION"), (2, "BLOCKS")] + list(blocks) \
            + [(0, "ENDSEC")]
    return pairs + [(0, "SECTION"), (2, "ENTITIES")] + list(entities) \
        + [(0, "ENDSEC"), (0, "EOF")]


def block(name, body, base=(0, 0)):
    return [(0, "BLOCK"), (8, "0"), (2, name), (70, "0"),
            (10, str(base[0])), (20, str(base[1])), (30, "0")] \
        + list(body) + [(0, "ENDBLK"), (8, "0")]


def line(layer, x1, y1, x2, y2, *more):
    return [(0, "LINE"), (8, layer), (10, str(x1)), (20, str(y1)),
            (11, str(x2)), (21, str(y2))] + list(more)


def insert(layer, name, x, y, *more):
    return [(0, "INSERT"), (8, layer), (2, name), (10, str(x)),
            (20, str(y))] + list(more)


def near(a, b, tol=1e-9):
    return abs(a - b) <= tol



# ------------------------------------------------------------------ sniff --

def test_sniff(tmp):
    sig = dx.BINARY_DXF_SIGNATURE
    assert len(sig) == 22 and sig.endswith(b"\r\n\x1a\x00"), sig
    assert sig[:18].isascii() and b"Binary DXF" in sig, sig

    good = os.path.join(tmp, "bg.dxf")
    dx.write_background(good, [{"layer": "S-GRID", "kind": "line",
                                "pts": [(0, 0), (10, 0)]}])
    commented = put(tmp, "commented.dxf", raw(
        [(999, "written by a test"), (999, "second comment")]
        + doc_pairs(line("0", 0, 0, 1, 1))))
    # the extension is never trusted, either way
    disguised = put(tmp, "plans.pdf", slurp(good))
    binary = put(tmp, "binary.dxf", sig + bytes(range(64)))
    native = put(tmp, "native.dxf", b"AC1032" + bytes(200))
    pdf = put(tmp, "sheet.dxf", b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n")
    prose = put(tmp, "notes.dxf", b"0\nNOT A SECTION\n")
    empty = put(tmp, "empty.dxf", b"")

    assert dx.sniff(good) == "ascii_dxf"
    assert dx.sniff(commented) == "ascii_dxf"
    assert dx.sniff(disguised) == "ascii_dxf"
    assert dx.sniff(binary) == "binary_dxf"
    assert dx.sniff(native) == "native_cad"
    assert dx.sniff(pdf) == "other"
    assert dx.sniff(prose) == "other"
    assert dx.sniff(empty) == "other"

    assert dx.NOT_ASCII_DXF == (
        "This CAD file is not an ASCII DXF. Please export the background "
        "as ASCII DXF and drop it again.")
    for bad in (binary, native, pdf, prose, empty):
        e = expect(DxfError, dx.read, bad)
        assert str(e) == dx.NOT_ASCII_DXF, (bad, str(e))
        assert isinstance(e, ValueError)
    assert len(dx.read(disguised).entities) == 1
    assert len(dx.read(commented).entities) == 1
    print("PASS sniff by content (ascii / binary / native / other) and "
          "the plain refusal message")


# -------------------------------------------------------------- round trip --

SHAPES = [
    {"layer": "S-GRID", "kind": "line", "pts": [(0, -5), (0, 45.125)]},
    {"layer": "S-GRID", "kind": "text", "at": (-0.25, 46.5), "text": "A",
     "height": 0.75},
    {"layer": "S-WALL", "kind": "poly", "closed": True,
     "pts": [(10, 10), (34.5, 10), (34.5, 11), (10, 11)]},
    {"layer": "S-WALL", "kind": "poly", "closed": False,
     "pts": [(1 / 3, 2 / 3), (12.3456, 7.0), (20.00004, -3.5)]},
    {"layer": "S-COLS", "kind": "circle", "center": (24, 20), "radius": 1},
    {"layer": "S-SLAB-EDGE", "kind": "arc", "center": (72, 40),
     "radius": 6.5, "start_deg": 270, "end_deg": 0},
    {"layer": "A-WALL-CMU", "kind": "point", "at": (5.5, 6.25)},
    {"layer": "A-WALL-CMU", "kind": "point", "at": (7, 8), "text": "CP-1",
     "height": 0.5},
]


def test_round_trip(tmp):
    for units, code in (("ft", 2), ("in", 1), ("", None)):
        path = os.path.join(tmp, f"bg_{units or 'none'}.dxf")
        count = dx.write_background(path, SHAPES, units=units)
        assert count == 9, count            # 8 shapes, one labeled point
        doc = dx.read(path)
        assert len(doc.entities) == count, len(doc.entities)
        assert doc.skipped == {}, doc.skipped
        assert doc.path == path and doc.units == units, doc.units
        assert doc.insunits == code, doc.insunits

        kinds = [e.kind for e in doc.entities]
        assert kinds == ["LINE", "TEXT", "POLYLINE", "POLYLINE", "CIRCLE",
                         "ARC", "POINT", "POINT", "TEXT"], kinds
        ln, label, wall, run, col, arc, p1, p2, tag = doc.entities
        assert ln.layer == "S-GRID" and ln.pts == [(0.0, -5.0), (0.0, 45.125)]
        assert ln.block == "" and not ln.closed and ln.bulges == []
        assert label.text == "A" and label.pts == [(-0.25, 46.5)]
        assert label.height == 0.75 and label.layer == "S-GRID"
        assert wall.closed and wall.layer == "S-WALL"
        assert wall.pts == [(10.0, 10.0), (34.5, 10.0), (34.5, 11.0),
                            (10.0, 11.0)], wall.pts
        assert wall.bulges == [0.0, 0.0, 0.0, 0.0]
        # exact to the written precision (four decimals)
        assert not run.closed
        assert run.pts == [(0.3333, 0.6667), (12.3456, 7.0), (20.0, -3.5)], \
            run.pts
        assert col.center == (24.0, 20.0) and col.radius == 1.0
        assert col.pts == [] and col.layer == "S-COLS"
        assert arc.center == (72.0, 40.0) and arc.radius == 6.5
        assert (arc.start_deg, arc.end_deg) == (270.0, 0.0)
        assert p1.pts == [(5.5, 6.25)] and p2.pts == [(7.0, 8.0)]
        assert tag.text == "CP-1" and tag.pts == [(7.25, 8.25)]
        assert tag.height == 0.5 and tag.layer == "A-WALL-CMU"

        assert {k: v["count"] for k, v in doc.layers.items()} == {
            "S-GRID": 2, "S-WALL": 2, "S-COLS": 1, "S-SLAB-EDGE": 1,
            "A-WALL-CMU": 3}, doc.layers
        assert all(v["color"] == 7 and not v["off"] and not v["frozen"]
                   for v in doc.layers.values())
        assert [e.kind for e in doc.on("S-WALL")] == ["POLYLINE", "POLYLINE"]
        assert len(doc.on("s-grid", "S-COLS")) == 3     # case-blind names
        assert doc.on("NOPE") == []
        # the arc runs from 270 to 0: its box reaches x = 78.5, y = 33.5
        assert doc.extents == (-0.25, -5.0, 78.5, 46.5), doc.extents

        # an independent reparse with the lenient pair reader in selvage
        data = slurp(path)
        assert data.count(b"\r\n") == data.count(b"\n") == data.count(b"\r")
        pairs = selvage.read_dxf_pairs(path)
        assert pairs[:4] == [(0, "SECTION"), (2, "HEADER"),
                             (9, "$ACADVER"), (1, "AC1009")], pairs[:4]
        assert ((9, "$INSUNITS") in pairs) == bool(units)
        if units:
            k = pairs.index((9, "$INSUNITS"))
            assert pairs[k + 1] == (70, str(code)), pairs[k + 1]
        k = pairs.index((0, "TABLE"))
        assert pairs[k + 1:k + 3] == [(2, "LAYER"), (70, "5")], pairs[k:k + 3]
        assert sum(1 for p in pairs if p == (0, "LAYER")) == 5
        assert sum(1 for p in pairs if p == (0, "VERTEX")) == 7
        assert sum(1 for p in pairs if p == (0, "SEQEND")) == 2
        assert pairs[-1] == (0, "EOF")
        assert not os.path.exists(path + ".part")

    ft = dx.read(os.path.join(tmp, "bg_ft.dxf"))
    inch = dx.read(os.path.join(tmp, "bg_in.dxf"))
    none = dx.read(os.path.join(tmp, "bg_none.dxf"))
    assert ft.feet() == 1.0 and inch.feet() == 1.0 / 12.0
    assert ft.feet("in") == 1.0             # the DECLARED units govern
    e = expect(DxfError, none.feet)
    assert "declares no units" in str(e), str(e)
    assert none.feet("in") == 1.0 / 12.0 and none.feet("FT") == 1.0
    assert "Unknown units" in str(expect(DxfError, none.feet, "furlong"))

    # extra header pairs ride along; a bulged polyline round-trips
    path = os.path.join(tmp, "bulged.dxf")
    n = dx.write_background(path, [
        {"layer": "S-SLAB-EDGE", "kind": "poly", "closed": True,
         "pts": [(0, 0), (10, 0), (10, 10), (0, 10)],
         "bulges": [0, 1, 0, 0]}],
        extra_header=[(9, "$EXTMIN"), (10, "0.0"), (20, "0.0")])
    doc = dx.read(path)
    assert n == 1 and doc.entities[0].bulges == [0.0, 1.0, 0.0, 0.0]
    assert doc.extents == (0.0, 0.0, 15.0, 10.0), doc.extents
    assert (9, "$EXTMIN") in selvage.read_dxf_pairs(path)
    print("PASS writer -> reader round trip (every shape kind, ft / in / "
          "undeclared units, selvage reparse, extents, bulges)")


# ------------------------------------------------------ LWPOLYLINE / MTEXT --

def test_lwpolyline_mtext(tmp):
    ents = [
        (0, "LWPOLYLINE"), (5, "2A"), (8, "S-SLAB-EDGE"), (90, "4"),
        (70, "1"),
        (10, "0.0"), (20, "0.0"),
        (10, "10.0"), (20, "0.0"), (42, "1.0"),
        (10, "10.0"), (20, "10.0"),
        (10, "0.0"), (20, "10.0"),
        (0, "LWPOLYLINE"), (8, "S-WALL"), (90, "3"), (70, "0"),
        (10, "1"), (20, "2"), (40, "0.1"), (41, "0.1"), (42, "-0.5"),
        (10, "3"), (20, "2"),
        (10, "3"), (20, "5"),
        (0, "MTEXT"), (8, "S-WALL-IDEN"), (10, "5.0"), (20, "6.0"),
        (40, "0.75"), (71, "5"),
        (3, "{\\fArial|b0|i0|c0|p34;SHEAR WALL}\\PSW"),
        (1, "1 \\H0.5x;TYP"),
        (0, "MTEXT"), (8, "S-WALL-IDEN"), (10, "1"), (20, "1"), (40, "0.5"),
        (1, "\\A1;4'-0 \\S1/2;\" \\LMIN\\l \\{A\\} C:\\\\X 45%%d"),
        (0, "MTEXT"), (8, "S-WALL-IDEN"), (10, "2"), (20, "2"), (40, "0.5"),
        (1, "{\\H1.5x;}"),
    ]
    doc = dx.read(put(tmp, "lw.dxf", raw(doc_pairs(ents))))
    edge, wall, note, frac = doc.entities
    assert doc.skipped == {"TEXT(empty)": 1}, doc.skipped

    assert edge.kind == "POLYLINE" and edge.closed
    assert edge.layer == "S-SLAB-EDGE"
    assert edge.pts == [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
    assert edge.bulges == [0.0, 1.0, 0.0, 0.0], edge.bulges
    assert wall.kind == "POLYLINE" and not wall.closed
    assert wall.pts == [(1.0, 2.0), (3.0, 2.0), (3.0, 5.0)]
    assert wall.bulges == [-0.5, 0.0, 0.0], wall.bulges

    assert note.kind == "TEXT" and note.text == "SHEAR WALL SW1 TYP", note.text
    assert note.pts == [(5.0, 6.0)] and note.height == 0.75
    assert frac.text == "4'-0 1/2\" MIN {A} C:\\X 45\u00b0", frac.text

    # the bulge on (10,0) -> (10,10) is a half circle about (10,5),
    # counter-clockwise, so it reaches out to x = 15
    c, r, a0, a1 = dx.bulge_arc(edge.pts[1], edge.pts[2], edge.bulges[1])
    assert c == (10.0, 5.0) and r == 5.0 and (a0, a1) == (270.0, 90.0)
    c, r, a0, a1 = dx.bulge_arc((10, 0), (10, 10), -1.0)   # clockwise
    assert c == (10.0, 5.0) and r == 5.0 and (a0, a1) == (90.0, 270.0)
    assert dx.bulge_arc((0, 0), (1, 0), 0.0) is None
    assert dx.bulge_arc((2, 2), (2, 2), 0.5) is None
    # a quarter circle: bulge = tan(90 / 4)
    c, r, a0, a1 = dx.bulge_arc((1, 0), (0, 1), math.tan(math.pi / 8))
    assert near(c[0], 0) and near(c[1], 0) and near(r, 1)
    assert near(a0, 0) and near(a1, 90)
    # the wall's -0.5 bulge runs clockwise from (1,2) to (3,2): a minor arc
    # over the top of a circle about (2, 1.25), cresting at y = 2.5
    c, r, a0, a1 = dx.bulge_arc((1, 2), (3, 2), -0.5)
    assert near(c[0], 2) and near(c[1], 1.25) and near(r, 1.25)
    assert near(a0, math.degrees(math.atan2(0.75, 1.0)))
    assert near(a1, 180.0 - math.degrees(math.atan2(0.75, 1.0)))
    assert near(doc.extents[0], 0.0) and near(doc.extents[2], 15.0)
    assert near(doc.extents[1], 0.0) and near(doc.extents[3], 10.0)
    # alone, that wall's crest (2.5) hides inside its own vertex box ...
    solo = dx.read(put(tmp, "lw2.dxf", raw(doc_pairs(ents[14:27]))))
    assert [e.pts for e in solo.entities] == [wall.pts]
    assert near(solo.extents[3], 5.0) and near(solo.extents[1], 2.0)
    assert near(solo.extents[0], 1.0) and near(solo.extents[2], 3.0)
    # ... while a lone half-circle segment does leave its vertex box
    up = dx.read(put(tmp, "lw3.dxf", raw(doc_pairs([
        (0, "LWPOLYLINE"), (8, "S-WALL"), (90, "2"), (70, "0"),
        (10, "1"), (20, "2"), (42, "-1"), (10, "3"), (20, "2")]))))
    assert up.extents == (1.0, 2.0, 3.0, 3.0), up.extents
    print("PASS LWPOLYLINE (bulge, closed flag) and MTEXT (chunks, inline "
          "codes) from hand-written pairs; bulge_arc")


# ----------------------------------------------------------------- INSERT --

COL = block("COL", [
    (0, "LWPOLYLINE"), (8, "0"), (90, "4"), (70, "1"),
    (10, "-1"), (20, "-1"), (10, "1"), (20, "-1"),
    (10, "1"), (20, "1"), (10, "-1"), (20, "1"),
    (0, "CIRCLE"), (8, "S-COLS-CORE"), (10, "0"), (20, "0"), (40, "0.5"),
])


def test_insert(tmp):
    ents = (insert("S-COLS-A", "COL", 10, 20)
            + insert("S-COLS-B", "col", 30, 40, (41, "2"), (42, "2"),
                     (50, "90")))
    doc = dx.read(put(tmp, "ins.dxf", raw(doc_pairs(ents, COL))))
    assert doc.skipped == {}, doc.skipped
    sq1, c1, sq2, c2 = doc.entities
    assert sq1.kind == "POLYLINE" and sq1.closed and sq1.block == "COL"
    assert sq1.layer == "S-COLS-A"          # layer 0 takes the INSERT's layer
    assert sq1.pts == [(9.0, 19.0), (11.0, 19.0), (11.0, 21.0), (9.0, 21.0)]
    assert c1.layer == "S-COLS-CORE"        # an explicit layer is kept
    assert c1.center == (10.0, 20.0) and c1.radius == 0.5
    # scale 2 -> rotate 90 -> translate (30, 40), EXACT at the quadrant
    assert sq2.layer == "S-COLS-B" and sq2.block == "COL"
    assert sq2.pts == [(32.0, 38.0), (32.0, 42.0), (28.0, 42.0),
                       (28.0, 38.0)], sq2.pts
    assert c2.center == (30.0, 40.0) and c2.radius == 1.0
    assert doc.layers["S-COLS-CORE"]["count"] == 2
    assert doc.extents == (9.0, 19.0, 32.0, 42.0), doc.extents

    # the block base point is where the INSERT lands
    mark = block("MARK", line("0", 5, 5, 6, 5) + line("S-FIX", 5, 5, 5, 7),
                 base=(5, 5))
    doc = dx.read(put(tmp, "base.dxf", raw(doc_pairs(
        insert("S-TAGS", "MARK", 100, 100)
        + insert("S-TAGS", "MARK", 0, 0, (50, "180")), mark))))
    a, b, c, d = doc.entities
    assert a.pts == [(100.0, 100.0), (101.0, 100.0)] and a.layer == "S-TAGS"
    assert b.pts == [(100.0, 100.0), (100.0, 102.0)] and b.layer == "S-FIX"
    assert c.pts == [(0.0, 0.0), (-1.0, 0.0)] and d.pts == [(0.0, 0.0),
                                                            (0.0, -2.0)]

    # nested: layer 0 climbs to the outermost INSERT; the path is recorded
    pair = block("PAIR", insert("0", "COL", 0, 0) + insert("0", "COL", 10, 0)
                 + line("S-AXIS", 0, 0, 10, 0))
    doc = dx.read(put(tmp, "nest.dxf", raw(doc_pairs(
        insert("S-COLS-N", "PAIR", 50, 50, (50, "90")), COL + pair))))
    assert doc.skipped == {}, doc.skipped
    sq1, c1, sq2, c2, axis = doc.entities
    assert sq1.layer == sq2.layer == "S-COLS-N"
    assert sq1.block == sq2.block == c1.block == "PAIR/COL"
    assert axis.block == "PAIR" and axis.layer == "S-AXIS"
    assert axis.pts == [(50.0, 50.0), (50.0, 60.0)], axis.pts
    assert sq1.pts == [(51.0, 49.0), (51.0, 51.0), (49.0, 51.0),
                       (49.0, 49.0)], sq1.pts
    assert c1.center == (50.0, 50.0) and c2.center == (50.0, 60.0)
    assert sq2.pts == [(51.0, 59.0), (51.0, 61.0), (49.0, 61.0),
                       (49.0, 59.0)], sq2.pts

    # missing block, a block that inserts itself, an array, zero scale:
    # all counted, never expanded wrong, never looping
    loop = block("LOOP", line("0", 0, 0, 1, 0) + insert("0", "LOOP", 1, 0))
    doc = dx.read(put(tmp, "bad.dxf", raw(doc_pairs(
        insert("0", "NOPE", 1, 1)
        + insert("S-X", "LOOP", 0, 0)
        + insert("S-X", "COL", 0, 0, (70, "3"), (71, "2"), (44, "5"),
                 (45, "5"))
        + insert("S-X", "COL", 0, 0, (41, "0"))
        + [(0, "INSERT"), (8, "S-X"), (2, "COL")], COL + loop))))
    assert doc.skipped == {"INSERT(unresolved)": 4, "INSERT(array)": 1}, \
        doc.skipped
    assert len(doc.entities) == 1 and doc.entities[0].block == "LOOP"
    assert doc.entities[0].pts == [(0.0, 0.0), (1.0, 0.0)]

    # the depth cap: 8 nested INSERTs expand, the 9th is counted
    def chain(n):
        blocks = []
        for k in range(1, n + 1):
            body = (line("0", 0, 0, 1, 0) if k == n
                    else insert("0", f"B{k + 1}", 1, 0))
            blocks += block(f"B{k}", body)
        return dx.read(put(tmp, f"chain{n}.dxf", raw(doc_pairs(
            insert("S-DEEP", "B1", 0, 0), blocks))))

    assert dx.MAX_INSERT_DEPTH == 8
    doc = chain(8)
    assert doc.skipped == {} and len(doc.entities) == 1, doc.skipped
    assert doc.entities[0].pts == [(7.0, 0.0), (8.0, 0.0)]
    assert doc.entities[0].layer == "S-DEEP"
    assert doc.entities[0].block == "B1/B2/B3/B4/B5/B6/B7/B8"
    doc = chain(9)
    assert doc.skipped == {"INSERT(unresolved)": 1} and doc.entities == []

    # unequal scale: the square is still a polygon, the circle is not a
    # circle any more -- counted, not emitted wrong
    arcs = block("ARCS", [
        (0, "ARC"), (8, "0"), (10, "0"), (20, "0"), (40, "1"),
        (50, "0"), (51, "90"),
        (0, "LWPOLYLINE"), (8, "0"), (90, "2"), (70, "0"),
        (10, "0"), (20, "0"), (42, "1"), (10, "2"), (20, "0"),
        (0, "TEXT"), (8, "0"), (10, "1"), (20, "1"), (40, "0.5"), (1, "T")])
    doc = dx.read(put(tmp, "nonuni.dxf", raw(doc_pairs(
        insert("S-N", "COL", 0, 0, (41, "2"), (42, "1"))
        + insert("S-N", "ARCS", 0, 0, (41, "2"), (42, "1")), COL + arcs))))
    assert doc.skipped == {"CIRCLE(nonuniform)": 1, "ARC(nonuniform)": 1,
                           "POLYLINE(nonuniform)": 1}, doc.skipped
    sq, txt = doc.entities
    assert sq.pts == [(-2.0, -1.0), (2.0, -1.0), (2.0, 1.0), (-2.0, 1.0)]
    assert txt.pts == [(2.0, 1.0)] and near(txt.height, 0.5 * math.sqrt(2))

    # a mirrored INSERT (x scale -1): the arc stays counter-clockwise and
    # the bulge changes sign; a plain turned-and-scaled one just rotates
    doc = dx.read(put(tmp, "mirror.dxf", raw(doc_pairs(
        insert("S-M", "ARCS", 0, 0, (41, "-1"))
        + insert("S-M", "ARCS", 10, 0, (41, "3"), (42, "3"), (50, "90"))
        + insert("S-M", "ARCS", 0, 0, (41, "2"), (42, "-2"), (50, "30")),
        arcs))))
    assert doc.skipped == {}, doc.skipped
    arc, pl, txt, arc2, pl2, txt2, arc3, pl3, _t3 = doc.entities
    assert (arc.start_deg, arc.end_deg) == (90.0, 180.0), arc
    assert arc.center == (0.0, 0.0) and arc.radius == 1.0
    assert pl.pts == [(0.0, 0.0), (-2.0, 0.0)] and pl.bulges == [-1.0, 0.0]
    assert txt.pts == [(-1.0, 1.0)] and txt.height == 0.5
    assert (arc2.start_deg, arc2.end_deg) == (90.0, 180.0), arc2
    assert arc2.center == (10.0, 0.0) and arc2.radius == 3.0
    assert pl2.pts == [(10.0, 0.0), (10.0, 6.0)] and pl2.bulges == [1.0, 0.0]
    assert txt2.pts == [(7.0, 3.0)] and near(txt2.height, 1.5)
    # mirror about x then turn 30: angle a lands on 30 - a
    assert near(arc3.start_deg, 300.0) and near(arc3.end_deg, 30.0), arc3
    assert near(arc3.radius, 2.0) and pl3.bulges == [-1.0, 0.0]
    # the mirrored arc is the same set of points the block drew: a point at
    # block angle 20 lands at 160, inside the new 90 -> 180 sweep
    px, py = -math.cos(math.radians(20.0)), math.sin(math.radians(20.0))
    got = math.degrees(math.atan2(py, px)) % 360.0
    assert near(got, 160.0) and arc.start_deg < got < arc.end_deg

    # attribute values ride the INSERT and are read as TEXT, in the
    # INSERT's own space; an invisible one is counted, the template
    # (ATTDEF) inside the block is counted per placement
    bub = block("BUB", [
        (0, "CIRCLE"), (8, "0"), (10, "0"), (20, "0"), (40, "1"),
        (0, "ATTDEF"), (8, "0"), (10, "-0.3"), (20, "-0.4"), (40, "0.8"),
        (1, "X"), (3, "Grid name"), (2, "GRID"), (70, "0")])
    ents = (
        [(0, "INSERT"), (8, "S-GRID"), (66, "1"), (2, "BUB"),
         (10, "0"), (20, "100"),
         (0, "ATTRIB"), (8, "S-GRID-IDEN"), (10, "-0.3"), (20, "99.6"),
         (40, "0.8"), (1, "A"), (2, "GRID"), (70, "0"), (72, "1"),
         (74, "2"), (11, "0"), (21, "100"),
         (0, "ATTRIB"), (8, "S-GRID-IDEN"), (10, "0"), (20, "0"),
         (40, "0.8"), (1, "hidden"), (2, "NOTE"), (70, "1"),
         (0, "ATTRIB"), (8, "0"), (10, "3"), (20, "98"), (40, "0.4"),
         (1, ""), (2, "BLANK"), (70, "0"),
         (0, "SEQEND"), (8, "S-GRID")]
        + line("S-GRID", 0, 0, 0, 99))
    doc = dx.read(put(tmp, "attrib.dxf", raw(doc_pairs(ents, bub))))
    assert doc.skipped == {"ATTRIB(invisible)": 1, "TEXT(empty)": 1,
                           "ATTDEF": 1}, doc.skipped
    label, ring, gl = doc.entities
    assert label.kind == "TEXT" and label.text == "A"
    assert label.pts == [(0.0, 100.0)]      # the alignment point: the center
    assert label.layer == "S-GRID-IDEN" and label.block == "BUB"
    assert label.height == 0.8
    assert ring.kind == "CIRCLE" and ring.center == (0.0, 100.0)
    assert ring.layer == "S-GRID" and gl.kind == "LINE"
    print("PASS INSERT expansion (base point, exact scale-rotate-translate, "
          "layer 0, nesting + path, depth cap, self-insert, missing block, "
          "arrays, unequal scale, mirror, attributes)")


# ----------------------------------------------------------------- planes --

def test_planes(tmp):
    down = [(210, "0"), (220, "0"), (230, "-1")]      # a flipped plane
    arcs = block("ARCS", [
        (0, "ARC"), (8, "0"), (10, "0"), (20, "0"), (40, "1"),
        (50, "0"), (51, "90"),
        (0, "LINE"), (8, "0"), (10, "0"), (20, "0"), (11, "2"), (21, "0")])
    ents = (
        [(0, "CIRCLE"), (8, "S-COLS"), (10, "5"), (20, "7"), (40, "1")] + down
        + [(0, "ARC"), (8, "S-SLAB"), (10, "5"), (20, "0"), (40, "1"),
           (50, "0"), (51, "90")] + down
        + [(0, "LWPOLYLINE"), (8, "S-WALL"), (90, "3"), (70, "1"),
           (10, "1"), (20, "0"), (42, "0.5"), (10, "3"), (20, "0"),
           (10, "3"), (20, "4")] + down
        + [(0, "TEXT"), (8, "S-NOTE"), (10, "4"), (20, "9"), (40, "1"),
           (1, "M")] + down
        + line("S-WALL", 1, 2, 3, 4, *down)            # plan coordinates
        + [(0, "POLYLINE"), (8, "S-WALL"), (66, "1"), (70, "8")] + down
        + [(0, "VERTEX"), (8, "S-WALL"), (10, "1"), (20, "1"),
           (0, "VERTEX"), (8, "S-WALL"), (10, "2"), (20, "1"),
           (0, "SEQEND")]
        + insert("S-M", "ARCS", 10, 0, *down)
        + insert("S-M", "ARCS", 10, 0, (50, "90"), *down)
        + [(0, "CIRCLE"), (8, "S-COLS"), (10, "1"), (20, "1"), (40, "1"),
           (210, "1"), (220, "0"), (230, "0")]
        + [(0, "MTEXT"), (8, "S-NOTE"), (10, "1"), (20, "1"), (40, "1"),
           (1, "FLIPPED")] + down
        + insert("S-M", "ARCS", 0, 0, (210, "0"), (220, "0.6"), (230, "0.8")))
    doc = dx.read(put(tmp, "planes.dxf", raw(doc_pairs(ents, arcs))))
    assert doc.skipped == {"CIRCLE(tilted)": 1, "MTEXT(extruded)": 1,
                           "INSERT(tilted)": 1}, doc.skipped
    circ, arc, pl, txt, seg, pl3, a1, l1, a2, l2 = doc.entities
    # x runs backwards on a flipped plane; nothing is left where it was stored
    assert circ.center == (-5.0, 7.0) and circ.radius == 1.0
    assert arc.center == (-5.0, 0.0) and arc.radius == 1.0
    assert (arc.start_deg, arc.end_deg) == (90.0, 180.0)   # still CCW
    assert pl.pts == [(-1.0, 0.0), (-3.0, 0.0), (-3.0, 4.0)] and pl.closed
    assert pl.bulges == [-0.5, 0.0, 0.0], pl.bulges
    assert txt.pts == [(-4.0, 9.0)] and txt.height == 1.0
    # LINE and 3D-polyline points are stored in plan coordinates already
    assert seg.pts == [(1.0, 2.0), (3.0, 4.0)]
    assert pl3.pts == [(1.0, 1.0), (2.0, 1.0)]
    # a flipped INSERT: place in its own plane, then flip
    assert a1.center == (-10.0, 0.0) and (a1.start_deg, a1.end_deg) == (90.0, 180.0)
    assert l1.pts == [(-10.0, 0.0), (-12.0, 0.0)]
    assert a2.center == (-10.0, 0.0) and (a2.start_deg, a2.end_deg) == (0.0, 90.0)
    assert l2.pts == [(-10.0, 0.0), (-10.0, 2.0)]
    # the flipped arc keeps the points the stored one had, mirrored
    c, s_ = math.cos(math.radians(30.0)), math.sin(math.radians(30.0))
    got = math.degrees(math.atan2(s_, -c)) % 360.0     # stored angle 30
    assert near(got, 150.0) and arc.start_deg < got < arc.end_deg

    # BLOCKS after ENTITIES (out of order): every block is still known
    # when the INSERT is expanded
    late = ([(0, "SECTION"), (2, "ENTITIES")]
            + insert("S-COLS", "COL", 4, 4) + line("S-X", 0, 0, 1, 0)
            + [(0, "ENDSEC"), (0, "SECTION"), (2, "BLOCKS")] + COL
            + [(0, "ENDSEC"), (0, "EOF")])
    doc = dx.read(put(tmp, "late.dxf", raw(late)))
    assert doc.skipped == {} and [e.kind for e in doc.entities] == [
        "POLYLINE", "CIRCLE", "LINE"], doc.skipped
    assert doc.entities[1].center == (4.0, 4.0)

    # terminators: a POLYLINE's SEQEND, an attribute-less INSERT's SEQEND
    # (flag 66) and a stray one are all just terminators
    ents = ([(0, "INSERT"), (8, "S-COLS"), (66, "1"), (2, "COL"),
             (10, "0"), (20, "0"), (0, "SEQEND"), (8, "S-COLS")]
            + [(0, "SEQEND")]
            + insert("S-COLS", "COL", 9, 9)
            + [(0, "POLYLINE"), (8, "S-WALL"), (66, "1"), (70, "0"),
               (0, "VERTEX"), (8, "S-WALL"), (10, "0"), (20, "0"),
               (0, "VERTEX"), (8, "S-WALL"), (10, "5"), (20, "0")]
            + line("S-X", 0, 0, 1, 0))                 # no SEQEND at all
    doc = dx.read(put(tmp, "seqend.dxf", raw(doc_pairs(ents, COL))))
    assert doc.skipped == {}, doc.skipped
    assert [e.kind for e in doc.entities] == [
        "POLYLINE", "CIRCLE", "POLYLINE", "CIRCLE", "POLYLINE", "LINE"]
    assert doc.entities[4].pts == [(0.0, 0.0), (5.0, 0.0)]
    print("PASS entity planes (flipped brought back to plan, tilted "
          "counted), section order, terminators")


# ---------------------------------------------------------------- skipped --

def test_skipped(tmp):
    ents = (
        line("S-WALL", 0, 0, 10, 0)
        + [(0, "SPLINE"), (8, "S-SLAB"), (70, "8"), (71, "3"),
           (10, "0"), (20, "0"), (10, "5"), (20, "5")]
        + [(0, "HATCH"), (8, "S-WALL-PATT"), (2, "SOLID"), (70, "1")]
        + line("S-WALL", 0, 0, 5, 5, (67, "1"))
        + line("S-WALL", 0, 0, 7, 7, (60, "1"))
        + [(0, "DIMENSION"), (8, "S-DIMS"), (2, "*D1"), (10, "0"), (20, "0")]
        + [(0, "ELLIPSE"), (8, "S-X"), (10, "0"), (20, "0")]
        + [(0, "3DFACE"), (8, "S-X"), (10, "0"), (20, "0")]
        + [(0, "POLYLINE"), (8, "S-MESH"), (66, "1"), (70, "64"),
           (0, "VERTEX"), (8, "S-MESH"), (10, "0"), (20, "0"), (70, "192"),
           (0, "VERTEX"), (8, "S-MESH"), (10, "1"), (20, "0"), (70, "192"),
           (0, "SEQEND")]
        + [(0, "LINE"), (8, "S-WALL"), (10, "1"), (20, "1")]
        + [(0, "CIRCLE"), (8, "S-COLS"), (10, "1"), (20, "1"), (40, "0")]
        + [(0, "VERTEX"), (8, "S-X"), (10, "9"), (20, "9")]
        + [(0, "POLYLINE"), (8, "S-WALL"), (66, "1"), (70, "9"),
           (0, "VERTEX"), (8, "S-WALL"), (10, "0"), (20, "0"), (30, "4"),
           (0, "VERTEX"), (8, "S-WALL"), (10, "0"), (20, "8"), (42, "0.25"),
           (0, "VERTEX"), (8, "S-WALL"), (10, "5"), (20, "5"), (70, "16"),
           (0, "VERTEX"), (8, "S-WALL"), (10, "6"), (20, "8"),
           (0, "SEQEND"), (8, "S-WALL")])
    doc = dx.read(put(tmp, "skip.dxf", raw(doc_pairs(ents))))
    assert doc.skipped == {
        "SPLINE": 1, "HATCH": 1, "PAPERSPACE": 1, "INVISIBLE": 1,
        "DIMENSION": 1, "ELLIPSE": 1, "3DFACE": 1, "POLYLINE(mesh)": 1,
        "LINE(incomplete)": 1, "CIRCLE(incomplete)": 1, "VERTEX": 1,
    }, doc.skipped
    # 13 top-level records: 2 read, 11 counted -- nothing silently dropped
    assert len(doc.entities) == 2
    assert len(doc.entities) + sum(doc.skipped.values()) == 13
    seg, pl = doc.entities
    assert seg.pts == [(0.0, 0.0), (10.0, 0.0)]
    # the 3D polyline reads in plan; its spline-frame vertex is not a vertex
    assert pl.closed and pl.pts == [(0.0, 0.0), (0.0, 8.0), (6.0, 8.0)]
    assert pl.bulges == [0.0, 0.25, 0.0], pl.bulges
    # a skipped entity's layer is not invented into the layer list
    assert sorted(doc.layers) == ["S-WALL"], sorted(doc.layers)

    # the three the brief names, alone: counts exact
    doc = dx.read(put(tmp, "skip3.dxf", raw(doc_pairs(
        line("S-WALL", 0, 0, 1, 1)
        + [(0, "SPLINE"), (8, "S-SLAB"), (10, "0"), (20, "0")]
        + [(0, "HATCH"), (8, "S-WALL-PATT"), (2, "SOLID")]
        + line("S-WALL", 0, 0, 5, 5, (67, "1"))))))
    assert doc.skipped == {"SPLINE": 1, "HATCH": 1, "PAPERSPACE": 1}
    assert len(doc.entities) == 1
    print("PASS coverage-honest skipped tally (every record read or "
          "counted, by type or reason)")


# ------------------------------------------------------------ layer roles --

def test_classify(tmp):
    names = ["S-GRID", "S-WALL-CONC", "S-COLS", "A-WALL-CMU", "S-SLAB-EDGE",
             "M-OPNG", "XREF-TITLE", "0", "S-SLAB-OPNG", "S-WALL-COLS",
             "TITLE-BLOCK", "BLDG|S-WALL", "BLDG$0$S-GRID", "S-GRID|A-MSNRY",
             "S-CONC", "S-EDGE", "SW-1", "A-WALL-CMU-OPNG", "DEFPOINTS",
             "S_COLUMN_NEW", "S SLABEDGE", "S-GRID-WALL-SLAB", "A-BLOCK"]
    doc = dx.read(put(tmp, "layers.dxf", raw(doc_pairs(
        line("s-cols-low", 0, 0, 1, 1), layers=names))))
    roles = dx.classify_layers(doc)
    assert roles == {
        "grid": ["BLDG$0$S-GRID", "S-GRID"],
        "wall": ["BLDG|S-WALL", "S-WALL-CONC", "SW-1"],
        "column": ["S-COLS", "S_COLUMN_NEW", "s-cols-low"],
        # the ONE precedence rule: the more specific token wins
        "masonry": ["A-BLOCK", "A-WALL-CMU", "S-GRID|A-MSNRY"],
        "slab": ["S SLABEDGE", "S-SLAB-EDGE"],
        "opening": ["A-WALL-CMU-OPNG", "M-OPNG", "S-SLAB-OPNG"],
        "unknown": ["0", "DEFPOINTS", "S-CONC", "S-EDGE",
                    "S-GRID-WALL-SLAB", "S-WALL-COLS", "TITLE-BLOCK",
                    "XREF-TITLE"],
    }, roles
    assert sum(len(v) for v in roles.values()) == len(doc.layers) == 24
    assert dx.layer_role("A-WALL-CMU") == "masonry"     # masonry over wall
    assert dx.layer_role("A-WALL") == "wall"
    assert dx.layer_role("S-WALL-COLS") == "unknown"    # two equal roles
    assert dx.layer_role("") == "unknown"
    # a layer that only an entity names is still listed (and counted)
    assert doc.layers["s-cols-low"]["count"] == 1
    assert doc.layers["S-GRID"]["count"] == 0

    # off / frozen flags from the LAYER table are reported, not acted on
    doc = dx.read(put(tmp, "off.dxf", raw(
        [(0, "SECTION"), (2, "TABLES"), (0, "TABLE"), (2, "LAYER"),
         (0, "LAYER"), (2, "S-OLD"), (70, "1"), (62, "-3"),
         (0, "LAYER"), (2, "S-NEW"), (70, "0"), (62, "5"),
         (0, "ENDTAB"), (0, "ENDSEC"),
         (0, "SECTION"), (2, "ENTITIES")]
        + line("s-old", 0, 0, 1, 1) + [(0, "ENDSEC"), (0, "EOF")])))
    assert doc.layers == {
        "S-OLD": {"color": 3, "count": 1, "off": True, "frozen": True},
        "S-NEW": {"color": 5, "count": 0, "off": False, "frozen": False},
    }, doc.layers
    assert doc.entities[0].layer == "S-OLD"            # one spelling kept
    print("PASS layer roles from names only (tokens, reference prefixes, "
          "the single precedence rule, unknown listed)")


# ------------------------------------------------------------------- text --

def test_text(tmp):
    ents = [
        (0, "TEXT"), (8, "S-GRID"), (10, "0"), (20, "0"), (40, "1.5"),
        (1, "B"), (72, "1"), (73, "2"), (11, "7"), (21, "8"),
        (0, "TEXT"), (8, "S-NOTE"), (10, "3"), (20, "4"), (40, "0.5"),
        (1, "%%uGENERAL NOTES%%u"),
        (0, "TEXT"), (8, "S-NOTE"), (10, "1"), (20, "2"), (40, "0.5"),
        (1, "45%%d %%c24 %%p1/4 100%%% %%176"), (11, "50"), (21, "60"),
        (0, "TEXT"), (8, "S-NOTE"), (10, "2"), (20, "2"), (40, "0.5"),
        (1, "FIT"), (72, "5"), (11, "9"), (21, "2"),
        (0, "TEXT"), (8, "S-NOTE"), (10, "2"), (20, "2"), (40, "0.5"),
        (1, "   "),
        (0, "TEXT"), (8, "S-NOTE"), (40, "0.5"), (1, "LOST"),
        (0, "TEXT"), (8, "S-NOTE"), (10, "6"), (20, "6"), (1, "R\\U+00D8"),
        (72, "2"), (11, "8"), (21, "6"),
    ]
    doc = dx.read(put(tmp, "text.dxf", raw(doc_pairs(ents))))
    assert doc.skipped == {"TEXT(empty)": 1, "TEXT(incomplete)": 1}, \
        doc.skipped
    b, head, codes, fit, uni = doc.entities
    assert b.text == "B" and b.pts == [(7.0, 8.0)] and b.height == 1.5
    assert head.text == "GENERAL NOTES" and head.pts == [(3.0, 4.0)]
    assert codes.text == "45\u00b0 \u00d824 \u00b11/4 100% \u00b0", codes.text
    assert codes.pts == [(1.0, 2.0)]        # left text: the insertion point
    assert fit.pts == [(2.0, 2.0)]          # aligned / fit text starts here
    assert uni.text == "R\u00d8" and uni.pts == [(8.0, 6.0)]
    assert uni.height == 0.0
    print("PASS TEXT anchors (alignment point when justified) and control "
          "codes")


# ------------------------------------------------------------------ units --

def test_units(tmp):
    want = {1: ("in", 1 / 12), 2: ("ft", 1.0), 4: ("mm", 1 / 304.8),
            5: ("cm", 1 / 30.48), 6: ("m", 1 / 0.3048)}
    assert dx.INSUNITS == {k: v[0] for k, v in want.items()}
    assert dx.FEET_PER_UNIT == {v[0]: v[1] for v in want.values()}
    for code, (name, feet) in want.items():
        doc = dx.read(put(tmp, f"u{code}.dxf", raw(doc_pairs(
            line("0", 0, 0, 1, 1),
            header=[(9, "$insunits"), (70, f"     {code}")]))))
        assert (doc.units, doc.insunits) == (name, code), doc.units
        assert doc.feet() == feet, (name, doc.feet())
    assert near(dx.FEET_PER_UNIT["m"] * 0.3048, 1.0)
    assert near(dx.FEET_PER_UNIT["mm"] * 304.8, 1.0)

    # unitless (0) and a unit this reader does not convert: no guessing
    doc = dx.read(put(tmp, "u0.dxf", raw(doc_pairs(
        line("0", 0, 0, 1, 1), header=[(9, "$INSUNITS"), (70, "0")]))))
    assert (doc.units, doc.insunits) == ("", 0)
    assert "declares no units" in str(expect(DxfError, doc.feet))
    doc = dx.read(put(tmp, "u3.dxf", raw(doc_pairs(
        line("0", 0, 0, 1, 1), header=[(9, "$INSUNITS"), (70, "3")]))))
    assert (doc.units, doc.insunits) == ("", 3)
    assert "unit code 3" in str(expect(DxfError, doc.feet))
    assert doc.feet("m") == 1 / 0.3048
    # an R12 file has no units variable at all
    doc = dx.read(put(tmp, "r12.dxf", raw(doc_pairs(line("0", 0, 0, 1, 1)))))
    assert (doc.units, doc.insunits) == ("", None)
    assert "declares no units" in str(expect(DxfError, doc.feet))
    print("PASS declared units (in / ft / mm / cm / m) and the refusal to "
          "guess when none are declared")


# -------------------------------------------------------------- tolerance --

def test_tolerance(tmp):
    pairs = doc_pairs(
        line("S-WALL", 0, 0, 10, 0)
        + [(0, "TEXT"), (8, "S-NOTE"), (10, "1"), (20, "1"), (40, "1"),
           (1, "")]
        + [(0, "CIRCLE"), (8, "S-COLS"), (10, "2"), (20, "3"), (40, "1.5")],
        header=[(9, "$INSUNITS"), (70, "2")])

    def check(doc, what):
        assert [e.kind for e in doc.entities] == ["LINE", "CIRCLE"], what
        # the empty TEXT value stayed a value: the pairs after it line up
        assert doc.skipped == {"TEXT(empty)": 1}, (what, doc.skipped)
        assert doc.entities[1].center == (2.0, 3.0), what
        assert doc.units == "ft", what

    check(dx.read(put(tmp, "crlf.dxf", raw(pairs))), "CR LF")
    check(dx.read(put(tmp, "lf.dxf", raw(pairs, "\n"))), "LF")
    check(dx.read(put(tmp, "cr.dxf", raw(pairs, "\r"))), "CR")
    check(dx.read(put(tmp, "crcrlf.dxf", raw(pairs, "\r\r\n"))), "CR CR LF")
    check(dx.read(put(tmp, "bom.dxf", b"\xef\xbb\xbf" + raw(pairs))), "BOM")
    check(dx.read(put(tmp, "blank.dxf",
                      b"\r\n\r\n" + raw(pairs) + b"\r\n\r\n   \r\n")),
          "leading / trailing blank lines")
    # blank lines between pairs (only where a group code is expected)
    spaced = b"".join(f"{c}\r\n{v}\r\n\r\n".encode() for c, v in pairs)
    check(dx.read(put(tmp, "spaced.dxf", spaced)), "blank lines between pairs")
    # right-justified codes, lowercase tags, and no ENDSEC / EOF at all
    lower = [(c, v.lower() if c in (0, 2, 9) else v) for c, v in pairs]
    lower = [p for p in lower if p[1] not in ("endsec", "eof")]
    padded = "".join(f"{c:>3}\r\n{v}\r\n" for c, v in lower).encode()
    check(dx.read(put(tmp, "lower.dxf", padded)), "lowercase, no ENDSEC/EOF")
    # text in the old single-byte code page still reads
    doc = dx.read(put(tmp, "ansi.dxf", raw(doc_pairs(
        [(0, "TEXT"), (8, "S-NOTE"), (10, "1"), (20, "1"), (40, "1"),
         (1, "PLACEHOLDER")])).replace(b"PLACEHOLDER", b"4\xb0 TYP")))
    assert doc.entities[0].text == "4\u00b0 TYP", doc.entities[0].text
    print("PASS tolerance (CR LF / LF / CR / doubled CR, BOM, blank lines, "
          "lowercase tags, missing ENDSEC and EOF, empty values kept)")


def test_modern_layout(tmp):
    """The section and record furniture a current CAD export carries
    (handles, subclass markers, other tables, pseudo-blocks for the two
    spaces, CLASSES and OBJECTS) is walked past without tripping.  The
    marker VALUES here are stand-ins: the reader never looks at them."""
    h = [(5, "1F"), (330, "1E"), (100, "MarkerEntity")]
    pairs = [
        (0, "SECTION"), (2, "HEADER"),
        (9, "$ACADVER"), (1, "AC1027"),
        (9, "$INSBASE"), (10, "0.0"), (20, "0.0"), (30, "0.0"),
        (9, "$INSUNITS"), (70, "     1"),
        (9, "$MEASUREMENT"), (70, "     0"),
        (0, "ENDSEC"),
        (0, "SECTION"), (2, "CLASSES"),
        (0, "CLASS"), (1, "SCALE"), (2, "MarkerScale"), (90, "1153"),
        (0, "ENDSEC"),
        (0, "SECTION"), (2, "TABLES"),
        (0, "TABLE"), (2, "VPORT"), (5, "8"), (100, "MarkerSymbolTable"),
        (70, "1"),
        (0, "VPORT"), (5, "94"), (100, "MarkerViewportTableRecord"),
        (2, "*ACTIVE"), (70, "0"), (10, "0.0"), (20, "0.0"),
        (0, "ENDTAB"),
        (0, "TABLE"), (2, "LTYPE"), (5, "5"), (70, "1"),
        (0, "LTYPE"), (5, "14"), (2, "BYBLOCK"), (70, "0"), (62, "-9"),
        (0, "ENDTAB"),
        (0, "TABLE"), (2, "LAYER"), (5, "2"), (100, "MarkerSymbolTable"),
        (70, "2"),
        (0, "LAYER"), (5, "10"), (100, "MarkerSymbolTableRecord"),
        (100, "MarkerLayerTableRecord"), (2, "0"), (70, "     0"),
        (62, "     7"), (6, "Continuous"), (370, "    -3"), (390, "F"),
        (0, "LAYER"), (5, "2B"), (100, "MarkerSymbolTableRecord"),
        (100, "MarkerLayerTableRecord"), (2, "S-Wall"), (70, "     0"),
        (62, "     1"), (6, "Continuous"),
        (0, "ENDTAB"),
        (0, "TABLE"), (2, "BLOCK_RECORD"), (5, "1"), (70, "2"),
        (0, "BLOCK_RECORD"), (5, "1F"), (2, "*Model_Space"), (70, "0"),
        (0, "BLOCK_RECORD"), (5, "1B"), (2, "*Paper_Space"), (70, "0"),
        (0, "ENDTAB"),
        (0, "ENDSEC"),
        (0, "SECTION"), (2, "BLOCKS"),
        (0, "BLOCK"), (5, "20"), (100, "MarkerEntity"), (8, "0"),
        (100, "MarkerBlockBegin"), (2, "*Model_Space"), (70, "0"),
        (10, "0.0"), (20, "0.0"), (30, "0.0"), (3, "*Model_Space"), (1, ""),
        (0, "ENDBLK"), (5, "21"), (100, "MarkerEntity"), (8, "0"),
        (100, "MarkerBlockEnd"),
        (0, "BLOCK"), (5, "1C"), (67, "1"), (8, "0"), (2, "*Paper_Space"),
        (70, "0"), (10, "0.0"), (20, "0.0"), (30, "0.0"),
        (3, "*Paper_Space"), (1, ""),
        (0, "ENDBLK"), (5, "1D"), (67, "1"), (8, "0"),
        (0, "ENDSEC"),
        (0, "SECTION"), (2, "ENTITIES"),
        (0, "LWPOLYLINE")] + h + [
        (8, "S-WALL"), (100, "MarkerPolyline"), (90, "        4"),
        (70, "     1"), (43, "0.0"),
        (10, "120.0"), (20, "0.0"), (10, "408.0"), (20, "0.0"),
        (10, "408.0"), (20, "12.0"), (10, "120.0"), (20, "12.0"),
        (0, "CIRCLE")] + h + [
        (8, "S-Cols"), (100, "MarkerCircle"), (10, "288.0"), (20, "240.0"),
        (30, "0.0"), (40, "12.0"),
        (0, "VIEWPORT")] + h + [(67, "1"), (8, "0"), (10, "5.25")] + [
        (0, "ENDSEC"),
        (0, "SECTION"), (2, "OBJECTS"),
        (0, "DICTIONARY"), (5, "C"), (330, "0"), (100, "MarkerDictionary"),
        (3, "GROUP_DICT"), (350, "D"),
        (0, "ENDSEC"),
        (0, "EOF")]
    doc = dx.read(put(tmp, "modern.dxf", raw(pairs)))
    assert (doc.units, doc.insunits) == ("in", 1) and doc.feet() == 1 / 12
    assert doc.skipped == {"PAPERSPACE": 1}, doc.skipped
    wall, col = doc.entities
    assert wall.closed and wall.pts == [(120.0, 0.0), (408.0, 0.0),
                                        (408.0, 12.0), (120.0, 12.0)]
    # the entity says S-WALL, the table S-Wall: one layer, table spelling
    assert wall.layer == "S-Wall" and col.layer == "S-Cols"
    assert doc.layers == {
        "0": {"color": 7, "count": 0, "off": False, "frozen": False},
        "S-Wall": {"color": 1, "count": 1, "off": False, "frozen": False},
        "S-Cols": {"color": 7, "count": 1, "off": False, "frozen": False},
    }, doc.layers
    assert dx.classify_layers(doc) == {
        "grid": [], "wall": ["S-Wall"], "column": ["S-Cols"], "masonry": [],
        "slab": [], "opening": [], "unknown": ["0"]}
    assert doc.extents == (120.0, 0.0, 408.0, 252.0), doc.extents
    # wall thickness in feet, through the declared units
    assert near((wall.pts[2][1] - wall.pts[1][1]) * doc.feet(), 1.0)
    print("PASS a current-format file layout (handles, subclass markers, "
          "other tables, space pseudo-blocks, CLASSES / OBJECTS)")


def test_broken(tmp):
    pairs = doc_pairs(line("S-WALL", 0, 0, 10, 0))
    lines = [s for c, v in pairs for s in (str(c), v)]

    # odd line count: the final value line is gone (cut off after "0")
    cut = lines[:-1]
    assert len(cut) % 2 == 1
    e = expect(DxfError, dx.read, put(tmp, "odd.dxf",
                                      ("\r\n".join(cut) + "\r\n").encode()))
    assert f"line {len(cut)}:" in str(e) and "odd number of lines" in str(e), \
        str(e)
    e = expect(DxfError, dx.read, put(tmp, "odd2.dxf",
                                      "\r\n".join(cut).encode()))
    assert f"line {len(cut)}:" in str(e), str(e)

    # a line lost in the middle: the next value is read as a group code
    k = lines.index("LINE")                  # 0-based index of that value
    lost = lines[:k - 1] + lines[k:]         # its group code "0" is gone
    e = expect(DxfError, dx.read, put(tmp, "lost.dxf",
                                      ("\r\n".join(lost) + "\r\n").encode()))
    assert f"line {k}:" in str(e) and "group code" in str(e), str(e)
    assert "'LINE'" in str(e), str(e)

    # a group code that is not a whole number
    bad = list(lines)
    bad[k + 1] = "8.5"                       # the code line after "LINE"
    e = expect(DxfError, dx.read, put(tmp, "code.dxf",
                                      ("\r\n".join(bad) + "\r\n").encode()))
    assert f"line {k + 2}:" in str(e), str(e)

    # a coordinate that is not a number
    bad = list(lines)
    j = bad.index("10") + 1                  # the value of the first group 10
    assert bad[j - 1] == "10" and bad[j] == "0"
    bad[j] = "ten"
    e = expect(DxfError, dx.read, put(tmp, "num.dxf",
                                      ("\r\n".join(bad) + "\r\n").encode()))
    assert f"line {j + 1}:" in str(e) and "group 10 needs a number" in str(e), \
        str(e)
    assert all(isinstance(x, ValueError) for x in (e,))
    print("PASS structural damage is refused with its line number (odd "
          "line count, lost line, bad group code, bad number)")


# ----------------------------------------------------------------- writer --

def test_writer_refusals(tmp):
    ok = {"kind": "line", "pts": [(0, 0), (1, 1)]}
    for bad in ("s-wall", "S WALL", "S.WALL", "", "X" * 32):
        path = os.path.join(tmp, "refused.dxf")
        e = expect(ValueError, dx.write_background, path,
                   [dict(ok, layer="S-GRID"), dict(ok, layer=bad)])
        assert "CAD layer name" in str(e), str(e)
        assert not isinstance(e, DxfError)
        assert not os.path.exists(path) and not os.path.exists(path + ".part")
    # the rule is enforced, not silently fixed: sanitizing is the caller's
    # explicit step
    fixed = selvage.dxf_layer_name("s wall.new")
    assert fixed == "S_WALL_NEW"
    path = os.path.join(tmp, "fixed.dxf")
    assert dx.write_background(path, [dict(ok, layer=fixed)]) == 1
    assert dx.read(path).entities[0].layer == "S_WALL_NEW"
    assert dx.write_background(path, [dict(ok, layer="X" * 31)]) == 1

    path = os.path.join(tmp, "refused2.dxf")
    for shape, msg in (
            ({"layer": "S-X", "kind": "spline", "pts": []}, "unknown kind"),
            ({"layer": "S-X", "kind": "line", "pts": [(0, 0)]},
             "exactly two points"),
            ({"layer": "S-X", "kind": "line"}, "needs 'pts'"),
            ({"layer": "S-X", "kind": "poly", "pts": [(0, 0)]},
             "at least two points"),
            ({"layer": "S-X", "kind": "poly", "pts": [(0, 0), (1, 1)],
              "bulges": [1]}, "one value per vertex"),
            ({"layer": "S-X", "kind": "circle", "center": (0, 0),
              "radius": 0}, "positive radius"),
            ({"layer": "S-X", "kind": "circle", "center": (0, 0)},
             "needs a 'radius'"),
            ({"layer": "S-X", "kind": "arc", "center": (0, 0), "radius": 1},
             "'start_deg' and 'end_deg'"),
            ({"layer": "S-X", "kind": "text", "at": (0, 0), "text": " "},
             "non-empty 'text'"),
            ({"layer": "S-X", "kind": "point"}, "needs 'at'")):
        e = expect(ValueError, dx.write_background, path, [shape])
        assert msg in str(e), (msg, str(e))
        assert not os.path.exists(path)
    e = expect(ValueError, dx.write_background, path,
               [dict(ok, layer="S-X")], units="furlong")
    assert "unknown units" in str(e) and not os.path.exists(path)

    # a control character can never inject a group code; -0 is written 0
    path = os.path.join(tmp, "clean.dxf")
    dx.write_background(path, [
        {"layer": "S-X", "kind": "text", "at": (-0.00001, 0),
         "text": "A\r\n0\r\nLINE", "height": 1},
        {"layer": "S-X", "kind": "line", "pts": [(0, 0), (1, 0)]}],
        units="mm", precision=2)
    doc = dx.read(path)
    assert [e.kind for e in doc.entities] == ["TEXT", "LINE"]
    assert doc.entities[0].text == "A  0  LINE"
    assert doc.entities[0].pts == [(0.0, 0.0)] and doc.units == "mm"
    assert b"-0.00" not in slurp(path)
    assert (10, "0.00") in selvage.read_dxf_pairs(path)
    print("PASS writer refusals (layer-name rule enforced at creation, bad "
          "shapes, nothing written) and value cleaning")


def main():
    tmp = tempfile.mkdtemp(prefix="dxfread_test_")
    test_sniff(tmp)
    test_round_trip(tmp)
    test_lwpolyline_mtext(tmp)
    test_insert(tmp)
    test_planes(tmp)
    test_skipped(tmp)
    test_classify(tmp)
    test_text(tmp)
    test_units(tmp)
    test_tolerance(tmp)
    test_modern_layout(tmp)
    test_broken(tmp)
    test_writer_refusals(tmp)
    leftovers = [f for _, _, fs in os.walk(tmp) for f in fs
                 if f.endswith(".part")]
    assert not leftovers, leftovers
    print("DXFREAD TEST PASSED")
    print("outputs in", tmp)


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print("DXFREAD TEST FAILED:", e)
        sys.exit(1)
