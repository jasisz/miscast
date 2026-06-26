"""Subtype / cast correctness generator: does the engine answer ref.test / ref.cast EXACTLY per the
GC type lattice — and is it internally consistent?

A different oracle law from `compose` (value-preservation) and `trapline` (trap-boundary): here the
per-program oracle is the spec-mandated ref.test 0/1, a cast-result field, a mandated cast TRAP, or —
for the self-consistency probes — a baked 0 (no law violated). The prize is a cast answer that
disagrees with the structural type lattice. A false NEGATIVE (ref.test 0 where 1 is due — an engine
that fails iso-recursive canonicalization for separately-declared structurally-identical types, or
for a concrete func type) is a correctness bug; a false POSITIVE (ref.test 1 / ref.cast success where
the value is NOT that type) would be a memory-safety hole. No second engine required — the lattice
fixes every verdict.

`compose` carries a value through value-preserving conduits; here the value stands still and the
ENGINE'S ANSWER ABOUT ITS TYPE is the thing under test — a distinct law. The cross-hierarchy
ILL-TYPED cases (ref.test/cast across disjoint hierarchies) live in the `invalid` validation battery,
not here: their verdict is REJECT (a validator question), and this generator is execution-only.
"""

# A fixed 5-type lattice shared by the lattice / cast families:
#   $base      <: (top)    struct{i32}
#   $sub       <: $base    struct{i32,i32}
#   $sub2      <: $sub     struct{i32,i32,i32}
#   $sibling   <: $base    struct{i32,i64}   (a sibling of $sub: same parent, distinct shape)
#   $unrelated <: (top)    struct{i64}
_LAT = ('  (type $base (sub (struct (field i32))))\n'
        '  (type $sub (sub $base (struct (field i32) (field i32))))\n'
        '  (type $sub2 (sub $sub (struct (field i32) (field i32) (field i32))))\n'
        '  (type $sibling (sub $base (struct (field i32) (field i64))))\n'
        '  (type $unrelated (sub (struct (field i64))))')
_SUBVAL = "(struct.new $sub (i32.const 10) (i32.const 20))"   # a value whose runtime type is exactly $sub


def _lattice(v):
    """A value of exact type $sub, ref.test against five lattice positions — every answer fixed by the
    subtype relation. own/super -> 1, strict-subtype/sibling/unrelated -> 0."""
    tgt, bit, name = [("$sub", 1, "own"), ("$base", 1, "super"), ("$sub2", 0, "strict-subtype"),
                      ("$sibling", 0, "sibling"), ("$unrelated", 0, "unrelated")][v % 5]
    wat = (f'(module\n{_LAT}\n'
           '  (func (export "f") (result i32)\n'
           f'    (ref.test (ref {tgt}) {_SUBVAL})))')
    return f"lattice[{name}]", f"OK {bit}", wat


def _cast(v):
    """ref.cast across the lattice: a cast that must succeed reads a field; one that must fail traps.
    Down (super value -> sub) and sideways (to a sibling) must trap; own / up must succeed."""
    kind = v % 4
    if kind == 0:        # own: cast $sub value to $sub, read its second field
        body, exp, name = f"(struct.get $sub 1 (ref.cast (ref $sub) {_SUBVAL}))", "OK 20", "own"
    elif kind == 1:      # up: cast $sub value to $base, read the shared first field
        body, exp, name = f"(struct.get $base 0 (ref.cast (ref $base) {_SUBVAL}))", "OK 10", "up"
    elif kind == 2:      # down-fail: a $base value is not a $sub -> trap
        body, exp, name = ("(struct.get $sub 0 (ref.cast (ref $sub) (struct.new $base (i32.const 1))))",
                           "TRAP", "down-fail")
    else:                # sibling-fail: a $sub value is not a $sibling -> trap
        body, exp, name = (f"(struct.get $sibling 0 (ref.cast (ref $sibling) {_SUBVAL}))", "TRAP", "sibling-fail")
    wat = f'(module\n{_LAT}\n  (func (export "f") (result i32)\n    {body}))'
    return f"cast[{name}]", exp, wat


