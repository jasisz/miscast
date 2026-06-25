"""Unit tests for reify — rewriting a GC-reference result into an i32 fingerprint so the
value-differential can see a subtype/cast unsoundness that returns a wrong-typed object.

Pure structural tests always run. An assemble+discriminate test runs when `wasm-tools` is on PATH
(the one external tool the project already requires): it confirms the rewrite is valid wat and that
struct / array / i31 / null references fingerprint to distinct values. Run: `python3 tests/test_reify.py`
(or under pytest)."""
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from miscast.reify import reify_ref_result
from miscast.wast import reified_case

STRUCT = '(module (type $s (struct (field i32))) (func (export "f") (result (ref $s)) (struct.new $s (i32.const 7))))'
ARRAY = '(module (type $a (array (mut i32))) (func (export "f") (result (ref $a)) (array.new $a (i32.const 1) (i32.const 3))))'
I31 = '(module (func (export "f") (result (ref i31)) (ref.i31 (i32.const 42))))'
NULLREF = '(module (func (export "f") (result (ref null i31)) (ref.null i31)))'
INT = '(module (func (export "f") (result i32) (i32.const 5)))'
FUNCREF = '(module (func $g) (func (export "f") (result (ref func)) (ref.func $g)))'
VOID = '(module (func (export "f")))'
BYID = '(module (type $s (struct (field i32))) (export "f" (func $mk)) (func $mk (result (ref $s)) (struct.new $s (i32.const 1))))'


def eq(name, got, want):
    assert got == want, f"{name}: got {got!r}, want {want!r}"


def test_reifies_gc_ref():
    new, ok = reify_ref_result(STRUCT, "f")
    eq("struct ref is reified", ok, True)
    eq("result becomes i32", "(result i32)" in new, True)
    eq("ref result is gone", "(result (ref $s))" in new, False)
    eq("captures the ref", "local.set $__rf" in new, True)
    eq("probes abstract type", "ref.test (ref struct)" in new and "ref.is_null" in new, True)


def test_reifies_by_id_export():
    eq("export-by-id form is reified", reify_ref_result(BYID, "f")[1], True)


def test_skips_non_reifiable():
    eq("plain int is not reified", reify_ref_result(INT, "f")[1], False)
    eq("funcref is not any-rooted", reify_ref_result(FUNCREF, "f")[1], False)
    eq("void is not reified", reify_ref_result(VOID, "f")[1], False)
    eq("missing export is a no-op", reify_ref_result(INT, "nope")[1], False)


def test_reified_case_wrapper():
    m, rt, exp = reified_case(INT, "f", "OK 5")
    eq("int result keeps int rtype", rt, "int")
    eq("int module is unchanged", m == INT, True)
    eq("int expected is kept", exp, "OK 5")
    m, rt, exp = reified_case(STRUCT, "f", "RET")
    eq("ref result becomes int rtype", rt, "int")
    eq("ref expected is dropped", exp, None)
    eq("ref module is rewritten", "(result i32)" in m, True)
    m, rt, exp = reified_case(FUNCREF, "f", None)
    eq("funcref stays status-only", rt, None)


def _fingerprint(module):
    """Assemble the reified module (must be valid) and, if a gc-capable wasmtime is found, return its
    fingerprint; else return None after asserting validity."""
    new, ok = reify_ref_result(module, "f")
    assert ok, "expected a reifiable reference"
    wasm = "/tmp/_test_reify.wasm"
    p = subprocess.run(["wasm-tools", "parse", "/dev/stdin", "-o", wasm], input=new.encode(),
                       capture_output=True)
    assert p.returncode == 0, f"reified module is not valid wat: {p.stderr.decode()[:200]}"
    for wt in ("wasmtime", "/tmp/wasmtime-v46.0.0-aarch64-macos/wasmtime"):
        if shutil.which(wt) or os.path.exists(wt):
            r = subprocess.run([wt, "run", "-W", "gc=y", "--invoke", "f", wasm],
                               capture_output=True, text=True)
            out = [l for l in r.stdout.strip().splitlines() if l.strip()]
            if r.returncode == 0 and out and out[-1].lstrip("-").isdigit():
                return out[-1]
    return None


def test_fingerprint_valid_and_discriminates():
    if not shutil.which("wasm-tools"):
        print("  (skip: wasm-tools not on PATH)")
        return
    fps = {k: _fingerprint(m) for k, m in
           (("struct", STRUCT), ("array", ARRAY), ("i31", I31), ("null", NULLREF))}
    # validity is asserted inside _fingerprint for all four (always, with just wasm-tools)
    if any(v is None for v in fps.values()):
        print("  (reified modules valid; skip discrimination: no gc-capable wasmtime)")
        return
    eq("struct/array/i31/null fingerprints are all distinct", len(set(fps.values())), 4)
    eq("struct has the struct + eq bits", fps["struct"], "20")
    eq("array has the array + eq bits", fps["array"], "24")
    eq("i31 carries the payload", fps["i31"], str((42 << 8) | (1 << 4) | (1 << 1)))
    eq("null fingerprints as null", fps["null"], "1")


if __name__ == "__main__":
    tests = [f for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"\n{len(tests)} test(s) passed")
