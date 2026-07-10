"""The Shuttle, writer half — object-graph importer + generic serializer.

minipdf's ``Document`` stays the closed-world *authoring* model; this is the
second, generic emitter that page SURGERY needs: deep-copy pages out of any
:class:`parse.MiniReader` (renumbering the object graph), assemble a new
page tree + optional outline, and serialize with the same byte discipline
as ``document.py`` — classic xref, 20-byte records, exact ``/Length``,
content-hash ``/ID``, and **no /Info, ever** (the writer structurally
cannot emit it; NDA posture, invariant 7).

The two load-bearing import rules (each prevents a graph bomb):

* ``/Parent`` on a page-like dict is CUT — otherwise one copied page's
  ``/Parent → /Kids`` drags the whole source file in;
* a reference to a pages node (any dict with ``/Kids``) becomes ``null``,
  and a reference to a page dict resolves through the page map (selected
  pages) or ``null`` — a GoTo ``/Dest`` must never drag in unselected
  pages.  A nulled ``/Dest`` is a dead link every viewer tolerates.

Copied streams are never re-encoded or re-formatted — the raw bytes
travel, so untouched pages stay pixel-identical by construction.
"""
from __future__ import annotations

import hashlib

from .content import fmt_num
from .encoding import pdf_name
from .parse import MiniReader, Name, PdfError, Ref, Stream

_HEADER = b"%PDF-1.6\n%\xe2\xe3\xcf\xd3\n"


class WriterPage:
    """Handle onto one imported page inside a :class:`MiniWriter`."""

    def __init__(self, writer: "MiniWriter", num: int):
        self._w = writer
        self.num = num

    @property
    def dict(self) -> dict:
        return self._w._objs[self.num]

    def rotate(self, deg: int) -> "WriterPage":
        d = self.dict
        cur = d.get("/Rotate", 0)
        cur = int(cur) if isinstance(cur, (int, float)) else 0
        d["/Rotate"] = (cur + int(deg)) % 360
        return self


