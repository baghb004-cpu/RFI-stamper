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
