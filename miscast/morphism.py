"""Representation-morphism shadow-GC oracle: a self-checking GC tester that needs no external engine, and
that not only DETECTS a GC divergence but ISOLATES it to a representation path.

One abstract object program (a data-segment "tape") is realized three ways over the same heap, all folding
the same checksum, plus a linear-memory shadow as ground truth:
  - CAST  rail: real Wasm GC, type membership via ref.test / ref.cast against the declared type $sub.
  - TAG   rail: real Wasm GC too, but cast-free — type membership comes from the tape tag and subtype
    fields are read through per-kind typed arrays, so it never executes a ref.cast / ref.test.
  - SHARD rail: identical to CAST except the sub-test uses $subB, a SEPARATELY DECLARED type that is
    structurally identical to $sub. Under iso-recursive canonicalization $sub and $subB are the SAME type,
    so a conformant engine must give the same answer; an engine that uses nominal (declaration) identity
    instead of canonical identity diverges here.

check() returns a bitmask that isolates the failing path:
  bit0 = cast  != shadow  -> a funcref / own-type ref.test or a value bug on the cast path
  bit1 = tag   != shadow  -> GC storage / relocation / identity (the cast-free rail diverged)
  bit2 = shard != cast    -> type canonicalization ($sub vs structurally-identical $subB disagree)
0 means every rail agrees and the canonicalization holds. A conformant engine returns 0 on every program;
the shadow can never be wrong about GC, so a single engine returning nonzero is a self-evident bug, and the
bits tell you which representation path is at fault. This caught the funcref ref.test bug (bit0) and a
struct-canonicalization divergence (bit2) in Talos and wasmz while wasmtime / WasmEdge / V8 stayed at 0.
"""
import random
import struct as _s

SHADOW = 16384


