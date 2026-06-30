#!/usr/bin/env python3
"""Normalize the local Talos runner for miscast CUSTOM_CMD.

Usage from miscast:
  CUSTOM_CMD="python3 tools/talos_run.py {wat} {wasm} {export}" ...
"""
import os
import re
import subprocess
import sys


RUNNER = os.environ.get("TALOS_RUNNER", os.path.expanduser("~/wasm-engines/talos/runner"))


def main():
    if len(sys.argv) < 4:
        print("usage: talos_run.py <wat> <wasm> <export> [args...]", file=sys.stderr)
        return 2
    wat, _wasm, export, *args = sys.argv[1:]
    cmd = [RUNNER, wat, export] + args
    p = subprocess.run(cmd, capture_output=True, text=True)
    both = (p.stdout + p.stderr).strip()
    low = both.lower()
    if any(k in low for k in ("trap", "unreachable", "out of bounds", "indirect call type mismatch",
                              "cast failure", "divide by zero", "execution failed")):
        print("TRAP")
        return 1
    nums = re.findall(r"-?\d+", p.stdout)
    if p.returncode == 0 and nums:
        print(nums[-1])
        return 0
    if nums and not any(k in low for k in ("error", "invalid", "ill-shaped", "unknown", "unsupported")):
        print(nums[-1])
        return 0
    if both:
        print(both, file=sys.stderr)
    return p.returncode or 1


if __name__ == "__main__":
    raise SystemExit(main())
