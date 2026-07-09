"""Self-contained tests for rfi_stamper.heartwood — Planloom's knowledge core.
Plain python, no pytest, no network, no gui imports.  Exercises:

* store round trip + idempotent schema; pure-Python BM25 finds seeded chunks
* lex: protected trade tokens survive whole; Porter stemmer cases (checked
  against values generated from the field-proven reference implementation)
* vectors: fnv1a/xorshift signature determinism (hard-coded reference
  values); synthetic corpus geometry (hotwire ~ ungrounded, not roofing)
* thesaurus: seed loads (>=100 entries), bidirectional exact-phrase expand,
  miner files unverified proposals with citations, approve/reject gates
* search: meaning bridge finds the ampacity chunk that never says
  "hot wire"; honest refusal off-trade; trade filter
* digest: verbatim sentences with citations, diversity pick
* restate: NUMBER LOCK fail-closed; approved-only swaps; fixed templates
* ingest: fake knowledge-base sqlite import (real column names) + error
  paths; capture_rfis answered/blank/dedupe; note trust/reject lane;
  unverified flag propagates through ask(); usage feedback logging
* offline: no networking imports anywhere in the engine

Run:  python3.12 tests/test_heartwood.py
"""
import glob
import os
import re
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rfi_stamper.heartwood import (                # noqa: E402
    Heartwood, HeartwoodStore, default_path)
from rfi_stamper.heartwood import (                # noqa: E402
    digest, ingest, lex, restate, search, thesaurus, vectors)

TMP = tempfile.mkdtemp(prefix="heartwood_test_")


def store_at(name: str) -> HeartwoodStore:
    return HeartwoodStore(os.path.join(TMP, name))


def hw_at(name: str) -> Heartwood:
    return Heartwood(os.path.join(TMP, name))


# ------------------------------------------------------------------ store --

def test_store():
    path = os.path.join(TMP, "roundtrip.db")
    st = HeartwoodStore(path)
    doc = st.add_document("Conduit Basics", "electrical", None, "text")
    c1 = st.add_chunk(doc, 0, "Conduit shall be supported every 10 ft.")
    c2 = st.add_chunk(doc, 1, "Junction box covers shall remain accessible.")
    assert c2 == c1 + 1
    row = st.chunk(c1)
    assert row["title"] == "Conduit Basics" and row["origin"] == "text"
    assert row["trade"] == "electrical" and row["seq"] == 0
    # BM25 finds the seeded chunk by stemmed term, best-first
    hits = st.bm25([lex.stem("conduit")])
    assert hits and hits[0][0] == c1 and hits[0][1] > 0, hits
    assert st.bm25([lex.stem("covers")])[0][0] == c2
    assert st.bm25(["zzzabsent"]) == []
    # idempotent schema: reopening the same file keeps every row
    st.close()
    st = HeartwoodStore(path)
    assert st.counts()["chunks"] == 2
    assert st.bm25([lex.stem("conduit")])[0][0] == c1
    # facade default path is computed without touching the gui package
    assert default_path().endswith(os.path.join(".planloom", "heartwood.db"))
    assert "rfi_stamper.gui" not in sys.modules
    st.close()


# -------------------------------------------------------------------- lex --

def test_lex():
    # protected trade tokens survive as single, normalized units
    toks = lex.tokenize('Install 3/4" EMT conduit per NEC 210.8 with #12 AWG copper')
    ts = [t.t for t in toks]
    assert '3/4"' in ts, ts
    assert "nec 210.8" in ts, ts
    assert "#12" in ts, ts
    assert all(t.is_num for t in toks if t.t in ('3/4"', "nec 210.8", "#12"))
    assert lex.tokenize("Pull 12 AWG copper")[1].t == "12 awg"
    assert lex.tokenize("The 2x4 studs at 16 o.c.")[0].t == "2x4"
    # stopwords dropped; code-meaning words kept
    kept = [t.t for t in lex.tokenize("the conductor shall not exceed max size")]
    assert "the" not in kept and "not" in kept and "shall" in kept and "max" in kept
    # stemmer: classic Porter cases, verified against the reference impl
    for word, expect in [
            ("caresses", "caress"), ("ponies", "poni"), ("sized", "size"),
            ("hopping", "hop"), ("relational", "relat"), ("happy", "happi"),
            ("relay", "relay"), ("electricity", "electr"),
            ("electrical", "electr"), ("adjustable", "adjust"),
            ("conductors", "conductor"), ("ampacity", "ampac"),
            ("installation", "instal"), ("venting", "vent"),
            ("roofing", "roof"), ("ungrounded", "unground")]:
        got = lex.stem(word)
        assert got == expect, (word, got, expect)
    # protected/numeric tokens are never stemmed
    assert lex.stem("210.8") == "210.8"
    assert [t.t for t in lex.terms("Grounded conductors sized per ampacity")] == \
        ["ground", "conductor", "size", "ampac"]


