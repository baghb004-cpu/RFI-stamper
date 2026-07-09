"""Shared filesystem primitives — the one atomic-write everybody uses.

Write beside the destination, flush + fsync, then ``os.replace``: a killed
process or crash can never leave a truncated file at the final path.  This
replaced four byte-identical private copies (transmittal / integrations /
fieldstitch / draft), which keep their old names as aliases so callers and
tests are untouched.
"""
from __future__ import annotations

import os


def atomic_write_bytes(data: bytes, out_path: str) -> None:
    tmp = out_path + ".part"
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, out_path)
