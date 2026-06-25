"""Unit tests for the spec-invalid GC validation battery.

Structural tests always run. An assemble + validate test runs when `wasm-tools` is on PATH: every module
must assemble (it has to be a well-formed binary so a SUT can be asked to load it) yet be REJECTED by the
ground-truth validator (`wasm-tools validate`) — that rejection is the oracle the differential holds a SUT
to. Run: `python3 tests/test_invalid.py`."""
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from miscast.invalid import MODULES, segs


def eq(name, got, want):
    assert got == want, f"{name}: got {got!r}, want {want!r}"


def test_battery_shape():
    eq("battery is non-empty", len(MODULES) >= 8, True)
    for label, wat, reason in MODULES:
        eq(f"{label}: is a module", wat.startswith("(module"), True)
        eq(f"{label}: exports a runnable f", '(export "f")' in wat, True)
        eq(f"{label}: has a reason", bool(reason), True)


def test_segs():
    s = segs()
    eq("one seg per module", len(s), len(MODULES))
    eq("seg name is prefixed", s[0]["name"].startswith("invalid:"), True)
    eq("seg is marked invalid", s[0]["kind"], "invalid")
    eq("seg is not stateful", s[0]["stateful"], False)


def test_assembles_but_invalid():
    if not shutil.which("wasm-tools"):
        print("  (skip: wasm-tools not on PATH)")
        return
    for label, wat, _reason in MODULES:
        p = subprocess.run(["wasm-tools", "parse", "/dev/stdin", "-o", "/tmp/_iv_test.wasm"],
                           input=wat.encode(), capture_output=True)
        eq(f"{label}: assembles to a binary", p.returncode, 0)
        v = subprocess.run(["wasm-tools", "validate", "/tmp/_iv_test.wasm"], capture_output=True, text=True)
        eq(f"{label}: ground-truth validator REJECTS it", v.returncode != 0, True)


if __name__ == "__main__":
    tests = [f for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"\n{len(tests)} test(s) passed")
