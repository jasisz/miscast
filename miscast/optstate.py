"""Hot GC optimizer state/alias probes with a Python-computed oracle.

The families put a mutable load on both sides of an operation whose alias/effect is easy to lose during
load/store elimination: a possibly-aliasing parameter, dynamic array index, overlapping ``array.copy``,
``call_ref``, subtype cast, or extern round-trip.  Repeated calls force optimized code and periodic junk
allocations introduce GC safepoints.  Every final checksum is modeled here, independent of another engine.
"""

MARKER = "miscast-optstate-stress"
_ITERS = 320


def _signed(value):
    value &= 0xFFFFFFFF
    return value if value < 0x80000000 else value - 0x100000000


def _module(types, hot, body):
    return f"""(module ;; {MARKER}
{types}
  (type $junk (array (mut i64)))
  (global $sink (mut (ref null $junk)) (ref.null $junk))
  (func $tick (param $i i32)
    (if (i32.eqz (i32.and (local.get $i) (i32.const 15)))
      (then (global.set $sink (array.new_default $junk (i32.const 128))))))
{hot}
{body}
)"""


def _struct_alias(seed):
    a, b, total = 10 + seed, 20 + seed, 0
    for i in range(_ITERS):
        before = a
        value = 1000 + seed * 3 + i
        if i % 3 == 0:
            a = value
        else:
            b = value
        total = (total + before * 31 + a) & 0xFFFFFFFF
    types = "  (type $cell (struct (field (mut i32))))"
    hot = """  (func $hot (param $x (ref $cell)) (param $y (ref $cell)) (param $v i32) (result i32)
    (local $before i32)
    (local.set $before (struct.get $cell 0 (local.get $x)))
    (struct.set $cell 0 (local.get $y) (local.get $v))
    (i32.add (i32.mul (local.get $before) (i32.const 31))
      (struct.get $cell 0 (local.get $x))))"""
    body = f"""  (func (export "f") (result i32)
    (local $a (ref $cell)) (local $b (ref $cell)) (local $y (ref $cell))
    (local $i i32) (local $sum i32)
    (local.set $a (struct.new $cell (i32.const {10 + seed})))
    (local.set $b (struct.new $cell (i32.const {20 + seed})))
    (block $done (loop $loop
      (br_if $done (i32.ge_u (local.get $i) (i32.const {_ITERS})))
      (call $tick (local.get $i))
      (local.set $y
        (if (result (ref $cell)) (i32.eqz (i32.rem_u (local.get $i) (i32.const 3)))
          (then (local.get $a)) (else (local.get $b))))
      (local.set $sum (i32.add (local.get $sum)
        (call $hot (local.get $a) (local.get $y)
          (i32.add (i32.const {1000 + seed * 3}) (local.get $i)))))
      (local.set $i (i32.add (local.get $i) (i32.const 1)))
      (br $loop)))
    (local.get $sum))"""
    return "struct-param-alias", _signed(total), _module(types, hot, body)


def _array_alias(seed):
    arr = [30 + seed + i * 7 for i in range(4)]
    total = 0
    for i in range(_ITERS):
        idx = i & 3
        other = idx if i % 3 == 0 else (idx + 1) & 3
        before = arr[idx]
        arr[other] = 2000 + seed * 5 + i
        total = (total + before * 17 + arr[idx]) & 0xFFFFFFFF
    types = "  (type $arr (array (mut i32)))"
    hot = """  (func $hot (param $a (ref $arr)) (param $i i32) (param $j i32) (param $v i32) (result i32)
    (local $before i32)
    (local.set $before (array.get $arr (local.get $a) (local.get $i)))
    (array.set $arr (local.get $a) (local.get $j) (local.get $v))
    (i32.add (i32.mul (local.get $before) (i32.const 17))
      (array.get $arr (local.get $a) (local.get $i))))"""
    body = f"""  (func (export "f") (result i32)
    (local $a (ref $arr)) (local $i i32) (local $idx i32) (local $other i32) (local $sum i32)
    (local.set $a (array.new_fixed $arr 4
      (i32.const {30 + seed}) (i32.const {37 + seed})
      (i32.const {44 + seed}) (i32.const {51 + seed})))
    (block $done (loop $loop
      (br_if $done (i32.ge_u (local.get $i) (i32.const {_ITERS})))
      (call $tick (local.get $i))
      (local.set $idx (i32.and (local.get $i) (i32.const 3)))
      (local.set $other
        (select (local.get $idx) (i32.and (i32.add (local.get $idx) (i32.const 1)) (i32.const 3))
          (i32.eqz (i32.rem_u (local.get $i) (i32.const 3)))))
      (local.set $sum (i32.add (local.get $sum)
        (call $hot (local.get $a) (local.get $idx) (local.get $other)
          (i32.add (i32.const {2000 + seed * 5}) (local.get $i)))))
      (local.set $i (i32.add (local.get $i) (i32.const 1)))
      (br $loop)))
    (local.get $sum))"""
    return "array-index-alias", _signed(total), _module(types, hot, body)


