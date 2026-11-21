"""The Batterboard text readers: general notes and the shear wall schedule.

Regression case 6 (owner brief section 9): two headings in one column;
the note must be cited under the heading directly above it.  Plus the
topics that make a note govern layout, the schedule table, the zone
length detail, /Rotate pages and the honest "left unread" remarks.

Every fixture is synthetic.  Run:  python tests/test_batterboard_text.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz                                                     # noqa: E402

from rfi_stamper import papertrail                              # noqa: E402
from rfi_stamper.batterboard import notes as N                  # noqa: E402
from rfi_stamper.batterboard import sampleset as ss             # noqa: E402
from rfi_stamper.batterboard import schedule as S               # noqa: E402
from rfi_stamper.batterboard.sampleset import Sheet             # noqa: E402
from rfi_stamper.draft import parse_ftin                        # noqa: E402

HEADS = ["MARK", "THICKNESS", "MIN LENGTH", "END BARS", "TIES", "ZONE LENGTH"]


def close(a, b, tol=1e-6):
    return a is not None and b is not None and abs(a - b) <= tol


def rect_close(a, b, tol=0.6):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def turned(page, rect):
    """A rot-0 viewer rectangle as it sits on a rotated page."""
    r = fitz.Rect(rect) * page.rotation_matrix
    r.normalize()
    return (r.x0, r.y0, r.x1, r.y1)


# ---------------------------------------------------------------- fixtures --

def notes_sheet(columns, title=None, body_bold=False) -> Sheet:
    """A notes page.  ``columns`` = ``[(x, [("H", text, bold) | ("N", number,
    text)])]``; ``title`` = ``(x, y, text, size)``."""
    s = Sheet(ss.SHEET_W, ss.SHEET_H)
    if title:
        s.text(title[0], title[1], title[2], title[3], bold=True)
    for x, items in columns:
        y = 120.0
        for it in items:
            if it[0] == "H":
                s.text(x, y, it[1], 9.5 if it[2] else 7.5, bold=it[2])
                y += 16.0
                continue
            _k, num, text = it
            lines, cur = [], ""
            for w in text.split():
                if fitz.get_text_length((cur + " " + w).strip(), "helv", 7.5) > 380.0:
                    lines.append(cur)
                    cur = w
                else:
                    cur = (cur + " " + w).strip()
            lines.append(cur)
            s.text(x, y, f"{num}.", 7.5, bold=body_bold)
            for ln in lines:
                s.text(x + 16.0, y, ln, 7.5, bold=body_bold)
                y += 10.5
            y += 3.0
    return s


def schedule_sheet(rows, heads=HEADS, rules=True, detail=None) -> Sheet:
    """A schedule page laid out like the sample's, with any rows."""
    s = Sheet(ss.SHEET_W, ss.SHEET_H)
    s.rect(36, 36, ss.SHEET_W - 36, ss.SHEET_H - 36, width=2.0)       # sheet border
    s.text(70, 90, "SHEAR WALL SCHEDULE", 12, bold=True)
    xs = [70.0, 130.0, 210.0, 300.0, 380.0, 480.0, 580.0][:len(heads) + 1]
    y = 110.0
    for i, row in enumerate([list(heads)] + [list(r) for r in rows]):
        for j, cell in enumerate(row):
            if cell:
                s.text(xs[j] + 5, y + 12, cell, 7.5, bold=(i == 0))
        if rules:
            s.line((xs[0], y), (xs[-1], y), width=0.5)
        y += 18.0
    if rules:
        s.line((xs[0], y), (xs[-1], y), width=0.5)
        for x in xs:
            s.line((x, 110.0), (x, y), width=0.5)
    for k, line in enumerate(detail or ()):
        s.text(350, 336.0 + 12.0 * k, line, 7.5)
    return s


LONG = ("THIS NOTE IS LONG ENOUGH TO WRAP ONTO A SECOND LINE SO THAT THE COLUMN IS "
        "FILLED OUT TO ITS FULL WIDTH AND THE LAST LINE IS SHORT.")


