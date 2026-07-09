"""PDF document assembler — objects, page tree, byte-exact xref, trailer.

Emits a ``%PDF-1.4`` file with a classic cross-reference table (ISO 32000-1
§7.5): the universal-compatibility floor, one debuggable code path, no object or
xref streams.  The byte-critical rules a hand-writer must not get wrong are all
here: the leading binary-marker comment, 20-byte xref records with the mandatory
free object 0, offsets counted in BYTES from the file start, an exact per-stream
``/Length``, ``/Size`` = object count + 1, and ``startxref`` at the ``xref``
keyword.

Output is **deterministic**: no ``/Info``, no timestamps, no ``/Producer`` — the
``/ID`` is a content hash — so identical input yields identical bytes.  That
protects the pixel-diff baseline and the offline/NDA posture (reportlab leaked a
timestamped ``/Producer``); it also makes byte-hash regression tests possible.
"""
from __future__ import annotations

import hashlib

from . import metrics
from .content import Content, fmt_num
from .encoding import pdf_name

_HEADER = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"   # version + 4-byte binary marker


class Page:
    def __init__(self, doc: "Document", width: float, height: float):
        self.width = width
        self.height = height
        self.content = Content(doc)


class Document:
    """Collects pages, then serializes to bytes.

    Fonts are registered on first use (``content.set_font``) and shared across
    all pages via one ``/Resources`` dict, keeping the object count low.
    """

    def __init__(self):
        self.pages: list[Page] = []
        self._fonts: dict[str, str] = {}   # base font name -> resource key Fn
        self._images: dict = {}            # content hash -> (key "ImN", Image)

    def add_page(self, width: float, height: float) -> Page:
        pg = Page(self, width, height)
        self.pages.append(pg)
        return pg

    def _use_font(self, font: str) -> str:
        """Register a base-14 font (validating the name) and return its /Fn key."""
        canon = metrics._canon_font(font)          # raises on an unknown font
        key = self._fonts.get(canon)
        if key is None:
            key = f"F{len(self._fonts) + 1}"
            self._fonts[canon] = key
        return key

    def _use_image(self, img) -> str:
        """Register an image (dedup by content hash) and return its /ImN key."""
        entry = self._images.get(img.key)
        if entry is None:
            entry = (f"Im{len(self._images) + 1}", img)
            self._images[img.key] = entry
        return entry[0]

    def to_bytes(self) -> bytes:
        fonts = list(self._fonts.items())          # (base_name, key) insertion order
        images = list(self._images.values())       # (key "ImN", Image) insertion order
        n_pages = len(self.pages)

        # deterministic object numbering: catalog, pages, then page+content
        # pairs, then one object per font, then one per image.
        catalog_no, pages_no = 1, 2
        num = 3
        page_nos, content_nos = [], []
        for _ in self.pages:
            page_nos.append(num); num += 1
            content_nos.append(num); num += 1
        font_nos = {}
        for _name, key in fonts:
            font_nos[key] = num; num += 1
        image_nos = {}
        for key, _img in images:
            image_nos[key] = num; num += 1
        total = num - 1

        objs: dict[int, bytes] = {}
        objs[catalog_no] = b"<< /Type /Catalog /Pages %d 0 R >>" % pages_no
        kids = b" ".join(b"%d 0 R" % p for p in page_nos)
        objs[pages_no] = (b"<< /Type /Pages /Kids [%s] /Count %d >>"
                          % (kids, n_pages))

        res_parts = []
        if fonts:
            font_res = b" ".join(b"%s %d 0 R" % (pdf_name(key), font_nos[key])
                                 for _n, key in fonts)
            res_parts.append(b"/Font << %s >>" % font_res)
        if images:
            img_res = b" ".join(b"%s %d 0 R" % (pdf_name(key), image_nos[key])
                                for key, _img in images)
            res_parts.append(b"/XObject << %s >>" % img_res)
        resources = b"<< %s >>" % b" ".join(res_parts) if res_parts else b"<< >>"

        for i, page in enumerate(self.pages):
            mbox = b"[0 0 %s %s]" % (fmt_num(page.width).encode("ascii"),
                                     fmt_num(page.height).encode("ascii"))
            objs[page_nos[i]] = (
                b"<< /Type /Page /Parent %d 0 R /MediaBox %s "
                b"/Resources %s /Contents %d 0 R >>"
                % (pages_no, mbox, resources, content_nos[i]))
            stream = bytes(page.content)
            objs[content_nos[i]] = (b"<< /Length %d >>\nstream\n%s\nendstream"
                                    % (len(stream), stream))

        for name, key in fonts:
            objs[font_nos[key]] = (
                b"<< /Type /Font /Subtype /Type1 /BaseFont %s "
                b"/Encoding /WinAnsiEncoding >>" % pdf_name(name))

        for key, img in images:
            objs[image_nos[key]] = (
                b"<< /Type /XObject /Subtype /Image /Width %d /Height %d "
                b"/ColorSpace /%s /BitsPerComponent 8 /Filter /%s "
                b"/Length %d >>\nstream\n%s\nendstream"
                % (img.width, img.height, img.colorspace.encode("ascii"),
                   img.filter.encode("ascii"), len(img.data), img.data))

        # --- serialize body, recording each object's byte offset ------------ #
        out = bytearray(_HEADER)
        offsets: dict[int, int] = {}
        for n in range(1, total + 1):
            offsets[n] = len(out)
            out += b"%d 0 obj\n" % n + objs[n] + b"\nendobj\n"

        # --- classic cross-reference table (20-byte records) ---------------- #
        xref_pos = len(out)
        out += b"xref\n0 %d\n" % (total + 1)
        out += b"0000000000 65535 f \n"            # mandatory free object 0
        for n in range(1, total + 1):
            out += b"%010d 00000 n \n" % offsets[n]

        # --- trailer with a deterministic content-hash /ID ------------------ #
        digest = hashlib.sha256(bytes(out)).hexdigest()[:32].upper().encode("ascii")
        out += (b"trailer\n<< /Size %d /Root %d 0 R /ID [<%s> <%s>] >>\n"
                % (total + 1, catalog_no, digest, digest))
        out += b"startxref\n%d\n%%%%EOF\n" % xref_pos
        return bytes(out)
