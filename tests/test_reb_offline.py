"""Regression: offline_guard must block unconnected UDP sendto/sendmsg, not
just TCP connect. Fast, headless, cross-platform (no tk, no AF_UNIX)."""
from __future__ import annotations

import os
import socket
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rfi_stamper import offline_guard as guard


def expect(exc, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except exc:
        return
    raise AssertionError(f"expected {exc.__name__} from {fn.__name__}")


def test_udp_sendto_blocked():
    guard.install()
    try:
        assert guard.is_active()
        sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # unconnected UDP packet to a non-local host must be blocked
        expect(guard.OfflineError, sk.sendto, b"leak", ("93.184.216.34", 53))
        if hasattr(sk, "sendmsg"):
            expect(guard.OfflineError, sk.sendmsg,
                   [b"leak"], [], 0, ("93.184.216.34", 53))
        sk.close()
    finally:
        guard.uninstall()


def test_udp_localhost_allowed():
    guard.install(allow_localhost=True)
    try:
        sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # loopback destination passes the guard (packet may still land nowhere)
        sk.sendto(b"ok", ("127.0.0.1", 9))
        if hasattr(sk, "sendmsg"):
            sk.sendmsg([b"ok"], [], 0, ("127.0.0.1", 9))
        sk.close()
    finally:
        guard.uninstall()


def test_udp_still_blocked_localhost_off():
    guard.install()  # allow_localhost False
    try:
        sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        expect(guard.OfflineError, sk.sendto, b"x", ("127.0.0.1", 9))
        sk.close()
    finally:
        guard.uninstall()


def test_uninstall_restores_sendto():
    guard.install()
    guard.uninstall()
    assert not guard.is_active()
    # unpatched send-path works again on loopback (no OfflineError)
    sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sk.sendto(b"x", ("127.0.0.1", 9))
    assert not hasattr(socket.socket.sendto, "__wrapped_by_guard__")
    sk.close()


def test_connected_udp_send_not_broken():
    # a connected UDP socket sends without an address; guard must not raise
    guard.install(allow_localhost=True)
    try:
        sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sk.connect(("127.0.0.1", 9))
        sk.send(b"ok")
        sk.close()
    finally:
        guard.uninstall()


if __name__ == "__main__":
    test_udp_sendto_blocked()
    test_udp_localhost_allowed()
    test_udp_still_blocked_localhost_off()
    test_uninstall_restores_sendto()
    test_connected_udp_send_not_broken()
    print("REB OFFLINE TESTS OK")