def _structural(v):
    """Separately-declared, structurally-identical types must canonicalize to the SAME type (iso-recursive
    structural identity), so ref.test of one against a value of the other is 1 — regardless of finality or
    where the value lives. Finality governs sub-typing, NOT identity. (talos / wasmz answer 0 here.)"""
    kind = v % 3
    if kind == 0:        # two non-final twins
        wat = ('(module\n'
               '  (type $a (sub (struct (field i32))))\n'
               '  (type $b (sub (struct (field i32))))\n'
               '  (func (export "f") (result i32)\n'
               '    (ref.test (ref $a) (struct.new $b (i32.const 7)))))')
        name = "nonfinal-twin"
    elif kind == 1:      # two final twins (finality is not nominal identity)
        wat = ('(module\n'
               '  (type $a (struct (field i32)))\n'
               '  (type $b (struct (field i32)))\n'
               '  (func (export "f") (result i32)\n'
               '    (ref.test (ref $a) (struct.new $b (i32.const 7)))))')
        name = "final-twin"
    else:                # the twin value reached via a const-initialised global
        wat = ('(module\n'
               '  (type $a (struct (field i32)))\n'
               '  (type $b (struct (field i32)))\n'
               '  (global $g (ref $b) (struct.new $b (i32.const 3)))\n'
               '  (func (export "f") (result i32)\n'
               '    (ref.test (ref $a) (global.get $g))))')
        name = "twin-via-global"
    return f"structural[{name}]", "OK 1", wat


def _funcref(v):
    """Concrete func / array type membership. A funcref of exactly type $ft tests 1 against (ref $ft);
    a funcref of a structurally-DIFFERENT signature tests 0; an array of exactly $arr tests 1.
    (wasmz answers 0 on the concrete-func-type case.)"""
    kind = v % 3
    if kind == 0:        # ref.func of exactly $ft -> tests true against (ref $ft)
        wat = ('(module\n'
               '  (type $ft (sub (func (result i32))))\n'
               '  (func $g (type $ft) (result i32) (i32.const 42))\n'
               '  (elem declare func $g)\n'
               '  (func (export "f") (result i32)\n'
               '    (ref.test (ref $ft) (ref.func $g))))')
        return "funcref[own]", "OK 1", wat
    if kind == 1:        # a funcref of a different signature is NOT the other concrete func type
        wat = ('(module\n'
               '  (type $a (sub (func (result i32))))\n'
               '  (type $b (sub (func (param i32) (result i32))))\n'
               '  (func $g (type $b) (param i32) (result i32) (local.get 0))\n'
               '  (elem declare func $g)\n'
               '  (func (export "f") (result i32)\n'
               '    (ref.test (ref $a) (ref.func $g))))')
        return "funcref[distinct]", "OK 0", wat
    wat = ('(module\n'                                  # concrete array type membership
           '  (type $arr (sub (array i32)))\n'
           '  (func (export "f") (result i32)\n'
           '    (ref.test (ref $arr) (array.new_fixed $arr 2 (i32.const 1) (i32.const 2)))))')
    return "funcref[array-own]", "OK 1", wat


def _abstract(v):
    """ref.test against the abstract heap types within the `any` hierarchy — i31 / eq / struct / array
    membership, all well-typed (same hierarchy), each answer fixed by the kind of the value."""
    s = '  (type $s (sub (struct (field i32))))'
    sval = "(struct.new $s (i32.const 1))"
    i31 = "(ref.i31 (i32.const 5))"
    cases = [
        ("eq-on-i31", "(ref eq)", i31, 1),
        ("i31-on-i31", "(ref i31)", i31, 1),
        ("struct-on-struct", "(ref struct)", sval, 1),
        ("array-on-struct", "(ref array)", sval, 0),
        ("struct-on-i31", "(ref struct)", i31, 0),
    ]
    name, tgt, val, bit = cases[v % len(cases)]
    decl = f'{s}\n' if "$s" in val else ''
    wat = (f'(module\n{decl}'
           '  (func (export "f") (result i32)\n'
           f'    (ref.test {tgt} {val})))')
    return f"abstract[{name}]", f"OK {bit}", wat


