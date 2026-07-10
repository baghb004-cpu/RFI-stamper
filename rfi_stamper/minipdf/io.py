"""The Shuttle, facade — the pypdf-shaped surface the call sites use.

``Reader``/``Writer`` expose exactly the inventory ``merge.py``,
``stamp.py``, ``reports.py`` and ``pdfdoctor.py`` need, so the runtime can
switch backends on the ``PLOOM_PDF_IO`` env var (``mini`` is the shipped
default; ``pypdf`` re-enables the retired library as a dev-box parity
oracle — the reportlab pattern).

Encryption policy: DETECT precisely (the trailer's ``/Encrypt`` never lies,
unlike fitz, which silently opens owner-locked files), but never implement
crypto.  ``decrypt("")`` re-saves through fitz — which the runtime already
carries — with encryption stripped, then re-parses; a real user password
fails exactly like today's pypdf path.
"""
from __future__ import annotations

from .graph import MiniWriter
from .pagemerge import add_overlay, overlay_ctm      # noqa: F401 (re-export)
from .parse import PdfError, read_pdf                # noqa: F401 (re-export)


class Reader:
    """Open a path or binary stream; ``.pages`` are lenient page proxies."""

    def __init__(self, source):
        self._path = source if isinstance(source, str) else None
        if self._path is None and hasattr(source, "seek"):
            source.seek(0)
        self._r = read_pdf(source)

    @property
    def pages(self) -> list:
        return self._r.pages

    @property
    def is_encrypted(self) -> bool:
        return self._r.is_encrypted

    @property
    def repaired(self) -> bool:
        return self._r.repaired

    def resolve(self, v):
        return self._r.resolve(v)

    def decrypt(self, password: str = "") -> int:
        """Blank/owner-password unlock via fitz; 1 on success, 0 otherwise."""
        import fitz
        try:
            doc = fitz.open(self._path) if self._path else fitz.open(
                stream=self._r.buf, filetype="pdf")
        except Exception:
            return 0
        try:
            if doc.needs_pass and not doc.authenticate(password or ""):
                return 0
            data = doc.tobytes(encryption=fitz.PDF_ENCRYPT_NONE)
        except Exception:
            return 0
        finally:
            doc.close()
        self._r = read_pdf(data)
        return 1


class Writer(MiniWriter):
    """pypdf-shaped writer.  ``metadata`` exists only so legacy call sites
    can assign ``None`` to it — this writer structurally cannot emit /Info."""

    metadata = None
