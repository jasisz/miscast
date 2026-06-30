"""Typed function-reference / table-call generator.

This is a different execution surface from GC field/array value transport: engines usually lower
`call_ref`, `call_indirect`, table bulk ops, function-subtyping checks, and branch-target casts through
their call-table machinery. Each generated program carries its own oracle: a conformant engine either
returns the baked integer or takes a mandated trap.
"""


def _sig(delta):
    return f"""  (type $sup (sub (func (param (ref eq)) (result (ref eq)))))
  (type $sub (sub $sup (func (param (ref any)) (result (ref i31)))))
  (func $impl (type $sub) (param (ref any)) (result (ref i31))
    (ref.i31
      (i32.add
        (i31.get_s (ref.cast (ref i31) (local.get 0)))
        (i32.const {delta}))))"""


def _call_expr(base, callee):
    return (f"(i31.get_s\n"
            f"      (ref.cast (ref i31)\n"
            f"        (call_ref $sup\n"
            f"          (ref.i31 (i32.const {base}))\n"
            f"          {callee})))")


def _typed_call_ref(v):
    base, delta = 37 + v * 3, 5 + v % 7
    wat = f"""(module
{_sig(delta)}
  (elem declare func $impl)
  (func (export "f") (result i32)
    {_call_expr(base, "(ref.cast (ref $sup) (ref.func $impl))")}))"""
    return "direct-call_ref-subtype", f"OK {base + delta}", wat


def _call_indirect_subtyped(v):
    base, delta = 41 + v * 5, 3 + v % 9
    wat = f"""(module
{_sig(delta)}
  (table 1 funcref)
  (elem (i32.const 0) func $impl)
  (func (export "f") (result i32)
    (i31.get_s
      (ref.cast (ref i31)
        (call_indirect (type $sup)
          (ref.i31 (i32.const {base}))
          (i32.const 0))))))"""
    return "call_indirect-subtyped-func", f"OK {base + delta}", wat


def _table_get_cast_call(v):
    base, delta = 53 + v * 7, 2 + v % 11
    wat = f"""(module
{_sig(delta)}
  (table $t 1 funcref)
  (elem (i32.const 0) func $impl)
  (func (export "f") (result i32)
    {_call_expr(base, "(ref.cast (ref $sup) (table.get $t (i32.const 0)))")}))"""
    return "table-get-cast-call_ref", f"OK {base + delta}", wat


def _table_init_copy(v):
    base, delta = 61 + v * 11, 4 + v % 5
    wat = f"""(module
{_sig(delta)}
  (table $t 3 funcref)
  (elem $e func $impl)
  (func (export "f") (result i32)
    (table.init $e (i32.const 1) (i32.const 0) (i32.const 1))
    (table.copy $t $t (i32.const 2) (i32.const 1) (i32.const 1))
    {_call_expr(base, "(ref.cast (ref $sup) (table.get $t (i32.const 2)))")}))"""
    return "table-init-copy-call_ref", f"OK {base + delta}", wat


def _table_fill(v):
    base, delta = 71 + v * 13, 6 + v % 13
    wat = f"""(module
{_sig(delta)}
  (table $t 2 funcref)
  (elem declare func $impl)
  (func (export "f") (result i32)
    (table.fill $t (i32.const 0) (ref.func $impl) (i32.const 2))
    {_call_expr(base, "(ref.cast (ref $sup) (table.get $t (i32.const 1)))")}))"""
    return "table-fill-call_ref", f"OK {base + delta}", wat


def _br_on_cast_func(v):
    base, delta = 83 + v * 17, 1 + v % 17
    wat = f"""(module
{_sig(delta)}
  (elem declare func $impl)
  (func (export "f") (result i32)
    {_call_expr(base, "(block $ok (result (ref $sup)) "
                       "(br_on_cast $ok (ref func) (ref $sup) (ref.func $impl)) "
                       "(unreachable))")}))"""
    return "br_on_cast-func-call_ref", f"OK {base + delta}", wat


def _br_on_cast_fail_success(v):
    base, delta = 97 + v * 19, 7 + v % 19
    wat = f"""(module
{_sig(delta)}
  (elem declare func $impl)
  (func (export "f") (result i32) (local (ref $sup))
    (block $miss (result (ref func))
      (br_on_cast_fail $miss (ref func) (ref $sup) (ref.func $impl))
      (local.set 0)
      (return {_call_expr(base, "(local.get 0)")}))
    (drop)
    (i32.const -1)))"""
    return "br_on_cast_fail-success-call_ref", f"OK {base + delta}", wat


