"""The Shuttle, reader half — a lenient from-scratch PDF parser.

minipdf has owned the WRITER half of PDF since v4.8.0; this module is the
reader that lets pypdf leave the runtime the way reportlab and Tesseract
did.  Scope is the call-site inventory, nothing more: open a file, walk the
page tree with inheritance, expose boxes/rotation/annots, detect
``/Encrypt`` — page *content* is never decoded (fitz owns rendering and
text; copied streams travel as raw bytes).

The leniency canon every serious parser converges on (each rule earned by
a real producer quirk):

* the xref is a HINT — a missed offset or absent ``startxref`` triggers a
  full-file scan rebuild (last definition wins = newest revision);
* ``/Length`` is a hint — read then VERIFY ``endstream`` follows, scan on
  mismatch (never scan first: real content contains those bytes);
* offsets are relative to the ``%PDF`` header, which may not be at byte 0;
* newest revision wins across the ``/Prev`` chain (first-seen-wins per
  object walking newest→oldest, visited-set loop guard);
* hybrid files: the classic section lists objstm objects as free — read
  the ``/XRefStm`` section BEFORE the classic ``/Prev``;
* ``/MediaBox``/``/CropBox``/``/Rotate``/``/Resources`` inherit down the
  page tree; corners normalize; crop intersects media;
* never trust ``/Count`` or ``/Type`` — enumerate by walking ``/Kids``.

No crypto lives here (and none ever will — the facade unlocks
blank-password files through fitz, which the runtime already carries).
"""
from __future__ import annotations

import re
import zlib
from typing import NamedTuple

SIZE_CAP = 500 * 1024 * 1024
_MAX_DEPTH = 256                    # nesting cap: broken files raise cleanly
_MAX_REVISIONS = 512


class PdfError(ValueError):
    """Any structural failure the lenient path could not recover from."""


class Name(str):
    """A PDF name object; subclass so /Name and (string) stay distinct."""
    __slots__ = ()


class Ref(NamedTuple):
    num: int
    gen: int


class Stream:
    """A stream object: its dict + the RAW (undecoded) body bytes."""
    __slots__ = ("dict", "raw")

    def __init__(self, d: dict, raw: bytes):
        self.dict = d
        self.raw = raw

    def __repr__(self):
        return f"Stream({self.dict!r}, {len(self.raw)} bytes)"


_WS = b"\x00\t\n\x0c\r "
_DELIM = b"()<>[]{}/%"
_NUMCHARS = b"+-.0123456789eE"


# --------------------------------------------------------------------------- #
#  Lexer / object parser                                                       #
# --------------------------------------------------------------------------- #

def _skip_ws(b: bytes, i: int) -> int:
    n = len(b)
    while i < n:
        c = b[i]
        if c in _WS:
            i += 1
        elif c == 0x25:                       # '%' comment to EOL
            while i < n and b[i] not in b"\r\n":
                i += 1
        else:
            break
    return i


def _token(b: bytes, i: int) -> tuple:
    """One bare token (keyword/number run) -> (bytes, new_i)."""
    i = _skip_ws(b, i)
    j = i
    n = len(b)
    while j < n and b[j] not in _WS and b[j] not in _DELIM:
        j += 1
    return b[i:j], j


def _parse_name(b: bytes, i: int) -> tuple:
    j = i + 1                                 # past '/'
    n = len(b)
    out = bytearray()
    while j < n and b[j] not in _WS and b[j] not in _DELIM:
        if b[j] == 0x23 and j + 2 < n:        # '#xx' escape
            try:
                out.append(int(b[j + 1:j + 3], 16))
                j += 3
                continue
            except ValueError:
                pass                          # invalid '#': keep literally
        out.append(b[j])
        j += 1
    # names carry their leading slash ("/Root") — matches the pypdf-shaped
    # surface the call sites use and keeps /Name distinct from (string)
    return Name("/" + out.decode("latin-1")), j