def _consistency(v):
    """Internal-consistency self-checks, folded into one i32 (0 = every law held). The engine must agree
    with itself across ref.test / ref.cast / br_on_cast — a baked-0 oracle that needs no external answer,
    only that the engine not contradict its own cast machinery."""
    if v % 2 == 0:
        # LAW: up-then-down cast roundtrip preserves identity (every field survives).
        wat = ('(module\n'
               '  (type $sup (sub (struct (field i32))))\n'
               '  (type $sub (sub $sup (struct (field i32) (field i32))))\n'
               '  (func (export "f") (result i32)\n'
               '    (local $viol i32) (local $orig (ref $sub)) (local $up (ref $sup)) (local $down (ref $sub))\n'
               '    (local.set $orig (struct.new $sub (i32.const 42) (i32.const 99)))\n'
               '    (local.set $up (local.get $orig))\n'
               '    (local.set $down (ref.cast (ref $sub) (local.get $up)))\n'
               '    (local.set $viol (i32.ne (struct.get $sub 0 (local.get $down)) (struct.get $sub 0 (local.get $orig))))\n'
               '    (local.set $viol (i32.or (local.get $viol)\n'
               '      (i32.ne (struct.get $sub 1 (local.get $down)) (struct.get $sub 1 (local.get $orig)))))\n'
               '    (local.set $viol (i32.or (local.get $viol)\n'
               '      (i32.ne (struct.get $sup 0 (local.get $up)) (struct.get $sub 0 (local.get $orig)))))\n'
               '    (local.get $viol)))')
        return "consistency[roundtrip]", "OK 0", wat
    # LAW: ref.test(v,T) == 1  iff  ref.cast(v,T) succeeds — observed via br_on_cast (no host trap).
    wat = ('(module\n'
           '  (type $sup (sub (struct (field i32))))\n'
           '  (type $sub (sub $sup (struct (field i32) (field i32))))\n'
           '  (func $casts (param $v (ref $sup)) (result i32)\n'
           '    (block $f (result i32) (block $o (result (ref $sub))\n'
           '        (br_on_cast $o (ref $sup) (ref $sub) (local.get $v)) (br $f (i32.const 0)))\n'
           '      (drop) (i32.const 1)))\n'
           '  (func (export "f") (result i32)\n'
           '    (local $viol i32) (local $a (ref $sup)) (local $b (ref $sup))\n'
           '    (local.set $a (struct.new $sub (i32.const 1) (i32.const 2)))\n'   # really a $sub
           '    (local.set $b (struct.new $sup (i32.const 3)))\n'                 # only a $sup
           '    (local.set $viol (i32.xor (ref.test (ref $sub) (local.get $a)) (call $casts (local.get $a))))\n'
           '    (local.set $viol (i32.or (local.get $viol)\n'
           '      (i32.xor (ref.test (ref $sub) (local.get $b)) (call $casts (local.get $b)))))\n'
           '    (local.get $viol)))')
    return "consistency[test-iff-cast]", "OK 0", wat


# --- deeper families: rec-group canonicalization, function subtyping, nullability + br_on_cast ---
# Each entry is a verified (oracle-agreeing) program; the per-program answer is fixed by construction.

