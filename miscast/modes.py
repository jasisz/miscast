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
  trapline trap-boundary generator: each trappable op swept across its {edge-1, edge, edge+1} with a baked TRAP/constant
  memory64 high (>= 2^32) linear-memory address must TRAP, not wrap to its low 32 bits (sandbox-escape probe)
  memcross multi-memory / bulk-memory cross-index probes with data init, copy/fill, grow and OOB traps
  arrayops bulk GC array ops (copy / fill / new_data / new_elem) swept for boundary, overlap, OOB and element variance
  callref  typed function refs: call_ref / call_indirect / table bulk / br_on_cast[_fail] with baked call results or traps
  simdlane SIMD lane algebra: shuffle/swizzle, saturation, signedness, extmul/dot, memory lanes and bitmasks
  nanjet  f32/f64 NaN payload bit-preservation through runtime storage/control-flow paths
  flowmerge control-flow joins and stack-polymorphic dead code carrying GC refs through if/select/br/br_table/try_table
  refalias reference identity / alias preservation through bulk ops, extern, casts, EH and call_ref
  mutalias mutable alias write-visibility through storage, bulk ops, extern, casts, EH, call_ref and type views
  packedops runtime packed-GC storage: i8/i16 struct/array set/fill/copy with sign/zero extension and truncation
  evalorder side-effecting operand order for bulk ops, calls, stores, aggregate constructors, EH payloads
  heapstorm stateful GC heap programs: many aliasing storage/copy/call/EH/extern operations plus a shadow checksum
  castalgebra subtype / cast correctness: ref.test / ref.cast swept across the type lattice + structural-twin canonicalization + self-consistency
  constinit GC const-expr init (global / elem / data: struct.new / array.new / ref.i31, extended-const) evaluated to a baked value
  hammer   broad deterministic sweep: all + trapline + memory64 + memcross + arrayops + callref + simdlane + nanjet + flowmerge + refalias + mutalias + packedops + evalorder + heapstorm + invalid
  invalid  a battery of spec-invalid GC modules: does the SUT reject them? (validation differential)
"""
import os

from .config import WORK
from .toolchain import _run
from .morphism import gen as morphism_gen
from .recgroup import gen as recgroup_gen
from .compose import compose_gen
from .trapline import trapline_gen
from .memory64 import memory64_gen
from .memcross import memcross_gen
from .arrayops import arrayops_gen
from .callref import callref_gen
from .simdlane import simdlane_gen
from .nanjet import nanjet_gen
from .flowmerge import flowmerge_gen
from .refalias import refalias_gen
from .mutalias import mutalias_gen
from .packedops import packedops_gen
from .evalorder import evalorder_gen, _FAMILIES as EVALORDER_FAMILIES
from .heapstorm import heapstorm_gen
from .castalgebra import castalgebra_gen
from .constinit import constinit_gen
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
    for i in range(min(n, len(EVALORDER_FAMILIES))):
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



def gen_trapline(_cases, n):
    """Trap-boundary probes (see trapline.py): each trappable op swept across its boundary {edge-1, edge,
    edge+1} with a baked TRAP or constant oracle — the off-by-one bounds check on array / memory / table
    accesses, div/rem-by-zero and INT_MIN/-1, trunc vs trunc_sat of out-of-range / NaN, a null deref, a
    failing ref.cast. A SUT that runs past a boundary (or fails to saturate) diverges; no second engine."""
    out = []
    for i in range(n):
        label, export, expected, wat = trapline_gen(i)
        out.append((f"trapline{i}|{label}", wat, export, [], expected, "int"))
    return out, []


def gen_memory64(_cases, n):
    """memory64 address-truncation probes (see memory64.py): a high (>= 2^32) linear-memory address must TRAP,
    not wrap to its low 32 bits. Each plants a sentinel low and accesses a high address; an engine that returns
    the sentinel (running where every oracle traps) truncated the address — a memory-safety sandbox escape."""
    out = []
    for i in range(n):
        label, export, expected, wat = memory64_gen(i)
        out.append((f"memory64{i}|{label}", wat, export, [], expected, "int"))
    return out, []


def gen_memcross(_cases, n):
    """Multi-memory / bulk-memory cross-index probes (see memcross.py): several memories, passive data init,
    memory.copy/fill isolation, overlapping memmove, nonzero-index memory.grow, and selected-memory OOB traps."""
    out = []
    for i in range(n):
        label, export, expected, wat = memcross_gen(i)
        out.append((f"memcross{i}|{label}", wat, export, [], expected, "int"))
    return out, []


def gen_arrayops(_cases, n):
    """Bulk GC array ops (see arrayops.py): array.copy / array.fill / array.new_data swept for boundary,
    overlap (memmove), OOB (must trap), and element-type variance — including the VALID widening copy that
    Wizard#656 wrongly rejects. Self-checking: a copy/fill leaves an exact element, an OOB op traps."""
    out = []
    for i in range(n):
        label, export, expected, wat = arrayops_gen(i)
        out.append((f"arrayops{i}|{label}", wat, export, [], expected, "int"))
    return out, []


