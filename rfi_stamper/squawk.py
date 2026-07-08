"""The Squawk Box: Planloom's speaker-trained voice-command deck.

Honest boundary (the product): this recognizes the PHRASES YOU TRAINED —
push-to-talk, speaker-dependent, a growing deck of short commands — not
open dictation.  An unconfident match returns candidates and asks; it
never guesses a drawing command (fail-closed, see :func:`confident`).

Fully offline, from scratch, numpy + stdlib only:

* **Capture** — written directly against the OS wave-in interface (winmm)
  via ctypes, rotating small buffers.  Windows-only by nature; everywhere
  else ``HAS_CAPTURE`` is False, :func:`list_devices` returns ``[]`` and
  :class:`Recorder` refuses loudly.  Every consumer must check
  ``HAS_CAPTURE`` first.  The trained deck itself is plain WAV files, so
  training/matching stay fully testable without a microphone.
* **DSP** — MFCC features computed here: pre-emphasis, raised-cosine
  window, rfft power spectrum, mel filterbank, log energies, orthonormal
  DCT-II, per-utterance cepstral mean subtraction.
* **Matcher** — classic dynamic time warping over MFCC frame sequences,
  euclidean local cost, banded (±20% of the longer sequence) and
  path-length-normalized.  The recordings ARE the model: 2–3 takes per
  phrase, matched by min-DTW — the same self-learning ethos as Heartwood.

Scores are mean per-step euclidean distances in MFCC space: 0 means
identical, same-word retakes land low, different words land high.  The
thresholds below were calibrated on the synthesized-word fixtures in
``tests/test_squawk.py``.
"""
from __future__ import annotations

import ctypes
import json
import os
import re
import shutil
import sys
import threading
import time
import wave

import numpy as np

# ---------------------------------------------------------------- constants --

#: matches[0]["score"] must be BELOW this for a confident auto-fire.
CONFIDENT_SCORE = 1.8
#: ...AND the runner-up must trail by MORE than this (best["gap"]).
CONFIDENT_GAP = 0.4

#: the day-one command deck offered for training (never auto-recorded)
SUGGESTED_PHRASES = [
    "cap the open ends",
    "slope one eighth",
    "slope one quarter",
    "check the piping",
    "undo that",
    "zoom fit",
    "zero", "one", "two", "three", "four",
    "five", "six", "seven", "eight", "nine",
    "inch", "foot",
    "sanitary", "cold water", "hot water", "vent",
]

DECK_FILE = "deck.json"

# ------------------------------------------------------------ capture layer --
# Written from scratch against the OS wave-in interface (winmm) via ctypes.
# No packages, nothing embedded; on any other platform HAS_CAPTURE is False.

_WAVE_MAPPER = 0xFFFFFFFF        # "the default input device"
_WAVE_FORMAT_PCM = 1
_CALLBACK_NULL = 0
_WHDR_DONE = 0x00000001


class _WAVEFORMATEX(ctypes.Structure):
    _fields_ = [("wFormatTag", ctypes.c_uint16),
                ("nChannels", ctypes.c_uint16),
                ("nSamplesPerSec", ctypes.c_uint32),
                ("nAvgBytesPerSec", ctypes.c_uint32),
                ("nBlockAlign", ctypes.c_uint16),
                ("wBitsPerSample", ctypes.c_uint16),
                ("cbSize", ctypes.c_uint16)]


class _WAVEHDR(ctypes.Structure):
    _fields_ = [("lpData", ctypes.c_void_p),
                ("dwBufferLength", ctypes.c_uint32),
                ("dwBytesRecorded", ctypes.c_uint32),
                ("dwUser", ctypes.c_void_p),
                ("dwFlags", ctypes.c_uint32),
                ("dwLoops", ctypes.c_uint32),
                ("lpNext", ctypes.c_void_p),
                ("reserved", ctypes.c_void_p)]


class _WAVEINCAPSW(ctypes.Structure):
    _fields_ = [("wMid", ctypes.c_uint16),
                ("wPid", ctypes.c_uint16),
                ("vDriverVersion", ctypes.c_uint32),
                ("szPname", ctypes.c_wchar * 32),
                ("dwFormats", ctypes.c_uint32),
                ("wChannels", ctypes.c_uint16),
                ("wReserved1", ctypes.c_uint16)]


