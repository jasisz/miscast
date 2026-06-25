"""Generation modes — each turns the parsed corpus into the list of cases to run.

  replay   the cases as-is (the assert is a fallback oracle / corroboration)
  mutate   each module's type slot swept across the subtyping matrix (assert dropped)
  smith    random valid GC modules via `wasm-tools smith` (breadth baseline)
  morphism self-checking shadow-GC programs: SEVERAL real GC representations vs a linear-memory model, isolating which path diverges
  recgroup rec-group canonicalization trap-differential: reordered recursion groups make distinct types, so call_indirect must trap
  externcv extern.convert_any / any.convert_extern round-trip: a GC ref pushed out to externref and back must be preserved
  compose  compositional feature-interaction generator: a payload threaded through a MIX of value-preserving
           conduits (tag / call / global / br_on_cast / tail-call / field / table / extern), a routed exception
           nest, or an exnref carry — self-checking by construction; subsumes castbr / externconvert / eh / exnstack
  invalid  a battery of spec-invalid GC modules: does the SUT reject them? (validation differential)
"""
import os

from .config import WORK
from .toolchain import _run
from .morphism import gen as morphism_gen
from .recgroup import gen as recgroup_gen
from .compose import compose_gen
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


def gen_compose(_cases, n):
    """Compositional feature-interaction programs (see compose.py). Three self-checking shapes — a GC payload
    threaded through a random MIX of value-preserving conduits (tag / call / global / br_on_cast / tail-call /
    field / table / extern), read for its value or for `ref.is_null`; a routed exception nest; or an exnref
    captured / carried / re-raised — together SUBSUME the old `castbr` / `externconvert` / `eh` / `exnstack`
    modes (verified to reproduce every finding they did, on every engine) while also generating mixed
    interactions no single one could. The oracle is computable by construction; a SUT that mangles the
    reference, mis-routes a throw, or fails a mandated trap returns a wrong value."""
    out = []
    for i in range(n):
        label, export, expected, wat = compose_gen(i)
        out.append((f"compose{i}|{label}", wat, export, [], expected, "int"))
    return out, []


def gen_all(cases, n):
    """Run the whole self-checking GC-soundness oracle suite in one pass — `morphism` + `recgroup` +
    `compose` (which itself subsumes the old castbr / externconvert / eh / exnstack corners). None need a
    corpus and each program carries its own per-program oracle, so this throws every soundness probe we have
    at a SUT in a single command. Case names stay
    mode-prefixed so a finding says which oracle fired. The low-shape modes are capped (they have only a few
    distinct programs); `morphism` scales with `-n`. (The `invalid` validation battery runs alongside this
    under `--mode all` too; it is wired in the CLI because it routes to the validation differential, not the
    execution one.)"""
    out, untested = [], []
    for gen, count in ((gen_morphism, n), (gen_recgroup, min(n, 6)), (gen_compose, min(n, 30))):
        w, u = gen(cases, count)
        out += w
        untested += u
    return out, untested


MODES = {"replay": gen_replay, "mutate": gen_mutate, "smith": gen_smith, "morphism": gen_morphism,
         "recgroup": gen_recgroup, "compose": gen_compose, "all": gen_all}
