"""Runtime operand-evaluation / stack-pop order probes.

Every case feeds multi-operand instructions with calls to `next()`, which increments a global and returns
the previous counter value. The expected checksum proves both how many operands were evaluated and which
value each operand position received. This catches engines that accidentally pop operands in the wrong order
for bulk ops, calls, branches, or aggregate constructors.
"""

_COUNTER = """  (global $g (mut i32) (i32.const 0))
  (func $next (result i32)
    (local $old i32)
    (local.set $old (global.get $g))
    (global.set $g (i32.add (local.get $old) (i32.const 1)))
    (local.get $old))
"""


def _memory_copy(_v):
    wat = f"""(module
  (memory 1)
  (data (i32.const 0) "\\0a\\0b\\0c\\0d\\0e")
{_COUNTER}
  (func (export "f") (result i32)
    (memory.copy (call $next) (call $next) (call $next))
    (i32.add
      (i32.mul (global.get $g) (i32.const 10000))
      (i32.add
        (i32.mul (i32.load8_u (i32.const 0)) (i32.const 100))
        (i32.load8_u (i32.const 1))))))"""
    return "memory-copy-operands", "OK 31112", wat


def _memory_fill(_v):
    wat = f"""(module
  (memory 1)
  (data (i32.const 0) "\\0a\\0b\\0c\\0d")
{_COUNTER}
  (func (export "f") (result i32)
    (memory.fill (call $next) (call $next) (call $next))
    (i32.add
      (i32.mul (global.get $g) (i32.const 10000))
      (i32.add
        (i32.mul (i32.load8_u (i32.const 0)) (i32.const 100))
        (i32.load8_u (i32.const 1))))))"""
    return "memory-fill-operands", "OK 30101", wat


def _memory_init(_v):
    wat = f"""(module
  (memory 1)
  (data $d "\\0a\\0b\\0c\\0d\\0e")
{_COUNTER}
  (func (export "f") (result i32)
    (memory.init $d (call $next) (call $next) (call $next))
    (i32.add
      (i32.mul (global.get $g) (i32.const 10000))
      (i32.add
        (i32.mul (i32.load8_u (i32.const 0)) (i32.const 100))
        (i32.load8_u (i32.const 1))))))"""
    return "memory-init-operands", "OK 31112", wat


def _array_copy(_v):
    wat = f"""(module
  (type $a (array (mut i32)))
{_COUNTER}
  (func (export "f") (result i32) (local $a (ref $a))
    (local.set $a (array.new_fixed $a 5
      (i32.const 10) (i32.const 11) (i32.const 12) (i32.const 13) (i32.const 14)))
    (array.copy $a $a (local.get $a) (call $next) (local.get $a) (call $next) (call $next))
    (i32.add
      (i32.mul (global.get $g) (i32.const 100000))
      (i32.add
        (i32.mul (array.get $a (local.get $a) (i32.const 0)) (i32.const 1000))
        (array.get $a (local.get $a) (i32.const 1))))))"""
    return "array-copy-operands", "OK 311012", wat


def _array_fill(_v):
    wat = f"""(module
  (type $a (array (mut i32)))
{_COUNTER}
  (func (export "f") (result i32) (local $a (ref $a))
    (local.set $a (array.new_fixed $a 4
      (i32.const 10) (i32.const 11) (i32.const 12) (i32.const 13)))
    (array.fill $a (local.get $a) (call $next) (call $next) (call $next))
    (i32.add
      (i32.mul (global.get $g) (i32.const 100000))
      (i32.add
        (i32.mul (array.get $a (local.get $a) (i32.const 0)) (i32.const 1000))
        (array.get $a (local.get $a) (i32.const 1))))))"""
    return "array-fill-operands", "OK 301001", wat


def _array_init_data(_v):
    wat = f"""(module
  (type $a (array (mut i8)))
  (data $d "\\0a\\0b\\0c\\0d\\0e")
{_COUNTER}
  (func (export "f") (result i32) (local $a (ref $a))
    (local.set $a (array.new_default $a (i32.const 5)))
    (array.init_data $a $d (local.get $a) (call $next) (call $next) (call $next))
    (i32.add
      (i32.mul (global.get $g) (i32.const 10000))
      (i32.add
        (i32.mul (array.get_u $a (local.get $a) (i32.const 0)) (i32.const 100))
        (array.get_u $a (local.get $a) (i32.const 1))))))"""
    return "array-init-data-operands", "OK 31112", wat