# ---------------------------------------------------------------- vectors --

def test_vectors():
    # the exact fnv1a -> xorshift signature chain, hard-coded from the
    # reference implementation: stable across rebuilds AND across codebases
    assert vectors.fnv1a("") == 2166136261
    assert vectors.fnv1a("conduit") == 2942671541
    assert vectors.fnv1a("hot wire") == 1125765937
    idx, sign = vectors.signature("conduit")
    assert list(idx) == [180, 52, 113, 17, 43, 182, 219, 2], list(idx)
    assert list(sign) == [-1, 1, 1, 1, -1, -1, 1, 1], list(sign)
    idx2, sign2 = vectors.signature("ungrounded")
    assert list(idx2) == [146, 70, 157, 88, 210, 33, 245, 4]
    assert list(sign2) == [1, 1, 1, 1, 1, -1, 1, -1]
    # deterministic: same term, same signature, distinct positions
    idx3, sign3 = vectors.signature("conduit")
    assert list(idx) == list(idx3) and list(sign) == list(sign3)
    assert len(set(idx)) == vectors.SIG_K

    # synthetic corpus: hotwire and ungrounded keep the same company,
    # roofing keeps different company -> geometry says so
    st = store_at("vectors.db")
    doc = st.add_document("synthetic", None, None, "text")
    elec = ["the {} feeds the breaker panel through the conduit run",
            "size the {} ampacity before the breaker trips again",
            "protect the {} with the breaker inside the panel",
            "the {} lands on the breaker lug in the panel"]
    seq = 0
    for term in ("hotwire", "ungrounded"):
        for tpl in elec:
            st.add_chunk(doc, seq, tpl.format(term)); seq += 1
    for line in ["the roofing membrane laps the ridge flashing shingle",
                 "install the roofing shingle over the deck flashing",
                 "the roofing crew mops the membrane at the parapet",
                 "flash the roofing curb before the membrane cures"]:
        st.add_chunk(doc, seq, line); seq += 1
    stats = vectors.train(st)
    assert stats["vocab"] > 0 and stats["chunks"] == seq
    assert vectors.load(st)
    hot = vectors.term_vec(st, "hotwire")
    ung = vectors.term_vec(st, "ungrounded")
    roof = vectors.term_vec(st, "roofing")     # stems to the trained term
    assert hot is not None and ung is not None and roof is not None
    close = vectors.cosine(hot, ung)
    far = vectors.cosine(hot, roof)
    assert close > far + 0.2, (close, far)
    # retrain is byte-deterministic
    hot1 = vectors.term_vec(st, "hotwire").copy()
    vectors.train(st)
    assert (vectors.term_vec(st, "hotwire") == hot1).all()
    # neighbors + phrase vectors round-trip through persistence
    vectors.unload(st)
    assert vectors.load(st)
    near = [n["term"] for n in vectors.similar_terms(st, "hotwire", k=4)]
    assert "unground" in near, near
    pv = vectors.phrase_vec(st, "breaker panel")
    assert pv is not None and abs(float((pv * pv).sum()) - 1.0) < 1e-5
    assert vectors.phrase_vec(st, "zzz qqq") is None
    st.close()


# -------------------------------------------------------------- thesaurus --

