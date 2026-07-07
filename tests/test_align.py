"""Self-contained tests for rfi_stamper.align (plain asserts, no pytest)."""
from __future__ import annotations

import math
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import fitz

from rfi_stamper.align import (AlignResult, auto_align, comparison_image,
                               make_comparison_pdf, render_page_gray)

PAGE_W, PAGE_H = 792.0, 612.0  # letter landscape, pt


def _linework_segments():
    """Deterministic asymmetric linework: border, grid, diagonals, notches."""
    segs = []
    m = 60.0
    x0, y0, x1, y1 = m, m, PAGE_W - m, PAGE_H - m
    segs += [(x0, y0, x1, y0), (x1, y0, x1, y1), (x1, y1, x0, y1), (x0, y1, x0, y0)]
    for i in range(1, 8):
        x = x0 + i * (x1 - x0) / 8.0
        segs.append((x, y0, x, y1))
    for j in range(1, 5):
        y = y0 + j * (y1 - y0) / 5.0
        segs.append((x0, y, x1, y))
    segs += [(x0, y0, x0 + 250, y0 + 180), (x1, y0, x1 - 320, y0 + 260),
             (x0 + 80, y1, x0 + 300, y1 - 200)]
    for k in range(6):
        x = 120.0 + k * 95.0
        segs.append((x, 100.0 + 12.0 * k, x + 40.0, 100.0 + 12.0 * k))
    return segs


def _make_pdf(path, off=(0.0, 0.0), angle_deg=0.0):
    """Draw the linework shifted by off (pt, fitz page coords: y down) and
    rotated by angle_deg about the page center (fitz.Matrix sign convention)."""
    ox, oy = off
    cx, cy = PAGE_W / 2.0, PAGE_H / 2.0
    th = math.radians(angle_deg)
    ct, st = math.cos(th), math.sin(th)

    def T(x, y):
        x, y = x - cx, y - cy
        return fitz.Point(x * ct - y * st + cx + ox, x * st + y * ct + cy + oy)

    doc = fitz.open()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    shape = page.new_shape()
    for (ax, ay, bx, by) in _linework_segments():
        shape.draw_line(T(ax, ay), T(bx, by))
    shape.finish(width=2.0, color=(0, 0, 0))
    shape.commit()
    doc.save(path)
    doc.close()


def _colored_counts(img):
    both = int((img == np.array([40, 40, 40], np.uint8)).all(axis=2).sum())
    white = int((img == 255).all(axis=2).sum())
    colored = img.shape[0] * img.shape[1] - both - white
    return colored, both