def _array_init_elem(_v):
    wat = f"""(module
  (type $a (array (mut (ref null i31))))
  (elem $e (ref i31)
    (ref.i31 (i32.const 10)) (ref.i31 (i32.const 11)) (ref.i31 (i32.const 12))
    (ref.i31 (i32.const 13)) (ref.i31 (i32.const 14)))
{_COUNTER}
  (func (export "f") (result i32) (local $a (ref $a))
    (local.set $a (array.new_default $a (i32.const 5)))
    (array.init_elem $a $e (local.get $a) (call $next) (call $next) (call $next))
    (i32.add
      (i32.mul (global.get $g) (i32.const 100000))
      (i32.add
        (i32.mul (i31.get_s (ref.as_non_null (array.get $a (local.get $a) (i32.const 0)))) (i32.const 1000))
        (i31.get_s (ref.as_non_null (array.get $a (local.get $a) (i32.const 1))))))))"""
    return "array-init-elem-operands", "OK 311012", wat


def _array_new(_v):
    wat = f"""(module
  (type $a (array i32))
{_COUNTER}
  (func (export "f") (result i32) (local $a (ref $a))
    (local.set $a (array.new $a (call $next) (call $next)))
    (i32.add
      (i32.mul (global.get $g) (i32.const 1000))
      (i32.add
        (i32.mul (array.len (local.get $a)) (i32.const 10))
        (array.get $a (local.get $a) (i32.const 0))))))"""
    return "array-new-value-before-length", "OK 2010", wat


def _table_copy(_v):
    wat = f"""(module
  (table $t 5 (ref null i31))
{_COUNTER}
  (func (export "f") (result i32)
    (table.set $t (i32.const 0) (ref.i31 (i32.const 10)))
    (table.set $t (i32.const 1) (ref.i31 (i32.const 11)))
    (table.set $t (i32.const 2) (ref.i31 (i32.const 12)))
    (table.set $t (i32.const 3) (ref.i31 (i32.const 13)))
    (table.copy $t $t (call $next) (call $next) (call $next))
    (i32.add
      (i32.mul (global.get $g) (i32.const 100000))
      (i32.add
        (i32.mul (i31.get_s (ref.as_non_null (table.get $t (i32.const 0)))) (i32.const 1000))
        (i31.get_s (ref.as_non_null (table.get $t (i32.const 1))))))))"""
    return "table-copy-operands", "OK 311012", wat


def _table_fill(_v):
    wat = f"""(module
  (table $t 4 (ref null i31))
{_COUNTER}
  (func (export "f") (result i32)
    (table.set $t (i32.const 0) (ref.i31 (i32.const 10)))
    (table.set $t (i32.const 1) (ref.i31 (i32.const 11)))
    (table.fill $t (call $next) (ref.i31 (call $next)) (call $next))
    (i32.add
      (i32.mul (global.get $g) (i32.const 100000))
      (i32.add
        (i32.mul (i31.get_s (ref.as_non_null (table.get $t (i32.const 0)))) (i32.const 1000))
        (i31.get_s (ref.as_non_null (table.get $t (i32.const 1))))))))"""
    return "table-fill-operands", "OK 301001", wat


def _table_init(_v):
    wat = f"""(module
  (table $t 5 (ref null i31))
  (elem $e (ref i31)
    (ref.i31 (i32.const 10)) (ref.i31 (i32.const 11)) (ref.i31 (i32.const 12))
    (ref.i31 (i32.const 13)) (ref.i31 (i32.const 14)))
{_COUNTER}
  (func (export "f") (result i32)
    (table.init $e (call $next) (call $next) (call $next))
    (i32.add
      (i32.mul (global.get $g) (i32.const 100000))
      (i32.add
        (i32.mul (i31.get_s (ref.as_non_null (table.get $t (i32.const 0)))) (i32.const 1000))
        (i31.get_s (ref.as_non_null (table.get $t (i32.const 1))))))))"""
    return "table-init-operands", "OK 311012", wat


def _table_grow(_v):
    wat = f"""(module
  (table $t 1 (ref null i31))
{_COUNTER}
  (func (export "f") (result i32) (local $old i32)
    (local.set $old (table.grow $t (ref.i31 (call $next)) (call $next)))
    (i32.add
      (i32.mul (global.get $g) (i32.const 1000))
      (i32.add
        (i32.mul (local.get $old) (i32.const 10))
        (i31.get_s (ref.as_non_null (table.get $t (i32.const 1))))))))"""
    return "table-grow-value-before-delta", "OK 2010", wat


def _table_set(_v):
    wat = f"""(module
  (table $t 2 (ref null i31))
{_COUNTER}
  (func (export "f") (result i32)
    (table.set $t (i32.const 0) (ref.i31 (i32.const 10)))
    (table.set $t (call $next) (ref.i31 (call $next)))
    (i32.add
      (i32.mul (global.get $g) (i32.const 1000))
      (i31.get_s (ref.as_non_null (table.get $t (i32.const 0)))))))"""
    return "table-set-index-before-value", "OK 2001", wat


