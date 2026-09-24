"""Unit tests for the backend-differential generators (simdgen / tailgen / exngen / memgen / intalg) and tools/backend_hunt.py."""
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from miscast import cmpfuse, exngen, intalg, loopgen, memgen, simdgen, tailgen

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WTDIFF = os.path.join(ROOT, "work", "wtdiff", "target", "release", "wtdiff")
GENS = {"simdgen": simdgen.gen_module, "simdgen-obs": simdgen.gen_module_obs, "tailgen": tailgen.gen_module,
        "exngen": exngen.gen_module, "memgen": memgen.gen_module, "intalg": intalg.gen_module,
        "loopgen": loopgen.gen_module, "cmpfuse": cmpfuse.gen_module}
TRAP_FREE = ("simdgen", "simdgen-obs", "tailgen", "exngen", "intalg", "loopgen", "cmpfuse")  # memgen traps on purpose (out-of-bounds probes)
N_SEEDS = 12
SIMD0_SHA = "6330f4d115151e18"  # simdgen.gen_module(0) as used by the 2026-09-23 campaigns
EXPORT = re.compile(r'\(func \(export "([^"]+)"\)([^\n]*)')


def eq(name, got, want):
    assert got == want, f"{name}: got {got!r}, want {want!r}"


def _validate(name, wat):
    p = subprocess.run(["wasm-tools", "validate", "/dev/stdin"], input=wat.encode(), capture_output=True)
    assert p.returncode == 0, f"{name}: invalid module: {p.stderr.decode()[:300]}"


def test_deterministic():
    for g, gen in GENS.items():
        eq(f"{g} seed 7 reproducible", gen(7), gen(7))
        eq(f"{g} seeds differ", gen(7) != gen(8), True)


def test_exports_take_no_params():
    """wtdiff only calls zero-param exports; a generator that emits one with params silently loses coverage."""
    for g, gen in GENS.items():
        for seed in range(N_SEEDS):
            exports = EXPORT.findall(gen(seed))
            assert exports, f"{g} seed {seed}: no exports"
            for name, sig in exports:
                assert "(param" not in sig, f"{g} seed {seed}: export {name} takes params"


def test_modules_validate():
    if not shutil.which("wasm-tools"):
        print("  (skip: wasm-tools not on PATH)")
        return
    for g, gen in GENS.items():
        for seed in range(N_SEEDS):
            _validate(f"{g} seed {seed}", gen(seed))


def test_exports_do_not_trap():
    """The generators are trap-free by construction (bounded loops, fuel-bounded tail chains, every escaping
    exception caught at the export), so a released wasmtime must run every export to completion."""
    if not (shutil.which("wasm-tools") and shutil.which("wasmtime")):
        print("  (skip: wasm-tools / wasmtime not on PATH)")
        return
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "m.wasm")
        for g in TRAP_FREE:
            gen = GENS[g]
            for seed in range(3):
                wat = gen(seed)
                subprocess.run(["wasm-tools", "parse", "/dev/stdin", "-o", path], input=wat.encode(), check=True)
                for name, _sig in EXPORT.findall(wat):
                    r = subprocess.run(["wasmtime", "run", "-W", "tail-call=y,exceptions=y,gc=y,function-references=y", "--invoke", name, path],
                                       capture_output=True, text=True)
                    assert r.returncode == 0, f"{g} seed {seed} export {name}: {r.stderr.strip()[-200:]}"


def test_memgen_traps_are_bounds_only():
    """memgen's only trap is an out-of-bounds access, and most exports still run to completion."""
    if not (shutil.which("wasm-tools") and shutil.which("wasmtime")):
        print("  (skip: wasm-tools / wasmtime not on PATH)")
        return
    ok = total = 0
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "m.wasm")
        for seed in range(4):
            wat = memgen.gen_module(seed)
            subprocess.run(["wasm-tools", "parse", "/dev/stdin", "-o", path], input=wat.encode(), check=True)
            for name, _sig in EXPORT.findall(wat):
                r = subprocess.run(["wasmtime", "run", "-W", "multi-memory=y,memory64=y", "--invoke", name, path],
                                   capture_output=True, text=True)
                total += 1
                if r.returncode == 0:
                    ok += 1
                else:
                    assert "out of bounds memory access" in r.stderr, f"memgen seed {seed} {name}: {r.stderr[-200:]}"
    assert ok * 2 >= total, f"only {ok}/{total} memgen exports complete: the edge probes trap too often"


