"""WinAnsi text encoding + PDF token escaping — the from-scratch writer's bytes.

The standard-14 fonts are declared ``/Encoding /WinAnsiEncoding`` and never
embedded, so every show-string must be **single-byte WinAnsi** (ISO 32000-1
Annex D), NOT UTF-8: a Python ``str`` is transcoded here to the byte each glyph
occupies in that encoding.  WinAnsi is Code Page 1252 for every printable glyph
(verified against the canonical byte->glyph table for the whole 0x20-0xFF range),
so the unicode->byte map is built once from the stdlib ``cp1252`` codec — no
third-party dependency, and exact for real text.

Getting this wrong is silent: encoding the em dash "—" or middle dot "·" (which
this app's note headers carry) as UTF-8 or Latin-1 drops or mojibakes the glyph,
and the box can still PASS a pixel diff while shipping a corrupted note.  The
SAME encoder feeds :mod:`metrics` (width measurement) and the content-stream
writer (drawing), so layout math and rendered ink can never diverge.
"""
from __future__ import annotations

# Unicode code point -> WinAnsi byte, derived from the cp1252 codec (== WinAnsi
# for every real glyph).  The five bytes cp1252 leaves undefined (0x81 0x8D 0x8F
# 0x90 0x9D) map to no real character and are simply absent, which is correct —
# nothing should ever encode to them.
_UNI_TO_BYTE: dict[str, int] = {}
for _b in range(256):
    try:
        _ch = bytes([_b]).decode("cp1252")
    except UnicodeDecodeError:
        continue
    _UNI_TO_BYTE.setdefault(_ch, _b)

#: byte drawn for any character outside WinAnsi (mirrors reports._latin's cp1252
#: 'replace' -> '?').  metrics.py charges the SAME glyph's width so measurement
#: and ink stay in lock-step.
FALLBACK_CHAR = "?"
FALLBACK_BYTE = ord(FALLBACK_CHAR)


def to_byte(ch: str) -> int:
    """WinAnsi byte for one character, or the fallback byte if unrepresentable."""
    return _UNI_TO_BYTE.get(ch, FALLBACK_BYTE)


def encode_winansi(text: str) -> bytes:
    """Transcode a str to WinAnsi bytes (out-of-encoding chars -> ``?``)."""
    return bytes(_UNI_TO_BYTE.get(c, FALLBACK_BYTE) for c in text)


# --- PDF token serialization (ISO 32000-1 §7.3.4) --------------------------- #

# A literal string is wrapped in ( ) with these bytes backslash-escaped; every
# other byte (incl. high-bit WinAnsi) may appear raw.  Escaping CR/LF as well
# keeps single-line show-strings on one physical line and the output stable.
_ESCAPE = {
    ord("("): b"\\(",
    ord(")"): b"\\)",
    ord("\\"): b"\\\\",
    0x0D: b"\\r",
    0x0A: b"\\n",
    0x09: b"\\t",
    0x08: b"\\b",
    0x0C: b"\\f",
}


def pdf_string(text: str) -> bytes:
    """A WinAnsi-encoded PDF literal string object: ``(escaped bytes)``."""
    out = bytearray(b"(")
    for byte in encode_winansi(text):
        esc = _ESCAPE.get(byte)
        out += esc if esc is not None else bytes((byte,))
    out += b")"
    return bytes(out)


def pdf_hexstring(text: str) -> bytes:
    """A WinAnsi-encoded PDF hex string object: ``<48656C…>`` (escaping-proof)."""
    return b"<" + encode_winansi(text).hex().upper().encode("ascii") + b">"


_NAME_OK = frozenset(range(0x21, 0x7F)) - {ord(c) for c in "#()<>[]{}/%"}


def pdf_name(name: str) -> bytes:
    """A PDF name object ``/Name`` with ``#xx`` escaping of unusual bytes."""
    out = bytearray(b"/")
    for byte in name.encode("ascii", "replace"):
        out += bytes((byte,)) if byte in _NAME_OK else b"#%02X" % byte
    return bytes(out)
