"""Headless GUI construction test for the Planloom workspace: builds the full
app (nav + all seven sections), switches between them, exercises the project
store through the UI layer, the viewer, markup regression paths, routing, and
theme round-trip — under a virtual display.

Run:  xvfb-run -a python3 tests/test_gui_construct.py     (Linux)
      python tests\\test_gui_construct.py                  (Windows/mac)
"""
import os
import sys
import tempfile

os.environ["PLOOM_NO_FIRST_RUN"] = "1"   # the Ropes offer: tested explicitly

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz                                            # noqa: E402


def res2_refused(hw):
    """Off-trade questions must be refused honestly (trades-only physics)."""
    r = hw.ask("best pizza dough recipe")
    return r["refused"]


def make_pdf(path, pages=2):
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page(width=612, height=792)
        page.draw_rect(fitz.Rect(40, 40, 572, 752))
        page.insert_text((60, 70), f"GUI TEST PAGE {i + 1}", fontsize=18)
    doc.save(path)
    doc.close()


def check_dnd(root):
    """The from-scratch drag-drop seam: router registration, hover synthesis,
    smallest-target routing, ext filtering, root fallback, deferred delivery —
    fed with synthetic backend events (a real OS drag cannot be synthesized
    under xvfb; the OS half is the ctypes OLE backend, Windows-smoke-tested)."""
    import time
    import tkinter as tk

    from rfi_stamper.gui import dnd

    def pump(w, ms=50):
        w.update()
        time.sleep(ms / 1000.0)
        w.update()

    win = tk.Toplevel(root)
    win.geometry("420x300+60+60")
    a = tk.Frame(win, width=120, height=60)
    a.place(x=10, y=10)
    b = tk.Frame(win, width=120, height=60)
    b.place(x=220, y=10)
    win.update_idletasks()

    got = {"a": None, "b": None, "root": None,
           "a_hover": 0, "a_leave": 0, "win_enter": 0, "win_leave": 0}
    ra = dnd.enable_drop(a, lambda p: got.__setitem__("a", p), exts=(".pdf",),
                         on_enter=lambda: got.__setitem__("a_hover", got["a_hover"] + 1),
                         on_leave=lambda: got.__setitem__("a_leave", got["a_leave"] + 1))
    dnd.enable_drop(b, lambda p: got.__setitem__("b", p))
    dnd.enable_drop(win, lambda p: got.__setitem__("root", p),
                    on_enter=lambda: got.__setitem__("win_enter", got["win_enter"] + 1),
                    on_leave=lambda: got.__setitem__("win_leave", got["win_leave"] + 1))
    # no OS backend under xvfb: registration works, activation is honestly off
    assert ra is dnd.HAS_DND and dnd.HAS_DND is False

    router = dnd._router_for(win)
    ax = a.winfo_rootx() + 30
    ay = a.winfo_rooty() + 20
    bx = b.winfo_rootx() + 30
    by = b.winfo_rooty() + 20

    router.drag_enter()
    assert got["win_enter"] == 1, "window-level enter (the overlay hook) fired"
    router.drag_move(ax, ay)
    assert got["a_hover"] == 1, "hover enter synthesized for the target"
    router.drag_move(bx, by)
    assert got["a_leave"] == 1, "hover leave synthesized when the cursor moves on"
    assert router.drop(ax, ay, ["x.pdf", "y.txt", "z.PDF"]) is True
    pump(win)
    assert got["a"] == ["x.pdf", "z.PDF"], f"ext-filtered, case-blind: {got['a']}"
    assert got["b"] is None, "the other target got nothing"
    assert got["win_leave"] >= 1, "drop hides the overlay (leave fires first)"

    # a drop outside every child target falls back to the toplevel handler
    router.drag_enter()
    assert router.drop(win.winfo_rootx() + 300, win.winfo_rooty() + 250,
                       ["q.pdf"]) is True
    pump(win)
    assert got["root"] == ["q.pdf"], f"root fallback routed: {got['root']}"

    # a drop with nothing passing the filter is honestly refused
    router.drag_enter()
    assert router.drop(ax, ay, ["nope.txt"]) is False

    # brace-quoted Tcl lists (paths with spaces) survive parsing
    parsed = dnd.parse_drop_paths(win, "{C:/a b/c.pdf} /tmp/d.pdf {e f.txt}",
                                  exts=(".pdf",))
    assert parsed == ["C:/a b/c.pdf", "/tmp/d.pdf"], parsed

    # the native backend module imports everywhere and is honest off-Windows
    from rfi_stamper.gui import dnd_win32
    assert dnd_win32.HAS_NATIVE == (sys.platform == "win32")
    if sys.platform != "win32":
        assert dnd_win32.attach(win, router) is False

    # lifecycle: destroying a registered widget prunes its router entry
    n_before = len(router.targets)
    b.destroy()
    pump(win)
    assert len(router.targets) == n_before - 1, "destroyed target pruned"
    assert all(t["widget"] is not b for t in router.targets)
    # a second toplevel gets its OWN router with no backend — enable_drop on
    # it must report False (its window frame has no native registration)
    win2 = tk.Toplevel(root)
    assert dnd.enable_drop(win2, lambda p: None) is False
    assert dnd._router_for(win2) is not router, "routers are per-toplevel"
    n_routers = len(dnd._routers)
    win2.destroy()
    pump(win)
    assert len(dnd._routers) == n_routers - 1, "destroyed toplevel's router pruned"

    win.destroy()
    print("  dnd: router routing/hover/filter/fallback/lifecycle + honest no-backend, ok")


