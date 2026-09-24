"""Unit tests for the intalg reference interpreter (miscast/watmodel.py)."""
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from miscast import intalg
from miscast.watmodel import Unsupported, _selftest, expected


def eq(name, got, want):
    assert got == want, f"{name}: got {got!r}, want {want!r}"


def _one(expr, t="i64"):
    wrap = expr if t == "i64" else f"(i64.extend_i32_u {expr})"
    return expected(f'(module (memory 1) (func (export "f") (result i64) {wrap}))')["f"]


def test_selftest():
    _selftest()


def test_semantics():
    eq("i64 div_s truncates toward zero", _one("(i64.div_s (i64.const -9) (i64.const 4))"), -2)
    eq("i64 rem_s takes the dividend's sign", _one("(i64.rem_s (i64.const -9) (i64.const 4))"), -1)
    eq("i32 rem_u", _one("(i32.rem_u (i32.const -1) (i32.const 10))", "i32"), 0xFFFFFFFF % 10)
    eq("i32 shr_u masks the count", _one("(i32.shr_u (i32.const -1) (i32.const 36))", "i32"), 0x0FFFFFFF)
    eq("i64 rotl", _one("(i64.rotl (i64.const 0x8000000000000001) (i64.const 1))"), 3)
    eq("i32 popcnt", _one("(i32.popcnt (i32.const -1))", "i32"), 32)
    eq("i64 extend32_s", _one("(i64.extend32_s (i64.const 0x80000000))"), -(1 << 31))
    eq("wrap then extend_s", _one("(i64.extend_i32_s (i32.wrap_i64 (i64.const 0x1FFFFFFFF)))"), -1)
    eq("signed compare", _one("(i32.lt_s (i32.const -1) (i32.const 0))", "i32"), 1)
    eq("unsigned compare", _one("(i32.lt_u (i32.const -1) (i32.const 0))", "i32"), 0)


def test_data_and_locals():
    wat = ('(module (memory 1) (data (i32.const 8) "\\ff\\80\\01\\00")\n'
           '  (func (export "f") (result i64) (local $x i32)\n'
           '    (local.set $x (i32.load16_s (i32.const 8)))\n'
           '    (i64.extend_i32_s (i32.add (local.get $x) (i32.load8_u (i32.const 10))))))')
    eq("load16_s + load8_u via a local", expected(wat)["f"], -0x7F01 + 1)  # 0x80ff as i16 is -32513


def test_control_flow():
    wat = ('(module (memory 1) (func (export "f") (result i64) (local $x i32)\n'
           ' (local.set $x (i32.const 3))\n'
           ' (i64.add (block $L (result i64) (drop (br_if $L (i64.const 10) (i32.gt_s (local.get $x) (i32.const 2))))'
           ' (i64.const 20))\n'
           '  (if (result i64) (i32.eqz (local.get $x)) (then (i64.const 100)) (else (i64.const 200))))))')
    eq("br_if taken with a value + if/else", expected(wat)["f"], 210)
    skip = ('(module (memory 1) (func (export "f") (result i64) (local $h i64)\n'
            ' (block $S (br_if $S (i32.const 1)) (local.set $h (i64.const 9))) (local.get $h)))')
    eq("br_if without a value skips the rest of the block", expected(skip)["f"], 0)


def test_cmpfuse_matches_released_wasmtime():
    """The model also covers cmpfuse's control flow: it agrees with the wasmtime CLI on PATH."""
    if not (shutil.which("wasm-tools") and shutil.which("wasmtime")):
        print("  (skip: wasm-tools / wasmtime not on PATH)")
        return
    from miscast import cmpfuse
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "m.wasm")
        for seed in range(5):
            wat = cmpfuse.gen_module(seed)
            subprocess.run(["wasm-tools", "parse", "/dev/stdin", "-o", path], input=wat.encode(), check=True)
            for export, want in expected(wat).items():
                r = subprocess.run(["wasmtime", "run", "--invoke", export, path], capture_output=True, text=True)
                eq(f"seed {seed} {export}", r.stdout.strip().splitlines()[-1], str(want))


def test_unsupported_is_loud():
    try:
        _one("(f64.sqrt (f64.const 2))")
    except Unsupported:
        return
    raise AssertionError("an unsupported op must raise, not produce an expectation")


def test_matches_released_wasmtime():
    """Independent of wtdiff and of wasmtime main: the model agrees with the wasmtime CLI on PATH (v46)."""
    if not (shutil.which("wasm-tools") and shutil.which("wasmtime")):
        print("  (skip: wasm-tools / wasmtime not on PATH)")
        return
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "m.wasm")
        for seed in range(8):
            wat = intalg.gen_module(seed)
            subprocess.run(["wasm-tools", "parse", "/dev/stdin", "-o", path], input=wat.encode(), check=True)
            for export, want in expected(wat).items():
                r = subprocess.run(["wasmtime", "run", "--invoke", export, path], capture_output=True, text=True)
                eq(f"seed {seed} {export}", r.stdout.strip().splitlines()[-1], str(want))


if __name__ == "__main__":
    tests = [f for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"\n{len(tests)} test(s) passed")