def _br_on_cast_fail_failure(v):
    wat = """(module
  (type $sup (sub (func (param (ref eq)) (result (ref eq)))))
  (type $other (sub (func (result i32))))
  (func $other_impl (type $other) (result i32) (i32.const 9))
  (elem declare func $other_impl)
  (func (export "f") (result i32)
    (block $miss (result (ref func))
      (br_on_cast_fail $miss (ref func) (ref $sup) (ref.func $other_impl))
      (drop)
      (return (i32.const -1)))
    (drop)
    (i32.const 13)))"""
    return "br_on_cast_fail-failure-branch", "OK 13", wat


def _null_call_ref(_v):
    wat = """(module
  (type $sup (sub (func (param (ref eq)) (result (ref eq)))))
  (func (export "f") (result i32)
    (drop
      (call_ref $sup
        (ref.i31 (i32.const 1))
        (ref.as_non_null (ref.null $sup))))
    (i32.const 99)))"""
    return "null-call_ref-trap", "TRAP", wat


def _null_call_indirect(_v):
    wat = """(module
  (type $sup (sub (func (param (ref eq)) (result (ref eq)))))
  (table 1 funcref)
  (func (export "f") (result i32)
    (drop
      (call_indirect (type $sup)
        (ref.i31 (i32.const 1))
        (i32.const 0)))
    (i32.const 99)))"""
    return "null-call_indirect-trap", "TRAP", wat


def _wrong_call_indirect(_v):
    wat = """(module
  (type $sup (sub (func (param (ref eq)) (result (ref eq)))))
  (type $wrong (sub (func (param i32) (result i32))))
  (func $wrong_impl (type $wrong) (param i32) (result i32) (local.get 0))
  (table 1 funcref)
  (elem (i32.const 0) func $wrong_impl)
  (func (export "f") (result i32)
    (drop
      (call_indirect (type $sup)
        (ref.i31 (i32.const 1))
        (i32.const 0)))
    (i32.const 99)))"""
    return "wrong-type-call_indirect-trap", "TRAP", wat


def _return_call_ref(v):
    base, delta = 109 + v * 23, 8 + v % 23
    wat = f"""(module
{_sig(delta)}
  (elem declare func $impl)
  (func $bounce (param (ref eq)) (param (ref $sup)) (result (ref eq))
    (return_call_ref $sup (local.get 0) (local.get 1)))
  (func (export "f") (result i32)
    (i31.get_s
      (ref.cast (ref i31)
        (call $bounce
          (ref.i31 (i32.const {base}))
          (ref.cast (ref $sup) (ref.func $impl)))))))"""
    return "return_call_ref-subtype", f"OK {base + delta}", wat


_FAMILIES = [_typed_call_ref, _call_indirect_subtyped, _table_get_cast_call, _table_init_copy,
             _table_fill, _br_on_cast_func, _br_on_cast_fail_success, _br_on_cast_fail_failure,
             _null_call_ref, _null_call_indirect, _wrong_call_indirect, _return_call_ref]


def callref_gen(seed):
    """Return (label, export, expected, wat): the seed-th function-reference/table-call probe."""
    fam = _FAMILIES[seed % len(_FAMILIES)]
    label, expected, wat = fam(seed // len(_FAMILIES))
    return f"callref-{label}", "f", expected, wat


if __name__ == "__main__":
    import os
    import re
    import subprocess

    def res(p):
        both = (p.stdout + p.stderr).lower()
        if p.returncode != 0 or "trap" in both or "unreachable" in both or "indirect call type mismatch" in both:
            return "TRAP"
        m = re.findall(r"-?\d+", p.stdout or "")
        return f"OK {m[-1]}" if m else "OK _"

    print("=== callref: typed function refs, table calls and branch casts ===")
    bad = 0
    for s in range(len(_FAMILIES)):
        label, export, expected, wat = callref_gen(s)
        open("/tmp/cr.wat", "w").write(wat)
        a = subprocess.run(["wasm-tools", "parse", "/tmp/cr.wat", "-o", "/tmp/cr.wasm"],
                           capture_output=True, text=True)
        if a.returncode != 0:
            print(f"  ASMFAIL {label}: {a.stderr.strip().splitlines()[-1][:96]}")
            bad += 1
            continue
        wt = subprocess.run(["wasmtime", "run", "-W", "function-references=y,gc=y,tail-call=y",
                             "--invoke", export, "/tmp/cr.wasm"], capture_output=True, text=True)
        got = res(wt)
        ok = got == expected
        bad += not ok
        print(f"  {'OK ' if ok else 'FAIL'} {label:40} exp={expected:8} wasmtime={got}")
    print(f"\n{len(_FAMILIES)} programs, {bad} disagreeing with the oracle")