_STR_ESC = {0x6E: b"\n", 0x72: b"\r", 0x74: b"\t", 0x62: b"\b", 0x66: b"\f",
            0x28: b"(", 0x29: b")", 0x5C: b"\\"}


def _parse_litstring(b: bytes, i: int) -> tuple:
    j = i + 1                                 # past '('
    n = len(b)
    depth = 1
    out = bytearray()
    while j < n:
        c = b[j]
        if c == 0x5C:                         # backslash
            j += 1
            if j >= n:
                break
            e = b[j]
            if e in _STR_ESC:
                out += _STR_ESC[e]
                j += 1
            elif 0x30 <= e <= 0x37:           # \ooo — 1-3 octal digits
                k = j
                while k < min(j + 3, n) and 0x30 <= b[k] <= 0x37:
                    k += 1
                out.append(int(b[j:k], 8) & 0xFF)
                j = k
            elif e in b"\r\n":                # backslash-EOL: continuation
                j += 1
                if e == 0x0D and j < n and b[j] == 0x0A:
                    j += 1
            else:                             # unknown escape: char literally
                out.append(e)
                j += 1
        elif c == 0x28:
            depth += 1
            out.append(c)
            j += 1
        elif c == 0x29:
            depth -= 1
            if depth == 0:
                return bytes(out), j + 1
            out.append(c)
            j += 1
        elif c == 0x0D:                       # bare CR / CRLF -> LF
            out.append(0x0A)
            j += 1
            if j < n and b[j] == 0x0A:
                j += 1
        else:
            out.append(c)
            j += 1
    raise PdfError("unterminated literal string")


def _parse_hexstring(b: bytes, i: int) -> tuple:
    j = i + 1                                 # past '<'
    n = len(b)
    hexd = bytearray()
    while j < n and b[j] != 0x3E:             # '>'
        c = b[j]
        if c in b"0123456789abcdefABCDEF":
            hexd.append(c)
        j += 1
    if len(hexd) % 2:
        hexd.append(0x30)                     # odd trailing digit: pad with 0
    return bytes.fromhex(hexd.decode("ascii")), j + 1


def _parse_number(tok: bytes):
    try:
        return int(tok)
    except ValueError:
        pass
    try:
        return float(tok)                     # '1.', '+.5', '1e-3'
    except ValueError:
        return 0                              # '--3' & friends: warn-level junk


def parse_object(b: bytes, i: int, depth: int = 0) -> tuple:
    """Parse one object at ``i`` -> ``(value, new_i)``.

    Indirect references need two-token lookahead: ``12 0 R`` must not
    surface as three tokens.  Streams are handled by the caller (they need
    the /Length resolver).
    """
    if depth > _MAX_DEPTH:
        raise PdfError("object nesting too deep")
    i = _skip_ws(b, i)
    if i >= len(b):
        raise PdfError("unexpected end of data")
    c = b[i]
    if c == 0x2F:                             # '/'
        return _parse_name(b, i)
    if c == 0x28:                             # '('
        return _parse_litstring(b, i)
    if c == 0x3C:                             # '<' or '<<'
        if b[i:i + 2] == b"<<":
            return _parse_dict(b, i, depth)
        return _parse_hexstring(b, i)
    if c == 0x5B:                             # '['
        i += 1
        out = []
        while True:
            i = _skip_ws(b, i)
            if i >= len(b):
                raise PdfError("unterminated array")
            if b[i] == 0x5D:
                return out, i + 1
            v, i = parse_object(b, i, depth + 1)
            out.append(v)
    tok, j = _token(b, i)
    if not tok:
        raise PdfError(f"lexing stalled at byte {i}")
    if tok == b"true":
        return True, j
    if tok == b"false":
        return False, j
    if tok == b"null":
        return None, j
    if tok[0:1].isdigit() or tok[0] in b"+-.":
        val = _parse_number(tok)
        if isinstance(val, int) and val >= 0:
            # lookahead for 'G R' (reference) — two tokens, both cheap
            t2, k2 = _token(b, j)
            if t2.isdigit():
                t3, k3 = _token(b, k2)
                if t3 == b"R":
                    return Ref(val, int(t2)), k3
        return val, j
    raise PdfError(f"unknown token {tok!r} at byte {i}")


