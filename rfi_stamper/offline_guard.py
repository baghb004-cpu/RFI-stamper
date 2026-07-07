"""Process-wide network kill-switch for NDA-safe operation.

Monkeypatches the stdlib ``socket`` entry points so that any attempt to
reach a non-local host raises :class:`OfflineError` before a single packet
leaves the machine -- name resolution included.  AF_UNIX sockets are always
allowed (the GUI needs the X11/Wayland display socket).

This is defense-in-depth, NOT a sandbox: code that imports ``_socket``
directly, or that captured references to the originals before ``install()``
ran, is not covered.  Its job is to turn an accidental network call (a
leftover cloud client, a library phoning home) into a loud, immediate error.
"""
from __future__ import annotations

import ipaddress
import socket

_MSG = "network access blocked by RFI Stamper offline guard"

# originals kept at module level so uninstall() can restore them exactly
_originals: dict = {}
_had_own: dict = {}          # was the name in socket.socket.__dict__ before us?
_allow_localhost = False


class OfflineError(OSError):
    """Raised when the offline guard blocks a network operation."""


def _host_of(address):
    return address[0] if isinstance(address, tuple) and address else address


def _is_local(host) -> bool:
    if host is None:
        return True                              # passive / wildcard binds
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    if not isinstance(host, str):
        return False
    h = host.strip("[]").rstrip(".").lower()
    if h in ("", "localhost") or h.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


def _check(family, address) -> None:
    if family == getattr(socket, "AF_UNIX", object()):
        return                                   # display sockets etc.
    if _allow_localhost and _is_local(_host_of(address)):
        return
    raise OfflineError(_MSG)


def _g_connect(self, address):
    _check(self.family, address)
    return _originals["connect"](self, address)


def _g_connect_ex(self, address):
    _check(self.family, address)
    return _originals["connect_ex"](self, address)


def _g_create_connection(address, *args, **kwargs):
    if not (_allow_localhost and _is_local(_host_of(address))):
        raise OfflineError(_MSG)
    return _originals["create_connection"](address, *args, **kwargs)


def _g_getaddrinfo(host, *args, **kwargs):
    if _allow_localhost and _is_local(host):
        return _originals["getaddrinfo"](host, *args, **kwargs)
    raise OfflineError(_MSG)


def install(allow_localhost: bool = False) -> None:
    """Activate the guard. Idempotent; repeat calls just update the flag."""
    global _allow_localhost
    _allow_localhost = allow_localhost
    if _originals:
        return
    for name, wrapper in (("connect", _g_connect), ("connect_ex", _g_connect_ex)):
        _had_own[name] = name in socket.socket.__dict__
        _originals[name] = getattr(socket.socket, name)
        setattr(socket.socket, name, wrapper)
    _originals["create_connection"] = socket.create_connection
    _originals["getaddrinfo"] = socket.getaddrinfo
    socket.create_connection = _g_create_connection
    socket.getaddrinfo = _g_getaddrinfo


def uninstall() -> None:
    """Restore the original socket entry points. Idempotent."""
    if not _originals:
        return
    for name in ("connect", "connect_ex"):
        if _had_own.get(name):
            setattr(socket.socket, name, _originals[name])
        else:                                    # was inherited from _socket
            delattr(socket.socket, name)
    socket.create_connection = _originals["create_connection"]
    socket.getaddrinfo = _originals["getaddrinfo"]
    _originals.clear()
    _had_own.clear()


def is_active() -> bool:
    return bool(_originals)