def gen_callref(_cases, n):
    """Typed function-reference / table-call probes (see callref.py): `call_ref`, `call_indirect`, table
    bulk ops, `br_on_cast[_fail]` over concrete function types, and mandated null / wrong-type call traps.
    Each case has a baked result or trap, so no second engine is needed."""
    out = []
    for i in range(n):
        label, export, expected, wat = callref_gen(i)
        out.append((f"callref{i}|{label}", wat, export, [], expected, "int"))
    return out, []


def gen_simdlane(_cases, n):
    """SIMD lane-algebra probes (see simdlane.py): byte shuffles, swizzles, saturating narrows,
    signed/unsigned extraction, extmul/dot arithmetic, lane load/store, q15 rounding, and bitmasks checked
    against a Python-computed checksum."""
    out = []
    for i in range(n):
        label, export, expected, wat = simdlane_gen(i)
        out.append((f"simdlane{i}|{label}", wat, export, [], expected, "int"))
    return out, []


def gen_nanjet(_cases, n):
    """NaN payload bit-preservation probes (see nanjet.py): f32/f64 NaNs are routed through runtime
    fields, arrays, globals, select, EH, call_ref, memory, sign ops, and br_table, then returned as i64 bits."""
    out = []
    for i in range(n):
        label, export, expected, wat = nanjet_gen(i)
        out.append((f"nanjet{i}|{label}", wat, export, [], expected, "int64"))
    return out, []


def gen_flowmerge(_cases, n):
    """Control-flow merge probes (see flowmerge.py): `if`, typed `select`, `br`, `br_table`, `try_table`, and
    stack-polymorphic dead code after `unreachable`, all carrying GC refs through merge points with a baked
    value or trap oracle."""
    out = []
    for i in range(n):
        label, export, expected, wat = flowmerge_gen(i)
        out.append((f"flowmerge{i}|{label}", wat, export, [], expected, "int"))
    return out, []


def gen_refalias(_cases, n):
    """Reference-identity probes (see refalias.py): `ref.eq` checks that a GC reference survives fields,
    arrays, bulk copies, tables, extern round-trips, casts, EH, and `call_ref` without being cloned or
    substituted; negative controls check equal-looking fresh objects are still distinct."""
    out = []
    for i in range(n):
        label, export, expected, wat = refalias_gen(i)
        out.append((f"refalias{i}|{label}", wat, export, [], expected, "int"))
    return out, []


def gen_mutalias(_cases, n):
    """Mutable-alias probes (see mutalias.py): mutate an object through one alias and read it through another
    after storage, bulk copies, extern/cast/EH/call_ref paths, structural-twin casts, or subtype views."""
    out = []
    for i in range(n):
        label, export, expected, wat = mutalias_gen(i)
        out.append((f"mutalias{i}|{label}", wat, export, [], expected, "int"))
    return out, []


def gen_packedops(_cases, n):
    """Runtime packed-GC storage probes (see packedops.py): mutable i8/i16 struct fields and arrays,
    write truncation, signed/unsigned reads, subtype field views, array.fill, and overlapping array.copy."""
    out = []
    for i in range(n):
        label, export, expected, wat = packedops_gen(i)
        out.append((f"packedops{i}|{label}", wat, export, [], expected, "int"))
    return out, []


def gen_evalorder(_cases, n):
    """Side-effecting operand-order probes (see evalorder.py): every operand position calls `next()`, then
    bulk memory/table/array ops, call_ref/call_indirect/return_call_ref, stores, aggregate constructors,
    br_table/select, and EH payload routing return a baked checksum that proves the exact evaluation/pop
    order."""
    out = []
    for i in range(min(n, len(EVALORDER_FAMILIES))):
        label, export, expected, wat = evalorder_gen(i)
        out.append((f"evalorder{i}|{label}", wat, export, [], expected, "int"))
    return out, []


