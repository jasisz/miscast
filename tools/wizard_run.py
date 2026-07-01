#!/usr/bin/env python3
"""Normalize the local Wizard wrapper for miscast CUSTOM_CMD.

The shell wrapper has a background watchdog; when called under capture_output the
watchdog can keep stdout/stderr pipes open until its timeout. This wrapper sends
Wizard output to real temp files, waits for the process, then prints just the
normalized result/error for miscast.
"""
import os
import re
import subprocess
import sys
import tempfile


WIZARD = os.environ.get("WIZARD_BIN", os.path.expanduser("~/wasm-engines/wizard/wizeng"))
TIMEOUT = int(os.environ.get("WIZARD_TIMEOUT", "30"))
_PURE_NUM = re.compile(r"\s*(-?(?:0x[0-9a-fA-F]+|\d+))(?:[uU]?[lL])?\s*\Z")


def main():
    if len(sys.argv) < 4:
        print("usage: wizard_run.py <wat> <wasm> <export> [args...]", file=sys.stderr)
        return 2
    _wat, wasm, export, *args = sys.argv[1:]
    with tempfile.NamedTemporaryFile("w+", delete=True) as out, tempfile.NamedTemporaryFile("w+", delete=True) as err:
        p = subprocess.Popen([WIZARD, f"--invoke={export}", "--print-result", wasm] + args,
                             stdout=out, stderr=err, text=True)
        try:
            rc = p.wait(timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()
            print("miscast: timeout", file=sys.stderr)
            return 124
        out.seek(0)
        err.seek(0)
        stdout, stderr = out.read().strip(), err.read().strip()
    both = (stdout + "\n" + stderr).strip()
    low = both.lower()
    if any(k in low for k in ("trap", "unreachable", "out of bounds", "indirect call type mismatch",
                              "cast failure", "execution failed")):
        print("TRAP")
        return 1
    m = _PURE_NUM.match(stdout)
    if m:
        print(str(int(m.group(1), 0)))
        return 0
    if both:
        print(both, file=sys.stderr)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
