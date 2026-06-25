"""Unit tests for the extern.convert_any / any.convert_extern round-trip differential.

Structural tests always run. An assemble + self-check test runs when `wasm-tools` is on PATH: every
generated program must be valid wat, and (when a gc-capable wasmtime is found) every program must return its
original value on a conformant engine — the round-trip preserves the reference. Run:
`python3 tests/test_externconvert.py` (or under pytest)."""
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from miscast.externconvert import gen, _SHAPES
from miscast.modes import gen_externconvert


def eq(name, got, want):
    assert got == want, f"{name}: got {got!r}, want {want!r}"


def test_generates_roundtrip():
    m, val = gen(0)
    eq("pushes a GC ref out to externref", "extern.convert_any" in m, True)
    eq("brings it back to anyref", "any.convert_extern" in m, True)
    eq("reads the field back via a cast", "ref.cast (ref $s)" in m, True)
    eq("the round-tripped value appears", f"i32.const {val}" in m, True)


def test_mode_cases():
    cases, untested = gen_externconvert(None, 2)
    eq("two cases", len(cases), 2)
    nm, mod, export, args, expected, rtype = cases[0]
    eq("export is rt", export, "rt")
    eq("the per-program oracle is OK <value>", expected.startswith("OK "), True)
    eq("no untested", untested, [])


def _assemble(module):
    p = subprocess.run(["wasm-tools", "parse", "/dev/stdin", "-o", "/tmp/_ec_test.wasm"],
                       input=module.encode(), capture_output=True)
    assert p.returncode == 0, f"extern-convert module is not valid wat: {p.stderr.decode()[:160]}"


def test_assembles_and_conformant_roundtrips():
    if not shutil.which("wasm-tools"):
        print("  (skip: wasm-tools not on PATH)")
        return
    for s in range(len(_SHAPES)):                          # every generated program must validate
        _assemble(gen(s)[0])
    wt = next((c for c in ("wasmtime", "/tmp/wasmtime-v46.0.0-aarch64-macos/wasmtime")
               if shutil.which(c) or os.path.exists(c)), None)

    def run(module):
        _assemble(module)
        r = subprocess.run([wt, "run", "-W", "function-references=y,gc=y", "--invoke", "rt", "/tmp/_ec_test.wasm"],
                           capture_output=True, text=True)
        out = [l for l in r.stdout.strip().splitlines() if l.strip()]
        return out[-1] if (r.returncode == 0 and out) else None

    if wt is None or run(gen(0)[0]) is None:
        print("  (modules valid; skip self-check: no gc-capable wasmtime)")
        return
    for s in range(len(_SHAPES)):                          # the round-trip must preserve the value
        wat, val = gen(s)
        eq(f"shape {s}: round-trip preserves value", run(wat), str(val))


if __name__ == "__main__":
    tests = [f for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"\n{len(tests)} test(s) passed")
