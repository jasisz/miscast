"""Remembered-set and bulk write-barrier stress for Wasm GC references.

Each probe keeps a destination container alive across allocation-driven collections, installs freshly
allocated objects into it, removes every direct local root, and collects again before reading a checksum.
This isolates old-to-young and relocation barriers from the broader alias semantics covered by mutalias and
heapstorm.  The backend recognizes ``MARKER`` and enables aggressive moving/minor collection where available.
"""

MARKER = "miscast-barrier-stress"

_PRE_CHURN = 40
_POST_CHURN = 40
_HOT_ITERS = 8

_PREFIX = f"""(module ;; {MARKER}
  (type $cell (struct (field i32)))
  (type $link (struct (field i32) (field (mut (ref null $link)))))
  (type $box (struct
    (field (mut (ref null $cell)))
    (field (mut (ref null $cell)))
    (field (mut (ref null $cell)))))
  (type $refs (array (mut (ref null $cell))))
  (type $junk (array (mut i64)))
  (global $box-root (mut (ref null $box)) (ref.null $box))
  (global $array-root (mut (ref null $refs)) (ref.null $refs))
  (global $link-root (mut (ref null $link)) (ref.null $link))
  (global $junk-root (mut (ref null $junk)) (ref.null $junk))
  (table $src-table 8 (ref null $cell))
  (table $dst-table 8 (ref null $cell))
  (func $churn (param $n i32) (local $i i32)
    (block $done
      (loop $again
        (br_if $done (i32.ge_u (local.get $i) (local.get $n)))
        (global.set $junk-root (array.new_default $junk (i32.const 512)))
        (local.set $i (i32.add (local.get $i) (i32.const 1)))
        (br $again))))
"""


def _driver():
    return f"""  (func (export "f") (result i32) (local $i i32) (local $answer i32)
    (block $done
      (loop $hot
        (local.set $answer (call $probe))
        (local.set $i (i32.add (local.get $i) (i32.const 1)))
        (br_if $hot (i32.lt_u (local.get $i) (i32.const {_HOT_ITERS})))))
    (local.get $answer))
)"""


def _module(probe):
    return _PREFIX + probe + "\n" + _driver()


def _struct_set(seed):
    values = [1000 + seed * 19 + i * 23 for i in range(3)]
    expected = values[0] * 3 + values[1] * 5 + values[2] * 7
    probe = f"""  (func $probe (result i32) (local $x (ref null $cell))
    (global.set $box-root
      (struct.new $box (ref.null $cell) (ref.null $cell) (ref.null $cell)))
    (call $churn (i32.const {_PRE_CHURN}))
    (local.set $x (struct.new $cell (i32.const {values[0]})))
    (struct.set $box 0 (ref.as_non_null (global.get $box-root)) (local.get $x))
    (local.set $x (struct.new $cell (i32.const {values[1]})))
    (struct.set $box 1 (ref.as_non_null (global.get $box-root)) (local.get $x))
    (local.set $x (struct.new $cell (i32.const {values[2]})))
    (struct.set $box 2 (ref.as_non_null (global.get $box-root)) (local.get $x))
    (local.set $x (ref.null $cell))
    (call $churn (i32.const {_POST_CHURN}))
    (i32.add
      (i32.add
        (i32.mul (i32.const 3) (struct.get $cell 0
          (ref.as_non_null (struct.get $box 0 (ref.as_non_null (global.get $box-root))))))
        (i32.mul (i32.const 5) (struct.get $cell 0
          (ref.as_non_null (struct.get $box 1 (ref.as_non_null (global.get $box-root)))))))
      (i32.mul (i32.const 7) (struct.get $cell 0
        (ref.as_non_null (struct.get $box 2 (ref.as_non_null (global.get $box-root))))))))"""
    return "struct-set", expected, _module(probe)