def gen(seed, K=16, bug=False):
    r = random.Random(seed)
    tape = b""
    for i in range(K):
        tape += _s.pack("<iiii", r.randint(0, 2), r.randint(-1, K - 1), r.randint(0, 4000) * 7 - 9000, r.randint(0, 1))
    data = "".join(f"\\{b:02x}" for b in tape)

    def T(i, off): return f"(i32.add (i32.mul {i} (i32.const 16)) (i32.const {off}))"
    def S(i, off): return f"(i32.add (i32.add (i32.const {SHADOW}) (i32.mul {i} (i32.const 20))) (i32.const {off}))"
    I = "(local.get $i)"
    link_expr = (f"(i32.rem_u (i32.add (local.get $lnk) (i32.const 1)) (i32.const {K}))" if bug else "(local.get $lnk)")

    def real_fold(hv, ST):
        # type membership / sub-field via ST (a ref.test / ref.cast rail). ST = $sub (cast) or $subB (shard).
        return f'''
      (local.set $cur (array.get $arr (local.get $a) {I}))
      (local.set ${hv} (i32.add (i32.mul (local.get ${hv}) (i32.const 31)) (struct.get $base $id (local.get $cur))))
      (local.set ${hv} (i32.add (i32.mul (local.get ${hv}) (i32.const 31)) (ref.test (ref {ST}) (local.get $cur))))
      (local.set ${hv} (i32.add (i32.mul (local.get ${hv}) (i32.const 31)) (ref.test (ref $sub2) (local.get $cur))))
      (local.set ${hv} (i32.add (i32.mul (local.get ${hv}) (i32.const 31))
        (if (result i32) (ref.test (ref {ST}) (local.get $cur)) (then (struct.get {ST} $extra (ref.cast (ref {ST}) (local.get $cur))))
          (else (if (result i32) (ref.test (ref $sub2) (local.get $cur)) (then (struct.get $sub2 $extra2 (ref.cast (ref $sub2) (local.get $cur)))) (else (i32.const 0)))))))
      (local.set $rr (struct.get $base $r (local.get $cur)))
      (local.set ${hv} (i32.add (i32.mul (local.get ${hv}) (i32.const 31)) (ref.eq (local.get $cur) (local.get $rr))))
      (local.set ${hv} (i32.add (i32.mul (local.get ${hv}) (i32.const 31)) (if (result i32) (ref.is_null (local.get $rr)) (then (i32.const -1)) (else (struct.get $base $id (local.get $rr))))))
      (local.set ${hv} (i32.add (i32.mul (local.get ${hv}) (i32.const 31)) (ref.test (ref $ftA) (table.get $t (i32.load {T(I,12)})))))
      (local.set ${hv} (i32.add (i32.mul (local.get ${hv}) (i32.const 31)) (ref.test (ref i31) (local.get $cur))))
      (local.set ${hv} (i32.add (i32.mul (local.get ${hv}) (i32.const 31)) (i31.get_u (ref.i31 (i32.load {T(I,8)})))))'''

    return f'''(module
  (type $base (sub (struct (field $id (mut i32)) (field $r (mut (ref null $base))))))
  (type $sub (sub $base (struct (field $id (mut i32)) (field $r (mut (ref null $base))) (field $extra (mut i32)))))
  (type $subB (sub $base (struct (field $id (mut i32)) (field $r (mut (ref null $base))) (field $extra (mut i32)))))
  (type $sub2 (sub $base (struct (field $id (mut i32)) (field $r (mut (ref null $base))) (field $extra2 (mut i32)) (field $pad (mut i32)))))
  (type $arr (array (mut (ref null $base))))
  (type $arrS (array (mut (ref null $sub))))
  (type $arrS2 (array (mut (ref null $sub2))))
  (type $ftA (func (result i32)))
  (type $ftB (func (param i32) (result i32)))
  (func $fa (type $ftA) (i32.const 111))
  (func $fb (type $ftB) (i32.add (local.get 0) (i32.const 222)))
  (table $t 2 funcref)
  (elem (table $t) (i32.const 0) func $fa $fb)
  (memory 1)
  (data (i32.const 0) "{data}")
  (func (export "check") (result i32)
    (local $a (ref $arr)) (local $as (ref $arrS)) (local $as2 (ref $arrS2))
    (local $i i32) (local $h i32) (local $tg i32) (local $ss i32) (local $sh i32)
    (local $cur (ref null $base)) (local $rr (ref null $base)) (local $tag i32) (local $lnk i32) (local $o (ref $sub)) (local $o2 (ref $sub2))
    (local.set $a (array.new_default $arr (i32.const {K})))
    (local.set $as (array.new_default $arrS (i32.const {K})))
    (local.set $as2 (array.new_default $arrS2 (i32.const {K})))
    (local.set $i (i32.const 0))
    (block $b1 (loop $l1 (br_if $b1 (i32.ge_u {I} (i32.const {K})))
      (local.set $tag (i32.load {T(I,0)}))
      (if (i32.eq (local.get $tag) (i32.const 1))
        (then (local.set $o (struct.new $sub {I} (ref.null $base) (i32.load {T(I,8)}))) (array.set $arr (local.get $a) {I} (local.get $o)) (array.set $arrS (local.get $as) {I} (local.get $o)))
        (else (if (i32.eq (local.get $tag) (i32.const 2))
          (then (local.set $o2 (struct.new $sub2 {I} (ref.null $base) (i32.load {T(I,8)}) (i32.const 0))) (array.set $arr (local.get $a) {I} (local.get $o2)) (array.set $arrS2 (local.get $as2) {I} (local.get $o2)))
          (else (array.set $arr (local.get $a) {I} (struct.new $base {I} (ref.null $base)))))))
      (local.set $i (i32.add {I} (i32.const 1))) (br $l1)))
    (local.set $i (i32.const 0))
    (block $b2 (loop $l2 (br_if $b2 (i32.ge_u {I} (i32.const {K})))
      (local.set $lnk (i32.load {T(I,4)}))
      (if (i32.ge_s (local.get $lnk) (i32.const 0)) (then (struct.set $base $r (array.get $arr (local.get $a) {I}) (array.get $arr (local.get $a) {link_expr}))))
      (drop (struct.new $base (i32.const -1) (ref.null $base))) (drop (struct.new $base (i32.const -1) (ref.null $base)))
      (local.set $i (i32.add {I} (i32.const 1))) (br $l2)))
    (local.set $i (i32.const 0))
    (block $b3 (loop $l3 (br_if $b3 (i32.ge_u {I} (i32.const {K})))
      (struct.set $base $id (array.get $arr (local.get $a) {I}) (i32.load {T(I,8)}))
      (local.set $i (i32.add {I} (i32.const 1))) (br $l3)))
    ;; CAST rail (ref.test / ref.cast against $sub)
    (local.set $i (i32.const 0))
    (block $b4 (loop $l4 (br_if $b4 (i32.ge_u {I} (i32.const {K}))){real_fold("h", "$sub")}
      (local.set $i (i32.add {I} (i32.const 1))) (br $l4)))
    ;; SHARD rail (same objects, sub-test against structurally-identical $subB)
    (local.set $i (i32.const 0))
    (block $bs (loop $ls (br_if $bs (i32.ge_u {I} (i32.const {K}))){real_fold("ss", "$subB")}
      (local.set $i (i32.add {I} (i32.const 1))) (br $ls)))
    ;; TAG rail (cast-free: typed arrays + tape tag)
    (local.set $i (i32.const 0))
    (block $bt (loop $lt (br_if $bt (i32.ge_u {I} (i32.const {K})))
      (local.set $cur (array.get $arr (local.get $a) {I})) (local.set $tag (i32.load {T(I,0)}))
      (local.set $tg (i32.add (i32.mul (local.get $tg) (i32.const 31)) (struct.get $base $id (local.get $cur))))
      (local.set $tg (i32.add (i32.mul (local.get $tg) (i32.const 31)) (i32.eq (local.get $tag) (i32.const 1))))
      (local.set $tg (i32.add (i32.mul (local.get $tg) (i32.const 31)) (i32.eq (local.get $tag) (i32.const 2))))
      (local.set $tg (i32.add (i32.mul (local.get $tg) (i32.const 31))
        (if (result i32) (i32.eq (local.get $tag) (i32.const 1)) (then (struct.get $sub $extra (array.get $arrS (local.get $as) {I})))
          (else (if (result i32) (i32.eq (local.get $tag) (i32.const 2)) (then (struct.get $sub2 $extra2 (array.get $arrS2 (local.get $as2) {I}))) (else (i32.const 0)))))))
      (local.set $rr (struct.get $base $r (local.get $cur)))
      (local.set $tg (i32.add (i32.mul (local.get $tg) (i32.const 31)) (ref.eq (local.get $cur) (local.get $rr))))
      (local.set $tg (i32.add (i32.mul (local.get $tg) (i32.const 31)) (if (result i32) (ref.is_null (local.get $rr)) (then (i32.const -1)) (else (struct.get $base $id (local.get $rr))))))
      (local.set $tg (i32.add (i32.mul (local.get $tg) (i32.const 31)) (i32.eq (i32.load {T(I,12)}) (i32.const 0))))
      (local.set $tg (i32.add (i32.mul (local.get $tg) (i32.const 31)) (i32.const 0)))
      (local.set $tg (i32.add (i32.mul (local.get $tg) (i32.const 31)) (i31.get_u (ref.i31 (i32.load {T(I,8)})))))
      (local.set $i (i32.add {I} (i32.const 1))) (br $lt)))
    ;; SHADOW (linear memory, ground truth)
    (local.set $i (i32.const 0))
    (block $b5 (loop $l5 (br_if $b5 (i32.ge_u {I} (i32.const {K})))
      (i32.store {S(I,0)} (i32.load {T(I,0)})) (i32.store {S(I,4)} (i32.load {T(I,8)})) (i32.store {S(I,8)} (i32.load {T(I,4)})) (i32.store {S(I,12)} (i32.load {T(I,8)})) (i32.store {S(I,16)} (i32.load {T(I,8)}))
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
      (local.set $sh (i32.add (i32.mul (local.get $sh) (i32.const 31)) (if (result i32) (i32.lt_s (local.get $lnk) (i32.const 0)) (then (i32.const -1)) (else (i32.load {S("(local.get $lnk)",4)})))))
      (local.set $sh (i32.add (i32.mul (local.get $sh) (i32.const 31)) (i32.eq (i32.load {T(I,12)}) (i32.const 0))))
      (local.set $sh (i32.add (i32.mul (local.get $sh) (i32.const 31)) (i32.const 0)))
      (local.set $sh (i32.add (i32.mul (local.get $sh) (i32.const 31)) (i32.and (i32.load {T(I,8)}) (i32.const 0x7fffffff))))
      (local.set $i (i32.add {I} (i32.const 1))) (br $l6)))
    ;; bit0 cast!=shadow, bit1 tag!=shadow, bit2 shard!=cast (canonicalization, isolated)
    (i32.or (i32.ne (local.get $h) (local.get $sh))
      (i32.or (i32.shl (i32.ne (local.get $tg) (local.get $sh)) (i32.const 1))
        (i32.shl (i32.ne (local.get $ss) (local.get $h)) (i32.const 2))))))
'''
