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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz                                            # noqa: E402


def make_pdf(path, pages=2):
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page(width=612, height=792)
        page.draw_rect(fitz.Rect(40, 40, 572, 752))
        page.insert_text((60, 70), f"GUI TEST PAGE {i + 1}", fontsize=18)
    doc.save(path)
    doc.close()


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

    # CLI --help unswallowed (regression)
    import subprocess
    r = subprocess.run([sys.executable, "-m", "rfi_stamper", "--help"],
                       capture_output=True, text=True,
                       cwd=os.path.dirname(os.path.dirname(
                           os.path.abspath(__file__))))
    assert r.returncode == 0 and "merge" in r.stdout

    # offline guard active by default; undo depth effectively unlimited
    from rfi_stamper import offline_guard
    assert offline_guard.is_active()
    assert mk_tab.UNDO_LIMIT >= 500

    app.on_close()
    print("GUI CONSTRUCT TEST PASSED")


if __name__ == "__main__":
    main()
