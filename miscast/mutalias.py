"""Mutable-alias / write-visibility generator.

`refalias` checks that two references still name the same object. This generator goes one step harder: mutate
a GC object through one path and read it through another. A conformant engine must preserve object identity,
write visibility, type-view consistency, and write barriers across arrays, tables, extern round-trips, casts,
exceptions, call_ref/tail-call paths, structural twins, and subtype views.
"""

_TYPES = """  (type $cell (sub (struct (field (mut i32)))))
  (type $subcell (sub $cell (struct (field (mut i32)) (field i32))))
  (type $box (sub (struct (field (mut (ref $cell))) (field (mut (ref $cell))))))
  (type $arr (array (mut (ref null $cell))))
"""


def _cell(value):
    return f"(struct.new $cell (i32.const {value}))"


def _set_cell(ref_expr, value):
    return f"(struct.set $cell 0 {ref_expr} (i32.const {value}))"


def _get_cell(ref_expr):
    return f"(struct.get $cell 0 {ref_expr})"


def _fresh_not_shared(v):
    old, new = 10 + v, 1000 + v
    wat = f"""(module
{_TYPES}
  (func (export "f") (result i32) (local $x (ref $cell)) (local $y (ref $cell))
    (local.set $x {_cell(old)})
    (local.set $y {_cell(old)})
    {_set_cell("(local.get $x)", new)}
    {_get_cell("(local.get $y)")}))"""
    return "fresh-equal-shape-not-shared", f"OK {old}", wat


def _struct_field_alias(v):
    old, new = 20 + v, 1020 + v
    wat = f"""(module
{_TYPES}
  (func (export "f") (result i32) (local $x (ref $cell)) (local $b (ref $box))
    (local.set $x {_cell(old)})
    (local.set $b (struct.new $box (local.get $x) (local.get $x)))
    {_set_cell("(struct.get $box 0 (local.get $b))", new)}
    {_get_cell("(struct.get $box 1 (local.get $b))")}))"""
    return "struct-field-alias-write", f"OK {new}", wat


def _struct_field_repoint(v):
    old, new, other = 30 + v, 1030 + v, 2030 + v
    wat = f"""(module
{_TYPES}
  (func (export "f") (result i32) (local $x (ref $cell)) (local $y (ref $cell)) (local $b (ref $box))
    (local.set $x {_cell(old)})
    (local.set $y {_cell(other)})
    (local.set $b (struct.new $box (local.get $x) (local.get $y)))
    (struct.set $box 1 (local.get $b) (local.get $x))
    {_set_cell("(struct.get $box 0 (local.get $b))", new)}
    {_get_cell("(struct.get $box 1 (local.get $b))")}))"""
    return "struct-field-repoint-then-write", f"OK {new}", wat


def _array_slots_alias(v):
    old, new = 40 + v, 1040 + v
    wat = f"""(module
{_TYPES}
  (func (export "f") (result i32) (local $x (ref $cell)) (local $a (ref $arr))
    (local.set $x {_cell(old)})
    (local.set $a (array.new_default $arr (i32.const 2)))
    (array.set $arr (local.get $a) (i32.const 0) (local.get $x))
    (array.set $arr (local.get $a) (i32.const 1) (local.get $x))
    {_set_cell("(ref.as_non_null (array.get $arr (local.get $a) (i32.const 0)))", new)}
    {_get_cell("(ref.as_non_null (array.get $arr (local.get $a) (i32.const 1)))")}))"""
    return "array-slots-alias-write", f"OK {new}", wat


def _array_copy_alias(v):
    old, new = 50 + v, 1050 + v
    wat = f"""(module
{_TYPES}
  (func (export "f") (result i32) (local $x (ref $cell)) (local $a (ref $arr))
    (local.set $x {_cell(old)})
    (local.set $a (array.new_default $arr (i32.const 2)))
    (array.set $arr (local.get $a) (i32.const 0) (local.get $x))
    (array.copy $arr $arr (local.get $a) (i32.const 1) (local.get $a) (i32.const 0) (i32.const 1))
    {_set_cell("(ref.as_non_null (array.get $arr (local.get $a) (i32.const 0)))", new)}
    {_get_cell("(ref.as_non_null (array.get $arr (local.get $a) (i32.const 1)))")}))"""
    return "array-copy-alias-write", f"OK {new}", wat


