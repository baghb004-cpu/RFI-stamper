"""Plan-set indexing: figure out which page is which sheet, page geometry."""
from __future__ import annotations

import re
from dataclasses import dataclass

import fitz

from .core import canon_loose

TOKEN_FULL = re.compile(r"^[A-Z]{1,3}-?\d{1,3}(\.\d{1,2})?$")


@dataclass
class PageInfo:
    page_no: int          # 1-based
    sheet: str            # canonical, e.g. P-10.10
    view_w: float         # pt, rotation applied (what the viewer sees)
    view_h: float
    rotation: int
    media_w: float
    media_h: float
    media_x0: float
    media_y0: float


class SheetIndex:
    def __init__(self, plan_path: str, log=lambda *_: None):
        self.path = plan_path
        self.pages: list[PageInfo] = []
        doc = fitz.open(plan_path)
        for i, page in enumerate(doc):
            rect = page.rect                       # rotation applied
            mb = page.mediabox
            sheet = self._detect_sheet(page, rect.width, rect.height) or f"PAGE-{i+1}"
            self.pages.append(PageInfo(
                page_no=i + 1, sheet=sheet,
                view_w=rect.width, view_h=rect.height,
                rotation=page.rotation % 360,
                media_w=mb.width, media_h=mb.height,
                media_x0=mb.x0, media_y0=mb.y0,
            ))
            log(f"  page {i+1}: {sheet}  ({rect.width:.0f}x{rect.height:.0f}pt rot{page.rotation})")
        doc.close()
        self.by_sheet = {}
        self.by_loose = {}
        for p in self.pages:
            self.by_sheet.setdefault(p.sheet, p.page_no)
            self.by_loose.setdefault(canon_loose(p.sheet), p.page_no)

    # ------------------------------------------------------------------ #
    @staticmethod
    def _detect_sheet(page, vw: float, vh: float):
        raw = page.get_text("words")
        if not raw:
            return None

        def score_words(words):
            best, best_score = None, -1.0
            for w in words:
                tok = w[4].strip().strip(".,;:()").upper()
                if not TOKEN_FULL.match(tok) or len(tok) < 3:
                    continue
                nx = ((w[0] + w[2]) / 2) / vw
                ny = ((w[1] + w[3]) / 2) / vh      # y grows downward
                if not (0 <= nx <= 1 and 0 <= ny <= 1):
                    continue
                score = nx + ny
                if nx > 0.72 and ny > 0.72:
                    score += 2.0                    # title-block corner
                if "." in tok or "-" in tok:
                    score += 0.3
                if score > best_score:
                    best_score, best = score, tok
            return best, best_score

        # Depending on PyMuPDF version, word coords on rotated pages may be in
        # unrotated media space.  Score the rotation-corrected view first, then
        # the raw coords, and keep whichever produces a corner hit.
        candidates = []
        if page.rotation % 360:
            m = page.rotation_matrix
            conv = [tuple(fitz.Rect(w[:4]) * m) + (w[4],) for w in raw]
            candidates.append(score_words(conv))
        candidates.append(score_words(raw))
        best, best_score = max(candidates, key=lambda c: c[1])
        if best and best_score >= 2.0:              # must be corner-ish
            if "-" not in best:                     # normalize S10.10 -> S-10.10
                m = re.match(r"([A-Z]+)(\d.*)", best)
                best = f"{m.group(1)}-{m.group(2)}"
            return best
        # fallback: explicit SHEET NO label in raw text
        txt = page.get_text()
        m = re.search(r"SHEET\s*(?:NO|NUMBER)\.?:?\s*\n?\s*([A-Z]{1,3}-?\d{1,3}(?:\.\d{1,2})?)",
                      txt, re.IGNORECASE)
        if m:
            tok = m.group(1).upper()
            if "-" not in tok:
                mm = re.match(r"([A-Z]+)(\d.*)", tok)
                tok = f"{mm.group(1)}-{mm.group(2)}"
            return tok
        return None

    # ------------------------------------------------------------------ #
    def match(self, token: str):
        """Return page_no for a canonical token, tolerant of leading zeros
        and missing hyphens; None if the token is not a sheet in this set."""
        if token in self.by_sheet:
            return self.by_sheet[token]
        return self.by_loose.get(canon_loose(token))

    def info(self, page_no: int) -> PageInfo:
        return self.pages[page_no - 1]