def _parse_dict(b: bytes, i: int, depth: int) -> tuple:
    i += 2                                    # past '<<'
    d: dict = {}
    n = len(b)
    while True:
        i = _skip_ws(b, i)
        if i >= n:
            raise PdfError("unterminated dictionary")
        if b[i:i + 2] == b">>":
            return d, i + 2
        if b[i] == 0x2F:
            key, i = _parse_name(b, i)
            val, i = parse_object(b, i, depth + 1)
            d[str(key)] = val                 # duplicate key: last wins
        else:                                 # leniency: skip garbage token
            _, i2 = _token(b, i)
            i = i2 if i2 > i else i + 1


def _read_stream_body(b: bytes, i: int, d: dict, resolve) -> tuple:
    """``i`` just past the ``stream`` keyword -> ``(raw, new_i)``.

    /Length first (resolving indirection), then VERIFY ``endstream``
    follows; on mismatch scan — never scan first, page content legally
    contains the bytes ``endstream``.
    """
    if b[i:i + 2] == b"\r\n":
        i += 2
    elif b[i:i + 1] in (b"\n", b"\r"):
        i += 1
    ln = d.get("/Length")
    try:
        ln = resolve(ln) if isinstance(ln, Ref) else ln
        ln = int(ln)
    except Exception:
        ln = -1
    if ln >= 0 and i + ln <= len(b):
        j = _skip_ws(b, i + ln)
        if b[j:j + 9] == b"endstream":
            return b[i:i + ln], j + 9
    k = b.find(b"endstream", i)               # /Length lied: scanned extent
    if k < 0:
        raise PdfError("stream without endstream")
    raw = b[i:k]
    if raw.endswith(b"\r\n"):
        raw = raw[:-2]
    elif raw.endswith((b"\n", b"\r")):
        raw = raw[:-1]
    return raw, k + 9


# --------------------------------------------------------------------------- #
#  Filters: Flate + PNG predictors (the only structural decoders needed)      #
# --------------------------------------------------------------------------- #

def inflate(data: bytes) -> bytes:
    """zlib inflate tolerating bogus headers and truncated tails."""
    try:
        d = zlib.decompressobj()
        return d.decompress(data) + d.flush()
    except zlib.error:
        pass
    try:
        d = zlib.decompressobj(-15)           # raw deflate (bogus zlib header)
        return d.decompress(data) + d.flush()
    except zlib.error:
        pass
    d = zlib.decompressobj()                  # salvage a truncated tail
    try:
        out = d.decompress(data)
    except zlib.error as e:
        raise PdfError(f"undecodable Flate stream: {e}") from None
    return out


def _paeth(a: int, bb: int, c: int) -> int:
    p = a + bb - c
    pa, pb, pc = abs(p - a), abs(p - bb), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return bb if pb <= pc else c