# ------------------------------------------------------------------ case 6 --

def case6():
    doc, page = ss.sheet_notes().build()
    got = N.read_notes(page, "S-001")
    want = [(col, heading, num, text)
            for col, heading, items in ss.NOTES for num, text in items]
    assert [(n.column, n.heading, n.number, n.text) for n in got] == want, \
        [(n.column, n.heading, n.number, n.text) for n in got]
    assert all(n.sheet == "S-001" and n.page == 1 for n in got)
    tol = next(n for n in got if n.text.startswith("TOLERANCES FOR CONCRETE CONSTRUCTION"))
    assert tol.heading == "CONCRETE" and tol.heading != "GENERAL" and tol.number == "2"
    assert tol.ref == "S-001/CONCRETE/2"
    sur = next(n for n in got if "SURVEY" in n.text)
    assert sur.heading == "SURVEY" and sur.heading != "OPENINGS" and sur.column == 1
    # the sheet title above column 0 is never a note's heading
    assert not any(n.heading == "GENERAL NOTES" for n in got)
    # every box starts at its column's margin and holds the note's lines
    for n in got:
        assert abs(n.bbox[0] - (70.0 if n.column == 0 else 520.0)) < 1.0, n.bbox
        assert n.bbox[2] - n.bbox[0] > 100 and 120 < n.bbox[1] < n.bbox[3] < 230, n.bbox
    assert N.read_notes(page, "S-001", page_no=7)[0].page == 7

    # a continuation column: notes with NO heading above them in their column
    cols = [(70.0, [("H", "GENERAL", True), ("N", "1", LONG), ("N", "2", LONG),
                    ("N", "3", LONG)]),
            (520.0, [("N", "4", LONG), ("N", "5", "A SHORT NOTE."),
                     ("H", "CONCRETE", True), ("N", "1", LONG), ("N", "2", "ANOTHER SHORT NOTE.")])]
    want2 = [(0, "GENERAL", "1"), (0, "GENERAL", "2"), (0, "GENERAL", "3"),
             (1, "", "4"), (1, "", "5"), (1, "CONCRETE", "1"), (1, "CONCRETE", "2")]
    for title in (None,
                  (393.0, 80.0, "STRUCTURAL GENERAL NOTES", 14.0),      # spans the gutter
                  (520.0, 80.0, "GENERAL NOTES", 14.0),                 # sits right above column 1
                  (70.0, 80.0, "GENERAL NOTES", 14.0)):
        _d, pg = notes_sheet(cols, title).build()
        got2 = N.read_notes(pg, "S-002")
        assert [(n.column, n.heading, n.number) for n in got2] == want2, (title, [
            (n.column, n.heading, n.number) for n in got2])
        assert got2[3].text == LONG and got2[4].text == "A SHORT NOTE."
    # only a sheet title on the page, no column headings: every heading is ""
    _d, pg = notes_sheet([(70.0, [("N", "1", LONG), ("N", "2", LONG)]),
                          (520.0, [("N", "3", LONG)])],
                         (70.0, 80.0, "GENERAL NOTES", 14.0)).build()
    assert [(n.column, n.heading, n.number) for n in N.read_notes(pg)] == [
        (0, "", "1"), (0, "", "2"), (1, "", "3")]
    # plain headings (not bold, body size) are known by the note 1 under
    # them; a short last line of a wrapped note never becomes a heading
    plain = [(70.0, [("H", "FORMWORK", False), ("N", "1", LONG), ("N", "2", LONG),
                     ("H", "CURING", False), ("N", "1", LONG), ("N", "2", "KEEP SURFACES WET.")])]
    for bold in (False, True):
        _d, pg = notes_sheet(plain, body_bold=bold).build()
        got3 = N.read_notes(pg, "S-003")
        assert [(n.heading, n.number) for n in got3] == [
            ("FORMWORK", "1"), ("FORMWORK", "2"), ("CURING", "1"), ("CURING", "2")], \
            (bold, [(n.heading, n.number, n.text) for n in got3])
        assert got3[1].text == LONG and got3[3].text == "KEEP SURFACES WET."
    print("PASS case 6: every note is cited under the heading directly above it in "
          "its own column; a continuation column and the sheet title give no heading")


