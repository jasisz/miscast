"""Unit tests for the typed function-reference / table-call oracle."""
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from miscast.callref import callref_gen, _FAMILIES
from miscast.modes import gen_callref


def eq(name, got, want):
    assert got == want, f"{name}: got {got!r}, want {want!r}"


def _assemble(module):
    p = subprocess.run(["wasm-tools", "parse", "/dev/stdin", "-o", "/tmp/_cr_test.wasm"],
                       input=module.encode(), capture_output=True)
    assert p.returncode == 0, f"callref module is not valid wat: {p.stderr.decode()[:200]}"


def _run_wasmtime(export):
    r = subprocess.run(["wasmtime", "run", "-W", "function-references=y,gc=y,tail-call=y",
                        "--invoke", export, "/tmp/_cr_test.wasm"],
                       capture_output=True, text=True)
    both = (r.stdout + r.stderr).lower()
    if r.returncode != 0 or "trap" in both or "unreachable" in both or "indirect call type mismatch" in both:
        return "TRAP"
    out = [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]
    return "OK " + out[-1] if out else "OK _"


def test_mode_cases():
    cases, untested = gen_callref(None, len(_FAMILIES))
    eq("one case per family", len(cases), len(_FAMILIES))
    eq("no untested cases", untested, [])
    nm, _mod, export, args, expected, rtype = cases[0]
    eq("case is prefixed", nm.startswith("callref0|callref-"), True)
    eq("export is f", export, "f")
    eq("no invoke args", args, [])
    eq("expected is baked", expected.startswith("OK ") or expected == "TRAP", True)
    eq("result type is int", rtype, "int")


def test_assembles_and_self_checks():
    if not shutil.which("wasm-tools"):
        print("  (skip: wasm-tools not on PATH)")
        return
    has_wasmtime = shutil.which("wasmtime") is not None
    for seed in range(len(_FAMILIES)):
        label, export, expected, wat = callref_gen(seed)
        _assemble(wat)
        if has_wasmtime:
            eq(f"{label}: wasmtime follows baked oracle", _run_wasmtime(export), expected)


if __name__ == "__main__":
    tests = [f for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"\n{len(tests)} test(s) passed")
