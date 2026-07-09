"""Raster images for the from-scratch writer — the minimal ISO 32000 slice.

Exactly two encodings, chosen so no image codec is ever written here:

* **JPEG passthrough** (``/DCTDecode``): the source file's bytes ARE the PDF
  stream — professional writers never transcode a JPEG.  Only the pixel size
  and component count are read, straight from the SOF frame header.
* **Raw samples + Flate** (``/FlateDecode``): a fitz pixmap's ``samples`` go
  in untouched (PDF image space and fitz are BOTH top-row-first — any
  "helpful" flip ships upside-down thumbnails), compressed with stdlib zlib
  at a fixed level for byte-reproducible output.

Everything else — PNG parsing, CMYK, alpha/SMask, EXIF rotation, inline
images, exotic filters — is deliberately out (BUILDOUT_PLAN Appendix A skip
list): refusals are loud and typed so the caller's honest fallback runs.
"""
from __future__ import annotations

import hashlib
import struct
import zlib
from typing import NamedTuple

_SOF = {0xC0, 0xC1, 0xC2}                    # baseline / ext-sequential / progressive
_STANDALONE = {0x01} | set(range(0xD0, 0xD8))  # TEM, RSTn — no length word


def jpeg_info(data: bytes) -> tuple[int, int, int]:
    """``(width, height, ncomponents)`` from a JPEG's SOF header (stdlib only)."""
    if data[:2] != b"\xff\xd8":
        raise ValueError("not a JPEG (no SOI marker)")
    i = 2
    while i < len(data) - 9:
        if data[i] != 0xFF:
            raise ValueError("JPEG marker desync")
        while data[i] == 0xFF:               # runs of fill bytes precede the code
            i += 1
        marker = data[i]
        i += 1
        if marker in _STANDALONE:
            continue
        if marker == 0xD9:                   # EOI without a SOF
            break
        (seglen,) = struct.unpack(">H", data[i:i + 2])
        if marker in _SOF:
            precision, h, w, ncomp = struct.unpack(">BHHB", data[i + 2:i + 8])
            if precision != 8:
                raise ValueError(f"unsupported JPEG precision {precision}")
            if ncomp not in (1, 3):
                raise ValueError("CMYK/unknown-component JPEG — out of scope")
            return w, h, ncomp
        if marker == 0xDA:                   # scan data reached, no SOF seen
            break
        i += seglen
    raise ValueError("no usable SOF frame (arithmetic/lossless JPEG?)")


class Image(NamedTuple):
    """One embeddable image: size, colorspace/filter names, stream bytes."""
    width: int
    height: int
    colorspace: str                          # "DeviceRGB" | "DeviceGray"
    filter: str                              # "DCTDecode" | "FlateDecode"
    data: bytes

    @property
    def key(self) -> bytes:
        """Content hash for dedup — the same pixels never embed twice."""
        h = hashlib.sha256()
        h.update(b"%s|%d|%d|%s|" % (self.filter.encode(), self.width,
                                    self.height, self.colorspace.encode()))
        h.update(self.data)
        return h.digest()


def make_image(src) -> Image:
    """Classify a source into an :class:`Image` (or raise a typed refusal).

    Accepts JPEG bytes, a JPEG file path, or a fitz-pixmap-shaped object
    (``samples/width/height/n/alpha/stride``).  Anything else is refused with
    an honest message rather than silently converted.
    """
    if isinstance(src, Image):
        return src
    if isinstance(src, str):
        with open(src, "rb") as f:
            src = f.read()
    if isinstance(src, (bytes, bytearray)):
        data = bytes(src)
        if data[:2] != b"\xff\xd8":
            raise TypeError("bytes input must be a JPEG (PNG is not supported"
                            " — pass the fitz pixmap instead)")
        w, h, ncomp = jpeg_info(data)
        cs = "DeviceRGB" if ncomp == 3 else "DeviceGray"
        return Image(w, h, cs, "DCTDecode", data)
    if hasattr(src, "samples") and hasattr(src, "n") and hasattr(src, "width"):
        if getattr(src, "alpha", 0):
            raise ValueError("alpha pixmaps are unsupported — render with"
                             " alpha=False")
        n = int(src.n)
        if n not in (1, 3):
            raise ValueError(f"unsupported pixmap with n={n} (want gray or RGB)")
        w, h = int(src.width), int(src.height)
        samples = bytes(src.samples)
        stride = int(getattr(src, "stride", w * n))
        if stride != w * n:                  # padded rows would shear the image
            samples = b"".join(samples[r * stride:r * stride + w * n]
                               for r in range(h))
        cs = "DeviceRGB" if n == 3 else "DeviceGray"
        return Image(w, h, cs, "FlateDecode", zlib.compress(samples, 9))
    raise TypeError("drawImage wants JPEG bytes/path or an alpha-free"
                    " gray/RGB pixmap")
