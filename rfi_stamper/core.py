"""RFI document reading and field parsing.

Handles three input shapes seen in the wild:
  * real PDFs (single- or multi-RFI files) -- text via PyMuPDF
  * zip packages that document controls export with .pdf extensions
    (page images + per-page OCR .txt + manifest.json)
  * raw text dumps (truncated exports)
"""
from __future__ import annotations

import os
import re
import zipfile
from dataclasses import dataclass, field

import fitz  # PyMuPDF

# sheet tokens like P-10.10, P-401, C502, S10.10, ET-10.12, E-002
SHEET_TOKEN = re.compile(r"\b([A-Z]{1,3})-?(\d{1,3}(?:\.\d{1,2})?)\b")
REF_LINE = re.compile(
    r"(?:Plan Ref(?:erence)?(?:\s*#)?|Drawing (?:Number|No\.?|Reference)s?|"
    r"LINKED DRAWINGS|Reference Drawings?)\s*[:#]?\s*([^\n]+)",
    re.IGNORECASE,
)
GHS_LINE = re.compile(r"\bP\d{3}\s*[\u2013\u2014-]\s")  # MSDS precaution codes, not sheets
FOOTER = re.compile(
    r"^(Page \d+ of ?\d*|Printed On:.*|uuu_.*|All Replies:?|BY DATE.*|"
    r"Awaiting an Official Response.*|Attachments? ?\(?\d*\)?:?.*|\d+ Item\(s\).*)$",
    re.IGNORECASE,
)
LETTERHEAD = re.compile(
    r"^[A-Z][\w .,+&\u2019'\-]{4,60}(Inc\.?|LLC|Company|Architecture(?:\s+and\s+Planning)?"
    r"|Planning, Inc\.?|Associates.*)$"
)


def canon(letters: str, num: str) -> str:
    return f"{letters.upper()}-{num}"


def canon_loose(tok: str) -> str:
    """Zero-stripped variant for fuzzy matching: E-002 -> E-2."""
    m = re.match(r"([A-Z]+)-(\d+)(\.\d+)?$", tok)
    if not m:
        return tok
    return f"{m.group(1)}-{int(m.group(2))}{m.group(3) or ''}"


@dataclass
class RFIRecord:
    number: str = ""
    title: str = ""
    question: str = ""
    answer: str = ""
    refs: list = field(default_factory=list)       # (token, via) via in {planref, body}
    source: str = ""
    warnings: list = field(default_factory=list)
    numbered: bool = False   # True only if the number was parsed from content
                             # (not the filename/sentinel fallback) -> safe to merge on

    @property
    def has_answer(self) -> bool:
        return len(re.sub(r"\s", "", self.answer)) >= 25


# ---------------------------------------------------------------- reading ---

def read_document(path: str):
    """Return (text, kind). kind in {pdf, zip-package, raw-text}."""
    with open(path, "rb") as f:
        head = f.read(8)
    if head.startswith(b"%PDF"):
        doc = fitz.open(path)
        text = "\n".join(page.get_text() for page in doc)
        doc.close()
        return _normalize_text(text), "pdf"
    if head.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(path) as z:
                txts = sorted(
                    (n for n in z.namelist() if re.fullmatch(r"\d+\.txt", os.path.basename(n))),
                    key=lambda n: int(os.path.basename(n).split(".")[0]),
                )
                if txts:
                    text = "\n".join(z.read(n).decode("utf-8", "replace") for n in txts)
                    return _normalize_text(text), "zip-package"
                # zip without txt pages: try any pdf inside
                for n in z.namelist():
                    if n.lower().endswith(".pdf"):
                        doc = fitz.open(stream=z.read(n), filetype="pdf")
                        text = "\n".join(p.get_text() for p in doc)
                        doc.close()
                        return _normalize_text(text), "zip-package"
        except zipfile.BadZipFile:
            pass
    # last resort: treat as text (covers truncated exports)
    with open(path, "rb") as f:
        raw = f.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1", "replace")
    return _normalize_text(text), "raw-text"