def _array_copy(seed):
    arr = [100 + seed + i * 11 for i in range(6)]
    total = 0
    for i in range(_ITERS):
        dst, src, read = (1, 0, 3) if i % 2 == 0 else (0, 1, 2)
        before = arr[read]
        copied = arr[src:src + 4]
        arr[dst:dst + 4] = copied
        total = (total + before * 13 + arr[read]) & 0xFFFFFFFF
    types = "  (type $arr (array (mut i32)))"
    hot = """  (func $hot (param $a (ref $arr)) (param $dst i32) (param $src i32) (param $read i32) (result i32)
    (local $before i32)
    (local.set $before (array.get $arr (local.get $a) (local.get $read)))
    (array.copy $arr $arr (local.get $a) (local.get $dst)
      (local.get $a) (local.get $src) (i32.const 4))
    (i32.add (i32.mul (local.get $before) (i32.const 13))
      (array.get $arr (local.get $a) (local.get $read))))"""
    vals = " ".join(f"(i32.const {100 + seed + i * 11})" for i in range(6))
    body = f"""  (func (export "f") (result i32)
    (local $a (ref $arr)) (local $i i32) (local $sum i32) (local $even i32)
    (local.set $a (array.new_fixed $arr 6 {vals}))
    (block $done (loop $loop
      (br_if $done (i32.ge_u (local.get $i) (i32.const {_ITERS})))
      (call $tick (local.get $i))
      (local.set $even (i32.eqz (i32.and (local.get $i) (i32.const 1))))
      (local.set $sum (i32.add (local.get $sum)
        (call $hot (local.get $a)
          (select (i32.const 1) (i32.const 0) (local.get $even))
          (select (i32.const 0) (i32.const 1) (local.get $even))
          (select (i32.const 3) (i32.const 2) (local.get $even)))))
      (local.set $i (i32.add (local.get $i) (i32.const 1)))
      (br $loop)))
    (local.get $sum))"""
    return "array-copy-overlap", _signed(total), _module(types, hot, body)


def _call_ref(seed):
    value, total = 300 + seed, 0
    for i in range(_ITERS):
        before = value
        value = 3000 + seed * 7 + i
        total = (total + before * 7 + value) & 0xFFFFFFFF
    types = """  (type $cell (struct (field (mut i32))))
  (type $setter (func (param (ref $cell)) (param i32)))"""
    hot = """  (func $set (type $setter) (param $x (ref $cell)) (param $v i32)
    (struct.set $cell 0 (local.get $x) (local.get $v)))
  (elem declare func $set)
  (func $hot (param $x (ref $cell)) (param $v i32) (param $fn (ref $setter)) (result i32)
    (local $before i32)
    (local.set $before (struct.get $cell 0 (local.get $x)))
    (call_ref $setter (local.get $x) (local.get $v) (local.get $fn))
    (i32.add (i32.mul (local.get $before) (i32.const 7))
      (struct.get $cell 0 (local.get $x))))"""
    body = f"""  (func (export "f") (result i32)
    (local $x (ref $cell)) (local $i i32) (local $sum i32)
    (local.set $x (struct.new $cell (i32.const {300 + seed})))
    (block $done (loop $loop
      (br_if $done (i32.ge_u (local.get $i) (i32.const {_ITERS})))
      (call $tick (local.get $i))
      (local.set $sum (i32.add (local.get $sum)
        (call $hot (local.get $x) (i32.add (i32.const {3000 + seed * 7}) (local.get $i))
          (ref.func $set))))
      (local.set $i (i32.add (local.get $i) (i32.const 1)))
      (br $loop)))
    (local.get $sum))"""
    return "call-ref-effects", _signed(total), _module(types, hot, body)