# ------------------------------------------------------------------ topics --

def topics():
    doc, page = ss.sheet_notes().build()
    got = N.read_notes(page, "S-001")
    by = {(n.heading, n.number): n for n in got}
    assert by[("GENERAL", "1")].topics == ["layout"]            # verify dimensions
    assert by[("GENERAL", "2")].topics == ["layout"]            # do not scale
    assert by[("CONCRETE", "1")].topics == []
    assert by[("CONCRETE", "2")].topics == ["tolerance"]
    assert by[("OPENINGS", "1")].topics == ["openings"]         # reinforcing at openings
    assert by[("OPENINGS", "2")].topics == ["openings"]         # openings need approval
    assert by[("SURVEY", "1")].topics == ["survey"]
    gov = N.governing(got)
    assert [(n.heading, n.number) for n in gov] == [
        ("GENERAL", "1"), ("GENERAL", "2"), ("CONCRETE", "2"),
        ("OPENINGS", "1"), ("OPENINGS", "2"), ("SURVEY", "1")]
    assert all(set(n.topics) <= set(N.TOPICS) for n in got)
    sv = N.survey_required(got)
    assert len(sv) == 1 and sv[0] is by[("SURVEY", "1")]
    assert N.topics_of("FIELD SURVEY OF EXISTING UTILITIES IS REQUIRED.") == ["survey"]
    plain_survey = N.Note("S-9", 1, 0, "X", "1", "FIELD SURVEY OF EXISTING UTILITIES.",
                          (0, 0, 1, 1), ["survey"])
    assert N.survey_required([plain_survey]) == []              # not a slab elevation survey
    assert N.topics_of("SET CONTROL POINTS BEFORE LAYOUT.") == ["layout"]
    assert N.topics_of("PROVIDE TRIM BARS AT EACH OPENING.") == ["openings"]
    assert N.topics_of("OPENINGS ARE SHOWN ON PLAN.") == []
    # the quote is the printed text, nothing reworded, with where it sits
    n = by[("CONCRETE", "2")]
    q = n.quote(file="set.pdf")
    assert isinstance(q, papertrail.Quote)
    assert (q.text, q.sheet, q.heading, q.note_no, q.file, q.page) == (
        ss.NOTES[1][2][1][1], "S-001", "CONCRETE", "2", "set.pdf", 1)
    assert q.cite() == "S-001, CONCRETE, note 2"
    assert papertrail.Quote.from_dict(q.to_dict()) == q
    print("PASS topics: tolerance, openings (2), survey and layout notes tagged; "
          "governing() and survey_required() exact; quote() carries the exact text")


# ---------------------------------------------------------------- schedule --

def check_schedule(sc, where=""):
    assert sc.problems == [], (where, sc.problems)
    assert sc.marks() == [r[0] for r in ss.SCHEDULE], (where, sc.marks())
    for row, src in zip(sc.rows, ss.SCHEDULE):
        mark, thick, minlen, bars, ties, zone = src
        assert row.mark == mark
        assert close(row.thickness_ft, parse_ftin(thick)), (where, row.thickness_ft)
        assert close(row.min_length_ft, parse_ftin(minlen)), (where, row.min_length_ft)
        assert close(row.zone_length_ft, parse_ftin(zone)), (where, row.zone_length_ft)
        assert row.end_bars == bars and row.ties == ties, (where, row.end_bars, row.ties)
        assert list(row.cells) == HEADS, (where, list(row.cells))
        assert [row.cells[h] for h in HEADS] == list(src), (where, row.cells)
        assert row.sheet == "S-501" and row.page == 1
    assert [round(r.thickness_ft, 4) for r in sc.rows] == [1.0, 1.0, 0.8333]
    assert [r.min_length_ft for r in sc.rows] == [10.0, 12.0, 8.0]
    assert [r.zone_length_ft for r in sc.rows] == [3.0, 2.5, 2.0]
    assert [(d.mark, d.zone_length_ft, d.text, d.detail) for d in sc.details] == [
        (m, parse_ftin(ln), f"{m} = {ln}", "5") for m, ln in ss.ZONE_DETAIL], \
        (where, [(d.mark, d.zone_length_ft, d.text, d.detail) for d in sc.details])
    assert sc.disagreements() == [("SW2", 2.5, 3.0)], (where, sc.disagreements())
    assert sc.row("sw2") is sc.rows[1] and sc.row("SW9") is None
    assert sc.detail("SW2").zone_length_ft == 3.0 and sc.detail("SW9") is None