def _table_copy_alias(v):
    old, new = 60 + v, 1060 + v
    wat = f"""(module
{_TYPES}
  (table $t 2 (ref null $cell))
  (func (export "f") (result i32) (local $x (ref $cell))
    (local.set $x {_cell(old)})
    (table.set $t (i32.const 0) (local.get $x))
    (table.copy $t $t (i32.const 1) (i32.const 0) (i32.const 1))
    {_set_cell("(ref.as_non_null (table.get $t (i32.const 0)))", new)}
    {_get_cell("(ref.as_non_null (table.get $t (i32.const 1)))")}))"""
    return "table-copy-alias-write", f"OK {new}", wat


def _table_fill_alias(v):
    old, new = 70 + v, 1070 + v
    wat = f"""(module
{_TYPES}
  (table $t 2 (ref null $cell))
  (func (export "f") (result i32) (local $x (ref $cell))
    (local.set $x {_cell(old)})
    (table.fill $t (i32.const 0) (local.get $x) (i32.const 2))
    {_set_cell("(ref.as_non_null (table.get $t (i32.const 0)))", new)}
    {_get_cell("(ref.as_non_null (table.get $t (i32.const 1)))")}))"""
    return "table-fill-alias-write", f"OK {new}", wat


def _global_table_alias(v):
    old, new = 80 + v, 1080 + v
    wat = f"""(module
{_TYPES}
  (global $g (mut (ref null $cell)) (ref.null $cell))
  (table $t 1 (ref null $cell))
  (func (export "f") (result i32) (local $x (ref $cell))
    (local.set $x {_cell(old)})
    (global.set $g (local.get $x))
    (table.set $t (i32.const 0) (local.get $x))
    {_set_cell("(ref.as_non_null (global.get $g))", new)}
    {_get_cell("(ref.as_non_null (table.get $t (i32.const 0)))")}))"""
    return "global-table-alias-write", f"OK {new}", wat


def _extern_alias(v):
    old, new = 90 + v, 1090 + v
    wat = f"""(module
{_TYPES}
  (func (export "f") (result i32) (local $x (ref $cell)) (local $y (ref $cell))
    (local.set $x {_cell(old)})
    (local.set $y (ref.cast (ref $cell) (any.convert_extern (extern.convert_any (local.get $x)))))
    {_set_cell("(local.get $y)", new)}
    {_get_cell("(local.get $x)")}))"""
    return "extern-roundtrip-alias-write", f"OK {new}", wat


def _br_on_cast_alias(v):
    old, new = 100 + v, 1100 + v
    wat = f"""(module
{_TYPES}
  (func (export "f") (result i32) (local $x (ref $cell)) (local $y (ref $cell))
    (local.set $x {_cell(old)})
    (local.set $y
      (block $ok (result (ref $cell))
        (br_on_cast $ok (ref eq) (ref $cell) (local.get $x))
        (unreachable)))
    {_set_cell("(local.get $y)", new)}
    {_get_cell("(local.get $x)")}))"""
    return "br_on_cast-alias-write", f"OK {new}", wat


def _try_table_alias(v):
    old, new = 110 + v, 1110 + v
    wat = f"""(module
{_TYPES}
  (tag $e (param (ref $cell)))
  (func (export "f") (result i32) (local $x (ref $cell)) (local $y (ref $cell))
    (local.set $x {_cell(old)})
    (local.set $y
      (block $h (result (ref $cell))
        (try_table (catch $e $h)
          (throw $e (local.get $x)))
        (unreachable)))
    {_set_cell("(local.get $y)", new)}
    {_get_cell("(local.get $x)")}))"""
    return "try_table-alias-write", f"OK {new}", wat


def _throw_ref_alias(v):
    old, new = 120 + v, 1120 + v
    wat = f"""(module
{_TYPES}
  (tag $e (param (ref $cell)))
  (func (export "f") (result i32) (local $x (ref $cell)) (local $y (ref $cell)) (local $ex exnref)
    (local.set $x {_cell(old)})
    (block $cap (result (ref $cell) exnref)
      (try_table (catch_ref $e $cap)
        (throw $e (local.get $x)))
      (unreachable))
    (local.set $ex)
    (drop)
    (local.set $y
      (block $h (result (ref $cell))
        (try_table (catch $e $h)
          (throw_ref (local.get $ex))
          (unreachable))
        (unreachable)))
    {_set_cell("(local.get $y)", new)}
    {_get_cell("(local.get $x)")}))"""
    return "throw_ref-alias-write", f"OK {new}", wat


