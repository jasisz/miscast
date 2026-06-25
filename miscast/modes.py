"""Generation modes — each turns the parsed corpus into the list of cases to run.

  replay   the cases as-is (the assert is a fallback oracle / corroboration)
  mutate   each module's type slot swept across the subtyping matrix (assert dropped)
  smith    random valid GC modules via `wasm-tools smith` (breadth baseline)
  dualrail self-checking shadow-GC programs (real Wasm GC vs a hand-rolled linear-memory model)
"""
import os

from .config import WORK
from .toolchain import _run
from .dualrail import gen as dualrail_gen
from .mutate import mutate_module


def gen_replay(cases, _n):
    return cases, []


def gen_mutate(cases, _n):
    out, untested = [], []
    for name, mod, export, args, _expected, rtype in cases:
        variants = mutate_module(mod)
        if not variants:
            untested.append(name); continue
        for label, vmod in variants:
            out.append((f"{name}|{label}", vmod, export, args, None, rtype))   # assert dropped — differential only
    return out, untested


def gen_smith(_cases, n):
    spec_wat, spec_wasm = os.path.join(WORK, "_spec.wat"), os.path.join(WORK, "_spec.wasm")
    open(spec_wat, "w").write('(module (func (export "f") (result i32) i32.const 0))')
    _run(["wasm-tools", "parse", spec_wat, "-o", spec_wasm])
    out = []
    for i in range(n):
        seed = os.path.join(WORK, f"_s{i}.bin")
        open(seed, "wb").write(os.urandom(500 + (i * 37) % 1500))
        m = os.path.join(WORK, f"_smith{i}.wasm")
        r = _run(["wasm-tools", "smith", "--exports", spec_wasm,
                  "--gc-enabled", "true", "--reference-types-enabled", "true",
                  "--simd-enabled", "false", "--relaxed-simd-enabled", "false", "--allow-floats", "false",
                  "--bulk-memory-enabled", "true", "--ensure-termination",
                  "--max-imports", "0", "--min-funcs", "1", "--max-funcs", "8", seed, "-o", m])
        if r.returncode == 0:
            out.append((f"smith{i}", _run(["wasm-tools", "print", m]).stdout, "f", [], None, "int"))
    return out, []


def gen_dualrail(_cases, n):
    """Self-checking dual-rail shadow-GC programs (see dualrail.py): each runs one object graph through
    real Wasm GC and through a hand-rolled linear-memory shadow, and `check` returns real_hash - shadow_hash.
    The correct result is 0 on every conformant engine, so the assert `OK 0` is the per-program oracle and a
    SUT returning nonzero (a GC lowering / cast / identity bug) shows as a VALUE divergence — no second
    engine required. A reference engine returning 0 confirms the two worlds are genuinely equivalent."""
    out = []
    for i in range(n):
        K = [12, 16, 24, 32, 48][i % 5]
        out.append((f"dualrail{i}", dualrail_gen(i, K), "check", [], "OK 0", "int"))
    return out, []


MODES = {"replay": gen_replay, "mutate": gen_mutate, "smith": gen_smith, "dualrail": gen_dualrail}