def schedule():
    doc, page = ss.sheet_schedule().build()
    sc = S.read_schedule(page, "S-501")
    check_schedule(sc)
    assert sc.sheet == "S-501" and sc.page == 1
    # row boxes sit in the table, detail boxes on the detail lines
    assert all(74 < r.bbox[0] < 76 and 128 < r.bbox[1] < r.bbox[3] < 182 for r in sc.rows), \
        [r.bbox for r in sc.rows]
    assert all(349 < d.bbox[0] < 351 and 325 < d.bbox[1] < d.bbox[3] < 365 for d in sc.details)
    # the same table drawn with no ruling lines reads the same
    rows = [list(r) for r in ss.SCHEDULE]
    _d, pg = schedule_sheet(rows, rules=False,
                            detail=[f"{m} = {ln}" for m, ln in ss.ZONE_DETAIL]).build()
    open_sc = S.read_schedule(pg, "S-501")
    assert open_sc.problems == [], open_sc.problems
    assert [[r.cells[h] for h in HEADS] for r in open_sc.rows] == rows, \
        [r.cells for r in open_sc.rows]
    assert [r.zone_length_ft for r in open_sc.rows] == [3.0, 2.5, 2.0]
    assert open_sc.disagreements() == [("SW2", 2.5, 3.0)]
    assert [d.detail for d in open_sc.details] == ["", "", ""]      # no view title drawn
    # a table with no ZONE column leaves the zone length unread, silently
    _d, pg = schedule_sheet([r[:5] for r in rows], heads=HEADS[:5]).build()
    nz = S.read_schedule(pg, "S-501")
    assert nz.marks() == ["SW1", "SW2", "SW3"] and nz.problems == []
    assert all(r.zone_length_ft is None for r in nz.rows) and nz.disagreements() == []
    assert [r.ties for r in nz.rows] == [r[4] for r in rows]
    # the detail given with a colon reads too; lengths need a unit
    _d, pg = schedule_sheet(rows, detail=["SW1: 3'-0\"", "SW2 = 30\"", "SW9 = 4'-0\""]).build()
    cz = S.read_schedule(pg, "S-501")
    assert [(d.mark, d.zone_length_ft) for d in cz.details] == [("SW1", 3.0), ("SW2", 2.5)]
    assert cz.disagreements() == []
    assert S.parse_length("12\"") == (1.0, True) and S.parse_length("1'-0\"") == (1.0, True)
    assert S.parse_length("7 1/2\"")[0] == 0.625 and S.parse_length("10 IN.") == (10 / 12, True)
    assert S.parse_length("-") == (None, True) and S.parse_length("") == (None, True)
    assert S.parse_length("12") == (None, False)                 # no unit: not guessed
    assert S.parse_length("12", "THICKNESS (IN)") == (1.0, True)
    assert S.parse_length("TBD") == (None, False)
    print("PASS schedule: rows, typed lengths, exact cells, the zone length detail "
          "(view 5) and the SW2 disagreement; ruled and unruled tables")


# ---------------------------------------------------------------- rotation --