def _cast_alias(seed):
    value, total = 400 + seed, 0
    for i in range(_ITERS):
        before = value
        value = 4000 + seed * 9 + i
        total = (total + before * 5 + value) & 0xFFFFFFFF
    types = """  (type $base (sub (struct (field (mut i32)))))
  (type $sub (sub $base (struct (field (mut i32)) (field i32))))"""
    hot = """  (func $hot (param $x (ref $base)) (param $v i32) (result i32)
    (local $before i32)
    (local.set $before (struct.get $base 0 (local.get $x)))
    (struct.set $sub 0 (ref.cast (ref $sub) (local.get $x)) (local.get $v))
    (i32.add (i32.mul (local.get $before) (i32.const 5))
      (struct.get $base 0 (local.get $x))))"""
    body = f"""  (func (export "f") (result i32)
    (local $x (ref $base)) (local $i i32) (local $sum i32)
    (local.set $x (struct.new $sub (i32.const {400 + seed}) (i32.const 17)))
    (block $done (loop $loop
      (br_if $done (i32.ge_u (local.get $i) (i32.const {_ITERS})))
      (call $tick (local.get $i))
      (local.set $sum (i32.add (local.get $sum)
        (call $hot (local.get $x) (i32.add (i32.const {4000 + seed * 9}) (local.get $i)))))
      (local.set $i (i32.add (local.get $i) (i32.const 1)))
      (br $loop)))
    (local.get $sum))"""
    return "cast-alias", _signed(total), _module(types, hot, body)


def _extern_alias(seed):
    value, total = 500 + seed, 0
    for i in range(_ITERS):
        before = value
        value = 5000 + seed * 11 + i
        total = (total + before * 3 + value) & 0xFFFFFFFF
    types = "  (type $cell (struct (field (mut i32))))"
    hot = """  (func $hot (param $x (ref $cell)) (param $v i32) (result i32) (local $y (ref $cell))
    (local $before i32)
    (local.set $before (struct.get $cell 0 (local.get $x)))
    (local.set $y (ref.cast (ref $cell)
      (any.convert_extern (extern.convert_any (local.get $x)))))
    (struct.set $cell 0 (local.get $y) (local.get $v))
    (i32.add (i32.mul (local.get $before) (i32.const 3))
      (struct.get $cell 0 (local.get $x))))"""
    body = f"""  (func (export "f") (result i32)
    (local $x (ref $cell)) (local $i i32) (local $sum i32)
    (local.set $x (struct.new $cell (i32.const {500 + seed})))
    (block $done (loop $loop
      (br_if $done (i32.ge_u (local.get $i) (i32.const {_ITERS})))
      (call $tick (local.get $i))
      (local.set $sum (i32.add (local.get $sum)
        (call $hot (local.get $x) (i32.add (i32.const {5000 + seed * 11}) (local.get $i)))))
      (local.set $i (i32.add (local.get $i) (i32.const 1)))
      (br $loop)))
    (local.get $sum))"""
    return "extern-roundtrip-alias", _signed(total), _module(types, hot, body)


_FAMILIES = (_struct_alias, _array_alias, _array_copy, _call_ref, _cast_alias, _extern_alias)


def optstate_gen(seed):
    """Return ``(label, export, expected, wat)`` for one optimizer-state probe."""
    family = _FAMILIES[seed % len(_FAMILIES)]
    label, expected, wat = family(seed)
    return f"optstate-{label}", "f", f"OK {expected}", wat