def _normalize_text(text: str) -> str:
    """Document controls love non-breaking spaces; regexes do not."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"[\u00a0\u2007\u202f\u2000-\u200b]", " ", text)


# ---------------------------------------------------------------- parsing ---

def _clean_block(block: str) -> str:
    block = re.sub(r"_{3,}", " ", block)           # ruled fill-in lines on forms
    lines = []
    for ln in block.replace("\r", "").split("\n"):
        s = ln.strip()
        if not s or FOOTER.match(s) or not re.search(r"[A-Za-z0-9]", s):
            continue
        lines.append(s)
    # drop a leading letterhead-only line (architect stamp header)
    while lines and LETTERHEAD.match(lines[0]) and len(lines[0]) < 70:
        lines.pop(0)
    return " ".join(lines).strip()


_SECTION_END = re.compile(
    r"\n\s*(?:Question:|Proposed Solution:|Revised RFI Question|Answer:|Attachments|"
    r"1 Item\(s\)|All Replies|Awaiting an Official|Record Information|Upper Form|"
    r"BY DATE|Page \d+ of|Project:|TO:\s|FROM:\s|Answered By|DATE INITIATED|"
    r"File Name\b|Ball in Court|Cost Impact|Schedule Impact|Received From|"
    r"Distribution\b|Location:)",
    re.IGNORECASE,
)

# start of attachment / printout regions inside one RFI record
_ATTACH_CUT = re.compile(r"Attachments?:|1 Item\(s\)|Awaiting an Official", re.IGNORECASE)


def _section_after(text: str, start: int, cap: int = 4000) -> str:
    m = _SECTION_END.search(text, start)
    end = m.start() if m else min(len(text), start + cap)
    return _clean_block(text[start:min(end, start + cap)])


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


_ADDR = (r"\d{2,6} [A-Z][A-Za-z .,]*(?:St|Street|Ave|Avenue|Blvd|Boulevard|"
         r"Rd|Road|Dr|Drive|Way|Suite|Ste)\b")
# candidates that open like attachment tables / address blocks are not answers
_JUNK_HEAD = re.compile(
    rf"^(?:File Name\b|{_ADDR})|Revision No\.?\s+Size|Issue Date|Phone:\s*\+?\d")
# footer junk to trim off the tail of a real answer
_TAIL_TRIM = re.compile(rf"\s+(?:{_ADDR}.*|Phone:.*|\+1\d{{9,}}.*)$")
_TAIL_COMPANY = re.compile(
    r"\s*[A-Z][\w&.,'\u2019 -]{2,60}(?:Company|Inc\.?|LLC|L\.L\.C\.|Corp\.?|"
    r"Construction(?: Company)?|Builders|Architecture(?: and Planning)?|"
    r"Architects?|Engineers?)\.?\s*$")


def _trim_tail(s: str) -> str:
    m = _TAIL_TRIM.search(s)
    if m and m.start() > max(40, 0.55 * len(s)):
        s = s[:m.start()].strip()
    return _TAIL_COMPANY.sub("", s).strip()


def parse_fields(chunk: str, fname: str) -> RFIRecord:
    rec = RFIRecord(source=fname)
    for pat in (r"Document:\s*(\d{2,12})\b", r"District RFI #:\s*(\d{1,12})\b",
                r"\bRFI\s*[#\u2013-]?\s*(\d{1,12})\b"):
        m = re.search(pat, chunk)
        if m:
            rec.number = m.group(1).zfill(3)
            rec.numbered = True         # parsed from content, safe to merge on
            break
    if not rec.number:
        m = re.search(r"(\d{2,12})\b", os.path.basename(fname))
        rec.number = m.group(1).zfill(3) if m else "???"
        rec.warnings.append("RFI number taken from file name")

    m = re.search(r"(?:Title|Subject):\s*([^\n]+)", chunk)
    if m:
        t = m.group(1)
        t = re.split(r"\s{2,}|Sub Ref", t)[0]
        rec.title = t.strip(" .")
    if not rec.title:
        base = os.path.splitext(os.path.basename(fname))[0]
        base = re.sub(r"^(RFI)?\d+_*", "", base, flags=re.IGNORECASE)
        rec.title = re.sub(r"[_\-]+", " ", base).strip() or f"RFI {rec.number}"
        rec.warnings.append("title taken from file name")

    # question: first Question: block
    m = re.search(r"Question:[ \t]*", chunk)
    if m:
        rec.question = _section_after(chunk, m.end())

    # answer: best candidate among all Answer:/Response: blocks.  Some
    # printouts restate the question after a blank Answer:, so any candidate
    # that opens with a verbatim(ish) copy of the question is rejected.
    best = ""
    nq = _norm(rec.question)
    for m in re.finditer(r"(?:Answer|Response):[ \t]*", chunk):
        cand = _section_after(chunk, m.end(), cap=1400)
        if not cand or _JUNK_HEAD.search(cand[:150]):
            continue                      # attachment table / letterhead, not an answer
        # question restatement, not an answer: reject when the normalized
        # question appears anywhere inside the candidate (this way a long or
        # labelled restatement that exceeds the head window can't sneak past)
        ncand = _norm(re.sub(r"^\s*(?:Original Question|RE)\s*[-:]\s*", "",
                             cand, flags=re.IGNORECASE))
        if nq and len(nq) >= 40 and nq[:90] in ncand:
            continue
        if len(cand) > len(best):
            best = cand
    best = _trim_tail(best)
    rec.answer = best if len(re.sub(r"\s", "", best)) >= 25 else ""

    # refs from labeled lines (high confidence)
    seen = set()
    for m in REF_LINE.finditer(chunk):
        for t in SHEET_TOKEN.finditer(m.group(1)):
            tok = canon(t.group(1), t.group(2))
            if tok not in seen:
                seen.add(tok)
                rec.refs.append((tok, "planref"))
    # refs from the body text; tokens seen only inside attachment / printout
    # regions are demoted to 'attachment' (reported, but not auto-mapped)
    mcut = _ATTACH_CUT.search(chunk)
    cut = mcut.start() if mcut else len(chunk)
    for region, via in ((chunk[:cut], "body"), (chunk[cut:], "attachment")):
        for line in region.split("\n"):
            if GHS_LINE.search(line):
                continue  # MSDS precaution codes like "P501 - Dispose of..."
            for t in SHEET_TOKEN.finditer(line):
                tok = canon(t.group(1), t.group(2))
                if tok not in seen:
                    seen.add(tok)
                    rec.refs.append((tok, via))
    return rec


RECORD_START = re.compile(r"Request for Information", re.IGNORECASE)


def _merge_key(rec: RFIRecord, idx: int):
    """Only records whose number was parsed from content share a merge key.
    Unnumbered / filename-fallback records get a unique key so distinct
    un-numbered RFIs stay distinct (each still flows to the appendix)."""
    if rec.numbered:
        return rec.number
    return f"{rec.number}-uniq-{idx}"


def _merge_into(keep: RFIRecord, r: RFIRecord) -> None:
    """Fold a duplicate record r into keep (answered copies of earlier RFIs
    ride inside later packages)."""
    if r.has_answer and not keep.has_answer:
        keep.answer = r.answer
    if len(r.question) > len(keep.question):
        keep.question = r.question
    have = {t for t, _ in keep.refs}
    keep.refs += [(t, v) for t, v in r.refs if t not in have]


def split_records(text: str, fname: str) -> list:
    starts = [m.start() for m in RECORD_START.finditer(text)]
    if len(starts) <= 1:
        chunks = [text]
    else:
        starts[0] = 0
        chunks = [text[s:e] for s, e in zip(starts, starts[1:] + [len(text)])]
    recs = [parse_fields(c, fname) for c in chunks if len(c.strip()) > 40]

    # merge duplicates (answered copies of earlier RFIs ride inside later packages)
    merged: dict = {}
    for i, r in enumerate(recs):
        k = _merge_key(r, i)
        if k not in merged:
            merged[k] = r
            continue
        _merge_into(merged[k], r)
    return list(merged.values())


def parse_paths(paths, log=print) -> list:
    """Read every file/dir given; return merged RFIRecord list."""
    files = []
    for p in paths:
        if os.path.isdir(p):
            files += sorted(
                os.path.join(p, f) for f in os.listdir(p)
                if f.lower().endswith((".pdf", ".txt", ".zip"))
            )
        else:
            files.append(p)
    out: dict = {}
    uniq = 0
    for f in files:
        try:
            text, kind = read_document(f)
        except Exception as e:                      # noqa: BLE001
            log(f"  !! could not read {os.path.basename(f)}: {e}")
            continue
        for r in split_records(text, f):
            # unnumbered records must never collapse together across files
            if r.numbered:
                k = r.number
            else:
                uniq += 1
                k = f"{r.number}-uniq-{uniq}"
            if k in out:
                _merge_into(out[k], r)
            else:
                out[k] = r
        log(f"  read {os.path.basename(f)} [{kind}]")
    return sorted(out.values(), key=lambda r: r.number)
