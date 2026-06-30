#!/usr/bin/env python3
"""Normalize Talos Module.validate for miscast CUSTOM_VALIDATE_CMD.

miscast interprets rc=0 as ACCEPT and nonzero as REJECT. Talos' probe prints VALID /
INVALID / DECODE-ERROR but may return 0 for all of them, so this wrapper maps stdout
to the exit status miscast expects.
"""
import os
import subprocess
import sys


VALPROBE = os.environ.get("TALOS_VALPROBE", os.path.expanduser("~/wasm-engines/talos/valprobe2"))


def main():
    if len(sys.argv) < 2:
        print("usage: talos_validate.py <wat> [wasm]", file=sys.stderr)
        return 2
    wat = sys.argv[1]
    p = subprocess.run([VALPROBE, wat], capture_output=True, text=True)
    both = (p.stdout + p.stderr).strip()
    if both:
        print(both)
    return 0 if both.strip() == "VALID" else 1


if __name__ == "__main__":
    raise SystemExit(main())