def _load_winmm():
    if sys.platform != "win32":
        return None
    try:
        return ctypes.WinDLL("winmm")
    except OSError:
        return None


_WINMM = _load_winmm()

#: True only where the wave-in capture layer actually works.  Check this
#: BEFORE building a Recorder; everywhere else the deck still trains and
#: matches from stored WAV files.
HAS_CAPTURE = _WINMM is not None

_NO_CAPTURE_MSG = ("audio capture runs on the OS wave-in interface and is "
                   "available on Windows only — check squawk.HAS_CAPTURE "
                   "before constructing a Recorder (the trained deck still "
                   "works from stored WAV files)")


def list_devices() -> list:
    """Every wave-in device: ``[{"id": int, "name": str}, ...]``.

    Honest on non-capture platforms: returns ``[]``, never raises.
    """
    if not HAS_CAPTURE:
        return []
    out = []
    try:
        n = int(_WINMM.waveInGetNumDevs())
    except Exception:   # noqa: BLE001 -- a broken audio stack lists nothing
        return []
    for i in range(n):
        caps = _WAVEINCAPSW()
        try:
            rc = _WINMM.waveInGetDevCapsW(i, ctypes.byref(caps),
                                          ctypes.sizeof(caps))
        except Exception:   # noqa: BLE001
            continue
        if rc == 0:
            out.append({"id": i, "name": caps.szPname})
    return out