def test_thesaurus():
    hw = hw_at("thesaurus.db")
    st = hw.store
    stats = thesaurus.stats(st)
    assert stats["seed"] >= 100, stats
    # bidirectional, exact-phrase expansion from the seed
    exp = [e["term"] for e in thesaurus.expand("hot wire", st)]
    assert "ungrounded conductor" in exp, exp
    back = [e["term"] for e in thesaurus.expand("ungrounded conductor", st)]
    assert "hot wire" in back, back
    assert thesaurus.expand("bedrock", st) == []       # never substring
    # seeding is idempotent across reopen
    hw.close()
    hw = hw_at("thesaurus.db")
    assert thesaurus.stats(hw.store)["seed"] == stats["seed"]
    st = hw.store
    # miner: definitional phrasing -> unverified proposal WITH citation
    doc = st.add_document("Vent Manual", "plumbing", None, "text")
    cid = st.add_chunk(doc, 0,
                       "The wet vent, also known as the combination vent, "
                       "serves two fixtures on one stack.")
    mined = thesaurus.mine(st)
    assert mined["proposed"] == 1 and mined["scanned"] >= 1, mined
    props = thesaurus.list_proposed(st)
    assert len(props) == 1, props
    p = props[0]
    assert p["term"] == "combination vent" and p["canonical"] == "wet vent", p
    assert p["source_chunk"] == cid and p["doc_title"] == "Vent Manual"
    # unverified proposals never expand; approval promotes them
    assert thesaurus.expand("combination vent", st) == []
    assert thesaurus.approve(st, p["id"])
    assert not thesaurus.approve(st, p["id"])          # gate is one-shot
    exp = [e for e in thesaurus.expand("combination vent", st)]
    assert exp and exp[0]["term"] == "wet vent" and exp[0]["why"] == "mined"
    # re-mine skips known pairs; reject gate marks without deleting
    assert thesaurus.mine(st)["proposed"] == 0
    st.add_chunk(doc, 1, "A cleanout is commonly called an access tee "
                         "by the old crews.")
    thesaurus.mine(st)
    p2 = thesaurus.list_proposed(st)[0]
    assert thesaurus.reject(st, p2["id"])
    assert thesaurus.expand(p2["term"], st) == []
    assert thesaurus.stats(st)["rejected"] == 1
    hw.close()


# ----------------------------------------------------------------- search --

def seeded_kb(name: str) -> Heartwood:
    """A mini knowledge base: two electrical docs, one plumbing, one roofing.
    The ampacity chunk NEVER says 'hot wire' — only the meaning bridge can
    find it from field words."""
    hw = hw_at(name)
    ingest.add_text(hw.store, "Conductor Ampacity",
                    "The ungrounded conductor shall be sized per the "
                    "ampacity tables. Select the ungrounded conductor size "
                    "so the ampacity exceeds the circuit breaker rating.",
                    trade="electrical")
    ingest.add_text(hw.store, "Breaker Coordination",
                    "The circuit breaker protects the ungrounded conductor. "
                    "Verify the breaker rating against the conductor size "
                    "before energizing the panelboard.",
                    trade="electrical")
    ingest.add_text(hw.store, "Trap Seals",
                    "The fixture trap shall maintain a 2 in water seal. "
                    "Trap seal primers protect seals at floor drains.",
                    trade="plumbing")
    ingest.add_text(hw.store, "Membrane Laps",
                    "The roofing membrane shall lap 6 in at every seam. "
                    "Flash the curb before the membrane cures.",
                    trade="roofing")
    ingest.rebuild(hw.store)
    return hw


def test_search():
    hw = seeded_kb("search.db")
    st = hw.store
    # the meaning bridge: field words -> canonical chunk that never says them
    phrases, expansions = search.expand_query(st, "hot wire size")
    assert "ungrounded conductor" in phrases, phrases
    assert any(e["term"] == "ungrounded conductor" and e["why"] == "thesaurus"
               for e in expansions), expansions
    results = search.search(st, "hot wire size")
    assert results, "no results"
    top = results[0]
    assert top["doc_title"] == "Conductor Ampacity", top
    assert "hot wire" not in st.chunk(top["chunk_id"])["text"].lower()
    assert any(w["term"] == "ungrounded conductor" for w in top["why"]), top["why"]
    assert search.confident(results), results[0]["score"]
    assert 0.0 <= top["score"] <= 1.05 and top["bm25"] > 0
    assert top["snippet"] and not top["unverified"]
    # honest refusal: off-trade question fails the confidence gate
    off = search.search(st, "best pizza dough recipe")
    assert not search.confident(off), off
    out = hw.ask("best pizza dough recipe")
    assert out["refused"] and out["blocks"] == [], out
    assert "Not in the knowledge base yet" in out["message"]
    # trade filter: plumbing search never surfaces electrical chunks
    plumbing = search.search(st, "seal rating size", trade="plumbing")
    assert plumbing, "trade filter returned nothing"
    assert all(r["trade"] in ("plumbing", "general", None) for r in plumbing), plumbing
    hw.close()


