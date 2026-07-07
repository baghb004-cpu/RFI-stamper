"""Run every test script in this folder in a subprocess; exit nonzero on any
failure.  GUI test runs under xvfb-run automatically when available."""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
GUI_TESTS = {"test_gui_construct.py"}


def main():
    failures = []
    tests = sorted(f for f in os.listdir(HERE)
                   if f.startswith(("test_", "smoke_")) and f.endswith(".py"))
    for t in tests:
        cmd = [sys.executable, os.path.join(HERE, t)]
        if t in GUI_TESTS and not os.environ.get("DISPLAY"):
            xvfb = shutil.which("xvfb-run")
            if xvfb:
                cmd = [xvfb, "-a"] + cmd
            else:
                print(f"-- {t}: SKIPPED (no display, no xvfb-run)")
                continue
        print(f"-- {t}")
        r = subprocess.run(cmd, cwd=os.path.dirname(HERE))
        if r.returncode != 0:
            failures.append(t)
    print()
    if failures:
        print("FAILED:", ", ".join(failures))
        return 1
    print(f"ALL {len(tests)} TEST SCRIPT(S) PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
