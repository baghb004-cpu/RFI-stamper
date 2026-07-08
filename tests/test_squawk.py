"""Self-contained tests for rfi_stamper.squawk — the Squawk Box, Planloom's
speaker-trained voice-command deck.  Plain python, no pytest, no audio
hardware: every "voice" is a synthesized multi-tone word.  Exercises:

* WAV write/read round trip (int16 + float in, stereo downmix, 8-bit
  widening, unsupported-width refusal, atomic writes)
* trim_silence: padded-silence boundaries within 150 ms, pass-throughs
* mfcc: shape math, determinism, cepstral mean subtraction, gain
  invariance (c0 dropped), word discrimination
* dtw: zero self-distance, symmetry, time-stretch tolerance, banded,
  same-word < cross-word, empty -> inf
* the Deck: register + WAV layout on disk, deck.json round trip, MFCC
  template caching, 9/9 matching on fresh noisy takes, fail-closed
  confidence (ambiguous blend + untrained word), remove_take/phrase,
  enable/disable
* capture honesty on this Linux box: HAS_CAPTURE False, list_devices []
  and Recorder refuses loudly (the winmm path itself needs Windows)
* SUGGESTED_PHRASES day-one deck, slug uniqueness/filesystem safety
* the Corral by construction: no networking / gui / eval in the engine

Run:  python3 tests/test_squawk.py
"""
import json
import os
import re
import sys
import tempfile
import wave

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rfi_stamper import squawk                               # noqa: E402
from rfi_stamper.squawk import (                             # noqa: E402
    CONFIDENT_GAP, CONFIDENT_SCORE, SUGGESTED_PHRASES, Deck, Recorder,
    confident, dtw, mfcc, read_wav, slugify, trim_silence, write_wav)

TMP = tempfile.mkdtemp(prefix="squawk_test_")
RATE = 16000


# ----------------------------------------------------- synthesized "words" --

def synth_word(kind, rate=RATE, dur=0.5, seed=0, noise=0.02):
    """A distinct multi-tone 'word' per kind, with per-seed take-to-take
    variation (duration wobble + fresh noise) — deterministic seeds."""
    rng = np.random.default_rng(seed * 100
                                + {"A": 1, "B": 2, "C": 3, "D": 4}[kind])
    dur = dur * (0.9 + 0.2 * rng.random())
    n = int(rate * dur)
    t = np.arange(n) / rate
    if kind == "A":                      # 300 Hz sweeping up + 900 Hz
        f0 = 300.0 + 150.0 * t / t[-1]
        x = np.sin(2 * np.pi * np.cumsum(f0) / rate) \
            + 0.6 * np.sin(2 * np.pi * 900.0 * t)
    elif kind == "B":                    # steady 500 + 1500 Hz
        x = np.sin(2 * np.pi * 500.0 * t) \
            + 0.6 * np.sin(2 * np.pi * 1500.0 * t)
    elif kind == "C":                    # noise burst then 2200 Hz tone
        n1 = int(n * 0.4)
        burst = np.random.default_rng(7777).standard_normal(n1) * 0.8
        x = np.concatenate([burst,
                            np.sin(2 * np.pi * 2200.0 * t[:n - n1])])
    else:                                # D: a word nobody trained
        f0 = 3800.0 - 1400.0 * t / t[-1]     # high-band down-sweep + tone
        x = np.sin(2 * np.pi * np.cumsum(f0) / rate) \
            + 0.5 * np.sin(2 * np.pi * 2000.0 * t)
    x = x / np.max(np.abs(x)) * 0.7
    x = x + noise * rng.standard_normal(n)
    edge = max(1, int(0.02 * rate))
    env = np.minimum(1.0, np.minimum(np.arange(n) / edge,
                                     (n - np.arange(n)) / edge))
    return (x * env * 32767 * 0.8).astype(np.int16)


def feats(kind, seed, noise=0.02):
    s = synth_word(kind, seed=seed, noise=noise)
    return mfcc(trim_silence(s, RATE), RATE)


# ------------------------------------------------------------------ WAV I/O --