# ----------------------------------------------------------------- digest --

def test_digest():
    hw = seeded_kb("digest.db")
    st = hw.store
    ids = [cid for cid, _ in st.iter_chunks()]
    picks = digest.summarize(st, ids, max_sentences=4)
    assert 0 < len(picks) <= 4
    texts = {cid: text for cid, text in st.iter_chunks()}
    seen = set()
    for p in picks:
        # verbatim: every sentence is a substring of its cited chunk
        assert p["text"] in texts[p["chunk_id"]], p
        assert p["doc_title"] and isinstance(p["chunk_id"], int)
        seen.add(p["text"])
    assert len(seen) == len(picks)          # MMR: no repeats
    # accepts search-result dicts too, and is deterministic
    again = digest.summarize(st, [{"chunk_id": i} for i in ids], max_sentences=4)
    assert [p["text"] for p in again] == [p["text"] for p in picks]
    # sentence splitter: decimals, abbreviations, code refs stay whole
    sents = digest.split_sentences(
        "The conductor shall be sized per NEC 310.16. Min. burial depth is "
        "24 in. below grade. Is the feeder 100 amps? Yes.")
    assert sents[0].endswith("NEC 310.16."), sents
    assert any(s.startswith("Min.") for s in sents), sents
    hw.close()


# ---------------------------------------------------------------- restate --

def test_restate():
    entries = [dict(e, approved=True) for e in thesaurus.seed_entries()]
    # templates + approved swap (expected text generated from the reference)
    out = restate.restate("The ungrounded conductor shall be sized at 12 AWG.",
                          "plain", entries=entries)
    assert out["text"] == ("The code requires The hot wire to be sized "
                           "at 12 AWG."), out
    assert out["changed"] and out["templated"] and out["safe"]
    assert out["subs"] == [{"from": "ungrounded conductor", "to": "hot wire"}]
    out = restate.restate(
        "Branch circuits shall not exceed 80% of the breaker rating.",
        "plain", entries=entries)
    assert out["text"].startswith("The code prohibits Branch circuits from"), out
    out = restate.restate(
        "Ampacity means the maximum current a conductor can carry.",
        "plain", entries=entries)
    assert out["text"].startswith("Ampacity — that is,"), out
    # mode 'code': field words -> canonical
    out = restate.restate("Land the hot wire on the breaker.", "code",
                          entries=entries)
    assert "ungrounded conductor" in out["text"], out
    assert "circuit breaker" in out["text"], out
    # NUMBER LOCK fail-closed: a swap that would eat a protected token
    trap = [{"field": "twelve gauge", "canonical": "12 AWG", "approved": True}]
    src = "Use 12 AWG for the branch circuit."
    out = restate.restate(src, "plain", entries=trap)
    assert out["text"] == src and not out["changed"], out
    # approved-only: unapproved entries never fire
    unapproved = [{"field": "hot wire", "canonical": "ungrounded conductor",
                   "approved": False}]
    src = "The ungrounded conductor connects the panel."
    out = restate.restate(src, "plain", entries=unapproved)
    assert out["text"] == src and not out["subs"], out
    # whole-token: "rock" never fires inside "bedrock"
    out = restate.restate("The bedrock supports the footing.", "code",
                          entries=entries)
    assert "bedrock" in out["text"], out
    # number multiset itself
    assert restate.number_multiset("Use 12 AWG at 100 psi per NEC 210.8") == \
        sorted(["12 awg", "100 psi", "nec 210.8"])


# ----------------------------------------------------------------- ingest --

