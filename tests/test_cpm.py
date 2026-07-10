"""Headless tests for rfi_stamper.cpm (the Tautline CPM scheduler).

The heart is the hand-computed textbook fixture — a merge point, an FS
lag (which splits Total Float from Free Float), multiple initial and
terminal nodes — asserted CELL BY CELL.  Plus workday-calendar mapping
across a weekend, cycle refusal that names the loop, dirty-data
tolerance, the start-no-earlier-than term, and determinism.

Run:  python3 tests/test_cpm.py
"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rfi_stamper import cpm                     # noqa: E402
from rfi_stamper.project import ScheduleItem    # noqa: E402

MON = "2026-01-05"                              # a Monday anchor


def _item(iid, dur, depends=(), start=MON):
    """dur workdays from ``start`` inclusive."""
    d0 = dt.date.fromisoformat(start)
    end = cpm.from_index(cpm.to_index(d0, d0) + dur - 1, d0)
    return ScheduleItem(id=iid, title=iid.upper(), start=start,
                        end=end.isoformat(), depends=list(depends))


def _network():
    return [
        _item("a", 3),
        _item("b", 4),
        _item("c", 2, ["a"]),
        _item("d", 5, ["a", "b"]),
        _item("e", 4, ["c+1"]),                 # FS lag +1
        _item("f", 3, ["d"]),
        _item("g", 2, ["e", "f"]),
    ]


# the dossier's hand-computed table: (dur, es, ef, ls, lf, tf, ff, crit)
_TABLE = {
    "a": (3, 0, 3, 1, 4, 1, 0, False),
    "b": (4, 0, 4, 0, 4, 0, 0, True),
    "c": (2, 3, 5, 5, 7, 2, 0, False),
    "d": (5, 4, 9, 4, 9, 0, 0, True),
    "e": (4, 6, 10, 8, 12, 2, 2, False),
    "f": (3, 9, 12, 9, 12, 0, 0, True),
    "g": (2, 12, 14, 12, 14, 0, 0, True),
}


def test_textbook_network():
    res = cpm.analyze(_network())
    assert not res.cycle and not res.warnings, (res.cycle, res.warnings)
    for iid, (dur, es, ef, ls, lf, tf, ff, crit) in _TABLE.items():
        got = res.by_id[iid]
        assert got["dur"] == dur, (iid, got)
        assert got["es"] == es and got["ef"] == ef, (iid, got)
        assert got["ls"] == ls and got["lf"] == lf, (iid, got)
        assert got["tf"] == tf and got["ff"] == ff, (iid, got)
        assert got["critical"] is crit, (iid, got)
    assert res.critical_ids == ["b", "d", "f", "g"]
    # project = 14 workdays from Mon 01-05 -> finish Thu 01-22
    assert res.project_finish == dt.date(2026, 1, 22), res.project_finish


def test_calendar_mapping():
    res = cpm.analyze(_network())
    a, d = res.by_id["a"], res.by_id["d"]
    # A occupies Mon..Wed of the anchor week
    assert a["es_date"] == dt.date(2026, 1, 5)
    assert a["ef_date"] == dt.date(2026, 1, 7)
    # D starts the anchor week's Friday and CROSSES the weekend
    assert d["es_date"] == dt.date(2026, 1, 9)
    assert d["ef_date"] == dt.date(2026, 1, 15)
    # to_index/from_index round-trip on workdays; weekend anchor tolerated
    d0 = dt.date(2026, 1, 5)
    for k in range(0, 25):
        day = cpm.from_index(k, d0)
        assert cpm.to_index(day, d0) == k, (k, day)
        assert day.weekday() < 5
    sat = dt.date(2026, 1, 3)                   # anchor on a Saturday
    assert cpm.from_index(0, sat) == dt.date(2026, 1, 5)
    # inclusive workday counts
    assert cpm.workdays_between(dt.date(2026, 1, 5),
                                dt.date(2026, 1, 9)) == 5
    assert cpm.workdays_between(dt.date(2026, 1, 9),
                                dt.date(2026, 1, 12)) == 2   # Fri + Mon


def test_cycles_refused_loudly():
    items = [_item("a", 2, ["b"]), _item("b", 3, ["a"])]
    res = cpm.analyze(items)
    assert res.cycle, "two-node cycle not detected"
    assert set(res.cycle) >= {"A", "B"}, res.cycle
    assert res.by_id == {}                      # floats NOT computed
    items = [_item("a", 2, ["c"]), _item("b", 3, ["a"]),
             _item("c", 1, ["b"]), _item("d", 2)]
    res = cpm.analyze(items)
    assert res.cycle and set(res.cycle) >= {"A", "B", "C"}, res.cycle


def test_dirty_data_tolerated():
    items = _network()
    items.append(ScheduleItem(id="x", title="JUNK", start="not-a-date",
                              end="2026-01-09"))
    items[2].depends.append("ghost")            # dangling pred on c
    res = cpm.analyze(items)
    assert not res.cycle
    assert any("JUNK" in w and "bad date" in w for w in res.warnings)
    assert any("ghost" in w for w in res.warnings)
    # the rest of the network still analyzed, unchanged
    assert res.by_id["g"]["ef"] == 14
    assert "x" not in res.by_id
    # weekend-only item: duration clamps to 1 with a warning
    wk = [ScheduleItem(id="w", title="WKND", start="2026-01-10",
                       end="2026-01-11")]
    res2 = cpm.analyze(wk)
    assert res2.by_id["w"]["dur"] == 1
    assert any("non-workdays" in w for w in res2.warnings)
    # self-dependency ignored with a warning
    res3 = cpm.analyze([_item("s", 2, ["s"])])
    assert not res3.cycle and res3.by_id["s"]["es"] == 0
    assert any("itself" in w for w in res3.warnings)


def test_start_no_earlier_than():
    items = _network()
    # give C an entered start at workday index 5 (Mon 01-12), same 2-day
    # duration: its ES must honor the user's own bar
    items[2] = _item("c", 2, ["a"], start="2026-01-12")
    res = cpm.analyze(items)
    c, e = res.by_id["c"], res.by_id["e"]
    assert c["es"] == 5 and c["ef"] == 7, c
    assert c["tf"] == 0 and c["critical"], c    # SNET ate the float
    assert e["es"] == 8 and e["ef"] == 12, e    # pushed through the lag
    assert res.by_id["g"]["ef"] == 14           # project finish unchanged


def test_lag_parsing_and_determinism():
    assert cpm.parse_depend("abc") == ("abc", 0)
    assert cpm.parse_depend("abc+3") == ("abc", 3)
    assert cpm.parse_depend("abc-1") == ("abc", -1)
    assert cpm.parse_depend("t-100+2") == ("t-100", 2)  # id keeps hyphens
    r1 = cpm.analyze(_network())
    r2 = cpm.analyze(_network())
    assert r1.by_id == r2.by_id
    assert r1.critical_ids == r2.critical_ids
    # negative lag may pull ES before a predecessor's finish; clamped >= 0
    items = [_item("a", 2), _item("b", 2, ["a-5"])]
    res = cpm.analyze(items)
    assert res.by_id["b"]["es"] == 0


def main():
    test_textbook_network()
    print("PASS textbook network — every table cell (TF vs FF, lag, merge)")
    test_calendar_mapping()
    print("PASS workday calendar (weekend crossing, round-trip, anchors)")
    test_cycles_refused_loudly()
    print("PASS cycles refused loudly, loop named by title")
    test_dirty_data_tolerated()
    print("PASS dirty data: junk dates, dangling preds, weekend items")
    test_start_no_earlier_than()
    print("PASS entered start = start-no-earlier-than")
    test_lag_parsing_and_determinism()
    print("PASS lag parsing, determinism, negative-lag clamp")
    print("CPM TESTS PASSED")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print("CPM TEST FAILED:", e)
        sys.exit(1)
