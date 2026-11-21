"""Smoke test for the BUILT executables (not the source tree).

    python tests/exe_smoke.py <planloom-cli exe> [<Planloom GUI exe>]

1. Builds the same synthetic plan set + RFI pair as smoke_test.py (one
   rotation-0 sheet, one /Rotate 90 sheet).
2. Runs the frozen CLI's `stamp` on it.  Passes only when the exe exits 0,
   writes the stamped PDF, and its *_report.txt ends in PASS (invariant 4:
   verification must pass before anything is delivered).
3. If a GUI exe is given, starts it and requires it to still be running
   after a settle period (a frozen build that is missing a module or a
   data file dies at import, long before that), then closes it.

Not picked up by run_all.py on purpose: it needs built executables.  The
Windows release workflow runs it after PyInstaller and before anything is
attached to a release.
"""
import glob
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from smoke_test import make_plan, make_rfi       # noqa: E402

GUI_SETTLE_S = 25


def check_cli(cli: str) -> None:
    tmp = tempfile.mkdtemp(prefix="exe_smoke_")
    plan = os.path.join(tmp, "plan.pdf")
    rfis = os.path.join(tmp, "rfis")
    os.makedirs(rfis)
    out = os.path.join(tmp, "plan_out.pdf")
    make_plan(plan)
    make_rfi(os.path.join(rfis, "RFI001.pdf"), "001", "Fake Pipe Routing",
             "T-1.01", "Where should the fake pipe route around the three "
             "fixtures?", "Route the fake pipe per detail 5 on this sheet.")
    make_rfi(os.path.join(rfis, "RFI002.pdf"), "002", "Missing Cleanout",
             "T-1.02", "No cleanout is shown at the end of the fake run.", "")

    proc = subprocess.run([cli, "stamp", "-p", plan, "-r", rfis, "-o", out],
                          capture_output=True, text=True, timeout=600)
    print(proc.stdout[-2000:])
    if proc.returncode != 0:
        print(proc.stderr[-4000:])
        raise SystemExit(f"CLI exited {proc.returncode}")
    if not os.path.isfile(out) or os.path.getsize(out) == 0:
        raise SystemExit("CLI did not write the stamped PDF")
    reports = glob.glob(os.path.join(tmp, "*_report.txt"))
    if not reports:
        raise SystemExit("CLI wrote no *_report.txt")
    with open(reports[0], encoding="utf-8", errors="replace") as fh:
        lines = [ln.strip() for ln in fh if ln.strip()]
    if not lines or "PASS" not in lines[-1]:
        raise SystemExit(f"report does not end in PASS: {lines[-1:]}")
    print("CLI OK: stamped, verified, report ends in PASS")


def check_gui(gui: str) -> None:
    proc = subprocess.Popen([gui])
    try:
        time.sleep(GUI_SETTLE_S)
        code = proc.poll()
        if code is not None:
            raise SystemExit(f"GUI exited early with code {code}")
        print(f"GUI OK: still running after {GUI_SETTLE_S} s")
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()


def main(argv) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    check_cli(argv[1])
    if len(argv) > 2:
        check_gui(argv[2])
    print("EXE SMOKE PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
