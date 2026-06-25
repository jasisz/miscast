"""Dual-rail shadow-GC oracle: a self-checking GC tester that needs no external engine.

One random program is emitted into TWO worlds that are equivalent by construction (both read the same
"tape" in a data segment):
  - REAL  uses actual Wasm GC: a subtype hierarchy ($base / $sub / $sub2), struct.new/set/get,
    ref.test / ref.cast / ref.eq, a funcref-type test, an i31 round-trip, churn that forces collection,
    and a post-GC field mutation.
  - SHADOW rebuilds the identical object graph by hand in linear memory (tag / id / r-index / extra per
    slot), with pure i32 loads/stores and no GC at all.
Both fold the SAME rolling hash over the traversal (ids, subtype-test outcomes, the right extra field,
ref.eq identity, the followed reference's id, the funcref-type test, and an i31 round-trip). `check`
returns real_hash - shadow_hash: 0 means the engine's GC agrees with the trustworthy linear-memory model,
a NONZERO means a GC bug (lowering, write barrier, relocation, object identity, or a subtype/cast check)
with the failing fold step as the witness. A conformant engine returns 0 on every program; the linear
-memory shadow can never be wrong about GC because it does not use GC.

This is the value-differential evolved from a scalar fingerprint (see reify.py) into a full running model
of the whole heap. It found the Talos #96 / wasmz #5 funcref subtype-check bug (`ref.test (ref $ft)` on a
concrete funcref) where a single returned i32 said 0 instead of the shadow's reference value.

Safety: a conformant engine returns 0; a buggy one returns nonzero; and a known-correct engine returning 0
validates that the two worlds are genuinely equivalent (so a nonzero elsewhere is that engine's bug, not a
generator artifact). `bug=True` deliberately breaks the REAL graph (links +1 instead of the tape value) to
prove the oracle detects a divergence.
"""
import random
import struct as _s

SHADOW = 8192   # 20-byte shadow slots: [tag, id, r_index, extra, extra2]; tape (16 B/obj) lives below it