def _select(_v):
    wat = f"""(module
{_COUNTER}
  (func (export "f") (result i32)
    (i32.add
      (i32.mul (global.get $g) (i32.const 10000))
      (select (result i32) (call $next) (call $next) (call $next)))))"""
    # The folded operands are evaluated left-to-right: value1=0, value2=1, condition=2 => select value1.
    # `global.get` is evaluated before the select, so it sees 0.
    return "select-operands", "OK 0", wat


def _br_table(_v):
    wat = f"""(module
{_COUNTER}
  (func (export "f") (result i32)
    (i32.add
      (block $out (result i32)
        (block $l0 (result i32)
          (block $l1 (result i32)
            (i32.const 100)
            (call $next)
            (br_table $l0 $l1 $out))))
      (i32.mul (global.get $g) (i32.const 1000)))))"""
    return "br_table-index-after-value", "OK 1100", wat


def _call_indirect(_v):
    wat = f"""(module
  (type $ft (func (param i32 i32) (result i32)))
{_COUNTER}
  (func $f0 (type $ft) (param $a i32) (param $b i32) (result i32)
    (i32.add (i32.mul (local.get $a) (i32.const 10)) (local.get $b)))
  (func $f1 (type $ft) (param $a i32) (param $b i32) (result i32)
    (i32.add (i32.const 1000) (i32.add (i32.mul (local.get $a) (i32.const 10)) (local.get $b))))
  (func $f2 (type $ft) (param $a i32) (param $b i32) (result i32)
    (i32.add (i32.const 2000) (i32.add (i32.mul (local.get $a) (i32.const 10)) (local.get $b))))
  (table 3 funcref)
  (elem (i32.const 0) func $f0 $f1 $f2)
  (func (export "f") (result i32)
    (i32.add
      (i32.mul (global.get $g) (i32.const 10000))
      (call_indirect (type $ft) (call $next) (call $next) (call $next)))))"""
    # `global.get` is evaluated before the indirect call, so it sees 0; args are 0,1 and table index is 2.
    return "call_indirect-args-before-index", "OK 2001", wat


def _call_ref(_v):
    wat = f"""(module
  (type $ft (func (param i32 i32) (result i32)))
{_COUNTER}
  (func $f (type $ft) (param $a i32) (param $b i32) (result i32)
    (i32.add (i32.mul (local.get $a) (i32.const 10)) (local.get $b)))
  (elem declare func $f)
  (func (export "f") (result i32)
    (i32.add
      (i32.mul (global.get $g) (i32.const 10000))
      (call_ref $ft (call $next) (call $next) (ref.func $f)))))"""
    # `global.get` is evaluated before the call_ref, so it sees 0; args are 0 and 1.
    return "call_ref-args-before-callee", "OK 1", wat


def _return_call_ref(_v):
    wat = f"""(module
  (type $ft (func (param i32 i32) (result i32)))
{_COUNTER}
  (func $f (type $ft) (param $a i32) (param $b i32) (result i32)
    (i32.add (i32.mul (local.get $a) (i32.const 10)) (local.get $b)))
  (elem declare func $f)
  (func $bounce (result i32)
    (return_call_ref $ft (call $next) (call $next) (ref.func $f)))
  (func (export "f") (result i32)
    (call $bounce)))"""
    return "return_call_ref-args-before-callee", "OK 1", wat


def _struct_new(_v):
    wat = f"""(module
  (type $s (struct (field i32) (field i32) (field i32)))
{_COUNTER}
  (func (export "f") (result i32) (local $s (ref $s))
    (local.set $s (struct.new $s (call $next) (call $next) (call $next)))
    (i32.add
      (i32.mul (global.get $g) (i32.const 10000))
      (i32.add
        (i32.mul (struct.get $s 0 (local.get $s)) (i32.const 100))
        (i32.add
          (i32.mul (struct.get $s 1 (local.get $s)) (i32.const 10))
          (struct.get $s 2 (local.get $s)))))))"""
    return "struct-new-field-order", "OK 30012", wat


def _struct_set(_v):
    wat = f"""(module
  (type $s (struct (field (mut i32))))
  (global $r (mut (ref null $s)) (ref.null $s))
{_COUNTER}
  (func $pick (result (ref $s))
    (drop (call $next))
    (ref.as_non_null (global.get $r)))
  (func (export "f") (result i32)
    (global.set $r (struct.new $s (i32.const -1)))
    (struct.set $s 0 (call $pick) (call $next))
    (i32.add
      (i32.mul (global.get $g) (i32.const 100))
      (struct.get $s 0 (ref.as_non_null (global.get $r))))))"""
    return "struct-set-ref-before-value", "OK 201", wat