def main():
    tmp = tempfile.mkdtemp(prefix="align_test_")
    base = os.path.join(tmp, "base.pdf")
    shifted = os.path.join(tmp, "shifted.pdf")
    rotated = os.path.join(tmp, "rotated.pdf")
    same = os.path.join(tmp, "same.pdf")
    _make_pdf(base)
    _make_pdf(shifted, off=(36.0, -18.0))
    _make_pdf(rotated, off=(20.0, 10.0), angle_deg=2.0)
    _make_pdf(same)

    # 1. known translation: overlay content at +(36,-18) pt -> shift of (-36,+18)
    for try_rot in (True, False):
        res = auto_align(base, shifted, try_rotation=try_rot)
        print(f"shift try_rotation={try_rot}: dx={res.dx:.2f} dy={res.dy:.2f} "
              f"rot={res.rotation:.2f} score={res.score:.3f}")
        assert abs(res.dx - (-36.0)) <= 2.0, res
        assert abs(res.dy - 18.0) <= 2.0, res
        assert abs(res.rotation) <= 0.6, res
        assert res.score > 0.05, res
    align_shift = auto_align(base, shifted, try_rotation=False)

    # 2. rotation + shift: 2.0 deg recovered within 0.6 deg
    res_rot = auto_align(base, rotated, try_rotation=True)
    print(f"rotated: dx={res_rot.dx:.2f} dy={res_rot.dy:.2f} "
          f"rot={res_rot.rotation:.2f} score={res_rot.score:.3f}")
    assert abs(abs(res_rot.rotation) - 2.0) <= 0.6, res_rot
    # and the recovered align must actually overlay the two rotated pages
    img_r0 = comparison_image(base, rotated)
    img_r1 = comparison_image(base, rotated, align=res_rot)
    c0, _ = _colored_counts(img_r0)
    c1, b1 = _colored_counts(img_r1)
    print(f"rotated compare: colored no-align={c0} with-align={c1} both={b1}")
    assert c1 < 0.5 * c0 and b1 > 1000, (c0, c1, b1)

    # 3. identical pages: near-zero shift, high score
    res_same = auto_align(base, same, try_rotation=True)
    print(f"identical: dx={res_same.dx:.3f} dy={res_same.dy:.3f} "
          f"rot={res_same.rotation:.2f} score={res_same.score:.3f}")
    assert abs(res_same.dx) <= 0.5 and abs(res_same.dy) <= 0.5, res_same
    assert res_same.score > 0.5, res_same

    # 4. comparison_image on shifted pair
    img_no = comparison_image(base, shifted)
    bc = np.array([200, 30, 30], np.uint8)
    oc = np.array([30, 80, 200], np.uint8)
    n_base = int((img_no == bc).all(axis=2).sum())
    n_over = int((img_no == oc).all(axis=2).sum())
    print(f"no-align: base-only={n_base} overlay-only={n_over}")
    assert n_base > 500 and n_over > 500, (n_base, n_over)
    img_al = comparison_image(base, shifted, align=align_shift)
    n_base_a = int((img_al == bc).all(axis=2).sum())
    n_over_a = int((img_al == oc).all(axis=2).sum())
    n_both_a = int((img_al == np.array([40, 40, 40], np.uint8)).all(axis=2).sum())
    print(f"aligned: base-only={n_base_a} overlay-only={n_over_a} both={n_both_a}")
    assert n_both_a > 1000, n_both_a
    assert (n_base_a + n_over_a) < 0.2 * (n_base + n_over), (n_base_a, n_over_a)

    # 5. make_comparison_pdf: valid single-page PDF at base page pt size
    out_pdf = os.path.join(tmp, "compare.pdf")
    make_comparison_pdf(base, shifted, out_pdf, align=align_shift,
                        log=lambda *a: None)
    doc = fitz.open(out_pdf)
    assert len(doc) == 1, len(doc)
    r = doc[0].rect
    assert abs(r.width - PAGE_W) <= 0.5 and abs(r.height - PAGE_H) <= 0.5, r
    pix = doc[0].get_pixmap(dpi=72)
    assert pix.width > 0 and pix.height > 0
    doc.close()

    # 6. blank page guard
    blank = os.path.join(tmp, "blank.pdf")
    d = fitz.open()
    d.new_page(width=PAGE_W, height=PAGE_H)
    d.save(blank)
    d.close()
    res_blank = auto_align(base, blank)
    assert res_blank == AlignResult(), res_blank

    # 7. render_page_gray sanity
    g = render_page_gray(base, dpi=72)
    assert g.dtype == np.uint8 and g.shape == (int(PAGE_H), int(PAGE_W)), g.shape

    # 8. regression: two Arch-D pages (2592x1728 pt) at default dpi with the
    # rotation sweep must stay under the spec's 10 s budget.  The rotated sweep
    # renders used to pad to sizes with large prime factors, which made the FFTs
    # pathologically slow (~11 s total).
    def _archd(path, off=(0.0, 0.0)):
        aw, ah, m = 2592.0, 1728.0, 100.0
        ox, oy = off
        d2 = fitz.open()
        pg = d2.new_page(width=aw, height=ah)
        sh = pg.new_shape()
        for i in range(25):
            x = m + i * (aw - 2 * m) / 24.0
            sh.draw_line(fitz.Point(x + ox, m + oy), fitz.Point(x + ox, ah - m + oy))
        for j in range(17):
            y = m + j * (ah - 2 * m) / 16.0
            sh.draw_line(fitz.Point(m + ox, y + oy), fitz.Point(aw - m + ox, y + oy))
        sh.draw_line(fitz.Point(m + ox, m + oy),
                     fitz.Point(aw / 2 + ox, ah - m + oy))
        sh.finish(width=3.0, color=(0, 0, 0))
        sh.commit()
        d2.save(path)
        d2.close()

    arch_a = os.path.join(tmp, "arch_a.pdf")
    arch_b = os.path.join(tmp, "arch_b.pdf")
    _archd(arch_a)
    _archd(arch_b, off=(24.0, -12.0))
    t0 = time.time()
    res_arch = auto_align(arch_a, arch_b)  # default dpi, try_rotation=True
    elapsed = time.time() - t0
    print(f"arch-d: dx={res_arch.dx:.2f} dy={res_arch.dy:.2f} "
          f"rot={res_arch.rotation:.2f} score={res_arch.score:.3f} "
          f"({elapsed:.2f}s)")
    assert elapsed < 10.0, elapsed
    assert abs(res_arch.dx - (-24.0)) <= 2.0 and abs(res_arch.dy - 12.0) <= 2.0, res_arch
    assert abs(res_arch.rotation) <= 0.6 and res_arch.score > 0.5, res_arch

    print("ALIGN TESTS PASSED")


if __name__ == "__main__":
    main()
