"""Tests for rfi_stamper.summarize and rfi_stamper.offline_guard. Plain python, no pytest."""
from __future__ import annotations

import os
import socket
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rfi_stamper import offline_guard as guard
from rfi_stamper.summarize import OfflineSummarizer, make_note


def expect(exc, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except exc as e:
        return e
    raise AssertionError(f"expected {exc.__name__} from {fn!r}{args!r}")


# ------------------------------------------------------------- summarize ---

def test_make_note_composes():
    q = "Should the conduit route through the slab penetration shown on the detail?"
    a = "Provide a 2 in. clearance and route the conduit below the beam per detail 5."
    note = make_note(q, a)
    assert note.startswith("Q: "), note
    assert " A: " in note, note
    assert "conduit" in note, note
    assert len(note) <= 240
    # deterministic
    assert make_note(q, a) == note
    # clipping at word boundary with ellipsis
    short = make_note(q, a, max_len=60)
    assert len(short) <= 60, short
    assert short.endswith("…"), short
    assert not short[:-1].endswith(" "), short


def test_make_note_unanswered():
    note = make_note("Is the footing elevation on the sheet correct?", "")
    assert note.startswith("Q: "), note
    assert note.endswith("Resp: not in file."), note
    assert " A: " not in note


def test_make_note_garbage():
    # never raises, whatever comes in
    for q, a in ((None, None), ("", ""), (12345, object()), ("\x00\x07" * 400, "???"),
                 ("   \n\t  ", "   "), ("a" * 5000, "b" * 5000)):
        note = make_note(q, a)  # type: ignore[arg-type]
        assert isinstance(note, str) and note, (q, a, note)
        assert len(note) <= 240
    # empty question -> readable placeholder body
    note = make_note("", "Approved as noted.")
    assert "question text not readable" in note, note
    # regression: "never raises" must hold for garbage max_len as well
    # (previously the except-path called int(max_len) and re-raised)
    for ml in (None, "abc", 3.7, -5, 0, True, 10**18):
        note = make_note("Is the beam ok?", "Provide detail.", max_len=ml)  # type: ignore[arg-type]
        assert isinstance(note, str), (ml, note)


def test_lexicon_beats_boilerplate():
    q = ("Thank you for your assistance with this request and your prompt attention. "
         "Should the conduit route through the slab penetration per the detail on "
         "the sheet, or be relocated below the footing? "
         "We appreciate your timely response on this matter.")
    a = ("We have reviewed the referenced request and offer the following. "
         "Provide 2 in. clearance and route the conduit below the beam per detail 5. "
         "Contact us with any further questions on this response.")
    note = make_note(q, a, max_len=400)
    qpart, apart = note.split(" A: ")
    assert "conduit" in qpart and "?" in qpart, note      # 2nd sentence won
    assert "Thank you" not in qpart, note
    assert "Provide" in apart and "beam" in apart, note   # directive sentence won
    assert "reviewed the referenced" not in apart, note


def test_offline_summarizer():
    class Rec:
        number = "042"
        title = "Duct clearance at beam"
        question = "Is there adequate clearance to install the duct below the beam?"
        answer = "Route the duct through the joist space and confirm the elevation."
    s = OfflineSummarizer().summarize(Rec())
    assert isinstance(s, str) and s.startswith("Q: "), s
    # broken rec -> None, no exception (caller falls back)
    assert OfflineSummarizer().summarize(object()) is None


# ----------------------------------------------------------------- guard ---

def closed_local_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_guard():
    assert not guard.is_active()
    guard.install()
    guard.install()                                      # idempotent
    assert guard.is_active()

    # blocked BEFORE any packet: name resolution itself raises
    expect(guard.OfflineError, socket.getaddrinfo, "example.com", 80)
    expect(guard.OfflineError, socket.create_connection, ("example.com", 80), 1)
    sk = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    expect(guard.OfflineError, sk.connect, ("93.184.216.34", 80))
    expect(guard.OfflineError, sk.connect_ex, ("93.184.216.34", 80))
    sk.close()
    # localhost is blocked too while allow_localhost is False
    expect(guard.OfflineError, socket.getaddrinfo, "localhost", 80)

    # allow_localhost: loopback passes the guard, hits the real (closed) port
    guard.install(allow_localhost=True)
    port = closed_local_port()
    try:
        socket.create_connection(("127.0.0.1", port), timeout=2)
        raise AssertionError("connect to closed port unexpectedly succeeded")
    except guard.OfflineError:
        raise AssertionError("guard blocked localhost despite allow_localhost=True")
    except OSError:
        pass                                             # ConnectionRefusedError etc.
    assert socket.getaddrinfo("localhost", 80)           # resolves locally
    # non-local still blocked
    expect(guard.OfflineError, socket.create_connection, ("example.com", 80), 1)

    # AF_UNIX always allowed
    a, b = socket.socketpair()
    a.sendall(b"ping")
    assert b.recv(4) == b"ping"
    a.close(); b.close()
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "guard.sock")
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(path)
        srv.listen(1)
        cli = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        cli.connect(path)                                # patched connect, AF_UNIX
        conn, _ = srv.accept()
        cli.sendall(b"ok")
        assert conn.recv(2) == b"ok"
        for s in (cli, conn, srv):
            s.close()

    guard.uninstall()
    guard.uninstall()                                    # idempotent
    assert not guard.is_active()
    assert socket.getaddrinfo("localhost", 80)           # originals restored
    assert socket.create_connection is not None
    sk = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sk.settimeout(1)
    assert isinstance(sk.connect_ex(("127.0.0.1", closed_local_port())), int)
    sk.close()


if __name__ == "__main__":
    test_make_note_composes()
    test_make_note_unanswered()
    test_make_note_garbage()
    test_lexicon_beats_boilerplate()
    test_offline_summarizer()
    test_guard()
    print("OFFLINE TESTS PASSED")
