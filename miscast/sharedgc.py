"""Self-checking Shared-Everything GC atomics for experimental V8.

The generator is deterministic and independent of V8's test builder.  It composes
atomic aggregate operations with aliasing, tier-up loops, allocation/collection
pressure, reference identity, packed fields, and traps. Each case has a
closed-form or Python-simulated oracle.
"""

from .atomicedge import _OPS, _fold32, _fold64, _i32, _u32op

_HEAD = "(module\n  (;; miscast-sharedgc-stress ;;)\n"


def _struct_rmw(v):
    ty = "i32" if v % 2 == 0 else "i64"
    op = _OPS[(v // 2) % 4]
    order = "seq_cst" if v % 4 < 2 else "acq_rel"
    if ty == "i32":
        old = (0x6A000123 + v * 0x10101) & 0xFFFFFFFF
        arg = (0x91020304 ^ (v * 0x11111111)) & 0xFFFFFFFF
        new, result = _u32op(op, old, arg), _fold32(old, _u32op(op, old, arg))
        body = f'''(local $x (ref $s)) (local $old i32)
    (local.set $x (struct.new $s (i32.const {old})))
    (local.set $old (struct.atomic.rmw.{op} {order} $s 0 (local.get $x) (i32.const {arg})))
    (i32.xor (i32.mul (local.get $old) (i32.const 257))
             (struct.atomic.get {order} $s 0 (local.get $x)))'''
    else:
        old = (0x6A00012389ABCDEF + v * 0x0101010101010101) & 0xFFFFFFFFFFFFFFFF
        arg = (0x9102030405060708 ^ (v * 0x1111111111111111)) & 0xFFFFFFFFFFFFFFFF
        new, result = _u32op(op, old, arg, 64), _fold64(old, _u32op(op, old, arg, 64))
        body = f'''(local $x (ref $s)) (local $old i64) (local $new i64)
    (local.set $x (struct.new $s (i64.const {old})))
    (local.set $old (struct.atomic.rmw.{op} {order} $s 0 (local.get $x) (i64.const {arg})))
    (local.set $new (struct.atomic.get {order} $s 0 (local.get $x)))
    (i32.xor
      (i32.xor (i32.wrap_i64 (local.get $old)) (i32.wrap_i64 (i64.shr_u (local.get $old) (i64.const 32))))
      (i32.xor (i32.wrap_i64 (local.get $new)) (i32.wrap_i64 (i64.shr_u (local.get $new) (i64.const 32)))))'''
    wat = _HEAD + f'''  (type $s (shared (struct (field (mut {ty})))))
  (func (export "f") (result i32)
    {body}))'''
    return f"struct-rmw-{ty}-{op}-{order}", f"OK {result}", wat


def _struct_alias(v):
    order = "seq_cst" if v % 2 == 0 else "acq_rel"
    iterations = 300 + v * 17
    delta = (v % 5) + 1
    value, check = 17 + v, 0
    for i in range(iterations):
        old = value
        value = _i32(value + delta)
        check = _i32(check + _i32(old ^ value))
    wat = _HEAD + f'''  (type $s (shared (struct (field (mut i32)))))
  (func (export "f") (result i32)
    (local $a (ref $s)) (local $alias (ref $s))
    (local $i i32) (local $old i32) (local $sum i32)
    (local.set $a (struct.new $s (i32.const {17 + v})))
    (local.set $alias (local.get $a))
    (loop $again
      (local.set $old (struct.atomic.rmw.add {order} $s 0 (local.get $alias) (i32.const {delta})))
      (local.set $sum (i32.add (local.get $sum)
        (i32.xor (local.get $old) (struct.atomic.get {order} $s 0 (local.get $a)))))
      (drop (struct.new $s (local.get $i)))
      (local.set $i (i32.add (local.get $i) (i32.const 1)))
      (br_if $again (i32.lt_u (local.get $i) (i32.const {iterations}))))
    (local.get $sum)))'''
    return f"struct-alias-hot-{order}-{iterations}", f"OK {check}", wat


def _array_hot(v):
    order = "seq_cst" if v % 2 == 0 else "acq_rel"
    op = "add" if v % 4 < 2 else "xor"
    iterations = 350 + v * 13
    vals = [101 + v, 203 + v, 307 + v, 409 + v, 503 + v]
    check = 0
    for i in range(iterations):
        idx = (i * 3 + v) % 5
        arg = (i & 7) + 1
        old = vals[idx]
        vals[idx] = _u32op(op, old, arg)
        check = _i32(check + _i32(old ^ vals[idx]))
    wat = _HEAD + f'''  (type $a (shared (array (mut i32))))
  (func (export "f") (result i32)
    (local $a (ref $a)) (local $i i32) (local $idx i32)
    (local $arg i32) (local $old i32) (local $sum i32)
    (local.set $a (array.new $a (i32.const 0) (i32.const 5)))
    (array.atomic.set {order} $a (local.get $a) (i32.const 0) (i32.const {101 + v}))
    (array.atomic.set {order} $a (local.get $a) (i32.const 1) (i32.const {203 + v}))
    (array.atomic.set {order} $a (local.get $a) (i32.const 2) (i32.const {307 + v}))
    (array.atomic.set {order} $a (local.get $a) (i32.const 3) (i32.const {409 + v}))
    (array.atomic.set {order} $a (local.get $a) (i32.const 4) (i32.const {503 + v}))
    (loop $again
      (local.set $idx (i32.rem_u (i32.add (i32.mul (local.get $i) (i32.const 3)) (i32.const {v})) (i32.const 5)))
      (local.set $arg (i32.add (i32.and (local.get $i) (i32.const 7)) (i32.const 1)))
      (local.set $old (array.atomic.rmw.{op} {order} $a (local.get $a) (local.get $idx) (local.get $arg)))
      (local.set $sum (i32.add (local.get $sum) (i32.xor (local.get $old)
        (array.atomic.get {order} $a (local.get $a) (local.get $idx)))))
      (drop (array.new $a (local.get $i) (i32.const 3)))
      (local.set $i (i32.add (local.get $i) (i32.const 1)))
      (br_if $again (i32.lt_u (local.get $i) (i32.const {iterations}))))
    (local.get $sum)))'''
    return f"array-hot-{op}-{order}-{iterations}", f"OK {check}", wat


def _ref_atomic(v):
    order = "seq_cst" if v % 2 == 0 else "acq_rel"
    array = v % 4 >= 2
    cmp = v >= 4
    success = v % 2 == 0
    target = "$old" if success else "$same_value_other_identity"
    if array:
        aggregate_type = "(type $box (shared (array (mut (ref null $leaf)))))"
        alloc = "(array.new $box (local.get $old) (i32.const 3))"
        operands = "(local.get $box) (i32.const 1)"
        get = f"(array.atomic.get {order} $box (local.get $box) (i32.const 1))"
        kind = "array"
    else:
        aggregate_type = "(type $box (shared (struct (field (mut (ref null $leaf))))))"
        alloc = "(struct.new $box (local.get $old))"
        operands = "(local.get $box)"
        get = f"(struct.atomic.get {order} $box 0 (local.get $box))"
        kind = "struct"
    if cmp:
        op = f"{kind}.atomic.rmw.cmpxchg {order} $box" + ("" if array else " 0")
        action = f"({op} {operands} (local.get {target}) (local.get $new))"
        final = 22 if success else 11
        label = f"{kind}-ref-cmpxchg-{'success' if success else 'failure'}"
    else:
        op = f"{kind}.atomic.rmw.xchg {order} $box" + ("" if array else " 0")
        action = f"({op} {operands} (local.get $new))"
        final = 22
        label = f"{kind}-ref-xchg"
    wat = _HEAD + f'''  (type $leaf (shared (struct (field i32))))
  {aggregate_type}
  (func (export "f") (result i32)
    (local $old (ref $leaf)) (local $new (ref $leaf))
    (local $same_value_other_identity (ref $leaf)) (local $box (ref $box))
    (local $seen (ref null $leaf)) (local $i i32)
    (local.set $old (struct.new $leaf (i32.const 11)))
    (local.set $new (struct.new $leaf (i32.const 22)))
    (local.set $same_value_other_identity (struct.new $leaf (i32.const 11)))
    (local.set $box {alloc})
    (local.set $seen {action})
    (loop $gc
      (drop (struct.new $leaf (local.get $i)))
      (local.set $i (i32.add (local.get $i) (i32.const 1)))
      (br_if $gc (i32.lt_u (local.get $i) (i32.const {120 + v * 7}))))
    (i32.add
      (i32.mul (struct.get $leaf 0 (ref.as_non_null (local.get $seen))) (i32.const 100))
      (struct.get $leaf 0 (ref.as_non_null {get})))))'''
    return f"ref-{label}-{order}", f"OK {1100 + final}", wat


def _struct_cmp_numeric(v):
    """Numeric cmpxchg through a shared aggregate, including 64-bit high-word checks."""
    order = "seq_cst" if v % 2 == 0 else "acq_rel"
    ty = "i32" if v % 4 < 2 else "i64"
    success = v < 4
    if ty == "i32":
        old = (0x71234560 + v * 17) & 0xFFFFFFFF
        expected = old if success else old ^ 7
        replacement = (0x89ABCDEF ^ (v * 0x10101)) & 0xFFFFFFFF
        final = replacement if success else old
        body = f'''(local $x (ref $s)) (local $seen i32)
    (local.set $x (struct.new $s (i32.const {old})))
    (local.set $seen (struct.atomic.rmw.cmpxchg {order} $s 0 (local.get $x)
      (i32.const {expected}) (i32.const {replacement})))
    (i32.xor (i32.mul (local.get $seen) (i32.const 257))
      (struct.atomic.get {order} $s 0 (local.get $x)))'''
        result = _fold32(old, final)
    else:
        old = (0x7123456089ABCDEF + v * 0x0101010101010101) & 0xFFFFFFFFFFFFFFFF
        expected = old if success else old ^ 7
        replacement = (0x89ABCDEF10325476 ^ (v * 0x0102030405060708)) & 0xFFFFFFFFFFFFFFFF
        final = replacement if success else old
        body = f'''(local $x (ref $s)) (local $seen i64) (local $now i64)
    (local.set $x (struct.new $s (i64.const {old})))
    (local.set $seen (struct.atomic.rmw.cmpxchg {order} $s 0 (local.get $x)
      (i64.const {expected}) (i64.const {replacement})))
    (local.set $now (struct.atomic.get {order} $s 0 (local.get $x)))
    (i32.xor
      (i32.xor (i32.wrap_i64 (local.get $seen)) (i32.wrap_i64 (i64.shr_u (local.get $seen) (i64.const 32))))
      (i32.xor (i32.wrap_i64 (local.get $now)) (i32.wrap_i64 (i64.shr_u (local.get $now) (i64.const 32)))))'''
        result = _fold64(old, final)
    wat = _HEAD + f'''  (type $s (shared (struct (field (mut {ty})))))
  (func (export "f") (result i32)
    {body}))'''
    return f"struct-cmpxchg-{ty}-{'success' if success else 'failure'}-{order}", f"OK {result}", wat


def _array_ref_alias(v):
    """Reference identity across an aliased array, atomic replacement, and collection pressure."""
    order = "seq_cst" if v % 2 == 0 else "acq_rel"
    cmp = v % 4 >= 2
    success = v < 4
    idx = v % 2
    if cmp:
        expected = "$old" if success else "$same_value_other_identity"
        action = f"(array.atomic.rmw.cmpxchg {order} $box (local.get $alias) (i32.const {idx}) (local.get {expected}) (local.get $new))"
        final = 22 if success else 11
        label = f"cmpxchg-{'success' if success else 'failure'}"
    else:
        action = f"(array.atomic.rmw.xchg {order} $box (local.get $alias) (i32.const {idx}) (local.get $new))"
        final, label = 22, "xchg"
    # Both index 0 and 1 initially alias $old. The untouched slot is an extra
    # identity oracle that must remain the exact old reference after GC.
    untouched = 1 - idx
    result = 11000 + final * 10 + 1
    wat = _HEAD + f'''  (type $leaf (shared (struct (field i32))))
  (type $box (shared (array (mut (ref null $leaf)))))
  (func (export "f") (result i32)
    (local $old (ref $leaf)) (local $new (ref $leaf))
    (local $same_value_other_identity (ref $leaf))
    (local $box (ref $box)) (local $alias (ref $box))
    (local $seen (ref null $leaf)) (local $i i32)
    (local.set $old (struct.new $leaf (i32.const 11)))
    (local.set $new (struct.new $leaf (i32.const 22)))
    (local.set $same_value_other_identity (struct.new $leaf (i32.const 11)))
    (local.set $box (array.new $box (local.get $old) (i32.const 2)))
    (local.set $alias (local.get $box))
    (local.set $seen {action})
    (loop $gc
      (drop (array.new $box (local.get $new) (i32.const 3)))
      (local.set $i (i32.add (local.get $i) (i32.const 1)))
      (br_if $gc (i32.lt_u (local.get $i) (i32.const {140 + v * 9}))))
    (i32.add
      (i32.add
        (i32.mul (struct.get $leaf 0 (ref.as_non_null (local.get $seen))) (i32.const 1000))
        (i32.mul (struct.get $leaf 0 (ref.as_non_null
          (array.atomic.get {order} $box (local.get $box) (i32.const {idx})))) (i32.const 10)))
      (ref.eq (array.atomic.get {order} $box (local.get $box) (i32.const {untouched}))
              (local.get $old)))))'''
    return f"array-ref-alias-{label}-{order}", f"OK {result}", wat


def _packed_trap(v):
    order = "seq_cst" if v % 2 == 0 else "acq_rel"
    if v < 4:
        bits = 8 if v < 2 else 16
        signed = v % 2 == 0
        raw = 0xF3 if bits == 8 else 0xF123
        got = raw - (1 << bits) if signed else raw
        suffix = "s" if signed else "u"
        wat = _HEAD + f'''  (type $s (shared (struct (field (mut i{bits})))))
  (func (export "f") (result i32)
    (local $x (ref $s))
    (local.set $x (struct.new $s (i32.const 0)))
    (struct.atomic.set {order} $s 0 (local.get $x) (i32.const {raw + (1 << bits) * 3}))
    (struct.atomic.get_{suffix} {order} $s 0 (local.get $x))))'''
        return f"packed-struct-i{bits}-{suffix}-{order}", f"OK {got}", wat
    if v == 4:
        wat = _HEAD + f'''  (type $s (shared (struct (field (mut i32)))))
  (func (export "f") (result i32)
    (struct.atomic.get {order} $s 0 (ref.null $s))))'''
        return "null-struct-get", "TRAP", wat
    if v == 5:
        wat = _HEAD + f'''  (type $a (shared (array (mut i32))))
  (func (export "f") (result i32)
    (array.atomic.get {order} $a (array.new $a (i32.const 7) (i32.const 3)) (i32.const 3))))'''
        return "array-get-exact-end", "TRAP", wat
    if v == 6:
        wat = _HEAD + f'''  (type $a (shared (array (mut i32))))
  (func (export "f") (result i32)
    (array.atomic.rmw.add {order} $a (array.new $a (i32.const 7) (i32.const 3))
      (i32.const 0xFFFFFFFF) (i32.const 1))))'''
        return "array-rmw-u32-index", "TRAP", wat
    wat = _HEAD + f'''  (type $a (shared (array (mut i16))))
  (func (export "f") (result i32)
    (local $a (ref $a))
    (local.set $a (array.new $a (i32.const 0) (i32.const 2)))
    (array.atomic.set {order} $a (local.get $a) (i32.const 1) (i32.const 0x2F123))
    (array.atomic.get_s {order} $a (local.get $a) (i32.const 1))))'''
    return f"packed-array-i16-s-{order}", f"OK {0xF123 - 0x10000}", wat


_FAMILIES = (_struct_rmw, _struct_alias, _array_hot, _ref_atomic,
             _struct_cmp_numeric, _array_ref_alias, _packed_trap)


def sharedgc_gen(seed):
    family = _FAMILIES[seed % len(_FAMILIES)]
    label, expected, wat = family(seed // len(_FAMILIES))
    return f"sharedgc-{label}", "f", expected, wat
