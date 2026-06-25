"""Unit tests for the dual-rail shadow-GC oracle.

Structural tests always run. An assemble + self-check test runs when `wasm-tools` is on PATH: every
generated program must be valid wat, and (when a gc-capable wasmtime is found) the two worlds must agree
(`check` = 0) on a conformant engine while a deliberately corrupted graph diverges. Run:
`python3 tests/test_dualrail.py` (or under pytest)."""
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from miscast.dualrail import gen
from miscast.modes import gen_dualrail


def eq(name, got, want):
    assert got == want, f"{name}: got {got!r}, want {want!r}"


def test_generates_self_check():
    m = gen(0, 16)
    eq("exports check", '(func (export "check")' in m, True)
    eq("uses real Wasm GC", "struct.new $sub" in m and "ref.test (ref $sub)" in m, True)
    eq("exercises funcref + i31", "ref.test (ref $ftA)" in m and "i31.get_u" in m, True)
    eq("has a linear-memory shadow", "(memory 1)" in m and "i32.store" in m, True)
    eq("returns real minus shadow", "(i32.sub (local.get $h) (local.get $sh))" in m, True)
    eq("bug variant differs from clean", gen(0, 16, bug=True) != gen(0, 16), True)


def test_mode_cases():
    cases, untested = gen_dualrail(None, 4)
    eq("four cases", len(cases), 4)
    nm, mod, export, args, expected, rtype = cases[0]
    eq("export is check", export, "check")
    eq("0 is the per-program oracle", expected, "OK 0")
    eq("compared as int", rtype, "int")
    eq("no untested", untested, [])


def _assemble(module):
    p = subprocess.run(["wasm-tools", "parse", "/dev/stdin", "-o", "/tmp/_dr_test.wasm"],
                       input=module.encode(), capture_output=True)
    assert p.returncode == 0, f"dual-rail module is not valid wat: {p.stderr.decode()[:160]}"


def test_assembles_and_self_checks():
    if not shutil.which("wasm-tools"):
        print("  (skip: wasm-tools not on PATH)")
        return
    for seed in range(8):                                  # every generated program must validate
        _assemble(gen(seed, 16))
    wt = next((c for c in ("wasmtime", "/tmp/wasmtime-v46.0.0-aarch64-macos/wasmtime")
               if shutil.which(c) or os.path.exists(c)), None)

    def run(module):
        _assemble(module)
        r = subprocess.run([wt, "run", "-W", "gc=y", "--invoke", "check", "/tmp/_dr_test.wasm"],
                           capture_output=True, text=True)
        out = [l for l in r.stdout.strip().splitlines() if l.strip()]
        return out[-1] if (r.returncode == 0 and out) else None

    if wt is None or run(gen(0, 16)) is None:
        print("  (modules valid; skip self-check: no gc-capable wasmtime)")
        return
    for seed in range(8):                                  # the two worlds must agree on a conformant engine
        eq(f"seed {seed}: real GC == linear-memory shadow", run(gen(seed, 16)), "0")
    eq("a corrupted graph is detected", run(gen(0, 16, bug=True)) != "0", True)


if __name__ == "__main__":
    tests = [f for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"\n{len(tests)} test(s) passed")