class MiniWriter:
    """Collects imported pages, then serializes one classic-xref file."""

    def __init__(self):
        self._objs: dict = {}          # dst num -> value
        self._next = 1
        self._page_nums: list = []     # dst page numbers, in order
        self._pagemap: dict = {}       # id(src page dict) -> dst num
        self._memo: dict = {}          # (id(src reader), src num) -> dst num
        self._outline: list = []       # (title, 0-based page index)

    # -- import ------------------------------------------------------------- #

    def _alloc(self, val=None) -> int:
        n = self._next
        self._next += 1
        self._objs[n] = val
        return n

    def add_page(self, page) -> WriterPage:
        """Deep-import one :class:`parse.PageProxy`; returns the handle."""
        src = page._doc
        dst = self._alloc()
        self._pagemap[id(page.dict)] = dst
        d = self._import_dict(src, page.dict)
        # the copy leaves its source tree, so inherited attributes must
        # materialize onto the page itself or they are silently lost
        for k in ("/Resources", "/MediaBox", "/CropBox", "/Rotate"):
            if k not in d and k in page.inherited:
                d[k] = self._import_val(src, page.inherited[k])
        self._objs[dst] = d
        self._page_nums.append(dst)
        return WriterPage(self, dst)

    def add_outline_item(self, title: str, page_index: int):
        self._outline.append((str(title), int(page_index)))

    @property
    def pages(self) -> list:
        return [WriterPage(self, n) for n in self._page_nums]

    def _import_val(self, src: MiniReader, v):
        if isinstance(v, Ref):
            tgt = src.resolve(v)
            td = tgt.dict if isinstance(tgt, Stream) else tgt
            if isinstance(td, dict):
                if "/Kids" in td:
                    return None            # a pages node: NEVER climb the tree
                if td.get("/Type") == "/Page":
                    dn = self._pagemap.get(id(td))
                    return Ref(dn, 0) if dn else None
            key = (id(src), v.num)
            if key in self._memo:
                return Ref(self._memo[key], 0)
            dn = self._alloc()
            self._memo[key] = dn           # memo BEFORE recursing: cycle-safe
            self._objs[dn] = self._import_val(src, tgt)
            return Ref(dn, 0)
        if isinstance(v, Stream):          # dict rebuilt, RAW bytes untouched
            return Stream(self._import_dict(src, v.dict), v.raw)
        if isinstance(v, dict):
            return self._import_dict(src, v)
        if isinstance(v, list):
            return [self._import_val(src, x) for x in v]
        return v

    def _import_dict(self, src: MiniReader, d: dict) -> dict:
        pagelike = d.get("/Type") == "/Page" or "/Contents" in d
        out: dict = {}
        for k, x in d.items():
            if k == "/Parent" and pagelike:
                continue                   # the /Parent cut (see module doc)
            out[k] = self._import_val(src, x)
        return out

    def add_stream(self, raw: bytes) -> Ref:
        """A new bare stream object (the overlay compositor's building block)."""
        return Ref(self._alloc(Stream({}, bytes(raw))), 0)

    def resolve(self, v):
        while isinstance(v, Ref):
            v = self._objs.get(v.num)
        return v

    # -- serialize ----------------------------------------------------------- #

    def write(self, f) -> None:
        """Assemble + serialize.  Every output must survive its own STRICT
        re-parse (recovery disabled) before a byte reaches ``f`` — fitz and
        pypdf silently rebuild broken xrefs and would hide a writer bug."""
        if not self._page_nums:
            raise PdfError("writer has no pages")
        kids = [Ref(n, 0) for n in self._page_nums]
        pages_no = self._alloc({"/Type": Name("/Pages"), "/Kids": kids,
                                "/Count": len(kids)})
        for n in self._page_nums:
            d = self._objs[n]
            d["/Type"] = Name("/Page")
            d["/Parent"] = Ref(pages_no, 0)
        cat = {"/Type": Name("/Catalog"), "/Pages": Ref(pages_no, 0)}
        if self._outline:
            root_no = self._alloc()
            item_nos = [self._alloc() for _ in self._outline]
            for i, (title, pidx) in enumerate(self._outline):
                if not (0 <= pidx < len(self._page_nums)):
                    raise PdfError(f"outline target page {pidx} out of range")
                item = {"/Title": title, "/Parent": Ref(root_no, 0),
                        "/Dest": [Ref(self._page_nums[pidx], 0), Name("/Fit")]}
                if i:
                    item["/Prev"] = Ref(item_nos[i - 1], 0)
                if i + 1 < len(item_nos):
                    item["/Next"] = Ref(item_nos[i + 1], 0)
                self._objs[item_nos[i]] = item
            self._objs[root_no] = {"/Type": Name("/Outlines"),
                                   "/First": Ref(item_nos[0], 0),
                                   "/Last": Ref(item_nos[-1], 0),
                                   "/Count": len(item_nos)}
            cat["/Outlines"] = Ref(root_no, 0)
        cat_no = self._alloc(cat)

        out = bytearray(_HEADER)
        offsets: dict = {}
        total = self._next - 1
        for n in range(1, total + 1):
            offsets[n] = len(out)
            out += b"%d 0 obj\n" % n
            out += _serialize(self._objs.get(n))
            out += b"\nendobj\n"
        xref_pos = len(out)
        out += b"xref\n0 %d\n" % (total + 1)
        out += b"0000000000 65535 f \n"
        for n in range(1, total + 1):
            out += b"%010d 00000 n \n" % offsets[n]
        digest = hashlib.sha256(bytes(out)).hexdigest()[:32].upper()
        out += (b"trailer\n<< /Size %d /Root %d 0 R /ID [<%s> <%s>] >>\n"
                % (total + 1, cat_no, digest.encode(), digest.encode()))
        out += b"startxref\n%d\n%%%%EOF\n" % xref_pos

        data = bytes(out)
        check = MiniReader(data, strict=True)      # the strict self-check
        if len(check.pages) != len(self._page_nums):
            raise PdfError("self-check: page count mismatch after write")
        f.write(data)


def _pdf_text(s: str) -> bytes:
    """A text string (outline titles): latin-1 when it fits, else UTF-16BE
    with BOM — always emitted as a hex string (deterministic, escape-proof)."""
    try:
        raw = s.encode("latin-1")
    except UnicodeEncodeError:
        raw = b"\xfe\xff" + s.encode("utf-16-be")
    return b"<" + raw.hex().upper().encode("ascii") + b">"


def _serialize(v) -> bytes:
    if v is None:
        return b"null"
    if isinstance(v, bool):
        return b"true" if v else b"false"
    if isinstance(v, Name):
        return pdf_name(str(v)[1:])
    if isinstance(v, (int, float)):
        return fmt_num(v).encode("ascii")
    if isinstance(v, Ref):
        return b"%d 0 R" % v.num
    if isinstance(v, bytes):                  # parsed strings: always hex
        return b"<" + v.hex().upper().encode("ascii") + b">"
    if isinstance(v, str):
        return _pdf_text(v)
    if isinstance(v, list):
        return b"[ " + b" ".join(_serialize(x) for x in v) + b" ]"
    if isinstance(v, Stream):
        d = dict(v.dict)
        d["/Length"] = len(v.raw)
        return _serialize(d) + b"\nstream\n" + v.raw + b"\nendstream"
    if isinstance(v, dict):
        parts = []
        for k in sorted(v):                   # sorted keys: byte-deterministic
            parts.append(pdf_name(str(k)[1:] if k.startswith("/") else k)
                         + b" " + _serialize(v[k]))
        return b"<< " + b" ".join(parts) + b" >>"
    raise PdfError(f"unserializable value {type(v).__name__}")
