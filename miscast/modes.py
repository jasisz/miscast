"""Generation modes — each turns the parsed corpus into the list of cases to run.

  replay   the cases as-is (the assert is a fallback oracle / corroboration)
  mutate   each module's type slot swept across the subtyping matrix (assert dropped)
  smith    random valid GC modules via `wasm-tools smith` (breadth baseline)
  morphism self-checking shadow-GC programs: SEVERAL real GC representations vs a linear-memory model, isolating which path diverges
  recgroup rec-group canonicalization trap-differential: reordered recursion groups make distinct types, so call_indirect must trap
  externcv extern.convert_any / any.convert_extern round-trip: a GC ref pushed out to externref and back must be preserved
"""
import os

from .config import WORK
from .toolchain import _run
from .morphism import gen as morphism_gen
from .recgroup import gen as recgroup_gen
from .externconvert import gen as externconvert_gen
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


def gen_morphism(_cases, n):
    """Representation-morphism programs (see morphism.py): one object graph realized as THREE real GC rails
    (cast / cast-free tag-dispatch / structurally-identical shard) plus a linear-memory shadow, all folding
    the same checksum. `check` returns a bitmask — 0 when every rail agrees, otherwise the bits ISOLATE the
    failing path (bit0 cast vs shadow = funcref/own-type ref.test, bit1 tag vs shadow = GC storage/identity,
    bit2 shard vs cast = type canonicalization). It needs no second engine — the linear-memory shadow cannot
    be wrong about GC, so a conformant engine returns 0 on every program — but a nonzero result also says
    WHICH representation is at fault."""
    out = []
    for i in range(n):
        K = [12, 16, 24, 32, 48][i % 5]
        out.append((f"morphism{i}", morphism_gen(i, K), "check", [], "OK 0", "int"))
    return out, []


def gen_recgroup(_cases, n):
    """Rec-group canonicalization trap-differential (see recgroup.py): two recursion groups holding the same
    mutually-recursive types in different ORDER are DISTINCT types under iso-recursive canonicalization, so a
    `call_indirect` against one type on a function of the other MUST trap. The per-program oracle is `TRAP`; a
    SUT that returns a value executed an ill-typed indirect call (it canonicalizes equi-recursively). No
    second engine required — the spec fixes the verdict."""
    return [(f"recgroup{i}", recgroup_gen(i), "go", [], "TRAP", "int") for i in range(n)], []


def gen_externconvert(_cases, n):
    """extern.convert_any / any.convert_extern round-trip (see externconvert.py): the spec requires a GC ref
    pushed out to `externref` and back to be preserved, so the self-check returns the original value. The
    per-program oracle is `OK <value>`; a SUT that traps or returns something else diverges (e.g. an engine
    that has not implemented the conversion opcodes)."""
    out = []
    for i in range(n):
        wat, val = externconvert_gen(i)
        out.append((f"externconvert{i}", wat, "rt", [], f"OK {val}", "int"))
    return out, []


MODES = {"replay": gen_replay, "mutate": gen_mutate, "smith": gen_smith, "morphism": gen_morphism,
         "recgroup": gen_recgroup, "externconvert": gen_externconvert}
