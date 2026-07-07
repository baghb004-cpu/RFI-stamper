"""Post-stamp verification: the only rendered change on any page must be the
note boxes themselves, and nothing may have existed under their footprints."""
from __future__ import annotations

import numpy as np
import fitz

from .layout import DARK_THRESH, DIFF_THRESH, PAD_PX


def render_gray(doc, page_no, dpi=90):
    pix = doc[page_no - 1].get_pixmap(dpi=dpi, colorspace=fitz.csGRAY, alpha=False)
    return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)


def verify(plan_path, out_path, placements, index, dpi=90, log=print):
    pre_doc = fitz.open(plan_path)
    post_doc = fitz.open(out_path)
    scale = dpi / 72.0
    ok = True
    results = []
    for i in range(1, len(pre_doc) + 1):
        pre = render_gray(pre_doc, i, dpi).astype(np.int16)
        post = render_gray(post_doc, i, dpi).astype(np.int16)
        if pre.shape != post.shape:
            results.append((i, "FAIL", "render size mismatch"))
            ok = False
            continue
        d = np.abs(pre - post) > DIFF_THRESH
        boxes = placements.get(i, [])
        if not boxes:
            n = int(d.sum())
            good = n == 0
            ok &= good
            results.append((i, "OK" if good else "FAIL",
                            "untouched" if good else f"{n}px changed unexpectedly"))
            continue
        H = pre.shape[0]
        mask = np.zeros_like(d)
        under = 0
        for b in boxes:
            if b.get("occ"):
                x0, yt, x1, yb = b["occ"]     # exact window the finder cleared
            else:
                x0 = int(b["x"] * scale) - PAD_PX
                x1 = int((b["x"] + b["w"]) * scale) + PAD_PX
                yt = H - int(b["ytop"] * scale) - PAD_PX
                yb = H - int((b["ytop"] - b["h"]) * scale) + PAD_PX
            x0, yt = max(0, x0), max(0, yt)
            x1, yb = min(pre.shape[1], x1), min(H, yb)
            mask[yt:yb, x0:x1] = True
            under += int((pre[yt:yb, x0:x1] < DARK_THRESH).sum())
        outside = int((d & ~mask).sum())
        inside = int((d & mask).sum())
        good = under == 0 and outside == 0 and inside > 300
        ok &= good
        results.append((i, "OK" if good else "FAIL",
                        f"boxes={len(boxes)} under={under} outside={outside} inside={inside}"))
    for pno in range(len(pre_doc) + 1, len(post_doc) + 1):
        results.append((pno, "OK", "appendix page (added, not diffed)"))
    pre_doc.close()
    post_doc.close()
    for pno, st, msg in results:
        log(f"  p{pno:02d} {st:4} {msg}")
    return ok, results