def gen_heapstorm(_cases, n):
    """Stateful GC heap-storm probes (see heapstorm.py): interleave many aliasing storage, bulk-copy,
    table/global, br_on_cast, EH, extern, call_ref, and return_call_ref operations, then compare a weighted
    checksum against a Python shadow model."""
    out = []
    for i in range(n):
        label, export, expected, wat = heapstorm_gen(i)
        out.append((f"heapstorm{i}|{label}", wat, export, [], expected, "int"))
    return out, []


def gen_castalgebra(_cases, n):
    """Subtype / cast correctness probes (see castalgebra.py): ref.test / ref.cast swept across a fixed
    type lattice (own / super / strict-subtype / sibling / unrelated), structurally-identical twin types
    that must canonicalize to one (a false negative = a canonicalization gap), concrete func / array
    membership, abstract-heap membership, and internal-consistency self-checks (test <-> cast, up-then-down
    roundtrip). The oracle is the spec-fixed 0/1 / field / TRAP; no second engine."""
    out = []
    for i in range(n):
        label, export, expected, wat = castalgebra_gen(i)
        out.append((f"castalgebra{i}|{label}", wat, export, [], expected, "int"))
    return out, []


def gen_constinit(_cases, n):
    """GC constant-expression init probes (see constinit.py): a global / elem / data segment initialised
    with struct.new / array.new / ref.i31 — optionally extended-const arithmetic, nested values — then read
    back to its baked value. Catches an engine that rejects a valid const-init (Talos#109) or mis-evaluates
    / crashes on one (wasmz). Self-checking; no second engine."""
    out = []
    for i in range(n):
        label, export, expected, wat = constinit_gen(i)
        out.append((f"constinit{i}|{label}", wat, export, [], expected, "int"))
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
    for gen, count in ((gen_morphism, n), (gen_recgroup, min(n, 6)), (gen_compose, min(n, 30)),
                       (gen_castalgebra, min(n, 54)), (gen_constinit, min(n, 80))):
        w, u = gen(cases, count)
        out += w
        untested += u
    return out, untested


def gen_hammer(cases, n):
    """A broad deterministic engine sweep. `all` focuses on GC soundness/value-preservation; this adds the
    mature-runtime stress surfaces that are otherwise separate: trap boundaries, memory64 high-address
    aliasing, multi-memory cross-indexing, bulk GC array operations, typed function-reference calls, SIMD lane
    algebra, NaN payload preservation, control-flow merge typing, reference identity preservation, mutable-alias write visibility,
    packed-GC storage, side-effecting operand order, and stateful heap storms. The invalid validation battery
    is added by the CLI."""
    out, untested = gen_all(cases, n)
    for gen, count in ((gen_trapline, min(n, 48)), (gen_memory64, min(n, 36)), (gen_memcross, min(n, 48)),
                       (gen_arrayops, min(n, 44)), (gen_callref, min(n, 36)), (gen_simdlane, min(n, 64)),
                       (gen_nanjet, min(n, 72)),
                       (gen_flowmerge, min(n, 40)), (gen_refalias, min(n, 40)), (gen_mutalias, min(n, 40)),
                       (gen_packedops, min(n, 48)), (gen_evalorder, min(n, 48)),
                       (gen_heapstorm, min(n, 48))):
        w, u = gen(cases, count)
        out += w
        untested += u
    return out, untested


MODES = {"replay": gen_replay, "mutate": gen_mutate, "smith": gen_smith, "morphism": gen_morphism,
         "recgroup": gen_recgroup, "compose": gen_compose, "trapline": gen_trapline, "memory64": gen_memory64,
         "memcross": gen_memcross,
         "arrayops": gen_arrayops, "callref": gen_callref, "simdlane": gen_simdlane,
         "nanjet": gen_nanjet,
         "flowmerge": gen_flowmerge,
         "refalias": gen_refalias,
         "mutalias": gen_mutalias,
         "packedops": gen_packedops,
         "evalorder": gen_evalorder,
         "heapstorm": gen_heapstorm,
         "castalgebra": gen_castalgebra,
         "constinit": gen_constinit, "all": gen_all, "hammer": gen_hammer}
