"""Headless GUI construction test: builds the full app (all four tabs, theme
toggle, command palette, viewer open/zoom/page-flip on a synthetic PDF) under
a virtual display and tears it down.  Catches import errors, broken widget
wiring, and theme regressions without a human at the screen.

Run:  xvfb-run -a python3 tests/test_gui_construct.py     (Linux)
      python tests\\test_gui_construct.py                  (Windows/mac, visible)
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
    from rfi_stamper.gui import dnd
    from rfi_stamper.gui.app import App

    tmp = tempfile.mkdtemp(prefix="rfi_gui_")
    pdf = os.path.join(tmp, "t.pdf")
    make_pdf(pdf)

    root = dnd.make_root()
    root.geometry("1200x800")
    app = App(root)
    root.update_idletasks()
    root.update()

    # every tab constructed (home + five tools)
    assert len(app.nb.tabs()) == 6, app.nb.tabs()

    # theme round-trip (start theme comes from user prefs — don't assume it)
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

    # viewer: open, flip, zoom, invert, markup store attached
    app.nb.select(app.markup)
    app.markup.open_pdf(pdf)
    root.update()
    assert app.markup.viewer.page_count == 2
    app.markup.viewer.next_page()
    root.update()
    assert app.markup.viewer.page_no == 2
    app.markup.viewer.zoom_by(1.5)
    app.markup.viewer.set_invert(True)
    root.update()
    assert app.markup.store is not None

    # add a markup programmatically and check list + sidecar autosave
    from rfi_stamper import markups as mk
    app.markup.push_undo()
    app.markup.store.add(mk.Markup.new(1, "rect", [(100, 100), (200, 160)],
                                       subject="gui-test"))
    app.markup.after_change()
    root.update()
    assert len(app.markup.mtree.get_children()) == 1
    assert os.path.exists(mk.MarkupStore.sidecar_path(pdf))
    app.markup.undo()
    root.update()
    assert len(app.markup.mtree.get_children()) == 0

    # poly-tool preview path (regression: _draw_poly_preview must exist and run)
    app.markup.set_tool("measure_polylength")
    app.markup._pts = [(50.0, 50.0), (120.0, 80.0)]
    app.markup._draw_poly_preview()
    root.update()
    assert app.markup.viewer.canvas.find_withtag("preview")
    # page navigation cancels the in-progress tool
    app.markup.viewer.goto(1)
    root.update()
    assert app.markup._pts == [] and not \
        app.markup.viewer.canvas.find_withtag("preview")
    # Esc exits the count tool back to select
    app.markup.set_tool("count")
    app.markup.on_escape()
    assert app.markup.tool == "select"
    # cancel_tool clears the hover rubber-band too
    app.markup.viewer.canvas.create_line(0, 0, 5, 5, tags="hoverseg")
    app.markup.cancel_tool()
    assert not app.markup.viewer.canvas.find_withtag("hoverseg")

    # merge tab accepts a file
    app.merge.add_paths([pdf])
    root.update()
    assert len(app.merge.items) == 1

    # stamp guard: stamping a plan that was never scanned must be refused
    assert app.stamp.scanned_plan is None and app.stamp._running is False

    # home routing: one PDF -> markup tab; several -> combine list
    before = len(app.merge.items)
    app.route_paths([pdf, pdf])
    root.update()
    assert len(app.merge.items) == before + 2
    app.route_paths([pdf])
    root.update()
    assert app.markup.viewer.path == pdf

    # full-window drop overlay: hint reflects the active tab, drop routes
    app.nb.select(app.merge)
    root.update()
    assert "Combine" in app._drop_hint()
    n = len(app.merge.items)
    app._drop_route([pdf])
    root.update()
    assert len(app.merge.items) == n + 1

    # scale preset sets a calibration and captions recompute
    app.nb.select(app.markup)
    app.markup._use_scale('1/8" = 1\'-0"', (1 / 0.125) / 72.0, "ft-in")
    assert app.markup.cal is not None and app.markup.cal.unit == "ft-in"
    assert abs(app.markup.cal.real_per_pt - 8.0 / 72.0) < 1e-9

    # auto-numbered counts: P -> P-001, P-002
    from rfi_stamper import markups as mk2
    app.markup.textlbl_var.set("P")
    app.markup.autonum_var.set(True)
    app.markup.set_tool("count")

    class _Ev:
        x, y, state = 30, 30, 0
    app.markup.on_press(_Ev())
    app.markup.on_press(_Ev())
    labels = sorted(m.text for m in app.markup.store.markups
                    if m.type == "count")
    assert labels == ["P-001", "P-002"], labels
    app.markup.undo()
    app.markup.undo()

    # stamp dashboard tiles exist and start unpopulated
    assert set(app.stamp._tile_vars) == {"rfis", "answered", "sheets",
                                         "unmatched"}

    # toast appears and self-destructs
    from rfi_stamper.gui.widgets import toast
    t = toast(root, app.theme, "test toast", ms=150)
    root.update()
    assert t.winfo_exists()

    # construction stamps seeded in a fresh tool chest
    tc = mk2.ToolChest(os.path.join(tmp, "toolchest.json"))
    names = [p.name for p in tc.presets]
    assert any("HOLD" in n for n in names), names
    assert any("Punch Dot" in n for n in names), names

    # per-page scale memory: a scale on page 2 doesn't leak to page 1.
    # Use a fresh PDF path so no earlier sub-test's .scale.json sidecar bleeds in.
    import shutil
    pdf2 = os.path.join(tmp, "scale_iso.pdf")
    shutil.copy(pdf, pdf2)
    app.nb.select(app.markup)
    app.markup.open_pdf(pdf2)
    root.update()
    app.markup.viewer.goto(2)
    app.markup.scale_all_pages.set(False)
    app.markup._use_scale('1/4" = 1\'-0"', (1 / 0.25) / 72.0, "ft-in")
    assert app.markup.cal_for(2) is not None
    assert app.markup.cal_for(1) is None

    # PDF Tools tab wired with the one-touch actions
    assert hasattr(app.pdftools, "auto_fix") and hasattr(app.pdftools, "autolink")

    # effectively-unlimited undo depth
    assert app.markup.UNDO_LIMIT >= 500

    # CLI: top-level --help must not be swallowed by the legacy-flag rewrite
    import subprocess
    r = subprocess.run([sys.executable, "-m", "rfi_stamper", "--help"],
                       capture_output=True, text=True,
                       cwd=os.path.dirname(os.path.dirname(
                           os.path.abspath(__file__))))
    assert r.returncode == 0 and "merge" in r.stdout and "compare" in r.stdout

    # offline guard is active by default
    from rfi_stamper import offline_guard
    assert offline_guard.is_active()

    app.on_close()
    print("GUI CONSTRUCT TEST PASSED")


if __name__ == "__main__":
    main()