def _array_new_fixed(_v):
    wat = f"""(module
  (type $a (array i32))
{_COUNTER}
  (func (export "f") (result i32) (local $a (ref $a))
    (local.set $a (array.new_fixed $a 3 (call $next) (call $next) (call $next)))
    (i32.add
      (i32.mul (global.get $g) (i32.const 10000))
      (i32.add
        (i32.mul (array.get $a (local.get $a) (i32.const 0)) (i32.const 100))
        (i32.add
          (i32.mul (array.get $a (local.get $a) (i32.const 1)) (i32.const 10))
          (array.get $a (local.get $a) (i32.const 2)))))))"""
    return "array-new-fixed-element-order", "OK 30012", wat


def _array_set(_v):
    wat = f"""(module
  (type $a (array (mut i32)))
  (global $r (mut (ref null $a)) (ref.null $a))
{_COUNTER}
  (func $pick (result (ref $a))
    (drop (call $next))
    (ref.as_non_null (global.get $r)))
  (func (export "f") (result i32)
    (global.set $r (array.new_fixed $a 3 (i32.const 10) (i32.const 11) (i32.const 12)))
    (array.set $a (call $pick) (call $next) (call $next))
    (i32.add
      (i32.mul (global.get $g) (i32.const 10000))
      (array.get $a (ref.as_non_null (global.get $r)) (i32.const 1)))))"""
    return "array-set-ref-index-value-order", "OK 30002", wat


def _i32_store(_v):
    wat = f"""(module
  (memory 1)
  (data (i32.const 0) "\\0a\\0b")
{_COUNTER}
  (func (export "f") (result i32)
    (i32.store8 (call $next) (call $next))
    (i32.add
      (i32.mul (global.get $g) (i32.const 1000))
      (i32.load8_u (i32.const 0)))))"""
    return "i32-store8-address-before-value", "OK 2001", wat


def _throw(_v):
    wat = f"""(module
  (tag $e (param i32 i32 i32))
{_COUNTER}
  (func (export "f") (result i32) (local $a i32) (local $b i32) (local $c i32)
    (block $h (result i32 i32 i32)
      (try_table (catch $e $h)
        (throw $e (call $next) (call $next) (call $next)))
      (unreachable))
    (local.set $c)
    (local.set $b)
    (local.set $a)
    (i32.add
      (i32.mul (global.get $g) (i32.const 10000))
      (i32.add
        (i32.mul (local.get $a) (i32.const 100))
        (i32.add
          (i32.mul (local.get $b) (i32.const 10))
          (local.get $c))))))"""
    return "throw-payload-order", "OK 30012", wat


_FAMILIES = [_memory_copy, _memory_fill, _memory_init,
             _array_copy, _array_fill, _array_init_data, _array_init_elem, _array_new,
             _table_copy, _table_fill, _table_init, _table_grow, _table_set,
             _select, _br_table, _call_indirect, _call_ref, _return_call_ref,
             _struct_new, _struct_set, _array_new_fixed, _array_set, _i32_store, _throw]


def evalorder_gen(seed):
    """Return (label, export, expected, wat): the seed-th operand-order probe."""
    fam = _FAMILIES[seed % len(_FAMILIES)]
    label, expected, wat = fam(seed // len(_FAMILIES))
    return f"evalorder-{label}", "f", expected, wat


if __name__ == "__main__":
    import re
    import shutil
    import subprocess

    def res(p):
        both = (p.stdout + p.stderr).lower()
        if p.returncode != 0 or any(k in both for k in ("trap", "unreachable", "runtimeerror")):
            return "TRAP"
        out = [ln.strip() for ln in p.stdout.splitlines() if ln.strip()]
        nums = re.findall(r"-?\d+", out[-1] if out else "")
        return "OK " + nums[-1] if nums else "OK _"

    print("=== evalorder: side-effecting operand order probes ===")
    bad = 0
    for s in range(len(_FAMILIES)):
        label, export, expected, wat = evalorder_gen(s)
        p = subprocess.run(["wasm-tools", "parse", "/dev/stdin", "-o", "/tmp/eo.wasm"],
                           input=wat, capture_output=True, text=True)
        if p.returncode != 0:
            print(f"  ASMFAIL {label}: {p.stderr.strip().splitlines()[-1][:96]}")
            bad += 1
            continue
        if shutil.which("wasmtime"):
            wt = subprocess.run(["wasmtime", "run", "-W", "function-references=y,gc=y,exceptions=y,tail-call=y",
                                 "--invoke", export, "/tmp/eo.wasm"], capture_output=True, text=True)
            got = res(wt)
            ok = got == expected
            bad += not ok
            print(f"  {'OK ' if ok else 'FAIL'} {label:38} exp={expected:10} wasmtime={got}")
        else:
            print(f"  OK  {label:38} assembles")
    print(f"\n{len(_FAMILIES)} programs, {bad} disagreeing with the oracle")