def gen(seed, K=16, bug=False):
    r = random.Random(seed)
    tape = b""
    for i in range(K):
        tag = r.randint(0, 2)                          # 0=base, 1=sub, 2=sub2
        link = r.randint(-1, K - 1)                    # r-pointer target index, or -1 (null)
        mutval = r.randint(0, 4000) * 7 - 9000         # post-gc id / extra value
        fidx = r.randint(0, 1)                          # 0 -> $fa($ftA), 1 -> $fb($ftB)
        tape += _s.pack("<iiii", tag, link, mutval, fidx)
    data = "".join(f"\\{b:02x}" for b in tape)

    def T(i, off): return f"(i32.add (i32.mul {i} (i32.const 16)) (i32.const {off}))"
    def S(i, off): return f"(i32.add (i32.add (i32.const {SHADOW}) (i32.mul {i} (i32.const 20))) (i32.const {off}))"
    I = "(local.get $i)"
    link_expr = (f"(i32.rem_u (i32.add (local.get $lnk) (i32.const 1)) (i32.const {K}))" if bug
                 else "(local.get $lnk)")           # bug = a corrupted next-pointer (real links +1)

    return f'''(module
  (type $base (sub (struct (field $id (mut i32)) (field $r (mut (ref null $base))))))
  (type $sub (sub $base (struct (field $id (mut i32)) (field $r (mut (ref null $base))) (field $extra (mut i32)))))
  (type $sub2 (sub $base (struct (field $id (mut i32)) (field $r (mut (ref null $base))) (field $extra2 (mut i32)) (field $pad (mut i32)))))
  (type $arr (array (mut (ref null $base))))
  (type $ftA (func (result i32)))
  (type $ftB (func (param i32) (result i32)))
  (func $fa (type $ftA) (i32.const 111))
  (func $fb (type $ftB) (i32.add (local.get 0) (i32.const 222)))
  (table $t 2 funcref)
  (elem (table $t) (i32.const 0) func $fa $fb)
  (memory 1)
  (data (i32.const 0) "{data}")
  (func (export "check") (result i32)
    (local $a (ref $arr)) (local $i i32) (local $h i32) (local $sh i32)
    (local $cur (ref null $base)) (local $rr (ref null $base)) (local $tag i32) (local $lnk i32)
    (local.set $a (array.new_default $arr (i32.const {K})))
    (local.set $i (i32.const 0))
    (block $b1 (loop $l1 (br_if $b1 (i32.ge_u {I} (i32.const {K})))
      (local.set $tag (i32.load {T(I,0)}))
      (array.set $arr (local.get $a) {I}
        (if (result (ref $base)) (i32.eq (local.get $tag) (i32.const 1))
          (then (struct.new $sub {I} (ref.null $base) (i32.load {T(I,8)})))
          (else (if (result (ref $base)) (i32.eq (local.get $tag) (i32.const 2))
            (then (struct.new $sub2 {I} (ref.null $base) (i32.load {T(I,8)}) (i32.const 0)))
            (else (struct.new $base {I} (ref.null $base)))))))
      (local.set $i (i32.add {I} (i32.const 1))) (br $l1)))
    (local.set $i (i32.const 0))
    (block $b2 (loop $l2 (br_if $b2 (i32.ge_u {I} (i32.const {K})))
      (local.set $lnk (i32.load {T(I,4)}))
      (if (i32.ge_s (local.get $lnk) (i32.const 0))
        (then (struct.set $base $r (array.get $arr (local.get $a) {I}) (array.get $arr (local.get $a) {link_expr}))))
      (drop (struct.new $base (i32.const -1) (ref.null $base)))
      (drop (struct.new $base (i32.const -1) (ref.null $base)))
      (local.set $i (i32.add {I} (i32.const 1))) (br $l2)))
    (local.set $i (i32.const 0))
    (block $b3 (loop $l3 (br_if $b3 (i32.ge_u {I} (i32.const {K})))
      (struct.set $base $id (array.get $arr (local.get $a) {I}) (i32.load {T(I,8)}))
      (local.set $i (i32.add {I} (i32.const 1))) (br $l3)))
    (local.set $i (i32.const 0))
    (block $b4 (loop $l4 (br_if $b4 (i32.ge_u {I} (i32.const {K})))
      (local.set $cur (array.get $arr (local.get $a) {I}))
      (local.set $h (i32.add (i32.mul (local.get $h) (i32.const 31)) (struct.get $base $id (local.get $cur))))
      (local.set $h (i32.add (i32.mul (local.get $h) (i32.const 31)) (ref.test (ref $sub) (local.get $cur))))
      (local.set $h (i32.add (i32.mul (local.get $h) (i32.const 31)) (ref.test (ref $sub2) (local.get $cur))))
      (local.set $h (i32.add (i32.mul (local.get $h) (i32.const 31))
        (if (result i32) (ref.test (ref $sub) (local.get $cur))
          (then (struct.get $sub $extra (ref.cast (ref $sub) (local.get $cur))))
          (else (if (result i32) (ref.test (ref $sub2) (local.get $cur))
            (then (struct.get $sub2 $extra2 (ref.cast (ref $sub2) (local.get $cur)))) (else (i32.const 0)))))))
      (local.set $rr (struct.get $base $r (local.get $cur)))
      (local.set $h (i32.add (i32.mul (local.get $h) (i32.const 31)) (ref.eq (local.get $cur) (local.get $rr))))
      (local.set $h (i32.add (i32.mul (local.get $h) (i32.const 31))
        (if (result i32) (ref.is_null (local.get $rr)) (then (i32.const -1)) (else (struct.get $base $id (local.get $rr))))))
      (local.set $h (i32.add (i32.mul (local.get $h) (i32.const 31))
        (ref.test (ref $ftA) (table.get $t (i32.load {T(I,12)})))))
      (local.set $h (i32.add (i32.mul (local.get $h) (i32.const 31)) (ref.test (ref i31) (local.get $cur))))
      (local.set $h (i32.add (i32.mul (local.get $h) (i32.const 31)) (i31.get_u (ref.i31 (i32.load {T(I,8)})))))
      (local.set $i (i32.add {I} (i32.const 1))) (br $l4)))
    (local.set $i (i32.const 0))
    (block $b5 (loop $l5 (br_if $b5 (i32.ge_u {I} (i32.const {K})))
      (i32.store {S(I,0)} (i32.load {T(I,0)}))
      (i32.store {S(I,4)} (i32.load {T(I,8)}))
      (i32.store {S(I,8)} (i32.load {T(I,4)}))
      (i32.store {S(I,12)} (i32.load {T(I,8)}))
      (i32.store {S(I,16)} (i32.load {T(I,8)}))
      (local.set $i (i32.add {I} (i32.const 1))) (br $l5)))
    (local.set $i (i32.const 0))
    (block $b6 (loop $l6 (br_if $b6 (i32.ge_u {I} (i32.const {K})))
      (local.set $sh (i32.add (i32.mul (local.get $sh) (i32.const 31)) (i32.load {S(I,4)})))
      (local.set $sh (i32.add (i32.mul (local.get $sh) (i32.const 31)) (i32.eq (i32.load {S(I,0)}) (i32.const 1))))
      (local.set $sh (i32.add (i32.mul (local.get $sh) (i32.const 31)) (i32.eq (i32.load {S(I,0)}) (i32.const 2))))
      (local.set $sh (i32.add (i32.mul (local.get $sh) (i32.const 31))
        (if (result i32) (i32.eq (i32.load {S(I,0)}) (i32.const 1)) (then (i32.load {S(I,12)}))
          (else (if (result i32) (i32.eq (i32.load {S(I,0)}) (i32.const 2)) (then (i32.load {S(I,16)})) (else (i32.const 0)))))))
      (local.set $lnk (i32.load {S(I,8)}))
      (local.set $sh (i32.add (i32.mul (local.get $sh) (i32.const 31)) (i32.eq {I} (local.get $lnk))))
      (local.set $sh (i32.add (i32.mul (local.get $sh) (i32.const 31))
        (if (result i32) (i32.lt_s (local.get $lnk) (i32.const 0)) (then (i32.const -1)) (else (i32.load {S("(local.get $lnk)",4)})))))
      (local.set $sh (i32.add (i32.mul (local.get $sh) (i32.const 31)) (i32.eq (i32.load {T(I,12)}) (i32.const 0))))
      (local.set $sh (i32.add (i32.mul (local.get $sh) (i32.const 31)) (i32.const 0)))
      (local.set $sh (i32.add (i32.mul (local.get $sh) (i32.const 31)) (i32.and (i32.load {T(I,8)}) (i32.const 0x7fffffff))))
      (local.set $i (i32.add {I} (i32.const 1))) (br $l6)))
    (i32.sub (local.get $h) (local.get $sh))))
'''
