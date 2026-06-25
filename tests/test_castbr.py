"""Unit tests for the br_on_cast value-forwarding differential.

Structural tests always run. An assemble + self-check test runs when `wasm-tools` is on PATH: every
generated program must be valid wat, and (when a gc-capable wasmtime is found) each must return its expected
value on a conformant engine — the cast operand is forwarded intact. Run: `python3 tests/test_castbr.py`."""
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from miscast.castbr import gen, _SHAPES
from miscast.modes import gen_castbr


def eq(name, got, want):
    assert got == want, f"{name}: got {got!r}, want {want!r}"


def test_generates_br_on_cast():
    m, val = gen(0)
    eq("uses br_on_cast", "br_on_cast" in m, True)
    eq("reads the forwarded operand back", "ref.is_null" in m or "ref.test" in m or "struct.get" in m, True)
    eq("a value-reading shape exists", any("ref.is_null" in gen(i)[0] for i in range(len(_SHAPES))), True)
    eq("a br_on_cast_fail shape exists", any("br_on_cast_fail" in gen(i)[0] for i in range(len(_SHAPES))), True)


def test_mode_cases():
    cases, untested = gen_castbr(None, 4)
    eq("four cases", len(cases), 4)
    nm, mod, export, args, expected, rtype = cases[0]
    eq("export is f", export, "f")
    eq("per-program oracle is OK <value>", expected.startswith("OK "), True)
    eq("no untested", untested, [])


def _assemble(module):
    p = subprocess.run(["wasm-tools", "parse", "/dev/stdin", "-o", "/tmp/_cb_test.wasm"],
                       input=module.encode(), capture_output=True)
    assert p.returncode == 0, f"castbr module is not valid wat: {p.stderr.decode()[:160]}"


def test_assembles_and_conformant_forwards():
    if not shutil.which("wasm-tools"):
        print("  (skip: wasm-tools not on PATH)")
        return
    for s in range(len(_SHAPES)):                          # every generated program must validate
        _assemble(gen(s)[0])
    wt = next((c for c in ("wasmtime", "/tmp/wasmtime-v46.0.0-aarch64-macos/wasmtime")
               if shutil.which(c) or os.path.exists(c)), None)

    def run(module):
        _assemble(module)
        r = subprocess.run([wt, "run", "-W", "function-references=y,gc=y", "--invoke", "f", "/tmp/_cb_test.wasm"],
                           capture_output=True, text=True)
        out = [l for l in r.stdout.strip().splitlines() if l.strip()]
        return out[-1] if (r.returncode == 0 and out) else None

    if wt is None or run(gen(0)[0]) is None:
        print("  (modules valid; skip self-check: no gc-capable wasmtime)")
        return
    for s in range(len(_SHAPES)):                          # the forwarded operand reads back to its value
        wat, val = gen(s)
        eq(f"shape {s}: conformant forwards the cast operand", run(wat), str(val))


if __name__ == "__main__":
    tests = [f for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"\n{len(tests)} test(s) passed")