def make_fake_tradeforge(path: str) -> None:
    """A fake companion-app KB with the REAL schema column names."""
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE kb_documents (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          source TEXT NOT NULL UNIQUE, module TEXT, trade TEXT, title TEXT,
          ingested_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE kb_chunks (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          document_id INTEGER NOT NULL REFERENCES kb_documents(id),
          ord INTEGER NOT NULL, heading TEXT, content TEXT NOT NULL
        );
        CREATE TABLE journeyman_thesaurus_proposed (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          term TEXT NOT NULL, canonical TEXT NOT NULL,
          chunk_id INTEGER, status TEXT DEFAULT 'unverified',
          created_at TEXT DEFAULT (datetime('now'))
        );
    """)
    db.execute("INSERT INTO kb_documents(source, module, trade, title) "
               "VALUES ('grounding.md', 'kb', 'electrical', 'Grounding Guide')")
    db.execute("INSERT INTO kb_documents(source, module, trade, title) "
               "VALUES ('venting.md', 'kb', 'plumbing', 'Vent Sizing')")
    db.executemany(
        "INSERT INTO kb_chunks(document_id, ord, heading, content) "
        "VALUES (?, ?, ?, ?)",
        [(1, 0, "Bonding", "The equipment grounding conductor bonds every "
                           "metal enclosure to the panelboard."),
         (1, 1, None, "Size the grounding electrode conductor per the "
                      "service rating."),
         (2, 0, "Vents", "Every fixture trap shall be vented within its "
                         "trap arm distance.")])
    db.execute("INSERT INTO journeyman_thesaurus_proposed(term, canonical, status) "
               "VALUES ('ground rod', 'grounding electrode', 'approved')")
    db.execute("INSERT INTO journeyman_thesaurus_proposed(term, canonical, status) "
               "VALUES ('bogus', 'nonsense', 'unverified')")
    db.commit()
    db.close()


def test_ingest_import():
    hw = hw_at("import.db")
    # error paths first: absent and foreign dbs give a clear error, no crash
    out = hw.import_tradeforge(os.path.join(TMP, "nope.sqlite"))
    assert out["error"] and "not found" in out["error"], out
    foreign = os.path.join(TMP, "foreign.sqlite")
    db = sqlite3.connect(foreign)
    db.execute("CREATE TABLE misc (x)")
    db.commit(); db.close()
    out = hw.import_tradeforge(foreign)
    assert out["error"] and "kb_documents" in out["error"], out
    assert out["docs"] == 0 and out["chunks"] == 0
    # the real import: docs + chunks + approved-only thesaurus rows
    fake = os.path.join(TMP, "tradeforge.sqlite")
    make_fake_tradeforge(fake)
    out = hw.import_tradeforge(fake)
    assert out["error"] is None, out
    assert out["docs"] == 2 and out["chunks"] == 3 and out["thesaurus"] == 1, out
    exp = [e["term"] for e in thesaurus.expand("ground rod", hw.store)]
    assert "grounding electrode" in exp, exp
    assert thesaurus.expand("bogus", hw.store) == []   # unverified stayed put
    # imported content is immediately searchable, trained, and cited
    st = hw.status()
    assert st["documents"] == 2 and st["chunks"] == 3 and st["trained"], st
    res = hw.search("equipment grounding conductor")
    assert res and res[0]["doc_title"] == "Grounding Guide", res
    assert res[0]["origin"] == "import"
    # re-import is idempotent (deduped by document source)
    out = hw.import_tradeforge(fake)
    assert out["docs"] == 0 and out["chunks"] == 0, out
    assert hw.status()["documents"] == 2
    hw.close()


def test_ingest_rfis_and_notes():
    from rfi_stamper.core import RFIRecord
    hw = seeded_kb("notes.db")
    answered = RFIRecord(number="007", title="Feeder conductor size",
                         question="What size feeds the new panelboard?",
                         answer="Use the ungrounded conductor size from the "
                                "ampacity tables for the 100 amps feeder.")
    blank = RFIRecord(number="008", title="Paint color",
                      question="Which color?", answer="")
    as_dict = {"number": "009", "subject": "Trap seal depth",
               "question": "Minimum seal?",
               "answer": "Maintain a 2 in water seal at every fixture trap "
                         "per the plumbing sheets."}
    out = hw.capture_rfis([answered, blank, as_dict])
    assert out == {"captured": 2, "skipped": 1}, out
    out = hw.capture_rfis([answered, as_dict])          # dedupe by head
    assert out == {"captured": 0, "skipped": 2}, out
    notes = hw.notes("unverified")
    assert len(notes) == 2 and all(n["origin"] == "rfi" for n in notes), notes
    assert notes[0]["text"].startswith("RFI 007 — Feeder conductor size"), notes[0]
    assert "Q: What size feeds" in notes[0]["text"]

    # unverified flag propagates through ask(): the RFI note is found but
    # marked, so the GUI can label it "shop note — unverified"
    res = hw.ask("feeder conductor size for the panelboard")
    assert not res["refused"], res
    note_blocks = [b for b in res["blocks"] if b["kind"] == "note"]
    assert note_blocks, res["blocks"]
    assert all(b["unverified"] for b in note_blocks), note_blocks
    assert all("[source:" in b["text"] for b in res["blocks"]), res["blocks"]
    trusted_kinds = {b["kind"] for b in res["blocks"] if not b["unverified"]}
    assert trusted_kinds <= {"quote", "restated", "summary"}, trusted_kinds
    # 'shown' feedback was logged for every cited chunk
    shown = hw.store.db.execute(
        "SELECT COUNT(*) FROM hw_feedback WHERE kind='shown'").fetchone()[0]
    cited = {b["chunk_id"] for b in res["blocks"]}
    assert shown >= len(cited) > 0, (shown, cited)

    # excluding unverified content removes the note from every answer
    res2 = hw.ask("feeder conductor size for the panelboard",
                  include_unverified=False)
    assert all(not b["unverified"] for b in res2["blocks"]), res2["blocks"]

    # the human gate: trust -> unflagged; reject -> gone entirely
    note_id = notes[0]["id"]
    assert hw.trust_note(note_id)
    res3 = hw.ask("feeder conductor size for the panelboard")
    for b in res3["blocks"]:
        if b["chunk_id"] in {nb["chunk_id"] for nb in note_blocks}:
            assert not b["unverified"], b
    assert hw.notes("trusted")[0]["id"] == note_id
    other_id = notes[1]["id"]
    assert hw.reject_note(other_id)
    assert hw.store.note_document(other_id) is None    # de-indexed
    assert not hw.trust_note(other_id)                 # rejection is final
    left = hw.search("trap seal depth fixture")
    assert all(r["origin"] != "rfi" or "007" in r["doc_title"] for r in left)

    # teach + mark_used: lane-1 usage boost reorders, never invents
    hw.teach("Shop note: torque the breaker lugs to the panel schedule.",
             author="field", origin="note")
    q = "feeder conductor size for the panelboard"
    top = hw.search(q)[0]
    hw.mark_used(q, top["chunk_id"])
    used = hw.store.used_feedback()
    assert used and used[-1][1] == top["chunk_id"], used
    boosted = hw.search(q)[0]
    assert boosted["chunk_id"] == top["chunk_id"]
    assert boosted["score"] <= top["score"] + search.USAGE_BOOST_CAP + 1e-9

    st = hw.status()
    assert st["notes_trusted"] == 1 and st["notes_rejected"] == 1, st
    assert st["thesaurus"]["seed"] >= 100
    hw.close()


# ---------------------------------------------------------------- offline --

def test_offline_by_construction():
    """No module in the engine may import networking (CLAUDE.md invariant 1)."""
    pkg = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "rfi_stamper", "heartwood")
    banned = re.compile(
        r"^\s*(?:import|from)\s+(?:socket|ssl|urllib|http|requests"
        r"|xmlrpc|ftplib|smtplib)\b", re.MULTILINE)
    for path in glob.glob(os.path.join(pkg, "*.py")):
        src = open(path, encoding="utf-8").read()
        assert not banned.search(src), f"networking import in {path}"
    # and no gui import either — the engine stands alone
    for path in glob.glob(os.path.join(pkg, "*.py")):
        src = open(path, encoding="utf-8").read()
        assert "rfi_stamper.gui" not in src and "tkinter" not in src, path


def main():
    test_store()
    print("PASS store round trip, idempotent schema, BM25")
    test_lex()
    print("PASS lex: protected tokens + Porter stemmer")
    test_vectors()
    print("PASS vectors: exact signatures, corpus geometry, determinism")
    test_thesaurus()
    print("PASS thesaurus: seed, expand, miner, approve/reject gates")
    test_search()
    print("PASS search: meaning bridge, honest refusal, trade filter")
    test_digest()
    print("PASS digest: verbatim + citations + diversity")
    test_restate()
    print("PASS restate: number lock, approved-only swaps, templates")
    test_ingest_import()
    print("PASS ingest: knowledge-base import + error paths")
    test_ingest_rfis_and_notes()
    print("PASS ingest: RFI capture, note lanes, unverified flag, feedback")
    test_offline_by_construction()
    print("PASS offline by construction (no network, no gui)")
    print("HEARTWOOD TEST PASSED  (the Old Hand answers only from the store)")
    print("stores in", TMP)


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print("HEARTWOOD TEST FAILED:", e)
        sys.exit(1)