_RECGROUP = [
    ("self-recursive-twin", "OK 36", """(module
  (type $node  (sub (struct (field i32) (field (ref null $node)))))
  (type $node2 (sub (struct (field i32) (field (ref null $node2)))))
  (type $wide  (sub (struct (field i64) (field (ref null $wide)))))
  (func (export "f") (result i32)
    (local $x (ref null $node))
    (local.set $x
      (struct.new $node (i32.const 10)
        (struct.new $node (i32.const 20) (ref.null $node))))
    (i32.add (i32.add (i32.add (i32.add
      (i32.add
        (ref.test (ref $node)       (local.get $x))
        (ref.test (ref $node2)      (local.get $x)))
      (i32.add
        (ref.test (ref null $node)  (local.get $x))
        (ref.test (ref null $node)  (ref.null $node))))
      (i32.add
        (ref.test (ref $node)       (ref.null $node))
        (ref.test (ref $wide)       (local.get $x))))
      (i32.add
        (ref.test (ref struct)      (local.get $x))
        (ref.test (ref $node)
          (ref.cast (ref $node2) (local.get $x)))))
      (i32.add
        (struct.get $node2 0 (ref.cast (ref $node2) (local.get $x)))
        (struct.get $node 0
          (ref.cast (ref $node)
            (struct.get $node 1 (local.get $x))))))))"""),
    ("two-rec-groups-equal", "OK 55", """(module
  (rec (type $a1 (sub (struct (field i32) (field (ref null $b1)))))
       (type $b1 (sub (struct (field (ref null $a1))))))
  (rec (type $a2 (sub (struct (field i32) (field (ref null $b2)))))
       (type $b2 (sub (struct (field (ref null $a2))))))
  (func (export "f") (result i32)
    (local $x (ref null $a1))
    (local $y (ref null $b1))
    (local.set $y (struct.new $b1 (ref.null $a1)))
    (local.set $x (struct.new $a1 (i32.const 50) (local.get $y)))
    (i32.add (i32.add (i32.add (i32.add
      (i32.add
        (ref.test (ref $a1) (local.get $x))
        (ref.test (ref $a2) (local.get $x)))
      (i32.add
        (ref.test (ref $b1) (local.get $x))
        (ref.test (ref $b2) (local.get $x))))
      (i32.add
        (ref.test (ref $b1) (local.get $y))
        (ref.test (ref $b2) (local.get $y))))
      (i32.add
        (ref.test (ref $a1) (local.get $y))
        (ref.test (ref struct) (local.get $x))))
      (i32.add
        (struct.get $a2 0 (ref.cast (ref $a2) (local.get $x)))
        (ref.test (ref $a2) (ref.cast (ref $b2) (local.get $y)))))))"""),
    ("reordered-distinct-trap", "TRAP", """(module
  (rec (type $a (sub (struct (field i32) (field (ref null $b)))))
       (type $b (sub (struct (field (ref null $a))))))
  (rec (type $b2 (sub (struct (field (ref null $a2)))))
       (type $a2 (sub (struct (field i32) (field (ref null $b2))))))
  (rec (type $a3 (sub (struct (field i32) (field (ref null $b3)))))
       (type $b3 (sub (struct (field (ref null $a3))))))
  (func (export "f") (result i32)
    (local $x (ref null $a))
    (local.set $x (struct.new $a (i32.const 42) (ref.null $b)))
    (drop (ref.test (ref $a2) (local.get $x)))
    (drop (ref.test (ref $a3) (local.get $x)))
    (drop (ref.cast (ref $a3) (local.get $x)))
    (drop (struct.get $a 0 (ref.cast (ref $a) (local.get $x))))
    (struct.get $a2 0 (ref.cast (ref $a2) (local.get $x)))))"""),
    ("declared-chain", "OK 11", """(module
  (rec (type $base  (sub (struct (field i32) (field (ref null $base)))))
       (type $mid   (sub $base (struct (field i32) (field (ref null $base)) (field i32))))
       (type $leaf  (sub $mid  (struct (field i32) (field (ref null $base)) (field i32) (field i32)))))
  (func (export "f") (result i32)
    (local $l (ref null $leaf))
    (local $b (ref null $base))
    (local.set $l (struct.new $leaf (i32.const 1) (ref.null $base) (i32.const 2) (i32.const 3)))
    (local.set $b (struct.new $base (i32.const 9) (ref.null $base)))
    (i32.add (i32.add (i32.add (i32.add
      (i32.add
        (ref.test (ref $leaf) (local.get $l))
        (ref.test (ref $mid)  (local.get $l)))
      (i32.add
        (ref.test (ref $base) (local.get $l))
        (ref.test (ref $leaf) (local.get $b))))
      (i32.add
        (ref.test (ref $mid)  (local.get $b))
        (ref.test (ref $base) (local.get $b))))
      (i32.add
        (ref.test (ref $leaf) (ref.cast (ref $mid) (local.get $l)))
        (ref.test (ref $base) (local.get $l))))
      (i32.add
        (struct.get $leaf 3 (ref.cast (ref $leaf) (local.get $l)))
        (struct.get $mid  2 (ref.cast (ref $mid)  (local.get $l)))))))"""),
    ("singleton-equals-standalone", "OK 77", """(module
  (type $node (sub (struct (field i32) (field (ref null $node)))))
  (rec (type $rnode (sub (struct (field i32) (field (ref null $rnode))))))
  (rec (type $xnode (sub (struct (field i32) (field (ref null $xnode)) (field i32)))))
  (func (export "f") (result i32)
    (local $x (ref null $node))
    (local $r (ref null $rnode))
    (local.set $x (struct.new $node  (i32.const 30) (ref.null $node)))
    (local.set $r (struct.new $rnode (i32.const 40) (ref.null $rnode)))
    (i32.add (i32.add (i32.add (i32.add
      (i32.add
        (ref.test (ref $rnode) (local.get $x))
        (ref.test (ref $node)  (local.get $r)))
      (i32.add
        (ref.test (ref $node)  (local.get $x))
        (ref.test (ref $rnode) (local.get $r))))
      (i32.add
        (ref.test (ref $xnode) (local.get $x))
        (ref.test (ref $node)  (local.get $x))))
      (i32.add
        (struct.get $rnode 0 (ref.cast (ref $rnode) (local.get $x)))
        (struct.get $node  0 (ref.cast (ref $node)  (local.get $r)))))
      (i32.add
        (ref.test (ref struct) (local.get $x))
        (ref.test (ref eq)     (local.get $r))))))"""),
]

