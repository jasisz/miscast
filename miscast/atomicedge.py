"""Deterministic shared-memory atomics probes with a baked Python oracle.

No scheduler is involved: every module has one Wasm agent.  The cases target the
semantics that optimizing compilers still have to preserve around atomic accesses:
RMW returns the old value, cmpxchg has distinct success/failure paths, narrow RMWs
truncate their stored value, atomic and plain accesses observe one another, and
alignment/effective-address checks trap at the exact boundary.  wait/notify cases
are chosen so they return immediately and cannot hang.
"""


def _i32(n):
    n &= 0xFFFFFFFF
    return n - 0x100000000 if n & 0x80000000 else n


def _u32op(op, old, arg, bits=32):
    mask = (1 << bits) - 1
    old, arg = old & mask, arg & mask
    return {
        "add": old + arg,
        "sub": old - arg,
        "and": old & arg,
        "or": old | arg,
        "xor": old ^ arg,
        "xchg": arg,
    }[op] & mask


def _fold32(old, new):
    return _i32(((old & 0xFFFFFFFF) * 257) ^ (new & 0xFFFFFFFF))


def _fold64(old, new):
    x = old ^ (old >> 32) ^ new ^ (new >> 32)
    return _i32(x)


_OPS = ("add", "sub", "and", "or", "xor", "xchg")


def _rmw32(v):
    op = _OPS[v % len(_OPS)]
    old = (0x7FFFFF00 + v * 0x10101) & 0xFFFFFFFF
    arg = (0x80000123 ^ (v * 0x11111111)) & 0xFFFFFFFF
    new = _u32op(op, old, arg)
    wat = f'''(module
  (memory 1 2 shared)
  (func (export "f") (result i32)
    (local $old i32)
    (i32.atomic.store (i32.const 32) (i32.const {old}))
    (local.set $old (i32.atomic.rmw.{op} (i32.const 32) (i32.const {arg})))
    (i32.xor
      (i32.mul (local.get $old) (i32.const 257))
      (i32.atomic.load (i32.const 32)))))'''
    return f"rmw32-{op}", f"OK {_fold32(old, new)}", wat


def _rmw64(v):
    op = _OPS[v % len(_OPS)]
    old = (0x7FFFFFF0ABCDEF00 + v * 0x0101010101010101) & 0xFFFFFFFFFFFFFFFF
    arg = (0x8000000123456789 ^ (v * 0x1111111111111111)) & 0xFFFFFFFFFFFFFFFF
    new = _u32op(op, old, arg, 64)
    wat = f'''(module
  (memory 1 2 shared)
  (func (export "f") (result i32)
    (local $old i64) (local $new i64)
    (i64.atomic.store (i32.const 40) (i64.const {old}))
    (local.set $old (i64.atomic.rmw.{op} (i32.const 40) (i64.const {arg})))
    (local.set $new (i64.atomic.load (i32.const 40)))
    (i32.xor
      (i32.xor (i32.wrap_i64 (local.get $old))
               (i32.wrap_i64 (i64.shr_u (local.get $old) (i64.const 32))))
      (i32.xor (i32.wrap_i64 (local.get $new))
               (i32.wrap_i64 (i64.shr_u (local.get $new) (i64.const 32)))))))'''
    return f"rmw64-{op}", f"OK {_fold64(old, new)}", wat