def _call_ref_alias(v):
    old, new = 130 + v, 1130 + v
    wat = f"""(module
{_TYPES}
  (type $mut (sub (func (param (ref $cell)) (result (ref $cell)))))
  (func $mut (type $mut) (param (ref $cell)) (result (ref $cell))
    {_set_cell("(local.get 0)", new)}
    (local.get 0))
  (elem declare func $mut)
  (func (export "f") (result i32) (local $x (ref $cell))
    (local.set $x {_cell(old)})
    (drop (call_ref $mut (local.get $x) (ref.func $mut)))
    {_get_cell("(local.get $x)")}))"""
    return "call_ref-mutates-alias", f"OK {new}", wat


def _return_call_ref_alias(v):
    old, new = 140 + v, 1140 + v
    wat = f"""(module
{_TYPES}
  (type $mut (sub (func (param (ref $cell)) (result (ref $cell)))))
  (func $mut (type $mut) (param (ref $cell)) (result (ref $cell))
    {_set_cell("(local.get 0)", new)}
    (local.get 0))
  (func $bounce (param (ref $cell)) (param (ref $mut)) (result (ref $cell))
    (return_call_ref $mut (local.get 0) (local.get 1)))
  (elem declare func $mut)
  (func (export "f") (result i32) (local $x (ref $cell))
    (local.set $x {_cell(old)})
    (drop (call $bounce (local.get $x) (ref.func $mut)))
    {_get_cell("(local.get $x)")}))"""
    return "return_call_ref-mutates-alias", f"OK {new}", wat


def _structural_twin_view(v):
    old, new = 150 + v, 1150 + v
    wat = f"""(module
  (type $a (struct (field (mut i32))))
  (type $b (struct (field (mut i32))))
  (func (export "f") (result i32) (local $x (ref $a))
    (local.set $x (struct.new $a (i32.const {old})))
    (struct.set $b 0 (ref.cast (ref $b) (local.get $x)) (i32.const {new}))
    (struct.get $a 0 (local.get $x))))"""
    return "structural-twin-view-write", f"OK {new}", wat


def _subtype_base_view(v):
    old, new = 160 + v, 1160 + v
    wat = f"""(module
{_TYPES}
  (func (export "f") (result i32) (local $x (ref $subcell)) (local $b (ref $cell))
    (local.set $x (struct.new $subcell (i32.const {old}) (i32.const 7)))
    (local.set $b (local.get $x))
    {_set_cell("(local.get $b)", new)}
    (struct.get $subcell 0 (local.get $x))))"""
    return "subtype-base-view-write", f"OK {new}", wat


_FAMILIES = [_fresh_not_shared, _struct_field_alias, _struct_field_repoint, _array_slots_alias,
             _array_copy_alias, _table_copy_alias, _table_fill_alias, _global_table_alias, _extern_alias,
             _br_on_cast_alias, _try_table_alias, _throw_ref_alias, _call_ref_alias, _return_call_ref_alias,
             _structural_twin_view, _subtype_base_view]


def mutalias_gen(seed):
    """Return (label, export, expected, wat): the seed-th mutable-alias probe."""
    fam = _FAMILIES[seed % len(_FAMILIES)]
    label, expected, wat = fam(seed // len(_FAMILIES))
    return f"mutalias-{label}", "f", expected, wat


if __name__ == "__main__":
    import re
    import shutil
    import subprocess

    def res(p):
        both = (p.stdout + p.stderr).lower()
        if p.returncode != 0 or any(k in both for k in (
                "trap", "unreachable", "null", "cast", "execution failed", "runtimeerror")):
            return "TRAP"
        nums = re.findall(r"-?\d+", p.stdout or "")
        return "OK " + nums[-1] if nums else "OK _"

    print("=== mutalias: writes through one alias must be visible through the other ===")
    bad = 0
    for s in range(2 * len(_FAMILIES)):
        label, export, expected, wat = mutalias_gen(s)
        p = subprocess.run(["wasm-tools", "parse", "/dev/stdin", "-o", "/tmp/ma.wasm"],
                           input=wat, capture_output=True, text=True)
        if p.returncode != 0:
            print(f"  ASMFAIL {label}: {p.stderr.strip().splitlines()[-1][:96]}")
            bad += 1
            continue
        if shutil.which("wasmtime"):
            wt = subprocess.run(["wasmtime", "run", "-W", "function-references=y,gc=y,exceptions=y,tail-call=y",
                                 "--invoke", export, "/tmp/ma.wasm"], capture_output=True, text=True)
            got = res(wt)
            ok = got == expected
            bad += not ok
            print(f"  {'OK ' if ok else 'FAIL'} {label:40} exp={expected:8} wasmtime={got}")
        else:
            print(f"  OK  {label:40} assembles")
    print(f"\n{2 * len(_FAMILIES)} programs, {bad} disagreeing with the oracle")