def test_wav_roundtrip():
    word = synth_word("A", seed=1)
    p = os.path.join(TMP, "round.wav")
    write_wav(p, RATE, word)
    assert os.path.exists(p) and not os.path.exists(p + ".part")
    rate, back = read_wav(p)
    assert rate == RATE
    assert back.dtype == np.int16
    assert np.array_equal(back, word)
    # float input is -1..1, scaled and clipped
    write_wav(p, 8000, np.array([0.0, 0.5, 1.0, -1.0, 2.0, -2.0]))
    rate, back = read_wav(p)
    assert rate == 8000
    assert back[0] == 0 and back[2] == 32767 and back[3] == -32767
    assert back[4] == 32767 and back[5] == -32767, "clipping failed"
    assert abs(int(back[1]) - 16383) <= 1
    # stereo downmix: L=1000, R=3000 -> 2000
    p2 = os.path.join(TMP, "stereo.wav")
    with wave.open(p2, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(RATE)
        frames = np.array([1000, 3000] * 50, dtype="<i2")
        w.writeframes(frames.tobytes())
    rate, back = read_wav(p2)
    assert len(back) == 50 and back.dtype == np.int16
    assert np.all(back == 2000), back[:4]
    # 8-bit widens (unsigned midpoint 128 -> 0)
    p3 = os.path.join(TMP, "eight.wav")
    with wave.open(p3, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(1)
        w.setframerate(RATE)
        w.writeframes(bytes([128, 255, 0]))
    _, back = read_wav(p3)
    assert back[0] == 0 and back[1] > 30000 and back[2] < -30000, back
    # anything fancier is honestly refused
    p4 = os.path.join(TMP, "deep.wav")
    with wave.open(p4, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(3)
        w.setframerate(RATE)
        w.writeframes(b"\x00" * 30)
    try:
        read_wav(p4)
        raise SystemExit("24-bit WAV should be refused")
    except ValueError as e:
        assert "24-bit" in str(e), e


# ------------------------------------------------------------ trim_silence --

def test_trim_silence():
    word = synth_word("B", seed=3)
    pad = np.zeros(int(0.4 * RATE), dtype=np.int16)
    padded = np.concatenate([pad, word, pad])
    trimmed = trim_silence(padded, RATE)
    # each boundary lands within 150 ms of the true word edge
    slack = int(0.15 * RATE) * 2
    assert len(word) <= len(trimmed) <= len(word) + slack, \
        (len(word), len(trimmed))
    assert trimmed.dtype == np.int16
    # the word itself survived intact (same peak)
    assert int(np.abs(trimmed.astype(np.int32)).max()) \
        == int(np.abs(word.astype(np.int32)).max())
    # an already-tight word passes through nearly whole
    tight = trim_silence(word, RATE)
    assert len(tight) >= int(0.8 * len(word))
    assert len(tight) <= len(word)
    # silence in, silence out (nothing to anchor on -> unchanged)
    flat = np.zeros(1000, dtype=np.int16)
    assert len(trim_silence(flat, RATE)) == 1000
    assert len(trim_silence(np.zeros(0, dtype=np.int16), RATE)) == 0


# -------------------------------------------------------------------- MFCC --

def test_mfcc():
    word = synth_word("A", seed=1)
    f = mfcc(word, RATE)
    # frame math: 1 + (N - 25ms) // 10ms
    want_frames = 1 + (len(word) - int(0.025 * RATE)) // int(0.010 * RATE)
    assert f.shape == (want_frames, 13), f.shape
    # parameter plumbing
    f8 = mfcc(word, RATE, n_mfcc=8, n_mels=20)
    assert f8.shape == (want_frames, 8), f8.shape
    # deterministic to the bit
    assert np.array_equal(f, mfcc(word, RATE))
    # per-utterance cepstral mean subtraction
    assert np.abs(f.mean(axis=0)).max() < 1e-9
    # gain invariance: c0 (the only gain-carrying coefficient) is dropped
    louder = (word.astype(np.float64) * 1.7)
    assert np.allclose(f, mfcc(louder, RATE), atol=1e-6)
    # shorter than one frame still yields one frame
    assert mfcc(word[:100], RATE).shape[0] == 1
    assert mfcc(np.zeros(0, dtype=np.int16), RATE).shape == (1, 13)
    # different words genuinely differ
    g = mfcc(synth_word("B", seed=1), RATE)
    n = min(len(f), len(g))
    assert not np.allclose(f[:n], g[:n], atol=1.0)


# --------------------------------------------------------------------- DTW --

def test_dtw():
    a = feats("A", 1)
    b = feats("B", 1)
    c = feats("C", 1)
    assert dtw(a, a) == 0.0
    assert dtw(b, b) == 0.0
    # symmetric-ish (identical up to float noise)
    assert abs(dtw(a, b) - dtw(b, a)) < 1e-9
    assert abs(dtw(a, c) - dtw(c, a)) < 1e-9
    # same word, different noisy take, beats every cross-word pairing
    a2 = feats("A", 7, noise=0.05)
    assert dtw(a, a2) < dtw(a, b)
    assert dtw(a, a2) < dtw(a, c)
    b2 = feats("B", 7, noise=0.05)
    assert dtw(b, b2) < dtw(b, a)
    assert dtw(b, b2) < dtw(b, c)
    c2 = feats("C", 7, noise=0.05)
    assert dtw(c, c2) < dtw(c, a)
    assert dtw(c, c2) < dtw(c, b)
    # distinct words are genuinely far apart
    assert dtw(a, b) > 1.0 and dtw(a, c) > 1.0 and dtw(b, c) > 1.0
    # time warping is the whole point: a 2x-stretched copy still lands at 0
    assert dtw(a, np.repeat(a, 2, axis=0)) < 1e-12
    # plain 1-D sequences work too
    assert dtw([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == 0.0
    assert dtw([0.0, 0.0], [1.0, 1.0]) > 0.0
    # empty input -> inf, never a crash
    assert dtw(np.zeros((0, 13)), a) == float("inf")
    assert dtw(a, np.zeros((0, 13))) == float("inf")


# ---------------------------------------------------------------- the deck --

WORDS = {"cap the open ends": "A", "slope one eighth": "B", "zoom fit": "C"}


def train_deck(dir_path):
    deck = Deck(dir_path)
    for text, kind in WORDS.items():
        for seed in (1, 2):
            deck.add_take(text, RATE, synth_word(kind, seed=seed))
    return deck


def test_deck_store():
    d = os.path.join(TMP, "deck1")
    deck = train_deck(d)
    # layout on disk: <dir>/<slug>/<n>.wav + deck.json
    assert os.path.exists(os.path.join(d, "deck.json"))
    for text in WORDS:
        slug = slugify(text)
        for n in (1, 2):
            assert os.path.exists(os.path.join(d, slug, f"{n}.wav")), (slug, n)
    reg = json.load(open(os.path.join(d, "deck.json"), encoding="utf-8"))
    assert reg.get("planloom_squawk") == 1
    assert len(reg["phrases"]) == 3
    assert {"text", "slug", "enabled"} <= set(reg["phrases"][0])
    # phrases() reports takes
    ph = deck.phrases()
    assert len(ph) == 3
    assert all(p["takes"] == 2 for p in ph), ph
    assert all(p["enabled"] for p in ph)
    # register round trip: a fresh Deck sees the same world
    deck2 = Deck(d)
    ph2 = deck2.phrases()
    assert [p["text"] for p in ph2] == [p["text"] for p in ph]
    assert all(p["takes"] == 2 for p in ph2)
    # add_phrase is idempotent
    before = len(deck.phrases())
    p = deck.add_phrase("cap the open ends")
    assert p["slug"] == "cap-the-open-ends"
    assert len(deck.phrases()) == before


def test_deck_match():
    deck = train_deck(os.path.join(TMP, "deck2"))
    hits = 0
    for text, kind in WORDS.items():
        for seed in (7, 8, 9):
            probe = synth_word(kind, seed=seed, noise=0.05)
            m = deck.match(RATE, probe)
            assert m and {"text", "score", "gap"} <= set(m[0]), m
            assert len(m) <= 3
            # scores sorted best-first, gap = distance to the next candidate
            assert m[0]["score"] <= m[1]["score"] <= m[2]["score"]
            assert abs(m[0]["gap"] - (m[1]["score"] - m[0]["score"])) < 1e-9
            assert m[-1]["gap"] == float("inf")     # nothing past the last
            hits += m[0]["text"] == text
            # a trained speaker's fresh take clears the shipped gate
            assert confident(m), (text, seed, m)
    assert hits == 9, f"deck matched {hits}/9"
    # top parameter caps the candidate list
    m = deck.match(RATE, synth_word("A", seed=7, noise=0.05), top=2)
    assert len(m) == 2
    # the gap of entry 2 still references the full sorted field
    assert m[1]["gap"] != float("inf")


def test_deck_fail_closed():
    deck = train_deck(os.path.join(TMP, "deck3"))
    # an ambiguous 65/35 blend of two trained words: candidates are close,
    # so the gate refuses to auto-fire and the GUI shows "did you mean…"
    a = synth_word("A", seed=5).astype(np.float64)
    b = synth_word("B", seed=5).astype(np.float64)
    n = min(len(a), len(b))
    blend = (0.65 * a[:n] + 0.35 * b[:n]).astype(np.int16)
    m = deck.match(RATE, blend)
    assert len(m) == 3
    assert not confident(m), m
    assert m[0]["gap"] < CONFIDENT_GAP, m       # refused on the gap margin
    # a word nobody trained scores far from everything: refused on score
    m = deck.match(RATE, synth_word("D", seed=6))
    assert m[0]["score"] >= CONFIDENT_SCORE, m
    assert not confident(m)
    # confidence semantics stand alone (fail-closed by construction)
    assert not confident([])
    assert confident([{"text": "x", "score": 0.5, "gap": float("inf")}])
    assert not confident([{"text": "x", "score": CONFIDENT_SCORE + 1,
                           "gap": float("inf")}])
    assert not confident([{"text": "x", "score": 0.5, "gap": 0.0}])
    assert not confident([{"text": "x", "score": 0.5,
                           "gap": CONFIDENT_GAP}])     # margin is strict
    # per-call overrides for field recalibration
    assert confident([{"text": "x", "score": 3.0, "gap": 2.0}],
                     score_max=4.0, gap_min=1.0)
    assert not confident([{"text": "x", "score": 3.0, "gap": 2.0}],
                         score_max=2.0, gap_min=1.0)


def test_deck_edit():
    d = os.path.join(TMP, "deck4")
    deck = train_deck(d)
    slug = slugify("zoom fit")
    # remove the last take, then a numbered one
    assert deck.remove_take("zoom fit")
    assert len(deck.take_paths("zoom fit")) == 1
    assert deck.remove_take("zoom fit", 1)
    assert deck.take_paths("zoom fit") == []
    assert not deck.remove_take("zoom fit")            # nothing left
    assert not deck.remove_take("never trained")
    # numbering continues from the register on disk, no reuse surprises
    p = deck.add_take("zoom fit", RATE, synth_word("C", seed=1))
    assert os.path.basename(p) == "1.wav"
    p = deck.add_take("zoom fit", RATE, synth_word("C", seed=2))
    assert os.path.basename(p) == "2.wav"
    # an untaken phrase never matches (no takes -> no candidate)
    deck.add_phrase("just a label")
    m = deck.match(RATE, synth_word("A", seed=7))
    assert all(c["text"] != "just a label" for c in m)
    # disable drops a phrase from matching without touching its takes
    assert deck.set_enabled("cap the open ends", False)
    m = deck.match(RATE, synth_word("A", seed=7, noise=0.05))
    assert all(c["text"] != "cap the open ends" for c in m), m
    assert deck.set_enabled("cap the open ends", True)
    m = deck.match(RATE, synth_word("A", seed=7, noise=0.05))
    assert m[0]["text"] == "cap the open ends"
    assert not deck.set_enabled("never trained", True)
    # remove_phrase clears register AND takes
    assert deck.remove_phrase("zoom fit")
    assert not os.path.isdir(os.path.join(d, slug))
    assert all(p["text"] != "zoom fit" for p in deck.phrases())
    assert not deck.remove_phrase("zoom fit")
    # a corrupt register never crashes the deck — it just starts empty
    bad = os.path.join(TMP, "deck5")
    os.makedirs(bad, exist_ok=True)
    with open(os.path.join(bad, "deck.json"), "w", encoding="utf-8") as f:
        f.write("{not json")
    assert Deck(bad).phrases() == []
    # match on a takeless deck returns [] (and confident([]) is False)
    assert Deck(bad).match(RATE, synth_word("A", seed=1)) == []


# ------------------------------------------------------------ capture layer --

def test_capture_honesty():
    # this test box is Linux: the winmm wave-in path must be honestly OFF
    assert sys.platform != "win32", "this suite calibrates the non-win path"
    assert squawk.HAS_CAPTURE is False
    assert squawk.list_devices() == []
    try:
        Recorder()
        raise SystemExit("Recorder must refuse without capture")
    except RuntimeError as e:
        assert "HAS_CAPTURE" in str(e), e
        assert "wave-in" in str(e), e
    try:
        Recorder(device_id=0, rate=8000)
        raise SystemExit("Recorder must refuse regardless of arguments")
    except RuntimeError:
        pass


# --------------------------------------------------------------- the deck --

def test_suggested_phrases():
    assert SUGGESTED_PHRASES, "day-one deck must not be empty"
    assert len(SUGGESTED_PHRASES) >= 20
    for want in ("cap the open ends", "slope one eighth",
                 "slope one quarter", "check the piping", "undo that",
                 "zoom fit", "inch", "foot", "sanitary", "cold water",
                 "hot water", "vent"):
        assert want in SUGGESTED_PHRASES, want
    for d in ("zero", "one", "two", "three", "four", "five", "six",
              "seven", "eight", "nine"):
        assert d in SUGGESTED_PHRASES, d
    slugs = [slugify(p) for p in SUGGESTED_PHRASES]
    assert len(set(slugs)) == len(slugs), "slugs must be unique"
    for s in slugs:
        assert re.fullmatch(r"[a-z0-9-]+", s), s
        assert not s.startswith("-") and not s.endswith("-"), s
    # slugify is filesystem-safe on hostile input and idempotent
    assert slugify('slope 1/8" per foot!') == "slope-1-8-per-foot"
    assert slugify("  Cap the OPEN ends  ") == "cap-the-open-ends"
    assert slugify("///") == "phrase"
    assert slugify(slugify("cold water")) == slugify("cold water")


# --------------------------------------------------------------- the Corral --

def test_corral_by_construction():
    """The Squawk Box obeys the standing rules: no networking, engine free
    of gui, no eval/exec (CLAUDE.md invariant 1)."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    banned = re.compile(
        r"^\s*(?:import|from)\s+(?:socket|ssl|urllib|http|requests"
        r"|xmlrpc|ftplib|smtplib)\b", re.MULTILINE)
    eng = open(os.path.join(root, "rfi_stamper", "squawk.py"),
               encoding="utf-8").read()
    assert not banned.search(eng), "networking import in squawk.py"
    assert "tkinter" not in eng and "rfi_stamper.gui" not in eng
    assert not re.search(r"\beval\s*\(|\bexec\s*\(", eng)
    gui = open(os.path.join(root, "rfi_stamper", "gui", "squawk_deck.py"),
               encoding="utf-8").read()
    assert not banned.search(gui), "networking import in squawk_deck.py"
    assert "HAS_CAPTURE" in gui, "the dialog must check HAS_CAPTURE"
    assert "after_cancel" in gui, "the level poll must be stoppable"
    # the Loft wire-in exists and is guarded so audio can't break drafting
    loft = open(os.path.join(root, "rfi_stamper", "gui", "tab_draft.py"),
                encoding="utf-8").read()
    assert "SquawkDialog" in loft and "_open_squawk" in loft
    assert "squawkdeck" in loft


def main():
    test_wav_roundtrip()
    print("PASS WAV round trip (int16/float/stereo/8-bit, honest refusal)")
    test_trim_silence()
    print("PASS trim_silence boundaries within 150 ms + pass-throughs")
    test_mfcc()
    print("PASS mfcc shape/determinism/CMS/gain-invariance")
    test_dtw()
    print("PASS dtw zero-self, symmetry, warp tolerance, discrimination")
    test_deck_store()
    print("PASS deck store layout + deck.json round trip")
    test_deck_match()
    print("PASS deck match 9/9 fresh noisy takes, all confident")
    test_deck_fail_closed()
    print("PASS fail-closed confidence (blend, untrained word, gate math)")
    test_deck_edit()
    print("PASS take/phrase editing, disable, corrupt-register survival")
    test_capture_honesty()
    print("PASS capture layer honest on this platform (no winmm)")
    test_suggested_phrases()
    print("PASS suggested day-one deck + slug safety")
    test_corral_by_construction()
    print("PASS Corral by construction (no network/gui/eval; wired + guarded)")
    print("SQUAWK BOX ENGINE TEST PASSED")
    print("outputs in", TMP)


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print("SQUAWK TEST FAILED:", e)
        sys.exit(1)
