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
    skipped = []
    tests = sorted(f for f in os.listdir(HERE)
                   if f.startswith(("test_", "smoke_")) and f.endswith(".py"))
    for t in tests:
        cmd = [sys.executable, os.path.join(HERE, t)]
        if t in GUI_TESTS:
            # Only POSIX X11 needs an xvfb/DISPLAY gate; win32/darwin have
            # native tkinter and run the GUI test directly.
            needs_xvfb = (sys.platform not in ("win32", "darwin")
                          and not os.environ.get("DISPLAY"))
            if needs_xvfb:
                xvfb = shutil.which("xvfb-run")
                if xvfb:
                    cmd = [xvfb, "-a"] + cmd
                else:
                    print(f"-- {t}: SKIPPED (no display, no xvfb-run)")
                    skipped.append(t)
                    continue
        print(f"-- {t}")
        r = subprocess.run(cmd, cwd=os.path.dirname(HERE))
        if r.returncode != 0:
            failures.append(t)
    print()
    ran = len(tests) - len(skipped)
    if failures:
        print("FAILED:", ", ".join(failures))
        return 1
    msg = f"ALL {ran} TEST SCRIPT(S) PASSED"
    if skipped:
        msg += f" ({len(skipped)} SKIPPED: {', '.join(skipped)})"
    print(msg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