_FUNCSUB = [
    ("correct-variance", "OK 1", """(module
  (type $f (sub (func (param (ref eq)) (result (ref eq)))))
  (type $g (sub $f (func (param (ref any)) (result (ref i31)))))
  (func $impl (type $g) (param (ref any)) (result (ref i31)) (ref.i31 (i32.const 7)))
  (elem declare func $impl)
  (func (export "f") (result i32) (ref.test (ref $f) (ref.func $impl))))"""),
    ("wrong-variance", "OK 0", """(module
  (type $f (sub (func (param (ref eq)) (result (ref eq)))))
  (type $bad (sub (func (param (ref i31)) (result (ref any)))))
  (func $impl (type $bad) (param (ref i31)) (result (ref any)) (ref.i31 (i32.const 1)))
  (elem declare func $impl)
  (func (export "f") (result i32) (ref.test (ref $f) (ref.func $impl))))"""),
    ("wrong-result-covariance", "OK 0", """(module
  (type $f (sub (func (param (ref eq)) (result (ref i31)))))
  (type $bad (sub (func (param (ref any)) (result (ref eq)))))
  (func $impl (type $bad) (param (ref any)) (result (ref eq)) (ref.i31 (i32.const 1)))
  (elem declare func $impl)
  (func (export "f") (result i32) (ref.test (ref $f) (ref.func $impl))))"""),
    ("cast-up-then-call-ref", "OK 77", """(module
  (type $f (sub (func (param (ref eq)) (result (ref eq)))))
  (type $g (sub $f (func (param (ref any)) (result (ref i31)))))
  (func $impl (type $g) (param (ref any)) (result (ref i31)) (ref.i31 (i32.const 77)))
  (elem declare func $impl)
  (func (export "f") (result i32)
    (i31.get_s
      (ref.cast (ref i31)
        (call_ref $f
          (ref.i31 (i32.const 0))
          (ref.cast (ref $f) (ref.func $impl)))))))"""),
    ("br-on-cast-fail-fallthrough", "OK 7", """(module
  (type $f (sub (func (param (ref eq)) (result (ref eq)))))
  (type $g (sub $f (func (param (ref any)) (result (ref i31)))))
  (func $impl (type $g) (param (ref any)) (result (ref i31)) (ref.i31 (i32.const 5)))
  (elem declare func $impl)
  (func (export "f") (result i32)
    (block $miss (result (ref func))
      (br_on_cast_fail $miss (ref func) (ref $f) (ref.func $impl))
      (drop)
      (return (i32.const 7)))
    (drop)
    (i32.const 100)))"""),
]

_NULLABLE = [
    ("nullable-test-of-null", "OK 1",
     '(module (type $s (sub (struct (field i32))))\n  (func (export "f") (result i32) (ref.test (ref null $s) (ref.null $s))))'),
    ("nonnull-test-of-null", "OK 0",
     '(module (type $s (sub (struct (field i32))))\n  (func (export "f") (result i32) (ref.test (ref $s) (ref.null $s))))'),
    ("cast-nullable-of-null-succeeds", "OK 1",
     '(module (type $s (sub (struct (field i32))))\n  (func (export "f") (result i32) (ref.is_null (ref.cast (ref null $s) (ref.null $s)))))'),
    ("cast-nonnull-of-null-traps", "TRAP",
     '(module (type $s (sub (struct (field i32))))\n  (func (export "f") (result i32) (drop (ref.cast (ref $s) (ref.null $s))) (i32.const 99)))'),
    ("deep-chain-bottom-and-sibling", "OK 10", """(module
  (type $t0 (sub (struct (field i32))))
  (type $t1 (sub $t0 (struct (field i32) (field i32))))
  (type $t2 (sub $t1 (struct (field i32) (field i32) (field i32))))
  (type $t3 (sub $t2 (struct (field i32) (field i32) (field i32) (field i32))))
  (type $t3b (sub $t2 (struct (field i32) (field i32) (field i32) (field f64))))
  (type $t4 (sub $t3 (struct (field i32) (field i32) (field i32) (field i32) (field i32))))
  (type $t5 (sub $t4 (struct (field i32) (field i32) (field i32) (field i32) (field i32) (field i32))))
  (func (export "f") (result i32)
    (local $v (ref $t5))
    (local.set $v (struct.new $t5 (i32.const 1) (i32.const 2) (i32.const 3) (i32.const 4) (i32.const 5) (i32.const 6)))
    (i32.add
      (i32.mul (i32.const 10) (ref.test (ref $t5) (local.get $v)))
      (ref.test (ref $t3b) (local.get $v)))))"""),
]