def main():
    from rfi_stamper.gui import dnd, fx
    from rfi_stamper.gui.app import SECTION_ORDER, App

    fx.set_quality("off")          # deterministic: animations jump to end

    tmp = tempfile.mkdtemp(prefix="ploom_gui_")
    pdf = os.path.join(tmp, "t.pdf")
    make_pdf(pdf)

    root = dnd.make_root()
    root.geometry("1400x900")
    app = App(root)
    root.update_idletasks()
    root.update()

    # all seven sections constructed and reachable through the nav
    assert set(app.sections) == set(SECTION_ORDER)
    for key in SECTION_ORDER:
        app.goto(key)
        root.update()
        assert app._current == key, key
    app.goto("home")
    root.update()

    # theme round-trip (start theme comes from user prefs)
    start = app.theme.name
    app.toggle_dark()
    root.update()
    assert app.theme.name != start
    app.toggle_dark()
    root.update()
    assert app.theme.name == start

    # command palette opens, filters, closes
    app.palette.open()
    root.update()
    app.palette.var.set("dark")
    root.update()
    assert app.palette.listbox.size() > 0
    app.palette.close()

    # project lifecycle through the app layer
    ppath = os.path.join(tmp, "job.ploom.json")
    app._load_project(ppath, create=True)
    root.update()
    assert app.project is not None and os.path.exists(ppath)
    from rfi_stamper.project import PunchItem, Task
    app.project.add("tasks", Task.new(title="hang duct", status="todo",
                                      due="2020-01-01"))
    app.project.add("punch", PunchItem.new(title="patch wall"))
    app.field.refresh()
    root.update()
    assert len(app.field.tasks.tree.get_children()) == 1
    assert len(app.field.punch.tree.get_children()) == 1

    # the Tautline: CPM runs once per refresh and paints the critical
    # chain red with hollow float tails on the Gantt
    from rfi_stamper.project import ScheduleItem
    app.project.add("schedule", ScheduleItem.new(
        id="mob", title="mobilize", start="2026-01-05", end="2026-01-08"))
    app.project.add("schedule", ScheduleItem.new(
        id="ri", title="rough-in", start="2026-01-05", end="2026-01-09",
        depends=["mob"]))
    app.project.add("schedule", ScheduleItem.new(
        id="pw", title="punch walk", start="2026-01-05", end="2026-01-06"))
    app.field.gantt.refresh(animate=False)
    root.update()
    cpm_res = app.field.gantt._cpm
    assert cpm_res is not None and not cpm_res.cycle
    assert cpm_res.by_id["mob"]["critical"] and cpm_res.by_id["ri"]["critical"]
    assert cpm_res.by_id["pw"]["tf"] > 0
    gcv = app.field.gantt.canvas
    assert gcv.find_withtag("critbar"), "critical bars missing"
    assert gcv.find_withtag("floatbar"), "float tail missing"

    app.truth.refresh()
    root.update()

    # plan viewing: open, flip, zoom, markup store attached
    app.goto("plans")
    root.update()
    mk_tab = app.plans.markup
    mk_tab.open_pdf(pdf)
    root.update()
    assert mk_tab.viewer.page_count == 2
    mk_tab.viewer.next_page()
    root.update()
    assert mk_tab.viewer.page_no == 2
    assert mk_tab.store is not None

    # markup regression paths kept from the previous architecture
    from rfi_stamper import markups as mk2
    mk_tab.push_undo()
    mk_tab.store.add(mk2.Markup.new(1, "rect", [(100, 100), (200, 160)],
                                    subject="gui-test"))
    mk_tab.after_change()
    root.update()
    assert len(mk_tab.mtree.get_children()) == 1
    mk_tab.undo()
    root.update()
    assert len(mk_tab.mtree.get_children()) == 0
    mk_tab.set_tool("measure_polylength")
    mk_tab._pts = [(50.0, 50.0), (120.0, 80.0)]
    mk_tab._draw_poly_preview()
    root.update()
    assert mk_tab.viewer.canvas.find_withtag("preview")
    mk_tab.viewer.goto(1)
    root.update()
    assert mk_tab._pts == []
    mk_tab.set_tool("count")
    mk_tab.on_escape()
    assert mk_tab.tool == "select"

    # per-page scale memory isolation
    import shutil
    pdf2 = os.path.join(tmp, "scale_iso.pdf")
    shutil.copy(pdf, pdf2)
    mk_tab.open_pdf(pdf2)
    root.update()
    mk_tab.viewer.goto(2)
    mk_tab.scale_all_pages.set(False)
    mk_tab._use_scale('1/4" = 1\'-0"', (1 / 0.25) / 72.0, "ft-in")
    assert mk_tab.cal_for(2) is not None and mk_tab.cal_for(1) is None

    # the Story Pole: witnessed autoscale calibrates PASS sheets only
    from rfi_stamper import draft as _dr
    _sm = _dr.DraftModel()
    _sw = _sm.add("wall", [(0, 0), (40, 0)], wtype="stud4")
    _sm.add("wall", [(0, 0), (0, 30)], wtype="stud4")
    for pts in ([(0, 0), (40, 0), (20, -4)], [(0, 0), (0, 30), (-4, 15)],
                [(0, 0), (12, 0), (6, -8)], [(12, 0), (40, 0), (26, -8)],
                [(0, 30), (40, 30), (20, 34)]):
        _sm.add("dim", pts)
    _sm.add("door", [], host=_sw.id, t=0.3, width_in=36.0)
    _plate = os.path.join(tmp, "storypole_plate.pdf")
    _dr.plate_pdf(_sm, _plate)
    mk_tab.open_pdf(_plate)
    root.update()
    mk_tab.story_pole_dialog()
    import time as _tsp
    import tkinter as _tksp
    from tkinter import ttk as _ttksp
    _dlg = None
    for _ in range(200):
        root.update()
        _dlg = next((w for w in mk_tab.winfo_children()
                     if isinstance(w, _tksp.Toplevel)), None)
        if _dlg is not None:
            break
        _tsp.sleep(0.05)
    assert _dlg is not None, "Story Pole verdict dialog should appear"
    _apply = [w for w in _dlg.winfo_children()[0].winfo_children()[-1]
              .winfo_children() if isinstance(w, _ttksp.Button)][0]
    assert "1 PASS" in _apply.cget("text"), _apply.cget("text")
    _apply.invoke()
    root.update()
    assert mk_tab.cal_for(1) is not None, "PASS sheet calibrated"
    assert abs(mk_tab.cal_for(1).real_per_pt * 9.0 - 1.0) < 0.01, \
        "1/8\" plate -> 9 pt/ft"

    # the Reed Count: fixture counting on the calibrated sheet
    _sm.add("fixture", [(6, 5)], stencil="wc")
    _sm.add("fixture", [(12, 5)], stencil="lav")
    _plate2 = os.path.join(tmp, "reedcount_plate.pdf")
    _dr.plate_pdf(_sm, _plate2)
    mk_tab.open_pdf(_plate2)
    root.update()
    mk_tab.story_pole_dialog()
    _dlg = None
    for _ in range(200):
        root.update()
        _dlg = next((w for w in mk_tab.winfo_children()
                     if isinstance(w, _tksp.Toplevel)), None)
        if _dlg is not None:
            break
        _tsp.sleep(0.05)
    [w for w in _dlg.winfo_children()[0].winfo_children()[-1]
     .winfo_children() if isinstance(w, _ttksp.Button)][0].invoke()
    root.update()
    mk_tab.reed_count_dialog()
    _dlg2 = None
    for _ in range(200):
        root.update()
        _dlg2 = next((w for w in mk_tab.winfo_children()
                      if isinstance(w, _tksp.Toplevel)), None)
        if _dlg2 is not None:
            break
        _tsp.sleep(0.05)
    assert _dlg2 is not None, "Reed Count dialog should appear"

    # counts tree is the first Treeview in the dialog
    def _find_trees(w, acc):
        for c in w.winfo_children():
            if isinstance(c, _ttksp.Treeview):
                acc.append(c)
            _find_trees(c, acc)
        return acc
    _trees = _find_trees(_dlg2, [])
    assert _trees, "counts tree present"
    _rows = {_trees[0].item(i)["values"][0]: _trees[0].item(i)["values"][2]
             for i in _trees[0].get_children()}
    assert _rows.get("wc") == 1 and _rows.get("lav") == 1, _rows
    _dlg2.destroy()
    root.update()

    # home routing: one PDF -> plan viewing; several -> combine list
    before = len(app.projsec.merge.items)
    app.route_paths([pdf, pdf])
    root.update()
    assert len(app.projsec.merge.items) == before + 2
    app.route_paths([pdf])
    root.update()
    assert app.plans.markup.viewer.path == pdf and app._current == "plans"

    # overlay hint reflects the active section
    app.goto("project")
    root.update()
    assert "plan set" in app._drop_hint().lower()

    # stamp guards intact + resolution hook wired
    st = app.projsec.stamp
    assert st.scanned_plan is None and st._running is False
    assert st.get_statuses is not None

    # BIM viewer: demo model renders segments
    app.goto("plans")
    app.plans.nb.select(app.plans.bim)
    root.update()
    import rfi_stamper.bim as bim
    app.plans.bim.set_model(bim.demo_building())
    root.update()
    assert len(app.plans.bim.canvas.find_all()) > 50

    # ground truth renders and computes without a scan
    app.goto("truth")
    root.update()
    app.truth.refresh()
    root.update()

    # ---- Fieldstitch studio: place, number, layer, world coords, export
    app.goto("plans")
    fst = app.plans.fieldstitch
    app.plans.nb.select(fst)
    root.update()
    fst.open_pdf(pdf)
    root.update()
    fst.prefix_var.set("CP-")
    fst.num_var.set("7")

    class _Ev:
        x, y, state = 120, 140, 0
    fst.set_tool("place")
    fst.on_press(_Ev())
    root.update()
    assert len(fst.job.points) == 1
    p0 = fst.job.points[0]
    assert fst.job.composed(p0) == "CP-007", fst.job.composed(p0)
    assert fst.job.next_num == 8
    # world coordinates after scale + basepoint
    fst.set_scale('1/8" = 1\'-0"', 8.0 / 72.0, "ft")
    fst.job.base_page_xy = (p0.x, p0.y)
    fst.job.base_world = (5000.0, 2000.0)
    n, e, _z = fst.job.to_world(p0)
    assert abs(n - 5000.0) < 1e-6 and abs(e - 2000.0) < 1e-6
    # strata: toggling visibility hides the marker
    fst.redraw_points()
    root.update()
    assert fst.viewer.canvas.find_withtag("pt")
    fst.job.layers[0].visible = False
    fst.redraw_points()
    root.update()
    assert not fst.viewer.canvas.find_withtag("pt")
    fst.job.layers[0].visible = True
    # export a full spool kit and check the files exist
    import rfi_stamper.fieldstitch as fs
    kitdir = os.path.join(tmp, "kit")
    os.makedirs(kitdir, exist_ok=True)
    res = fs.export_kit(fst.job, kitdir, "fullspool")
    assert res["points"] == 1
    exts = sorted(os.path.splitext(f)[1] for f in res["files"])
    assert exts == [".csv", ".dxf", ".json", ".xlsx"], exts

    # ---- Fieldstitch Pro (A1): status glyphs, witness, walk order, QA loop
    import rfi_stamper.fieldpro as fp
    # a bad prefix must land in the status bar, never a traceback
    fst.prefix_var.set("BAD PREFIX WAY TOO LONG!!")
    fst.set_tool("place")
    fst.on_press(_Ev())
    root.update()
    assert len(fst.job.points) == 1, "invalid label must not place a point"
    fst.prefix_var.set("CP-")
    # witness point rides its parent and draws a tether
    fst.selection = p0.id
    import tkinter.simpledialog as _sd
    _ask0 = _sd.askstring
    _sd.askstring = lambda *a, **k: "2, 0"
    try:
        fst.add_witness_sel()
    finally:
        _sd.askstring = _ask0
    root.update()
    wits = [p for p in fst.job.points if p.is_witness]
    assert len(wits) == 1 and wits[0].parent_uid == p0.uid
    n0, e0, _ = fst.job.to_world(p0)
    nw, ew, _ = fst.job.to_world(wits[0])
    assert abs((nw - n0) - 2.0) < 1e-6 and abs(ew - e0) < 1e-6
    # status lifecycle drives the table chip and pin shape
    fst.job.set_status(p0, "STAKED", by="QA")
    fst.fill_table()
    fst.redraw_points()
    root.update()
    assert fst.ptree.set(p0.id, "st") == "S"
    fst.job.set_status(p0, "VERIFIED")
    assert fst.job.seed_statuses({p0.num: "PENDING"}) == 0, \
        "seeding must never downgrade"
    # walk order proposes an order without touching numbers
    for i in range(3):
        fst.on_press(_Ev())
    root.update()
    nums_before = [p.num for p in fst.job.points]
    fst.toggle_walk_order()
    root.update()
    assert fst._route is not None
    assert [p.num for p in fst.job.points] == nums_before
    fst.toggle_walk_order()
    # as-staked QA loop through the engine the dialogs call
    qa = fp.QAStore(fst.job.pdf_path)
    design_n, design_e, _z0 = fst.job.to_world(p0)
    shots = os.path.join(tmp, "shots.csv")
    with open(shots, "w", newline="") as fh:
        fh.write(f"{p0.num},{design_n + 0.004:.4f},"
                 f"{design_e - 0.003:.4f},0.00,STK\r\n")
    paired = fp.pair_asstaked(fst.job, shots)
    assert paired["count"] == 1 and paired["rows"][0]["via"] == "id"
    out = fp.commit_asstaked(fst.job, qa, paired["rows"],
                             session_id="S1", staked_by="QA")
    assert out["committed"] == 1 and out["passed"] == 1, out
    ledger = os.path.join(tmp, "ledger.pdf")
    lres = fp.ledger_pdf(fst.job, qa, ledger, foot="international foot")
    assert os.path.exists(ledger) and lres["rows"] >= 1

    # ---- 3D: pins land in the viewer and Horizon Slice culls geometry
    fst.push_pins()
    root.update()
    assert app.plans.bim.pins, "pins should reach the BIM viewer"
    import rfi_stamper.bim as bim2
    app.plans.bim.set_model(bim2.demo_building())
    root.update()
    full_items = len(app.plans.bim.canvas.find_all())
    app.plans.bim.slice_var.set(30.0)
    app.plans.bim._on_slice()
    root.update()
    sliced_items = len(app.plans.bim.canvas.find_all())
    assert sliced_items < full_items, (sliced_items, full_items)
    assert app.plans.bim.canvas.find_withtag("cut"), "cut plane drawn"
    app.plans.bim.slice_var.set(100.0)
    app.plans.bim._on_slice()

    # ---- Daybook: entry through the store the panel binds to
    app.goto("field")
    root.update()
    db = app.field.daybook
    store = db._ensure_store()
    assert store is not None, "project is open, store should bind"
    store.add(date="2026-07-08", crew="crew A", weather="clear",
              summary="hung duct mains", measurements=["riser 9'-2\""],
              photos=[])
    db.refresh()
    root.update()
    assert len(db.tree.get_children()) == 1
    assert "1 entr" in db.counts_lbl.cget("text")

    # ---- Reckoner: takeoff from a synthetic marked-up PDF
    import json as _json

    from rfi_stamper import markups as mk3
    tk_pdf = os.path.join(tmp, "takeoff.pdf")
    make_pdf(tk_pdf)
    st2 = mk3.MarkupStore(tk_pdf)
    for i in range(3):
        st2.add(mk3.Markup.new(1, "count", [(50 + i * 20, 60)],
                               text=f"S-{i}", subject="Sprinkler Head"))
    st2.add(mk3.Markup.new(1, "measure_length", [(0, 0), (100, 0)],
                           subject="Pipe Run"))
    st2.save()
    with open(tk_pdf + ".scale.json", "w", encoding="utf-8") as f:
        _json.dump({"version": 2, "pages": {},
                    "default": {"real_per_pt": 0.1, "unit": "ft"}}, f)
    book = os.path.join(tmp, "prices.csv")
    with open(book, "w", encoding="utf-8") as f:
        f.write("code,description,unit,cost\nSPK,Sprinkler Head,ea,45.50\n"
                "PIPE,Pipe Run,ft,12.00\n")
    app.goto("project")
    rp = app.projsec.reckoner
    app.projsec.nb.select(rp)
    root.update()
    rp.pdf_var.set(tk_pdf)
    rp.book_var.set(book)
    rp.run_takeoff()
    for _ in range(100):
        root.update()
        if rp.lines:
            break
        import time as _t
        _t.sleep(0.05)
    assert rp.lines, "takeoff produced lines"
    by_subj = {ln.subject: ln for ln in rp.lines}
    assert by_subj["Sprinkler Head"].qty == 3
    assert abs(by_subj["Pipe Run"].qty - 10.0) < 1e-6      # 100pt * 0.1ft
    assert abs(by_subj["Sprinkler Head"].total - 136.5) < 1e-6

    # ---- Extrude: the Fieldstitch plan becomes a 3D model in world coords
    from rfi_stamper import extrude
    model, stats = extrude.model_from_plan(pdf, page_no=1, job=fst.job,
                                           wall_height=10.0, floors=2)
    assert stats["walls"] > 0 and len(model.segments) > 0
    (_mnx, _mny, mnz), (_mxx, _mxy, mxz) = model.bounds()
    assert mxz - mnz >= 20.0, "two floors of 10 should stack"
    app.plans.bim.set_model(model)
    root.update()
    assert len(app.plans.bim.canvas.find_all()) > 10

    # ---- regressions from the adversarial GUI review -------------------
    import tkinter as tk
    from tkinter import messagebox as _mb

    # theme toggle restyles live widgets: crud status chips + insight tags
    app.toggle_dark()
    root.update()
    _c = app.theme.colors
    assert str(app.truth.feed.tag_cget("rule", "foreground")) == _c["muted"], \
        "insight 'rule' tag must follow the theme"
    _chips = app.field.tasks.chips.winfo_children()
    assert _chips and all(str(w.cget("bg")) == _c["panel"] for w in _chips), \
        "status chips must follow the theme"
    app.toggle_dark()
    root.update()

    # Return inside a multiline Text edits text, it must not save-close
    panel = app.field.tasks
    panel._editor(None)
    root.update()
    dlg = [w for w in panel.winfo_children()
           if isinstance(w, tk.Toplevel)][-1]
    txt = [w for w in dlg.winfo_children()[0].winfo_children()
           if isinstance(w, tk.Text)][0]
    txt.focus_force()
    root.update()
    txt.event_generate("<Return>")
    root.update()
    assert dlg.winfo_exists(), "Return in a Text field must not submit"
    dlg.destroy()
    root.update()

    # a scan that finds no RFIs must not pop the resolution 'sync' modal
    st = app.projsec.stamp
    _orig_info = _mb.showinfo

    def _no_modal(*a, **k):
        raise AssertionError(f"unexpected modal: {a}")
    _mb.showinfo = _no_modal
    try:
        st.rows, st.index, st.scanned_plan = [], None, pdf
        st.on_scanned(pdf)
    finally:
        _mb.showinfo = _orig_info
    root.update()

    # resolution board drag: outside release cancels, inside release moves
    class _Rec:
        def __init__(self, n):
            self.number, self.title, self.has_answer = n, "t", False

    class _Row:
        def __init__(self, n):
            self.record, self.pages, self.via = _Rec(n), [1], "planref"

    st.rows = [_Row("001"), _Row("002")]
    st.scanned_plan = pdf
    app.goto("project")
    root.update()
    board = app.projsec.board
    app.projsec.nb.select(board)
    root.update()
    board.sync()
    root.update()
    rstore = board._ensure_store()
    assert rstore.statuses()["001"] == "open"
    bbox = board.canvas.bbox("card_001")

    class _Ev:
        pass
    press = _Ev()
    press.x, press.y = (bbox[0] + bbox[2]) // 2, (bbox[1] + bbox[3]) // 2
    board._press(press)
    outside = _Ev()
    outside.x, outside.y = int(board._colw * 4.5), -25
    board._release(outside)
    assert rstore.statuses()["001"] == "open", \
        "release outside the board must cancel the drag"
    board._press(press)
    inside = _Ev()
    inside.x, inside.y = int(board._colw * 1.5), 60
    board._release(inside)
    assert rstore.statuses()["001"] == "answered", \
        "release in the ANSWERED column must advance the card"

    # daybook store rebinds when the project changes
    p2 = os.path.join(tmp, "job2.ploom.json")
    app._load_project(p2, create=True)
    root.update()
    assert app.field.daybook._ensure_store().base_path == p2, \
        "daybook store must follow the open project"

    # ---- regressions: fieldstitch / bim3d / pano review pass ------------
    import time as _t

    class _EvAt:
        def __init__(self, x, y):
            self.x, self.y, self.state = int(x), int(y), 0

    def _ev_at_page(px, py):
        """Event whose canvas coords land on page point (px, py) — the
        canvas may be scrolled after a zoom, so raw coords won't do."""
        cv = fst.viewer.canvas
        return _EvAt(px * fst.viewer.scale - cv.canvasx(0),
                     py * fst.viewer.scale - cv.canvasy(0))

    app.goto("plans")
    app.plans.nb.select(fst)
    _t.sleep(0.06)
    root.update()                      # flush the viewer's deferred fit
    fst.viewer.set_zoom(1.0)
    root.update()

    # canvas click on a point the table filter hides must not raise
    fst.set_tool("select")
    fst.filter_var.set("zzz-no-match")
    root.update()
    fst.on_press(_ev_at_page(p0.x, p0.y))
    assert fst.selection == p0.id
    fst.filter_var.set("")
    root.update()

    # hit radius is 9 SCREEN px at any zoom (not 9 page pts)
    fst.viewer.set_zoom(4.0)
    root.update()
    assert fst._hit(p0.x + 8, p0.y) is None      # 32 screen px away: miss
    assert fst._hit(p0.x + 2, p0.y) == p0.id     # 8 screen px away: hit
    fst.viewer.set_zoom(0.2)
    root.update()
    assert fst._hit(p0.x + 10, p0.y) == p0.id    # 2 screen px away: hit
    fst.viewer.set_zoom(1.0)
    root.update()

    # hidden layers are click-through; locked layers select but never move
    ly0 = fst.job.layers[0]
    ly0.visible = False
    assert fst._hit(p0.x, p0.y) is None
    ly0.visible = True
    ly0.locked = True
    fst.on_press(_ev_at_page(p0.x, p0.y))
    assert fst.selection == p0.id
    _ox = p0.x
    fst.on_drag(_ev_at_page(p0.x + 40, p0.y))
    fst.on_release(_EvAt(0, 0))
    assert p0.x == _ox, "locked layer point moved"
    fst.delete_sel()
    assert fst.job.get(p0.id) is not None, "locked layer point deleted"
    ly0.locked = False

    # typing prefix / next # updates the job without any click
    fst.prefix_var.set("ZZ-")
    fst.num_var.set("55")
    assert fst.job.prefix == "ZZ-" and fst.job.next_num == 55

    # blank grid sheet: a previous layout is offered, never silently
    # adopted or clobbered (HOME redirected: real ~/.planloom untouched)
    _envs = {k: os.environ.get(k) for k in ("HOME", "USERPROFILE")}
    os.environ["HOME"] = os.environ["USERPROFILE"] = tmp
    try:
        fst.blank_sheet()
        root.update()
        first_sheet = fst.viewer.path
        fst.set_tool("place")
        fst.on_press(_EvAt(80, 80))
        root.update()
        assert len(fst.job.points) == 1
        _ask0 = _mb.askyesno
        _mb.askyesno = lambda *a, **k: False     # decline: start fresh
        try:
            fst.blank_sheet()
            root.update()
        finally:
            _mb.askyesno = _ask0
        assert fst.viewer.path != first_sheet
        assert not fst.job.points, "previous blank-grid layout leaked in"
        assert os.path.exists(first_sheet + ".stitch.json"), \
            "previous blank-grid layout was clobbered"
    finally:
        for k, v in _envs.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    # bim3d: a user drag cancels the fly-in instead of fighting it
    fx.set_quality("full")
    bv = app.plans.bim
    app.plans.nb.select(bv)
    root.update()
    import rfi_stamper.bim as bim3
    bv.set_model(bim3.demo_building())           # starts the fly-in tween
    root.update()
    bv._on_press(_EvAt(300, 300))
    bv._on_drag(_EvAt(400, 300))
    yaw_hold = bv.cam.yaw
    t_end = _t.time() + 0.3
    while _t.time() < t_end:
        root.update()
        _t.sleep(0.02)
    assert bv.cam.yaw == yaw_hold, "fly-in fought the user's drag"
    bv._on_release(_EvAt(400, 300))
    fx.set_quality("off")

    # bim3d: the Draw-In (Open IFC) — engine wired in, coverage surfaced
    ifc_fix = os.path.join(tmp, "wall.ifc")
    with open(ifc_fix, "w", encoding="latin-1") as fh:
        fh.write(
            "ISO-10303-21;\nHEADER;\nFILE_SCHEMA(('IFC4'));\nENDSEC;\nDATA;\n"
            "#1=IFCPROJECT('0000000000000000000001',$,'P',$,$,$,$,(#20),#7);\n"
            "#7=IFCUNITASSIGNMENT((#8));\n"
            "#8=IFCSIUNIT(*,.LENGTHUNIT.,.MILLI.,.METRE.);\n"
            "#20=IFCGEOMETRICREPRESENTATIONCONTEXT($,'Model',3,1.0E-005,#21,$);\n"
            "#21=IFCAXIS2PLACEMENT3D(#22,$,$);\n"
            "#22=IFCCARTESIANPOINT((0.,0.,0.));\n"
            "#30=IFCLOCALPLACEMENT($,#21);\n"
            "#40=IFCRECTANGLEPROFILEDEF(.AREA.,$,#41,4000.,200.);\n"
            "#41=IFCAXIS2PLACEMENT2D(#42,$);\n"
            "#42=IFCCARTESIANPOINT((0.,0.));\n"
            "#43=IFCDIRECTION((0.,0.,1.));\n"
            "#45=IFCEXTRUDEDAREASOLID(#40,$,#43,3000.);\n"
            "#50=IFCSHAPEREPRESENTATION(#20,'Body','SweptSolid',(#45));\n"
            "#51=IFCPRODUCTDEFINITIONSHAPE($,$,(#50));\n"
            "#60=IFCWALL('0000000000000000000002',$,'W1',$,$,#30,#51,$,$);\n"
            "ENDSEC;\nEND-ISO-10303-21;\n")
    infos = []
    _info1 = _mb.showinfo
    _mb.showinfo = lambda *a, **k: infos.append(a)
    try:
        bv.load_ifc(ifc_fix)
        root.update()
    finally:
        _mb.showinfo = _info1
    assert bv.model.faces and ("walls", "#9aab9e") in bv.model.systems, \
        "Draw-In wall did not reach the viewer"
    assert infos and "Imported 1 wall(s)" in infos[0][1], \
        "coverage report not surfaced"

    def _has_ifc_btn(w):
        for c in w.winfo_children():
            try:
                if str(c.cget("text")) == "Open IFC…":
                    return True
            except tk.TclError:
                pass
            if _has_ifc_btn(c):
                return True
        return False
    assert _has_ifc_btn(bv), "Open IFC… button missing from the viewer bar"

    # the Swatchbook: cut-sheet submittal packets from the project section.
    # A synthetic library (real hashes) drives resolve -> gap -> build -> log
    # without the shipped seed kit.
    import json as _json

    import rfi_stamper.swatchbook as _sb
    swroot = os.path.join(tmp, "swlib")
    os.makedirs(os.path.join(swroot, "seed_library"), exist_ok=True)
    _swd = fitz.open()
    _swd.new_page(width=612, height=792).insert_text((60, 90), "SW SHEET")
    swpdf = os.path.join(swroot, "seed_library", "sw_alpha.pdf")
    _swd.save(swpdf)
    _swd.close()
    with open(os.path.join(swroot, "manifest.json"), "w") as _fh:
        _json.dump({"components": [{
            "id": "sw_alpha", "manufacturer": "MakerX",
            "aliases": ["SWX-1"], "file": "seed_library/sw_alpha.pdf",
            "pages": 1, "sha256": _sb._sha256(swpdf), "source_url": "",
            "fetched": "", "notes": "", "source": "seed"}]}, _fh)
    swp = app.projsec.swatchbook
    app.projsec.nb.select(swp)
    root.update()
    swp._lib_root = swroot
    swp.callout_var.set("swx1")
    swp._resolve_live()
    assert "sw_alpha" in str(swp.match.cget("text")), "live resolve label"
    swp.callout_var.set("nope-99")
    swp._resolve_live()
    assert "GAP" in str(swp.match.cget("text")), "gap shows loud"
    swp.add_fixture("HB-1", 21, ["SWX-1", "ghost part"])
    root.update()
    assert len(swp.tree.get_children()) == 1, "fixture listed"
    swout = os.path.join(tmp, "swout")
    res = swp.build_to(swout)
    assert dict(res["built"]) == {"21-HB-1.pdf": 1}, res["built"]
    assert res["gapped"]["21-HB-1.pdf"], "gap recorded in the build"
    assert os.path.exists(res["log_path"]), "00-BUILD-LOG.md written"
    _chk = fitz.open(os.path.join(swout, "21-HB-1.pdf"))
    assert "HB-1" in _chk[0].get_text(), "packet page stamped"
    _chk.close()
    swp.load_reference()
    root.update()
    assert len(swp.tree.get_children()) == 19, "reference project loads"

    # the Cut Ticket: tag a fixture in the Loft -> save -> the pull list
    # lands in the project store -> the Swatchbook auto-feeds it as a
    # proposal -> nothing builds without the explicit action
    loft = app.plans.loft
    loft._select_tool("fixture")
    loft._set_stencil("hb")
    root.update()
    assert "ftag" in loft._opt_vars, "fixture tool grew the Tag entry"
    loft._opt_vars["ftag"].set("hb9")
    loft._click_fixture(20.0, 10.0, None)
    root.update()
    fent = next(e for e in loft.model.ents
                if e.kind == "fixture" and e.props.get("tag") == "hb9")
    loft.path = os.path.join(tmp, "ct.loft.json")
    loft.save()
    root.update()
    pull = {it.tag: it for it in app.project.pull_list}
    assert "HB-9" in pull and pull["HB-9"].count == 1, \
        f"save synced the Cut Ticket: {sorted(pull)}"
    assert pull["HB-9"].prefix == 21, "hb stencil guessed prefix 21"
    app.projsec.nb.select(app.projsec.stamp)   # leave, then re-enter: the
    root.update()                              # tab-change event auto-feeds
    app.projsec.nb.select(swp)
    root.update()
    rows = [swp.tree.item(i)["values"] for i in swp.tree.get_children()]
    assert any(r[0] == "21-HB-9.pdf" for r in rows), \
        f"model-sourced proposal appears: {[r[0] for r in rows][:5]}"
    assert not any(f == "21-HB-9.pdf"
                   for _d, _s, fs in os.walk(tmp) for f in fs), \
        "the Cut Ticket never builds a PDF on its own"
    # deleting the fixture tombstones the row on the next save — flagged,
    # never auto-deleted
    loft.model.remove([fent.id])
    loft.save()
    root.update()
    it = next(i for i in app.project.pull_list if i.tag == "HB-9")
    assert it.missing_from_model and it.count == 0, \
        "orphaned tag tombstoned, kept on the list"

    # the Cut Ticket set-scan: a legend sheet's schedule rows land on the
    # pull list as proposals with pre-filled callouts (loud provenance)
    _ssd = fitz.open()
    _ssp = _ssd.new_page(width=792, height=612)
    _ssp.insert_text((60, 100), "MARK", fontsize=9)
    _ssp.insert_text((130, 100), "DESCRIPTION", fontsize=9)
    _ssp.insert_text((420, 100), "MANUFACTURER", fontsize=9)
    _ssp.insert_text((560, 100), "MODEL", fontsize=9)
    _ssp.insert_text((60, 120), "MS-7", fontsize=8)
    _ssp.insert_text((130, 120), "MOP SINK, TERRAZZO", fontsize=8)
    _ssp.insert_text((420, 120), "MAKER Z", fontsize=8)
    _ssp.insert_text((560, 120), "TSB-300", fontsize=8)
    _sspdf = os.path.join(tmp, "scanset.pdf")
    _ssd.save(_sspdf)
    _ssd.close()
    _info_ss = _mb.showinfo
    _mb.showinfo = lambda *a, **k: None      # skipped-notes dialog: no modal
    import time as _tss
    try:
        swp.scan_plan_set(_sspdf)
        for _ in range(200):
            root.update()
            if any(i.tag == "MS-7" for i in app.project.pull_list):
                break
            _tss.sleep(0.05)
    finally:
        _mb.showinfo = _info_ss
    _ms7 = next(i for i in app.project.pull_list if i.tag == "MS-7")
    assert _ms7.origin == "set-scan" and _ms7.callouts == ["MAKER Z TSB-300"], \
        f"set-scan proposal with pre-filled callouts: {_ms7.callouts}"
    assert _ms7.prefix == -1, "schedule-only row never guesses a category"

    # pano: alpha images and PDF "photos" load; unreadable files never
    # orphan a Toplevel; a 2:1 image opens as a live panorama
    from rfi_stamper.gui import pano
    rgba = os.path.join(tmp, "rgba.png")
    pm = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 64, 32), True)   # alpha
    pm.clear_with(120)
    pm.save(rgba)
    assert pano.load_image_rgb(rgba).shape == (32, 64, 3)
    assert pano.load_image_rgb(pdf).shape[2] == 3        # PDF -> page 1
    tops0 = sum(isinstance(w, tk.Toplevel) for w in root.winfo_children())
    assert pano.open_lookout(root, app.theme,
                             os.path.join(tmp, "missing.jpg")) is None
    root.update()
    assert sum(isinstance(w, tk.Toplevel)
               for w in root.winfo_children()) == tops0, \
        "unreadable image leaked a Toplevel"
    lv = pano.open_lookout(root, app.theme, rgba)        # 64x32 = 2:1 pano
    root.update()
    assert lv is not None and lv.pano
    lv._render()
    root.update()
    assert lv.canvas.find_all(), "panorama did not render"
    lv.destroy()
    root.update()

    # ---- Crewpass ledger (temp path, never the real one)
    from rfi_stamper import crewpass
    led = crewpass.Ledger(os.path.join(tmp, "cp.json"))
    s1 = led.assign("field lead", "tablet-01", "field")
    led.transfer(s1.id, "tablet-02")
    assert led.active()[0].device == "tablet-02"
    rep_pdf = os.path.join(tmp, "cp.pdf")
    crewpass.report_pdf(led, rep_pdf)
    assert os.path.exists(rep_pdf)

    # ---- App Integrations: a dropped CSV imports directly (no re-prompt)
    csvp = os.path.join(tmp, "tasks_in.csv")
    with open(csvp, "w", encoding="utf-8") as f:
        f.write("title,assignee,status,due\n"
                "install hangers,crew B,todo,2026-08-01\n")
    before_tasks = len(app.project.items("tasks"))
    app.goto("integrations")
    root.update()
    app.integrations.handle_drop([csvp])
    for _ in range(60):
        root.update()
        if len(app.project.items("tasks")) > before_tasks:
            break
        import time as _t2
        _t2.sleep(0.05)
    assert len(app.project.items("tasks")) == before_tasks + 1, \
        "dropped CSV should import without a dialog"

    # CLI --help unswallowed (regression)
    import subprocess
    r = subprocess.run([sys.executable, "-m", "rfi_stamper", "--help"],
                       capture_output=True, text=True,
                       cwd=os.path.dirname(os.path.dirname(
                           os.path.abspath(__file__))))
    assert r.returncode == 0 and "merge" in r.stdout

    # ---- The Loft: draft with real tools, snap, undo, tally, bridges
    app.goto("plans")
    loft = app.plans.loft
    app.plans.nb.select(loft)
    root.update()
    import rfi_stamper.draft as draft_mod

    class _LEv:
        def __init__(self, x, y, state=0):
            self.x, self.y, self.state = int(x), int(y), state

    def click(tool_xy):
        ev = _LEv(*loft.to_screen(*tool_xy))
        loft._on_motion(ev)
        loft._on_press(ev)

    loft.ppf, loft.vx, loft.vy = 10.0, -5.0, 30.0     # known view transform
    loft._select_tool("wall")
    click((0.0, 0.0))
    click((20.0, 0.0))       # wall 1: 20' along x
    click((20.0, 12.0))      # wall 2 chains from the same point
    root.update()
    walls = [en for en in loft.model.ents if en.kind == "wall"]
    assert len(walls) == 2, [e.kind for e in loft.model.ents]
    loft._escape()           # end the wall chain

    # Plumbline: hovering near a wall corner snaps to the endpoint
    ev = _LEv(*loft.to_screen(0.05, -0.03))
    loft._on_motion(ev)
    assert loft._snap_hit is not None and loft._snap_hit.kind == "end", \
        loft._snap_hit

    # door hangs on the nearest wall with a sane param
    loft._select_tool("door")
    click((10.0, 0.2))
    doors = [en for en in loft.model.ents if en.kind == "door"]
    assert len(doors) == 1 and doors[0].props["host"] == walls[0].id
    assert 0.3 < float(doors[0].props["t"]) < 0.7, doors[0].props

    # fixture stencil places at the cursor
    loft._select_tool("fixture")
    loft._set_stencil("wc")
    click((5.0, 5.0))
    assert [en for en in loft.model.ents if en.kind == "fixture"]

    # grids: verticals number, horizontal letters; intersections labeled
    loft._select_tool("grid")
    for a, b in (((2.0, -2.0), (2.0, 14.0)), ((8.0, -2.0), (8.0, 14.0)),
                 ((-2.0, 6.0), (22.0, 6.0))):
        click(a)
        click(b)
    grids = [en for en in loft.model.ents if en.kind == "grid"]
    assert len(grids) == 3
    assert {g.props["label"] for g in grids} == {"1", "2", "A"}, \
        {g.props["label"] for g in grids}
    gpts = draft_mod.grid_points(loft.model)
    assert len(gpts) == 2, gpts

    # dimension via three clicks; render carries the feet-inches text
    loft._select_tool("dim")
    for p in ((0.0, 0.0), (20.0, 0.0), (10.0, -3.0)):
        click(p)
    assert [en for en in loft.model.ents if en.kind == "dim"]
    ops = draft_mod.render_ops(loft.model)
    assert any(op[0] == "text" and "20'-0" in str(op[3]) for op in ops), \
        "dim text should read 20'-0\""

    # tally: two walls = 32 LF
    st2 = loft.model.stats()
    assert abs(st2["wall_lf"] - 32.0) < 1e-6, st2

    # undo/redo through the model the GUI drives
    n0 = len(loft.model.ents)
    assert loft.model.undo() and loft.model.undo()
    assert len(loft.model.ents) == n0 - 2
    assert loft.model.redo() and loft.model.redo()
    assert len(loft.model.ents) == n0

    # window box-select grabs everything fully inside; Esc clears
    loft._select_tool("select")
    s0 = loft.to_screen(-4.0, -5.0)
    s1 = loft.to_screen(24.0, 16.0)
    loft._box_select(s0[0], s0[1], s1[0], s1[1])
    assert len(loft.sel) == n0, (len(loft.sel), n0)
    loft._traits_refresh()
    root.update()
    loft._escape()
    assert not loft.sel

    # binder: picking a stencil arms the fixture tool; ply toggle hides ops
    loft._fill_binder()
    if loft.binder.exists("st:lav"):
        loft.binder.selection_set("st:lav")
        root.update()
        assert loft.tool == "fixture" and loft._last_stencil == "lav"
    assert loft.model.ply("S-GRID") is not None
    n_ops_vis = len(draft_mod.render_ops(loft.model))
    loft.model.ply("S-GRID").visible = False
    assert len(draft_mod.render_ops(loft.model)) < n_ops_vis
    loft.model.ply("S-GRID").visible = True

    # save -> recents routing round trip (save clears dirty; no dialogs)
    lpath = os.path.join(tmp, "draft.loft.json")
    loft.model.save(lpath)
    assert os.path.exists(lpath) and not loft.model.dirty
    app.route_paths([lpath])
    root.update()
    assert loft.path == lpath
    assert app.prefs["recent"][0]["kind"] == "loft"

    # plate PDF is a real one-page PDF at the chosen sheet size
    plate = os.path.join(tmp, "plate.pdf")
    res = draft_mod.plate_pdf(loft.model, plate, sheet="ARCH D")
    from rfi_stamper.minipdf.io import Reader as PdfReader
    rd = PdfReader(plate)
    assert len(rd.pages) == 1
    assert abs(float(rd.pages[0].mediabox.width) - 36 * 72) < 1.0, res

    # draft extrudes into the BIM viewer through the section bridge;
    # clash-lite pins ride along and an empty send clears them
    m3 = draft_mod.to_bim(loft.model, wall_height=9.0, floors=2)
    app.plans._loft_to_3d(m3, [(1.0, 2.0, 3.0, "C1", "#c1121f")])
    root.update()
    assert app.plans.bim.model is m3
    assert app.plans.bim.pins == [(1.0, 2.0, 3.0, "C1", "#c1121f")]
    app.plans._loft_to_3d(m3)
    root.update()
    assert app.plans.bim.pins == []

    # the Slipsheet vector-diff button rides in the As-Built compare tab
    assert app.plans.asbuilt.compare.vdiff_btn.winfo_exists()

    # ---- Pipewright: draw a run by tool, slope it, cap it, check it
    import rfi_stamper.pipewright as pw
    app.plans.nb.select(loft)
    root.update()
    loft._select_tool("pipe")
    loft._opt_vars["psys"].set("san")
    loft._opt_vars["pdia"].set("4")
    for p in ((30.0, 0.0), (50.0, 0.0), (50.0, 8.0)):
        click(p)
    loft._finish_poly()
    root.update()
    pipes = [en for en in loft.model.ents if en.kind == "pipe"]
    assert len(pipes) == 1 and pipes[0].props["system"] == "san"
    assert abs(float(pipes[0].props["dia_in"]) - 4.0) < 1e-9
    res = pw.slope_run(loft.model, pipes[0].id, 0.125,
                       start_invert_ft=100.0)
    assert res["total_fall"] == "0'-3 1/2\"", res["total_fall"]  # 28 LF
    ops_p = draft_mod.render_ops(loft.model)
    assert any(op[0] == "text" and str(op[3]) == '4"' for op in ops_p), \
        "pipe size label missing"
    assert any(op[0] == "text" and str(op[3]).startswith("IE ")
               for op in ops_p), "invert annotations missing"
    warns0 = [w["code"] for w in pw.check(loft.model)]
    assert "open-end" in warns0, warns0
    loft.pipe_cap()
    root.update()
    assert "open-end" not in [w["code"] for w in pw.check(loft.model)]
    assert loft.model.undo()          # one command = one undo
    assert "open-end" in [w["code"] for w in pw.check(loft.model)]
    assert loft.model.redo()
    tl_pipe = pw.takeoff(loft.model)
    assert any(t.kind == "length" and abs(t.qty - 28.0) < 0.1
               for t in tl_pipe), [(t.subject, t.qty) for t in tl_pipe]

    # ---- The Weaver: type to the board, it draws — the owner's command
    n_pipes0 = len([e for e in loft.model.ents if e.kind == "pipe"])
    loft.weave_var.set('run 4" sanitary from the wc to the main '
                       'at 1/8 per foot')
    loft.weave()
    root.update()
    saytext = loft.weave_say.cget("text")
    assert saytext.startswith("✓"), saytext
    pipes_now = [e for e in loft.model.ents if e.kind == "pipe"]
    assert len(pipes_now) == n_pipes0 + 1, "the Weaver should run new pipe"
    assert "'" in saytext, "say must speak feet-and-inches"
    # one undo reverts the whole command
    assert loft.model.undo()
    assert len([e for e in loft.model.ents if e.kind == "pipe"]) == n_pipes0
    assert loft.model.redo()
    # ask flow: missing slot -> one question -> answer -> done
    loft.weave_var.set("add a drinking fountain")
    loft.weave()
    root.update()
    assert loft._weave_pending is not None
    assert loft.weave_say.cget("text").startswith("?")
    loft.weave_var.set("at 12, 20")
    loft.weave()
    root.update()
    assert loft._weave_pending is None
    assert any(e.kind == "fixture" and e.props.get("stencil") == "df"
               for e in loft.model.ents), "answered ask should place the df"
    # refusals never touch the model
    n_ents = len(loft.model.ents)
    loft.weave_var.set("order me a pizza")
    loft.weave()
    root.update()
    assert loft.weave_say.cget("text").startswith("✋")
    assert len(loft.model.ents) == n_ents
    # cap through the bar
    loft.weave_var.set("cap the open ends")
    loft.weave()
    root.update()
    assert loft.weave_say.cget("text").startswith("✓")

    # ---- Harvest: Loft grids -> ghost pins -> committed layout points
    fsth = app.plans.fieldstitch
    app.plans.nb.select(fsth)
    root.update()
    # the blank-sheet regression above swapped in an uncalibrated job:
    # Harvest needs the world frame, so establish it (the GUI warns
    # instead when it's missing — that path blocks headless)
    fsth.set_scale('1/8" = 1\'-0"', 8.0 / 72.0, "ft")
    fsth.job.base_page_xy = (100.0, 700.0)
    fsth.job.base_world = (5000.0, 2000.0)
    n_before = len(fsth.job.points)
    fsth.harvest_gridiron()
    root.update()
    assert fsth._ghosts, "gridiron should propose the grid intersections"
    ghosts = len(fsth._ghosts)
    assert ghosts == 2, ghosts          # grids 1,2 x A in the Loft draft
    fsth._harvest_commit()
    root.update()
    assert len(fsth.job.points) == n_before + ghosts
    newest = fsth.job.points[-1]
    assert newest.provenance and newest.provenance.get("gen") == "gridiron"
    assert not fsth._ghosts, "commit clears the ghost tray"

    # ---- The Backcheck: run peer check on the Loft, filter, jump, mark
    bcheck = app.plans.backcheck
    app.plans.nb.select(loft)
    root.update()
    # seed a deliberate defect: two dims over the same span
    loft.model.add("dim", [(0.0, 0.0), (20.0, 0.0), (10.0, -3.0)])
    loft.model.add("dim", [(0.0, 0.0), (20.0, 0.0), (10.0, -4.0)])
    loft.backcheck_draft()               # Loft button -> panel runs check
    root.update()
    import time as _tb
    for _ in range(200):
        root.update()
        if bcheck.report is not None:
            break
        _tb.sleep(0.05)
    assert bcheck.report is not None, "Backcheck should produce a report"
    codes = {f.code for f in bcheck.report.findings}
    assert "DATA-DUPDIM" in codes, codes
    assert bcheck.tree.get_children(), "findings should populate the tree"
    # the honesty feature: out-of-scope checks are surfaced, not faked
    skipped = {s["code"] for s in bcheck.report.stats["skipped"]}
    assert "STD-HOLE-GDT" in skipped and "DFX-DRAFT-ANGLE" in skipped
    # severity filter hides rows
    shown_all = len(bcheck.tree.get_children())
    for v in bcheck.sev_vars.values():
        v.set(False)
    bcheck._fill()
    root.update()
    assert len(bcheck.tree.get_children()) == 0
    for v in bcheck.sev_vars.values():
        v.set(True)
    bcheck._fill()
    root.update()
    assert len(bcheck.tree.get_children()) == shown_all
    # jump to a finding selects it without error; mark drops a Q-BACK ply
    dup = next(f for f in bcheck.report.findings if f.code == "DATA-DUPDIM")
    bcheck.tree.selection_set(dup.id)
    bcheck.jump()
    root.update()
    n_ents = len(loft.model.ents)
    bcheck.mark_loft()
    root.update()
    assert loft.model.ply("Q-BACK") is not None
    assert len(loft.model.ents) > n_ents, "mark should add Q-BACK text ents"
    # clean up the defect dims so later assertions see a tidy model
    loft.model.remove([e.id for e in loft.model.ents
                       if e.kind == "dim" or e.ply == "Q-BACK"])

    # ---- The Old Hand + Heartwood: drawer plumbing, cited ask, refusal
    hwdb = os.path.join(tmp, "hw.db")
    app.oldhand.db_path = hwdb            # keep the test KB out of ~/
    app.oldhand.toggle(True)
    root.update()
    assert app.oldhand.open_
    from rfi_stamper.heartwood import Heartwood
    with Heartwood(hwdb) as hw:
        hw.ingest_text("Conductor sizing",
                       "The ungrounded conductor for a 20 ampere branch "
                       "circuit shall be 12 AWG copper minimum sizing.")
        hw.ingest_text("Branch circuits",
                       "Every 20 ampere branch circuit uses a 12 AWG "
                       "copper ungrounded conductor unless derated.")
        hw.ingest_text("Roofing membrane",
                       "Fully adhered membrane laps shall be 3 inches "
                       "minimum at side seams.")
        hw.rebuild()
        res = hw.ask("what size is the hot wire for a 20 amp circuit")
        assert not res["refused"] and res["blocks"], res
        assert res2_refused(hw)
        hw.teach("Panel schedules live in the trailer top drawer.",
                 author="QA")
        assert hw.notes(status="unverified")
    # the drawer answers through its worker thread
    app.oldhand.ask("hot wire size for 20 amp circuit")
    import time as _t3
    for _ in range(200):
        root.update()
        text_now = app.oldhand.log.get("1.0", "end")
        if "confidence" in text_now or "Not in" in text_now:
            break
        _t3.sleep(0.05)
    logtext = app.oldhand.log.get("1.0", "end")
    assert "You:" in logtext and "confidence" in logtext, logtext[-400:]
    app.oldhand.toggle(False)
    root.update()
    assert not app.oldhand.open_

    # ---- Phase F: the Corral — Ground Truth card + the Manage dialog
    import time as _t35
    # WITHOUT a store: the Heartwood card stays hidden, nothing is created,
    # nothing raises (fresh-install rule)
    app.oldhand.db_path = os.path.join(tmp, "no_such_hw.db")
    app.truth.refresh()
    root.update()
    assert not app.truth.hw_frame.winfo_manager(), "card shown w/o a store"
    assert not os.path.exists(app.oldhand.db_path), "refresh created a store"
    # WITH the seeded store: the card renders with real gauge numbers
    app.oldhand.db_path = hwdb
    app.truth.refresh()
    root.update()
    assert app.truth.hw_frame.winfo_manager(), "card hidden with a store"
    from rfi_stamper.heartwood import corral as _corral
    with Heartwood(hwdb) as hwq:
        g = _corral.gauges(hwq.store)
    assert g["chunks"] >= 3 and g["db_size_mb"] > 0, g
    assert app.truth.hw_tiles["passages"].counter._value >= 0

    # the Manage dialog opens headless with the provenance section and
    # populates through its worker thread (no blocking dialogs on this path)
    app.oldhand.manage_dialog()
    root.update()
    for _ in range(200):
        root.update()
        if app.oldhand.prov_tree.get_children():
            break
        _t35.sleep(0.05)
    rows = app.oldhand.prov_tree.get_children()
    assert rows, "provenance tree stayed empty"
    kinds = {app.oldhand.prov_tree.item(i, "values")[0] for i in rows}
    assert {"thesaurus", "note", "document"} <= kinds, kinds

    # Export learning… through the dialog path (explicit path, no dialog)
    snap = os.path.join(tmp, "learning.json")
    app.oldhand.export_learning(snap)
    for _ in range(200):
        root.update()
        if os.path.exists(snap):
            break
        _t35.sleep(0.05)
    assert os.path.exists(snap), "export produced no carry file"
    import json as _json
    bundle = _json.load(open(snap, encoding="utf-8"))
    assert bundle.get("format") == "planloom-heartwood-learning", bundle
    assert bundle.get("notes"), "the taught note did not travel"

    # Compact now: bg worker + toast; the growth series gains a snapshot
    with Heartwood(hwdb) as hwq:
        glen0 = len(hwq.gauges()["growth"])
    app.oldhand.compact_now()
    for _ in range(200):
        root.update()
        with Heartwood(hwdb) as hwq:
            if len(hwq.gauges()["growth"]) > glen0:
                break
        _t35.sleep(0.05)
    with Heartwood(hwdb) as hwq:
        assert len(hwq.gauges()["growth"]) == glen0 + 1, "compact not seen"

    # Purge (confirm bypassed: no messagebox headless): the taught note goes
    for _ in range(200):
        root.update()
        rows = app.oldhand.prov_tree.get_children()
        if rows:
            break
        _t35.sleep(0.05)
    note_iids = [i for i in app.oldhand.prov_tree.get_children()
                 if app.oldhand.prov_tree.item(i, "values")[0] == "note"]
    assert note_iids, "no note row to purge"
    app.oldhand.prov_tree.selection_set(note_iids[0])
    app.oldhand.purge_selected(confirm=False)
    for _ in range(200):
        root.update()
        with Heartwood(hwdb) as hwq:
            if not hwq.notes():
                break
        _t35.sleep(0.05)
    with Heartwood(hwdb) as hwq:
        assert hwq.notes() == [], "purge did not remove the note"

    # Import learning… into a FRESH store: the note returns, unverified
    hwdb2 = os.path.join(tmp, "hw2.db")
    app.oldhand.db_path = hwdb2
    app.oldhand.import_learning(snap)
    for _ in range(300):
        root.update()
        if os.path.exists(hwdb2):
            with Heartwood(hwdb2) as hwq:
                if hwq.notes():
                    break
        _t35.sleep(0.05)
    with Heartwood(hwdb2) as hwq:
        notes2 = hwq.notes()
    assert notes2 and all(n["status"] == "unverified" for n in notes2), \
        "import promoted or lost the note"
    # and Ground Truth renders against the imported store too
    app.truth.refresh()
    root.update()
    assert app.truth.hw_frame.winfo_manager()
    app.oldhand.db_path = hwdb

    # ---- Phase D: 3D uplift — shaded faces, pipe solids, walk, iso, measure
    bv3 = app.plans.bim
    app.goto("plans")
    app.plans.nb.select(bv3)
    root.update()
    # faces ride the Loft bridge; segments stay identical (regression pin)
    m4p = draft_mod.to_bim(loft.model, wall_height=9.0, floors=2)
    m4f = draft_mod.to_bim(loft.model, wall_height=9.0, floors=2, faces=True)
    assert m4f.faces and len(m4f.segments) == len(m4p.segments)
    bv3.set_model(m4f)
    root.update()
    assert not bv3.shaded_var.get(), "quality 'off': wireframe by default"
    assert not bv3.canvas.find_withtag("face")
    bv3.shaded_var.set(True)
    bv3._render()
    root.update()
    n_faces = len(bv3.canvas.find_withtag("face"))
    assert n_faces > 0, "shaded mode should draw wall faces"
    # Horizon Slice and legend toggles cull faces exactly like segments
    bv3.slice_var.set(20.0)
    bv3._on_slice()
    root.update()
    assert len(bv3.canvas.find_withtag("face")) < n_faces
    bv3.slice_var.set(100.0)
    bv3._on_slice()
    root.update()
    bv3.toggle_system("walls")
    root.update()
    assert not bv3.canvas.find_withtag("face"), "hidden system leaked faces"
    bv3.toggle_system("walls")
    root.update()

    # pipe solids + slope exaggeration (render-time only, model untouched)
    pdm = draft_mod.DraftModel()
    prun = pdm.add("pipe", [(0.0, 0.0), (40.0, 0.0)])
    pw.slope_run(pdm, prun.id, 0.25, start_invert_ft=10.0)
    m5p = pw.to_bim(pdm)
    assert m5p.segments[0].radius > 0, "pipewright should set the radius"
    bv3.set_model(m5p)
    root.update()
    bv3.cam.yaw, bv3.cam.pitch = 0.0, 0.0    # broadside: z maps to screen-y
    bv3._render()
    root.update()
    box1 = bv3.canvas.bbox("pipe3d")
    assert box1, "pipe run should render as a solid in shaded mode"
    z0ab = (m5p.segments[0].a[2], m5p.segments[0].b[2])
    bv3.slope_var.set(5.0)
    bv3._on_slope()
    root.update()
    assert "×5" in bv3.slope_lbl.cget("text")
    box5 = bv3.canvas.bbox("pipe3d")
    assert (box5[3] - box5[1]) > (box1[3] - box1[1]) + 2, (box1, box5)
    assert (m5p.segments[0].a[2], m5p.segments[0].b[2]) == z0ab, \
        "the slider must never mutate the model"
    bv3.slope_var.set(1.0)
    bv3._on_slope()
    root.update()

    # walk mode: enter at eye height, step forward, Esc restores orbit cam
    cam0 = (bv3.cam.yaw, bv3.cam.pitch, bv3.cam.dist, bv3.cam.target,
            bv3.cam.ortho)
    bv3.toggle_walk()
    root.update()
    assert bv3.walking
    assert bv3.canvas.find_withtag("hud"), "you-are-here chip missing"
    t_walk = bv3.cam.target
    assert bv3.walk_key("w"), "walk step not handled"
    root.update()
    assert bv3.cam.target != t_walk, "step must move the camera"
    bv3._on_escape(None)
    root.update()
    assert not bv3.walking
    assert (bv3.cam.yaw, bv3.cam.pitch, bv3.cam.dist, bv3.cam.target,
            bv3.cam.ortho) == cam0, "Esc must restore the orbit camera"

    # isometric presets: quality "off" snaps straight to the corner
    bv3.iso_view("NE")
    root.update()
    assert abs(bv3.cam.yaw % 360.0 - 45.0) < 1e-6, bv3.cam.yaw
    assert abs(bv3.cam.pitch - 30.0) < 1e-6, bv3.cam.pitch
    bv3.iso_view("SW")
    root.update()
    assert abs(bv3.cam.yaw % 360.0 - 315.0) < 1e-6, bv3.cam.yaw

    # 3D measure: two snapped clicks -> feet-inches tape; third click clears
    bv3.toggle_measure()
    assert bv3.measuring
    w3 = bv3.canvas.winfo_width()
    h3 = bv3.canvas.winfo_height()
    scr3 = bim3.project_points([m5p.segments[0].a, m5p.segments[-1].b],
                               bv3.cam, w3, h3)
    bv3._measure_click(int(scr3[0][0]), int(scr3[0][1]))
    bv3._measure_click(int(scr3[1][0]), int(scr3[1][1]))
    root.update()
    m_texts = [bv3.canvas.itemcget(i, "text")
               for i in bv3.canvas.find_withtag("measure")
               if bv3.canvas.type(i) == "text"]
    assert any("'" in t and "SD" in t and "HD" in t and "VD" in t
               for t in m_texts), m_texts
    bv3._measure_click(5, 5)                      # third click clears
    root.update()
    assert not bv3.canvas.find_withtag("measure")
    bv3._on_escape(None)                          # Esc exits measure mode
    assert not bv3.measuring
    bv3.shaded_var.set(False)

    # ---- Holler: the hands-free voice companion (dispatch, Ticker, Songbook)
    app.open_holler()
    root.update()
    hd = app.holler
    assert hd is not None and hd.win.winfo_exists()
    hd.dispatch("two feet seven and seven eighths")   # the Caller grammar
    root.update()
    hd.dispatch("line")                                # a seed Trip
    root.update()
    hd.dispatch("issued for construction")             # a seed Placard
    root.update()
    hd.dispatch("nonsense zzz")                         # a miss
    root.update()
    tape = hd.tape.get("1.0", "end")
    assert "2'-7 7/8\"" in tape, tape
    assert "l+Enter" in tape and "ISSUED FOR CONSTRUCTION" in tape
    assert "not in the Songbook" in tape
    s = hd.ticker.summary()
    assert s["commands"] == 3 and s["keystrokes_saved"] > 20, s
    assert len(hd.tree.get_children()) == len(hd.songbook.entries) >= 7
    # re-opening resurfaces the single instance, never a second window
    app.open_holler()
    root.update()
    assert app.holler is hd
    hd.close()
    root.update()

    # ---- the Tracer: built-in OCR is wired into PDF Tools + palette
    pt = app.projsec.pdftools
    assert hasattr(pt, "tracer_ocr"), "built-in OCR button missing"
    import rfi_stamper.tracer as tracer_mod
    assert tracer_mod.available() is True
    assert tracer_mod.info()["path"] == "builtin"
    # the engine reads a rasterized scan end to end (no external OCR engine)
    sdir = tempfile.mkdtemp(prefix="tracer_")
    sp = os.path.join(sdir, "scan.pdf")
    scr = fitz.open()
    _pg = scr.new_page(width=612, height=792)
    _pg.insert_text((72, 220), "P-101", fontsize=64)
    _pix = _pg.get_pixmap(dpi=200)
    scr.close()
    doc2 = fitz.open()
    ip = doc2.new_page(width=612, height=792)
    ip.insert_image(ip.rect, pixmap=_pix)
    doc2.save(sp)
    doc2.close()
    txt = tracer_mod.ocr_page_text(sp, 1)
    # P2 ensemble reads the alphanumerics reliably (marks improved too)
    flat = "".join(txt.split()).upper()
    assert "101" in flat and "P" in flat, repr(txt)
    # P3: the set's own sheet index cross-checks a scanned title-block read,
    # the same context the PDF Tools built-in-OCR button now passes
    from rfi_stamper.tracer import lexicon as _tlex
    idxpdf = os.path.join(sdir, "set.pdf")
    idxout = os.path.join(sdir, "set_ocr.pdf")
    dd = fitz.open()
    ip1 = dd.new_page(width=612, height=792)
    ip1.insert_text((72, 120), "INDEX S-100 S-101 S-102", fontsize=20)
    _s = fitz.open()
    _sp = _s.new_page(width=612, height=792)
    _sp.insert_text((470, 720), "S-101", fontsize=30)
    _px = _sp.get_pixmap(dpi=200)
    _s.close()
    ip2 = dd.new_page(width=612, height=792)
    ip2.insert_image(ip2.rect, pixmap=_px)
    dd.save(idxpdf)
    dd.close()
    with fitz.open(idxpdf) as _hd:
        assert "S-101" in tracer_mod.harvest_sheet_hints(_hd)
    rr = tracer_mod.ocr_pdf(idxpdf, idxout,
                            lexicon=_tlex.Lexicon.default(),
                            log=lambda *a: None)
    assert rr["pages_ocred"] == 1
    with fitz.open(idxout) as _o:
        assert "S-101" in _o[1].get_text("text").upper()
    assert any("Tracer" in c[0] or "built-in" in c[0].lower()
               for c in pt.commands()), "Tracer palette command missing"

    # ---- round 4: icon asset loads, hero spin guarded, stamp-slam no-ops
    from rfi_stamper.gui.app import resource_path
    icon_png = resource_path(os.path.join("assets", "planloom.png"))
    assert os.path.exists(icon_png), icon_png
    import tkinter as tk2
    probe_img = tk2.PhotoImage(file=icon_png)
    assert probe_img.width() == 256
    assert hasattr(app.home, "_start_spin") and app.home._spin_on is False, \
        "quality is 'off' in tests: hero spin must not be running"
    app.celebrate_verified()      # quality off -> must be a silent no-op
    root.update()
    from rfi_stamper.gui import fx as fx2
    assert fx2.quality() == "off"

    # offline guard active by default; undo depth effectively unlimited
    from rfi_stamper import offline_guard
    assert offline_guard.is_active()
    assert mk_tab.UNDO_LIMIT >= 500

    # the Ropes: first-run offer, tour engine (spotlight + hands-on click +
    # Show me), progress persistence, and the Training Center
    from rfi_stamper.gui import prefs as _prefs_mod
    from rfi_stamper.gui import ropes as _ropes
    import time as _trp
    _pp0 = (_prefs_mod.PREFS_DIR, _prefs_mod.PREFS_PATH)
    _prefs_mod.PREFS_DIR = os.path.join(tmp, "ploomprefs")
    _prefs_mod.PREFS_PATH = os.path.join(_prefs_mod.PREFS_DIR, "prefs.json")
    os.makedirs(_prefs_mod.PREFS_DIR, exist_ok=True)
    try:
        _dlg = _ropes.first_run_offer(app)
        root.update()
        assert _dlg is not None and _dlg.winfo_exists(), "offer shows once"
        _dlg.destroy()
        root.update()
        assert _ropes.first_run_offer(app) is None, "offer never nags twice"

        _cat = _ropes.courses(app)
        assert _cat[0]["key"] == "grand_tour" and len(_cat) >= 5
        assert all(c["roadmap"] and c["steps"] for c in _cat), "roadmaps set"

        _ended = []
        tour = _ropes.RopesTour(app, _cat[0],
                                on_end=lambda d: _ended.append(d))
        tour.start(0)
        for _ in range(100):
            root.update()
            if tour.drawn >= 1:
                break
            _trp.sleep(0.02)
        assert tour.cv is not None and tour.cv.winfo_exists(), \
            "spotlight overlay drew"
        assert tour._circle is None, "welcome step: card only, no hole"
        tour._next()                        # -> the "home" nav step
        for _ in range(100):
            root.update()
            if tour.drawn >= 2:
                break
            _trp.sleep(0.02)
        assert tour._circle is not None, "nav step spotlights the nav zone"
        _hy = tour._circle[1]
        assert _hy < app.nav.HEIGHT + 40, "spotlight sits on the nav bar"
        assert tour._strips, "fallback tint strips leave the hole OPEN"
        # the hole is physically uncovered: no strip contains its center
        _hcx = (tour._circle[0] + tour._circle[2]) / 2
        _hcy = (tour._circle[1] + tour._circle[3]) / 2
        for _st in tour._strips:
            _sx, _sy = _st.winfo_x(), _st.winfo_y()
            assert not (_sx <= _hcx <= _sx + _st.winfo_width()
                        and _sy <= _hcy <= _sy + _st.winfo_height()), \
                "a tint strip covers the spotlight hole"
        # hands-on: the trainee clicks the REAL nav item through the open
        # hole; simulate that real action — the done_when watcher advances
        _i0 = tour.i
        app.goto("field")
        for _ in range(100):
            root.update()
            if tour.drawn >= 3 and tour.i == _i0 + 1:
                break
            _trp.sleep(0.02)
        assert tour.i == _i0 + 1, "the real action advanced the step"
        # Show me: the pointer performs the step and moves on
        _step = tour.course["steps"][tour.i]
        _bbox = tour._target_bbox(_step)
        assert _bbox is not None
        _i1 = tour.i
        tour._show_me(_bbox)
        for _ in range(150):
            root.update()
            if tour.i == _i1 + 1:
                break
            _trp.sleep(0.02)
        assert tour.i == _i1 + 1, "Show me performed and advanced"
        assert app._current == "project", "Show me really navigated"
        tour.end()
        root.update()
        assert tour.cv is None and _ended == [False], "tour ends cleanly"
        _saved = _prefs_mod.load().get("ropes", {}).get("grand_tour", {})
        assert _saved.get("step") == _i1 + 1 and not _saved.get("done"), \
            _saved
        # the Training Center lists every course and offers Resume
        tc = _ropes.TrainingCenter(app)
        root.update()
        assert tc.listbox.size() == len(_cat), "all courses listed"
        assert "Resume" in str(tc.start_btn.cget("text")), \
            tc.start_btn.cget("text")
        tc.dlg.destroy()
        root.update()
    finally:
        _prefs_mod.PREFS_DIR, _prefs_mod.PREFS_PATH = _pp0
    app.goto("home")
    root.update()

    # the from-scratch drag-drop layer (tkinterdnd2 retired)
    check_dnd(root)

    app.on_close()
    print("GUI CONSTRUCT TEST PASSED")


if __name__ == "__main__":
    main()
