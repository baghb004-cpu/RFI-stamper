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

    # every tab constructed
    assert len(app.nb.tabs()) == 4, app.nb.tabs()

    # theme round-trip
    app.toggle_dark()
    root.update()
    assert app.theme.name == "dark"
    app.toggle_dark()
    root.update()
    assert app.theme.name == "light"

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