def _array_set(seed):
    values = [2000 + seed * 17 + i * 29 for i in range(4)]
    expected = sum((i + 3) * v for i, v in enumerate(values))
    stores = "\n".join(
        f"    (local.set $x (struct.new $cell (i32.const {v})))\n"
        f"    (array.set $refs (ref.as_non_null (global.get $array-root)) (i32.const {i}) (local.get $x))"
        for i, v in enumerate(values)
    )
    terms = [
        f"(i32.mul (i32.const {i + 3}) (struct.get $cell 0 (ref.as_non_null "
        f"(array.get $refs (ref.as_non_null (global.get $array-root)) (i32.const {i})))))"
        for i in range(4)
    ]
    checksum = terms[0]
    for term in terms[1:]:
        checksum = f"(i32.add {checksum} {term})"
    probe = f"""  (func $probe (result i32) (local $x (ref null $cell))
    (global.set $array-root (array.new_default $refs (i32.const 8)))
    (call $churn (i32.const {_PRE_CHURN}))
{stores}
    (local.set $x (ref.null $cell))
    (call $churn (i32.const {_POST_CHURN}))
    {checksum})"""
    return "array-set", expected, _module(probe)


def _array_fill(seed):
    value = 3000 + seed * 31
    expected = value * (11 + 13 + 17 + 19)
    probe = f"""  (func $probe (result i32) (local $x (ref null $cell))
    (global.set $array-root (array.new_default $refs (i32.const 8)))
    (call $churn (i32.const {_PRE_CHURN}))
    (local.set $x (struct.new $cell (i32.const {value})))
    (array.fill $refs (ref.as_non_null (global.get $array-root))
      (i32.const 2) (local.get $x) (i32.const 4))
    (local.set $x (ref.null $cell))
    (call $churn (i32.const {_POST_CHURN}))
    (i32.add
      (i32.add
        (i32.mul (i32.const 11) (struct.get $cell 0 (ref.as_non_null
          (array.get $refs (ref.as_non_null (global.get $array-root)) (i32.const 2)))))
        (i32.mul (i32.const 13) (struct.get $cell 0 (ref.as_non_null
          (array.get $refs (ref.as_non_null (global.get $array-root)) (i32.const 3))))))
      (i32.add
        (i32.mul (i32.const 17) (struct.get $cell 0 (ref.as_non_null
          (array.get $refs (ref.as_non_null (global.get $array-root)) (i32.const 4)))))
        (i32.mul (i32.const 19) (struct.get $cell 0 (ref.as_non_null
          (array.get $refs (ref.as_non_null (global.get $array-root)) (i32.const 5))))))))"""
    return "array-fill", expected, _module(probe)


def _array_copy(seed):
    values = [4000 + seed * 13 + i * 37 for i in range(4)]
    expected = sum((23 + i * 6) * v for i, v in enumerate(values))
    init = "\n".join(
        f"    (local.set $x (struct.new $cell (i32.const {v})))\n"
        f"    (array.set $refs (local.get $src) (i32.const {i}) (local.get $x))"
        for i, v in enumerate(values)
    )
    terms = [
        f"(i32.mul (i32.const {23 + i * 6}) (struct.get $cell 0 (ref.as_non_null "
        f"(array.get $refs (ref.as_non_null (global.get $array-root)) (i32.const {i + 2})))))"
        for i in range(4)
    ]
    checksum = terms[0]
    for term in terms[1:]:
        checksum = f"(i32.add {checksum} {term})"
    probe = f"""  (func $probe (result i32)
    (local $src (ref null $refs)) (local $x (ref null $cell))
    (global.set $array-root (array.new_default $refs (i32.const 8)))
    (call $churn (i32.const {_PRE_CHURN}))
    (local.set $src (array.new_default $refs (i32.const 4)))
{init}
    (array.copy $refs $refs (ref.as_non_null (global.get $array-root)) (i32.const 2)
      (ref.as_non_null (local.get $src)) (i32.const 0) (i32.const 4))
    (array.fill $refs (ref.as_non_null (local.get $src))
      (i32.const 0) (ref.null $cell) (i32.const 4))
    (local.set $x (ref.null $cell))
    (local.set $src (ref.null $refs))
    (call $churn (i32.const {_POST_CHURN}))
    {checksum})"""
    return "array-copy", expected, _module(probe)