def test_simdgen_obs_keeps_default_stream():
    """gen_module_obs must not perturb gen_module: old campaign seeds keep reproducing the same modules."""
    import hashlib
    eq("simdgen seed 0 unchanged", hashlib.sha256(simdgen.gen_module(0).encode()).hexdigest()[:16], SIMD0_SHA)


def test_memgen_portable_profile():
    """The portable profile uses one 32-bit memory and no v128, and leaves the default stream untouched."""
    for seed in range(20):
        wat = memgen.gen_module_portable(seed)
        eq(f"seed {seed} one memory", wat.count("(memory $"), 1)
        assert "v128" not in wat and "(memory $m0 i64" not in wat, f"seed {seed} not portable"
    eq("default profile unaffected", memgen.gen_module(3), memgen.gen_module(3, simd=True, multi=True, mem64=True))


def test_model_hunt_parse():
    """Only a whole-line result counts; Wizard's trap trace offset (`<wasm func #4> +511`) must not."""
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import model_hunt
    eq("wasmedge", model_hunt.parse("-961691672050465424\n"), -961691672050465424)
    eq("wamr", model_hunt.parse("0xf2a76284fffb0570:i64"), -961691672050465424)
    eq("wizard", model_hunt.parse("17485052401659086192uL"), -961691672050465424)
    eq("trap trace", model_hunt.parse("<wasm func #4> +511\n  !trap[MEMORY_OOB]"), None)


def test_model_hunt_selfcheck_helpers():
    """Self-checking generators' baked expectations become model values; i32 results compare as bit patterns."""
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import model_hunt
    eq("baked int", model_hunt._baked("OK -3"), -3)
    eq("baked trap", model_hunt._baked("TRAP"), "TRAP")
    eq("baked unusable", model_hunt._baked("OK"), None)
    eq("wamr i32", model_hunt.parse("0x14:i32"), 20)
    eq("i32 printed unsigned", model_hunt._same(0xFFFFFFFF, -1), True)
    eq("different values", model_hunt._same(5, 6), False)
    eq("i64 not wrapped", model_hunt._same(1 << 40, 0), False)


def test_wtref_isolated():
    """Each export runs in a fresh instance: a grow in one export is not seen by the next."""
    if not os.path.exists(WTDIFF):
        print("  (skip: work/wtdiff not built)")
        return
    from miscast import wtref
    wat = ('(module (memory 1 4)\n'
           '  (func (export "a") (result i64) (i64.extend_i32_s (memory.grow (i32.const 1))))\n'
           '  (func (export "b") (result i64) (i64.extend_i32_u (memory.size))))')
    eq("shared instance", wtref.expected(wat), {"a": 1, "b": 2})
    eq("fresh instance per export", wtref.expected_isolated(wat), {"a": 1, "b": 1})


def test_backend_hunt_driver():
    """tools/backend_hunt.py drives wtdiff end to end (only when the local wtdiff build exists)."""
    if not os.path.exists(WTDIFF):
        print("  (skip: work/wtdiff not built)")
        return
    with tempfile.TemporaryDirectory() as tmp:
        p = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "backend_hunt.py"),
                            "miscast.exngen:gen_module", "0", "2", "--cfgs", "cl2,cl0", "-j", "2", "--out", tmp],
                           capture_output=True, text=True, cwd=ROOT)
        eq("driver exit code", p.returncode, 0)
        eq("driver summary", p.stdout.strip().splitlines()[-1].split(",")[0], "done 2")


if __name__ == "__main__":
    tests = [f for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"\n{len(tests)} test(s) passed")