def _recgroup(v):
    """ref.test / ref.cast over REC-GROUP and self-recursive types: a singleton group canonically equals
    a structurally-identical standalone type, a reordered group is DISTINCT (cast traps), a declared chain
    inside a group subtypes correctly. Stresses iso-recursive canonicalization."""
    name, exp, wat = _RECGROUP[v % len(_RECGROUP)]
    return f"recgroup[{name}]", exp, wat


def _funcsub(v):
    """Function-type subtyping variance: a funcref tests TRUE against a supertype func type only when params
    are contravariant (widened) and results covariant (narrowed); the wrong variance tests 0. Plus a cast-up
    then call_ref and a br_on_cast_fail fallthrough."""
    name, exp, wat = _FUNCSUB[v % len(_FUNCSUB)]
    return f"funcsub[{name}]", exp, wat


def _nullable(v):
    """Nullability: a nullable ref.test accepts null (1) where the non-null one rejects it (0); a nullable
    ref.cast of null succeeds where the non-null one traps; a deep (depth-6) subtype chain tests true at the
    bottom and false on a sibling branch."""
    name, exp, wat = _NULLABLE[v % len(_NULLABLE)]
    return f"nullable[{name}]", exp, wat


_FAMILIES = [_lattice, _cast, _structural, _funcref, _abstract, _consistency, _recgroup, _funcsub, _nullable]


def castalgebra_gen(seed):
    """Return (label, export, expected, wat): the seed-th subtype/cast-correctness probe."""
    fam = _FAMILIES[seed % len(_FAMILIES)]
    label, expected, wat = fam(seed // len(_FAMILIES))
    return f"castalgebra-{label}", "f", expected, wat


if __name__ == "__main__":
    import subprocess, os, re
    os.environ["DYLD_LIBRARY_PATH"] = os.environ.get("WASMEDGE_LIB", os.path.expanduser("~/wasm-engines/wasmedge/lib"))
    ENG = os.path.expanduser("~/wasm-engines")
    REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    WT = f"{ENG}/wasmtime-v46/wasmtime"; WE = f"{ENG}/wasmedge/bin/wasmedge"
    MCR = f"{REPO}/runner/target/release/mc-runner"
    NODE = os.path.expanduser("~/.nvm/versions/node/v26.3.0/bin/node"); V8 = f"{REPO}/miscast/oracle/v8.js"

    def res(p):
        out = ((p.stdout or "") + (p.stderr or "")).lower()
        if p.returncode != 0 or any(k in out for k in (
                "trap", "unreachable", "out of bounds", "cast", "execution failed", "null", "runtimeerror")):
            return "TRAP"
        m = re.findall(r"-?\d+", p.stdout or "")
        return f"OK {m[-1]}" if m else "OK _"

    def run(eng, wasm):
        if eng == "wt": c = [WT, "run", "-W", "function-references=y,gc=y", "--invoke", "f", wasm]
        elif eng == "we": c = [WE, "run", wasm, "f"]
        elif eng == "mcr": c = [MCR, wasm, "--invoke", "f"]
        elif eng == "v8": c = [NODE, V8, wasm, "f"]
        return res(subprocess.run(c, capture_output=True, text=True))

    print("=== castalgebra: conformant engines must answer ref.test/cast exactly per the lattice ===")
    bad = 0
    for s in range(54):
        label, export, expected, wat = castalgebra_gen(s)
        open("/tmp/ca.wat", "w").write(wat)
        a = subprocess.run(["wasm-tools", "parse", "/tmp/ca.wat", "-o", "/tmp/ca.wasm"], capture_output=True, text=True)
        if a.returncode != 0:
            print(f"  ASMFAIL {label}: {a.stderr.strip().splitlines()[-1][:64]}"); bad += 1; continue
        row = {e: run(e, "/tmp/ca.wasm") for e in ("wt", "we", "mcr", "v8")}
        ok = all(v == expected for v in row.values())
        bad += not ok
        print(f"  {'OK ' if ok else 'FAIL'} {label:34} exp={expected:8} {row}")
    print(f"\n54 programs, {bad} disagreeing")