def _table_bulk(seed):
    values = [5000 + seed * 11 + i * 41 for i in range(3)]
    expected = values[0] * 43 + values[1] * 47 + values[2] * 53
    probe = f"""  (func $probe (result i32) (local $x (ref null $cell))
    (table.fill $src-table (i32.const 0) (ref.null $cell) (i32.const 8))
    (table.fill $dst-table (i32.const 0) (ref.null $cell) (i32.const 8))
    (call $churn (i32.const {_PRE_CHURN}))
    (local.set $x (struct.new $cell (i32.const {values[0]})))
    (table.set $src-table (i32.const 0) (local.get $x))
    (local.set $x (struct.new $cell (i32.const {values[1]})))
    (table.set $src-table (i32.const 1) (local.get $x))
    (local.set $x (struct.new $cell (i32.const {values[2]})))
    (table.set $src-table (i32.const 2) (local.get $x))
    (table.copy $dst-table $src-table (i32.const 3) (i32.const 0) (i32.const 3))
    (table.fill $src-table (i32.const 0) (ref.null $cell) (i32.const 3))
    (local.set $x (ref.null $cell))
    (call $churn (i32.const {_POST_CHURN}))
    (i32.add
      (i32.add
        (i32.mul (i32.const 43) (struct.get $cell 0
          (ref.as_non_null (table.get $dst-table (i32.const 3)))))
        (i32.mul (i32.const 47) (struct.get $cell 0
          (ref.as_non_null (table.get $dst-table (i32.const 4))))))
      (i32.mul (i32.const 53) (struct.get $cell 0
        (ref.as_non_null (table.get $dst-table (i32.const 5)))))))"""
    return "table-copy", expected, _module(probe)


def _global_chain(seed):
    values = [6000 + seed * 7 + i * 43 for i in range(4)]
    expected = sum((59 + i * 2) * v for i, v in enumerate(values))
    build = f"""    (local.set $x (struct.new $link (i32.const {values[3]}) (ref.null $link)))
    (local.set $x (struct.new $link (i32.const {values[2]}) (local.get $x)))
    (local.set $x (struct.new $link (i32.const {values[1]}) (local.get $x)))
    (local.set $x (struct.new $link (i32.const {values[0]}) (local.get $x)))"""
    refs = ["(ref.as_non_null (global.get $link-root))"]
    for _ in range(3):
        refs.append(f"(ref.as_non_null (struct.get $link 1 {refs[-1]}))")
    terms = [f"(i32.mul (i32.const {59 + i * 2}) (struct.get $link 0 {ref}))" for i, ref in enumerate(refs)]
    checksum = terms[0]
    for term in terms[1:]:
        checksum = f"(i32.add {checksum} {term})"
    probe = f"""  (func $probe (result i32) (local $x (ref null $link))
    (global.set $link-root (struct.new $link (i32.const 0) (ref.null $link)))
    (call $churn (i32.const {_PRE_CHURN}))
{build}
    (struct.set $link 1 (ref.as_non_null (global.get $link-root)) (local.get $x))
    (global.set $link-root (ref.as_non_null
      (struct.get $link 1 (ref.as_non_null (global.get $link-root)))))
    (local.set $x (ref.null $link))
    (call $churn (i32.const {_POST_CHURN}))
    {checksum})"""
    return "nested-chain", expected, _module(probe)


_FAMILIES = (_struct_set, _array_set, _array_fill, _array_copy, _table_bulk, _global_chain)


def barrier_gen(seed):
    """Return ``(label, export, expected, wat)`` for one write-barrier probe."""
    family = _FAMILIES[seed % len(_FAMILIES)]
    label, expected, wat = family(seed)
    expected &= 0xFFFFFFFF
    if expected >= 0x80000000:
        expected -= 0x100000000
    return f"barrier-{label}", "f", f"OK {expected}", wat
