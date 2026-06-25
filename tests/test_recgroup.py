"""Unit tests for the rec-group canonicalization trap-differential.

Structural tests always run. An assemble + self-check test runs when `wasm-tools` is on PATH: every
generated program must be valid wat, and (when a gc-capable wasmtime is found) every program must TRAP on a
conformant engine — that is the per-program oracle, so a SUT that returns a value is unsound. Run:
`python3 tests/test_recgroup.py` (or under pytest)."""
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from miscast.recgroup import gen, _SHAPES
from miscast.modes import gen_recgroup


def eq(name, got, want):
    assert got == want, f"{name}: got {got!r}, want {want!r}"


def test_generates_reordered_rec_groups():
    m = gen(0, 77)
    eq("two rec groups", m.count("(rec"), 2)
    eq("mutually recursive types", "(struct (field (ref null $A1)))" in m, True)
    eq("call_indirect against the reordered type", "(call_indirect (type $A2)" in m, True)
    eq("the sentinel return value appears", "i32.const 77" in m, True)
    eq("shapes are distinct", len({gen(i, 1) for i in range(len(_SHAPES))}), len(_SHAPES))


def test_mode_cases():
    cases, untested = gen_recgroup(None, 3)
    eq("three cases", len(cases), 3)
    nm, mod, export, args, expected, rtype = cases[0]
    eq("export is go", export, "go")
    eq("TRAP is the per-program oracle", expected, "TRAP")
    eq("no untested", untested, [])


def _assemble(module):
    p = subprocess.run(["wasm-tools", "parse", "/dev/stdin", "-o", "/tmp/_rg_test.wasm"],
                       input=module.encode(), capture_output=True)
    assert p.returncode == 0, f"rec-group module is not valid wat: {p.stderr.decode()[:160]}"


def test_assembles_and_conformant_traps():
    if not shutil.which("wasm-tools"):
        print("  (skip: wasm-tools not on PATH)")
        return
    for s in range(len(_SHAPES)):                          # every generated program must validate
        _assemble(gen(s))
    wt = next((c for c in ("wasmtime", "/tmp/wasmtime-v46.0.0-aarch64-macos/wasmtime")
               if shutil.which(c) or os.path.exists(c)), None)

    def traps(module):
        _assemble(module)
        r = subprocess.run([wt, "run", "-W", "function-references=y,gc=y", "--invoke", "go", "/tmp/_rg_test.wasm"],
                           capture_output=True, text=True)
        low = (r.stdout + r.stderr).lower()
        return r.returncode != 0 and ("mismatch" in low or "trap" in low or "unreachable" in low)

    if wt is None:
        print("  (modules valid; skip self-check: no wasmtime)")
        return
    probe = subprocess.run([wt, "run", "-W", "function-references=y,gc=y", "--invoke", "go", "/tmp/_rg_test.wasm"],
                           capture_output=True, text=True)
    if "unknown" in (probe.stdout + probe.stderr).lower() and probe.returncode != 0 and "mismatch" not in (probe.stdout + probe.stderr).lower():
        print("  (modules valid; skip self-check: no gc-capable wasmtime)")
        return
    for s in range(len(_SHAPES)):                          # the conformant verdict is TRAP on every program
        eq(f"shape {s}: conformant engine traps", traps(gen(s)), True)


if __name__ == "__main__":
    tests = [f for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"\n{len(tests)} test(s) passed")