def _packed(v):
    bits = 8 if v % 2 == 0 else 16
    op = _OPS[(v // 2) % len(_OPS)]
    old = (0xA5F0 + v * 37) & ((1 << bits) - 1)
    arg = (0x1FF23 ^ (v * 0x1234)) & 0xFFFFFFFF
    new = _u32op(op, old, arg, bits)
    addr = 51 if bits == 8 else 52
    wat = f'''(module
  (memory 1 2 shared)
  (func (export "f") (result i32)
    (local $old i32)
    (i32.atomic.store{bits} (i32.const {addr}) (i32.const {old}))
    (local.set $old (i32.atomic.rmw{bits}.{op}_u (i32.const {addr}) (i32.const {arg})))
    (i32.xor
      (i32.mul (local.get $old) (i32.const 257))
      (i32.atomic.load{bits}_u (i32.const {addr})))))'''
    return f"packed{bits}-{op}", f"OK {_fold32(old, new)}", wat


def _cmpxchg(v):
    bits = (32, 64, 8, 16)[v % 4]
    success = (v // 4) % 2 == 0
    old = 0x7A if bits == 8 else 0x7A55 if bits == 16 else 0x7A551234
    if bits == 64:
        old = 0x7A55123489ABCDEF
    expected = old if success else old ^ 3
    replacement = 0xE7 if bits == 8 else 0xE755 if bits == 16 else 0xE7554321
    if bits == 64:
        replacement = 0xE755432110325476
    final = replacement if success else old
    ty = "i64" if bits == 64 else "i32"
    store = f"{ty}.atomic.store" + (str(bits) if bits in (8, 16) else "")
    load = f"{ty}.atomic.load" + (f"{bits}_u" if bits in (8, 16) else "")
    rmw = f"{ty}.atomic.rmw" + (str(bits) if bits in (8, 16) else "") + ".cmpxchg" + ("_u" if bits in (8, 16) else "")
    align = 8 if bits == 64 else 4 if bits == 32 else bits // 8
    addr = 64 if align >= 4 else 67 if align == 1 else 66
    if bits == 64:
        body = f'''(local $seen i64) (local $now i64)
    ({store} (i32.const {addr}) (i64.const {old}))
    (local.set $seen ({rmw} (i32.const {addr}) (i64.const {expected}) (i64.const {replacement})))
    (local.set $now ({load} (i32.const {addr})))
    (i32.xor
      (i32.xor (i32.wrap_i64 (local.get $seen)) (i32.wrap_i64 (i64.shr_u (local.get $seen) (i64.const 32))))
      (i32.xor (i32.wrap_i64 (local.get $now)) (i32.wrap_i64 (i64.shr_u (local.get $now) (i64.const 32)))))'''
        result = _fold64(old, final)
    else:
        body = f'''(local $seen i32)
    ({store} (i32.const {addr}) (i32.const {old}))
    (local.set $seen ({rmw} (i32.const {addr}) (i32.const {expected}) (i32.const {replacement})))
    (i32.xor (i32.mul (local.get $seen) (i32.const 257)) ({load} (i32.const {addr})))'''
        result = _fold32(old, final)
    wat = f'''(module
  (memory 1 2 shared)
  (func (export "f") (result i32)
    {body}))'''
    return f"cmpxchg{bits}-{'success' if success else 'failure'}", f"OK {result}", wat


def _boundary(v):
    shape = v % 8
    if shape == 0:
        op, addr, expected = "i32.atomic.load", 65532, "OK 31337"
        setup = "(i32.atomic.store (i32.const 65532) (i32.const 31337))"
        expr = f"({op} (i32.const {addr}))"
        label = "last-aligned-i32"
    elif shape == 1:
        setup, expr, expected, label = "", "(i32.atomic.load (i32.const 65533))", "TRAP", "oob-i32"
    elif shape == 2:
        setup, expr, expected, label = "", "(i32.atomic.load (i32.const 1))", "TRAP", "misaligned-load"
    elif shape == 3:
        setup, expr, expected, label = "", "(i32.atomic.rmw.add (i32.const 2) (i32.const 1))", "TRAP", "misaligned-rmw"
    elif shape == 4:
        setup, expr, expected, label = "", "(i32.atomic.rmw.cmpxchg (i32.const 3) (i32.const 0) (i32.const 1))", "TRAP", "misaligned-cmpxchg"
    elif shape == 5:
        setup, expr, expected, label = "", "(i32.atomic.load offset=4294967295 (i32.const 1))", "TRAP", "ea-wrap"
    elif shape == 6:
        setup, expr, expected, label = "", "(i64.atomic.load (i32.const 65528))", f"OK {_i32(0x89ABCDEF)}", "last-aligned-i64"
        setup = "(i64.atomic.store (i32.const 65528) (i64.const 81985529216486895))"
        expr = f"(i32.wrap_i64 {expr})"
    else:
        setup, expr, expected, label = "", "(i64.atomic.load (i32.const 65532))", "TRAP", "oob-i64"
        expr = f"(i32.wrap_i64 {expr})"
    wat = f'''(module
  (memory 1 2 shared)
  (func (export "f") (result i32)
    {setup}
    {expr}))'''
    return f"boundary-{label}", expected, wat


def _waitnotify(v):
    shape = v % 4
    if shape == 0:
        label, expected = "wait32-not-equal", "OK 1"
        body = '''(i32.atomic.store (i32.const 80) (i32.const 7))
    (memory.atomic.wait32 (i32.const 80) (i32.const 8) (i64.const -1))'''
    elif shape == 1:
        label, expected = "wait64-not-equal", "OK 1"
        body = '''(i64.atomic.store (i32.const 88) (i64.const 9))
    (memory.atomic.wait64 (i32.const 88) (i64.const 10) (i64.const -1))'''
    elif shape == 2:
        label, expected = "notify-no-waiters", "OK 0"
        body = "(memory.atomic.notify (i32.const 96) (i32.const 2147483647))"
    else:
        label, expected = "wait-misaligned", "TRAP"
        body = "(memory.atomic.wait32 (i32.const 81) (i32.const 0) (i64.const 0))"
    wat = f'''(module
  (memory 1 2 shared)
  (func (export "f") (result i32)
    {body}))'''
    return label, expected, wat


def _visibility(v):
    shape = v % 4
    if shape == 0:
        label, expected = "plain-store-atomic-load", 0x12345678
        body = '''(i32.store (i32.const 112) (i32.const 0x12345678))
    (i32.atomic.load (i32.const 112))'''
    elif shape == 1:
        label, expected = "atomic-store-plain-load", _i32(0x89ABCDEF)
        body = '''(i32.atomic.store (i32.const 112) (i32.const 0x89ABCDEF))
    (i32.load (i32.const 112))'''
    elif shape == 2:
        label, expected = "grow-then-atomic", 424242
        body = '''(drop (memory.grow (i32.const 1)))
    (i32.atomic.store (i32.const 65536) (i32.const 424242))
    (i32.atomic.load (i32.const 65536))'''
    else:
        iterations = 20000 + (v // 4) * 3
        label, expected = f"hot-rmw-loop-{iterations}", iterations
        body = f'''(local $i i32)
    (i32.atomic.store (i32.const 112) (i32.const 0))
    (loop $again
      (drop (i32.atomic.rmw.add (i32.const 112) (i32.const 1)))
      (local.set $i (i32.add (local.get $i) (i32.const 1)))
      (br_if $again (i32.lt_u (local.get $i) (i32.const {iterations}))))
    (i32.load (i32.const 112))'''
    wat = f'''(module
  (memory 1 2 shared)
  (func (export "f") (result i32)
    {body}))'''
    return label, f"OK {expected}", wat


_FAMILIES = (_rmw32, _rmw64, _packed, _cmpxchg, _boundary, _waitnotify, _visibility)


def atomicedge_gen(seed):
    family = _FAMILIES[seed % len(_FAMILIES)]
    label, expected, wat = family(seed // len(_FAMILIES))
    return f"atomicedge-{label}", "f", expected, wat