class Recorder:
    """Push-to-talk wave-in recorder: mono 16-bit PCM, rotating small
    buffers, live level.  ``start()`` … speak … ``stop() -> bytes``.

    ``device_id=None`` records from the system default input device.
    Raises RuntimeError immediately where capture is unavailable.
    """

    BUF_MS = 100          # one rotating buffer ≈ the level-meter window
    N_BUF = 6

    def __init__(self, device_id=None, rate: int = 16000):
        if not HAS_CAPTURE:
            raise RuntimeError(_NO_CAPTURE_MSG)
        self.device_id = _WAVE_MAPPER if device_id is None else int(device_id)
        self.rate = int(rate)
        self._h = ctypes.c_void_p()
        self._hdrs: list = []
        self._bufs: list = []
        self._chunks: list = []
        self._level = 0.0
        self._running = False
        self._thread = None
        self._lock = threading.Lock()

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        if self._running:
            return
        fmt = _WAVEFORMATEX(_WAVE_FORMAT_PCM, 1, self.rate, self.rate * 2,
                            2, 16, 0)
        rc = _WINMM.waveInOpen(ctypes.byref(self._h), self.device_id,
                               ctypes.byref(fmt), 0, 0, _CALLBACK_NULL)
        if rc != 0:
            raise RuntimeError(f"waveInOpen failed (mm error {rc}) — is the "
                               f"device still plugged in?")
        nbytes = max(2, self.rate * 2 * self.BUF_MS // 1000)
        self._hdrs, self._bufs, self._chunks = [], [], []
        self._level = 0.0
        try:
            for _ in range(self.N_BUF):
                buf = ctypes.create_string_buffer(nbytes)
                hdr = _WAVEHDR()
                hdr.lpData = ctypes.cast(buf, ctypes.c_void_p)
                hdr.dwBufferLength = nbytes
                self._bufs.append(buf)
                self._hdrs.append(hdr)
                self._queue_buffer(hdr)
            rc = _WINMM.waveInStart(self._h)
            if rc != 0:
                raise RuntimeError(f"waveInStart failed (mm error {rc})")
        except Exception:
            self._teardown()
            raise
        self._running = True
        self._thread = threading.Thread(target=self._pump, daemon=True)
        self._thread.start()

    def stop(self) -> bytes:
        """Stop capturing and return everything heard as mono 16-bit PCM."""
        if not self._h:
            return b""
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        try:
            _WINMM.waveInStop(self._h)
            _WINMM.waveInReset(self._h)      # flushes pending buffers as DONE
            self._collect_done()             # ...including the partial tail
        finally:
            self._teardown()
        with self._lock:
            data = b"".join(self._chunks)
            self._chunks = []
        self._level = 0.0
        return data

    def level(self) -> float:
        """Live 0..1 RMS of the most recent ~100 ms buffer (meter food)."""
        return float(self._level)

    # -- internals (only ever run where HAS_CAPTURE is True) ----------------
    def _queue_buffer(self, hdr) -> None:
        hdr.dwFlags = 0
        hdr.dwBytesRecorded = 0
        _WINMM.waveInPrepareHeader(self._h, ctypes.byref(hdr),
                                   ctypes.sizeof(hdr))
        _WINMM.waveInAddBuffer(self._h, ctypes.byref(hdr),
                               ctypes.sizeof(hdr))

    def _collect_done(self, requeue: bool = False) -> None:
        for buf, hdr in zip(self._bufs, self._hdrs):
            if not (hdr.dwFlags & _WHDR_DONE):
                continue
            n = int(hdr.dwBytesRecorded)
            if n > 0:
                chunk = buf.raw[:n]
                with self._lock:
                    self._chunks.append(chunk)
                samples = np.frombuffer(chunk[:n - (n % 2)], dtype="<i2")
                if len(samples):
                    rms = float(np.sqrt(np.mean(
                        samples.astype(np.float64) ** 2)))
                    self._level = min(1.0, rms / 32768.0)
            _WINMM.waveInUnprepareHeader(self._h, ctypes.byref(hdr),
                                         ctypes.sizeof(hdr))
            if requeue:
                self._queue_buffer(hdr)

    def _pump(self) -> None:
        while self._running:
            try:
                self._collect_done(requeue=True)
            except Exception:   # noqa: BLE001 -- a dying device just stops
                break
            time.sleep(0.02)

    def _teardown(self) -> None:
        if self._h:
            for hdr in self._hdrs:
                try:
                    _WINMM.waveInUnprepareHeader(self._h, ctypes.byref(hdr),
                                                 ctypes.sizeof(hdr))
                except Exception:   # noqa: BLE001
                    pass
            try:
                _WINMM.waveInClose(self._h)
            except Exception:   # noqa: BLE001
                pass
        self._h = ctypes.c_void_p()
        self._hdrs, self._bufs = [], []


# ------------------------------------------------------------------ WAV I/O --
# The trained deck stores plain little WAVs — stdlib `wave`, no codecs.

def write_wav(path: str, rate: int, samples) -> None:
    """Write mono 16-bit PCM.  Float input is taken as -1..1.  Atomic."""
    arr = np.asarray(samples)
    if arr.dtype.kind == "f":
        arr = (np.clip(arr, -1.0, 1.0) * 32767.0).astype("<i2")
    else:
        arr = arr.astype("<i2")
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    part = path + ".part"
    with wave.open(part, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(rate))
        w.writeframes(arr.tobytes())
    os.replace(part, path)


def read_wav(path: str) -> tuple:
    """(rate, np.int16 mono samples).  Multi-channel input is downmixed;
    8-bit is widened.  Anything fancier is honestly refused."""
    with wave.open(path, "rb") as w:
        rate = w.getframerate()
        ch = w.getnchannels()
        width = w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if width == 2:
        data = np.frombuffer(raw, dtype="<i2").astype(np.int16)
    elif width == 1:
        data = ((np.frombuffer(raw, dtype=np.uint8).astype(np.int16) - 128)
                << 8)
    else:
        raise ValueError(f"unsupported WAV sample width: {width * 8}-bit "
                         f"(the deck stores 16-bit PCM)")
    if ch > 1:
        data = data.reshape(-1, ch).mean(axis=1).astype(np.int16)
    return rate, data


# ------------------------------------------------------- DSP (from scratch) --

def trim_silence(samples, rate: int, thresh_frac: float = 0.08,
                 window_ms: int = 30, margin_ms: int = 100):
    """Cut leading/trailing silence: energy threshold = ``thresh_frac`` of
    the peak RMS over ``window_ms`` windows, keeping ``margin_ms`` margins.
    All-silent input comes back unchanged (nothing to anchor on)."""
    arr = np.asarray(samples)
    n = len(arr)
    if n == 0:
        return arr
    x = arr.astype(np.float64)
    win = max(1, int(rate * window_ms / 1000))
    nwin = (n + win - 1) // win
    pad = nwin * win - n
    if pad:
        x = np.concatenate([x, np.zeros(pad)])
    rms = np.sqrt((x.reshape(nwin, win) ** 2).mean(axis=1))
    peak = float(rms.max())
    if peak <= 0.0:
        return arr
    hot = np.nonzero(rms >= thresh_frac * peak)[0]
    margin = int(rate * margin_ms / 1000)
    lo = max(0, int(hot[0]) * win - margin)
    hi = min(n, (int(hot[-1]) + 1) * win + margin)
    return arr[lo:hi]


_FB_CACHE: dict = {}
_DCT_CACHE: dict = {}


def _mel_filterbank(rate: int, nfft: int, n_mels: int) -> np.ndarray:
    """(n_mels, nfft//2+1) triangular filters, mel = 2595*log10(1+f/700)."""
    key = (rate, nfft, n_mels)
    fb = _FB_CACHE.get(key)
    if fb is not None:
        return fb
    mel_lo, mel_hi = 0.0, 2595.0 * np.log10(1.0 + (rate / 2.0) / 700.0)
    mels = np.linspace(mel_lo, mel_hi, n_mels + 2)
    hz = 700.0 * (10.0 ** (mels / 2595.0) - 1.0)
    bins = np.floor((nfft + 1) * hz / rate).astype(int)
    # degenerate low-frequency triangles collapse at small nfft; keep every
    # filter at least one bin wide so no row is silently all-zero
    for i in range(1, len(bins)):
        bins[i] = max(bins[i], bins[i - 1] + 1)
    bins = np.minimum(bins, nfft // 2)
    fb = np.zeros((n_mels, nfft // 2 + 1))
    for m in range(1, n_mels + 1):
        a, b, c = int(bins[m - 1]), int(bins[m]), int(bins[m + 1])
        if b > a:
            fb[m - 1, a:b] = (np.arange(a, b) - a) / float(b - a)
        if c > b:
            fb[m - 1, b:c] = (c - np.arange(b, c)) / float(c - b)
        fb[m - 1, b] = 1.0
    _FB_CACHE[key] = fb
    return fb


def _dct_ortho(n: int) -> np.ndarray:
    """Orthonormal DCT-II matrix (n x n): row k dots a length-n log-mel
    vector into cepstral coefficient k."""
    d = _DCT_CACHE.get(n)
    if d is not None:
        return d
    k = np.arange(n)[:, None]
    m = np.arange(n)[None, :]
    d = np.cos(np.pi * (m + 0.5) * k / n) * np.sqrt(2.0 / n)
    d[0] *= np.sqrt(0.5)
    _DCT_CACHE[n] = d
    return d


def mfcc(samples, rate: int, n_mfcc: int = 13, frame_ms: int = 25,
         hop_ms: int = 10, n_mels: int = 26) -> np.ndarray:
    """(frames x n_mfcc) MFCC features, computed from scratch:

    pre-emphasis 0.97 → raised-cosine window → rfft power spectrum →
    mel filterbank → log energies (floor 1e-10) → orthonormal DCT-II →
    keep coefficients 1..n_mfcc (c0 dropped) → per-utterance cepstral
    mean subtraction.  Deterministic: same samples, same features.
    """
    x = np.asarray(samples, dtype=np.float64).ravel()
    if len(x) == 0:
        x = np.zeros(1)
    x = np.concatenate([x[:1], x[1:] - 0.97 * x[:-1]])       # pre-emphasis
    flen = max(2, int(rate * frame_ms / 1000))
    hop = max(1, int(rate * hop_ms / 1000))
    if len(x) < flen:
        x = np.concatenate([x, np.zeros(flen - len(x))])
    nfr = 1 + (len(x) - flen) // hop
    idx = np.arange(flen)[None, :] + hop * np.arange(nfr)[:, None]
    frames = x[idx] * np.hamming(flen)
    nfft = 1
    while nfft < flen:
        nfft *= 2
    power = np.abs(np.fft.rfft(frames, nfft, axis=1)) ** 2
    mel = power @ _mel_filterbank(rate, nfft, n_mels).T
    logmel = np.log(np.maximum(mel, 1e-10))
    ceps = logmel @ _dct_ortho(n_mels).T
    c = ceps[:, 1:n_mfcc + 1]
    return c - c.mean(axis=0)                 # cepstral mean subtraction


# ----------------------------------------------------------------- matcher --

def dtw(a, b, band_frac: float = 0.2) -> float:
    """Classic dynamic time warping between two MFCC frame sequences.

    Euclidean local cost, banded (window = ``band_frac`` of the longer
    sequence, widened to keep the corner reachable), path-length-normalized
    by (len(a)+len(b)).  0.0 means identical.  Pure numpy; the row DP uses
    the prefix-min identity  D[i,j] = cs[j] + min_{t<=j}(best[t]-cs[t-1])
    so the inner loop is fully vectorized.  Empty input -> inf.
    """
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    if a.ndim == 1:
        a = a[:, None]
    if b.ndim == 1:
        b = b[:, None]
    n, m = len(a), len(b)
    if n == 0 or m == 0:
        return float("inf")
    w = max(int(band_frac * max(n, m)), abs(n - m)) + 1
    inf = np.inf
    rows = np.full((2, m + 1), inf)
    rows[0, 0] = 0.0
    for i in range(1, n + 1):
        prv = rows[(i - 1) & 1]
        cur = rows[i & 1]
        cur[:] = inf
        jlo, jhi = max(1, i - w), min(m, i + w)
        diff = b[jlo - 1:jhi] - a[i - 1]
        cost = np.sqrt(np.einsum("ij,ij->i", diff, diff))
        best = np.minimum(prv[jlo:jhi + 1], prv[jlo - 1:jhi])  # up, diagonal
        # left-neighbor fold, vectorized: a path reaches (i,j) by entering
        # this row at some column t<=j (cost best[t]) then walking right
        cs = np.cumsum(cost)
        run = np.minimum.accumulate(best - (cs - cost))
        cur[jlo:jhi + 1] = cs + run
    total = rows[n & 1][m]
    return float(total) / float(n + m)


# ---------------------------------------------------------------- the deck --

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    """Filesystem-safe phrase key: lowercase, [a-z0-9-] only."""
    s = _SLUG_RE.sub("-", str(text).strip().lower()).strip("-")
    return s or "phrase"


def confident(matches, score_max: float = None, gap_min: float = None) -> bool:
    """Fail-closed confidence gate for :meth:`Deck.match` results.

    True only when the best score beats ``CONFIDENT_SCORE`` AND the
    runner-up trails by more than ``CONFIDENT_GAP``.  Anything else means
    "show the candidates and ask" — never auto-fire a drawing command.
    """
    if not matches:
        return False
    smax = CONFIDENT_SCORE if score_max is None else float(score_max)
    gmin = CONFIDENT_GAP if gap_min is None else float(gap_min)
    best = matches[0]
    score = float(best.get("score", float("inf")))
    gap = float(best.get("gap", float("inf")))
    return score < smax and gap > gmin


class Deck:
    """The trained phrase deck: ``<dir>/<slug>/<n>.wav`` takes plus a
    ``deck.json`` register (phrase text, slug, enabled).  The WAVs ARE the
    model — record 2–3 takes per phrase in your own voice and match() finds
    the closest by min-DTW over cached MFCC templates."""

    def __init__(self, dir_path: str):
        self.dir = dir_path
        self._phrases: list = []            # [{"text","slug","enabled"}]
        self._mfcc_cache: dict = {}         # slug -> {path: features}
        self._load()

    # -- register ------------------------------------------------------------
    def _load(self) -> None:
        path = os.path.join(self.dir, DECK_FILE)
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
        except Exception:   # noqa: BLE001 -- fresh/absent/corrupt -> empty
            return
        for p in raw.get("phrases", []):
            if not isinstance(p, dict) or not p.get("text"):
                continue
            self._phrases.append({
                "text": str(p["text"]),
                "slug": slugify(p.get("slug") or p["text"]),
                "enabled": bool(p.get("enabled", True)),
            })

    def _save(self) -> None:
        os.makedirs(self.dir, exist_ok=True)
        path = os.path.join(self.dir, DECK_FILE)
        part = path + ".part"
        with open(part, "w", encoding="utf-8") as f:
            json.dump({"planloom_squawk": 1, "phrases": self._phrases},
                      f, indent=2)
        os.replace(part, path)

    def _find(self, text_or_slug: str):
        slug = slugify(text_or_slug)
        for p in self._phrases:
            if p["slug"] == slug:
                return p
        return None

    # -- phrases ----------------------------------------------------------
    def phrases(self) -> list:
        """[{"text", "slug", "enabled", "takes"}] in deck order."""
        return [dict(p, takes=len(self.take_paths(p["slug"])))
                for p in self._phrases]

    def add_phrase(self, text: str) -> dict:
        found = self._find(text)
        if found is not None:
            return dict(found)
        p = {"text": str(text).strip(), "slug": slugify(text),
             "enabled": True}
        self._phrases.append(p)
        self._save()
        return dict(p)

    def remove_phrase(self, text_or_slug: str) -> bool:
        p = self._find(text_or_slug)
        if p is None:
            return False
        self._phrases.remove(p)
        self._mfcc_cache.pop(p["slug"], None)
        d = os.path.join(self.dir, p["slug"])
        if os.path.isdir(d):
            shutil.rmtree(d, ignore_errors=True)
        self._save()
        return True

    def set_enabled(self, text_or_slug: str, enabled: bool) -> bool:
        p = self._find(text_or_slug)
        if p is None:
            return False
        p["enabled"] = bool(enabled)
        self._save()
        return True

    # -- takes ------------------------------------------------------------
    def take_paths(self, text_or_slug: str) -> list:
        slug = slugify(text_or_slug)
        d = os.path.join(self.dir, slug)
        if not os.path.isdir(d):
            return []
        names = [f for f in os.listdir(d)
                 if f.endswith(".wav") and f[:-4].isdigit()]
        return [os.path.join(d, f)
                for f in sorted(names, key=lambda f: int(f[:-4]))]

    def add_take(self, text: str, rate: int, samples) -> str:
        """Record one training take (2–3 per phrase expected).  Returns the
        stored WAV path; the phrase is added to the register if new."""
        self.add_phrase(text)
        slug = slugify(text)
        existing = self.take_paths(slug)
        n = (int(os.path.basename(existing[-1])[:-4]) + 1) if existing else 1
        path = os.path.join(self.dir, slug, f"{n}.wav")
        write_wav(path, rate, samples)
        self._mfcc_cache.pop(slug, None)
        return path

    def remove_take(self, text_or_slug: str, n: int = None) -> bool:
        """Delete take ``n`` (default: the most recent)."""
        slug = slugify(text_or_slug)
        paths = self.take_paths(slug)
        if not paths:
            return False
        if n is None:
            victim = paths[-1]
        else:
            victim = os.path.join(self.dir, slug, f"{int(n)}.wav")
            if victim not in paths:
                return False
        os.remove(victim)
        self._mfcc_cache.pop(slug, None)
        return True

    # -- matching ---------------------------------------------------------
    def _templates(self, slug: str) -> dict:
        cached = self._mfcc_cache.get(slug)
        if cached is not None:
            return cached
        feats = {}
        for path in self.take_paths(slug):
            try:
                rate, samples = read_wav(path)
                feats[path] = mfcc(trim_silence(samples, rate), rate)
            except Exception:   # noqa: BLE001 -- a corrupt take never
                continue        # poisons the whole phrase
        self._mfcc_cache[slug] = feats
        return feats

    def match(self, rate: int, samples, top: int = 3) -> list:
        """Best-first candidates for an utterance:
        ``[{"text", "score", "gap"}, ...]`` — score = min DTW across that
        phrase's takes (lower is better, path-length-normalized); gap = how
        far the NEXT candidate trails this one (inf when alone).  Feed the
        result to :func:`confident` before auto-firing anything."""
        probe = mfcc(trim_silence(np.asarray(samples), rate), rate)
        scored = []
        for p in self._phrases:
            if not p.get("enabled", True):
                continue
            feats = self._templates(p["slug"])
            if not feats:
                continue
            score = min(dtw(probe, f) for f in feats.values())
            scored.append({"text": p["text"], "score": float(score)})
        scored.sort(key=lambda d: d["score"])
        out = scored[:max(1, int(top))]
        for i, d in enumerate(out):
            nxt = (scored[i + 1]["score"] if i + 1 < len(scored)
                   else float("inf"))
            d["gap"] = float(nxt - d["score"])
        return out