def rotation():
    d0, p0 = ss.sheet_notes().build()
    base = N.read_notes(p0, "S-001")
    s0, q0 = ss.sheet_schedule().build()
    sbase = S.read_schedule(q0, "S-501")
    for rot in (90, 270, 180):
        _d, page = ss.sheet_notes().build(rot)
        got = N.read_notes(page, "S-001")
        assert [(n.column, n.heading, n.number, n.text, n.topics) for n in got] == [
            (n.column, n.heading, n.number, n.text, n.topics) for n in base], rot
        for a, b in zip(got, base):
            assert rect_close(a.bbox, turned(page, b.bbox)), (rot, a.bbox, turned(page, b.bbox))
        _d, page = ss.sheet_schedule().build(rot)
        sc = S.read_schedule(page, "S-501")
        check_schedule(sc, f"rot{rot}")
        for a, b in zip(sc.rows, sbase.rows):
            assert rect_close(a.bbox, turned(page, b.bbox)), (rot, a.bbox)
        for a, b in zip(sc.details, sbase.details):
            assert rect_close(a.bbox, turned(page, b.bbox)), (rot, a.bbox)
    print("PASS rotation: the same notes and the same schedule on /Rotate 90, "
          "270 and 180 pages, boxes in viewer points")


# ---------------------------------------------------------- honest problems --

def problems():
    rows = [list(r) for r in ss.SCHEDULE]
    rows[1][1] = "TBD"
    _d, page = schedule_sheet(rows).build()
    sc = S.read_schedule(page, "S-501")
    assert sc.marks() == ["SW1", "SW2", "SW3"]
    assert sc.row("SW2").thickness_ft is None
    assert sc.row("SW2").cells["THICKNESS"] == "TBD"            # the cell is kept as printed
    assert sc.row("SW1").thickness_ft == 1.0 and sc.row("SW2").min_length_ft == 12.0
    assert len(sc.problems) == 1, sc.problems
    assert "SW2" in sc.problems[0] and "TBD" in sc.problems[0] \
        and "THICKNESS" in sc.problems[0], sc.problems
    # a blank or dashed cell is blank, not a problem
    rows = [list(r) for r in ss.SCHEDULE]
    rows[0][5] = "-"
    rows[2][5] = ""
    _d, page = schedule_sheet(rows).build()
    sc = S.read_schedule(page, "S-501")
    assert [r.zone_length_ft for r in sc.rows] == [None, 2.5, None] and sc.problems == []
    # a bare number under a header with no unit is not guessed at
    rows = [list(r) for r in ss.SCHEDULE]
    rows[2][1] = "10"
    _d, page = schedule_sheet(rows).build()
    sc = S.read_schedule(page, "S-501")
    assert sc.row("SW3").thickness_ft is None and len(sc.problems) == 1
    assert "SW3" in sc.problems[0] and "\"10\"" in sc.problems[0], sc.problems
    # no MARK header: an empty schedule and one plain line saying so
    heads = ["WALL"] + HEADS[1:]
    _d, page = schedule_sheet([list(r) for r in ss.SCHEDULE], heads=heads).build()
    sc = S.read_schedule(page, "S-777")
    assert sc.rows == [] and sc.details == [] and len(sc.problems) == 1
    assert "S-777" in sc.problems[0] and "MARK" in sc.problems[0], sc.problems
    _d, blank = Sheet().build()
    sc = S.read_schedule(blank, "S-778")
    assert sc.rows == [] and len(sc.problems) == 1
    assert N.read_notes(blank, "S-778") == []
    # a notes page read as a schedule (and the reverse) finds nothing, quietly
    _d, npage = ss.sheet_notes().build()
    assert S.read_schedule(npage, "S-001").rows == []
    _d, spage = ss.sheet_schedule().build()
    assert N.read_notes(spage, "S-501") == []
    print("PASS honest problems: an unreadable cell is left unread and named; a "
          "page with no MARK header gives an empty schedule and says why")


def main():
    case6()
    topics()
    schedule()
    rotation()
    problems()
    print("BATTERBOARD TEXT TEST PASSED")


if __name__ == "__main__":
    main()
