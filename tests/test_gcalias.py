"""Unit tests for the GC alias-region generator and its Python model."""
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from miscast.gcalias import gcalias_gen, _Model
from miscast.modes import MODES, gen_gcalias

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
N_SEEDS = 40


def eq(name, got, want):
    assert got == want, f"{name}: got {got!r}, want {want!r}"


def _assemble(module, path):
    p = subprocess.run(["wasm-tools", "parse", "/dev/stdin", "-o", path], input=module.encode(), capture_output=True)
    assert p.returncode == 0, f"gcalias module is not valid wat: {p.stderr.decode()[:200]}"


def _run_wasmtime(path, export):
    r = subprocess.run(["wasmtime", "run", "-W", "function-references=y,gc=y", "--invoke", export, path],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return "TRAP " + r.stderr.strip()[-200:]
    out = [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]
    return "OK " + out[-1] if out else "OK _"


def test_mode_cases():
    eq("registered as a mode", MODES["gcalias"], gen_gcalias)
    cases, untested = gen_gcalias(None, 5)
    eq("five cases", len(cases), 5)
    eq("no untested cases", untested, [])
    nm, _mod, export, args, expected, rtype = cases[0]
    eq("case is prefixed", nm.startswith("gcalias0|gcalias-"), True)
    eq("export is f", export, "f")
    eq("no invoke args", args, [])
    eq("expected is baked", expected.startswith("OK "), True)
    eq("result type is int", rtype, "int")


def test_deterministic():
    for seed in range(5):
        eq(f"seed {seed} reproducible", gcalias_gen(seed), gcalias_gen(seed))
    eq("seeds differ", gcalias_gen(0)[3] != gcalias_gen(1)[3], True)


def test_model_packed_reads():
    m = _Model()
    eq("i8 get_s", m.read("i8", 0x80, "s"), (1 << 64) - 128)
    eq("i8 get_u", m.read("i8", 0x80, "u"), 0x80)
    eq("i16 get_s", m.read("i16", 0xFFFF, "s"), (1 << 64) - 1)
    eq("i32 sign-extends", m.read("i32", 0x80000000, "u"), (1 << 64) - (1 << 31))
    m.acc = 0x1_0000_00FF
    eq("i8 store truncates", m.setval("i8", 0x100), 0xFF)
    eq("i64 store xors sign-extended const", m.setval("i64", 0xFFFFFFFF), 0x1_0000_00FF ^ ((1 << 64) - 1))


def test_model_matches_wasmtime():
    """The Python model and a released wasmtime (v46, before GC alias regions) must agree on every seed."""
    if not shutil.which("wasm-tools"):
        print("  (skip: wasm-tools not on PATH)")
        return
    has_wasmtime = shutil.which("wasmtime") is not None
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "m.wasm")
        for seed in range(N_SEEDS):
            label, export, expected, wat = gcalias_gen(seed)
            _assemble(wat, path)
            if has_wasmtime:
                eq(f"seed {seed} {label}: wasmtime follows the model", _run_wasmtime(path, export), expected)


def test_selfcheck_hunt_driver():
    """tools/selfcheck_hunt.py runs end to end on the default wasmtime with no --wt."""
    if not (shutil.which("wasm-tools") and shutil.which("wasmtime")):
        print("  (skip: wasm-tools / wasmtime not on PATH)")
        return
    with tempfile.TemporaryDirectory() as tmp:
        p = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "selfcheck_hunt.py"),
                            "miscast.gcalias:gcalias_gen", "0", "3", "--cfg", "-C collector=drc", "-j", "3",
                            "--out", tmp], capture_output=True, text=True, cwd=ROOT)
        eq("driver exit code", p.returncode, 0)
        eq("driver summary", p.stdout.strip().splitlines()[-1], "done 3 cases, 0 mismatching")


if __name__ == "__main__":
    tests = [f for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"\n{len(tests)} test(s) passed")