def unpredict(data: bytes, parms: dict) -> bytes:
    """Undo PNG row predictors (/Predictor >= 10) on decoded stream data."""
    pred = int(parms.get("/Predictor", 1) or 1)
    if pred < 10:
        if pred == 2:
            raise PdfError("TIFF predictor 2 unsupported on structural stream")
        return data
    cols = (int(parms.get("/Columns", 1) or 1)
            * int(parms.get("/Colors", 1) or 1)
            * int(parms.get("/BitsPerComponent", 8) or 8)) // 8
    rowlen = cols + 1
    out = bytearray()
    prev = bytes(cols)
    for r in range(0, len(data) - rowlen + 1, rowlen):
        ft = data[r]
        cur = bytearray(data[r + 1:r + rowlen])
        for i in range(cols):
            a = cur[i - 1] if i else 0
            up = prev[i]
            c = prev[i - 1] if i else 0
            if ft == 1:
                cur[i] = (cur[i] + a) & 0xFF
            elif ft == 2:
                cur[i] = (cur[i] + up) & 0xFF
            elif ft == 3:
                cur[i] = (cur[i] + (a + up) // 2) & 0xFF
            elif ft == 4:
                cur[i] = (cur[i] + _paeth(a, up, c)) & 0xFF
        prev = bytes(cur)
        out += prev
    return bytes(out)


def decode_stream(st: Stream, resolve) -> bytes:
    """Decoded bytes of a STRUCTURAL stream (xref/objstm/own overlay).

    Flate (+ optional PNG predictor) or no filter only; anything else on a
    structural stream is a loud error (it does not occur in practice).
    """
    filt = resolve(st.dict.get("/Filter"))
    parms = resolve(st.dict.get("/DecodeParms")) or {}
    if isinstance(filt, list):
        if len(filt) > 1:
            raise PdfError(f"filter chain unsupported: {filt}")
        filt = filt[0] if filt else None
    if isinstance(parms, list):
        parms = parms[0] if parms else {}
    if filt is None:
        return st.raw
    if str(filt) != "/FlateDecode":
        raise PdfError(f"unsupported structural filter {filt}")
    out = inflate(st.raw)
    if isinstance(parms, dict) and parms.get("/Predictor"):
        out = unpredict(out, {k: resolve(v) for k, v in parms.items()})
    return out


# --------------------------------------------------------------------------- #
#  Boxes / page proxy                                                          #
# --------------------------------------------------------------------------- #

class Box:
    """A normalized rectangle with pypdf-shaped accessors (floats)."""
    __slots__ = ("left", "bottom", "right", "top")

    def __init__(self, arr):
        x0, y0, x1, y1 = (float(v) for v in arr[:4])
        self.left, self.right = min(x0, x1), max(x0, x1)
        self.bottom, self.top = min(y0, y1), max(y0, y1)

    @property
    def width(self) -> float:
        return self.right - self.left

    @property
    def height(self) -> float:
        return self.top - self.bottom

    def as_list(self) -> list:
        return [self.left, self.bottom, self.right, self.top]


_INHERIT = ("/Resources", "/MediaBox", "/CropBox", "/Rotate")


class PageProxy:
    """One page: its dict + inherited attributes, resolved lazily."""

    def __init__(self, doc, ref, d: dict, inherited: dict):
        self._doc = doc
        self.ref = ref                        # Ref of the page object (or None)
        self.dict = d
        self.inherited = inherited

    def _attr(self, key):
        v = self.dict.get(key, self.inherited.get(key))
        return self._doc.resolve(v)

    @property
    def mediabox(self) -> Box:
        mb = self._attr("/MediaBox")
        if not isinstance(mb, list) or len(mb) < 4:
            return Box([0, 0, 612, 792])      # letter default: never crash
        return Box([self._doc.resolve(v) for v in mb])

    @property
    def cropbox(self) -> Box:
        cb = self._attr("/CropBox")
        mb = self.mediabox
        if not isinstance(cb, list) or len(cb) < 4:
            return mb
        c = Box([self._doc.resolve(v) for v in cb])
        # intersect with media — out-of-bounds crops occur in the wild
        left, right = max(c.left, mb.left), min(c.right, mb.right)
        bottom, top = max(c.bottom, mb.bottom), min(c.top, mb.top)
        if left >= right or bottom >= top:
            return mb
        return Box([left, bottom, right, top])

    @property
    def rotation(self) -> int:
        r = self._attr("/Rotate")
        try:
            r = int(r) % 360
        except (TypeError, ValueError):
            return 0
        return r - (r % 90)                   # snap to a multiple of 90

    def get(self, key, default=None):
        v = self.dict.get(key, self.inherited.get(key) if key in _INHERIT
                          else None)
        return default if v is None else self._doc.resolve(v)

    def __contains__(self, key) -> bool:
        return key in self.dict or (key in _INHERIT and key in self.inherited)


# --------------------------------------------------------------------------- #
#  The document reader                                                         #
# --------------------------------------------------------------------------- #

_OBJ_RE = re.compile(rb"(\d{1,10})\s+(\d{1,5})\s+obj\b")


class MiniReader:
    """Lenient reader over one in-memory PDF.

    ``strict=True`` disables every recovery path — the self-check mode for
    files Planloom itself wrote (fitz/pypdf silently rebuild broken xrefs
    and hide writer bugs; this reader refuses to).
    """

    def __init__(self, data: bytes, strict: bool = False):
        if len(data) > SIZE_CAP:
            raise PdfError(f"file exceeds the {SIZE_CAP // 2**20} MB cap")
        self.buf = data
        self.strict = strict
        self.repaired = False
        head = data[:1024].find(b"%PDF")
        if head < 0 and strict:
            raise PdfError("no %PDF header")
        self.base = max(head, 0)
        self.xref: dict = {}                  # num -> ("n", off) | ("o", s, k)
        self.trailer: dict = {}
        self._cache: dict = {}
        self._objstm: dict = {}               # objstm num -> {objnum: value}
        self._pages: list = []
        try:
            self._load_chain()
        except PdfError:
            if strict:
                raise
            self._rebuild()
        if "/Root" not in self.trailer:
            if strict:
                raise PdfError("no /Root in trailer")
            self._rebuild()
            if "/Root" not in self.trailer:
                raise PdfError("no document catalog found")
        self._walk_pages()

    # -- object access ---------------------------------------------------- #

    def resolve(self, v):
        seen = 0
        while isinstance(v, Ref):
            v = self.get(v.num)
            seen += 1
            if seen > 64:
                raise PdfError("reference chain loop")
        return v

    def get(self, num: int):
        if num in self._cache:
            return self._cache[num]
        ent = self.xref.get(num)
        if ent is None:
            if not self.strict and not self.repaired:
                self._rebuild()
                ent = self.xref.get(num)
            if ent is None:
                return None                   # free / absent -> null
        try:
            val = self._fetch(num, ent)
        except PdfError:
            if self.strict or self.repaired:
                raise
            self._rebuild()
            ent = self.xref.get(num)
            if ent is None:
                return None
            val = self._fetch(num, ent)
        self._cache[num] = val
        return val

    def _fetch(self, num: int, ent):
        if ent[0] == "o":
            return self._from_objstm(ent[1], num)
        b = self.buf
        # offsets are nominally absolute; a junk-prefixed file (mail/print-
        # spool wrappers before %PDF) wrote them relative to the header —
        # try absolute first, then +header_pos
        for delta in ((0, self.base) if self.base else (0,)):
            i = _skip_ws(b, ent[1] + delta)
            t1, j = _token(b, i)
            t2, j = _token(b, j)
            t3, j = _token(b, j)
            if t1.isdigit() and int(t1) == num and t3 == b"obj":
                break
        else:
            raise PdfError(f"object {num} not at xref offset")
        val, j = parse_object(b, j)
        if isinstance(val, dict):
            k = _skip_ws(b, j)
            if b[k:k + 6] == b"stream":
                raw, j = _read_stream_body(b, k + 6, val, self.resolve)
                return Stream(val, raw)
        return val

    def _from_objstm(self, stm_num: int, want: int):
        if stm_num not in self._objstm:
            st = self.get(stm_num)
            if not isinstance(st, Stream):
                raise PdfError(f"object stream {stm_num} missing")
            data = decode_stream(st, self.resolve)
            n = int(self.resolve(st.dict.get("/N", 0)) or 0)
            first = int(self.resolve(st.dict.get("/First", 0)) or 0)
            pairs = []
            i = 0
            for _ in range(n):
                t1, i = _token(data, i)
                t2, i = _token(data, i)
                pairs.append((int(t1), int(t2)))
            objs = {}
            for objnum, off in pairs:         # cache ALL on first touch —
                v, _ = parse_object(data, first + off)   # never O(n²) inflate
                objs[objnum] = v
            self._objstm[stm_num] = objs
        objs = self._objstm[stm_num]
        if want not in objs:
            raise PdfError(f"object {want} not in object stream {stm_num}")
        return objs[want]

    # -- xref loading ------------------------------------------------------ #

    def _merge_entry(self, num: int, ent):
        if num not in self.xref:              # first seen (newest) wins
            self.xref[num] = ent

    def _merge_trailer(self, d: dict):
        for k, v in d.items():
            if k not in self.trailer:
                self.trailer[k] = v

    def _load_chain(self):
        tail = self.buf[-2048:]
        k = tail.rfind(b"startxref")
        if k < 0:
            raise PdfError("no startxref")
        tok, _ = _token(tail, k + 9)
        try:
            off = int(tok)
        except ValueError:
            raise PdfError("bad startxref") from None
        seen: set = set()
        for _ in range(_MAX_REVISIONS):
            if off is None or off in seen:
                return
            seen.add(off)
            off = self._load_section(off)
        raise PdfError("xref chain too long")

    def _load_section(self, off: int):
        b = self.buf
        i = _skip_ws(b, off)
        if self.base and b[i:i + 4] != b"xref" \
                and not _OBJ_RE.match(b, i):
            i = _skip_ws(b, off + self.base)  # junk-prefixed file
        if b[i:i + 4] == b"xref":
            return self._load_classic(i + 4)
        # else an xref STREAM: 'N G obj' then the stream object
        t1, j = _token(b, i)
        t2, j = _token(b, j)
        t3, j = _token(b, j)
        if not (t1.isdigit() and t3 == b"obj"):
            raise PdfError("xref offset points at garbage")
        d, j = parse_object(b, j)
        if not isinstance(d, dict):
            raise PdfError("xref stream is not a stream")
        k = _skip_ws(b, j)
        if b[k:k + 6] != b"stream":
            raise PdfError("xref stream body missing")
        raw, _ = _read_stream_body(b, k + 6, d, lambda v: v)
        self._load_xref_stream(Stream(d, raw))
        self._merge_trailer(d)
        prev = d.get("/Prev")
        return int(prev) if isinstance(prev, (int, float)) else None

    def _load_classic(self, i: int):
        b = self.buf
        while True:                           # tokenwise: survives 19/21-byte
            i = _skip_ws(b, i)                # rows and stray spaces
            if b[i:i + 7] == b"trailer":
                d, _ = parse_object(b, i + 7)
                if not isinstance(d, dict):
                    raise PdfError("bad trailer dict")
                # hybrid file: the stream section supplies objstm objects
                # the classic table deliberately lists as free — read it
                # BEFORE honoring /Prev (first-seen-wins does the rest)
                xs = d.get("/XRefStm")
                if isinstance(xs, (int, float)):
                    try:
                        self._load_section(int(xs))
                    except PdfError:
                        pass
                self._merge_trailer(d)
                prev = d.get("/Prev")
                return int(prev) if isinstance(prev, (int, float)) else None
            t1, j = _token(b, i)
            t2, j2 = _token(b, j)
            if not (t1.isdigit() and t2.isdigit()):
                raise PdfError("bad xref subsection header")
            start, count = int(t1), int(t2)
            i = j2
            for k in range(count):
                o, i = _token(b, i)
                g, i = _token(b, i)
                ty, i = _token(b, i)
                if ty == b"n" and o.isdigit():
                    self._merge_entry(start + k, ("n", int(o)))
                # type f (free) entries are ignored — nobody walks the list

    def _load_xref_stream(self, st: Stream):
        data = decode_stream(st, self.resolve)
        w = [int(x) for x in st.dict.get("/W", [1, 1, 1])]
        if len(w) < 3:
            w = (w + [0, 0, 0])[:3]
        size = int(st.dict.get("/Size", 0) or 0)
        index = st.dict.get("/Index", [0, size])
        rowlen = sum(w)
        if rowlen <= 0:
            raise PdfError("bad /W in xref stream")
        pos = 0
        for s in range(0, len(index) - 1, 2):
            start, count = int(index[s]), int(index[s + 1])
            for k in range(count):
                row = data[pos:pos + rowlen]
                pos += rowlen
                if len(row) < rowlen:
                    return
                f = []
                q = 0
                for width in w:
                    f.append(int.from_bytes(row[q:q + width], "big")
                             if width else None)
                    q += width
                ftype = 1 if w[0] == 0 else f[0]
                if ftype == 1:
                    self._merge_entry(start + k, ("n", f[1]))
                elif ftype == 2:
                    self._merge_entry(start + k, ("o", f[1], f[2] or 0))
                # type 0 (free) and unknown types: ignored per spec

    # -- recovery ----------------------------------------------------------- #

    def _rebuild(self):
        """Full-file scan: object headers (LAST wins = newest revision) +
        the last trailer carrying /Root, else a /Type /Catalog scan."""
        if self.strict:
            raise PdfError("recovery disabled (strict)")
        self.repaired = True
        self._cache.clear()
        self._objstm.clear()
        table: dict = {}
        for m in _OBJ_RE.finditer(self.buf):
            table[int(m.group(1))] = ("n", m.start())
        if not table:
            raise PdfError("no objects found in file")
        self.xref = table
        for m in re.finditer(rb"trailer", self.buf):
            try:
                d, _ = parse_object(self.buf, m.end())
            except PdfError:
                continue
            if isinstance(d, dict) and "/Root" in d:
                self.trailer = dict(d)
        if "/Root" not in self.trailer:
            for num in sorted(table):
                try:
                    v = self.get(num)
                except PdfError:
                    continue
                d = v.dict if isinstance(v, Stream) else v
                if isinstance(d, dict) and d.get("/Type") == "/Catalog":
                    self.trailer["/Root"] = Ref(num, 0)
                    break

    # -- page tree ----------------------------------------------------------- #

    @property
    def is_encrypted(self) -> bool:
        return "/Encrypt" in self.trailer

    @property
    def pages(self) -> list:
        return self._pages

    def _walk_pages(self):
        root = self.resolve(self.trailer.get("/Root"))
        if isinstance(root, Stream):
            root = root.dict
        if not isinstance(root, dict):
            raise PdfError("catalog unreadable")
        out: list = []
        seen: set = set()

        def walk(ref, inherited, depth):
            if depth > _MAX_DEPTH:
                return
            node = self.resolve(ref)
            if isinstance(node, Stream):
                node = node.dict
            if not isinstance(node, dict):
                return
            key = ref.num if isinstance(ref, Ref) else id(node)
            if key in seen:                   # /Kids cycles exist in the wild
                return
            seen.add(key)
            inh = dict(inherited)
            for k in _INHERIT:
                if k in node:
                    inh[k] = node[k]
            kids = self.resolve(node.get("/Kids"))
            if isinstance(kids, list):        # a Pages node even if /Type is
                for kid in kids:              # missing — structure over type
                    walk(kid, inh, depth + 1)
            else:
                out.append(PageProxy(
                    self, ref if isinstance(ref, Ref) else None, node,
                    {k: v for k, v in inh.items() if k not in node}))

        walk(root.get("/Pages"), {}, 0)
        if not out and not self.strict and not self.repaired:
            self._rebuild()
            self._walk_pages()
            return
        self._pages = out


def read_pdf(source, strict: bool = False) -> MiniReader:
    """Open a path or binary stream -> :class:`MiniReader`."""
    if isinstance(source, (bytes, bytearray)):
        data = bytes(source)
    elif hasattr(source, "read"):
        data = source.read()
    else:
        with open(source, "rb") as fh:
            data = fh.read(SIZE_CAP + 1)
    return MiniReader(data, strict=strict)
