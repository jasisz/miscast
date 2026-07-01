"""Unit tests for the multi-memory cross-index oracle."""
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from miscast.memcross import memcross_gen, _FAMILIES
from miscast.modes import gen_memcross


def eq(name, got, want):
    assert got == want, f"{name}: got {got!r}, want {want!r}"


def _assemble(module):
    p = subprocess.run(["wasm-tools", "parse", "/dev/stdin", "-o", "/tmp/_mc_test.wasm"],
                       input=module.encode(), capture_output=True)
    assert p.returncode == 0, f"memcross module is not valid wat: {p.stderr.decode()[:200]}"


def _run_wasmtime(export):
    r = subprocess.run(["wasmtime", "run", "--invoke", export, "/tmp/_mc_test.wasm"],
                       capture_output=True, text=True)
    both = (r.stdout + r.stderr).lower()
    if r.returncode != 0 or any(k in both for k in ("trap", "out of bounds", "runtimeerror")):
        return "TRAP"
    out = [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]
    return "OK " + out[-1] if out else "OK _"


def test_mode_cases():
    cases, untested = gen_memcross(None, len(_FAMILIES))
    eq("one case per family", len(cases), len(_FAMILIES))
    eq("no untested cases", untested, [])
    nm, _mod, export, args, expected, rtype = cases[0]
    eq("case is prefixed", nm.startswith("memcross0|memcross-"), True)
    eq("export is f", export, "f")
    eq("no invoke args", args, [])
    eq("expected is baked", expected.startswith("OK ") or expected == "TRAP", True)
    eq("result type is int", rtype, "int")


def test_assembles_and_self_checks():
    if not shutil.which("wasm-tools"):
        print("  (skip: wasm-tools not on PATH)")
        return
    has_wasmtime = shutil.which("wasmtime") is not None
    for seed in range(3 * len(_FAMILIES)):
        label, export, expected, wat = memcross_gen(seed)
        _assemble(wat)
        if has_wasmtime:
            eq(f"{label}: wasmtime follows baked oracle", _run_wasmtime(export), expected)


if __name__ == "__main__":
    tests = [f for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"\n{len(tests)} test(s) passed")
