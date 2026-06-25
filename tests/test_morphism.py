"""Unit tests for the representation-morphism oracle.

Structural tests always run. An assemble + self-check test runs when `wasm-tools` is on PATH: every
generated program must be valid wat, and (when a gc-capable wasmtime is found) all three real rails must
agree with the shadow (`check` = 0) on a conformant engine, while a deliberately corrupted graph diverges.
Run: `python3 tests/test_morphism.py` (or under pytest)."""
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from miscast.morphism import gen
from miscast.modes import gen_morphism


def eq(name, got, want):
    assert got == want, f"{name}: got {got!r}, want {want!r}"


def test_generates_three_rails_and_shadow():
    m = gen(0, 16)
    eq("exports check", '(func (export "check")' in m, True)
    eq("cast rail tests $sub", "(ref.test (ref $sub) (local.get $cur))" in m, True)
    eq("shard rail declares structurally-identical $subB", "(type $subB (sub $base" in m and "(ref.test (ref $subB)" in m, True)
    eq("tag rail is cast-free (typed arrays)", "array.get $arrS " in m, True)
    eq("has a linear-memory shadow", "(memory 1)" in m and "i32.store" in m, True)
    eq("returns an isolation bitmask", "(i32.shl (i32.ne (local.get $ss) (local.get $h)) (i32.const 2))" in m, True)
    eq("bug variant differs from clean", gen(0, 16, bug=True) != gen(0, 16), True)


def test_mode_cases():
    cases, untested = gen_morphism(None, 4)
    eq("four cases", len(cases), 4)
    nm, mod, export, args, expected, rtype = cases[0]
    eq("export is check", export, "check")
    eq("0 is the per-program oracle", expected, "OK 0")
    eq("compared as int", rtype, "int")
    eq("no untested", untested, [])


def _assemble(module):
    p = subprocess.run(["wasm-tools", "parse", "/dev/stdin", "-o", "/tmp/_mp_test.wasm"],
                       input=module.encode(), capture_output=True)
    assert p.returncode == 0, f"morphism module is not valid wat: {p.stderr.decode()[:160]}"


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
        r = subprocess.run([wt, "run", "-W", "gc=y", "--invoke", "check", "/tmp/_mp_test.wasm"],
                           capture_output=True, text=True)
        out = [l for l in r.stdout.strip().splitlines() if l.strip()]
        return out[-1] if (r.returncode == 0 and out) else None

    if wt is None or run(gen(0, 16)) is None:
        print("  (modules valid; skip self-check: no gc-capable wasmtime)")
        return
    for seed in range(8):                                  # every rail must agree with the shadow on a conformant engine
        eq(f"seed {seed}: all rails agree (bitmask 0)", run(gen(seed, 16)), "0")
    eq("a corrupted graph is detected", run(gen(0, 16, bug=True)) != "0", True)


if __name__ == "__main__":
    tests = [f for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"\n{len(tests)} test(s) passed")
